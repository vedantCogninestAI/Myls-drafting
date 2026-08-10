import structlog

from app.core.db import AsyncSessionLocal
from app.graph.state import DraftingState
from app.services.draft.context import build_draft_context
from app.services.draft.pipeline import generate_draft

logger = structlog.get_logger(__name__)


async def generate_draft_node(state: DraftingState) -> dict:
    case_id = state["case_id"]
    process_type = state["process_type"]
    filing_fee_data = state.get("filing_fee_data")
    filing_address_data = state.get("filing_address_data")
    logger.info(
        "generate_draft_node_started",
        case_id=case_id,
        has_filing_fee_data=filing_fee_data is not None,
        has_filing_address_data=filing_address_data is not None,
    )

    async with AsyncSessionLocal() as session:
        ctx = await build_draft_context(session, case_id, process_type)

        draft_text = await generate_draft(
            case_id=case_id,
            process_type=process_type,
            revision_count=1,
            templates=ctx.templates,
            form_data=ctx.form_data,
            available_files=ctx.available_files,
            get_exhibit_text=ctx.get_exhibit_text,
            filing_fee_data=filing_fee_data,
            filing_address_data=filing_address_data,
        )

    logger.info(
        "generate_draft_node_done",
        case_id=case_id,
        template_count=len(ctx.templates),
        draft_length=len(draft_text),
    )
    return {"draft": draft_text}
