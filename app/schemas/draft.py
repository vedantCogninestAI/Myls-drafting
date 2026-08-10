from pydantic import BaseModel


class DraftReviseRequest(BaseModel):
    feedback: str
