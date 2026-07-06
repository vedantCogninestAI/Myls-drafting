import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class FormFeeAddress(Base):
    __tablename__ = "form_fees_address"
    __table_args__ = (
        UniqueConstraint("form_number", "filing_category", name="uq_form_fees_address_form_category"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    form_number: Mapped[str] = mapped_column(String, nullable=False, index=True)
    form_title: Mapped[str] = mapped_column(String, nullable=False)
    form_url: Mapped[str | None] = mapped_column(String, nullable=True)

    filing_category: Mapped[str] = mapped_column(String, nullable=False)
    paper_fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    online_fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee_details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
