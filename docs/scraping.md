# USCIS Fee Scraping

## Goal

Scrape fee data for every form listed in USCIS's fee calculator dropdown
(https://www.uscis.gov/feecalculator?topic_id=97256) and store it in our own
DB (`form_fees_address` table), triggered on-demand via an API endpoint
(not a cron job — the firm triggers a re-scrape whenever they want fresh
data).

## Current status: working, Firecrawl-based, manually verified against live USCIS data

The feature is fully built and the fetch layer runs on Firecrawl (see
"Original blocker" below for why). Scraping, parsing, batching/rate
limiting, and DB writes have all been exercised end-to-end and
spot-checked against the live USCIS site. There is one known, deliberately
unfixed edge case — see "Known follow-up" below.

### How it works now

- `app/services/fee/scraper.py`'s `new_client()` returns an
  `AsyncFirecrawlApp` (from `firecrawl-py`, needs `FIRECRAWL_API_KEY` set
  in `.env`). `_fetch()` calls `client.scrape_url(url, formats=["html"])`
  and reads `result.html` — this is **not** a context manager, don't wrap
  it in `async with` (it doesn't implement `__aenter__`/`__aexit__`; this
  bit us once already in `app/api/v1/endpoints/fee.py`).
- `scrape_all_fees()` processes the ~132 forms in batches of
  `FEE_SCRAPE_CONCURRENCY` (default 5), sleeping
  `FEE_SCRAPE_BATCH_DELAY` seconds (default 5) between batches — this
  matters because Firecrawl's free tier rate-limits. Each batch's fee
  items are pushed to the DB immediately via an `on_batch` callback
  (supplied by `fee_service.run_fee_scrape`) rather than waiting for all
  132 forms to finish and doing one giant insert — `scraper.py` itself
  stays DB-free (no repository import), the callback is how persistence
  gets wired in from `fee_service.py`.
- `FeeRepository.bulk_upsert` dedupes incoming rows by
  `(form_number, filing_category)` before the insert, because Postgres's
  `ON CONFLICT DO UPDATE` cannot affect the same row twice in one
  statement (`CardinalityViolation`) — and USCIS's own data does contain
  same-key duplicates in some batches (e.g. the same form listed under
  multiple `topic_id`s in the dropdown).
- Structured logs (`structlog`, JSON) are emitted at every stage —
  `firecrawl_fetch`, `parse_form_list(_done)`, `parse_fee_page`,
  `fee_batch_upserted`, `scrape_all_fees_done`, `run_fee_scrape_done` —
  useful for watching a live scrape or debugging a specific form.

### Parsing correctness — what's handled

USCIS's fee tables aren't uniform: many rows pack **multiple sub-categories
with different fees into one `<tr>`** (e.g. I-129's "H-1B petition" /
"Small Employer or Nonprofit" pair, or the "Additional Fees: Asylum
Program Fee / Nonprofit / Small Employer" block — 2-3 distinct dollar
amounts in one row). The original naive parser flattened these into one
DB row and kept only the first dollar amount, silently dropping the rest.

Fixed, verified against live USCIS data (I-129, cross-checked row-by-row
against the site):
- `_cell_items()` in `_parse_fee_page` detects `<li>`-based multi-item
  cells (a nested list inside a `<td>` — the reliable "these are
  enumerated parallel options" signal) and splits that `<tr>` into one
  fee row per `<li>`, pairing each category sub-item with its own fee
  amount positionally. Columns with only one value (e.g. an "N/A" online
  fee that applies to all sub-items) broadcast that single value across
  the split rows instead of forcing a mismatched split.
- Repeated sub-case labels (e.g. "If you are filing as a Small Employer
  or Nonprofit.") that appear across multiple different `<tr>`s with
  **different fees per row** get the row's primary label prefixed on
  (`"{primary} — {sub_item}"`) so they don't collide on the
  `(form_number, filing_category)` upsert key and silently overwrite each
  other.
- `_row_to_fee_item`'s `filing_category` fallback no longer grabs an
  arbitrary column value when a form's table has no "Filing
  Category"/"Category" column at all (found via the G-639 form, whose
  table is just two fee columns) — it now defaults straight to
  `"General Filing"` instead of `next(iter(row.values()), ...)`, which
  was accidentally picking up a fee amount as the category label.

**Deliberately not auto-fixed:** cells where multiple dollar amounts sit
in the same `<td>` **without** `<li>` markup (found via I-102: a bolded
"Additional Form I-94 Fee: $24" sits as plain/paragraph text alongside
the main "$560" fee, no list). Splitting on `<p>` count was tried in
reasoning and rejected — `<p>` is just prose formatting, not a reliable
"these are N parallel options" signal, and would have created spurious
extra rows with wrong duplicated fees for merely-explanatory paragraphs.
Instead, `_parse_fee_page` logs a `possible_dropped_fee_amount` warning
(compares raw `$` count in any "...Fee" column's cell text vs. how many
fee items were actually captured from it) so these cases surface via logs
across all 132 forms during a real scrape, instead of being silently
guessed at or silently lost.

### Known follow-up (not yet done)

- **Grep scrape logs for `possible_dropped_fee_amount`** after a full run
  and manually resolve whatever forms it flags (I-102 is a confirmed
  instance; there are likely a handful of others with the same
  paragraph-based multi-fee pattern). Decide case-by-case or look for a
  common secondary pattern once a few are seen — don't guess blind.
- **Double `fetch_form_list()` call per `POST /scrape`**: the endpoint
  (`app/api/v1/endpoints/fee.py`) calls it once directly just to report
  `total_forms` in the response, then `scrape_all_fees()` calls it again
  internally — wastes one Firecrawl call per request. Minor, not fixed
  yet.
- `FEE_SCRAPE_TIMEOUT` (`app/config.py`) is unused dead code (was for the
  old `httpx` GET timeout) — either wire it into the Firecrawl call or
  remove it.
- Firecrawl cost: 132 forms scraped per `POST /fees/scrape` call. Worth
  checking usage against the free-tier/plan limits before this gets
  triggered often or automated — this is why batching + a delay between
  batches (`FEE_SCRAPE_CONCURRENCY` / `FEE_SCRAPE_BATCH_DELAY`) was added.

### Operational note: re-scraping after a parser change

Because the upsert key is `(form_number, filing_category)` and a parser
fix can change what `filing_category` text gets generated (e.g. a row
that used to merge into one now splits into several with new label text),
old rows from a previous parser version **will not get overwritten** by a
new scrape — they'll sit there stale alongside the new correct rows.
`TRUNCATE TABLE form_fees_address;` before re-scraping whenever the
parsing logic changes.

## Original blocker (context — resolved by moving to Firecrawl)

The feature was fully built (models, migration, repository, service, API
endpoint) but **the scrape call was blocked by USCIS's bot protection**.
This section is kept for context on why Firecrawl was chosen over a plain
HTTP client.

### What's confirmed

- USCIS's fee calculator page (`/feecalculator?topic_id=...`) is plain
  server-rendered HTML — no JavaScript rendering needed. Confirmed by
  reading the raw HTML: the dropdown's `<option value="{topic_id}"
  data-marker="{slug}">{label}</option>` list and the fee table
  (`<div class="form-fee-body"><table>...`) are both present in the raw
  response.
- A plain `curl -A "<Chrome UA string>" https://www.uscis.gov/feecalculator`
  succeeds (`200 OK`) reliably, every time we tested it.
- The same request via Python `httpx` (`httpx.AsyncClient` /
  `httpx.Client`), with an **identical** `User-Agent` and even a full
  browser-like header set (`Accept`, `Accept-Language`, `Accept-Encoding`,
  `Sec-Fetch-*`, etc.) **and** HTTP/2 enabled, still gets `403 Forbidden`.
- Re-testing `curl` again immediately after the `httpx` 403 still returns
  `200`, from the same machine/IP — so this isn't IP-level rate limiting.
- Conclusion: USCIS's bot protection (almost certainly Akamai Bot Manager,
  common on federal sites) is fingerprinting the **TLS handshake** itself
  (JA3/JA4 — cipher suite order, TLS extensions, ALPN, etc.), not just HTTP
  headers. `curl`'s TLS fingerprint passes; Python's standard `ssl`-based
  stack (which `httpx` uses) doesn't. No amount of header/HTTP-version
  tuning in `httpx` fixes this — it's a lower-layer signature.

### Decision: moved to Firecrawl

Two options were evaluated: `curl_cffi` (free, local TLS-impersonation
library — never actually tested against USCIS) vs. **Firecrawl** (paid
API, real-browser-equivalent requests). Firecrawl was chosen and
confirmed working — the fetch layer now goes through it end-to-end.

## What's built

| Piece | File | Status |
|---|---|---|
| DB model | `app/models/fee.py` (`FormFeeAddress`, table `form_fees_address`) | Done |
| Migration | `alembic/versions/3857ed402cdc_create_ingestion_results_and_form_fees_.py` | Done, applied locally |
| Repository | `app/repositories/fee.py` (`FeeRepository.bulk_upsert`, `.list_all`) | Done — dedupes by `(form_number, filing_category)` before insert |
| Schemas | `app/schemas/fee.py` (`FormFeeItem`, `ScrapeFeesResponse`) | Done |
| Orchestration | `app/services/fee/fee_service.py` (`run_fee_scrape`) | Done — wires per-batch persistence via callback |
| API endpoints | `app/api/v1/endpoints/fee.py` (`POST /fees/scrape`, `GET /fees`) | Done |
| HTML fetch + parse | `app/services/fee/scraper.py` | Done — Firecrawl-based, see parsing correctness above |

### `form_fees_address` schema (already decided, do not re-litigate)

Fields: `form_number`, `form_title`, `form_url`, `filing_category`,
`paper_fee`, `online_fee`, `fee_details` (raw scraped row as JSON, kept as
a safety net since fee table columns vary per form), `scraped_at`,
`created_at`. Upsert key is `(form_number, filing_category)`.

`topic_id` and `data_marker` (USCIS's internal identifiers) were
deliberately **not** persisted — they're only used transiently during
scraping to build the request URL.

**Mailing/filing addresses are explicitly out of scope for now.** The
table is named `form_fees_address` anticipating that addresses get added
later (each form has a separate `/{form}-addresses` page, e.g.
`/i-130-addresses`, with lockbox mailing addresses — confirmed present but
not yet modeled). Do not build this until asked; it was deferred on
purpose.

## Infra fixes made along the way (unrelated to Firecrawl, already resolved)

These were pre-existing gaps in the backend scaffold, unrelated to the fee
feature itself, discovered and fixed while getting the app to actually
run locally. Documented so they aren't re-debugged:

- **Alembic was completely unwired** (`alembic.ini`, `alembic/env.py`,
  `alembic/script.py.mako` were empty stub files, no versions existed).
  Wired up properly; `alembic/env.py` builds its DB URL as a plain Python
  string/dict rather than via `config.set_main_option()`, because
  `configparser` (which `alembic.ini` uses) treats `%` as interpolation
  syntax and chokes on URL-encoded characters in DB passwords (e.g. `%40`
  for `@`).
- **Windows + `psycopg` async driver incompatibility**: `psycopg`'s async
  mode requires a `SelectorEventLoop`; Windows defaults to
  `ProactorEventLoop`. Fixed by setting
  `asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())`
  at the top of both `alembic/env.py` and `app/main.py` (guarded by
  `sys.platform == "win32"`).
- **LangGraph checkpointer setup needs `autocommit=True`**:
  `AsyncPostgresSaver.setup()` runs `CREATE INDEX CONCURRENTLY`, which
  can't run inside a transaction. `AsyncConnectionPool` in `app/main.py`
  now passes `kwargs={"autocommit": True}`.
- **`app/api/v1/endpoints/health.py` was an empty stub** despite being
  imported by `router.py` and documented in `docs/api.md` — this crashed
  app startup entirely (`AttributeError: module ... has no attribute
  'router'`). Implemented a minimal `GET /api/v1/health/` health check.
- **`app/core/logging.py` had a `structlog` config bug that crashed every
  single request** (not just fee-related ones): it configured
  `structlog.PrintLoggerFactory()` but used
  `structlog.stdlib.add_logger_name` / `structlog.stdlib.add_log_level` in
  the processor chain, both of which expect a stdlib `logging.Logger`
  (with a `.name` attribute) that `PrintLogger` doesn't have. Fixed by
  switching to `structlog.processors.add_log_level` (non-stdlib
  equivalent) and dropping `add_logger_name`.
- Local `.env` was missing `DATABASE_URL` and `CLASSIFY_PDF_LLM` (the
  latter has no default in `app/config.py`, so the app can't even import
  `Settings()` without it) — both added for local dev.

## How to resume

1. If picking this back up: re-read "Known follow-up" above — that's the
   actual remaining work, not a fresh investigation.
2. `TRUNCATE TABLE form_fees_address;` before any re-scrape after a
   parser change (see "Operational note" above).
3. `POST /api/v1/fees/scrape`, then grep the terminal/logs for
   `possible_dropped_fee_amount` to find forms needing a closer look.
