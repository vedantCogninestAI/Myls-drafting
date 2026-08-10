import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.db import get_db
from app.graph.main_graph import get_graph
from app.repositories.draft_session import DraftSessionRepository
from app.repositories.ingestion import IngestionRepository
from app.schemas.draft import DraftReviseRequest
from app.services.draft.context import build_draft_context
from app.services.draft.patch import try_patch_draft
from app.services.draft.pipeline import generate_draft
from app.services.draft.word_formatter import draft_to_docx_bytes

logger = structlog.get_logger(__name__)
router = APIRouter()

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.post("/{case_name}/{process_type}/generate")
async def generate_draft_endpoint(
    case_name: str,
    process_type: str,
    session: AsyncSession = Depends(get_db),
    graph=Depends(get_graph),
) -> Response:
    case = await IngestionRepository(session).get_case_by_name(case_name)
    if case is None:
        raise HTTPException(status_code=404, detail=f"No case named '{case_name}' found.")
    case_id = case.id

    logger.info(
        "draft_generate_request_received", case_id=case_id, case_name=case_name, process_type=process_type
    )

    result = await graph.ainvoke({"case_id": case_id, "process_type": process_type})
    draft = result.get("draft", "")

    draft_session = await DraftSessionRepository(session).create(
        case_id=case_id,
        process_type=process_type,
        draft=draft,
        filing_fee_data=result.get("filing_fee_data"),
        filing_address_data=result.get("filing_address_data"),
    )

    logger.info(
        "draft_generate_request_done",
        case_id=case_id,
        process_type=process_type,
        session_id=str(draft_session.id),
        draft_length=len(draft),
    )
    return Response(
        content=draft_to_docx_bytes(draft),
        media_type=DOCX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="draft_{case_id}.docx"',
            "X-Case-Id": str(case_id),
            "X-Session-Id": str(draft_session.id),
        },
    )


@router.post("/revise/{session_id}")
async def revise_draft_endpoint(
    session_id: uuid.UUID,
    body: DraftReviseRequest,
    session: AsyncSession = Depends(get_db),
) -> Response:
    draft_repo = DraftSessionRepository(session)
    draft_session = await draft_repo.get_by_id(session_id)
    if draft_session is None:
        raise HTTPException(status_code=404, detail=f"No draft session '{session_id}' found.")

    case_id = draft_session.case_id
    process_type = draft_session.process_type
    logger.info(
        "draft_revise_request_received",
        case_id=case_id,
        process_type=process_type,
        session_id=str(session_id),
        revision_count=draft_session.revision_count,
    )

    if draft_session.revision_count >= settings.MAX_DRAFT_REVISIONS:
        logger.warning(
            "draft_revision_cap_reached",
            case_id=case_id,
            session_id=str(session_id),
            revision_count=draft_session.revision_count,
            max_revisions=settings.MAX_DRAFT_REVISIONS,
        )
        raise HTTPException(
            status_code=409,
            detail=f"Maximum of {settings.MAX_DRAFT_REVISIONS} revisions reached for this session.",
        )

    next_revision_count = draft_session.revision_count + 1
    ctx = await build_draft_context(session, case_id, process_type)

    new_draft = await try_patch_draft(
        case_id=case_id,
        process_type=process_type,
        revision_count=next_revision_count,
        previous_draft=draft_session.current_draft,
        feedback=body.feedback,
        available_files=ctx.available_files,
    )
    used_patch = new_draft is not None

    if new_draft is None:
        new_draft = await generate_draft(
            case_id=case_id,
            process_type=process_type,
            revision_count=next_revision_count,
            templates=ctx.templates,
            form_data=ctx.form_data,
            available_files=ctx.available_files,
            get_exhibit_text=ctx.get_exhibit_text,
            filing_fee_data=draft_session.filing_fee_data,
            filing_address_data=draft_session.filing_address_data,
            previous_draft=draft_session.current_draft,
            feedback=body.feedback,
        )

    updated = await draft_repo.update_after_revision(draft_session, new_draft)

    logger.info(
        "draft_revise_request_done",
        case_id=case_id,
        process_type=process_type,
        session_id=str(session_id),
        revision_count=updated.revision_count,
        used_patch=used_patch,
        draft_length=len(updated.current_draft),
    )
    return Response(
        content=draft_to_docx_bytes(updated.current_draft),
        media_type=DOCX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="draft_{case_id}.docx"',
            "X-Case-Id": str(case_id),
            "X-Session-Id": str(updated.id),
            "X-Revision-Count": str(updated.revision_count),
        },
    )
