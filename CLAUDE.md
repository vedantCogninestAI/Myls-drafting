# Backend — Claude Guide

## Project Goal

AI-based drafting solution for a law firm that minimises the manual effort required to produce legal drafts (similar to vislaw.ai drafting).

Given a client's case, the system:
1. Ingests **client exhibits** — supporting documents and proof files.
2. Ingests **client-filed documents** — forms and filings specific to the firm's legal process.
3. Generates a **client-specific cover letter** — tailored to the client's details and the process being followed.
4. Generates the **draft** — a process-aware, client-specific legal draft assembled from the above inputs.

The firm receives a near-complete draft to review and finalise, rather than write from scratch.

## Stack
- **Framework**: FastAPI (Python)
- **Orchestration**: LangGraph
- **LLM**: AWS Bedrock via boto3
- **DB Migrations**: Alembic
- **Config**: pydantic-settings, all env vars in `.env`

## Working Dir

```
backend/
├── app/                  # main application
│   ├── api/              # route definitions (versioned)
│   ├── core/             # logging, security setup
│   ├── graph/            # LangGraph graphs, nodes, and state
│   │   ├── nodes/        # one file per feature node
│   │   ├── state.py      # DraftingState — single state for all sessions
│   │   └── main_graph.py # builds and compiles the graph (singleton)
│   ├── models/           # database table definitions (SQLAlchemy)
│   ├── schemas/          # request / response shapes (Pydantic)
│   ├── services/         # business logic & LLM/AI layer
│   ├── repositories/     # database query layer
│   ├── middleware/       # request-level middleware
│   ├── prompts.py        # all LLM prompts
│   ├── config.py         # settings & env vars
│   └── main.py           # app entry point — compiles graph at startup
├── alembic/              # database migrations
├── docs/                 # markdown documentation
├── tests/                # unit & integration tests
├── scripts/              # shell utilities
├── .env.example
├── pyproject.toml
├── Dockerfile
└── docker-compose.yml
```

## Key Rules
- All LLM prompts go in `app/prompts.py` — never inline in service files
- All LLM calls must use `retry_llm_call` from `app/services/llm/client.py` — never call LLM functions directly
- All env vars go in `app/config.py` via pydantic-settings — never read `os.environ` directly
- Call chain: `endpoint → graph node → service → repository → database`
- New features follow this order: `services/` → `graph/nodes/` → `graph/main_graph.py` → `api/` — **except** admin/data utilities with no drafting-session `thread_id` (e.g. fee scraping), which go `services/` → `api/` directly. See `docs/architecture.md`.
- New APIs: one file per feature in `app/api/v1/endpoints/`, registered in `app/api/v1/router.py`

## Docs
For specifics, refer to the `docs/` folder:

| Topic | File |
|---|---|
| API routing, prefix, tags, how to add a new API | `docs/api.md` |
| Ingestion pipeline flow, concurrency, env vars | `docs/ingestion.md` |
| Architecture decisions | `docs/architecture.md` |
| Local setup & running the app | `docs/setup.md` |
| Deployment | `docs/deployment.md` |
| USCIS fee scraping — status, blockers, how to resume | `docs/scraping.md` |
| USCIS filing-address scraping — status, known gaps, how to resume | `docs/address-scraping.md` |
