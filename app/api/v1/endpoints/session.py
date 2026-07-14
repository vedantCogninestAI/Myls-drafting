import structlog
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.repositories.ingestion import IngestionRepository
from app.schemas.session import SessionStartRequest, SessionStartResponse

logger = structlog.get_logger(__name__)
router = APIRouter()


@router.post("/start", response_model=SessionStartResponse)
async def start_session(
    body: SessionStartRequest,
    session: AsyncSession = Depends(get_db),
) -> SessionStartResponse:
    case = await IngestionRepository(session).create_case(process_type=body.process_type)
    logger.info("case_created", case_id=case.id, process_type=body.process_type)
    logger.info("session_started", case_id=case.id)
    return SessionStartResponse(case_id=case.id, process_type=body.process_type)
