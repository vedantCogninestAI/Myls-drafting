from fastapi import APIRouter

from app.api.v1.endpoints import address, draft, fee, health, ingestion, session, template_generation

router = APIRouter()

router.include_router(health.router, prefix="/health", tags=["health"])
router.include_router(session.router, prefix="/session", tags=["session"])
router.include_router(ingestion.router, prefix="/ingest", tags=["ingestion"])
router.include_router(fee.router, prefix="/fees", tags=["fees"])
router.include_router(address.router, prefix="/addresses", tags=["addresses"])
router.include_router(
    template_generation.router, prefix="/template-generation", tags=["template-generation"]
)
router.include_router(draft.router, prefix="/draft", tags=["draft"])
