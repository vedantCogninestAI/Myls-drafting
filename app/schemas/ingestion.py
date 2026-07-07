from pydantic import BaseModel


class DocumentResult(BaseModel):
    name: str
    text: str | None = None
    type: str | None = None  # "exhibit" or "filed_doc" when OCR is on


class IngestResponse(BaseModel):
    results: dict[str, DocumentResult]
    failed: dict[str, str] = {}
