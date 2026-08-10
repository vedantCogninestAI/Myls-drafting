import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Uuid, func
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DraftSession(Base):
    __tablename__ = "tb_draft_sessions_draft_ai"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[int] = mapped_column(ForeignKey("tb_cases_draft_ai.id"), nullable=False, index=True)
    process_type: Mapped[str] = mapped_column(String(100), nullable=False)

    # set once at /generate, never touched again
    initial_draft: Mapped[str] = mapped_column(LONGTEXT, nullable=False)
    # overwritten in place on every /revise
    current_draft: Mapped[str] = mapped_column(LONGTEXT, nullable=False)
    revision_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # computed once at /generate (via the graph), reused as-is on every
    # /revise — never re-fetched, see docs/future_work.md
    filing_fee_data: Mapped[str | None] = mapped_column(LONGTEXT, nullable=True)
    filing_address_data: Mapped[str | None] = mapped_column(LONGTEXT, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
