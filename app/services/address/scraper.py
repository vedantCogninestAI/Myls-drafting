import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urljoin

import structlog
from bs4 import BeautifulSoup
from firecrawl import AsyncFirecrawlApp

from app.config import settings

logger = structlog.get_logger(__name__)

_ADDRESS_SIGNAL_RE = re.compile(r"P\.O\. Box|FedEx, UPS, and DHL|U\.S\. Postal Service", re.IGNORECASE)
_COMBINED_LABEL_RE = re.compile(
    r"U\.S\.\s*Postal Service\s*\(USPS\),\s*FedEx,\s*UPS,\s*and\s*DHL\s*deliveries:", re.IGNORECASE
)
_USPS_LABEL_RE = re.compile(r"U\.S\.\s*Postal Service\s*\(USPS\)(?:\s*deliveries)?:", re.IGNORECASE)
_COURIER_LABEL_RE = re.compile(r"FedEx,\s*UPS,\s*and\s*DHL\s*deliveries:", re.IGNORECASE)
_FORM_HEADING_RE = re.compile(r"^([A-Z0-9]+(?:-[A-Z0-9]+)*(?:/[A-Z0-9-]+)?)\s*\|\s*(.+)$")
_FILING_ADDRESS_LINK_RE = re.compile(r"filing\s+address(es)?", re.IGNORECASE)

_ALL_FORMS_URL = f"{settings.USCIS_BASE_URL}/forms/all-forms"


@dataclass
class FormEntry:
    form_number: str
    form_name: str


def new_client() -> AsyncFirecrawlApp:
    return AsyncFirecrawlApp(api_key=settings.FIRECRAWL_API_KEY)


def _parse_all_forms_page(html: str) -> list[FormEntry]:
    soup = BeautifulSoup(html, "lxml")

    entries: list[FormEntry] = []
    seen: set[str] = set()
    for a in soup.find_all("a"):
        text = a.get_text(strip=True)
        match = _FORM_HEADING_RE.match(text)
        if not match:
            continue
        form_number, form_name = match.group(1).strip(), match.group(2).strip()
        if form_number.upper().startswith("DS-"):
            continue
        if form_number in seen:
            continue
        seen.add(form_number)
        entries.append(FormEntry(form_number=form_number, form_name=form_name))

    logger.info("parse_all_forms_page", html_length=len(html), form_count=len(entries))
    return entries


async def _fetch(client: AsyncFirecrawlApp, url: str) -> str:
    result = await client.scrape_url(url, formats=["html"])
    html = result.html or ""
    logger.info("uscis_fetch", url=url, html_length=len(html))
    return html


async def fetch_all_forms_list(client: AsyncFirecrawlApp) -> list[FormEntry]:
    html = await _fetch(client, _ALL_FORMS_URL)
    return _parse_all_forms_page(html)


def _split_address_cell(text: str) -> tuple[str, str | None, str | None]:
    combined = _COMBINED_LABEL_RE.search(text)
    if combined:
        lockbox_name = text[: combined.start()].strip()
        address = text[combined.end() :].strip()
        return lockbox_name, address, address

    usps = _USPS_LABEL_RE.search(text)
    courier = _COURIER_LABEL_RE.search(text)

    if usps and courier:
        first_start = min(usps.start(), courier.start())
        lockbox_name = text[:first_start].strip()
        if usps.start() < courier.start():
            usps_address = text[usps.end() : courier.start()].strip()
            courier_address = text[courier.end() :].strip()
        else:
            courier_address = text[courier.end() : usps.start()].strip()
            usps_address = text[usps.end() :].strip()
        return lockbox_name, usps_address, courier_address

    if usps:
        return text[: usps.start()].strip(), text[usps.end() :].strip(), None

    if courier:
        return text[: courier.start()].strip(), None, text[courier.end() :].strip()

    return text.strip(), None, None


def _find_filing_scenario(table) -> str | None:
    panel = table.find_parent("div", class_="accordion__panel")
    if panel is None:
        return None
    header = panel.find_previous_sibling("h4", class_="accordion__header")
    if header is None:
        return None
    return header.get_text(strip=True)


