import structlog
from fastapi import Request
from langgraph.checkpoint.mysql.asyncmy import AsyncMySaver

from app.core.db import create_raw_conn, is_conn_alive
from app.graph.main_graph import build_graph

logger = structlog.get_logger(__name__)


async def create_graph_and_conn():
    conn = await create_raw_conn()
    checkpointer = AsyncMySaver(conn=conn)
    await checkpointer.setup()
    graph = build_graph(checkpointer=checkpointer)
    return conn, graph


async def get_graph(request: Request):
    """FastAPI dependency: returns a guaranteed-live graph, transparently
    reconnecting the checkpointer's MySQL connection if it died (idle
    timeout, network blip) since the app started or the last request.
    """
    app = request.app
    if not await is_conn_alive(app.state.checkpointer_conn):
        logger.warning("checkpointer_connection_dead_reconnecting")
        old_conn = app.state.checkpointer_conn
        app.state.checkpointer_conn, app.state.graph = await create_graph_and_conn()
        try:
            old_conn.close()
        except Exception:
            pass
    return app.state.graph
