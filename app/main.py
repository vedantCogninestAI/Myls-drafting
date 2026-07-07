import asyncio
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

from app.api.v1.router import router as v1_router
from app.config import settings
from app.core.logging import setup_logging
from app.graph.main_graph import build_graph
from app.middleware.request_logging import RequestLoggingMiddleware

# psycopg's async driver requires a SelectorEventLoop; Windows defaults to
# ProactorEventLoop, which it can't use.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    # autocommit is required: the checkpointer's setup migrations include
    # `CREATE INDEX CONCURRENTLY`, which cannot run inside a transaction.
    async with AsyncConnectionPool(
        conninfo=settings.DATABASE_URL, kwargs={"autocommit": True}
    ) as pool:
        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()
        app.state.graph = build_graph(checkpointer=checkpointer)
        yield


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.PROJECT_NAME,
        version=settings.VERSION,
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestLoggingMiddleware)

    app.include_router(v1_router, prefix="/api/v1")

    return app


app = create_app()
