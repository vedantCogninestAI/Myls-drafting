# USCIS Filing-Address Scraping

## Goal

For every USCIS form, scrape where to mail it — separately for **U.S. Postal
Service (USPS)** vs **FedEx/UPS/DHL** deliveries, since USCIS routes these to
different physical addresses per lockbox (couriers can't deliver to PO
Boxes). Store it in `form_filing_addresses`, triggered on-demand via
`POST /api/v1/addresses/scrape` — same on-demand-refresh pattern as fee
scraping (`docs/scraping.md`), not a cron job.

This is a **separate feature from fee scraping**, built afterward in the
same session, sharing some lessons but not sharing fetch infrastructure.
`docs/scraping.md`'s original design note ("table is named
`form_fees_address` anticipating addresses get added later") assumed
addresses would live in the *same* table as fees — that assumption turned
out to be wrong once the real data was inspected (see "Why a separate
table" below), so this is a new table, new model, new everything.

## Current status: pipeline works, but real completeness is unmeasured and likely incomplete

The infrastructure (form discovery, fetch, parse, batch-upsert) is solid
and verified correct for the cases it handles. But there is a **known,
confirmed, systemic gap**: addresses presented as plain paragraph text
instead of inside an HTML `<table>` are completely invisible to the
current parser, and evidence suggests this affects a meaningful — not
small — fraction of the true data. See "Known gap" below before trusting
row counts as complete coverage. Do not claim any specific completeness
percentage without building the detector described there first.

## Why a separate table (not extending `form_fees_address`)

Investigated early on by manually reading `/i-130-addresses` and
`/i-539-addresses` on the live site. Findings:

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

So `form_filing_addresses` is keyed by
`(form_number, filing_scenario, lockbox_name)`, not
`(form_number, filing_category)`.

## What's built

| Piece | File |
|---|---|
| DB model | `app/models/address.py` (`FormFilingAddress`, table `form_filing_addresses`) |
| Migration | `alembic/versions/95a8b0b0ffcb_create_form_filing_addresses_table.py` |
| Repository | `app/repositories/address.py` (`AddressRepository.bulk_upsert`, `.list_all`) |
| Schemas | `app/schemas/address.py` (`FormFilingAddressItem`, `ScrapeAddressesResponse`) |
| Scraper (fetch + parse) | `app/services/address/scraper.py` |
| Orchestration | `app/services/address/address_service.py` (`run_address_scrape`) |
| API endpoints | `app/api/v1/endpoints/address.py` (`POST /addresses/scrape`, `GET /addresses`) |

`form_filing_addresses` columns: `form_number`, `form_title`, `form_url`
(the actual address page discovered — see below, not a guessed URL),
`filing_scenario`, `applies_to` (raw condition/state-list text — no
separate `states` column yet, see "Not done" below), `lockbox_name`,
`usps_address`, `courier_address`, `address_details` (raw row as JSONB,
same safety-net pattern as `fee_details`), `scraped_at`, `created_at`.

**Migration gotcha already hit and fixed once, will happen again on
future `--autogenerate` runs**: LangGraph's checkpoint tables
(`checkpoints`, `checkpoint_writes`, `checkpoint_blobs`,
`checkpoint_migrations`) aren't in our SQLAlchemy models (managed
separately by `AsyncPostgresSaver.setup()` at startup), so autogenerate
always proposes dropping them. Strip those `op.drop_table`/`op.drop_index`
calls (and their `downgrade()` counterparts) out of any newly generated
migration before applying it — same issue as documented in
`docs/scraping.md`.

## Fetch mechanism: plain `requests`, NOT Firecrawl (unlike the fee scraper)

This is a deliberate, validated divergence from `docs/scraping.md`'s
Firecrawl approach:

- Empirically tested (multiple times, including a 10-request burst with
  no blocking) that plain `requests` — unlike `httpx`, which USCIS blocks
  via TLS/JA3 fingerprinting — currently gets real `200`/`404` responses
  from USCIS with a normal browser `User-Agent`. Same machine/IP that
  `httpx` failed on before.
- Switching to Firecrawl would **not** have fixed any of the real bugs
  found this session (wrong URLs, missing table detection) — those are
  about *which* URL is requested and *our own parsing logic*, not which
  library fetches the bytes. Firecrawl's `formats=["html"]` mode also
  returns *cleaned* HTML, which is a real risk for this parser since it
  depends on exact structural class names (`table.dataTable`,
  `div.accordion__panel`, `h4.accordion__header`) — raw/unprocessed
  `requests` output is safer here, not riskier.
