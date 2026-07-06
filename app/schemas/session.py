from pydantic import BaseModel


class SessionStartRequest(BaseModel):
    process_type: str


class SessionStartResponse(BaseModel):
    thread_id: str
    process_type: str
