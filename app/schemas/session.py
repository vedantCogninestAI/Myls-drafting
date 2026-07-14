from pydantic import BaseModel


class SessionStartRequest(BaseModel):
    process_type: str


class SessionStartResponse(BaseModel):
    case_id: int
    process_type: str
