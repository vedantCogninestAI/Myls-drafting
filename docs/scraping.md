# USCIS Scraping

Both scraping features (fees, filing addresses) live in this one doc —
they hit the same USCIS site and share the on-demand-refresh pattern, but
are otherwise separate pipelines (different tables, different fetch
libraries, different parsers).

## Fee Scraping

### Goal

Scrape fee data for every form listed in USCIS's fee calculator dropdown
(https://www.uscis.gov/feecalculator?topic_id=97256) and store it in our own
DB (`form_fees` table), triggered on-demand via an API endpoint
(not a cron job — the firm triggers a re-scrape whenever they want fresh
data).

### Current status: working, Firecrawl-based, manually verified against live USCIS data

Scraping, parsing, batching/rate limiting, and DB writes have all been
exercised end-to-end and spot-checked against the live USCIS site. There is
one known, deliberately unfixed edge case — see "Known follow-up" below.

### How it works

- `app/services/fee/scraper.py`'s `new_client()` returns an
  `AsyncFirecrawlApp` (from `firecrawl-py`, needs `FIRECRAWL_API_KEY` set
  in `.env`). Fetch goes through Firecrawl rather than a plain HTTP client
  because USCIS's bot protection (Akamai) TLS-fingerprints requests —
  `curl` passes, `httpx`/`requests` get `403`. `_fetch()` calls
  `client.scrape_url(url, formats=["html"])` and reads `result.html` — this
  is **not** a context manager, don't wrap it in `async with` (it doesn't
  implement `__aenter__`/`__aexit__`).
- `scrape_all_fees()` processes the ~132 forms in batches of
  `SCRAPE_CONCURRENCY` (default 5), sleeping
  `FEE_SCRAPE_BATCH_DELAY` seconds (default 5) between batches — this
  matters because Firecrawl's free tier rate-limits. Each batch's fee
  items are pushed to the DB immediately via an `on_batch` callback
  (supplied by `fee_service.run_fee_scrape`) rather than waiting for all
  132 forms to finish and doing one giant insert — `scraper.py` itself
  stays DB-free (no repository import), the callback is how persistence
  gets wired in from `fee_service.py`.
- `run_fee_scrape()` clears `form_fees` before repopulating it,
  so a fresh scrape does not leave stale rows from an older parser
  version sitting next to corrected rows.
- `FeeRepository.bulk_upsert` dedupes incoming rows by
  `(form_number, filing_category)` before the insert, because Postgres's
  `ON CONFLICT DO UPDATE` cannot affect the same row twice in one
  statement (`CardinalityViolation`) — and USCIS's own data does contain
  same-key duplicates in some batches (e.g. the same form listed under
  multiple `topic_id`s in the dropdown).
- Structured logs (`structlog`, JSON) are emitted at every stage —
  `scrape_all_fees_started` (includes the actual `concurrency`,
  `batch_delay`, and `timeout` in effect, so a slow/rate-limited run can be
  traced back to what was configured), `firecrawl_fetch`,
  `parse_form_list(_done)`, `parse_fee_page`, `fee_batch_upserted`,
  `scrape_all_fees_done`, `run_fee_scrape_done` — useful for watching a live
  scrape or debugging a specific form. `scrape_all_addresses()` logs the
  same `_started` shape (`scrape_all_addresses_started`).

### Parsing correctness — what's handled

USCIS's fee tables aren't uniform: many rows pack **multiple sub-categories
with different fees into one `<tr>`** (e.g. I-129's "H-1B petition" /
"Small Employer or Nonprofit" pair, or the "Additional Fees: Asylum
Program Fee / Nonprofit / Small Employer" block — 2-3 distinct dollar
amounts in one row).