- Cost: free, vs. Firecrawl's per-page charge — meaningful since this
  feature checks ~100+ forms per run, more than the fee scraper's ~132
  (fee scraper's 132 include multiple filing-category duplicates of the
  same form; this feature's ~100 are the deduplicated "all forms" list).
- If `requests` ever starts getting blocked the way `httpx` did, the fix
  is the same category as before: try a different HTTP client library
  (different TLS fingerprint) before reaching for a paid service again.

`new_client()` in `app/services/address/scraper.py` returns a
`requests.Session`, not an `AsyncFirecrawlApp`. Blocking `requests` calls
are wrapped in `asyncio.to_thread()` so they don't block the event loop
inside the async batching loop.

## Form discovery: the "All Forms" page, not the fee calculator's list

Uses `https://www.uscis.gov/forms/all-forms` (`fetch_all_forms_list()`),
not `app.services.fee.scraper.fetch_form_list` — broader coverage than
the fee calculator's ~132-entry dropdown (which is scoped to
fee-calculator entries specifically, not a general form index). No
special "Forms" filter needs to be applied — visiting the bare URL with
no query params already returns the Forms-filtered, unpaginated, complete
list (confirmed: all forms are on one page, no pagination). Extraction
regex matches link text like `"I-130 | Petition for Alien Relative"`,
skips DS-series forms (State Dept, not USCIS). Currently finds **102**
form entries.

That curated 16-form list at `/forms/filing-guidance` ("Direct Filing
Addresses by Form Type") is explicitly non-exhaustive (the page says
"including:") — confirmed by testing forms not on it (I-539 works fine).
Don't use it as a discovery source; it's fine as a manual sanity-check
reference only.

## URL discovery: link-following, not pattern-guessing — this was the main bug fixed this session

**Original (broken) approach**: guess `/{form_number.lower()}-addresses`
for every form. This is wrong often enough to be unusable — confirmed via
direct testing:

| Form | Guessed URL | Real URL |
|---|---|---|
| I-751 | `/i-751-addresses` | `/i-751-direct-filing-addresses` |
| I-212 | `/i-212-addresses` | `/forms/all-forms/direct-filing-addresses-for-form-i-212-application-for-permission-to-reapply-for-admission-into-the` |
| I-129F | `/i-129f-addresses` | no separate page — info would need to live on `/i-129f` itself (in practice: not present there either, see below) |

USCIS has **at least 3 different URL schemes** across forms, with no
predictable pattern derivable from the form number.

**Current (fixed) approach**, in `_fetch_form_addresses`:
1. Fetch the form's own canonical page: `{USCIS_BASE_URL}/{slug}` (this
   part *is* a reliable pattern — confirmed via many `form_url` values
   already seen in the fee-scraping data, e.g. `/g-1566`, `/ar-11`).
2. Search that page for an `<a>` whose **text** matches
   `filing\s+address(es)?` (case-insensitive) — `_find_address_page_url`.
   Matching on text, not href, avoids false positives like a "Change of
   Address" link (`/addresschange`) which contains "address" but isn't
   what we want.
3. If found, follow that real link (resolved via `urljoin` for relative
   hrefs) and parse *that* page.
4. If not found, try parsing the form's own page directly — covers forms
   that embed their address table right on the main page instead of a
   separate subpage (e.g. I-612, I-589 both have their address table
   directly on their own page, confirmed working this way).
5. If neither yields rows, that form genuinely has no discoverable
   address data via this method (verified true for G-28 — makes sense,
   it's a supplementary form always filed attached to another form's
   package, so it wouldn't need its own mailing address; I-129F also
   still ends up with nothing after step 4, unresolved why).

