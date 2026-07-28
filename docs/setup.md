# Local Setup

## Prerequisites

- Python 3.12+
- A local PostgreSQL instance (pgAdmin or any client works — you just need
  a connection string)

## Steps

1. **Create and activate a venv** (from the `backend/` directory):
   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   ```

2. **Install dependencies:**
   ```powershell
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

3. **Create a database** for the project (any name), then set `.env`:
   ```
   DATABASE_URL=postgresql://<user>:<password>@<host>:<port>/<dbname>
   ```
   If your password contains special characters (e.g. `@`), URL-encode
   them — `@` becomes `%40`, etc. Otherwise the connection string parser
   misreads where the credentials end and the host begins.

4. **Set the remaining required env vars.** Most settings in
   `app/config.py` have defaults. Two have no default and will crash
   `Settings()` on import if missing from `.env`:
   - `CLASSIFY_PDF_LLM` (`true`/`false`) — toggles whether `/ingest` also
     classifies each document as `exhibit`/`filed_doc` via Bedrock. OCR
     always runs regardless of this flag; when `false`, extracted text is
     returned with `type: null` instead of being classified.
   - `TEMPLATE_CONCURRENCY` (int) — max sample `.docx` files parsed in
     parallel by `POST /api/v1/template-generation/{process_type}` (see
     `docs/templates.md`) — not an LLM call, just bounds concurrent
     `python-docx` parsing. Same pattern as `INGESTION_CONCURRENCY`, just a
     separate knob since it's a fully independent feature.

   `DATABASE_URL` does have a default (empty string), so it won't crash
   `Settings()` on import — but without a real value the app fails as soon
   as it tries to connect. Set it as described above.

   Everything else (`AWS_*`, `SECRET_KEY`, etc.) has safe defaults for
   local dev, but `AWS_*` creds are required to run `/ingest` at all
   (OCR uses Bedrock unconditionally, regardless of `CLASSIFY_PDF_LLM`).
   (`FIRECRAWL_API_KEY` is only needed if you're hitting
   `POST /api/v1/fees/scrape` — see `docs/scraping.md`).

5. **Run the database migrations:**
   ```powershell
   alembic upgrade head
   ```
   (To generate a new migration after changing a model:
   `alembic revision --autogenerate -m "description"`, then
   `alembic upgrade head`.)

6. **Start the app:**
   ```powershell
   uvicorn app.main:app --reload
   ```
   Visit `http://127.0.0.1:8000/docs` for the interactive API docs (only
   available when `DEBUG=true`). On startup, a `config_loaded` structured
   log line (`app/main.py`) dumps every operationally-relevant setting
   currently in effect (concurrencies, retries, delays, timeouts, model ID,
   region, log level — secrets like `AWS_SECRET_ACCESS_KEY`,
   `FIRECRAWL_API_KEY`, `SECRET_KEY`, and `DATABASE_URL` are deliberately
   excluded) — check this first when a run behaves differently than
   expected, rather than re-reading `.env` by hand.

## Utility Scripts

Both are standalone — not wired into the FastAPI app, not reachable over
HTTP, run manually from the `backend/` directory. Both use the same
`DATABASE_URL` as the app (via `app.core.db.engine`), require typing `yes`
to confirm, and print row counts before/after so the effect is visible.

- **`python reset_db.py`** — wipes all case/ingestion/template data
  (`cases`, `ingestion_files`, `form_fields`, `templates`, plus LangGraph's
  checkpoint tables) in one atomic `TRUNCATE ... CASCADE`. Leaves scraped
  reference data (`form_fees`, `form_address`) untouched. Useful after a
  schema-breaking change, so stale rows/checkpoints from before don't
  linger against a new schema.
- **`python reset_scraped_data.py`** — wipes `form_fees` and/or
  `form_address` only (interactive prompt: fees, addresses, or both).
  These have no foreign keys in either direction (see `docs/database.md`),
  so wiping them is always safe in isolation, independent of case data.

## Streamlit Test UI

`backend/streamlit/` — a separate, lightweight manual-test UI (Streamlit),
complementary to Swagger (`/docs`): Swagger is better for poking at one
endpoint in isolation, this is better for walking the actual multi-step
flow (case → templates → ingest → generate → review/approve) without
manually copying IDs between requests. It's a plain HTTP client hitting
the same backend `requests` would — no shared code, no shared state beyond
whatever's actually in the database.

Setup (separate from the backend's own dependencies/`.env` — kept in its
own `requirements.txt` so Streamlit never ends up in the API's deploy):
```powershell
pip install -r streamlit\requirements.txt
copy streamlit\.env.example streamlit\.env
streamlit run streamlit\app.py
```
Requires the backend running separately (`uvicorn app.main:app --reload`)
in another terminal.

`streamlit/.env` needs one required value, `API_BASE_URL` (e.g.
`http://127.0.0.1:8000/api/v1`) — required with no default (same
fail-loud pattern as `CLASSIFY_PDF_LLM`/`TEMPLATE_CONCURRENCY` above), read
via `streamlit/config.py`'s own `pydantic-settings` instance, resolved
relative to that file's own location (not the working directory) so it
always finds `streamlit/.env` regardless of which directory `streamlit
run` is launched from.

## Windows-specific gotchas

These are already fixed in the codebase, but documented here in case they
resurface (e.g. after an `alembic`/`psycopg` version bump):

- **`psycopg`'s async driver needs a `SelectorEventLoop`.** Windows
  defaults to `ProactorEventLoop`, which `psycopg` can't use for async
  connections — it fails with `psycopg.InterfaceError: Psycopg cannot use
  the 'ProactorEventLoop'...`. Fixed by setting
  `asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())`
  near the top of both `app/main.py` and `alembic/env.py`, guarded by
  `sys.platform == "win32"`.
- **A `%` in your DB password breaks `alembic.ini`.** `alembic.ini` is
  read via Python's `configparser`, which treats `%` as the start of
  interpolation syntax (`%(name)s`). If your password has a URL-encoded
  character (e.g. `%40` for `@`), calling
  `config.set_main_option("sqlalchemy.url", url)` throws
  `ValueError: invalid interpolation syntax`. `alembic/env.py` avoids this
  by building the engine from a plain Python dict instead of routing the
  URL through `configparser`.

## LangGraph checkpointer

`app/main.py`'s startup calls `AsyncPostgresSaver(pool).setup()`, which
creates LangGraph's own checkpoint tables automatically the first time the
app boots against your DB — **this is separate from Alembic**, nothing to
run manually for it. It does require the connection pool to be in
`autocommit` mode (`AsyncConnectionPool(..., kwargs={"autocommit":
True})`), because one of its setup migrations runs `CREATE INDEX
CONCURRENTLY`, which can't execute inside a transaction.
