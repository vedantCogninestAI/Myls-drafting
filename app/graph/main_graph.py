from langgraph.graph import StateGraph, START, END

from app.graph.state import DraftingState
from app.graph.nodes.draft import generate_draft_node, draft_review_node, route_after_review
from app.graph.nodes.filing_data import resolve_filing_data_node


def build_graph(checkpointer):
    graph = StateGraph(DraftingState)

    # --- draft phase — cover letter is generated as part of the same draft
    # document, not a separate phase. draft_review loops back to
    # resolve_filing_data on rejection (with feedback), until approved or
    # MAX_DRAFT_REVISIONS is reached. resolve_filing_data runs ahead of
    # generate_draft on every pass — fresh generation and every revision —
    # so fee/address resolution can never be silently skipped just because
    # a revision's feedback didn't mention it. ---
    graph.add_node("resolve_filing_data", resolve_filing_data_node)
    graph.add_node("generate_draft", generate_draft_node)
    graph.add_node("draft_review", draft_review_node)
    graph.add_edge(START, "resolve_filing_data")
    graph.add_edge("resolve_filing_data", "generate_draft")
    graph.add_edge("generate_draft", "draft_review")
    graph.add_conditional_edges(
        "draft_review",
        route_after_review,
        {"regenerate": "resolve_filing_data", "done": END},
    )

    return graph.compile(checkpointer=checkpointer)
