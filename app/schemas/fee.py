from pydantic import BaseModel


class FormFeeItem(BaseModel):
    topic_id: str
    label: str
    form_url: str
    rendered_tables: list[str]
    context: str
    hash_id: str


class ScrapeFeesResponse(BaseModel):
    status: str
    total_forms: int
