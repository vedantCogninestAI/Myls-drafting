# Draft Generation Agent

The one feature in this codebase that actually uses LangGraph (see the exception in `docs/architecture.md`) — it's the only step that needs pause/resume across separate requests (human review, possibly multiple rounds). Everything else (ingestion, templates, fees, address) is plain `endpoint → service → repository`.

Two separate LLM agents cooperate here, each its own graph node: `resolve_filing_data_node` (fetches this case's filing fee/address, if the document type needs one) and `generate_draft_node` (writes the document). See "Two-Agent Split" below for why they're split rather than one agent with more tools.

## What This Generates

`process_type` defines *what kind* of draft this agent produces (e.g. "N-400 Cover Letter") — its templates (`docs/templates.md`) are structure only, never a real client's content. A case (`docs/database.md`) carries no `process_type` of its own; it's just a client's ingested data. Both are supplied independently at generate time — `POST /draft/{case_name}/{process_type}/generate` — so the same case's data can be drafted against any `process_type`, and the same `process_type` can be applied to any case. Each run is for one specific `(case, process_type)` pair — the draft is built entirely from that client's own Form Data and exhibits, structured per that `process_type`'s templates.

## Flow

```
POST /api/v1/draft/{case_name}/{process_type}/generate
        ↓                        (always a fresh first pass — see
        ↓                         "Fresh-Start Guarantee" below)
endpoint resolves case_name → the case row (IngestionRepository.
get_case_by_name()) — 404 if no case has that name — then uses its
numeric id for everything below
        ↓
resolve_filing_data_node (LangGraph node) — runs on EVERY pass: fresh
generation AND every revision, never skipped, never gated by feedback
        ↓
reads process_type straight from state (supplied by the endpoint, not
looked up from the case); fetches templates (by process_type), case's
form_data
        ↓
if templates exist, calls resolve_filing_data() (service) — a tool-calling
agent loop with only two tools available:
        ↓
    scans the templates for a filing-fee slot and/or a filing-address slot
    (a structural judgment — does ANY template show one)
        ↓
    for each slot found, calls get_filing_fee(form_number, reason) and/or
    get_filing_address(form_number, reason) using this case's actual form
    number(s) from Form Data — its job stops at fetching; it does not pick
    which candidate row applies to this client
        ↓
    its final text answer is discarded — only the tool calls it made and
    their raw results are kept
        ↓
stores filing_fee_data / filing_address_data on graph state — None for
either one nobody found a slot for, or the lookup found nothing
        ↓
generate_draft_node (LangGraph node)
        ↓
fetches: case files (split into form_data — filed_docs only — and
available_files — exhibits only, filename+doc_type, no text); reads
filing_fee_data/filing_address_data straight from state
        ↓
calls generate_draft() (service) — the tool-calling agent loop:
        ↓
    sends templates + form_data + exhibit list + Filing Data (if any) to
    the model
        ↓
    loop (up to MAX_TOOL_ITERATIONS = 10):
        model calls get_exhibit_text(filename, reason) → fetch that one
        exhibit's OCR text, feed back, loop again
        or returns its final answer as plain text — no JSON, no tool call
        for the final answer, just the draft itself
        ↓
draft_review_node — interrupt() — graph PAUSES here
        ↓
draft converted to a .docx file (app/services/draft/word_formatter.py)
and returned to caller as a downloadable file, not JSON
        ↓
User reviews the draft
        ↓
POST /api/v1/draft/{case_name}/{process_type}/approve        (repeatable —
same case_name + process_type as the /generate call, resolved the same way)
→ {"approved": false, "feedback": "..."}
    → resumes past interrupt(), routes back to resolve_filing_data_node
      (not straight to generate_draft_node — filing data is re-resolved
      every round; see "Two-Agent Split" below for why)
    → same two-agent sequence runs again; generate_draft() this time
      includes the previous draft + feedback, so it revises instead of
      starting over
    → pauses again at draft_review_node with a new draft — call /approve
      again with more feedback, or with approved: true
→ {"approved": true}
    → resumes, graph reaches END, thread is done
→ if rejected MAX_DRAFT_REVISIONS times without approval, the loop stops
  anyway (response header X-Max-Revisions-Reached: true)
```

## Two-Agent Split: Filing Data vs. Drafting

Fee/address resolution and document drafting are two separate LLM agents, not one agent with more tools. The drafting agent does not have `get_filing_fee`/`get_filing_address` as tools — it cannot call them, and cannot forget to.

**Why they're separate.** The drafting agent's revision rule is "change only what the feedback flagged." If fee/address lookups were tools on the drafting agent, whether to call them would compete for attention with reading templates, fetching exhibits, and following that revision rule — a revision round whose feedback doesn't mention the fee gives the model no prompt to reconsider a fee slot it should still resolve. `resolve_filing_data_node` runs *every* time drafting runs — fresh generation and every revision — as an unconditional graph edge, never gated by feedback content, and has exactly one job with nothing else competing for its attention.

**Division of labor:**
- **`resolve_filing_data` (agent 1)** decides *whether* a slot exists — a structural judgment from the templates, the same "templates give structure" rule the drafting agent follows — and fetches the *raw* candidate rows for the case's form number(s). It does **not** pick which row applies to this specific client.
- **`generate_draft` (agent 2)** receives whatever agent 1 resolved, as an optional "Filing Data" section, and does the row selection — picking the candidate this case's established facts (income, military status, state, wherever Form Data/exhibits establish them) support, or gapping it if nothing does.

## Fresh-Start Guarantee (`/generate`)

`/generate` and `/approve` share the same LangGraph `thread_id` — `f"{case_id}:{process_type}"`, built from the case's resolved numeric id (not `case_name`, so the thread stays stable even though the name is what the API surface uses) and the `process_type` from the URL. This means a given `(case, process_type)` pair gets its own independent HITL thread — running a different `process_type` against the same case, or the same `process_type` against a different case, never collides with an in-progress revision loop.

Only `/approve` is meant to carry state forward — it resumes a paused `interrupt()` with the human's decision via `Command(resume=...)`. `/generate` is meant to always be a clean first pass, so `generate_draft_endpoint` (`app/api/v1/endpoints/draft.py`) explicitly resets `draft`, `draft_feedback`, `draft_approved`, and `draft_revision_count` to their initial values in the `graph.ainvoke()` input on every call:

```python
result = await graph.ainvoke(
    {
        "case_id": case_id,
        "process_type": process_type,
        "draft": None,
        "draft_feedback": None,
        "draft_approved": False,
        "draft_revision_count": 0,
    },
    config={"configurable": {"thread_id": thread_id}},
)
```

`DraftingState` is a plain `TypedDict` with no custom reducers, so each key is "last value wins" — these values overwrite whatever was checkpointed from any prior round on that `(case, process_type)` thread before any node runs.

## Internal Agent Design

Two separate tool-calling agents (see "Graph Structure" in `docs/architecture.md`), each a single LLM tool-use loop — `resolve_filing_data_node`, `generate_draft_node`, and `draft_review_node` are the three graph nodes; everything about "should I fetch this, which one, when am I done" happens inside the respective service call's loop, not as separate graph nodes.

**`get_exhibit_text(filename, reason)`** — the drafting agent's only tool. `IngestionRepository.get_file_by_filename` (`case_id` + exact filename match, no `doc_type` filter, no `ORDER BY`, no LLM inside it) — fetches one file's already-OCR'd text. `reason` is a *required* input field (not optional prose) — the model must state what it's looking for and why, purely for observability (logged as `draft_generation_tool_call`, never returned in any API response). Only `exhibit`-classified files are listed in the "Available Files" section the model sees, and the prompt instructs it to use `Form Data` for `filed_doc`s instead of calling this tool for them — but this is a prompt-level convention, not a code-enforced restriction: the underlying repository call has no `doc_type` check, so it will return a `filed_doc`'s text too if the model calls it with that file's exact filename.

**`get_filing_fee(form_number, reason)` / `get_filing_address(form_number, reason)`** — the filing-data agent's only tools (see "Fee/Address Lookup Tools" below). Same `reason`-required pattern, logged as `filing_data_resolution_tool_call`.

**No mid-generation human interrupts on either agent.** Both resolve their own uncertainty — the filing-data agent decides for itself whether a template implies a slot; the drafting agent decides for itself whether an exhibit likely has a missing value.

## Fee/Address Lookup Tools

`get_filing_fee` and `get_filing_address` take a `form_number` string (e.g. `"N-400"`) and match it against the distinct `form_number` values in `form_fees` / `form_address` using `rapidfuzz` (`app/repositories/fuzzy_match.py`, shared by both repositories) — **not** an exact string match, since the model's form number and the scraped `form_number` column aren't guaranteed to be formatted identically. `FUZZY_MATCH_THRESHOLD = 80.0` (rapidfuzz's 0–100 `WRatio` scale). Only the single best-scoring candidate is considered — if it's below the threshold, or there is no candidate at all, the tool returns no rows (the caller then gaps the value) rather than guessing between close competitors.

Each tool returns *every* candidate row on file for the matched form (e.g. every fee category, every mailing scenario) — raw, unfiltered. The filing-data agent doesn't narrow this down; it's passed straight through to the drafting agent, which picks the row this case's established facts support (or gaps it — see `DRAFT_GENERATION_PROMPT`'s Filing Data rules).

`graph/nodes/filing_data.py` logs the raw DB-fetch outcome (`filing_data_fee_fetch_done` / `_not_found`, same for address — query, matched `form_number`, score, row count); `services/draft/filing_data.py` logs the tool-call-in-context (`filing_data_resolution_tool_call` — same fields plus `iteration` and the model's stated `reason`). This mirrors the `draft_exhibit_fetch_done` / `_not_found` + `draft_generation_tool_call` split already used for `get_exhibit_text`.

This is the only path to a filing fee or filing address — the drafting agent's prompt forbids sourcing either from Form Data, an exhibit, a template, or the model's own knowledge.

**Known gap in this design:** the tools guarantee the *data* isn't invented, but nothing yet enforces that the filing-data agent's stated reasoning for calling (or not calling) a tool is faithful, or that the drafting agent's row selection matches what was actually fetched. That deeper enforcement layer (a same-session output/tool-result match check) does not exist.

**Output is plain text, not JSON.** The drafting agent's entire final answer *is* the draft, plain text, nothing else — no `{"draft": ..., "gaps": [...]}` wrapper, no code fences. `DRAFT_GENERATION_PROMPT`'s `# Output` section states this directly ("STRICTLY output the draft only. Nothing else."), enforced only by the prompt — there is no code-level check on the model's final answer before it's handed to `word_formatter.py`. This labeled plain text is an internal representation only — the endpoint converts it to a `.docx` file before it ever reaches the caller (see "Word Document Export" below). The filing-data agent's final text answer is different: it's discarded entirely, since only its tool calls matter.

**Reference templates are structure-only, never content.** Templates come from other, unrelated clients' past cases (see `docs/templates.md`) — the prompt must explicitly forbid the model from copying their specific exhibit letters, form names, counts, or facts, since the LLM otherwise tends to pattern-match a template's literal enumeration (e.g. reproducing "Exhibit A" through "Exhibit N" from an example) instead of restricting itself to *this* client's actual `Available Files` and `Form Data`. `DRAFT_GENERATION_PROMPT` states this explicitly: every exhibit/form named in the draft must correspond one-to-one to an entry actually provided for this case. The filing-data agent's use of templates is narrower — it only asks "does a fee/address slot exist," never copies a value from one.

A template's structure includes how many named parties it shows (e.g. a caption block written for one respondent). The prompt instructs the model that Form Data may describe more parties than any template shows — several respondents, each with their own filed form of the same type — and in that case to adapt the structure to name every one of them (extending the caption block, switching to plural phrasing) rather than gapping a party's identity or dropping any of them.

Templates that use a repeating bracket character (`)` or `:`) down a caption block's margin, including lines that are nothing but that character, are a tab-stop-based visual convention — the prompt instructs the model to never reproduce a line consisting solely of a bracket character, attaching it to the nearest real content line instead. This doesn't restore exact column alignment (see the tab-stop limitation noted under "Word Document Export"); it only avoids an isolated floating character. The rule also covers lines the model inserts itself between stacked items (e.g. one line per party) — see "Word Document Export" below for the code-level backstop added after prompt-only enforcement of that case proved unreliable in testing.

If a case has no exhibits, the "Available Files" section of the prompt's input renders as `(none — this case has no exhibits)` rather than an empty section (`app/services/draft/pipeline.py`'s `_build_initial_message`), and the prompt states explicitly that this is normal, complete input — never a reason to ask a question or stop.

**Alignment convention: `[CENTER]` / `[RIGHT]` / `[JUSTIFY]` inline labels.** Templates are stored with these same labels (see `docs/templates.md`) — derived directly from `python-docx`'s `paragraph.alignment.name`, not a hardcoded translation table. Left-aligned is the unmarked default. The prompt instructs the drafting agent to use the same labels in its own output wherever equivalent content should carry that alignment (e.g. a letterhead is typically `[CENTER]`, a body paragraph often `[JUSTIFY]`). These labels are what `word_formatter.py` later parses back into real Word paragraph alignment.

**Gaps: inline, not structured.** If nothing (form data, an exhibit, or Filing Data) covers a field, the drafting agent marks it `[GAP: brief description]` exactly where that information would go, rather than fabricating a value. There is no separate list of gaps anywhere — they stay visible in-context within the draft text (and therefore the exported `.docx`) itself.

## Word Document Export

`app/services/draft/word_formatter.py`'s `draft_to_docx_bytes()` turns the agent's labeled plain-text draft into real `.docx` bytes: sets 0.75" left/right margins (narrower than `python-docx`'s unadjusted 1" default, giving long caption/label lines more room before wrapping), splits the text into blank-line-separated blocks, drops any block that's just a caption-margin bracket character (`:` or `)`) sitting between two multi-field rows (`_drop_redundant_bracket_separators()` — a duplicated separator the model sometimes still inserts between stacked items like multiple parties, despite the prompt rule against it), then for each remaining block —

