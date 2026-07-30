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

MAX_TOOL_ITERATIONS = 6


@dataclass
class FilingLookupResult:
    """Result of a get_filing_fee / get_filing_address tool call, for both the
    tool-result content sent back to the model and the log line describing it."""

    matched_form_number: str | None
    score: float
    row_count: int
    content: str


@dataclass
class FilingData:
    """What resolve_filing_data() hands to the draft-writing agent — raw,
    unsummarized tool output, or None if no template showed that slot."""

    fee: str | None
    address: str | None


GET_FILING_FEE_TOOL = {
    "toolSpec": {
        "name": "get_filing_fee",
        "description": (
            "Look up the official filing fee(s) for a form number, from the "
            "authoritative scraped USCIS fee data. Returns every filing-category row "
            "for that form (e.g. General Filing, reduced-fee, fee waiver)."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "form_number": {
                        "type": "string",
                        "description": "The form number to look up (e.g. 'N-400', 'I-485').",
                    },
                    "reason": {
                        "type": "string",
                        "description": (
                            "Briefly: which template showed a filing-fee slot, and "
                            "which form this case is filing."
                        ),
                    },
                },
                "required": ["form_number", "reason"],
            }
        },
    }
}

GET_FILING_ADDRESS_TOOL = {
    "toolSpec": {
        "name": "get_filing_address",
        "description": (
            "Look up the official filing/mailing address(es) for a form number, "
            "from the authoritative scraped USCIS address data. Returns every "
            "filing-scenario row for that form (e.g. by state, by military status)."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "form_number": {
                        "type": "string",
                        "description": "The form number to look up (e.g. 'N-400', 'I-485').",
                    },
                    "reason": {
                        "type": "string",
                        "description": (
                            "Briefly: which template showed a filing-address slot, and "
                            "which form this case is filing."
                        ),
                    },
                },
                "required": ["form_number", "reason"],
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
    tools = [GET_FILING_FEE_TOOL, GET_FILING_ADDRESS_TOOL]
    fetchers = {"get_filing_fee": get_filing_fee, "get_filing_address": get_filing_address}

    fee_results: list[str] = []
    address_results: list[str] = []
    results_by_tool = {"get_filing_fee": fee_results, "get_filing_address": address_results}

    log.info(
        "filing_data_resolution_started",
        template_count=len(templates),
        form_data_file_count=len(form_data),
    )

    for iteration in range(1, MAX_TOOL_ITERATIONS + 1):
        assistant_message, stop_reason = await loop.run_in_executor(
            _executor,
            retry_llm_call,
            invoke_with_tools,
            messages,
            tools,
            FILING_DATA_RESOLUTION_PROMPT,
        )
        messages.append(assistant_message)

        if stop_reason != "tool_use":
            log.info(
                "filing_data_resolution_done",
                iterations=iteration,
                fee_calls=len(fee_results),
                address_calls=len(address_results),
            )
            return FilingData(
                fee="\n\n".join(fee_results) or None,
                address="\n\n".join(address_results) or None,
            )

        tool_use_blocks = [
            block["toolUse"] for block in assistant_message["content"] if "toolUse" in block
        ]
        tool_result_content = []

        for tool_use_block in tool_use_blocks:
            tool_name = tool_use_block["name"]
            tool_input = tool_use_block["input"]
            reason = tool_input.get("reason")
            tool_use_id = tool_use_block["toolUseId"]

            if tool_name in fetchers:
                form_number = tool_input["form_number"]
                lookup = await fetchers[tool_name](form_number)
                result_text = lookup.content
                # Appended regardless of row_count — a "nothing found" result
                # (see _NOT_FOUND_INSTRUCTION in graph/nodes/filing_data.py)
                # must still reach the drafting agent's Filing Data section,
                # not be silently dropped to a bare None indistinguishable
                # from "this document never needed the value at all."
                results_by_tool[tool_name].append(lookup.content)
                log.info(
                    "filing_data_resolution_tool_call",
                    tool=tool_name,
                    iteration=iteration,
                    query=form_number,
                    matched_form_number=lookup.matched_form_number,
                    match_score=lookup.score,
                    row_count=lookup.row_count,
                    reason=reason,
                    result=lookup.content,
                )
            else:
                log.error("filing_data_resolution_unknown_tool", tool=tool_name, iteration=iteration)
                result_text = f"Unknown tool '{tool_name}'."

            tool_result_content.append(
                {"toolResult": {"toolUseId": tool_use_id, "content": [{"text": result_text}]}}
            )

        messages.append({"role": "user", "content": tool_result_content})

    log.warning(
        "filing_data_resolution_max_iterations_exceeded", max_iterations=MAX_TOOL_ITERATIONS
    )
    return FilingData(
        fee="\n\n".join(fee_results) or None,
        address="\n\n".join(address_results) or None,
    )
