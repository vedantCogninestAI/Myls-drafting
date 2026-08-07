import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class FormAddress(Base):
    __tablename__ = "tb_form_address_draft_ai"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)

    form_number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    form_title: Mapped[str] = mapped_column(String(500), nullable=False)
    form_url: Mapped[str] = mapped_column(String(2048), nullable=False)

    # Raw LLM-extracted {tables, context} blob for this form's "Where to
    # File" section — not normalized into columns, see docs/scraping.md.
    extracted_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # sha256 of the cleaned "Where to File" section markdown, used to skip
    # re-extraction when the source page hasn't changed since last scrape.
    hash_id: Mapped[str] = mapped_column(String(64), nullable=False)

    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
