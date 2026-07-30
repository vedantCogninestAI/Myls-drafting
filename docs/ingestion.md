# Ingestion Pipeline

Accepts all documents in one upload, processes them concurrently — text extraction and classification run in parallel per document — and writes classified extracted text to the DB. `filed_doc` and `ic_notes` classified files both get structured JSON extracted from their text into the `form_fields` table — see "Field Extraction" below.

Plain feature: `POST /api/v1/ingest/{case_name}` is a straight `endpoint → service → repository` call, no graph. See `docs/architecture.md`.

## File Type Routing: PDF vs. `.docx`

The endpoint splits uploaded files by extension *before* any processing, into two pipelines that extract text differently but both classify into the **same three types** and feed the same response shape / `ingestion_files` / `form_fields` tables:

- **`.pdf`** → `run_ingestion_pipeline()` — pymupdf renders pages, Bedrock OCRs them.
- **`.docx`** → `run_docx_pipeline()` (`app/services/ingestion/docx_pipeline.py`) — `python-docx` extracts text directly (paragraphs + table cells), no OCR, no page rendering, same "already-digital-text" reasoning `docs/templates.md` uses for its own `.docx` uploads.

Both then run the **same classification call**, `DOCUMENT_CLASSIFICATION_PROMPT`, deciding among `exhibit` / `filed_doc` / `ic_notes` — see "Classification" below. `.docx` is not assumed to be IC Notes; it's classified the same way a PDF is, just from a different text-extraction source. Concurrency reuses `INGESTION_CONCURRENCY` for both (no separate `.docx` env var).

**First-page equivalent for `.docx`:** a PDF's classification call uses page 1's OCR text; a `.docx` has no fixed pagination without rendering it, so `docx_pipeline.py` uses the first `FIRST_PAGE_PARAGRAPH_COUNT` (8) non-empty paragraphs as the classification sample instead — same purpose, closest available equivalent.

## Flow

