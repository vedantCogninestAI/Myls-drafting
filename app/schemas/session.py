from datetime import datetime

from pydantic import BaseModel


class SessionStartRequest(BaseModel):
    case_name: str


class SessionStartResponse(BaseModel):
    case_id: int
    case_name: str


class CaseListItem(BaseModel):
    case_id: int
    case_name: str
    created_at: datetime


class CaseListResponse(BaseModel):
    cases: list[CaseListItem]
