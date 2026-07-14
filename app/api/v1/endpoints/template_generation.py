import structlog
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.repositories.template import TemplateRepository
from app.schemas.template import (
    TemplateItem,
    TemplateListResponse,
    TemplateResult,
    TemplateUploadResponse,
)
from app.services.template_generation.pipeline import run_template_pipeline

logger = structlog.get_logger(__name__)
router = APIRouter()

MAX_TEMPLATES_PER_PROCESS_TYPE = 5


@router.post(
    "/{process_type}",
    response_model=TemplateUploadResponse,
    openapi_extra={
        # Swagger UI's file-picker widget only reliably detects the OpenAPI
        # 3.0-style `format: "binary"` marker on array items — FastAPI's
        # default OpenAPI 3.1 schema for `list[UploadFile]` doesn't render
        # a "Choose Files" button without this override.
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "files": {
                                "type": "array",
                                "items": {"type": "string", "format": "binary"},
                            },
                        },
                        "required": ["files"],
                    },
                },
            },
        },
    },
)
async def upload_templates(
    process_type: str,
    files: list[UploadFile] = File(...),
    session: AsyncSession = Depends(get_db),
) -> TemplateUploadResponse:
    repository = TemplateRepository(session)
    existing_count = await repository.count_by_process_type(process_type)
    if existing_count + len(files) > MAX_TEMPLATES_PER_PROCESS_TYPE:
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{process_type}' already has {existing_count} template(s); "
                f"max {MAX_TEMPLATES_PER_PROCESS_TYPE} allowed. "
                f"Delete an existing template before uploading more."
            ),
        )

    file_data = {f.filename: await f.read() for f in files}
    logger.info("template_upload_received", process_type=process_type, filenames=list(file_data.keys()))

    results, failed = await run_template_pipeline(file_data)

    saved = await repository.save_templates(
        process_type,
        [{"filename": filename, "ocr_text": text} for filename, text in results.items()],
    )

    logger.info(
        "template_upload_done",
        process_type=process_type,
        saved_count=len(saved),
        failed_count=len(failed),
    )
    return TemplateUploadResponse(
        process_type=process_type,
        uploaded=[TemplateResult(id=t.id, filename=t.filename) for t in saved],
        failed=failed,
    )


@router.get("/{process_type}", response_model=TemplateListResponse)
async def get_templates(
    process_type: str,
    session: AsyncSession = Depends(get_db),
) -> TemplateListResponse:
    repository = TemplateRepository(session)
    templates = await repository.get_by_process_type(process_type)

    return TemplateListResponse(
        process_type=process_type,
        templates=[
            TemplateItem(id=t.id, filename=t.filename, ocr_text=t.ocr_text, is_active=t.is_active)
            for t in templates
        ],
    )
