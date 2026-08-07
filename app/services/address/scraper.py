import asyncio
import hashlib
import json
import re
from dataclasses import dataclass

import structlog
from firecrawl import AsyncFirecrawl
from openai import AsyncOpenAI

from app.config import settings
from app.prompts import ADDRESS_TABLE_EXTRACTION_PROMPT
from app.repositories.address import AddressRepository

logger = structlog.get_logger(__name__)

_ALL_FORMS_URL = f"{settings.USCIS_BASE_URL}/forms/all-forms"

_FORMS_LIST_RE = re.compile(
    r"\[([A-Z]+-[0-9A-Za-z]+)\s*\\?\|\s*([^\]]+)\]\((https://www\.uscis\.gov/[^)]+)\)"
)
_ADDR_RE = re.compile(
    r"\]\((https://www\.uscis\.gov/[^)]*(?:addresses|filing-locations)[^)]*)\)", re.I
)
_SECTION_RE = re.compile(r"^#{1,6}\s*Where\s+to\s+[Ff]ile\s*$", re.I | re.M)
_NEXT_HEAD_RE = re.compile(r"^#{1,6}\s+\S", re.M)


@dataclass
class FormListing:
    form_number: str
    title: str
    url: str


def content_hash(markdown: str) -> str:
    lines = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if "chatwidget" in stripped or "Emma Logo" in stripped:
            continue
        if re.fullmatch(r"\d+", stripped):
            continue
        lines.append(stripped)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _extract_json(text: str) -> dict:
    cleaned = text.strip()
    cleaned = cleaned.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(cleaned)


def slice_wtf(markdown: str) -> str:
    match = _SECTION_RE.search(markdown)
    if not match:
        return markdown
    start = match.end()
    next_heading = _NEXT_HEAD_RE.search(markdown, start)
    return markdown[start : next_heading.start() if next_heading else len(markdown)].strip()


def pick_addr(page_markdown: str, form_number: str) -> str | None:
    links = list(dict.fromkeys(_ADDR_RE.findall(page_markdown)))
    if not links:
        return None
    slug = form_number.lower()
    own = [u for u in links if slug in u.lower()]
    return own[0] if own else links[0]


def new_firecrawl_client() -> AsyncFirecrawl:
    return AsyncFirecrawl(api_key=settings.FIRECRAWL_API_KEY)


def new_openai_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.OPENAI_KEY)


async def _fetch_markdown(client: AsyncFirecrawl, url: str, tries: int = 3) -> str:
    for attempt in range(tries):
        try:
            doc = await client.scrape(url, formats=["markdown"], max_age=0)
            return doc.markdown or ""
        except Exception as exc:
            if attempt < tries - 1:
                await asyncio.sleep(30 if "Rate Limit" in str(exc) else 5)
            else:
                raise


async def fetch_form_list(client: AsyncFirecrawl) -> list[FormListing]:
    markdown = await _fetch_markdown(client, _ALL_FORMS_URL)

    listings = []
    for code, title, _url in _FORMS_LIST_RE.findall(markdown):
        listings.append(
            FormListing(
                form_number=code,
                title=title.strip(),
                url=f"{settings.USCIS_BASE_URL}/{code.lower()}",
            )
        )
    logger.info("fetch_form_list_done", listing_count=len(listings))
    return listings


async def _extract_address_json(openai_client: AsyncOpenAI, wtf_section: str) -> dict:
    prompt = f"""{ADDRESS_TABLE_EXTRACTION_PROMPT}

Return ONLY valid JSON. No preamble, no markdown fences.

<webpage>
{wtf_section}
</webpage>"""

    response = await openai_client.chat.completions.create(
        model=settings.OPENAI_SCRAPING_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=16000,
    )
    text = response.choices[0].message.content
    return _extract_json(text)


async def _process_form(
    firecrawl_client: AsyncFirecrawl,
    openai_client: AsyncOpenAI,
    existing_hashes: dict[str, str],
    listing: FormListing,
) -> dict | None:
    try:
        page_markdown = await _fetch_markdown(firecrawl_client, listing.url)

        redirect_url = pick_addr(page_markdown, listing.form_number)
        has_redirect = bool(redirect_url) and listing.form_number.lower() not in redirect_url.lower()
        if has_redirect:
            logger.warning(
                "address_redirect_detected",
                form_number=listing.form_number,
                form_url=listing.url,
                redirect_url=redirect_url,
            )

        wtf_section = slice_wtf(page_markdown)
        hash_id = content_hash(wtf_section)

        if existing_hashes.get(listing.form_number) == hash_id:
            logger.info("address_scrape_unchanged", form_number=listing.form_number)
            return None

        extracted_data = await _extract_address_json(openai_client, wtf_section)
        logger.info("address_scrape_updated", form_number=listing.form_number)
        return {
            "form_number": listing.form_number,
            "form_title": listing.title,
            "form_url": listing.url,
            "extracted_data": extracted_data,
            "hash_id": hash_id,
        }
    except Exception:
        logger.exception("address_scrape_failed", form_number=listing.form_number)
        return None


async def scrape_all_addresses(repository: AddressRepository) -> dict:
    firecrawl_client = new_firecrawl_client()
    openai_client = new_openai_client()

    listings = await fetch_form_list(firecrawl_client)
    existing_hashes = await repository.get_all_hashes()

    batch_size = settings.SCRAPE_CONCURRENCY
    logger.info(
        "scrape_all_addresses_started",
        listing_count=len(listings),
        concurrency=batch_size,
        batch_delay=settings.FEE_SCRAPE_BATCH_DELAY,
    )

    updated_count = 0
    skipped_count = 0

    for i in range(0, len(listings), batch_size):
        batch = listings[i : i + batch_size]
        results = await asyncio.gather(
            *(
                _process_form(firecrawl_client, openai_client, existing_hashes, listing)
                for listing in batch
            )
        )

        for changed_row in results:
            if changed_row is None:
                skipped_count += 1
                continue
            await repository.upsert(**changed_row)
            updated_count += 1

        if i + batch_size < len(listings):
            await asyncio.sleep(settings.FEE_SCRAPE_BATCH_DELAY)

    logger.info(
        "scrape_all_addresses_done",
        listing_count=len(listings),
        updated_count=updated_count,
        skipped_count=skipped_count,
    )
    return {"total": len(listings), "updated": updated_count, "skipped": skipped_count}
