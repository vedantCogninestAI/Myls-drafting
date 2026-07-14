# Ingestion Pipeline

Accepts all documents in one upload, processes them concurrently — OCR and classification run in parallel per document — and writes classified extracted text to the DB. `filed_doc`-classified files also get their fields extracted into structured JSON (`form_fields` table) — see "Field Extraction" below.

Plain feature: `POST /api/v1/ingest/{case_id}` is a straight `endpoint → service → repository` call, no graph. See `docs/architecture.md`.

## Flow

```
POST /api/v1/session/start          (creates a `cases` row, returns its id)
        ↓
POST /api/v1/ingest/{case_id}       (upload all PDFs in one request)
        ↓
endpoint reads file bytes, calls run_ingestion_pipeline() (service) directly
        ↓
run_ingestion_pipeline — asyncio.Semaphore(INGESTION_CONCURRENCY)
        ↓
for each PDF (up to N concurrent):
    render all pages as PNG images (pymupdf, 150 DPI)
    OCR page 1 → get first page text
        ↓ (parallel)
    ├── classify document using first page text → "exhibit" or "filed_doc"
    │   (skipped if CLASSIFY_PDF_LLM=false — type is left as None)
    └── OCR remaining pages (sequential)
        ↓
    stitch all page texts → one block per PDF
        ↓
endpoint splits results → exhibit_texts + filed_doc_texts + unclassified_texts
        ↓
endpoint calls IngestionRepository.save_files() — one `ingestion_files` DB
row per file (filename, doc_type, ocr_text, error, case_id from the path
param) — s3_url is currently always null, S3 upload isn't built yet
        ↓
get_filed_docs(case_id) — queries ALL ingestion_files for this case where
doc_type = "filed_doc" AND fields_extracted = false (not just this
request's files — picks up any unextracted form from earlier requests too)
        ↓
extract_form_fields() on each — full ocr_text sent to the LLM, extracts a
flat {label: value} JSON of every filled-in field
        ↓
    one `form_fields` row written per successfully-extracted file
    (linked via ingestion_file_id), and that file's fields_extracted
    flipped to true in the same transaction — never runs for "exhibit" files
        ↓
Response: { "results": { "file.pdf": { "text": "...", "type": "exhibit" } }, "failed": {...} }
```

## Concurrency

- Max `INGESTION_CONCURRENCY` PDFs processed simultaneously (default 3), via `asyncio.Semaphore`
- Per document: classification and remaining-pages OCR run in parallel via `asyncio.gather`
- Pages within a PDF are processed sequentially
- boto3 (sync) runs in a `ThreadPoolExecutor` to avoid blocking the event loop

## Retry

Each OCR and classification LLM call is wrapped with `retry_llm_call` from `app/services/llm/client.py`. On failure it retries up to `LLM_MAX_RETRIES` times (default 3) before marking that PDF as failed. Retries are immediate — there is no backoff or sleep between attempts.

## Logging

Structured logs (`structlog`, JSON) are emitted at every stage, so a
slow/stuck request can be traced to exactly where it's at:

