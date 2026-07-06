from pydantic import BaseModel


class FormFilingAddressItem(BaseModel):
    form_number: str
    form_title: str
    form_url: str | None = None
    filing_scenario: str
    applies_to: str
    lockbox_name: str
    usps_address: str | None = None
    courier_address: str | None = None


class ScrapeAddressesResponse(BaseModel):
    status: str
    total_forms: int
