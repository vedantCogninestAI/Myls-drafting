from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import AsyncSessionLocal, get_db
from app.repositories.address import AddressRepository
from app.schemas.address import FormFilingAddressItem, ScrapeAddressesResponse
from app.services.address.address_service import run_address_scrape
from app.services.address.scraper import fetch_all_forms_list, new_client

router = APIRouter()


async def _run_scrape_job() -> None:
    async with AsyncSessionLocal() as session:
        repository = AddressRepository(session)
        await run_address_scrape(repository)


@router.post("/scrape", response_model=ScrapeAddressesResponse)
async def scrape_addresses(background_tasks: BackgroundTasks) -> ScrapeAddressesResponse:
    client = new_client()
    entries = await fetch_all_forms_list(client)

    background_tasks.add_task(_run_scrape_job)

    return ScrapeAddressesResponse(status="started", total_forms=len(entries))


@router.get("", response_model=list[FormFilingAddressItem])
async def list_addresses(session: AsyncSession = Depends(get_db)) -> list[FormFilingAddressItem]:
    repository = AddressRepository(session)
    records = await repository.list_all()
    return [
        FormFilingAddressItem(
            form_number=record.form_number,
            form_title=record.form_title,
            form_url=record.form_url,
            filing_scenario=record.filing_scenario,
            applies_to=record.applies_to,
            lockbox_name=record.lockbox_name,
            usps_address=record.usps_address,
            courier_address=record.courier_address,
        )
        for record in records
    ]