| Event | Emitted from | When |
|---|---|---|
| `case_created` | `session.py` (endpoint) | after the `cases` row is inserted |
| `session_started` | `session.py` (endpoint) | right after, once `case_id` is known |
| `ingest_request_received` | `ingestion.py` (endpoint) | files read into memory, before the pipeline runs |
| `ingestion_pipeline_started` / `_done` | `pipeline.py` | before/after all files are processed |
| `document_processing_started` | `pipeline.py` | per file, before rendering pages |
| `pdf_pages_rendered` | `pipeline.py` | per file, after pymupdf renders all pages to PNG |
| `first_page_ocr_done` | `pipeline.py` | per file, after page 1 OCR completes |
| `remaining_pages_ocr_done` | `pipeline.py` | per file, after pages 2+ OCR completes |
| `classification_done` / `classification_skipped` | `pipeline.py` | per file, after classification call (skipped log if `CLASSIFY_PDF_LLM=false`) |
| `document_processing_done` | `pipeline.py` | per file, on success |
| `document_processing_failed` | `pipeline.py` | per file, on exception (logged with traceback via `logger.exception`) |
| `llm_call_retry` | `services/llm/client.py` | per failed attempt inside `retry_llm_call` |
| `llm_call_exhausted` | `services/llm/client.py` | after all `LLM_MAX_RETRIES` attempts fail, just before raising |
| `ingestion_db_save_started` / `_done` | `ingestion.py` (endpoint) | before/after `ingestion_files` rows are bulk-inserted |
| `field_extraction_batch_started` / `_done` | `pipeline.py` | before/after all `filed_doc` files in this request are extracted |
| `field_extraction_started` | `pipeline.py` | per `filed_doc` file, before the extraction LLM call |
| `field_extraction_done` | `pipeline.py` | per file, on success, with `field_count` |
| `field_extraction_failed` | `pipeline.py` | per file, on exception (LLM call failed or response wasn't valid JSON) |
| `field_extraction_partial_failure` | `ingestion.py` (endpoint) | if any files in the batch failed extraction — doesn't block the request |
| `ingest_request_done` | `ingestion.py` (endpoint) | before the response is returned, with exhibit/filed_doc/unclassified/failed counts |

## Key Files

| File | Role |
|---|---|
| `app/api/v1/endpoints/session.py` | `POST /start` — creates a `cases` row, returns its id as `case_id` |
| `app/api/v1/endpoints/ingestion.py` | `POST /{case_id}` — calls the pipeline, splits results by type, saves via `IngestionRepository`, triggers field extraction, shapes the response. Also `GET /{case_id}` — fetches `ingestion_files` + `form_fields`, merged. |
| `app/services/ingestion/pipeline.py` | Orchestrates concurrency, OCR, parallel classification, stitching, and field extraction (`extract_form_fields()`) |
| `app/services/llm/client.py` | Bedrock boto3 client — `invoke_with_image` for OCR, `invoke_with_text` for classification/field extraction, `retry_llm_call` for retry wrapper |
| `app/prompts.py` | `OCR_EXTRACTION_PROMPT`, `DOCUMENT_CLASSIFICATION_PROMPT`, `FIELD_EXTRACTION_PROMPT` |
| `app/repositories/ingestion.py` | `IngestionRepository` — `create_case()`, `save_files()`, `get_filed_docs()`, `save_form_fields()`, `get_case_files()`, `get_case_form_fields()`, `get_case()`, `get_file_by_filename()` |

## Prompts

| Prompt | Purpose |
|---|---|
| `OCR_EXTRACTION_PROMPT` | Extracts all text from a page image preserving structure |
| `DOCUMENT_CLASSIFICATION_PROMPT` | Classifies a document as `exhibit` or `filed_doc` using first page text |
| `FIELD_EXTRACTION_PROMPT` | Extracts every filled-in field label + value from a `filed_doc`'s full OCR text into a flat JSON object; only runs for `filed_doc`-classified files |

## Classification

Documents are automatically classified into two types:

| Type | Description |
|---|---|
| `exhibit` | Proof document provided by the client (e.g. birth certificate, passport, bank statement) |
| `filed_doc` | Form or application filled out by the client for the legal process |

Only the first page text is sent for classification — enough to identify the document type while keeping the LLM call fast and cheap. If classification returns an unexpected value, it defaults to `exhibit`.

## Field Extraction

After `ingestion_files` rows are saved, the `POST /{case_id}` endpoint calls `IngestionRepository.get_filed_docs(case_id)` — a real DB query, not an in-memory filter of just-saved rows — to find every `filed_doc` for this case that hasn't been extracted yet. Never runs on `exhibit` files, since proof documents (passports, bank statements) have no fixed field structure to extract.

- **DB-driven, not request-scoped**: `get_filed_docs()` returns `WHERE case_id = ? AND doc_type = 'filed_doc' AND fields_extracted = false` — this picks up forms from *any* prior `/ingest` request for this case, not just files uploaded in the current one. Calling `/ingest` again for the same case (even uploading an unrelated file) will pick up and extract any previously-unextracted forms.
- **Skips files with no `ocr_text`**: a `filed_doc` whose OCR failed (`ocr_text` is null) is excluded from extraction, not just ones already `fields_extracted = true`.
- **Dedup via `fields_extracted`**: once a file is successfully extracted, `save_form_fields()` sets `ingestion_files.fields_extracted = true` in the same transaction as inserting the `form_fields` row — so it's excluded from `get_filed_docs()` on every subsequent call. A file that fails extraction stays `false` and will be retried on the next `/ingest` call for that case.
- The file's full `ocr_text` (all pages, already stitched) is sent to the LLM via `FIELD_EXTRACTION_PROMPT`, which asks for a flat `{label: value}` JSON object of every filled-in field, using the form's own label text.
- Runs concurrently across files in the batch (a separate semaphore from OCR's, but using the same `INGESTION_CONCURRENCY` limit), wrapped in `retry_llm_call` same as everything else.
- The LLM's response is parsed defensively (`_parse_fields_json`) — strips a markdown code fence if the model adds one despite being told not to, then `json.loads()`s the result.
- On failure (LLM error or invalid JSON), that file just doesn't get a `form_fields` row — logged via `field_extraction_failed` / `field_extraction_partial_failure`, doesn't fail the request or affect other files.
- **Not included in `/ingest`'s own response** — `IngestResponse` is unchanged, still just OCR text + classification. Extracted fields are fetched separately via `GET /api/v1/ingest/{case_id}` — see "Fetching Ingested Data" below.
- **No standalone endpoint to trigger extraction** — it only runs as a side effect of `/ingest`; there's no way to kick it off independently of an OCR request yet.

## Response Shape

```json
{
  "results": {
    "passport.pdf": {
      "name": "passport.pdf",
      "text": "extracted text...",
      "type": "exhibit"
    },
    "application_form.pdf": {
      "name": "application_form.pdf",
      "text": "extracted text...",
      "type": "filed_doc"
    },
    "unclassified_when_toggle_off.pdf": {
      "name": "unclassified_when_toggle_off.pdf",
      "text": "extracted text...",
      "type": null
    }
  },
  "failed": {
    "bad_file.pdf": "error message if any"
  }
}
```

Failed PDFs do not break the whole batch — they are captured separately and returned in `failed`.

## Fetching Ingested Data

`GET /api/v1/ingest/{case_id}` — a plain DB read via `IngestionRepository`. Returns every `ingestion_files` row for the case, each merged with its `form_fields` row if one exists (matched via `ingestion_file_id`).

```json
{
  "case_id": 12,
  "files": [
    {
      "id": "b3e1a2d4-1234-4a2b-9c3d-abcdef123456",
      "filename": "passport.pdf",
      "doc_type": "exhibit",
      "ocr_text": "PASSPORT\nUnited States of America...",
      "error": null,
      "fields_extracted": false,
      "fields": null
    },
    {
      "id": "9f2c7e10-5678-4c1d-8e2f-abcdef654321",
      "filename": "I-130.pdf",
      "doc_type": "filed_doc",
      "ocr_text": "Form I-130, Petition for Alien Relative...",
      "error": null,
      "fields_extracted": true,
      "fields": {
        "form_number": "I-130",
        "petitioner_name": "John Doe"
      }
    }
  ]
}
```

`exhibit`/`unclassified` files always have `fields: null` (extraction only ever runs on `filed_doc`s). Files that failed OCR show `error` populated and `ocr_text: null`.

Repository methods: `IngestionRepository.get_case_files(case_id)`, `get_case_form_fields(case_id)` (`app/repositories/ingestion.py`). Schema: `CaseIngestionResponse` / `IngestedFile` (`app/schemas/ingestion.py`).

## ENV vars

| Key | Description |
|---|---|
| `AWS_REGION` | AWS region for Bedrock |
| `AWS_ACCESS_KEY_ID` | AWS credentials |
| `AWS_SECRET_ACCESS_KEY` | AWS credentials |
| `BEDROCK_MODEL_ID` | Model to use for OCR and classification |
| `CLASSIFY_PDF_LLM` | `true`/`false` — gates the classification step only. OCR always runs regardless. |
| `INGESTION_CONCURRENCY` | Max parallel PDFs (default 3) |
| `LLM_MAX_RETRIES` | Max retry attempts per LLM call (default 3) |
