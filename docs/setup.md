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
   `app/config.py` have defaults, but a few don't and will crash
   `Settings()` on import if missing from `.env`:
   - `CLASSIFY_PDF_LLM` (`true`/`false`) — toggles whether `/ingest` runs
     OCR+classification via Bedrock, or just returns filenames as-is. Set
     `false` if you don't have AWS Bedrock credentials configured locally.
   - `DATABASE_URL` — see above.

   Everything else (`AWS_*`, `SECRET_KEY`, etc.) has safe defaults for
   local dev and isn't required unless you're actually exercising that
   code path (e.g. AWS creds are only needed if `CLASSIFY_PDF_LLM=true`;
   `FIRECRAWL_API_KEY` is only needed if you're hitting
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
   available when `DEBUG=true`).

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
