from pydantic import BaseModel


class FormFilingAddressItem(BaseModel):
    form_number: str
    form_title: str
    form_url: str
    rendered_tables: list[str]
    context: str
    hash_id: str


class ScrapeAddressesResponse(BaseModel):
    status: str
    total_forms: int
