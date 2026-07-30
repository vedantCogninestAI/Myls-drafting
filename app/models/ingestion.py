import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, Uuid, func
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class IngestionFile(Base):
    __tablename__ = "tb_ingestion_files_draft_ai"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[int] = mapped_column(ForeignKey("tb_cases_draft_ai.id"), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    doc_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    ocr_text: Mapped[str | None] = mapped_column(LONGTEXT, nullable=True)
    s3_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    fields_extracted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FormFields(Base):
    __tablename__ = "tb_form_fields_draft_ai"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ingestion_file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tb_ingestion_files_draft_ai.id"), nullable=False, index=True
    )
    case_id: Mapped[int] = mapped_column(ForeignKey("tb_cases_draft_ai.id"), nullable=False, index=True)
    fields: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
