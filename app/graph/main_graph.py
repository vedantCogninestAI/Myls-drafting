from fastapi import Request
from langgraph.graph import StateGraph, START, END

from app.graph.state import DraftingState
from app.graph.nodes.draft import generate_draft_node
from app.graph.nodes.filing_data import resolve_filing_data_node


def build_graph():
    graph = StateGraph(DraftingState)

    # --- draft phase — cover letter is generated as part of the same draft
    # document, not a separate phase. Runs once, start to end, within a
    # single /generate request — no pause/resume. Revisions are handled by
    # a plain (non-graph) endpoint instead, see docs/future_work.md. ---
    graph.add_node("resolve_filing_data", resolve_filing_data_node)
    graph.add_node("generate_draft", generate_draft_node)
    graph.add_edge(START, "resolve_filing_data")
    graph.add_edge("resolve_filing_data", "generate_draft")
    graph.add_edge("generate_draft", END)

    return graph.compile()


def get_graph(request: Request):
    return request.app.state.graph
