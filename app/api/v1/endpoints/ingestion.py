import structlog
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.repositories.ingestion import IngestionRepository
from app.schemas.ingestion import CaseIngestionResponse, DocumentResult, IngestedFile, IngestResponse
from app.services.ingestion.pipeline import extract_form_fields, run_ingestion_pipeline

logger = structlog.get_logger(__name__)
router = APIRouter()


@router.post(
    "/{case_name}",
    response_model=IngestResponse,
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
async def ingest_documents(
    case_name: str,
    files: list[UploadFile] = File(...),
    session: AsyncSession = Depends(get_db),
) -> IngestResponse:
    repository = IngestionRepository(session)
    case = await repository.get_case_by_name(case_name)
    if case is None:
        raise HTTPException(status_code=404, detail=f"No case named '{case_name}' found.")
    case_id = case.id

    file_data = {f.filename: await f.read() for f in files}
    logger.info(
        "ingest_request_received", case_id=case_id, case_name=case_name, filenames=list(file_data.keys())
    )

    results, failed = await run_ingestion_pipeline(file_data)

    exhibit_texts: dict[str, str] = {}
    filed_doc_texts: dict[str, str] = {}
    unclassified_texts: dict[str, str] = {}
    for filename, doc in results.items():
        if doc["type"] == "exhibit":
            exhibit_texts[filename] = doc["text"]
        elif doc["type"] == "filed_doc":
            filed_doc_texts[filename] = doc["text"]
        else:
            unclassified_texts[filename] = doc["text"]

    files_to_save = [
        {"filename": filename, "doc_type": doc["type"], "ocr_text": doc["text"], "error": None}
        for filename, doc in results.items()
    ] + [
        {"filename": filename, "doc_type": None, "ocr_text": None, "error": error}
        for filename, error in failed.items()
    ]
    logger.info("ingestion_db_save_started", case_id=case_id, file_count=len(files_to_save))
    saved = await repository.save_files(case_id, files_to_save)

    filed_docs = await repository.get_filed_docs(case_id)
    filed_doc_saved = {record.filename: record for record in filed_docs if record.ocr_text}
    if filed_doc_saved:
        extracted, extraction_failed = await extract_form_fields(
            {filename: record.ocr_text for filename, record in filed_doc_saved.items()}
        )
        for filename, fields in extracted.items():
            record = filed_doc_saved[filename]
            await repository.save_form_fields(case_id, record.id, fields)
        if extraction_failed:
            logger.warning("field_extraction_partial_failure", case_id=case_id, failed=extraction_failed)
    logger.info("ingestion_db_save_done", case_id=case_id, saved_count=len(saved))

    all_results: dict[str, DocumentResult] = {}
    for filename, text in exhibit_texts.items():
        all_results[filename] = DocumentResult(name=filename, text=text, type="exhibit")
    for filename, text in filed_doc_texts.items():
        all_results[filename] = DocumentResult(name=filename, text=text, type="filed_doc")
    for filename, text in unclassified_texts.items():
        all_results[filename] = DocumentResult(name=filename, text=text, type=None)

    logger.info(
        "ingest_request_done",
        case_id=case_id,
        exhibit_count=len(exhibit_texts),
        filed_doc_count=len(filed_doc_texts),
        unclassified_count=len(unclassified_texts),
        failed_count=len(failed),
    )
    return IngestResponse(results=all_results, failed=failed)


@router.get("/{case_name}", response_model=CaseIngestionResponse)
async def get_case_ingestion(
    case_name: str,
    session: AsyncSession = Depends(get_db),
) -> CaseIngestionResponse:
    repository = IngestionRepository(session)
    case = await repository.get_case_by_name(case_name)
    if case is None:
        raise HTTPException(status_code=404, detail=f"No case named '{case_name}' found.")

    files = await repository.get_case_files(case.id)
    form_fields = await repository.get_case_form_fields(case.id)
    fields_by_file_id = {ff.ingestion_file_id: ff.fields for ff in form_fields}

    return CaseIngestionResponse(
        case_id=case.id,
        files=[
            IngestedFile(
                id=f.id,
                filename=f.filename,
                doc_type=f.doc_type,
                ocr_text=f.ocr_text,
                error=f.error,
                fields_extracted=f.fields_extracted,
                fields=fields_by_file_id.get(f.id),
            )
            for f in files
        ],
    )
