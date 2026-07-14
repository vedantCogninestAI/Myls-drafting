import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from langgraph.types import Command

from app.config import settings
from app.schemas.draft import DraftApproveRequest
from app.services.draft.word_formatter import draft_to_docx_bytes

logger = structlog.get_logger(__name__)
router = APIRouter()

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.post("/{case_id}/generate")
async def generate_draft_endpoint(case_id: int, request: Request) -> Response:
    thread_id = str(case_id)
    graph = request.app.state.graph
    logger.info("draft_generate_request_received", case_id=case_id)

    result = await graph.ainvoke(
        {
            "case_id": case_id,
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


@router.post("/{case_id}/approve")
async def approve_draft_endpoint(
    case_id: int, body: DraftApproveRequest, request: Request
) -> Response:
    thread_id = str(case_id)
    graph = request.app.state.graph
    config = {"configurable": {"thread_id": thread_id}}
    logger.info("draft_approve_request_received", case_id=case_id, approved=body.approved)

    state_snapshot = await graph.aget_state(config)
    if not state_snapshot.next:
        logger.warning("draft_approve_no_pending_review", case_id=case_id)
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
        approved=approved,
        revision_count=revision_count,
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