- if it matches markdown table syntax (pipe-delimited rows plus a `| --- | --- |`-style separator row, detected by `_parse_markdown_table()`), builds a real `python-docx` `Table` (style `"Table Grid"`) with one row/cell per parsed row/cell, instead of writing the raw markdown characters as text. This is a plain structural pattern match — no hardcoded column names or section titles — so it applies to any table-shaped block the model produces, matching the same markdown syntax `_table_to_markdown()` (`app/services/template_generation/pipeline.py`) produces when a template's own source `.docx` contains a real table.
- otherwise, strips a leading `[CENTER]`/`[RIGHT]`/`[JUSTIFY]` label if present and adds it as a normal paragraph with that alignment (default left-aligned if no label).

Returns the saved document as bytes from an in-memory buffer — no disk I/O, no DB.

**Not handled:** tab-stop-based columnar layouts (e.g. a caption block's bracket characters aligned down the right margin via a custom Word tab stop, or a judge/hearing pair laid out side by side with tabs). `paragraph.text` extraction captures the literal tab characters but not the paragraph's tab-stop position, and `add_paragraph()` here creates paragraphs with Word's default tab stops, not the original template's — so these render at different horizontal positions than the source template intended. `DRAFT_GENERATION_PROMPT` has a rule against reproducing a caption-block line that consists solely of a bracket character with no other content (so no isolated floating punctuation), and against inserting one as a separator between stacked items it writes itself (e.g. multiple parties) — but that prompt rule alone proved unreliable for the stacked-item case in testing, so `_drop_redundant_bracket_separators()` now removes a bracket-only block sitting between two multi-field rows as a code-level backstop. The deeper column-alignment problem — actually lining values up in columns, not just removing a duplicated separator — is still not fixed at the code level.

Both `/generate` and `/approve` call this after getting `draft` from the graph and return it as the HTTP response body directly — `media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"`, `Content-Disposition: attachment; filename="draft_{case_id}.docx"`, so the client downloads a real file, not JSON. Since the body is binary, response metadata travels as headers instead: `X-Case-Id` (both endpoints), plus `X-Approved` and `X-Max-Revisions-Reached` (`/approve` only). `feedback` is deliberately **not** echoed back in a header — it's free text the client already has (they sent it in the request), and HTTP headers must be ASCII/latin-1-safe, which arbitrary human-typed text isn't guaranteed to be.

## Multi-Round HITL Revision

`draft_review_node` calls `interrupt({"draft": state.get("draft")})`, which pauses the graph. On resume via `POST /approve`, `route_after_review()` (a LangGraph conditional edge) decides:
- `approved: true` → `END`
- `approved: false` and under the revision cap → back to `resolve_filing_data_node` (which re-runs, then hands off to `generate_draft_node` — see "Two-Agent Split" above for why filing data is re-resolved every round rather than only routing back to drafting)
- `approved: false` and `draft_revision_count >= MAX_DRAFT_REVISIONS` → `END` anyway (logged as `draft_revision_cap_reached`), response header `X-Max-Revisions-Reached: true`

`MAX_DRAFT_REVISIONS` (default `5`) is env-configurable via `.env`, no hardcoded cap in code.

`app/api/v1/endpoints/draft.py`'s `/approve` handler checks `graph.aget_state(config).next` before resuming — an empty tuple means nothing is paused (case never generated, or the thread already reached `END`), and the endpoint returns `409` instead of letting `Command(resume=...)` fail unhandled. `DraftApproveRequest` (`app/schemas/draft.py`) requires non-blank `feedback` whenever `approved` is `false`, enforced by a `model_validator` (`422` if violated) — this exists because `generate_draft()`'s revision path only activates when *both* `previous_draft` and `feedback` are present; a rejection with no feedback would otherwise be treated as a fresh generation instead of a targeted revision, while still consuming one of the `MAX_DRAFT_REVISIONS` rounds.

Generation and review are deliberately **separate graph nodes**, not one. LangGraph re-executes a node from the top on every resume — if the expensive LLM generation and the `interrupt()` were in the same node, every `/approve` call would silently re-run the entire (costly) generation a second time before reaching the pause point again. Splitting them means only `draft_review_node` (cheap — just the pause) re-executes on resume; `resolve_filing_data_node` and `generate_draft_node` only run when the conditional edge actually routes back to them.

On a revision round, `generate_draft_node` passes the previous `draft` and the attorney's `feedback` back into `generate_draft()`, which includes them in the prompt as a "Previous Draft" / "Attorney Feedback" section — the agent is instructed to revise specifically what was flagged, not regenerate from scratch. Filing data is the deliberate exception to "only what's flagged" — it's resolved fresh regardless of feedback content.

## Data Fetching

| Data | Lookup key | Method |
|---|---|---|
| Case row (name → id resolution) | `case_name` | `IngestionRepository.get_case_by_name()` — done once, in the endpoint, before the graph runs |
| Templates | `process_type` | `TemplateRepository.get_by_process_type()` — **no `is_active` filter** (see gap below) |
| Form data (JSON) | `case_id` | `IngestionRepository.get_case_form_fields()`, grouped by source filename |
| Exhibit list | `case_id` | `IngestionRepository.get_case_files()`, filtered to `doc_type == "exhibit"` |
| One exhibit's text (tool) | `case_id` + `filename` | `IngestionRepository.get_file_by_filename()` |
| Filing fee rows (tool) | `form_number` (fuzzy) | `FeeRepository.get_by_form_number()` |
| Filing address rows (tool) | `form_number` (fuzzy) | `AddressRepository.get_by_form_number()` |

`case_name` is resolved to the case's numeric `id` once, at the top of the endpoint, before the graph is ever invoked — every node downstream works with that `id` from `DraftingState`, never the name. `process_type` is a second, independent input carried in `DraftingState` from the same endpoint call — it drives templates directly, with no lookup through the case at all. Fee/address lookups are keyed by `form_number` instead, since fee/address data is scraped per-form, not per-`process_type` (a packet can include multiple forms). `resolve_filing_data_node` fetches templates/form_data independently of `generate_draft_node` (a second, separate DB round-trip each pass) since it's a separate graph node with its own `AsyncSessionLocal()` block.

## Known Gaps

- **Fee/address resolution isn't independently enforced.** See "Known gap in this design" under "Fee/Address Lookup Tools" above.
- **`resolve_filing_data_node` adds a full extra LLM call to every generation and every revision**, even for a `process_type` whose templates never mention a fee or address (it does skip the call entirely if there are zero templates, but not based on template *content*). Cost/latency tradeoff accepted deliberately — see "Two-Agent Split" above for why unconditional re-resolution matters more than the saved cost.
- **`templates.is_active` still never gets set** (same gap documented in `docs/templates.md`/`docs/database.md`) — since there's no activate endpoint, both agents fetch *every* template row for a `process_type`, not a curated "2 active" subset.
- **No `drafts` table.** The generated draft only exists inside the LangGraph checkpoint (Postgres-backed, survives restarts, but not queryable). There's no way to fetch a case's current/past drafts outside of the `/generate` or `/approve` response itself — no `GET` endpoint, no relational row. Deliberately deferred until after seeing real agent output; not yet revisited.
- **`/generate` doesn't validate that `process_type` actually has templates.** `GET /api/v1/template-generation/process-types` (`docs/templates.md`) is the intended way a caller picks a valid `process_type`, but nothing on `/draft/{case_name}/{process_type}/generate` enforces that the value passed actually matches one — a typo'd or nonexistent `process_type` just degrades gracefully (zero templates found, `resolve_filing_data_no_templates` / `draft_context_no_templates` logged, draft generated with no structural reference and heavy `[GAP: ...]` markers) rather than being rejected outright.
- **Revision loop is otherwise unbounded in cost** if someone scripts repeated rejections — `MAX_DRAFT_REVISIONS` caps *rounds*, but each round is now two real, billed LLM calls (filing-data resolution + drafting) instead of one.
- **No code-level check on the drafting agent's final answer.** The model can still return a clarifying question, a reasoning preamble, or any other non-document text as its "final answer" — `generate_draft()` only checks that it's non-empty. Everything preventing this is prompt-only (see `# Output` in `DRAFT_GENERATION_PROMPT`).
- **`get_file_by_filename` (`app/repositories/ingestion.py`) has no `ORDER BY` and no unique constraint on `(case_id, filename)`.** If two `ingestion_files` rows share a filename for the same case (e.g. a corrected re-upload), `get_exhibit_text` can return either one nondeterministically.
- **Tab-stop-based columnar layouts don't reconstruct correctly** — see the note under "Word Document Export" above.

## API Endpoints

| Method | Path | Body | Response |
|---|---|---|---|
| `POST` | `/api/v1/draft/{case_name}/{process_type}/generate` | none | `.docx` file (binary body), header `X-Case-Id`; `404` if `case_name` doesn't match any case |
| `POST` | `/api/v1/draft/{case_name}/{process_type}/approve` | `{approved, feedback?}` (`feedback` required, non-blank, when `approved: false` — `422` otherwise) | `.docx` file (binary body), headers `X-Case-Id`, `X-Approved`, `X-Max-Revisions-Reached`; `404` if `case_name` doesn't match any case, `409` if no draft is currently pending review for this `(case, process_type)` |

Both responses: `media_type` `application/vnd.openxmlformats-officedocument.wordprocessingml.document`, `Content-Disposition: attachment; filename="draft_{case_id}.docx"` — the filename still uses the resolved numeric `case_id`, not `case_name`. The `X-Case-Id` header is likewise always the numeric id, for backend traceability even though the request itself was addressed by name.

## Key Files

| File | Role |
|---|---|
| `app/api/v1/endpoints/draft.py` | `POST /{case_name}/{process_type}/generate` (resolves `case_name` via `IngestionRepository`, then `graph.ainvoke()`), `POST /{case_name}/{process_type}/approve` (same resolution, then `graph.ainvoke(Command(resume=...))`) — both convert the draft to `.docx` and return it as the response |
| `app/repositories/ingestion.py` | `get_case_by_name()` — resolves the endpoint's `case_name` path param to the case row, used by both draft endpoints before touching the graph |
| `app/graph/nodes/filing_data.py` | `resolve_filing_data_node` — reads `process_type` straight from state, fetches templates/form_data, wires `FeeRepository`/`AddressRepository`, formats their rows into tool-result text (`_format_fee_result`/`_format_address_result`) |
| `app/graph/nodes/draft.py` | `generate_draft_node`, `draft_review_node`, `route_after_review` |
| `app/graph/main_graph.py` | Wires all three nodes: `resolve_filing_data → generate_draft → draft_review`, with the review→regenerate edge routing back to `resolve_filing_data` |
| `app/graph/state.py` | `DraftingState` — `process_type`, `case_id`, `draft`, `draft_revision_count`, `draft_feedback`, `draft_approved`, `filing_fee_data`, `filing_address_data`; both `process_type` and `case_id` are set once at `/generate` from the endpoint's resolved inputs and never derived from a DB row mid-graph |
| `app/services/draft/filing_data.py` | `resolve_filing_data()` — the filing-data agent's tool-calling loop, `GET_FILING_FEE_TOOL` / `GET_FILING_ADDRESS_TOOL` specs, `FilingLookupResult`, `FilingData` |
| `app/services/draft/pipeline.py` | `generate_draft()` — the drafting agent's tool-calling loop, `GET_EXHIBIT_TEXT_TOOL` spec |
| `app/services/draft/word_formatter.py` | `draft_to_docx_bytes()` — converts the labeled plain-text draft into `.docx` bytes, see "Word Document Export" above |
| `app/services/llm/client.py` | `invoke_with_tools()` — Bedrock Converse API wrapper with `toolConfig`, explicit `maxTokens: 8192` (a missing token limit causes truncated/unparseable responses) |
| `app/prompts.py` | `FILING_DATA_RESOLUTION_PROMPT`, `DRAFT_GENERATION_PROMPT` |
| `app/repositories/fee.py` / `address.py` | `get_by_form_number()` — fuzzy lookup backing the two filing-data tools |
| `app/repositories/fuzzy_match.py` | `best_form_number_match()`, `FUZZY_MATCH_THRESHOLD` — shared `rapidfuzz` matcher used by both repos |
| `app/schemas/draft.py` | `DraftApproveRequest` — the only schema in this feature, since responses are `.docx` files, not JSON; `model_validator` requires non-blank `feedback` when `approved` is `false` |
| `app/config.py` | `MAX_DRAFT_REVISIONS` (env-configurable, default `5`) |

## Logging

Every event below carries `case_id` — including the service-layer ones inside the two tool-calling loops (`services/draft/pipeline.py`, `services/draft/filing_data.py`). Both `generate_draft()` and `resolve_filing_data()` take `case_id` as a parameter and bind it once at the top (`log = logger.bind(case_id=case_id)`), so a case's filing-data resolution and its draft generation can each be traced end to end without joining by timestamp adjacency.

| Event | Emitted from | When |
|---|---|---|
| `draft_generate_request_received` / `_done` | `draft.py` (endpoint) | before/after `/generate`, once `case_name` has resolved to `case_id`; `_received` also includes `case_name` |
| `draft_approve_request_received` / `_done` | `draft.py` (endpoint) | before/after `/approve`, once `case_name` has resolved to `case_id`; `_done` includes `max_revisions` (the configured cap) alongside `max_revisions_reached` (whether it was hit) |
| `draft_approve_no_pending_review` | `draft.py` (endpoint) | warning — `/approve` called with nothing paused for this `(case, process_type)`; returns `409` |
| `resolve_filing_data_node_started` / `_done` | `graph/nodes/filing_data.py` | node entry/exit, `_done` includes whether fee/address were found |
| `resolve_filing_data_no_templates` | `graph/nodes/filing_data.py` | zero templates matched — skips the LLM call entirely, both fields resolve to `None` |
| `filing_data_fee_fetch_done` / `_not_found` | `graph/nodes/filing_data.py` | per `get_filing_fee` call — `query`, `matched_form_number`, `score`, `row_count` |
| `filing_data_address_fetch_done` / `_not_found` | `graph/nodes/filing_data.py` | per `get_filing_address` call — same fields as above |
| `filing_data_resolution_started` / `_done` | `services/draft/filing_data.py` | start/end of the filing-data agent loop, `_done` includes `fee_calls`/`address_calls` counts |
| `filing_data_resolution_tool_call` | `services/draft/filing_data.py` | per tool call, includes `tool` name, `query`, `matched_form_number`, `match_score`, `row_count`, the model's stated `reason` |
| `filing_data_resolution_unknown_tool` | `services/draft/filing_data.py` | error, safety-net case if the model calls a tool name outside the two registered specs |
| `filing_data_resolution_max_iterations_exceeded` | `services/draft/filing_data.py` | warning — degrades gracefully (returns whatever was captured) rather than raising, since this is a helper agent, not the primary deliverable |
| `generate_draft_node_started` / `_done` | `graph/nodes/draft.py` | node entry/exit, includes `revision_count`, `is_revision`, `has_filing_fee_data`, `has_filing_address_data` |
| `draft_context_fetched` | `graph/nodes/draft.py` | after templates/files/form_data fetched, with counts |
| `draft_context_no_templates` | `graph/nodes/draft.py` | warning if zero templates matched the `process_type` |
| `draft_exhibit_fetch_done` / `_not_found` | `graph/nodes/draft.py` | per `get_exhibit_text` call, whether the DB lookup succeeded |
| `draft_review_resumed` | `graph/nodes/draft.py` | after `interrupt()` resumes with the human's decision |
| `draft_revision_cap_reached` | `graph/nodes/draft.py` | warning, if `MAX_DRAFT_REVISIONS` hit without approval; includes `max_revisions` (the configured cap) alongside `revision_count` |
| `draft_generation_started` / `_done` | `services/draft/pipeline.py` | start/end of the drafting agent loop, `_done` includes `draft_length` |
| `draft_generation_tool_call` | `services/draft/pipeline.py` | per `get_exhibit_text` call, includes `filename` and the model's stated `reason` |
| `draft_generation_empty_response` | `services/draft/pipeline.py` | warning, if the model's final answer was empty |
| `draft_generation_max_iterations_exceeded` | `services/draft/pipeline.py` | error, safety-net case that shouldn't normally happen |
