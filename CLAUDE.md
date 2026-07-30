# Backend — Claude Guide

## Project Goal

AI-based drafting solution for a law firm that minimises the manual effort required to produce legal drafts (similar to vislaw.ai drafting).

Given a client's case, the system:
1. Ingests **client exhibits** — supporting documents and proof files.
2. Ingests **client-filed documents** — forms and filings specific to the firm's legal process.
3. Generates the **draft** — a process-aware, client-specific legal draft (its cover letter is part of this same document, not a separate deliverable) assembled from the above inputs.

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
- Call chain (default, used by every feature except one): `endpoint → service → repository → database` — the endpoint calls both directly, no graph involved. `endpoint → graph node → service → repository → database` is reserved for features that need session state or pause/resume (HITL) — currently only the draft generation agent (`docs/draft.md`), since a human may reject and request revisions multiple times before approving. See `docs/architecture.md`.
- New features follow this order: `services/` → `repositories/` → `api/` directly, unless the feature needs session state or HITL pause/resume, in which case it's `services/` → `graph/nodes/` → `graph/main_graph.py` → `api/`. See `docs/architecture.md`.
- New APIs: one file per feature in `app/api/v1/endpoints/`, registered in `app/api/v1/router.py`

## Working Style
- Use dedicated tools (Read, Glob, Grep, Edit, etc.) instead of Bash for file/code operations — reserve Bash for things that genuinely need a shell (git, installs, running scripts)
- Explain things in bullet points, not long paragraphs
- Keep explanations short and to the point
- Use diagrams (flow diagrams, ASCII, mermaid) wherever they explain something better than prose

## Docs
For specifics, refer to the `docs/` folder:

| Topic | File |
|---|---|
| API routing, prefix, tags, how to add a new API | `docs/api.md` |
| Ingestion pipeline flow, concurrency, env vars | `docs/ingestion.md` |
| Template generation (sample-`.docx` upload, structure extraction, storage, known gaps) | `docs/templates.md` |
| Draft generation agent — the one LangGraph feature, tool-calling loop, HITL revision cycle | `docs/draft.md` |
| Architecture decisions | `docs/architecture.md` |
| Database tables, schema, relationships, migration history | `docs/database.md` |
| Local setup & running the app | `docs/setup.md` |
| Deployment | `docs/deployment.md` |
| USCIS fee + filing-address scraping — status, blockers, known gaps, how to resume | `docs/scraping.md` |
