import asyncio
import hashlib
import json
import re
from dataclasses import dataclass

import structlog
from firecrawl import AsyncFirecrawl
from openai import AsyncOpenAI

from app.config import settings
from app.prompts import FEE_TABLE_EXTRACTION_PROMPT
from app.repositories.fee import FeeRepository

logger = structlog.get_logger(__name__)

_SELECT_RE = re.compile(r'<select[^>]*id="form-fee-title"[^>]*>(.*?)</select>', re.DOTALL)
_OPTION_TAG_RE = re.compile(r"<option([^>]*)>([^<]*)</option>", re.IGNORECASE)
_ATTR_RE = re.compile(r'(\w[\w-]*)\s*=\s*"([^"]*)"')
_JSON_FENCE_RE = re.compile(r"^```json\s*|\s*```$")
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


@dataclass
class FormListing:
    topic_id: str
    label: str
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
    cleaned = _JSON_FENCE_RE.sub("", text.strip())
    cleaned = _TRAILING_COMMA_RE.sub(r"\1", cleaned)
    return json.loads(cleaned, strict=False)


def new_firecrawl_client() -> AsyncFirecrawl:
    return AsyncFirecrawl(api_key=settings.FIRECRAWL_API_KEY)


def new_openai_client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.OPENAI_KEY)


async def fetch_form_list(client: AsyncFirecrawl) -> list[FormListing]:
    doc = await client.scrape(settings.USCIS_FEE_CALCULATOR_URL, formats=["html"])
    html = doc.html or ""

    select_match = _SELECT_RE.search(html)
    if not select_match:
        raise ValueError(
            "Could not find the form dropdown (id='form-fee-title') on the page — "
            "page structure may have changed."
        )
    select_html = select_match.group(1)

    listings = []
    for attrs_str, label in _OPTION_TAG_RE.findall(select_html):
        attrs = dict(_ATTR_RE.findall(attrs_str))
        topic_id = attrs.get("value", "").strip()
        label = label.strip()
        if not topic_id or not label:
            continue
        listings.append(
            FormListing(
                topic_id=topic_id,
                label=label,
                url=f"{settings.USCIS_FEE_CALCULATOR_URL}?topic_id={topic_id}",
            )
        )
    logger.info("fetch_form_list_done", listing_count=len(listings))
    return listings


async def _extract_fee_json(openai_client: AsyncOpenAI, markdown: str) -> dict:
    prompt = f"""{FEE_TABLE_EXTRACTION_PROMPT}

Return ONLY valid JSON. No preamble, no markdown fences.

<webpage>
{markdown}
</webpage>"""

    response = await openai_client.chat.completions.create(
        model=settings.OPENAI_SCRAPING_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=4000,
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
        doc = await firecrawl_client.scrape(listing.url, formats=["markdown"], max_age=0)
        markdown = doc.markdown or ""
        hash_id = content_hash(markdown)

        if existing_hashes.get(listing.topic_id) == hash_id:
            logger.info("fee_scrape_unchanged", topic_id=listing.topic_id, label=listing.label)
            return None

        extracted_data = await _extract_fee_json(openai_client, markdown)
        logger.info("fee_scrape_updated", topic_id=listing.topic_id, label=listing.label)
        return {
            "topic_id": listing.topic_id,
            "label": listing.label,
            "form_url": listing.url,
            "extracted_data": extracted_data,
            "hash_id": hash_id,
        }
    except Exception:
        logger.exception("fee_scrape_failed", topic_id=listing.topic_id, label=listing.label)
        return None


async def scrape_all_fees(repository: FeeRepository) -> dict:
    firecrawl_client = new_firecrawl_client()
    openai_client = new_openai_client()

    listings = await fetch_form_list(firecrawl_client)
    existing_hashes = await repository.get_all_hashes()

    batch_size = settings.SCRAPE_CONCURRENCY
    logger.info(
        "scrape_all_fees_started",
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

        # DB writes happen sequentially here, after the concurrent fetch/extract
        # phase — an AsyncSession isn't safe to use from multiple coroutines at once.
        for changed_row in results:
            if changed_row is None:
                skipped_count += 1
                continue
            await repository.upsert(**changed_row)
            updated_count += 1

        if i + batch_size < len(listings):
            await asyncio.sleep(settings.FEE_SCRAPE_BATCH_DELAY)

    logger.info(
        "scrape_all_fees_done",
        listing_count=len(listings),
        updated_count=updated_count,
        skipped_count=skipped_count,
    )
    return {"total": len(listings), "updated": updated_count, "skipped": skipped_count}
