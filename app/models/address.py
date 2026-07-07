import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class FormFilingAddress(Base):
    __tablename__ = "form_filing_addresses"
    __table_args__ = (
        UniqueConstraint(
            "form_number",
            "filing_scenario",
            "lockbox_name",
            name="uq_form_filing_addresses_form_scenario_lockbox",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    form_number: Mapped[str] = mapped_column(String, nullable=False, index=True)
    form_title: Mapped[str] = mapped_column(String, nullable=False)
    form_url: Mapped[str | None] = mapped_column(String, nullable=True)

    filing_scenario: Mapped[str] = mapped_column(String, nullable=False)
    applies_to: Mapped[str] = mapped_column(String, nullable=False)
    lockbox_name: Mapped[str] = mapped_column(String, nullable=False)
    usps_address: Mapped[str | None] = mapped_column(String, nullable=True)
    courier_address: Mapped[str | None] = mapped_column(String, nullable=True)
    address_details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
