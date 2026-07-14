import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Awaitable, Callable

import structlog

from app.prompts import DRAFT_GENERATION_PROMPT
from app.services.llm.client import invoke_with_tools, retry_llm_call

logger = structlog.get_logger(__name__)
_executor = ThreadPoolExecutor()

MAX_TOOL_ITERATIONS = 10

GET_EXHIBIT_TEXT_TOOL = {
    "toolSpec": {
        "name": "get_exhibit_text",
        "description": (
            "Fetch the full OCR'd text of one case file by its exact filename, as "
            "given in the available files list."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "The exact filename of the case file to fetch.",
                    },
                    "reason": {
                        "type": "string",
                        "description": (
                            "Briefly: what information you're looking for, and why "
                            "this specific file is likely to have it."
                        ),
                    },
                },
                "required": ["filename", "reason"],
            }
        },
    }
}


def _build_initial_message(
    templates: list[str],
    form_data: dict[str, dict],
    available_files: list[dict],
    filing_fee_data: str | None = None,
    filing_address_data: str | None = None,
    previous_draft: str | None = None,
    feedback: str | None = None,
) -> str:
    template_blocks = "\n\n".join(
        f"### Example {i}\n{text}" for i, text in enumerate(templates, start=1)
    )
    files_block = "\n".join(
        f'- "{f["filename"]}" (doc_type: {f["doc_type"]})' for f in available_files
    ) or "(none — this case has no exhibits)"
    message = (
        f"## Reference Templates\n\n{template_blocks}\n\n"
        f"## Form Data (JSON)\n{json.dumps(form_data, indent=2)}\n\n"
        f"## Available Files\n{files_block}"
    )
    if filing_fee_data or filing_address_data:
        message += "\n\n## Filing Data"
        if filing_fee_data:
            message += f"\n\n{filing_fee_data}"
        if filing_address_data:
            message += f"\n\n{filing_address_data}"
    if previous_draft and feedback:
        message += f"\n\n## Previous Draft\n{previous_draft}\n\n## Attorney Feedback\n{feedback}"
    return message


async def generate_draft(
    case_id: int,
    templates: list[str],
    form_data: dict[str, dict],
    available_files: list[dict],
    get_exhibit_text: Callable[[str], Awaitable[str | None]],
    filing_fee_data: str | None = None,
    filing_address_data: str | None = None,
    previous_draft: str | None = None,
    feedback: str | None = None,
) -> str:
    log = logger.bind(case_id=case_id)
    loop = asyncio.get_event_loop()
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "text": _build_initial_message(
                        templates,
                        form_data,
                        available_files,
                        filing_fee_data,
                        filing_address_data,
                        previous_draft,
                        feedback,
                    )
                }
            ],
        }
    ]
    tools = [GET_EXHIBIT_TEXT_TOOL]

    log.info(
        "draft_generation_started",
        template_count=len(templates),
        form_data_file_count=len(form_data),
        available_file_count=len(available_files),
        has_filing_fee_data=filing_fee_data is not None,
        has_filing_address_data=filing_address_data is not None,
        is_revision=bool(previous_draft and feedback),
    )

    for iteration in range(1, MAX_TOOL_ITERATIONS + 1):
        assistant_message, stop_reason = await loop.run_in_executor(
            _executor, retry_llm_call, invoke_with_tools, messages, tools, DRAFT_GENERATION_PROMPT
        )
        messages.append(assistant_message)

        if stop_reason != "tool_use":
            final_text = next(
                (block["text"] for block in assistant_message["content"] if "text" in block),
                "",
            ).strip()
            if not final_text:
                log.warning(
                    "draft_generation_empty_response", iteration=iteration, stop_reason=stop_reason
                )
            log.info(
                "draft_generation_done",
                iterations=iteration,
                draft_length=len(final_text),
            )
            return final_text

        tool_use_blocks = [
            block["toolUse"] for block in assistant_message["content"] if "toolUse" in block
        ]
        tool_result_content = []

        for tool_use_block in tool_use_blocks:
            filename = tool_use_block["input"]["filename"]
            reason = tool_use_block["input"].get("reason")
            tool_use_id = tool_use_block["toolUseId"]

            log.info(
                "draft_generation_tool_call",
                iteration=iteration,
                filename=filename,
                reason=reason,
            )
            exhibit_text = await get_exhibit_text(filename)

            tool_result_content.append(
                {
                    "toolResult": {
                        "toolUseId": tool_use_id,
                        "content": [
                            {
                                "text": exhibit_text
                                or f"No file found with filename '{filename}'."
                            }
                        ],
                    }
                }
            )

        messages.append({"role": "user", "content": tool_result_content})

    log.error("draft_generation_max_iterations_exceeded", max_iterations=MAX_TOOL_ITERATIONS)
    raise RuntimeError(f"Draft generation exceeded {MAX_TOOL_ITERATIONS} tool-call iterations")
