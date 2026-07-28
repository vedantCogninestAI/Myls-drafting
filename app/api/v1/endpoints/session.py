import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.repositories.ingestion import IngestionRepository
from app.schemas.session import CaseListItem, CaseListResponse, SessionStartRequest, SessionStartResponse

logger = structlog.get_logger(__name__)
router = APIRouter()


@router.post("/start", response_model=SessionStartResponse)
async def start_session(
    body: SessionStartRequest,
    session: AsyncSession = Depends(get_db),
) -> SessionStartResponse:
    try:
        case = await IngestionRepository(session).create_case(case_name=body.case_name)
    except IntegrityError:
        await session.rollback()
        logger.warning("case_name_conflict", case_name=body.case_name)
        raise HTTPException(
            status_code=400, detail=f"A case named '{body.case_name}' already exists."
        )
    logger.info("case_created", case_id=case.id, case_name=body.case_name)
    logger.info("session_started", case_id=case.id)
    return SessionStartResponse(case_id=case.id, case_name=body.case_name)


@router.get("/cases", response_model=CaseListResponse)
async def list_cases(
    session: AsyncSession = Depends(get_db),
) -> CaseListResponse:
    cases = await IngestionRepository(session).list_cases()
    return CaseListResponse(
        cases=[
            CaseListItem(case_id=c.id, case_name=c.case_name, created_at=c.created_at) for c in cases
        ]
    )
