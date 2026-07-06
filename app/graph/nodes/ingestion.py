from app.graph.state import DraftingState
from app.services.ingestion.pipeline import run_ingestion_pipeline


async def ocr_documents_node(state: DraftingState) -> dict:
    updates: dict = {"raw_files": None}

    raw_files = state.get("raw_files") or {}
    if not raw_files:
        return updates

    results, failed = await run_ingestion_pipeline(raw_files)

    exhibit_texts: dict[str, str] = {}
    filed_doc_texts: dict[str, str] = {}
    for filename, doc in results.items():
        if doc["type"] == "exhibit":
            exhibit_texts[filename] = doc["text"]
        else:
            filed_doc_texts[filename] = doc["text"]

    updates["exhibit_texts"] = exhibit_texts
    updates["filed_doc_texts"] = filed_doc_texts
    if failed:
        updates["ingestion_errors"] = failed

    return updates
