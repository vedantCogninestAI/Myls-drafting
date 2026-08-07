import structlog

from app.repositories.address import AddressRepository
from app.services.address.scraper import scrape_all_addresses

logger = structlog.get_logger(__name__)


async def run_address_scrape(repository: AddressRepository) -> dict:
    summary = await scrape_all_addresses(repository)
    logger.info("run_address_scrape_done", **summary)
    return summary
