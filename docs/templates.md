# Template Generation

Admin/data-utility feature (see the LangGraph exception in `docs/architecture.md`) — not tied to a case or `thread_id`. An admin uploads sample "gold standard" draft Word documents (`.docx`) per `process_type`; each is parsed for structure and stored as a reference template. These are read later by the draft-generation agent (`docs/draft.md`) so it can model its output's structure on real prior drafts.

**Not OCR-based.** Templates are always native `.docx` files, not scans, so their structure (headings, alignment, tables) is read directly via `python-docx` rather than rendered to images and guessed at via a vision model. **Zero LLM calls** — faster, cheaper, and more accurate than OCR would be for this file type.

## Flow

```
POST /api/v1/template-generation/{process_type}   (upload up to 5 sample .docx files)
        ↓
endpoint checks existing count for this process_type via
TemplateRepository.count_by_process_type()
        ↓
if existing_count + new_files > 5 → reject with 400
        ↓
run_template_pipeline() — asyncio.Semaphore(TEMPLATE_CONCURRENCY): max N files at a time
        ↓
for each .docx (up to N concurrent, in a ThreadPoolExecutor — python-docx
parsing is synchronous/blocking):
    walk paragraphs + tables in true document order (python-docx's own
    .paragraphs/.tables are separate flat lists that lose interleaving —
    _iter_block_items() walks the raw XML body instead)
        ↓
    for each paragraph: map style name → Markdown heading level (#/##/...),
    bullet/numbered list (-/1.), or plain text; if alignment is CENTER,
    RIGHT, or JUSTIFY (read directly from paragraph.alignment.name — not a
    hardcoded lookup table), prefix the line with that label, e.g.
    "[CENTER] Some Heading". Left-aligned (or unset) stays unmarked — the
    default, no label.
        ↓
    for each table: convert rows/cells directly to a Markdown table
    (the draft agent's `word_formatter.py` converts this same Markdown
    table syntax back into a real `.docx` table when reconstructing a
    generated draft — see "Word Document Export" in `docs/draft.md`)
        ↓
    join every block with blank lines → one Markdown+label text per file
        ↓
TemplateRepository.save_templates() — one `templates` row per successfully
parsed file (process_type, filename, ocr_text; is_active defaults to false)
        ↓
Response: { "process_type": "...", "uploaded": [{"id": "...", "filename": "..."}], "failed": {...} }
```

## Alignment Convention

`[CENTER]` / `[RIGHT]` / `[JUSTIFY]` — plain text labels prefixed on a line, not HTML. Left-aligned is the unmarked default, since most content is left-aligned and marking every line would be noise. This is the *same* convention the draft agent is instructed to use in its own generated output (see `docs/draft.md`) — templates and generated drafts speak the same structural language.

**Deliberately not captured:** indentation, fonts, colors, exact spacing, headers/footers, images. Only alignment plus structural elements (headings, lists, tables) are tracked — a deliberate scope decision, not an oversight. `python-docx` can also read indentation (`paragraph.paragraph_format.left_indent`), which does matter for some templates, but scope is kept to alignment only for now.

## Fetching Stored Templates

`GET /api/v1/template-generation/{process_type}` — plain DB read via `TemplateRepository.get_by_process_type()`, returns every stored row for that draft type (not filtered to `is_active`, since this endpoint is for an admin reviewing what's there before deciding what to activate).

```json
{
  "process_type": "I-130",
  "templates": [
    {
      "id": "9f2c7e10-5678-4c1d-8e2f-abcdef654321",
      "filename": "sample_i130_approved.docx",
      "ocr_text": "[CENTER] GEHI & ASSOCIATES\n\n[CENTER] ATTORNEYS & COUNSELORS AT LAW\n\n...\n\n[JUSTIFY] I am the retained attorney...",
      "is_active": false
    }
  ]
}
```

(The column is still named `ocr_text` in the `templates` table/schema — a naming leftover from when this was OCR-based. Left as-is rather than renamed, since it's just a column/field name, not user-facing.)

## Cap and Activation — known gap

- **Max 5 templates per `process_type`.** Enforced at upload time (`MAX_TEMPLATES_PER_PROCESS_TYPE` in `app/api/v1/endpoints/template_generation.py`) — the whole upload request is rejected with a 400 if it would push the count over 5. No endpoint to delete a template yet, so once 5 exist for a type, no more can be added until one is removed directly in the DB.
- **`is_active` is never set to `true` by any code path.** No activate/deactivate endpoint exists. The draft agent works around this — it fetches *every* template for a `process_type` (no `is_active` filter, no cap to 2), not a curated subset. See `docs/draft.md`.

## Concurrency

- Max `TEMPLATE_CONCURRENCY` files processed simultaneously, controlled via `asyncio.Semaphore` — same pattern as ingestion, but a fully separate env var and a fully separate code path (`app/services/template_generation/pipeline.py` does not import anything from `app/services/ingestion/`)
- No default value — required in `.env` (same treatment as `CLASSIFY_PDF_LLM`)
- Each file's parsing runs in a `ThreadPoolExecutor` (via `run_in_executor`) since `python-docx` parsing is synchronous/blocking — kept off the event loop, the same way boto3 (also sync) is elsewhere

## Logging

| Event | Emitted from | When |
|---|---|---|
| `template_upload_received` | `template_generation.py` (endpoint) | files read into memory, before pipeline runs |
| `template_pipeline_started` / `_done` | `pipeline.py` | before/after all files are processed |
| `template_processing_started` | `pipeline.py` | per file, before parsing |
| `template_processing_done` | `pipeline.py` | per file, on success |
| `template_processing_failed` | `pipeline.py` | per file, on exception (logged with traceback via `logger.exception`) |
| `template_upload_done` | `template_generation.py` (endpoint) | before the response is returned |

## Key Files

| File | Role |
|---|---|
| `app/api/v1/endpoints/template_generation.py` | `POST /{process_type}` (upload + cap check) and `GET /{process_type}` (fetch) — no graph, straight `endpoint → service`/`repository` |
| `app/services/template_generation/pipeline.py` | `python-docx`-based structure extraction (own copy, not shared with `ingestion/pipeline.py`), `run_template_pipeline()`, `_iter_block_items()`, `_paragraph_to_markdown()`, `_table_to_markdown()` |
| `app/repositories/template.py` | `TemplateRepository` — `count_by_process_type()`, `save_templates()`, `get_by_process_type()` |
| `app/models/template.py` | `Template` model (`templates` table) |
| `app/schemas/template.py` | `TemplateUploadResponse` / `TemplateResult`, `TemplateListResponse` / `TemplateItem` |

## ENV vars

| Key | Description |
|---|---|
| `TEMPLATE_CONCURRENCY` | Max parallel `.docx` files parsed per upload request. No default — required in `.env`. |

No AWS/Bedrock env vars needed for this feature anymore — it's pure Python, no LLM calls.