def _parse_address_page(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict] = []

    tables = [t for t in soup.find_all("table") if t.get("class") and "dataTable" in t.get("class")]
    for table in tables:
        filing_scenario = _find_filing_scenario(table) or "General Filing"
        for tr in table.find_all("tr"):
            cells = tr.find_all("td")
            if not cells:
                continue

            address_cell_text = None
            other_cell_texts = []
            for td in cells:
                cell_text = td.get_text("\n", strip=True)
                if address_cell_text is None and _ADDRESS_SIGNAL_RE.search(cell_text):
                    address_cell_text = cell_text
                else:
                    other_cell_texts.append(cell_text)

            if address_cell_text is None:
                continue

            applies_to = "\n".join(t for t in other_cell_texts if t) or "General Filing"
            lockbox_name, usps_address, courier_address = _split_address_cell(address_cell_text)

            rows.append(
                {
                    "filing_scenario": filing_scenario,
                    "applies_to": applies_to,
                    "lockbox_name": lockbox_name or "N/A",
                    "usps_address": usps_address,
                    "courier_address": courier_address,
                    "raw_cell_text": address_cell_text,
                }
            )

    logger.info("parse_address_page", html_length=len(html), table_count=len(tables), row_count=len(rows))
    return rows


def _row_to_address_item(form_number: str, form_title: str, form_url: str | None, row: dict) -> dict:
    return {
        "form_number": form_number,
        "form_title": form_title,
        "form_url": form_url,
        "filing_scenario": row["filing_scenario"],
        "applies_to": row["applies_to"],
        "lockbox_name": row["lockbox_name"],
        "usps_address": row["usps_address"],
        "courier_address": row["courier_address"],
        "address_details": row,
    }


def _find_address_page_url(html: str, base_url: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        if _FILING_ADDRESS_LINK_RE.search(a.get_text(strip=True)):
            return urljoin(base_url, a["href"])
    return None


async def _fetch_form_addresses(
    client: AsyncFirecrawlApp, entry: FormEntry
) -> tuple[list[dict], str | None]:
    slug = entry.form_number.lower().replace(" ", "-").replace("/", "-")
    form_page_url = f"{settings.USCIS_BASE_URL}/{slug}"
    try:
        form_page_html = await _fetch(client, form_page_url)
    except Exception:
        logger.info("fetch_form_addresses_skipped", form_number=entry.form_number, url=form_page_url)
        return [], None
    if not form_page_html:
        return [], None

    address_url = _find_address_page_url(form_page_html, form_page_url)
    if address_url is None:
        # some forms embed their filing addresses directly on the form's own page
        rows = _parse_address_page(form_page_html)
        return rows, (form_page_url if rows else None)

    try:
        address_html = await _fetch(client, address_url)
    except Exception:
        logger.info("fetch_form_addresses_skipped", form_number=entry.form_number, url=address_url)
        return [], None
    if not address_html:
        return [], None
    return _parse_address_page(address_html), address_url


async def scrape_all_addresses(
    on_batch: Callable[[list[dict]], Awaitable[None]] | None = None,
) -> list[dict]:
    client = new_client()
    entries = await fetch_all_forms_list(client)

    batch_size = settings.SCRAPE_CONCURRENCY
    logger.info(
        "scrape_all_addresses_started",
        entry_count=len(entries),
        concurrency=batch_size,
        batch_delay=settings.FEE_SCRAPE_BATCH_DELAY,
    )
    address_items: list[dict] = []
    for i in range(0, len(entries), batch_size):
        batch = entries[i : i + batch_size]
        batch_results = await asyncio.gather(
            *(_fetch_form_addresses(client, entry) for entry in batch)
        )

        batch_items: list[dict] = []
        for entry, (rows, form_url) in zip(batch, batch_results):
            for row in rows:
                batch_items.append(
                    _row_to_address_item(entry.form_number, entry.form_name, form_url, row)
                )

        if on_batch is not None:
            await on_batch(batch_items)
        address_items.extend(batch_items)

        if i + batch_size < len(entries):
            await asyncio.sleep(settings.FEE_SCRAPE_BATCH_DELAY)

    logger.info(
        "scrape_all_addresses_done", entry_count=len(entries), address_item_count=len(address_items)
    )
    return address_items