Verified this approach against 6 forms before implementing (I-212,
I-129F, I-102, I-140, I-129, G-28) and confirmed no regression on
already-working forms (I-129 still resolves to `/i-129-addresses`, same
as the old guess would've produced). After implementing, a full run
against all 102 forms confirmed the fix: I-212 and I-751 now produce real
rows where they got 404s before, plus a bonus fix for **N-400** (wasn't
even a targeted case) which now yields 5 rows for a high-volume form that
previously had zero.

**Nuance, not a bug**: some forms' own pages link to a *different* form's
address page rather than having their own — e.g. I-601 and I-602 both
resolve to I-485's/I-601's address pages respectively. This is plausible,
correct USCIS guidance (these forms are often filed *concurrently* with
another, so "use that form's mailing address" is the right answer) — but
it means some stored rows are attributed to a `form_number` whose actual
address data was fetched from a different form's page. Not yet flagged
in the data anywhere (no "borrowed_from" marker) — a future session might
want to add one if this causes confusion downstream.

## Parsing (unchanged from initial build, reused fee-scraper patterns)

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

## Known gap (confirmed, NOT fixed): plain-text addresses are invisible

This is the most important open item. `_parse_address_page` only looks
inside `<table class="dataTable">`. Confirmed evidence this misses real
data, not just edge cases:

- **I-102, I-140**: link-discovery correctly finds their real address
  page URLs, but those pages have **zero** `table.dataTable` elements —
  their address content is presented as plain paragraph/prose text.
- **I-192**: same pattern — discovered link resolves to a real page
  (shared "Certain-VAWA-T-U-Filing-Locations" page), zero tables found.
- **I-539 — the clearest sign this is a big gap, not a small one**: this
  page has **14 distinct accordion filing scenarios** (confirmed via
  direct DOM inspection: "Filing Form I-539 with a Principal's Form
  I-129...", "Nonimmigrants and Their Dependents", "H-4 Spouse...",
  "CNMI", "P-4 Dependents", "Extension of Stay for T Nonimmigrants",
  "Extension of Stay for U Nonimmigrants", "V Nonimmigrant" [confirmed:
  "mail your form to the Chicago Lockbox" is plain prose, no table],
  "Change of Status to A, G or NATO", "Change of Status to G
  Classification", "Dependent Spouse/Child...", "Principal NATO
  Nonimmigrant", "Extensions of Stay for A-3, G-5, or NATO-7
  Nonimmigrant", "All Other Form I-539 Filers"). A full scrape run found
  only `table_count: 3` across the *entire* page — meaning roughly
  **11 of 14 scenarios' addresses are prose, not tables**, and are being
  silently skipped. The 6 rows currently captured for I-539 are likely a
  small fraction of its true filing-address data.

There is currently **no detector or warning for this**, unlike the fee
scraper's `possible_dropped_fee_amount` log. We genuinely don't know the
true completeness percentage across all 102 forms — don't estimate one
without measuring it first.

**Suggested fix approaches for next session** (not yet evaluated in
depth):
- Extend `_parse_address_page` to also scan for `_ADDRESS_SIGNAL_RE`
  matches (`P.O. Box` / `FedEx, UPS, and DHL` / `U.S. Postal Service`)
  in accordion-panel text *outside* any table, and parse those as
  paragraph-based address blocks (similar `<p>`+`<br>` structure to the
  fee scraper's multi-line cells, based on what we've seen).
- At minimum, add a detector: for each accordion panel (or the whole
  page if no accordion), check if `_ADDRESS_SIGNAL_RE` matches somewhere
  outside any parsed table, and log a warning if so — turns "silently
  missing" into "visibly flagged," same philosophy as the fee scraper's
  drop-detector, even before a real parser for this format is built.

## Other unresolved items, unverified — check before assuming broken or fine

- **I-290B's `applies_to = "Decision made by"` and some I-131 rows'
  `applies_to = "For"` / `"Send your form to:"` look like truncated
  fragments.** This was flagged mid-session but never actually
  re-verified. Important context: a very similar-looking issue (
  `usps_address` showing just `"USCIS"`) turned out to be a **pgAdmin
  display artifact**, not a real bug — the full multi-line value was
  correctly stored the whole time, pgAdmin's grid was just only showing
  the first line (confirmed via `SELECT LENGTH(usps_address)...` and by
  double-clicking the cell). Do the same `LENGTH()` check on these
  `applies_to` values before concluding they're actually broken — don't
  assume based on the grid view alone.
- **`states` column** — discussed at length but never built. Idea: a
  separate JSONB array column populated only when `applies_to`'s
  condition is genuinely a list of US states/territories (checked
  against a reference list of the 50 states + DC + outlying areas USCIS
  uses — a legitimate reference dataset, not a form-specific hack),
  left `NULL` for classification/conditional rows. Decided this is
  useful but lower priority than the plain-text-address gap above.
- **Double fetch of the form list per `POST /addresses/scrape` call**:
  same inefficiency pattern as the fee scraper (documented in
  `docs/scraping.md`) — the endpoint calls `fetch_all_forms_list()` once
  for the `total_forms` response field, then `scrape_all_addresses()`
  calls it again internally. Not fixed in either feature yet.

## Operational notes

- **Truncate before re-scraping after any parser change**:
  `TRUNCATE TABLE form_filing_addresses;` — same reasoning as
  `docs/scraping.md`: the upsert key includes `filing_scenario`/
  `lockbox_name` text that can change shape when parsing logic changes,
  so old rows won't get overwritten, just left stale alongside new ones.
- A full scrape run against all 102 forms takes roughly 2–3 minutes
  (batches of `FEE_SCRAPE_CONCURRENCY`, `FEE_SCRAPE_BATCH_DELAY` between
  batches — same settings reused from the fee scraper, since both
  ultimately hit the same USCIS site and should be equally polite about
  it, even though this feature isn't Firecrawl-rate-limited).
- No new env vars needed — reuses `USCIS_BASE_URL`,
  `FEE_SCRAPE_CONCURRENCY`, `FEE_SCRAPE_BATCH_DELAY`,
  `FEE_SCRAPE_TIMEOUT` from existing config. `requests` added to
  `requirements.txt` as an explicit dependency (was previously only an
  indirect dependency via `firecrawl-py`).

## How to resume

1. Read "Known gap" above first — that's the real unresolved work, not
   a fresh investigation.
2. Decide on an approach for the plain-text-address parsing gap (see
   suggested approaches above), implement it, and re-run against all 102
   forms to get an actual completeness number — don't guess one.
3. While in there, resolve the `applies_to` truncation question (check
   `LENGTH()` first, per "Other unresolved items" above) before treating
   it as a bug.
4. `TRUNCATE TABLE form_filing_addresses;` before any re-scrape after
   parser changes.
