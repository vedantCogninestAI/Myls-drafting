from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Case(Base):
    __tablename__ = "tb_cases_draft_ai"
    __table_args__ = (UniqueConstraint("case_name", name="uq_tb_cases_draft_ai_case_name"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    case_name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
