from fastapi import APIRouter, File, Request, UploadFile

from app.config import settings
from app.schemas.ingestion import DocumentResult, IngestResponse

router = APIRouter()


@router.post("/{thread_id}", response_model=IngestResponse)
async def ingest_documents(
    thread_id: str,
    request: Request,
    files: list[UploadFile] = File(...),
) -> IngestResponse:
    file_data = {f.filename: await f.read() for f in files}

    if not settings.CLASSIFY_PDF_LLM:
        return IngestResponse(
            results={name: DocumentResult(name=name) for name in file_data}
        )

    graph = request.app.state.graph
    result = await graph.ainvoke(
        {"raw_files": file_data},
        config={"configurable": {"thread_id": thread_id}},
    )

    all_results: dict[str, DocumentResult] = {}
    for filename, text in result.get("exhibit_texts", {}).items():
        all_results[filename] = DocumentResult(name=filename, text=text, type="exhibit")
    for filename, text in result.get("filed_doc_texts", {}).items():
        all_results[filename] = DocumentResult(name=filename, text=text, type="filed_doc")

    return IngestResponse(
        results=all_results,
        failed=result.get("ingestion_errors", {}),
    )
