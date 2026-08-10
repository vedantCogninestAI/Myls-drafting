from typing_extensions import TypedDict


class DraftingState(TypedDict, total=False):
    # set at first invocation (POST /draft/{case_name}/{process_type}/generate)
    # — a case's ingested data can be drafted against any process_type, so
    # both are required inputs. The graph runs once, start to end, within a
    # single request — no thread_id, no pause/resume. Revisions are handled
    # by a plain (non-graph) endpoint against the draft_sessions table
    # instead, see docs/future_work.md.
    process_type: str
    case_id: int                         # cases.id

    # populated by generate_draft node — the draft IS the whole document,
    # cover letter included; not a separate field/phase. Any unresolved
    # fields are marked inline in the text itself ([GAP: ...]), not tracked
    # as separate state.
    draft: str

    # populated by resolve_filing_data node, ahead of generate_draft. None
    # means no reference template showed that slot, or the lookup tool
    # found nothing.
    filing_fee_data: str | None
    filing_address_data: str | None
