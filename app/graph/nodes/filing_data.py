import structlog

from app.core.db import AsyncSessionLocal
from app.graph.state import DraftingState
from app.models.address import FormAddress
from app.models.fee import FormFee
from app.repositories.address import AddressRepository
from app.repositories.fee import FeeRepository
from app.repositories.fuzzy_match import FormNumberMatch
from app.repositories.ingestion import IngestionRepository
from app.repositories.template import TemplateRepository
from app.services.address.render import render_extracted_data_as_text_tables as render_address_tables
from app.services.draft.filing_data import FilingLookupResult, resolve_filing_data
from app.services.fee.render import render_extracted_data_as_text_tables as render_fee_tables

logger = structlog.get_logger(__name__)


def _format_fee_result(row: FormFee | None, match: FormNumberMatch) -> FilingLookupResult:
    if row is None:
        return FilingLookupResult(matched_form_number=match.form_number, score=match.score, found=False, content="")

    rendered_tables, context = render_fee_tables(row.extracted_data)
    lines = [f"Filing fee data for '{row.label}':"]
    lines.extend(rendered_tables)
    if context:
        lines.append(f"Notes: {context}")
    return FilingLookupResult(
        matched_form_number=match.form_number,
        score=match.score,
        found=True,
        content="\n\n".join(lines),
    )


def _format_address_result(row: FormAddress | None, match: FormNumberMatch) -> FilingLookupResult:
    if row is None:
        return FilingLookupResult(matched_form_number=match.form_number, score=match.score, found=False, content="")

    rendered_tables, context = render_address_tables(row.extracted_data)
    lines = [f"Filing address data for '{row.form_number} — {row.form_title}':"]
    lines.extend(rendered_tables)
    if context:
        lines.append(f"Notes: {context}")
    return FilingLookupResult(
        matched_form_number=match.form_number,
        score=match.score,
        found=True,
        content="\n\n".join(lines),
    )


async def resolve_filing_data_node(state: DraftingState) -> dict:
    case_id = state["case_id"]
    revision_count = 1
    logger.info("resolve_filing_data_node_started", case_id=case_id)

    async with AsyncSessionLocal() as session:
        ingestion_repo = IngestionRepository(session)
        template_repo = TemplateRepository(session)
        fee_repo = FeeRepository(session)
        address_repo = AddressRepository(session)

        process_type = state["process_type"]

        templates = await template_repo.get_by_process_type(process_type)
        if not templates:
            logger.info(
                "resolve_filing_data_no_templates", case_id=case_id, process_type=process_type
            )
            return {"filing_fee_data": None, "filing_address_data": None}

        files = await ingestion_repo.get_case_files(case_id)
        form_fields_rows = await ingestion_repo.get_case_form_fields(case_id)
        files_by_id = {f.id: f for f in files}
        form_data = {
            files_by_id[ff.ingestion_file_id].filename: ff.fields
            for ff in form_fields_rows
            if ff.ingestion_file_id in files_by_id
        }

        async def get_filing_fee(query: str) -> FilingLookupResult:
            row, match = await fee_repo.get_by_form_number(query)
            return _format_fee_result(row, match)

        async def get_filing_address(query: str) -> FilingLookupResult:
            row, match = await address_repo.get_by_form_number(query)
            return _format_address_result(row, match)

        filing_data = await resolve_filing_data(
            case_id=case_id,
            process_type=process_type,
            revision_count=revision_count,
            templates=[t.ocr_text for t in templates],
            form_data=form_data,
            get_filing_fee=get_filing_fee,
            get_filing_address=get_filing_address,
        )

    logger.info(
        "resolve_filing_data_node_done",
        case_id=case_id,
        fee_found=filing_data.fee is not None,
        address_found=filing_data.address is not None,
    )
    return {
        "filing_fee_data": filing_data.fee,
        "filing_address_data": filing_data.address,
    }
