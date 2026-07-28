import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog
from bs4 import BeautifulSoup
from bs4.element import NavigableString
from firecrawl import AsyncFirecrawlApp

from app.config import settings

logger = structlog.get_logger(__name__)

_MONEY_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)")
_WHITESPACE_RE = re.compile(r"\s+")
_ORDERED_TEXT_ITEM_RE = re.compile(r"^\d+[.)]\s+(.+)")


@dataclass
class FormListing:
    topic_id: str
    marker: str
    label: str


def _split_form_label(label: str) -> tuple[str, str]:
    if "," in label:
        number, title = label.split(",", 1)
        return number.strip(), title.strip()
    return label.strip(), label.strip()


def _parse_money(value: str | None) -> float | None:
    if not value:
        return None
    match = _MONEY_RE.search(value)
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    text = _WHITESPACE_RE.sub(" ", value.replace("\xa0", " ")).strip()
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    return text


def _node_text(node) -> str:
    return _normalize_text(node.get_text(" ", strip=True))


def _parse_form_list(html: str) -> list[FormListing]:
    soup = BeautifulSoup(html, "lxml")
    select = soup.find("select", id="form-fee-title")
    logger.info("parse_form_list", html_length=len(html), select_found=select is not None)
    if select is None:
        return []

    listings = []
    for option in select.find_all("option"):
        topic_id = option.get("value", "").strip()
        marker = option.get("data-marker", "").strip()
        label = _node_text(option)
        if not topic_id or not label:
            continue
        listings.append(FormListing(topic_id=topic_id, marker=marker, label=label))
    logger.info("parse_form_list_done", listing_count=len(listings))
    return listings


def _cell_list_prefix(td, first_list) -> str:
    parts: list[str] = []
    for child in td.children:
        if child is first_list:
            break
        if isinstance(child, NavigableString):
            parts.append(_normalize_text(str(child)))
        else:
            parts.append(_node_text(child))
    return _normalize_text(" ".join(part for part in parts if part))


def _join_prefix(prefix: str, value: str) -> str:
    if not prefix:
        return value
    if not value:
        return prefix
    return f"{prefix} {value}"


def _text_items(td) -> list[str]:
    paragraphs = [_node_text(p) for p in td.find_all("p", recursive=False)]
    paragraphs = [p for p in paragraphs if p]
    if len(paragraphs) < 2:
        return []

    items = []
    for paragraph in paragraphs:
        match = _ORDERED_TEXT_ITEM_RE.match(paragraph)
        if match is None:
            return []
        items.append(_normalize_text(match.group(1)))
    return items


def _cell_items(td) -> list[str]:
    first_list = td.find(["ol", "ul"])
    lis = first_list.find_all("li", recursive=False) if first_list is not None else []
    if first_list is not None and not lis:
        lis = first_list.find_all("li")
    if not lis:
        text_items = _text_items(td)
        if text_items:
            return text_items
        return [_node_text(td)]

    prefix = _cell_list_prefix(td, first_list)
    items = [_node_text(li) for li in lis]
    if prefix:
        items = [_join_prefix(prefix, item) for item in items]
    return items


def _cell_has_list_prefix(td) -> bool:
    first_list = td.find(["ol", "ul"])
    return first_list is not None and bool(_cell_list_prefix(td, first_list))


def _has_meaningful_shared_prefix(first: str, second: str) -> bool:
    common = ""
    for left, right in zip(first, second):
        if left != right:
            break
        common += left
    return len(common.strip()) >= 25


def _table_headers(table) -> list[str]:
    return [_node_text(th) for th in table.find_all("th")]


def _headers_for_cells(table_headers: list[str], cells) -> list[str]:
    headers: list[str] = []
    for idx, cell in enumerate(cells):
        header = table_headers[idx] if idx < len(table_headers) else ""
        headers.append(header or _normalize_text(cell.get("data-label")))
    return headers


def _fee_table_containers(soup: BeautifulSoup, body) -> list:
    if body is not None:
        tables = body.find_all("table")
        if tables:
            return tables
        return body.find_all("tbody")

    tables = soup.find_all("table")
    if tables:
        return tables

    return soup.find_all("tbody")


