import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from typing import TypedDict

import fitz  # pymupdf
import structlog

from app.config import settings
from app.prompts import DOCUMENT_CLASSIFICATION_PROMPT, FIELD_EXTRACTION_PROMPT, OCR_EXTRACTION_PROMPT
from app.services.llm.client import invoke_with_image, invoke_with_text, retry_llm_call

logger = structlog.get_logger(__name__)
_executor = ThreadPoolExecutor()


class DocumentResult(TypedDict):
    text: str
    type: str | None  # "exhibit" or "filed_doc", or None if classification is disabled


def _pdf_to_page_images(pdf_bytes: bytes, dpi: int = 150) -> list[bytes]:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = [page.get_pixmap(dpi=dpi).tobytes("png") for page in doc]
    doc.close()
    return pages


async def _no_classification() -> None:
    return None


async def _ocr_remaining_pages(loop: asyncio.AbstractEventLoop, pages: list[bytes]) -> list[str]:
    texts = []
    for page in pages:
        text = await loop.run_in_executor(
            _executor, retry_llm_call, invoke_with_image, page, OCR_EXTRACTION_PROMPT
        )
        texts.append(text)
    return texts


async def _process_single(
    semaphore: asyncio.Semaphore,
    filename: str,
    pdf_bytes: bytes,
) -> tuple[str, DocumentResult | None, str | None]:
    async with semaphore:
        loop = asyncio.get_event_loop()
        logger.info("document_processing_started", filename=filename, size_bytes=len(pdf_bytes))
        try:
            pages = await loop.run_in_executor(_executor, _pdf_to_page_images, pdf_bytes)
            logger.info("pdf_pages_rendered", filename=filename, page_count=len(pages))

            # OCR first page
            first_page_text = await loop.run_in_executor(
                _executor, retry_llm_call, invoke_with_image, pages[0], OCR_EXTRACTION_PROMPT
            )
            logger.info(
                "first_page_ocr_done", filename=filename, text_length=len(first_page_text)
            )

            # Parallel: OCR remaining pages + (optionally) classify using first page text
            classify_awaitable = (
                loop.run_in_executor(
                    _executor, retry_llm_call, invoke_with_text,
                    DOCUMENT_CLASSIFICATION_PROMPT, first_page_text
                )
                if settings.CLASSIFY_PDF_LLM
                else _no_classification()
            )
            remaining_texts, doc_type = await asyncio.gather(
                _ocr_remaining_pages(loop, pages[1:]),
                classify_awaitable,
            )
            logger.info(
                "remaining_pages_ocr_done", filename=filename, page_count=len(remaining_texts)
            )
            if settings.CLASSIFY_PDF_LLM:
                logger.info("classification_done", filename=filename, raw_doc_type=doc_type)
            else:
                logger.info("classification_skipped", filename=filename)

            full_text = "\n\n".join([first_page_text] + remaining_texts)
            if doc_type is not None:
                doc_type = doc_type.lower().strip()
                if doc_type not in ("exhibit", "filed_doc"):
                    doc_type = "exhibit"

            logger.info(
                "document_processing_done",
                filename=filename,
                doc_type=doc_type,
                text_length=len(full_text),
            )
            return filename, DocumentResult(text=full_text, type=doc_type), None

        except Exception as exc:
            logger.exception("document_processing_failed", filename=filename, error=str(exc))
            return filename, None, str(exc)


async def run_ingestion_pipeline(
    files: dict[str, bytes],
) -> tuple[dict[str, DocumentResult], dict[str, str]]:
    logger.info(
        "ingestion_pipeline_started",
        file_count=len(files),
        concurrency=settings.INGESTION_CONCURRENCY,
        classify_enabled=settings.CLASSIFY_PDF_LLM,
    )
    semaphore = asyncio.Semaphore(settings.INGESTION_CONCURRENCY)
    tasks = [_process_single(semaphore, name, data) for name, data in files.items()]
    outcomes = await asyncio.gather(*tasks)

    results: dict[str, DocumentResult] = {}
    failed: dict[str, str] = {}
    for filename, doc_result, error in outcomes:
        if error:
            failed[filename] = error
        else:
            results[filename] = doc_result

    logger.info(
        "ingestion_pipeline_done",
        success_count=len(results),
        failed_count=len(failed),
    )
    return results, failed


def _parse_fields_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


async def _extract_fields_single(
    semaphore: asyncio.Semaphore,
    filename: str,
    ocr_text: str,
) -> tuple[str, dict | None, str | None]:
    async with semaphore:
        loop = asyncio.get_event_loop()
        logger.info("field_extraction_started", filename=filename)
        try:
            raw = await loop.run_in_executor(
                _executor, retry_llm_call, invoke_with_text, FIELD_EXTRACTION_PROMPT, ocr_text
            )
            fields = _parse_fields_json(raw)
            logger.info("field_extraction_done", filename=filename, field_count=len(fields))
            return filename, fields, None
        except Exception as exc:
            logger.exception("field_extraction_failed", filename=filename, error=str(exc))
            return filename, None, str(exc)


async def extract_form_fields(
    filed_docs: dict[str, str],
) -> tuple[dict[str, dict], dict[str, str]]:
    logger.info(
        "field_extraction_batch_started",
        file_count=len(filed_docs),
        concurrency=settings.INGESTION_CONCURRENCY,
        llm_max_retries=settings.LLM_MAX_RETRIES,
    )
    semaphore = asyncio.Semaphore(settings.INGESTION_CONCURRENCY)
    tasks = [_extract_fields_single(semaphore, name, text) for name, text in filed_docs.items()]
    outcomes = await asyncio.gather(*tasks)

    results: dict[str, dict] = {}
    failed: dict[str, str] = {}
    for filename, fields, error in outcomes:
        if error:
            failed[filename] = error
        else:
            results[filename] = fields

    logger.info(
        "field_extraction_batch_done",
        success_count=len(results),
        failed_count=len(failed),
    )
    return results, failed
