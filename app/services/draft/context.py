from dataclasses import dataclass
from typing import Awaitable, Callable

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.ingestion import IngestionRepository
from app.repositories.template import TemplateRepository

logger = structlog.get_logger(__name__)

PREVIEW_LENGTH = 500


def _preview(text: str | None) -> str | None:
    if text is None:
        return None
    return text[:PREVIEW_LENGTH] + "..." if len(text) > PREVIEW_LENGTH else text


@dataclass
class DraftContext:
    templates: list[str]
    form_data: dict[str, dict]
    available_files: list[dict]
    get_exhibit_text: Callable[[str], Awaitable[str | None]]


async def build_draft_context(session: AsyncSession, case_id: int, process_type: str) -> DraftContext:
    """Assembles generate_draft()'s inputs for a case — reference templates,
    extracted form fields, and the exhibit-lookup tool. Used by both
    generate_draft_node (fresh /generate) and the /revise endpoint (full
    regen fallback), which need identical inputs. The returned
    get_exhibit_text closure stays valid only as long as `session` stays
    open — callers must keep this call inside their own session block."""
    ingestion_repo = IngestionRepository(session)
    template_repo = TemplateRepository(session)

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
        logger.warning("draft_context_no_templates", case_id=case_id, process_type=process_type)

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

    return DraftContext(
        templates=[t.ocr_text for t in templates],
        form_data=form_data,
        available_files=available_files,
        get_exhibit_text=get_exhibit_text,
    )