```
POST /api/v1/session/start          (creates a `cases` row from a case_name,
                                     returns its id)
        ↓
POST /api/v1/ingest/{case_name}     (upload PDFs and/or .docx files in one
                                     request)
        ↓
endpoint resolves case_name → the case row via
IngestionRepository.get_case_by_name() — 404 if no case has that name
        ↓
endpoint reads file bytes, splits by extension: pdf_data vs. docx_data
        ↓ (both run independently, results merged)
run_ingestion_pipeline(pdf_data) — asyncio.Semaphore(INGESTION_CONCURRENCY)
        ↓
for each PDF (up to N concurrent):
    render all pages as PNG images (pymupdf, 150 DPI)
    OCR page 1 → get first page text
        ↓ (parallel)
    ├── classify document using first page text → "exhibit" / "filed_doc"
    │   / "ic_notes" (skipped if CLASSIFY_PDF_LLM=false — type left as None)
    └── OCR remaining pages (sequential)
        ↓
    stitch all page texts → one block per PDF

run_docx_pipeline(docx_data) — asyncio.Semaphore(INGESTION_CONCURRENCY)
        ↓
for each .docx (up to N concurrent):
    extract full text via python-docx (paragraphs + table cells, no OCR);
    also take first 8 non-empty paragraphs as a first-page-equivalent sample
        ↓
    classify using that sample, same DOCUMENT_CLASSIFICATION_PROMPT and same
    three types as PDFs — "exhibit" / "filed_doc" / "ic_notes" (skipped if
    CLASSIFY_PDF_LLM=false — type left as None)
        ↓
endpoint splits merged results → exhibit_texts + filed_doc_texts +
ic_notes_texts + unclassified_texts
        ↓
endpoint calls IngestionRepository.save_files() — one `ingestion_files` DB
row per file (filename, doc_type, ocr_text, error, case_id from the resolved
case row) — s3_url is currently always null, S3 upload isn't built yet
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

get_unextracted_ic_notes(case_id) — same DB-driven pattern as
get_filed_docs(), for doc_type = "ic_notes" AND fields_extracted = false
        ↓
extract_ic_notes_fields() on each — full extracted text sent to the LLM via
IC_NOTES_EXTRACTION_PROMPT (not FIELD_EXTRACTION_PROMPT — there are no fixed
form labels to copy, this is prose), extracts a flat JSON of every concrete
fact stated, using descriptive keys the model chooses itself
        ↓
    one `form_fields` row per successfully-extracted IC Notes file, same
    fields_extracted flip — this is the same table/shape filed_doc fields
    use, so it flows into "Form Data" for draft generation (`docs/draft.md`)
    automatically, with zero changes needed on the drafting-agent side
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
| `case_name_conflict` | `session.py` (endpoint) | warning — `case_name` already exists, `IntegrityError` caught and returned as `400` |
| `session_started` | `session.py` (endpoint) | right after, once `case_id` is known |
| `ingest_request_received` | `ingestion.py` (endpoint) | after `case_name` resolves to a case row, before the pipeline runs |
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
| `field_extraction_batch_started` / `_done` | `pipeline.py` | before/after all `filed_doc` files in this request are extracted; `_started` includes `concurrency` and `llm_max_retries` in effect |
| `field_extraction_started` | `pipeline.py` | per `filed_doc` file, before the extraction LLM call |
| `field_extraction_done` | `pipeline.py` | per file, on success, with `field_count` |
| `field_extraction_failed` | `pipeline.py` | per file, on exception (LLM call failed or response wasn't valid JSON) |
| `field_extraction_partial_failure` | `ingestion.py` (endpoint) | if any files in the batch failed extraction — doesn't block the request |
| `docx_pipeline_started` / `_done` | `docx_pipeline.py` | before/after all `.docx` files in this request are processed |
| `docx_processing_started` | `docx_pipeline.py` | per `.docx` file, before text extraction |
| `docx_text_extracted` | `docx_pipeline.py` | per file, after `python-docx` extraction, with `text_length` |
| `docx_classification_done` / `_skipped` | `docx_pipeline.py` | per file, after classification call (skipped log if `CLASSIFY_PDF_LLM=false`) |
| `docx_processing_failed` | `docx_pipeline.py` | per file, on exception (logged with traceback via `logger.exception`) |
| `ic_notes_extraction_batch_started` / `_done` | `pipeline.py` | before/after all unextracted `ic_notes` files for the case are extracted |
| `ic_notes_extraction_started` | `pipeline.py` | per `ic_notes` file, before the extraction LLM call |
| `ic_notes_extraction_done` | `pipeline.py` | per file, on success, with `field_count` |
| `ic_notes_extraction_failed` | `pipeline.py` | per file, on exception (LLM call failed or response wasn't valid JSON) |
| `ic_notes_extraction_partial_failure` | `ingestion.py` (endpoint) | if any files in the batch failed extraction — doesn't block the request |
| `ingest_request_done` | `ingestion.py` (endpoint) | before the response is returned, with exhibit/filed_doc/ic_notes/unclassified/failed counts |

## Key Files

| File | Role |
|---|---|
| `app/api/v1/endpoints/session.py` | `POST /start` — creates a `cases` row from a `case_name`, returns `case_id`. Also `GET /cases` — lists every case. |
| `app/api/v1/endpoints/ingestion.py` | `POST /{case_name}` — resolves the name to a case row, splits uploads by extension (PDF vs. `.docx`), calls both pipelines, splits results by type, saves via `IngestionRepository`, triggers field extraction for both `filed_doc` and `ic_notes`, shapes the response. Also `GET /{case_name}` — fetches `ingestion_files` + `form_fields`, merged. |
| `app/services/ingestion/pipeline.py` | Orchestrates concurrency, OCR, parallel classification, stitching for PDFs (`run_ingestion_pipeline()`); shared field-extraction batching (`_extract_fields_batch()`) used by both `extract_form_fields()` (`filed_doc`) and `extract_ic_notes_fields()` (`ic_notes`) |
| `app/services/ingestion/docx_pipeline.py` | `run_docx_pipeline()` — `.docx` text extraction via `python-docx` (paragraphs + table cells, no OCR), first-8-paragraph classification sample, classifies into the same three types as PDFs |
| `app/services/llm/client.py` | Bedrock boto3 client — `invoke_with_image` for OCR, `invoke_with_text` for classification/field extraction, `retry_llm_call` for retry wrapper |
| `app/prompts.py` | `OCR_EXTRACTION_PROMPT`, `DOCUMENT_CLASSIFICATION_PROMPT`, `FIELD_EXTRACTION_PROMPT`, `IC_NOTES_EXTRACTION_PROMPT` |
| `app/repositories/ingestion.py` | `IngestionRepository` — `create_case()`, `get_case_by_name()`, `list_cases()`, `save_files()`, `get_filed_docs()`, `get_unextracted_ic_notes()`, `save_form_fields()`, `get_case_files()`, `get_case_form_fields()`, `get_file_by_filename()` |

## Prompts

| Prompt | Purpose |
|---|---|
| `OCR_EXTRACTION_PROMPT` | Extracts all text from a page image preserving structure |
| `DOCUMENT_CLASSIFICATION_PROMPT` | Classifies a document as `exhibit`, `filed_doc`, or `ic_notes` using first-page (or first-page-equivalent, for `.docx`) text — shared by both the PDF and `.docx` pipelines |
| `FIELD_EXTRACTION_PROMPT` | Extracts every filled-in field label + value from a `filed_doc`'s full text into a flat JSON object; only runs for `filed_doc`-classified files, regardless of whether the source was a PDF or a `.docx` |
| `IC_NOTES_EXTRACTION_PROMPT` | Extracts every concrete fact stated in an `ic_notes`-classified file's free-form text into a flat JSON object, using descriptive keys the model chooses itself (there are no fixed form labels to copy, unlike `FIELD_EXTRACTION_PROMPT`'s input); only runs for `ic_notes`-classified files |

## Classification

Both PDFs and `.docx` files are automatically classified into the same three types (an LLM call, gated by `CLASSIFY_PDF_LLM`):

| Type | Description |
|---|---|
| `exhibit` | Proof document provided by the client (e.g. birth certificate, passport, bank statement) |
| `filed_doc` | Form or application filled out by the client for the legal process, with fixed fields/labels |
| `ic_notes` | Free-form intake/case notes written by attorney or staff — client/case demographics, the underlying issue, and/or drafting guidance for the document being prepared. No fixed fields. |

Only the first-page (PDF) or first-page-equivalent (`.docx`, see "File Type Routing" above) text is sent for classification — enough to identify the document type while keeping the LLM call fast and cheap. If classification returns an unexpected value, it defaults to `exhibit`.

**Classification is based on content, not file type** — a `.docx` isn't assumed to be `ic_notes` just because of its extension, and in principle a PDF could be classified `ic_notes` too (e.g. a scanned printout of notes), though in practice `ic_notes` files are essentially always native `.docx`.

## Field Extraction

After `ingestion_files` rows are saved, the `POST /{case_name}` endpoint calls `IngestionRepository.get_filed_docs(case_id)` — a real DB query, not an in-memory filter of just-saved rows — to find every `filed_doc` for this case that hasn't been extracted yet. Never runs on `exhibit` files, since proof documents (passports, bank statements) have no fixed field structure to extract.

- **DB-driven, not request-scoped**: `get_filed_docs()` returns `WHERE case_id = ? AND doc_type = 'filed_doc' AND fields_extracted = false` — this picks up forms from *any* prior `/ingest` request for this case, not just files uploaded in the current one. Calling `/ingest` again for the same case (even uploading an unrelated file) will pick up and extract any previously-unextracted forms.
- **Skips files with no `ocr_text`**: a `filed_doc` whose OCR failed (`ocr_text` is null) is excluded from extraction, not just ones already `fields_extracted = true`.
- **Dedup via `fields_extracted`**: once a file is successfully extracted, `save_form_fields()` sets `ingestion_files.fields_extracted = true` in the same transaction as inserting the `form_fields` row — so it's excluded from `get_filed_docs()` on every subsequent call. A file that fails extraction stays `false` and will be retried on the next `/ingest` call for that case.
- The file's full `ocr_text` (all pages, already stitched) is sent to the LLM via `FIELD_EXTRACTION_PROMPT`, which asks for a flat `{label: value}` JSON object of every filled-in field, using the form's own label text.
- Runs concurrently across files in the batch (a separate semaphore from OCR's, but using the same `INGESTION_CONCURRENCY` limit), wrapped in `retry_llm_call` same as everything else.
- The LLM's response is parsed defensively (`_parse_fields_json`) — strips a markdown code fence if the model adds one despite being told not to, then `json.loads()`s the result.
- On failure (LLM error or invalid JSON), that file just doesn't get a `form_fields` row — logged via `field_extraction_failed` / `field_extraction_partial_failure`, doesn't fail the request or affect other files.
- **Not included in `/ingest`'s own response** — `IngestResponse` is unchanged, still just OCR text + classification. Extracted fields are fetched separately via `GET /api/v1/ingest/{case_name}` — see "Fetching Ingested Data" below.
- **No standalone endpoint to trigger extraction** — it only runs as a side effect of `/ingest`; there's no way to kick it off independently of an OCR request yet.

**IC Notes extraction follows the identical pattern**, in parallel: `IngestionRepository.get_unextracted_ic_notes(case_id)` (same DB-driven, not request-scoped query shape as `get_filed_docs()`, just filtered to `doc_type = 'ic_notes'`) finds every unextracted IC Notes file for the case, `extract_ic_notes_fields()` sends each one's full extracted text to `IC_NOTES_EXTRACTION_PROMPT` instead of `FIELD_EXTRACTION_PROMPT` — the prompt differs because IC Notes are free-form prose with no fixed field labels to copy, so the model chooses its own descriptive JSON keys rather than reusing labels already printed on a form. Both extraction paths share the same underlying batching/retry/JSON-parsing code (`_extract_fields_batch()` in `pipeline.py`) and write to the exact same `form_fields` table, via the exact same `save_form_fields()` call. This is why IC Notes facts reach the drafting agent with zero changes needed on that side: `generate_draft_node`/`resolve_filing_data_node` build "Form Data" from every `form_fields` row for the case regardless of which `doc_type` produced it — see `docs/draft.md`.

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
    "IC Facts and Petition Guidance.docx": {
      "name": "IC Facts and Petition Guidance.docx",
      "text": "extracted text...",
      "type": "ic_notes"
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

Failed files (PDF or `.docx`) do not break the whole batch — they are captured separately and returned in `failed`.

## Fetching Ingested Data

`GET /api/v1/ingest/{case_name}` — resolves `case_name` to the case row, then a plain DB read via `IngestionRepository`. Returns every `ingestion_files` row for the case, each merged with its `form_fields` row if one exists (matched via `ingestion_file_id`). The response body still identifies the case by its numeric `case_id` (see example below), since that's the internal key `files` are actually stored under.

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
    },
    {
      "id": "3a7b9c21-4321-4e8f-9d2a-fedcba098765",
      "filename": "IC Facts and Petition Guidance.docx",
      "doc_type": "ic_notes",
      "ocr_text": "Khaleda Akhter Begum, 9/16/1936 DOB...",
      "error": null,
      "fields_extracted": true,
      "fields": {
        "client_name": "Khaleda Akhter Begum",
        "aip_date_of_birth": "September 16, 1936",
        "diagnosis": "Alzheimer's, diagnosed approximately 3 years ago"
      }
    }
  ]
}
```

`exhibit`/`unclassified` files always have `fields: null` (extraction only runs on `filed_doc` and `ic_notes` files). Files that failed OCR/extraction show `error` populated and `ocr_text: null`.

Repository methods: `IngestionRepository.get_case_files(case_id)`, `get_case_form_fields(case_id)` (`app/repositories/ingestion.py`). Schema: `CaseIngestionResponse` / `IngestedFile` (`app/schemas/ingestion.py`).

## ENV vars

| Key | Description |
|---|---|
| `AWS_REGION` | AWS region for Bedrock |
| `AWS_ACCESS_KEY_ID` | AWS credentials |
| `AWS_SECRET_ACCESS_KEY` | AWS credentials |
| `BEDROCK_MODEL_ID` | Model to use for OCR and classification |
| `CLASSIFY_PDF_LLM` | `true`/`false` — gates the classification step for **both** PDFs and `.docx` files (name predates `.docx` support, kept as-is to avoid a breaking rename). OCR/text extraction always runs regardless. |
| `INGESTION_CONCURRENCY` | Max parallel PDFs, and separately max parallel `.docx` files (each pipeline has its own semaphore, both sized off this same value) (default 3) |
| `LLM_MAX_RETRIES` | Max retry attempts per LLM call (default 3) |
