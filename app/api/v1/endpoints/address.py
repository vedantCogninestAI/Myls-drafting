from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import AsyncSessionLocal, get_db
from app.repositories.address import AddressRepository
from app.schemas.address import FormFilingAddressItem, ScrapeAddressesResponse
from app.services.address.address_service import run_address_scrape
from app.services.address.render import render_extracted_data_as_text_tables
from app.services.address.scraper import fetch_form_list, new_firecrawl_client

router = APIRouter()


async def _run_scrape_job() -> None:
    async with AsyncSessionLocal() as session:
        repository = AddressRepository(session)
        await run_address_scrape(repository)


@router.post("/scrape", response_model=ScrapeAddressesResponse)
async def scrape_addresses(background_tasks: BackgroundTasks) -> ScrapeAddressesResponse:
    client = new_firecrawl_client()
    listings = await fetch_form_list(client)

    background_tasks.add_task(_run_scrape_job)

    return ScrapeAddressesResponse(status="started", total_forms=len(listings))


@router.get("", response_model=list[FormFilingAddressItem])
async def list_addresses(session: AsyncSession = Depends(get_db)) -> list[FormFilingAddressItem]:
    repository = AddressRepository(session)
    records = await repository.list_all()
    items = []
    for record in records:
        rendered_tables, context = render_extracted_data_as_text_tables(record.extracted_data)
        items.append(
            FormFilingAddressItem(
                form_number=record.form_number,
                form_title=record.form_title,
                form_url=record.form_url,
                rendered_tables=rendered_tables,
                context=context,
                hash_id=record.hash_id,
            )
        )
    return items
