from pydantic import BaseModel


class FormFeeItem(BaseModel):
    form_number: str
    form_title: str
    form_url: str | None = None
    filing_category: str
    paper_fee: float | None = None
    online_fee: float | None = None
    paper_fee_text: str | None = None
    online_fee_text: str | None = None
    fee_details: dict | None = None


class ScrapeFeesResponse(BaseModel):
    status: str
    total_forms: int
