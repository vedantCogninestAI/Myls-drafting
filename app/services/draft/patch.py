import asyncio
from concurrent.futures import ThreadPoolExecutor

import structlog

from app.prompts import DRAFT_PATCH_PROMPT
from app.services.draft.formatting_checks import check_formatting
from app.services.llm.client import invoke_with_tools, retry_llm_call

logger = structlog.get_logger(__name__)
_executor = ThreadPoolExecutor()

APPLY_DRAFT_PATCH_TOOL = {
    "toolSpec": {
        "name": "apply_draft_patch",
        "description": (
            "Report whether the attorney's feedback can be satisfied with precise, "
            "localized text replacements, and if so, exactly what to replace."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "patchable": {
                        "type": "boolean",
                        "description": (
                            "True if the feedback reduces to one or more localized text "
                            "replacements. False if it requires a broader rewrite (tone, "
                            "structure, ordering, or anything with no single anchor)."
                        ),
                    },
                    "edits": {
                        "type": "array",
                        "description": "One entry per spot that needs to change. Empty if patchable is false.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "original": {
                                    "type": "string",
                                    "description": (
                                        "The exact, verbatim text from the Previous Draft to "
                                        "replace — same whitespace, punctuation, and line breaks."
                                    ),
                                },
                                "replacement": {
                                    "type": "string",
                                    "description": "The corrected text for that exact span, nothing more.",
                                },
                            },
                            "required": ["original", "replacement"],
                        },
                    },
                },
                "required": ["patchable", "edits"],
            }
        },
    }
}


def _build_patch_message(previous_draft: str, feedback: str) -> str:
    return f"## Previous Draft\n{previous_draft}\n\n## Attorney Feedback\n{feedback}"


async def try_patch_draft(
    case_id: int,
    process_type: str,
    revision_count: int,
    previous_draft: str,
    feedback: str,
    available_files: list[dict],
) -> str | None:
    """Attempt a localized patch instead of a full redraft. Returns the patched
    draft on success, or None if the feedback isn't patchable, an edit can't be
    applied unambiguously, or the patched result fails formatting checks —
    callers should fall back to a full generate_draft() call in that case."""
    log = logger.bind(case_id=case_id, process_type=process_type, revision_count=revision_count)
    loop = asyncio.get_event_loop()
    messages = [
        {"role": "user", "content": [{"text": _build_patch_message(previous_draft, feedback)}]}
    ]

    assistant_message, _ = await loop.run_in_executor(
        _executor,
        retry_llm_call,
        invoke_with_tools,
        messages,
        [APPLY_DRAFT_PATCH_TOOL],
        DRAFT_PATCH_PROMPT,
        "any",
    )

    tool_use_blocks = [
        block["toolUse"] for block in assistant_message["content"] if "toolUse" in block
    ]
    if not tool_use_blocks:
        log.warning("draft_patch_no_tool_call")
        return None

    patch_input = tool_use_blocks[0]["input"]
    log.info(
        "draft_patch_llm_response",
        patchable=patch_input.get("patchable"),
        edits=patch_input.get("edits"),
    )

    if not patch_input.get("patchable"):
        log.info("draft_patch_declined")
        return None

    edits = patch_input.get("edits") or []
    if not edits:
        log.info("draft_patch_declined_no_edits")
        return None

    patched = previous_draft
    for edit_index, edit in enumerate(edits):
        original = edit["original"]
        replacement = edit["replacement"]
        occurrences = patched.count(original)
        if occurrences != 1:
            log.warning(
                "draft_patch_edit_not_unique",
                edit_index=edit_index,
                occurrences=occurrences,
            )
            return None
        patched = patched.replace(original, replacement, 1)

    issues = check_formatting(patched, available_files)
    if issues:
        log.warning("draft_patch_formatting_check_failed", issues=issues)
        return None

    log.info("draft_patch_applied", edit_count=len(edits))
    return patched
