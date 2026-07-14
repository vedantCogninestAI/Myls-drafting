# Architecture

## Target End-to-End Flow

The intended full pipeline, packet upload to finalized draft. New work should
map to one of these boxes, and this doc should be updated as boxes get built.

```
User gives the packet (all forms and documents)
        │
        ├────────────────────────────────► Store the files in S3
        ↓
Generate OCR of all documents (classify doc/proof)
        │
        ├────────────────────────────────► Store the extracted OCR/classification in DB
        ↓
Extract the JSON (all fields) of forms
        │
        └────────────────────────────────► (feeds into the same DB store above)

Get the template from DB
  + JSON & document OCR
  + fees & address data (scraped)
        │
        ↓
Using the AI agent, generate the draft of the document
        ↑                                        │
        │                                        ↓
        └──────── loop back if not approved ── Show the generated draft
                                                for Review (HITL)
                                                (else: finalize it)
```

**Status against each box:**

| Step | Status |
|---|---|
| Store files in S3 | ❌ Not built — no bucket configured yet |
| Generate OCR of all documents (classify doc/proof) | ✅ `POST /api/v1/ingest/{case_id}` → `app/services/ingestion/pipeline.py`. See `docs/ingestion.md` |
| Store extracted OCR/classification in DB | ✅ `ingestion_files` table, one row per file. See `docs/database.md` |
| Extract the JSON (all fields) of forms | ✅ `extract_form_fields()` (`app/services/ingestion/pipeline.py`), runs only on `filed_doc` files, writes to `form_fields` |
| Fees & address data (scraped) | ✅ `form_fees`, `form_address` tables. See `docs/scraping.md` |
| Templates in DB | ✅ `templates` table, upload + fetch at `/api/v1/template-generation/{process_type}`. See `docs/templates.md` |
| AI agent generates the draft | ✅ Two agents, two graph nodes: `resolve_filing_data_node` (fetches fee/address, `app/graph/nodes/filing_data.py`) runs first, then `generate_draft_node` (`app/graph/nodes/draft.py`) writes the document using whatever it resolved. Fees/address now wired in — see `docs/draft.md`'s "Two-Agent Split" |
| Show for Review (HITL) / finalize | ✅ `draft_review_node` + `interrupt()`, multi-round revision loop via `POST /api/v1/draft/{case_id}/approve`. See `docs/draft.md` |

---

## When to Use the Graph

**Default: no graph.** A feature goes `endpoint → service → repository → database`.
The endpoint calls the service for the work and the repository to persist it,
both directly in its own function body.

**Use LangGraph only when a feature needs `interrupt()`-based pause/resume
across separate HTTP requests.** Today that is exactly one feature: the draft
generation agent (`app/graph/nodes/draft.py`, `docs/draft.md`), where a human
may reject and request revisions several times before approving.

Everything else — ingestion, templates, fees, addresses — is plain. If the work
completes within a single request/response, it does not need `DraftingState` or
a `thread_id`, and the DB (`cases.id` as the link) is enough on its own.

---

## Graph Structure

One flat graph, three nodes.

```
    START → [resolve_filing_data] → [generate_draft] → [draft_review] ─┬─ (approved) ──────────→ END
                    ↑                                                  ├─ (rejected, under cap) ─┘ (loops back)
                    └──────────────────────────────────────────────────┘
                                                                       └─ (rejected, cap reached) → END
```

The loop-back edge is a conditional edge (`route_after_review`), not a plain
edge — see `docs/draft.md`. It routes back to `resolve_filing_data`, not
directly to `generate_draft`, so filing-fee/address data is re-resolved on
every revision round, not just the first pass — see `docs/draft.md`'s
"Two-Agent Split" for why that's deliberate rather than wasteful.

The cover letter is not a separate node; it is part of the same draft document
`generate_draft` produces. `resolve_filing_data` produces no user-visible
output of its own — it only populates state that `generate_draft` reads.

---

## Application Flow

**Compile vs. invoke:** the graph is compiled once at app startup
(`build_graph(checkpointer=...)` in `app/main.py`, stored on `app.state.graph`).
Compiling only builds the node/edge structure — it runs no node logic. The graph
is invoked (`await graph.ainvoke(...)`) per API call that needs it: each
invocation loads that `thread_id`'s state from Postgres, merges in new input,
runs whatever node comes next, and saves the updated state back.

