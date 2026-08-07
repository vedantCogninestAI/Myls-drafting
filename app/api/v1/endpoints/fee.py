from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import AsyncSessionLocal, get_db
from app.repositories.fee import FeeRepository
from app.schemas.fee import FormFeeItem, ScrapeFeesResponse
from app.services.fee.fee_service import run_fee_scrape
from app.services.fee.render import render_extracted_data_as_text_tables
from app.services.fee.scraper import fetch_form_list, new_firecrawl_client

router = APIRouter()


async def _run_scrape_job() -> None:
    async with AsyncSessionLocal() as session:
        repository = FeeRepository(session)
        await run_fee_scrape(repository)


@router.post("/scrape", response_model=ScrapeFeesResponse)
async def scrape_fees(background_tasks: BackgroundTasks) -> ScrapeFeesResponse:
    client = new_firecrawl_client()
    listings = await fetch_form_list(client)

    background_tasks.add_task(_run_scrape_job)

    return ScrapeFeesResponse(status="started", total_forms=len(listings))


@router.get("", response_model=list[FormFeeItem])
async def list_fees(session: AsyncSession = Depends(get_db)) -> list[FormFeeItem]:
    repository = FeeRepository(session)
    records = await repository.list_all()
    items = []
    for record in records:
        rendered_tables, context = render_extracted_data_as_text_tables(record.extracted_data)
        items.append(
            FormFeeItem(
                topic_id=record.topic_id,
                label=record.label,
                form_url=record.form_url,
                rendered_tables=rendered_tables,
                context=context,
                hash_id=record.hash_id,
            )
        )
    return items
