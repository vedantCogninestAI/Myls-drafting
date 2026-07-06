from typing import Optional
from typing_extensions import TypedDict


class DraftingState(TypedDict, total=False):
    # set at session start
    process_type: str

    # ingestion input — ephemeral, cleared after ocr node runs
    raw_files: Optional[dict[str, bytes]]

    # ingestion outputs
    exhibit_texts: dict[str, str]
    filed_doc_texts: dict[str, str]
    ingestion_errors: dict[str, str]

    # cover letter phase (populated in future nodes)
    cover_letter: str
    cover_letter_feedback: str
    cover_letter_approved: bool

    # draft phase (populated in future nodes)
    draft: str
    draft_feedback: str
    draft_approved: bool