`thread_id` is never API-facing. Endpoints take `case_id` and derive
`thread_id = str(case_id)` at the `graph.ainvoke()` call site.

```
User selects process
        ↓
POST /api/v1/session/start
→ creates a `cases` row (process_type) — plain DB write, no graph
→ case.id returned to the UI as `case_id`

User uploads documents
        ↓
POST /api/v1/ingest/{case_id}
→ plain endpoint: calls run_ingestion_pipeline() directly — OCR + classification
  run in parallel per document (classification only if CLASSIFY_PDF_LLM=true)
→ writes one `ingestion_files` row per document via IngestionRepository
→ response built directly from pipeline results

POST /api/v1/draft/{case_id}/generate
→ always invoked as a fresh first pass: seeds state with `{"case_id": ...,
  "draft": None, "draft_feedback": None, "draft_approved": False,
  "draft_revision_count": 0}`, overwriting whatever was checkpointed from any
  prior round on this thread — only `/approve` is meant to carry `draft`/
  `draft_feedback` forward, via `Command(resume=...)` against a paused
  `interrupt()`
→ resolve_filing_data_node runs first: resolves `process_type` from `cases`,
  fetches templates + form fields, and — if a template shows a fee and/or
  address slot — fetches the real value via get_filing_fee/get_filing_address
  tool calls (see docs/draft.md). Writes filing_fee_data/filing_address_data
  onto DraftingState (None for whichever slot wasn't found).
→ generate_draft_node runs next: fetches exhibits + form fields + template
  from the DB again (its own DB round-trip — the two nodes don't share a
  session), reads filing_fee_data/filing_address_data from state, generates
  the full draft (cover letter included) as plain text, hits interrupt(),
  graph pauses
→ draft returned to the UI

User reviews the draft
        ↓
POST /api/v1/draft/{case_id}/approve   (repeatable — a loop, not a single call)
→ {"approved": false, "feedback": "..."} → resumes, routes back to
  resolve_filing_data_node (re-resolving filing data unconditionally, not
  gated by what the feedback says — see docs/draft.md), which then hands off
  to generate_draft_node with feedback + previous draft, so it revises rather
  than starting over; pauses again with a new draft
→ {"approved": true} → resumes, graph reaches END
→ if rejected MAX_DRAFT_REVISIONS times without approval, stops looping anyway
```

---

## State

One state object per session, keyed internally by `thread_id` (always
`str(case_id)`). LangGraph loads it before each node runs and saves it after.

It is deliberately minimal: identity, plus the draft generation's own output. It
does **not** hold ingestion data — exhibits and form fields live in
`ingestion_files`/`form_fields` and are fetched from there by the draft agent
rather than threaded through state. Unresolved fields are marked inline in the
draft text (`[GAP: ...]`), not tracked as structured state.

```python
class DraftingState(TypedDict, total=False):
    process_type: str                    # see note below
    case_id: int                         # cases.id — thread_id is str(case_id)

    # populated by generate_draft node — the draft IS the whole document,
    # cover letter included
    draft: str
    draft_revision_count: int            # how many times generate_draft has run

    # populated by resolve_filing_data node, ahead of every generate_draft
    # run (fresh generation and every revision) — None means no template
    # showed that slot, or the lookup found nothing
    filing_fee_data: str | None
    filing_address_data: str | None

    # populated by draft_review node, after interrupt() resumes
    draft_feedback: str
    draft_approved: bool
```

> **`process_type` is currently vestigial.** Nothing `/generate` seeds, and
> nothing `generate_draft_node` returns (only `draft` and
> `draft_revision_count`), ever writes `process_type` into state. The
> node reads it as a fallback (`case.process_type if case else
> state.get("process_type")`), but that fallback can never be populated. Either
> wire it or drop the field.

---

## APIs as Thin Orchestrators

Endpoints never contain DB query code or LLM-calling code. There are two shapes:

**Plain (the default, every feature except one):** the endpoint calls `services/`
for the work and `repositories/` to persist/fetch, directly. No node, no
`thread_id`, no `graph.ainvoke()`. Example: `POST /api/v1/ingest/{case_id}` calls
`run_ingestion_pipeline()` then `IngestionRepository.save_files()`.

**Graph-based (the draft agent only):** the endpoint accepts input (human
feedback, approvals) and calls `graph.ainvoke(input, config={"configurable":
{"thread_id": ...}})`. It calls no service or repository itself — the node does
that. `app/api/v1/endpoints/draft.py` is the one live example: `POST /generate`
invokes fresh, `POST /approve` resumes via `Command(resume=...)`.

Either way the endpoint never executes a DB query or an LLM call directly.

---

## Code Layers

- **`models/`** — table definitions (columns/types), one file per domain. Not a
  DB action itself; it is the source of truth Alembic's `--autogenerate` reads.
  Editing a model doesn't touch the real DB — running the resulting migration does.
- **`repositories/`** — the only layer that executes DB reads/writes
  (`select`/`insert`/`update` via SQLAlchemy). Called by whichever layer needs it:
  an endpoint directly, or a graph node. No business logic, only queries.
- **`services/`** — the functions that do a feature's actual work (LLM calls, OCR,
  parsing, scraping). No DB access, no session state, no knowledge of being called
  from an API. `services/` never imports `repositories/` — data comes in as
  arguments and goes out as return values.
- **`graph/nodes/`** — brings `services/` and `repositories/` together under one
  flow and reads/writes `DraftingState`. Only exists for features needing
  pause/resume; `app/graph/nodes/draft.py` is the one live example.
- **`api/endpoints/`** — orchestrates; never executes DB/LLM work itself. Two
  shapes, per the section above.
- **`api/router.py`** — aggregates each endpoint file's router under a URL prefix
  and tag. No logic.

Two worked examples:

```
Ingestion (plain):
  app/services/ingestion/pipeline.py   (OCR, classify, extract fields — no DB)
  app/repositories/ingestion.py        (save_files(), save_form_fields() — no logic)
        ↑ both called directly from ↑
  app/api/v1/endpoints/ingestion.py    (orchestrates both, shapes the response)

Draft agent (graph-based — the one HITL feature, now two cooperating agents):
  app/services/draft/filing_data.py    (filing-data agent's tool-calling loop — no DB)
  app/services/draft/pipeline.py       (drafting agent's tool-calling loop — no DB)
  app/repositories/ingestion.py, template.py, fee.py, address.py  (called from inside the nodes)
        ↑ all called from ↑
  app/graph/nodes/filing_data.py       (resolve_filing_data_node)
  app/graph/nodes/draft.py             (generate_draft_node, draft_review_node)
        ↑ invoked/resumed by ↑
  app/api/v1/endpoints/draft.py        (graph.ainvoke() / Command(resume=...))
```

### Deciding whether to create a new file

The test is not "is this a new table/service/function" — it's **is this a new
domain, or one more thing inside a domain that already exists?**

- New domain (e.g. draft generation vs. ingestion) → new file(s), even if small.
- New table/function/capability *within* an existing domain → same file. Example:
  `form_fields` is a new table, but it lives in `app/models/ingestion.py` beside
  `IngestionFile`, and its repository method lives in the existing
  `IngestionRepository` — it's still "ingestion," just one more step of it.

When unsure, default to *not* creating a new file.

---

## Key Rules

| Rule | Reason |
|---|---|
| Compile the graph once at app startup, reuse across all requests | Compiling per request kills performance |
| Use `AsyncPostgresSaver` as the checkpointer | State must survive server restarts; sessions can span hours |
| All HITL is implemented via LangGraph `interrupt()` | Keeps pause/resume logic inside the graph, not scattered across endpoints |
| `resolve_filing_data`, `generate_draft`, and `draft_review` stay separate nodes | LangGraph re-runs a node from the top on resume; merging them would re-run the expensive LLM call(s) on every `/approve` |
| `resolve_filing_data` runs unconditionally ahead of every `generate_draft` pass, not just the first | A revision's feedback content must never gate whether filing data gets (re-)resolved — see `docs/draft.md`'s "Two-Agent Split" for the bug this prevents |