def _parse_fee_page(html: str) -> tuple[list[dict], str | None]:
    soup = BeautifulSoup(html, "lxml")

    body = soup.find("div", class_="form-fee-body")
    rows: list[dict] = []
    for table in _fee_table_containers(soup, body):
        table_headers = _table_headers(table)
        for tr in table.find_all("tr"):
            cells = tr.find_all("td")
            if not cells:
                continue
            headers = _headers_for_cells(table_headers, cells)
            if not any(headers):
                continue
            category_idx = next(
                (idx for idx, h in enumerate(headers) if h in ("Filing Category", "Category")), None
            )
            cell_items = [_cell_items(td) for td in cells]

            for idx, header in enumerate(headers):
                if idx >= len(cells) or "fee" not in header.lower():
                    continue
                raw_dollar_count = cells[idx].get_text().count("$")
                captured_count = len(cell_items[idx])
                if raw_dollar_count > captured_count:
                    logger.warning(
                        "possible_dropped_fee_amount",
                        header=header,
                        raw_dollar_count=raw_dollar_count,
                        captured_item_count=captured_count,
                        raw_cell_text=_node_text(cells[idx])[:300],
                    )

            fee_indexes = [
                idx for idx, header in enumerate(headers)
                if idx < len(cells) and "fee" in header.lower()
            ]
            should_split = any(len(cell_items[idx]) > 1 for idx in fee_indexes)
            if not should_split:
                row = {
                    header: _node_text(cell)
                    for header, cell in zip(headers, cells)
                    if header
                }
                if row:
                    rows.append(row)
                continue

            split_count = max(len(items) for items in cell_items)
            for i in range(split_count):
                values = [
                    items[i] if len(items) == split_count else items[min(i, len(items) - 1)]
                    for items in cell_items
                ]
                # sub-case labels (e.g. "Small Employer or Nonprofit") repeat across rows
                # with different fees, so keep the row's primary label attached for uniqueness
                if (
                    category_idx is not None
                    and i > 0
                    and len(cell_items[category_idx]) == split_count
                    and not _cell_has_list_prefix(cells[category_idx])
                    and not _has_meaningful_shared_prefix(
                        cell_items[category_idx][0], values[category_idx]
                    )
                ):
                    primary_label = cell_items[category_idx][0]
                    values[category_idx] = f"{primary_label} — {values[category_idx]}"
                row = {
                    header: value
                    for header, value in zip(headers, values)
                    if header
                }
                if row:
                    rows.append(row)

    form_url = None
    link_container = soup.find("div", class_="form-fee-visit-link")
    if link_container is not None:
        anchor = link_container.find("a", href=True)
        if anchor is not None:
            href = anchor["href"]
            form_url = href if href.startswith("http") else f"{settings.USCIS_BASE_URL}{href}"

    logger.info(
        "parse_fee_page",
        html_length=len(html),
        form_fee_body_found=body is not None,
        row_count=len(rows),
        form_url=form_url,
    )
    return rows, form_url


def _row_to_fee_item(form_number: str, form_title: str, form_url: str | None, row: dict) -> dict:
    filing_category = row.get("Filing Category") or row.get("Category") or ""
    paper_fee = _parse_money(row.get("Paper Filing Fee") or row.get("Paper Fee"))
    online_fee = _parse_money(row.get("Online Filing Fee") or row.get("Online Fee"))

    return {
        "form_number": form_number,
        "form_title": form_title,
        "form_url": form_url,
        "filing_category": filing_category,
        "paper_fee": paper_fee,
        "online_fee": online_fee,
        "fee_details": row,
    }


async def _fetch(client: AsyncFirecrawlApp, url: str) -> str:
    result = await client.scrape_url(url, formats=["html"])
    html = result.html or ""
    logger.info("firecrawl_fetch", url=url, html_length=len(html), html_preview=html[:300])
    return html


async def fetch_form_list(client: AsyncFirecrawlApp) -> list[FormListing]:
    html = await _fetch(client, settings.USCIS_FEE_CALCULATOR_URL)
    return _parse_form_list(html)


async def _fetch_form_fee_rows(
    client: AsyncFirecrawlApp, listing: FormListing
) -> tuple[list[dict], str | None]:
    try:
        html = await _fetch(
            client, f"{settings.USCIS_FEE_CALCULATOR_URL}?topic_id={listing.topic_id}"
        )
    except Exception:
        logger.exception("fetch_form_fee_rows_failed", topic_id=listing.topic_id, label=listing.label)
        return [], None
    return _parse_fee_page(html)


def new_client() -> AsyncFirecrawlApp:
    return AsyncFirecrawlApp(api_key=settings.FIRECRAWL_API_KEY)


async def scrape_all_fees(
    on_batch: Callable[[list[dict]], Awaitable[None]] | None = None,
) -> list[dict]:
    client = new_client()
    listings = await fetch_form_list(client)

    batch_size = settings.SCRAPE_CONCURRENCY
    logger.info(
        "scrape_all_fees_started",
        listing_count=len(listings),
        concurrency=batch_size,
        batch_delay=settings.FEE_SCRAPE_BATCH_DELAY,
        timeout=settings.FEE_SCRAPE_TIMEOUT,
    )
    fee_items: list[dict] = []
    for i in range(0, len(listings), batch_size):
        batch = listings[i : i + batch_size]
        batch_results = await asyncio.gather(
            *(_fetch_form_fee_rows(client, listing) for listing in batch)
        )

        batch_items: list[dict] = []
        for listing, (rows, form_url) in zip(batch, batch_results):
            form_number, form_title = _split_form_label(listing.label)
            for row in rows:
                batch_items.append(_row_to_fee_item(form_number, form_title, form_url, row))

        if on_batch is not None:
            await on_batch(batch_items)
        fee_items.extend(batch_items)

        if i + batch_size < len(listings):
            await asyncio.sleep(settings.FEE_SCRAPE_BATCH_DELAY)

    logger.info("scrape_all_fees_done", listing_count=len(listings), fee_item_count=len(fee_items))
    return fee_items