- `_cell_items()` in `_parse_fee_page` detects `<li>`-based multi-item
  cells (a nested list inside a `<td>` — the reliable "these are
  enumerated parallel options" signal) and splits that `<tr>` into one
  fee row per `<li>`, pairing each category sub-item with its own fee
  amount positionally. Columns with only one value (e.g. an "N/A" online
  fee that applies to all sub-items) broadcast that single value across
  the split rows instead of forcing a mismatched split.
- Splitting is driven by multi-valued **fee** cells, not category lists
  alone. If a filing-category cell has an explanatory `<ol>`/`<ul>` but
  the fee cells contain a single fee (for example H-1B/L fraud-fee
  conditions or I-131 IMMVI descriptions), the parser keeps the row as
  one form row instead of creating duplicate rows.
- Some USCIS fee cells use numbered paragraphs instead of real lists
  (for example `1. $1,040 ...` / `2. $545 ...` on H-2A online fees).
  The parser treats those as parallel fee items and strips the display
  number before parsing money, so the fee is `1040.0`, not `1.0`.
- `_parse_fee_page` reads column names from `<th>` headers when present,
  and falls back to each cell's `data-label` attribute when USCIS serves
  responsive/mobile-style rows without visible table headers. This covers
  tables like I-129's E-1/E-2/E-2C/TN, E-3, H-3, O, P, Q, R, and
  "Additional Fees" rows.
- Repeated sub-case labels (e.g. "If you are filing as a Small Employer
  or Nonprofit.") that appear across multiple different `<tr>`s with
  **different fees per row** get the row's primary label prefixed on
  (`"{primary} — {sub_item}"`) so they don't collide on the
  `(form_number, filing_category)` upsert key and silently overwrite each
  other.
- `_row_to_fee_item`'s `filing_category` fallback does not grab an
  arbitrary column value when a form's table has no "Filing
  Category"/"Category" column at all (e.g. G-639, whose table is just two
  fee columns). It stores a blank category instead of inventing
  `"General Filing"`, since that text is not present on USCIS's page.
- `GET /api/v1/fees` returns `paper_fee_text`, `online_fee_text`, and
  `fee_details` in addition to numeric `paper_fee`/`online_fee`, since some
  USCIS values are not a single number (e.g. `$1,500 or $750`).

**Deliberately not auto-fixed:** cells where multiple dollar amounts sit
in the same `<td>` **without** `<li>` markup (e.g. I-102: a bolded
"Additional Form I-94 Fee: $24" sits as plain/paragraph text alongside
the main "$560" fee, no list). `<p>` count is not a reliable "these are N
parallel options" signal, so splitting on it would create spurious extra
rows with wrong duplicated fees for merely-explanatory paragraphs.
Instead, `_parse_fee_page` logs a `possible_dropped_fee_amount` warning
(compares raw `$` count in any "...Fee" column's cell text vs. how many
fee items were actually captured from it) so these cases surface via logs
during a real scrape, instead of being silently guessed at or lost.

### Known follow-up (not yet done)

- **Grep scrape logs for `possible_dropped_fee_amount`** after a full run
  and manually resolve whatever forms it flags (I-102 is a confirmed
  instance; there are likely a handful of others with the same
  paragraph-based multi-fee pattern). Decide case-by-case or look for a
  common secondary pattern once a few are seen — don't guess blind.
- **Double `fetch_form_list()` call per `POST /scrape`**: the endpoint
  (`app/api/v1/endpoints/fee.py`) calls it once directly just to report
  `total_forms` in the response, then `scrape_all_fees()` calls it again
  internally — wastes one Firecrawl call per request.
- `FEE_SCRAPE_TIMEOUT` (`app/config.py`) is not used by this scraper — the
  Firecrawl call has no explicit timeout wired to it. (It is used by the
  address scraper below, via its `requests` calls.)
- Firecrawl cost: 132 forms scraped per `POST /fees/scrape` call. Worth
  checking usage against the free-tier/plan limits before this gets
  triggered often or automated — this is why batching + a delay between
  batches (`SCRAPE_CONCURRENCY` / `FEE_SCRAPE_BATCH_DELAY`) exists.

### Operational note: re-scraping after a parser change

Because the upsert key is `(form_number, filing_category)` and a parser
fix can change what `filing_category` text gets generated for the same
underlying page content (e.g. one merged row splitting into several rows
with different label text), rows keyed on the old parser's text would
not get overwritten by a new scrape under the new key. `run_fee_scrape()`
clears the table first, so a normal refresh does not need a manual
`TRUNCATE TABLE form_fees;` beforehand.

### What's built

| Piece | File |
|---|---|
| DB model | `app/models/fee.py` (`FormFee`, table `form_fees`) |
| Repository | `app/repositories/fee.py` (`FeeRepository.bulk_upsert`, `.list_all`, `.get_by_form_number`) — dedupes by `(form_number, filing_category)` before insert. `.get_by_form_number()` fuzzy-matches (`app/repositories/fuzzy_match.py`) rather than exact-matching `form_number` — added for the draft agent's filing-data lookup, see `docs/draft.md` |
| Schemas | `app/schemas/fee.py` (`FormFeeItem`, `ScrapeFeesResponse`) |
| Orchestration | `app/services/fee/fee_service.py` (`run_fee_scrape`) — wires per-batch persistence via callback |
| API endpoints | `app/api/v1/endpoints/fee.py` (`POST /fees/scrape`, `GET /fees`) |
| HTML fetch + parse | `app/services/fee/scraper.py` — Firecrawl-based, see parsing correctness above |

#### `form_fees` schema (already decided, do not re-litigate)

Fields: `form_number`, `form_title`, `form_url`, `filing_category`,
`paper_fee`, `online_fee`, `fee_details` (raw scraped row as JSON, kept as
a safety net since fee table columns vary per form), `scraped_at`,
`created_at`. Upsert key is `(form_number, filing_category)`.

`topic_id` and `data_marker` (USCIS's internal identifiers) are
deliberately **not** persisted — they're only used transiently during
scraping to build the request URL.

**Mailing/filing addresses live in a separate table** (`form_address`) —
see "Address Scraping" below.

### How to resume (Fee Scraping)

1. If picking this back up: re-read "Known follow-up" above — that's the
   actual remaining work, not a fresh investigation.
2. `TRUNCATE TABLE form_fees;` before any re-scrape after a
   parser change (see "Operational note" above).
3. `POST /api/v1/fees/scrape`, then grep the terminal/logs for
   `possible_dropped_fee_amount` to find forms needing a closer look.

---

## Address Scraping

### Goal

For every USCIS form, scrape where to mail it — separately for **U.S. Postal
Service (USPS)** vs **FedEx/UPS/DHL** deliveries, since USCIS routes these to
different physical addresses per lockbox (couriers can't deliver to PO
Boxes). Store it in `form_address`, triggered on-demand via
`POST /api/v1/addresses/scrape` — same on-demand-refresh pattern as fee
scraping, not a cron job.

Separate feature from fee scraping — different table, different fetch
library, different parser (see "Why a separate table" and "Fetch
mechanism" below).

### Current status: pipeline works, but real completeness is unmeasured and likely incomplete

The infrastructure (form discovery, fetch, parse, batch-upsert) is solid
and verified correct for the cases it handles. But there is a **known,
confirmed, systemic gap**: addresses presented as plain paragraph text
instead of inside an HTML `<table>` are completely invisible to the
current parser, and evidence suggests this affects a meaningful — not
small — fraction of the true data. See "Known gap" below before trusting
row counts as complete coverage. Do not claim any specific completeness
percentage without building the detector described there first.

### Why a separate table (not extending `form_fees`)

- Each real filing address is actually a **pair**: one USPS (PO Box)
  address and one combined FedEx/UPS/DHL (street) address per lockbox.
  Occasionally (e.g. CNMI residents on some forms) all couriers share one
  address instead of a USPS/courier split.
- The address depends on **multiple factors that don't align with fee
  categories**: which lockbox, the filer's **state of residence**, and
  sometimes an entirely different **filing scenario/classification**
  (e.g. I-539's page has *H-4 spouse filing separately* vs *P-4
  dependent* vs *CNMI resident*, each with its own state→lockbox table).
- A fee row's `filing_category` and an address row's routing condition
  are different dimensions — forcing them into one table would mean
  either duplicating fee rows per state (fees don't vary by state) or
  duplicating address rows per fee category (wrong grouping).

So `form_address` is keyed by
`(form_number, filing_scenario, lockbox_name)`, not
`(form_number, filing_category)`.

### What's built

| Piece | File |
|---|---|
| DB model | `app/models/address.py` (`FormAddress`, table `form_address`) |
| Repository | `app/repositories/address.py` (`AddressRepository.bulk_upsert`, `.list_all`, `.get_by_form_number`) — same fuzzy-match lookup as `FeeRepository`, see `docs/draft.md` |
| Schemas | `app/schemas/address.py` (`FormFilingAddressItem`, `ScrapeAddressesResponse`) |
| Scraper (fetch + parse) | `app/services/address/scraper.py` |
| Orchestration | `app/services/address/address_service.py` (`run_address_scrape`) |
| API endpoints | `app/api/v1/endpoints/address.py` (`POST /addresses/scrape`, `GET /addresses`) |

`form_address` columns: `form_number`, `form_title`, `form_url`
(the actual address page discovered — see below, not a guessed URL),
`filing_scenario`, `applies_to` (raw condition/state-list text — no
separate `states` column yet, see "Other unresolved items" below),
`lockbox_name`, `usps_address`, `courier_address`, `address_details` (raw
row as JSONB, same safety-net pattern as `fee_details`), `scraped_at`,
`created_at`.

**Migration note**: LangGraph's checkpoint tables (`checkpoints`,
`checkpoint_writes`, `checkpoint_blobs`, `checkpoint_migrations`) aren't
in our SQLAlchemy models (managed separately by
`AsyncPostgresSaver.setup()` at startup), so `--autogenerate` always
proposes dropping them. Strip those `op.drop_table`/`op.drop_index` calls
(and their `downgrade()` counterparts) out of any newly generated
migration before applying it.

### Fetch mechanism: plain `requests`, NOT Firecrawl

Deliberate divergence from Fee Scraping's Firecrawl approach:

- Plain `requests` currently gets real `200`/`404` responses from USCIS
  with a normal browser `User-Agent` — unlike `httpx`, which USCIS blocks
  via TLS/JA3 fingerprinting (see Fee Scraping above).
- Firecrawl's `formats=["html"]` mode returns *cleaned* HTML, which is a
  real risk for this parser since it depends on exact structural class
  names (`table.dataTable`, `div.accordion__panel`, `h4.accordion__header`)
  — raw/unprocessed `requests` output is safer here.
- Cost: free, vs. Firecrawl's per-page charge — meaningful since this
  feature checks ~100+ forms per run.
- If `requests` ever starts getting blocked the way `httpx` did, try a
  different HTTP client library (different TLS fingerprint) before
  reaching for a paid service again.

`new_client()` in `app/services/address/scraper.py` returns a
`requests.Session`. Blocking `requests` calls are wrapped in
`asyncio.to_thread()` so they don't block the event loop inside the async
batching loop.

### Form discovery: the "All Forms" page, not the fee calculator's list

Uses `https://www.uscis.gov/forms/all-forms` (`fetch_all_forms_list()`),
not `app.services.fee.scraper.fetch_form_list` — broader coverage than
the fee calculator's ~132-entry dropdown (scoped to fee-calculator entries
specifically, not a general form index). No special "Forms" filter is
needed — the bare URL with no query params returns the Forms-filtered,
unpaginated, complete list. Extraction regex matches link text like
`"I-130 | Petition for Alien Relative"`, skips DS-series forms (State
Dept, not USCIS). Currently finds **102** form entries.

The curated 16-form list at `/forms/filing-guidance` ("Direct Filing
Addresses by Form Type") is explicitly non-exhaustive (the page says
"including:") — not used as a discovery source, only a manual
sanity-check reference.

### URL discovery: link-following, not pattern-guessing

USCIS has **at least 3 different URL schemes** across forms (e.g. I-751 →
`/i-751-direct-filing-addresses`, I-212 →
`/forms/all-forms/direct-filing-addresses-for-form-i-212-...`), with no
predictable pattern derivable from the form number — guessing
`/{form_number.lower()}-addresses` is not a viable strategy.

`_fetch_form_addresses` instead:
1. Fetches the form's own canonical page: `{USCIS_BASE_URL}/{slug}` (this
   part *is* a reliable pattern).
2. Searches that page for an `<a>` whose **text** matches
   `filing\s+address(es)?` (case-insensitive) — `_find_address_page_url`.
   Matching on text, not href, avoids false positives like a "Change of
   Address" link (`/addresschange`).
3. If found, follows that real link (resolved via `urljoin` for relative
   hrefs) and parses *that* page.
4. If not found, tries parsing the form's own page directly — covers forms
   that embed their address table on the main page instead of a separate
   subpage (e.g. I-612, I-589).
5. If neither yields rows, that form has no discoverable address data via
   this method (e.g. G-28, a supplementary form always filed attached to
   another form's package, so it wouldn't need its own mailing address;
   I-129F also ends up with nothing after step 4, unresolved why).

**Nuance, not a bug**: some forms' own pages link to a *different* form's
address page rather than having their own — e.g. I-601 and I-602 both
resolve to another form's address page. This is plausible, correct USCIS
guidance (these forms are often filed *concurrently* with another, so
"use that form's mailing address" is correct) — but it means some stored
rows are attributed to a `form_number` whose actual address data was
fetched from a different form's page. Not currently flagged in the data
(no "borrowed_from" marker).

### Parsing

- `_parse_address_page`: finds `table.dataTable` elements (same class
  convention as the fee scraper's tables), for each row separates the
  "address cell" (contains "P.O. Box"/"FedEx, UPS, and DHL"/"U.S. Postal
  Service") from "other cells" (joined into `applies_to`).
- `_split_address_cell`: handles the combined-label case ("U.S. Postal
  Service (USPS), FedEx, UPS, and DHL deliveries:" — one address for
  all couriers) and the split-label case (separate USPS/courier labels,
  in either order) by finding label positions via regex and slicing.
- `_find_filing_scenario`: walks up from a table to its nearest
  `div.accordion__panel` ancestor, then finds that panel's preceding
  `h4.accordion__header` sibling for the scenario name (e.g. "H-4 Spouse
  of an H-1B Nonimmigrant..."). Falls back to `"General Filing"` for
  pages with no accordion structure (e.g. I-130).

### Known gap (confirmed, NOT fixed): plain-text addresses are invisible

This is the most important open item. `_parse_address_page` only looks
inside `<table class="dataTable">`. Confirmed evidence this misses real
data, not just edge cases:

- **I-102, I-140**: link-discovery correctly finds their real address
  page URLs, but those pages have **zero** `table.dataTable` elements —
  their address content is plain paragraph/prose text.
- **I-192**: same pattern — discovered link resolves to a real page
  (shared "Certain-VAWA-T-U-Filing-Locations" page), zero tables found.
- **I-539 — the clearest sign this is a big gap, not a small one**: this
  page has **14 distinct accordion filing scenarios** (H-4 Spouse, CNMI,
  P-4 Dependents, V Nonimmigrant, Change of Status variants, etc.). A full
  scrape run found only `table_count: 3` across the *entire* page —
  meaning roughly **11 of 14 scenarios' addresses are prose, not tables**,
  and are being silently skipped. The 6 rows currently captured for I-539
  are likely a small fraction of its true filing-address data.

There is currently **no detector or warning for this**, unlike the fee
scraper's `possible_dropped_fee_amount` log. The true completeness
percentage across all 102 forms is unmeasured — don't estimate one
without measuring it first.

**Suggested fix approaches for next session** (not yet evaluated in
depth):
- Extend `_parse_address_page` to also scan for `_ADDRESS_SIGNAL_RE`
  matches (`P.O. Box` / `FedEx, UPS, and DHL` / `U.S. Postal Service`)
  in accordion-panel text *outside* any table, and parse those as
  paragraph-based address blocks.
- At minimum, add a detector: for each accordion panel (or the whole
  page if no accordion), check if `_ADDRESS_SIGNAL_RE` matches somewhere
  outside any parsed table, and log a warning if so — turns "silently
  missing" into "visibly flagged," same philosophy as the fee scraper's
  drop-detector, even before a real parser for this format is built.

### Other unresolved items, unverified — check before assuming broken or fine

- **I-290B's `applies_to = "Decision made by"` and some I-131 rows'
  `applies_to = "For"` / `"Send your form to:"` look like truncated
  fragments** — not yet re-verified. Before concluding these are real
  truncation bugs, check `SELECT LENGTH(applies_to)...` and double-click
  the cell in your DB client — grid views (e.g. pgAdmin) can display only
  the first line of a multi-line value while the full value is correctly
  stored.
- **`states` column** — not yet built. Idea: a separate JSONB array
  column populated only when `applies_to`'s condition is genuinely a list
  of US states/territories (checked against a reference list of the 50
  states + DC + outlying areas USCIS uses), left `NULL` for
  classification/conditional rows. Lower priority than the plain-text-address
  gap above.
- **Double fetch of the form list per `POST /addresses/scrape`**: same
  inefficiency pattern as the fee scraper (documented above) — the
  endpoint calls `fetch_all_forms_list()` once for the `total_forms`
  response field, then `scrape_all_addresses()` calls it again internally.

### Operational notes

- **Truncate before re-scraping after any parser change**:
  `TRUNCATE TABLE form_address;` — the upsert key includes
  `filing_scenario`/`lockbox_name` text that can change shape when parsing
  logic changes, so old rows won't get overwritten, just left stale
  alongside new ones.
- A full scrape run against all 102 forms takes roughly 2–3 minutes
  (batches of `SCRAPE_CONCURRENCY`, `FEE_SCRAPE_BATCH_DELAY` between
  batches — same settings reused from the fee scraper, since both hit the
  same USCIS site, even though this feature isn't Firecrawl-rate-limited).
- No new env vars needed — reuses `USCIS_BASE_URL`,
  `SCRAPE_CONCURRENCY`, `FEE_SCRAPE_BATCH_DELAY`,
  `FEE_SCRAPE_TIMEOUT` from existing config. `requests` is an explicit
  dependency in `requirements.txt`.

### How to resume (Address Scraping)

1. Read "Known gap" above first — that's the real unresolved work, not
   a fresh investigation.
2. Decide on an approach for the plain-text-address parsing gap (see
   suggested approaches above), implement it, and re-run against all 102
   forms to get an actual completeness number — don't guess one.
3. While in there, resolve the `applies_to` truncation question (check
   `LENGTH()` first, per "Other unresolved items" above) before treating
   it as a bug.
4. `TRUNCATE TABLE form_address;` before any re-scrape after
   parser changes.
