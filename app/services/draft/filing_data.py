import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Awaitable, Callable

import structlog

from app.prompts import FILING_DATA_RESOLUTION_PROMPT
from app.services.llm.client import invoke_with_tools, retry_llm_call

logger = structlog.get_logger(__name__)
_executor = ThreadPoolExecutor()


@dataclass
class FilingLookupResult:
    """Result of one deterministic fee/address lookup for a single ingested
    form — `found=False` means no scraped row matched, and the caller drops
    it rather than surfacing a "not found" message to the drafting agent."""

    matched_form_number: str | None
    score: float
    found: bool
    content: str


@dataclass
class FilingData:
    """What resolve_filing_data() hands to the draft-writing agent — every
    real match found across this case's forms, concatenated, or None if
    nothing was needed or nothing matched."""

    fee: str | None
    address: str | None


DECIDE_FILING_DATA_NEEDS_TOOL = {
    "toolSpec": {
        "name": "decide_filing_data_needs",
        "description": (
            "Report whether the document being drafted needs a filing fee and/or "
            "a filing address, based on whether any reference template shows that slot."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "needs_fee": {
                        "type": "boolean",
                        "description": "True if any reference template shows a filing-fee slot.",
                    },
                    "fee_reason": {
                        "type": "string",
                        "description": "Briefly: which template (if any) shows a filing-fee slot, or why none does.",
                    },
                    "needs_address": {
                        "type": "boolean",
                        "description": "True if any reference template shows a filing-address slot.",
                    },
                    "address_reason": {
                        "type": "string",
                        "description": (
                            "Briefly: which template (if any) shows a filing-address slot, "
                            "or why none does."
                        ),
                    },
                },
                "required": ["needs_fee", "fee_reason", "needs_address", "address_reason"],
            }
        },
    }
}


def _build_initial_message(templates: list[str], form_data: dict[str, dict]) -> str:
    template_blocks = "\n\n".join(
        f"### Example {i}\n{text}" for i, text in enumerate(templates, start=1)
    )
    return (
        f"## Reference Templates\n\n{template_blocks}\n\n"
        f"## Form Data (JSON)\n{json.dumps(form_data, indent=2)}"
    )


async def _fetch_for_every_form(
    log,
    form_data: dict[str, dict],
    fetcher: Callable[[str], Awaitable[FilingLookupResult]],
    event_prefix: str,
) -> list[str]:
    """Call `fetcher` once per ingested form (keyed by filename, e.g. 'FORM
    I-360.pdf') and keep only the real matches — a form with no scraped row
    (e.g. it was never on USCIS's fee-calculator list at all) is dropped
    rather than turned into a "not found" message, since the drafting
    agent's own "Filing Data absent -> gap" rule already covers that case
    correctly without risking a spurious gap for a form that was never
    fee/address-bearing to begin with."""
    results = []
    for filename in form_data:
        lookup = await fetcher(filename)
        log.info(
            f"{event_prefix}_lookup" if lookup.found else f"{event_prefix}_lookup_not_found",
            query=filename,
            matched_form_number=lookup.matched_form_number,
            score=lookup.score,
        )
        if lookup.found:
            results.append(lookup.content)
    return results


async def resolve_filing_data(
    case_id: int,
    process_type: str,
    revision_count: int,
    templates: list[str],
    form_data: dict[str, dict],
    get_filing_fee: Callable[[str], Awaitable[FilingLookupResult]],
    get_filing_address: Callable[[str], Awaitable[FilingLookupResult]],
) -> FilingData:
    log = logger.bind(case_id=case_id, process_type=process_type, revision_count=revision_count)
    loop = asyncio.get_event_loop()
    messages = [
        {"role": "user", "content": [{"text": _build_initial_message(templates, form_data)}]}
    ]

    log.info(
        "filing_data_resolution_started",
        template_count=len(templates),
        form_data_file_count=len(form_data),
    )

    assistant_message, _ = await loop.run_in_executor(
        _executor,
        retry_llm_call,
        invoke_with_tools,
        messages,
        [DECIDE_FILING_DATA_NEEDS_TOOL],
        FILING_DATA_RESOLUTION_PROMPT,
        "any",
    )

    tool_use_blocks = [
        block["toolUse"] for block in assistant_message["content"] if "toolUse" in block
    ]
    if not tool_use_blocks:
        log.warning("filing_data_resolution_no_tool_call")
        return FilingData(fee=None, address=None)

    decision = tool_use_blocks[0]["input"]
    needs_fee = bool(decision.get("needs_fee"))
    needs_address = bool(decision.get("needs_address"))
    log.info(
        "filing_data_needs_decided",
        needs_fee=needs_fee,
        fee_reason=decision.get("fee_reason"),
        needs_address=needs_address,
        address_reason=decision.get("address_reason"),
    )

    fee_results = (
        await _fetch_for_every_form(log, form_data, get_filing_fee, "filing_data_fee")
        if needs_fee
        else []
    )
    address_results = (
        await _fetch_for_every_form(log, form_data, get_filing_address, "filing_data_address")
        if needs_address
        else []
    )

    log.info(
        "filing_data_resolution_done",
        needs_fee=needs_fee,
        needs_address=needs_address,
        fee_matches=len(fee_results),
        address_matches=len(address_results),
    )
    return FilingData(
        fee="\n\n".join(fee_results) or None,
        address="\n\n".join(address_results) or None,
    )
