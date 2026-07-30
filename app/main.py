import asyncio
import sys
from contextlib import asynccontextmanager

import asyncmy
import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.mysql.asyncmy import AsyncMySaver
from sqlalchemy.engine import make_url

from app.api.v1.router import router as v1_router
from app.config import settings
from app.core.logging import setup_logging
from app.graph.main_graph import build_graph
from app.middleware.request_logging import RequestLoggingMiddleware

# Some async DB drivers require a SelectorEventLoop; Windows defaults to
# ProactorEventLoop, which they can't use.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info(
        "config_loaded",
        debug=settings.DEBUG,
        ingestion_concurrency=settings.INGESTION_CONCURRENCY,
        classify_pdf_llm=settings.CLASSIFY_PDF_LLM,
        template_concurrency=settings.TEMPLATE_CONCURRENCY,
        max_draft_revisions=settings.MAX_DRAFT_REVISIONS,
        scrape_concurrency=settings.SCRAPE_CONCURRENCY,
        fee_scrape_batch_delay=settings.FEE_SCRAPE_BATCH_DELAY,
        fee_scrape_timeout=settings.FEE_SCRAPE_TIMEOUT,
        llm_max_retries=settings.LLM_MAX_RETRIES,
        bedrock_model_id=settings.BEDROCK_MODEL_ID,
        aws_region=settings.AWS_REGION,
        log_level=settings.LOG_LEVEL,
    )
    # Not using AsyncMySaver.from_conn_string(DATABASE_URL) here: it parses
    # the connection string with a plain urllib.parse.urlparse(), which does
    # NOT decode percent-encoded characters in the password (e.g. "%40"
    # stays literal instead of becoming "@") — unlike SQLAlchemy's URL
    # parser, which does. A password containing a URL-encoded character
    # (as recommended in docs/setup.md) would silently fail to authenticate.
    # The connection is built manually instead, using SQLAlchemy's URL
    # parser for correct decoding.
    db_url = make_url(settings.DATABASE_URL)
    conn = await asyncmy.connect(
        host=db_url.host,
        port=db_url.port or 3306,
        user=db_url.username,
        password=db_url.password or "",
        database=db_url.database,
        autocommit=True,
    )
    try:
        checkpointer = AsyncMySaver(conn=conn)
        await checkpointer.setup()
        app.state.graph = build_graph(checkpointer=checkpointer)
        yield
    finally:
        conn.close()


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
