# Database

Postgres, accessed via SQLAlchemy's async engine (`psycopg3`), migrated with Alembic. One model file per feature domain in `app/models/` — never a monolithic `models.py`.

## Connection

`app/core/db.py` builds the async engine from `DATABASE_URL` (rewriting the `postgresql://` scheme to `postgresql+psycopg://` for the async driver) and exposes `AsyncSessionLocal` + a `get_db()` FastAPI dependency.

```
DATABASE_URL=postgresql://<user>:<password>@<host>:<port>/<dbname>
```

URL-encode special characters in the password (e.g. `@` → `%40`) — see `docs/setup.md` for the full local-setup gotchas.

## Migrations (Alembic)

- Config: `alembic.ini`, `alembic/env.py`, `alembic/versions/`
- `alembic/env.py` imports every model explicitly so `Base.metadata` (autogenerate's source of truth) sees them — **new models must be added to the import list there** or `--autogenerate` won't detect them.
- Windows fix: `alembic/env.py` forces `WindowsSelectorEventLoopPolicy` because `psycopg`'s async driver can't use the default `ProactorEventLoop`.
- Workflow:
  ```powershell
  # after adding/changing a model:
  alembic revision --autogenerate -m "description"
  alembic upgrade head
  ```
- **LangGraph's checkpoint tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`) are NOT managed by Alembic.** They're created automatically by `AsyncPostgresSaver(pool).setup()` at app startup (see `docs/setup.md`). Autogenerate will see them as "unmanaged/extraneous" — strip any drop/create ops for them out of a generated migration before committing it (see the note in `95a8b0b0ffcb_create_form_filing_addresses_table.py` for an example of this being done).

## Tables

### `cases`

The anchor record for a client's ingested data — not tied to any particular draft type. Every other case-scoped table points back to this via `case_id`. A case carries no `process_type` of its own: the same case's ingested data can be drafted against any `process_type` (looked up independently from `templates`, see `docs/templates.md`), chosen at draft-generation time (`docs/draft.md`), not fixed at case creation.

| Column | Type | Notes |
|---|---|---|
| `id` | Integer, PK, autoincrement | Internal anchor other tables FK to (`ingestion_files.case_id`, `form_fields.case_id`) — not the identifier callers use. |
| `case_name` | String, not null, unique | The human-facing identifier — set once at `POST /session/start`, enforced unique at the DB level (`uq_cases_case_name`) so it can be used directly in API paths (`/ingest/{case_name}`, `/draft/{case_name}/{process_type}/...`) without ambiguity. |
| `created_at` | DateTime (tz) | `server_default=now()` |

Model: `app/models/case.py`. Repository: `app/repositories/ingestion.py` — `create_case()` (raises on a duplicate `case_name`, caught by the endpoint and returned as `400`), `get_case_by_name()` (resolves the API-facing name to the row, used by every downstream endpoint), `list_cases()` (backs `GET /api/v1/session/cases`, the enum a UI selects a case from).

### `ingestion_files`

One row per uploaded PDF (not one row per case) — a case that uploads 5 files gets 5 rows sharing the same `case_id`.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID, PK, default `uuid4()` | |
| `case_id` | Integer, FK → `cases.id`, indexed, not null | |
| `filename` | String, not null | Original filename. |
| `doc_type` | String, nullable | `"exhibit"` \| `"filed_doc"` \| `null` when `CLASSIFY_PDF_LLM=false`. |
| `ocr_text` | Text, nullable | Nullable because OCR can fail independently of the S3 upload — see `error`. |
| `s3_url` | String, nullable | Nullable for the same reason, in the other direction. |
| `error` | String, nullable | Set when OCR and/or S3 upload failed for this file. |
| `fields_extracted` | Boolean, not null, default `false` | Set to `true` once a `form_fields` row exists for this file — see the `form_fields` dedup note below. |
| `created_at` | DateTime (tz) | `server_default=now()` |

Model: `app/models/ingestion.py`. Repository: `app/repositories/ingestion.py` (`create_case()`, `save_files()`).

**Status:** wired — `POST /session/start` creates the `cases` row from a `case_name` (plain DB write, no graph); `POST /api/v1/ingest/{case_name}` resolves the name to its row, then writes one `ingestion_files` row per uploaded PDF (OCR text + classification) after the pipeline runs — a plain endpoint call, not a graph node (see `docs/ingestion.md`). `s3_url` is currently always `null` — S3 upload isn't built yet (deliberately deferred, no bucket configured).

### `form_fields`

One row per `filed_doc`-classified PDF — the structured field extraction ("Extract the JSON (all fields) of forms" step), distinct from the raw `ocr_text` on `ingestion_files`. Never populated for `exhibit`-classified files (proof documents have no fixed field structure).

| Column | Type | Notes |
|---|---|---|
| `id` | UUID, PK, default `uuid4()` | |
| `ingestion_file_id` | UUID, FK → `ingestion_files.id`, indexed, not null | Which PDF this extraction came from. |
| `case_id` | Integer, FK → `cases.id`, indexed, not null | Denormalized from `ingestion_files.case_id` so "all fields for this case" doesn't need a join. |
| `fields` | JSONB, not null | Flat `{label: value}` object — the model's own label text for each key, only fields with an actual filled-in value (blanks/unchecked/N/A are omitted). |
| `created_at` | DateTime (tz) | `server_default=now()` |

Model: `app/models/ingestion.py` (`FormFields`, alongside `IngestionFile`). Repository: `app/repositories/ingestion.py` (`save_form_fields()`).

**Status:** wired and verified end-to-end. After `ingestion_files` rows are saved, the `POST /api/v1/ingest/{case_name}` endpoint calls `IngestionRepository.get_filed_docs(case_id)` — a real `SELECT ... WHERE case_id = ? AND doc_type = 'filed_doc' AND fields_extracted = false`, not an in-memory filter — so it picks up *every* unextracted form for the case, not just files from the current request. This means calling `/ingest` again for the same case re-checks all its forms and extracts any that were added or flagged since the last call, without re-processing ones already done. `save_form_fields()` sets `fields_extracted = true` in the same transaction as inserting the `form_fields` row, so the two never drift out of sync. A file that fails extraction stays `fields_extracted = false` and will be retried on the next `/ingest` call for that case — logged via `field_extraction_partial_failure`, doesn't block the rest of the request. Not surfaced in `/ingest`'s own `POST` response, but fetchable via `GET /api/v1/ingest/{case_name}` — see `docs/ingestion.md`.

### `form_fees`

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

Unique constraint: `(form_number, filing_category)`. Model: `app/models/fee.py`.

Repository: `app/repositories/fee.py` (`FeeRepository.bulk_upsert`, `.list_all`, `.get_by_form_number`). `get_by_form_number()` — added for the draft agent's filing-data lookup, see `docs/draft.md` — is **not** an exact `form_number` match; it fuzzy-matches (via `rapidfuzz`, `app/repositories/fuzzy_match.py`, shared with `AddressRepository`) since a caller's form number isn't guaranteed to be formatted identically to the scraped column. Returns `(rows, FormNumberMatch)` — empty rows if the best match scores below `FUZZY_MATCH_THRESHOLD` (80/100) or there are no candidates at all.

### `form_address`

USCIS filing/lockbox addresses, scraped via `requests`. Admin/data-utility table, same category as `form_fees`. See `docs/scraping.md` (Address Scraping section).

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

Unique constraint: `(form_number, filing_scenario, lockbox_name)`. Model: `app/models/address.py`.

Repository: `app/repositories/address.py` (`AddressRepository.bulk_upsert`, `.list_all`, `.get_by_form_number`) — same fuzzy-match lookup as `FeeRepository` above, sharing `app/repositories/fuzzy_match.py`.

### `templates`

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
cases (1) ──< (many) ingestion_files      [ingestion_files.case_id → cases.id]
ingestion_files (1) ──< (0..1) form_fields [form_fields.ingestion_file_id → ingestion_files.id]
cases (1) ──< (many) form_fields          [form_fields.case_id → cases.id, denormalized]

form_fees        — standalone, no FK (looked up by form_number at draft time)
form_address     — standalone, no FK (looked up by form_number at draft time)
```

No FK from `form_fees` / `form_address` to `cases` — they're reference/lookup data scraped independently of any case, joined in application code (not the DB) when a draft needs a form's fee/address.

For migration history, see `alembic/versions/` and `git log`.

## Planned (not yet built)

- **Draft storage — no model yet.** The draft *agent* itself is built and working (`docs/draft.md`) — this is specifically about persistence: the generated draft currently only exists inside the LangGraph checkpoint (Postgres-backed, survives restarts, but not a real queryable table). There's no `GET` endpoint to fetch a case's draft outside of the `/generate`/`/approve` response itself. Deliberately deferred until after seeing real agent output, to avoid guessing at a schema prematurely. Likely to follow the same pattern as `ingestion_files`: a table keyed by `case_id`. The cover letter is part of the same draft document, not a separately stored artifact — no separate "cover letter" table is planned.
