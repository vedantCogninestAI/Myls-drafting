import uuid

from pydantic import BaseModel


class TemplateResult(BaseModel):
    id: uuid.UUID
    filename: str


class TemplateUploadResponse(BaseModel):
    process_type: str
    uploaded: list[TemplateResult]
    failed: dict[str, str] = {}


class TemplateItem(BaseModel):
    id: uuid.UUID
    filename: str
    ocr_text: str
    is_active: bool


class TemplateListResponse(BaseModel):
    process_type: str
    templates: list[TemplateItem]
