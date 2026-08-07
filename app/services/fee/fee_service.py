import structlog

from app.repositories.fee import FeeRepository
from app.services.fee.scraper import scrape_all_fees

logger = structlog.get_logger(__name__)


async def run_fee_scrape(repository: FeeRepository) -> dict:
    summary = await scrape_all_fees(repository)
    logger.info("run_fee_scrape_done", **summary)
    return summary
