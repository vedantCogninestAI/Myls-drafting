# API Mapping

Each feature has its own file in `app/api/v1/endpoints/`. The full URL is built across three layers:

```
POST "/{thread_id}"   ← defined in endpoints/ingestion.py
+ /ingest             ← prefix set in api/v1/router.py
+ /api/v1             ← prefix set in main.py
= POST /api/v1/ingest/{thread_id}
```

## Router, Prefix and Tags

- **file name** → used in the import, this is what pulls the code in
- **`ingestion.router`** → the object that holds all routes and their functions from that file. This is what decides the code that runs
- **`prefix`** → sets the URL path
- **`tags`** → label in `/docs` UI only, cosmetic, no functional impact

## Purpose of Each Folder

| Folder | Responsibility |
|---|---|
| `app/services/` | Pure business logic — OCR, LLM calls, text processing. No LangGraph, no FastAPI. Testable in isolation. |
| `app/graph/` | LangGraph orchestration — nodes read from state, call a service function, write back to state. Owns the flow: sequence, branching, HITL. |
| `app/api/` | Thin HTTP entry points — accepts input, passes it to the graph with a `thread_id`, returns the result. No business logic here. |

## Adding a New Feature

Every new feature follows this exact pattern in order:

1. Write the logic in `app/services/yourfeature/`
2. Wrap it as a node in `app/graph/nodes/yourfeature.py`
3. Add the node to the graph in `app/graph/main_graph.py`
4. Add an endpoint in `app/api/v1/endpoints/yourfeature.py` that triggers the graph

Then register it in `app/api/v1/router.py`:

```python
from app.api.v1.endpoints import yourfeature

router.include_router(yourfeature.router, prefix="/yourfeature", tags=["yourfeature"])
```

## Current Endpoints

| File | Prefix | URL | Purpose |
|---|---|---|---|
| `health.py` | `/health` | `GET /api/v1/health/` | Server health check |
| `session.py` | `/session` | `POST /api/v1/session/start` | Create a new session, returns `thread_id` |
| `ingestion.py` | `/ingest` | `POST /api/v1/ingest/{thread_id}` | Upload documents into a session, runs OCR |
| `fee.py` | `/fees` | `POST /api/v1/fees/scrape` | Trigger a background scrape of USCIS form fees into `form_fees_address` (see `docs/scraping.md`) |
| `fee.py` | `/fees` | `GET /api/v1/fees` | List scraped fee rows |

**Note:** `fee.py` is a deliberate exception to "endpoint → graph node → graph
→ database". It's an on-demand admin/data utility unrelated to a drafting
session (no `thread_id`), so it goes straight from `app/services/fee/` to
the API endpoint, bypassing LangGraph entirely. See `docs/architecture.md`
for when this exception applies.
