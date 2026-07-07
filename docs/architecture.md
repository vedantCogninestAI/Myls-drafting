# Architecture

## Framework: LangGraph

All solution code is built on **LangGraph** as the orchestration layer.

Every new service, agent, workflow, or pipeline added to this project must be designed with LangGraph in mind:

- Multi-step document processing flows are modelled as LangGraph graphs (nodes + edges).
- State is passed explicitly between nodes via a typed state schema.
- Conditional branching (e.g. different draft types per process) is handled via LangGraph conditional edges.
- LLM calls are wrapped as LangGraph nodes, not called inline in service functions.

When in doubt about how to structure a new piece of logic, reach for a LangGraph graph first.

**Exception:** admin/data utilities that aren't tied to a drafting
session (no `thread_id`, nothing in `DraftingState`) skip the graph
entirely and go straight `app/services/yourfeature/` → API endpoint. The
USCIS fee-scraping feature (`app/services/fee/`, `docs/scraping.md`) is
the first example — it's a standalone on-demand scrape-and-store
operation, not a step in any drafting session's flow, so forcing it
through `DraftingState` would add state fields with no session meaning.
Use judgment: if the work reads/writes a session's state or needs
pause/resume (HITL), it belongs in the graph; if it's infra/utility work
triggered independently of any session, a thin service → endpoint is
fine.

---

## Graph Structure

There is **one flat graph** with nodes grouped by phase. As the project grows, phases will be extracted into sub-graphs for modularity, but the state and thread_id model stays the same.

```
Current (flat graph):
    [ocr_documents] → END

Planned (as nodes are added):
    [ocr_documents] → [generate_cover_letter] → [cover_letter_review] → [generate_draft] → [draft_review] → END
```

---

## Application Flow

```
User selects process
        ↓
POST /session/start
→ new graph thread created, process_type set in state
→ thread_id returned to UI

User uploads documents
        ↓
POST /api/v1/ingest/{thread_id}
→ ocr_documents node runs OCR + classification in parallel per document
→ results split into exhibit_texts + filed_doc_texts by type and written to state

POST /api/v1/cover-letter/{thread_id}/generate
→ cover letter node reads extracted texts from state
→ generates cover letter, hits interrupt(), graph pauses
→ cover letter returned to UI

User reviews and edits cover letter
        ↓
POST /api/v1/cover-letter/{thread_id}/approve
→ human feedback + approval written to state
→ graph resumes, advances to draft node

POST /api/v1/draft/{thread_id}/generate
→ draft node reads exhibits + filed docs + cover letter from state
→ generates draft, hits interrupt(), graph pauses
→ draft returned to UI

User reviews and edits draft
        ↓
POST /api/v1/draft/{thread_id}/approve
→ human feedback + approval written to state
→ graph completes
```

---

## State

Each session has **one state object**, identified by a `thread_id`. LangGraph automatically loads it before each node runs and saves it after. APIs never pass state manually — they only pass the `thread_id`.

```python
class DraftingState(TypedDict, total=False):
    # set at session start
    process_type: str

    # ephemeral — passed in at ingest time, cleared after ocr_documents node runs
    raw_files: dict[str, bytes]

    # populated by ocr_documents node
    exhibit_texts: dict[str, str]        # filename → extracted text
    filed_doc_texts: dict[str, str]      # filename → extracted text
    ingestion_errors: dict[str, str]     # filename → error message

    # populated by cover letter node
    cover_letter: str
    cover_letter_feedback: str
    cover_letter_approved: bool

    # populated by draft node
    draft: str
    draft_feedback: str
    draft_approved: bool
```

---

## APIs as Thin Triggers

FastAPI endpoints do not contain business logic. Each endpoint does exactly two things:

1. Accepts input from the UI (files, human feedback, approvals).
2. Starts or resumes the graph thread for that session.

The graph decides what runs next. The endpoint just provides the entry point.

---

## Key Rules

| Rule | Reason |
|---|---|
| Compile the graph once at app startup, reuse across all requests | Compiling per request kills performance |
| Use `AsyncPostgresSaver` as the checkpointer in production | State must survive server restarts; sessions can span hours |
| Use `MemorySaver` in development/tests only | Fast to set up, not persistent |
| All HITL is implemented via LangGraph `interrupt()` | Keeps pause/resume logic inside the graph, not scattered across endpoints |
| Design all generation nodes to support streaming | Cover letter and draft generation should stream tokens to the UI |
