import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.db import get_db
from app.repositories.ingestion import IngestionRepository
from app.schemas.draft import DraftApproveRequest
from app.services.draft.word_formatter import draft_to_docx_bytes

logger = structlog.get_logger(__name__)
router = APIRouter()

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.post("/{case_name}/{process_type}/generate")
async def generate_draft_endpoint(
    case_name: str, process_type: str, request: Request, session: AsyncSession = Depends(get_db)
) -> Response:
    case = await IngestionRepository(session).get_case_by_name(case_name)
    if case is None:
        raise HTTPException(status_code=404, detail=f"No case named '{case_name}' found.")
    case_id = case.id

    thread_id = f"{case_id}:{process_type}"
    graph = request.app.state.graph
    logger.info(
        "draft_generate_request_received", case_id=case_id, case_name=case_name, process_type=process_type
    )

    result = await graph.ainvoke(
        {
            "case_id": case_id,
            "process_type": process_type,
            "draft": None,
            "draft_feedback": None,
            "draft_approved": False,
            "draft_revision_count": 0,
        },
        config={"configurable": {"thread_id": thread_id}},
    )
    draft = result.get("draft", "")

    logger.info(
        "draft_generate_request_done",
        case_id=case_id,
        process_type=process_type,
        draft_length=len(draft),
    )
    return Response(
        content=draft_to_docx_bytes(draft),
        media_type=DOCX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="draft_{case_id}.docx"',
            "X-Case-Id": str(case_id),
        },
    )


@router.post("/{case_name}/{process_type}/approve")
async def approve_draft_endpoint(
    case_name: str,
    process_type: str,
    body: DraftApproveRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> Response:
    case = await IngestionRepository(session).get_case_by_name(case_name)
    if case is None:
        raise HTTPException(status_code=404, detail=f"No case named '{case_name}' found.")
    case_id = case.id

    thread_id = f"{case_id}:{process_type}"
    graph = request.app.state.graph
    config = {"configurable": {"thread_id": thread_id}}
    logger.info(
        "draft_approve_request_received",
        case_id=case_id,
        process_type=process_type,
        approved=body.approved,
    )

    state_snapshot = await graph.aget_state(config)
    if not state_snapshot.next:
        logger.warning("draft_approve_no_pending_review", case_id=case_id, process_type=process_type)
        raise HTTPException(
            status_code=409, detail="No draft is currently pending review for this case."
        )

    result = await graph.ainvoke(
        Command(resume={"approved": body.approved, "feedback": body.feedback}),
        config=config,
    )

    approved = result.get("draft_approved", False)
    revision_count = result.get("draft_revision_count", 0)
    max_revisions_reached = not approved and revision_count >= settings.MAX_DRAFT_REVISIONS
    draft = result.get("draft", "")

    logger.info(
        "draft_approve_request_done",
        case_id=case_id,
        process_type=process_type,
        approved=approved,
        revision_count=revision_count,
        max_revisions=settings.MAX_DRAFT_REVISIONS,
        max_revisions_reached=max_revisions_reached,
    )
    return Response(
        content=draft_to_docx_bytes(draft),
        media_type=DOCX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="draft_{case_id}.docx"',
            "X-Case-Id": str(case_id),
            "X-Approved": str(approved).lower(),
            "X-Max-Revisions-Reached": str(max_revisions_reached).lower(),
        },
    )
