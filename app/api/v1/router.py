from fastapi import APIRouter

from app.api.v1.endpoints import address, fee, health, ingestion, session

router = APIRouter()

router.include_router(health.router, prefix="/health", tags=["health"])
router.include_router(session.router, prefix="/session", tags=["session"])
router.include_router(ingestion.router, prefix="/ingest", tags=["ingestion"])
router.include_router(fee.router, prefix="/fees", tags=["fees"])
router.include_router(address.router, prefix="/addresses", tags=["addresses"])
