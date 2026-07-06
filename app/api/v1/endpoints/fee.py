from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import AsyncSessionLocal, get_db
from app.repositories.fee import FeeRepository
from app.schemas.fee import FormFeeItem, ScrapeFeesResponse
from app.services.fee.fee_service import run_fee_scrape
from app.services.fee.scraper import fetch_form_list, new_client

router = APIRouter()


async def _run_scrape_job() -> None:
    async with AsyncSessionLocal() as session:
        repository = FeeRepository(session)
        await run_fee_scrape(repository)


@router.post("/scrape", response_model=ScrapeFeesResponse)
async def scrape_fees(background_tasks: BackgroundTasks) -> ScrapeFeesResponse:
    client = new_client()
    listings = await fetch_form_list(client)

    background_tasks.add_task(_run_scrape_job)

    return ScrapeFeesResponse(status="started", total_forms=len(listings))


@router.get("", response_model=list[FormFeeItem])
async def list_fees(session: AsyncSession = Depends(get_db)) -> list[FormFeeItem]:
    repository = FeeRepository(session)
    records = await repository.list_all()
    return [
        FormFeeItem(
            form_number=record.form_number,
            form_title=record.form_title,
            form_url=record.form_url,
            filing_category=record.filing_category,
            paper_fee=record.paper_fee,
            online_fee=record.online_fee,
        )
        for record in records
    ]
