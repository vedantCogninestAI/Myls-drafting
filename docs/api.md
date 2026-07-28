# API Mapping

Each feature has its own file in `app/api/v1/endpoints/`. The full URL is built across three layers:

```
POST "/{case_name}"   ← defined in endpoints/ingestion.py
+ /ingest             ← prefix set in api/v1/router.py
+ /api/v1             ← prefix set in main.py
= POST /api/v1/ingest/{case_name}
```

## Router, Prefix and Tags

- **file name** → used in the import, this is what pulls the code in
- **`ingestion.router`** → the object that holds all routes and their functions from that file. This is what decides the code that runs
- **`prefix`** → sets the URL path
- **`tags`** → label in `/docs` UI only, cosmetic, no functional impact

## Purpose of Each Folder

| Folder | Responsibility |
|---|---|
| `app/services/` | Pure business logic — OCR, LLM calls, text processing. No DB, no LangGraph, no FastAPI. Testable in isolation. |
| `app/repositories/` | The only layer that actually reads/writes the DB. |
| `app/graph/` | LangGraph orchestration — nodes read from state, call a service function, write back to state. Reserved for features needing session state or pause/resume (HITL). One live feature: the draft agent (`docs/draft.md`). |
| `app/api/` | Thin orchestrators — accepts input, calls `services/`+`repositories/` directly (current default) or `graph.ainvoke()` (only for HITL-needing features), returns the result. Never executes DB/LLM work itself. |

## Adding a New Feature

**Default (no pause/resume needed) — every feature except the draft agent:**

1. Write the logic in `app/services/yourfeature/`
2. Write DB access in `app/repositories/yourfeature.py`
3. Add an endpoint in `app/api/v1/endpoints/yourfeature.py` that calls both directly

**Only if the feature needs session state or HITL pause/resume** (currently only the draft agent — `docs/draft.md` — needs this):

1. Write the logic in `app/services/yourfeature/`
2. Wrap it as a node in `app/graph/nodes/yourfeature.py`
3. Add the node to the graph in `app/graph/main_graph.py`
4. Add an endpoint in `app/api/v1/endpoints/yourfeature.py` that triggers the graph

Either way, register the router in `app/api/v1/router.py`:

```python
from app.api.v1.endpoints import yourfeature

router.include_router(yourfeature.router, prefix="/yourfeature", tags=["yourfeature"])
```

## Current Endpoints

| File | Prefix | URL | Purpose |
|---|---|---|---|
| `health.py` | `/health` | `GET /api/v1/health/` | Server health check |
| `session.py` | `/session` | `POST /api/v1/session/start` | Create a new case from a `case_name` (unique), returns `case_id` |
| `session.py` | `/session` | `GET /api/v1/session/cases` | List every case as `{case_id, case_name, created_at}` — the enum a caller selects a case from |
| `ingestion.py` | `/ingest` | `POST /api/v1/ingest/{case_name}` | Upload documents for a case (identified by name), runs OCR + classification + field extraction |
| `ingestion.py` | `/ingest` | `GET /api/v1/ingest/{case_name}` | Fetch a case's OCR'd files (text, doc type, error) merged with any extracted `form_fields` |
| `fee.py` | `/fees` | `POST /api/v1/fees/scrape` | Trigger a background scrape of USCIS form fees into `form_fees` (see `docs/scraping.md`) |
| `fee.py` | `/fees` | `GET /api/v1/fees` | List scraped fee rows |
| `address.py` | `/addresses` | `POST /api/v1/addresses/scrape` | Trigger a background scrape of USCIS filing addresses into `form_address` (see `docs/scraping.md`) |
| `address.py` | `/addresses` | `GET /api/v1/addresses` | List scraped address rows |
| `template_generation.py` | `/template-generation` | `POST /api/v1/template-generation/{process_type}` | Upload up to 5 sample Word (`.docx`) documents, extract structure (headings/lists/tables/alignment) + store as reference templates for that draft type — the sole origination point for a `process_type` (see `docs/templates.md`) |
| `template_generation.py` | `/template-generation` | `GET /api/v1/template-generation/{process_type}` | List stored templates for a draft type |
| `template_generation.py` | `/template-generation` | `GET /api/v1/template-generation/process-types` | List every distinct `process_type` that has templates — the enum a caller selects a draft type from |
| `draft.py` | `/draft` | `POST /api/v1/draft/{case_name}/{process_type}/generate` | Start the draft agent for a case (by name) against a chosen draft type — first invocation of the graph for that `(case, process_type)` pair, returns the draft as a downloadable `.docx` file (see `docs/draft.md`) |
| `draft.py` | `/draft` | `POST /api/v1/draft/{case_name}/{process_type}/approve` | Submit human review — `approved: false` loops back for a revision, `approved: true` finalizes (repeatable) — returns the current draft as a downloadable `.docx` file |

**Note:** every endpoint above calls `services/`+`repositories/` directly
**except** `draft.py`, which is the one live example of
"endpoint → graph node → graph → database" — the only feature that needs
pause/resume (HITL), since an attorney may reject and request revisions
multiple times before approving. See `docs/architecture.md`'s LangGraph
exception section for the reasoning, and `docs/draft.md` for the draft
agent's full design.
