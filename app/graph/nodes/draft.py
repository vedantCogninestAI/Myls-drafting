import structlog
from langgraph.types import interrupt

from app.config import settings
from app.core.db import AsyncSessionLocal
from app.graph.state import DraftingState
from app.repositories.ingestion import IngestionRepository
from app.repositories.template import TemplateRepository
from app.services.draft.pipeline import generate_draft

logger = structlog.get_logger(__name__)

PREVIEW_LENGTH = 500


def _preview(text: str | None) -> str | None:
    if text is None:
        return None
    return text[:PREVIEW_LENGTH] + "..." if len(text) > PREVIEW_LENGTH else text


async def generate_draft_node(state: DraftingState) -> dict:
    case_id = state["case_id"]
    revision_count = state.get("draft_revision_count", 0) + 1
    previous_draft = state.get("draft")
    feedback = state.get("draft_feedback")
    filing_fee_data = state.get("filing_fee_data")
    filing_address_data = state.get("filing_address_data")
    logger.info(
        "generate_draft_node_started",
        case_id=case_id,
        revision_count=revision_count,
        is_revision=bool(previous_draft and feedback),
        has_filing_fee_data=filing_fee_data is not None,
        has_filing_address_data=filing_address_data is not None,
    )

    async with AsyncSessionLocal() as session:
        ingestion_repo = IngestionRepository(session)
        template_repo = TemplateRepository(session)

        process_type = state["process_type"]

        templates = await template_repo.get_by_process_type(process_type)
        files = await ingestion_repo.get_case_files(case_id)
        form_fields_rows = await ingestion_repo.get_case_form_fields(case_id)

        files_by_id = {f.id: f for f in files}
        form_data = {
            files_by_id[ff.ingestion_file_id].filename: ff.fields
            for ff in form_fields_rows
            if ff.ingestion_file_id in files_by_id
        }
        available_files = [
            {"filename": f.filename, "doc_type": f.doc_type}
            for f in files
            if f.doc_type == "exhibit"
        ]

        logger.info(
            "draft_context_fetched",
            case_id=case_id,
            process_type=process_type,
            template_count=len(templates),
            exhibit_count=len(available_files),
            form_data_file_count=len(form_data),
        )
        if not templates:
            logger.warning(
                "draft_context_no_templates",
                case_id=case_id,
                process_type=process_type,
            )

        async def get_exhibit_text(filename: str) -> str | None:
            file = await ingestion_repo.get_file_by_filename(case_id, filename)
            if file is None:
                logger.warning("draft_exhibit_fetch_not_found", case_id=case_id, filename=filename)
                return None
            logger.info(
                "draft_exhibit_fetch_done",
                case_id=case_id,
                filename=filename,
                text_length=len(file.ocr_text or ""),
                text_preview=_preview(file.ocr_text),
            )
            return file.ocr_text

        draft_text = await generate_draft(
            case_id=case_id,
            process_type=process_type,
            revision_count=revision_count,
            templates=[t.ocr_text for t in templates],
            form_data=form_data,
            available_files=available_files,
            get_exhibit_text=get_exhibit_text,
            filing_fee_data=filing_fee_data,
            filing_address_data=filing_address_data,
            previous_draft=previous_draft,
            feedback=feedback,
        )

    logger.info(
        "generate_draft_node_done",
        case_id=case_id,
        revision_count=revision_count,
        template_count=len(templates),
        draft_length=len(draft_text),
    )
    return {
        "draft": draft_text,
        "draft_revision_count": revision_count,
    }


async def draft_review_node(state: DraftingState) -> dict:
    review = interrupt({"draft": state.get("draft")})
    logger.info("draft_review_resumed", case_id=state.get("case_id"), approved=review.get("approved"))
    return {
        "draft_approved": review.get("approved", False),
        "draft_feedback": review.get("feedback"),
    }


def route_after_review(state: DraftingState) -> str:
    if state.get("draft_approved"):
        return "done"
    if state.get("draft_revision_count", 0) >= settings.MAX_DRAFT_REVISIONS:
        logger.warning(
            "draft_revision_cap_reached",
            case_id=state.get("case_id"),
            revision_count=state.get("draft_revision_count"),
            max_revisions=settings.MAX_DRAFT_REVISIONS,
        )
        return "done"
    return "regenerate"
