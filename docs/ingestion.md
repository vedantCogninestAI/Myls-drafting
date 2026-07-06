# Ingestion Pipeline

Accepts all documents in one upload, processes them concurrently — OCR and classification run in parallel per document — and writes classified extracted text into session state.

## Flow

```
POST /api/v1/session/start          (create session, get thread_id)
        ↓
POST /api/v1/ingest/{thread_id}     (upload all PDFs in one request)
        ↓
endpoint passes raw file bytes into graph state
        ↓
ocr_documents_node (LangGraph node)
        ↓
run_ingestion_pipeline — asyncio.Semaphore(3): max 3 PDFs at a time
        ↓
for each PDF (up to 3 concurrent):
    render all pages as PNG images (pymupdf, 150 DPI)
    OCR page 1 → get first page text
        ↓ (parallel)
    ├── classify document using first page text → "exhibit" or "filed_doc"
    └── OCR remaining pages (sequential)
        ↓
    stitch all page texts → one block per PDF
        ↓
node splits results → exhibit_texts + filed_doc_texts written to session state
raw file bytes cleared from state
        ↓
Response: { "results": { "file.pdf": { "text": "...", "type": "exhibit" } }, "failed": {...} }
```

## Concurrency

- Max 3 PDFs processed simultaneously, controlled via `asyncio.Semaphore`
- Configurable via `INGESTION_CONCURRENCY` in `.env`
- Per document: classification and remaining pages OCR run in parallel via `asyncio.gather`
- Pages within a PDF are processed sequentially
- boto3 (sync) runs in a `ThreadPoolExecutor` to avoid blocking the event loop

## Retry

Each OCR and classification LLM call is wrapped with `retry_llm_call` from `app/services/llm/client.py`. On failure it retries up to `LLM_MAX_RETRIES` times (configured in `.env`, default 3) before marking that PDF as failed.

## Key Files

| File | Role |
|---|---|
| `app/api/v1/endpoints/session.py` | Creates session, returns `thread_id` |
| `app/api/v1/endpoints/ingestion.py` | Receives files + `thread_id`, invokes graph, returns result |
| `app/graph/nodes/ingestion.py` | LangGraph node — reads raw files from state, calls pipeline, splits results into exhibit/filed_doc by type |
| `app/graph/state.py` | `DraftingState` — holds `exhibit_texts`, `filed_doc_texts`, `ingestion_errors` |
| `app/services/ingestion/pipeline.py` | Orchestrates concurrency, OCR, parallel classification, stitching |
| `app/services/llm/client.py` | Bedrock boto3 client — `invoke_with_image` for OCR, `invoke_with_text` for classification, `retry_llm_call` for retry wrapper |
| `app/prompts.py` | `OCR_EXTRACTION_PROMPT` and `DOCUMENT_CLASSIFICATION_PROMPT` |

## Prompts

| Prompt | Purpose |
|---|---|
| `OCR_EXTRACTION_PROMPT` | Extracts all text from a page image preserving structure |
| `DOCUMENT_CLASSIFICATION_PROMPT` | Classifies a document as `exhibit` or `filed_doc` using first page text |

## Classification

Documents are automatically classified into two types:

| Type | Description |
|---|---|
| `exhibit` | Proof document provided by the client (e.g. birth certificate, passport, bank statement) |
| `filed_doc` | Form or application filled out by the client for the legal process |

Only the first page text is sent for classification — enough to identify the document type while keeping the LLM call fast and cheap. If classification returns an unexpected value, it defaults to `exhibit`.

## Response Shape

```json
{
  "results": {
    "passport.pdf": {
      "text": "extracted text...",
      "type": "exhibit"
    },
    "application_form.pdf": {
      "text": "extracted text...",
      "type": "filed_doc"
    }
  },
  "failed": {
    "bad_file.pdf": "error message if any"
  }
}
```

Failed PDFs do not break the whole batch — they are captured separately in `ingestion_errors` on state and returned in `failed`.

## ENV vars

| Key | Description |
|---|---|
| `AWS_REGION` | AWS region for Bedrock |
| `AWS_ACCESS_KEY_ID` | AWS credentials |
| `AWS_SECRET_ACCESS_KEY` | AWS credentials |
| `BEDROCK_MODEL_ID` | Model to use for OCR and classification |
| `INGESTION_CONCURRENCY` | Max parallel PDFs (default 3) |
| `LLM_MAX_RETRIES` | Max retry attempts per LLM call (default 3) |
