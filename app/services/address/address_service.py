import structlog

from app.repositories.address import AddressRepository
from app.services.address.scraper import scrape_all_addresses

logger = structlog.get_logger(__name__)


async def run_address_scrape(repository: AddressRepository) -> int:
    total_upserted = 0

    async def on_batch(batch_items: list[dict]) -> None:
        nonlocal total_upserted
        upserted = await repository.bulk_upsert(batch_items)
        total_upserted += upserted
        logger.info("address_batch_upserted", batch_item_count=len(batch_items), upserted=upserted)

    address_items = await scrape_all_addresses(on_batch=on_batch)
    logger.info(
        "run_address_scrape_done", address_item_count=len(address_items), upserted=total_upserted
    )
    return total_upserted
