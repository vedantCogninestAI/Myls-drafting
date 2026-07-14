from typing_extensions import TypedDict


class DraftingState(TypedDict, total=False):
    # set at first invocation (POST /draft/{case_id}/generate)
    process_type: str
    case_id: int                         # cases.id — thread_id is str(case_id)

    # populated by generate_draft node — the draft IS the whole document,
    # cover letter included; not a separate field/phase. Any unresolved
    # fields are marked inline in the text itself ([GAP: ...]), not tracked
    # as separate state.
    draft: str
    draft_revision_count: int            # how many times generate_draft has run for this case

    # populated by resolve_filing_data node, ahead of every generate_draft
    # run (fresh generation and every revision) — re-resolved each time so a
    # revision round can never silently skip it. None means no reference
    # template showed that slot, or the lookup tool found nothing.
    filing_fee_data: str | None
    filing_address_data: str | None

    # populated by draft_review node, after interrupt() resumes
    draft_feedback: str
    draft_approved: bool
