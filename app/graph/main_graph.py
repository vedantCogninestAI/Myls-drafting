from langgraph.graph import StateGraph, START, END

from app.graph.state import DraftingState
from app.graph.nodes.ingestion import ocr_documents_node


def build_graph(checkpointer):
    graph = StateGraph(DraftingState)

    # --- ingestion phase ---
    graph.add_node("ocr_documents", ocr_documents_node)
    graph.add_edge(START, "ocr_documents")
    graph.add_edge("ocr_documents", END)

    # --- cover letter phase (future) ---
    # graph.add_node("generate_cover_letter", generate_cover_letter_node)
    # graph.add_node("cover_letter_review", cover_letter_hitl_node)

    # --- draft phase (future) ---
    # graph.add_node("generate_draft", generate_draft_node)
    # graph.add_node("draft_review", draft_hitl_node)

    return graph.compile(checkpointer=checkpointer)
