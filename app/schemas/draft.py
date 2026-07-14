from pydantic import BaseModel, model_validator


class DraftApproveRequest(BaseModel):
    approved: bool
    feedback: str | None = None

    @model_validator(mode="after")
    def feedback_required_on_rejection(self) -> "DraftApproveRequest":
        if not self.approved and not (self.feedback and self.feedback.strip()):
            raise ValueError("feedback is required when approved is false")
        return self
