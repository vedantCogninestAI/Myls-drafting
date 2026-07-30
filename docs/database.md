# Database

MySQL, accessed via SQLAlchemy's async engine (`asyncmy`), migrated with Alembic. One model file per feature domain in `app/models/` — never a monolithic `models.py`.

**Was Postgres until 2026-07-31** — this project moved to MySQL to match the naming/schema conventions of the shared client database it now runs against (see "Table naming convention" below). The old Postgres-dialect migration history was squashed into a single fresh MySQL-native migration rather than translated statement-by-statement — see "Migrations" below. If you need the old Postgres schema/history for reference, it's in `git log` prior to that date.

## Connection

`app/core/db.py` builds the async engine from `DATABASE_URL` (rewriting the `mysql://` scheme to `mysql+asyncmy://` for the async driver) and exposes `AsyncSessionLocal` + a `get_db()` FastAPI dependency.

```
DATABASE_URL=mysql://<user>:<password>@<host>:<port>/<dbname>
```

URL-encode special characters in the password (e.g. `@` → `%40`) — see `docs/setup.md` for the full local-setup gotchas.

## Migrations (Alembic)

- Config: `alembic.ini`, `alembic/env.py`, `alembic/versions/`
- `alembic/env.py` imports every model explicitly so `Base.metadata` (autogenerate's source of truth) sees them — **new models must be added to the import list there** or `--autogenerate` won't detect them.
- Windows fix: `alembic/env.py` forces `WindowsSelectorEventLoopPolicy` because `asyncmy`'s async driver can't use the default `ProactorEventLoop`.
- Single migration, no history to replay: `f6c2a80b9d3e_create_all_tables_mysql.py` creates all 6 tables directly, MySQL-native types from the start (`JSON` not `JSONB`, `Uuid`/`CHAR(32)` not Postgres-native `UUID`, explicit `VARCHAR` lengths — MySQL's InnoDB requires them, unlike Postgres's unbounded `VARCHAR`).
- Workflow:
  ```powershell
  # after adding/changing a model:
  alembic revision --autogenerate -m "description"
  alembic upgrade head
  ```
- **LangGraph's checkpoint tables are NOT managed by Alembic.** They're created automatically by `AsyncMySaver.setup()` at app startup (see `docs/setup.md`) — via `langgraph-checkpoint-mysql`, not the Postgres-only `langgraph-checkpoint-postgres` used before. Their names are hardcoded inside that library itself (not configurable), so they don't follow this project's `tb_..._draft_ai` table-naming convention below — autogenerate will see them as "unmanaged/extraneous" — strip any drop/create ops for them out of a generated migration before committing it.

## Table naming convention

Every table this project owns (i.e. every SQLAlchemy model in `app/models/`) is named `tb_<name>_draft_ai` — e.g. the cases table is `tb_cases_draft_ai`. Matches the naming convention already used across the rest of the shared client database this project's tables now live alongside. Created directly under these names by the single migration above — doesn't apply to LangGraph's own checkpoint tables, see above.

## Tables

### `tb_cases_draft_ai`

The anchor record for a client's ingested data — not tied to any particular draft type. Every other case-scoped table points back to this via `case_id`. A case carries no `process_type` of its own: the same case's ingested data can be drafted against any `process_type` (looked up independently from `tb_templates_draft_ai`, see `docs/templates.md`), chosen at draft-generation time (`docs/draft.md`), not fixed at case creation.

| Column | Type | Notes |
|---|---|---|
| `id` | Integer, PK, autoincrement | Internal anchor other tables FK to (`tb_ingestion_files_draft_ai.case_id`, `tb_form_fields_draft_ai.case_id`) — not the identifier callers use. |
| `case_name` | String, not null, unique | The human-facing identifier — set once at `POST /session/start`, enforced unique at the DB level (`uq_tb_cases_draft_ai_case_name`) so it can be used directly in API paths (`/ingest/{case_name}`, `/draft/{case_name}/{process_type}/...`) without ambiguity. |
| `created_at` | DateTime (tz) | `server_default=now()` |

Model: `app/models/case.py`. Repository: `app/repositories/ingestion.py` — `create_case()` (raises on a duplicate `case_name`, caught by the endpoint and returned as `400`), `get_case_by_name()` (resolves the API-facing name to the row, used by every downstream endpoint), `list_cases()` (backs `GET /api/v1/session/cases`, the enum a UI selects a case from).

### `tb_ingestion_files_draft_ai`

One row per uploaded file — PDF or `.docx` (not one row per case) — a case that uploads 5 files gets 5 rows sharing the same `case_id`.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID, PK, default `uuid4()` | |
| `case_id` | Integer, FK → `tb_cases_draft_ai.id`, indexed, not null | |
| `filename` | String, not null | Original filename. |
| `doc_type` | String, nullable | `"exhibit"` \| `"filed_doc"` \| `"ic_notes"` \| `null` when `CLASSIFY_PDF_LLM=false`. Same three-way classification for both PDF and `.docx` uploads — see `docs/ingestion.md`. |
| `ocr_text` | Text, nullable | Nullable because text extraction can fail independently of the S3 upload — see `error`. For `.docx` this is `python-docx`-extracted text, not OCR (column name is a naming leftover, kept as-is). |
| `s3_url` | String, nullable | Nullable for the same reason, in the other direction. |
| `error` | String, nullable | Set when text extraction and/or S3 upload failed for this file. |
| `fields_extracted` | Boolean, not null, default `false` | Set to `true` once a `tb_form_fields_draft_ai` row exists for this file — see the dedup note below. |
| `created_at` | DateTime (tz) | `server_default=now()` |

Model: `app/models/ingestion.py`. Repository: `app/repositories/ingestion.py` (`create_case()`, `save_files()`).

**Status:** wired — `POST /session/start` creates the `tb_cases_draft_ai` row from a `case_name` (plain DB write, no graph); `POST /api/v1/ingest/{case_name}` resolves the name to its row, then writes one `tb_ingestion_files_draft_ai` row per uploaded file (PDF or `.docx`; OCR/text extraction + classification) after the pipeline runs — a plain endpoint call, not a graph node (see `docs/ingestion.md`). `s3_url` is currently always `null` — S3 upload isn't built yet (deliberately deferred, no bucket configured).

### `tb_form_fields_draft_ai`

One row per `filed_doc`- or `ic_notes`-classified file (PDF or `.docx`) — the structured field extraction (`FIELD_EXTRACTION_PROMPT` for `filed_doc`, `IC_NOTES_EXTRACTION_PROMPT` for `ic_notes` — different prompts, same table/shape), distinct from the raw `ocr_text` on `tb_ingestion_files_draft_ai`. Never populated for `exhibit`-classified files (proof documents have no fixed field structure).

| Column | Type | Notes |
|---|---|---|
| `id` | UUID, PK, default `uuid4()` | |
| `ingestion_file_id` | UUID, FK → `tb_ingestion_files_draft_ai.id`, indexed, not null | Which file this extraction came from. |
| `case_id` | Integer, FK → `tb_cases_draft_ai.id`, indexed, not null | Denormalized from `tb_ingestion_files_draft_ai.case_id` so "all fields for this case" doesn't need a join. |
| `fields` | JSONB, not null | Flat JSON object. For `filed_doc`: `{label: value}` using the form's own printed labels, only fields with an actual filled-in value (blanks/unchecked/N/A omitted). For `ic_notes`: descriptive keys the model chooses itself (there are no printed labels in free-form notes), only facts actually stated. |
| `created_at` | DateTime (tz) | `server_default=now()` |

Model: `app/models/ingestion.py` (`FormFields`, alongside `IngestionFile`). Repository: `app/repositories/ingestion.py` (`save_form_fields()`).

**Status:** wired and verified end-to-end for `filed_doc`; the `ic_notes` path shares the same table/mechanism (text-extraction verified against a real sample file, live LLM extraction not yet run — see `docs/ingestion.md`). After `tb_ingestion_files_draft_ai` rows are saved, `POST /api/v1/ingest/{case_name}` runs two parallel extraction passes: `IngestionRepository.get_filed_docs(case_id)` (`SELECT ... WHERE doc_type = 'filed_doc' AND fields_extracted = false`) and `get_unextracted_ic_notes(case_id)` (same shape, `doc_type = 'ic_notes'`) — both real DB queries, not in-memory filters, so each picks up *every* unextracted file of its type for the case, not just ones from the current request. This means calling `/ingest` again for the same case re-checks all outstanding files of both types and extracts any that were added or previously failed, without re-processing ones already done. `save_form_fields()` sets `fields_extracted = true` in the same transaction as inserting the `tb_form_fields_draft_ai` row (same call, used by both paths), so the two never drift out of sync. A file that fails extraction stays `fields_extracted = false` and will be retried on the next `/ingest` call for that case — logged via `field_extraction_partial_failure` / `ic_notes_extraction_partial_failure`, doesn't block the rest of the request. Not surfaced in `/ingest`'s own `POST` response, but fetchable via `GET /api/v1/ingest/{case_name}` — see `docs/ingestion.md`.

### `tb_form_fees_draft_ai`

USCIS filing fees, scraped via Firecrawl. Admin/data-utility table — not tied to a case or `thread_id`. See `docs/scraping.md`.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID, PK, default `uuid4()` | |
| `form_number` | String, indexed, not null | e.g. `"I-485"` |
| `form_title` | String, not null | |
| `form_url` | String, nullable | |
| `filing_category` | String, not null | Part of the unique constraint with `form_number`. |
| `paper_fee` | Float, nullable | |
| `online_fee` | Float, nullable | |
| `fee_details` | JSONB, not null | |
| `scraped_at` | DateTime (tz) | `server_default=now()` |
| `created_at` | DateTime (tz) | `server_default=now()` |

Unique constraint: `(form_number, filing_category)` (`uq_tb_form_fees_draft_ai_form_category`). Model: `app/models/fee.py`.

Repository: `app/repositories/fee.py` (`FeeRepository.bulk_upsert`, `.list_all`, `.get_by_form_number`). `get_by_form_number()` — added for the draft agent's filing-data lookup, see `docs/draft.md` — is **not** an exact `form_number` match; it fuzzy-matches (via `rapidfuzz`, `app/repositories/fuzzy_match.py`, shared with `AddressRepository`) since a caller's form number isn't guaranteed to be formatted identically to the scraped column. Returns `(rows, FormNumberMatch)` — empty rows if the best match scores below `FUZZY_MATCH_THRESHOLD` (80/100) or there are no candidates at all.

### `tb_form_address_draft_ai`

USCIS filing/lockbox addresses, scraped via `requests`. Admin/data-utility table, same category as `tb_form_fees_draft_ai`. See `docs/scraping.md` (Address Scraping section).

| Column | Type | Notes |
|---|---|---|
| `id` | UUID, PK, default `uuid4()` | |
| `form_number` | String, indexed, not null | |
| `form_title` | String, not null | |
| `form_url` | String, nullable | |
| `filing_scenario` | String, not null | Part of the unique constraint. |
| `applies_to` | String, not null | |
| `lockbox_name` | String, not null | Part of the unique constraint. |
| `usps_address` | String, nullable | |
| `courier_address` | String, nullable | |
| `address_details` | JSONB, not null | |
| `scraped_at` | DateTime (tz) | `server_default=now()` |
| `created_at` | DateTime (tz) | `server_default=now()` |

Unique constraint: `(form_number, filing_scenario, lockbox_name)` (`uq_tb_form_address_draft_ai_form_scenario_lockbox`). Model: `app/models/address.py`.

Repository: `app/repositories/address.py` (`AddressRepository.bulk_upsert`, `.list_all`, `.get_by_form_number`) — same fuzzy-match lookup as `FeeRepository` above, sharing `app/repositories/fuzzy_match.py`.

### `tb_templates_draft_ai`

Reference/sample documents the draft-generation agent uses to model its output structure on (`docs/draft.md`). Admin/data-utility table — not tied to a case or `thread_id`. See `docs/templates.md`.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID, PK, default `uuid4()` | |
| `process_type` | String, indexed, not null | The only place a `process_type` is defined — not a formal FK to anywhere, just a string the draft agent matches against `DraftingState.process_type` (supplied per-request at `/draft/{case_name}/{process_type}/generate`, not stored on any case). See `docs/templates.md`. |
| `filename` | String, not null | Original filename of the sample `.docx`. |
| `ocr_text` | Text, not null | Column name is misleading — this is **not** OCR output, it's structure extracted directly from the `.docx` via `python-docx` (headings/lists/tables/alignment), zero LLM calls. See `docs/templates.md`. |
| `is_active` | Boolean, not null, default `false` | Meant to be admin-set — only active templates should be read by the draft agent, max 2 per `process_type` by convention (not DB-enforced). Currently never set to `true` by any code path — the draft agent works around this by using every row for a `process_type`, unfiltered. |
| `created_at` | DateTime (tz) | `server_default=now()` |

Model: `app/models/template.py`. Repository: `app/repositories/template.py` (`count_by_process_type()`, `save_templates()`, `get_by_process_type()`).

**Status:** upload + fetch wired — `POST /api/v1/template-generation/{process_type}` extracts structure from up to 5 sample `.docx` files and saves one row per sample (rejected with 400 once 5 rows already exist for that `process_type`); `GET /api/v1/template-generation/{process_type}` fetches all stored rows. **Gap:** nothing sets `is_active` yet — no activate/deactivate endpoint exists, so every row defaults to (and stays) `false`. The draft agent (`docs/draft.md`) works around this by fetching every template for a `process_type`, unfiltered, rather than a curated "2 active" subset.

## Relationships

```
tb_cases_draft_ai (1) ──< (many) tb_ingestion_files_draft_ai      [tb_ingestion_files_draft_ai.case_id → tb_cases_draft_ai.id]
tb_ingestion_files_draft_ai (1) ──< (0..1) tb_form_fields_draft_ai [tb_form_fields_draft_ai.ingestion_file_id → tb_ingestion_files_draft_ai.id]
tb_cases_draft_ai (1) ──< (many) tb_form_fields_draft_ai          [tb_form_fields_draft_ai.case_id → tb_cases_draft_ai.id, denormalized]

tb_form_fees_draft_ai        — standalone, no FK (looked up by form_number at draft time)
tb_form_address_draft_ai     — standalone, no FK (looked up by form_number at draft time)
```

No FK from `tb_form_fees_draft_ai` / `tb_form_address_draft_ai` to `tb_cases_draft_ai` — they're reference/lookup data scraped independently of any case, joined in application code (not the DB) when a draft needs a form's fee/address.

For migration history, see `alembic/versions/` and `git log`.

## Planned (not yet built)

- **Draft storage — no model yet.** The draft *agent* itself is built and working (`docs/draft.md`) — this is specifically about persistence: the generated draft currently only exists inside the LangGraph checkpoint (MySQL-backed, survives restarts, but not a real queryable table). There's no `GET` endpoint to fetch a case's draft outside of the `/generate`/`/approve` response itself. Deliberately deferred until after seeing real agent output, to avoid guessing at a schema prematurely. Likely to follow the same pattern as `tb_ingestion_files_draft_ai`: a table keyed by `case_id`. The cover letter is part of the same draft document, not a separately stored artifact — no separate "cover letter" table is planned.
