import structlog

from app.repositories.fee import FeeRepository
from app.services.fee.scraper import scrape_all_fees

logger = structlog.get_logger(__name__)


async def run_fee_scrape(repository: FeeRepository) -> int:
    total_upserted = 0
    await repository.clear_all()

    async def on_batch(batch_items: list[dict]) -> None:
        nonlocal total_upserted
        upserted = await repository.bulk_upsert(batch_items)
        total_upserted += upserted
        logger.info("fee_batch_upserted", batch_item_count=len(batch_items), upserted=upserted)

    fee_items = await scrape_all_fees(on_batch=on_batch)
    logger.info("run_fee_scrape_done", fee_item_count=len(fee_items), upserted=total_upserted)
    return total_upserted
