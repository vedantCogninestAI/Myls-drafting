import uuid

from pydantic import BaseModel


class DocumentResult(BaseModel):
    name: str
    text: str | None = None
    type: str | None = None  # "exhibit", "filed_doc", or "ic_notes"; None when OCR classification is off


class IngestResponse(BaseModel):
    results: dict[str, DocumentResult]
    failed: dict[str, str] = {}


class IngestedFile(BaseModel):
    id: uuid.UUID
    filename: str
    doc_type: str | None = None
    ocr_text: str | None = None
    error: str | None = None
    fields_extracted: bool
    fields: dict | None = None


class CaseIngestionResponse(BaseModel):
    case_id: int
    files: list[IngestedFile]
