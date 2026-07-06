import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import TypedDict

import fitz  # pymupdf

from app.config import settings
from app.prompts import DOCUMENT_CLASSIFICATION_PROMPT, OCR_EXTRACTION_PROMPT
from app.services.llm.client import invoke_with_image, invoke_with_text, retry_llm_call

_executor = ThreadPoolExecutor()


class DocumentResult(TypedDict):
    text: str
    type: str  # "exhibit" or "filed_doc"


def _pdf_to_page_images(pdf_bytes: bytes, dpi: int = 150) -> list[bytes]:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = [page.get_pixmap(dpi=dpi).tobytes("png") for page in doc]
    doc.close()
    return pages


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
        try:
            pages = await loop.run_in_executor(_executor, _pdf_to_page_images, pdf_bytes)

            # OCR first page
            first_page_text = await loop.run_in_executor(
                _executor, retry_llm_call, invoke_with_image, pages[0], OCR_EXTRACTION_PROMPT
            )

            # Parallel: classify using first page + OCR remaining pages
            remaining_texts, doc_type = await asyncio.gather(
                _ocr_remaining_pages(loop, pages[1:]),
                loop.run_in_executor(
                    _executor, retry_llm_call, invoke_with_text,
                    DOCUMENT_CLASSIFICATION_PROMPT, first_page_text
                ),
            )

            full_text = "\n\n".join([first_page_text] + remaining_texts)
            doc_type = doc_type.lower().strip()
            if doc_type not in ("exhibit", "filed_doc"):
                doc_type = "exhibit"

            return filename, DocumentResult(text=full_text, type=doc_type), None

        except Exception as exc:
            return filename, None, str(exc)


async def run_ingestion_pipeline(
    files: dict[str, bytes],
) -> tuple[dict[str, DocumentResult], dict[str, str]]:
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

    return results, failed
