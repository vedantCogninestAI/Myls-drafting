import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class FormAddress(Base):
    __tablename__ = "tb_form_address_draft_ai"
    __table_args__ = (
        UniqueConstraint(
            "form_number",
            "filing_scenario",
            "lockbox_name",
            name="uq_tb_form_address_draft_ai_form_scenario_lockbox",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)

    form_number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    form_title: Mapped[str] = mapped_column(String(500), nullable=False)
    form_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    filing_scenario: Mapped[str] = mapped_column(String(150), nullable=False)
    applies_to: Mapped[str] = mapped_column(String(200), nullable=False)
    lockbox_name: Mapped[str] = mapped_column(String(150), nullable=False)
    usps_address: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    courier_address: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    address_details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
