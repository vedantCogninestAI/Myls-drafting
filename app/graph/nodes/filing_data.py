import json

import structlog

from app.core.db import AsyncSessionLocal
from app.graph.state import DraftingState
from app.repositories.address import AddressRepository
from app.repositories.fee import FeeRepository
from app.repositories.fuzzy_match import FUZZY_MATCH_THRESHOLD, FormNumberMatch
from app.repositories.ingestion import IngestionRepository
from app.repositories.template import TemplateRepository
from app.services.draft.filing_data import FilingLookupResult, resolve_filing_data

logger = structlog.get_logger(__name__)


def _format_fee_result(form_number: str, rows: list, match: FormNumberMatch) -> FilingLookupResult:
    if not rows:
        if match.form_number is None:
            content = f"No filing fee data found for form '{form_number}'."
        else:
            content = (
                f"No filing fee data found for form '{form_number}' — closest match "
                f"was '{match.form_number}' at {match.score:.0f}%, below the "
                f"{FUZZY_MATCH_THRESHOLD:.0f}% match threshold."
            )
        return FilingLookupResult(
            matched_form_number=match.form_number, score=match.score, row_count=0, content=content
        )

    lines = [f"Filing fee data for form '{match.form_number}' ('{rows[0].form_title}'):"]
    for row in rows:
        lines.append(
            f"\n- Filing category: {row.filing_category}\n"
            f"  Paper filing fee: {row.paper_fee if row.paper_fee is not None else 'not stated'}\n"
            f"  Online filing fee: {row.online_fee if row.online_fee is not None else 'not stated'}"
        )
        if row.fee_details:
            lines.append(f"  Details: {json.dumps(row.fee_details)}")
    return FilingLookupResult(
        matched_form_number=match.form_number,
        score=match.score,
        row_count=len(rows),
        content="\n".join(lines),
    )


def _format_address_result(form_number: str, rows: list, match: FormNumberMatch) -> FilingLookupResult:
    if not rows:
        if match.form_number is None:
            content = f"No filing address data found for form '{form_number}'."
        else:
            content = (
                f"No filing address data found for form '{form_number}' — closest "
                f"match was '{match.form_number}' at {match.score:.0f}%, below the "
                f"{FUZZY_MATCH_THRESHOLD:.0f}% match threshold."
            )
        return FilingLookupResult(
            matched_form_number=match.form_number, score=match.score, row_count=0, content=content
        )

    lines = [f"Filing address data for form '{match.form_number}' ('{rows[0].form_title}'):"]
    for row in rows:
        lines.append(
            f"\n- Filing scenario: {row.filing_scenario} (applies to: {row.applies_to})\n"
            f"  Lockbox: {row.lockbox_name}\n"
            f"  USPS address: {row.usps_address or 'not stated'}\n"
            f"  Courier address: {row.courier_address or 'not stated'}"
        )
        if row.address_details:
            lines.append(f"  Details: {json.dumps(row.address_details)}")
    return FilingLookupResult(
        matched_form_number=match.form_number,
        score=match.score,
        row_count=len(rows),
        content="\n".join(lines),
    )


async def resolve_filing_data_node(state: DraftingState) -> dict:
    case_id = state["case_id"]
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

        async def get_filing_fee(form_number: str) -> FilingLookupResult:
            rows, match = await fee_repo.get_by_form_number(form_number)
            logger.info(
                "filing_data_fee_fetch_done" if rows else "filing_data_fee_fetch_not_found",
                case_id=case_id,
                query=form_number,
                matched_form_number=match.form_number,
                score=match.score,
                row_count=len(rows),
            )
            return _format_fee_result(form_number, rows, match)

        async def get_filing_address(form_number: str) -> FilingLookupResult:
            rows, match = await address_repo.get_by_form_number(form_number)
            logger.info(
                "filing_data_address_fetch_done" if rows else "filing_data_address_fetch_not_found",
                case_id=case_id,
                query=form_number,
                matched_form_number=match.form_number,
                score=match.score,
                row_count=len(rows),
            )
            return _format_address_result(form_number, rows, match)

        filing_data = await resolve_filing_data(
            case_id=case_id,
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
