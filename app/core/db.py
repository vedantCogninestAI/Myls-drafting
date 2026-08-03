from collections.abc import AsyncGenerator

import asyncmy
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# asyncmy requires the mysql+asyncmy:// scheme
_url = settings.DATABASE_URL.replace("mysql://", "mysql+asyncmy://", 1)

engine = create_async_engine(_url, pool_pre_ping=True, pool_recycle=1800)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


async def create_raw_conn() -> asyncmy.Connection:
    """Open a raw asyncmy connection (not a SQLAlchemy session) for callers
    that need direct driver access, e.g. LangGraph's MySQL checkpointer.

    Built manually rather than parsed from a raw connection string, since a
    plain urllib parse (unlike SQLAlchemy's URL parser, used here) does not
    decode percent-encoded password characters (e.g. "%40" would stay
    literal instead of becoming "@").
    """
    db_url = make_url(settings.DATABASE_URL)
    return await asyncmy.connect(
        host=db_url.host,
        port=db_url.port or 3306,
        user=db_url.username,
        password=db_url.password or "",
        database=db_url.database,
        autocommit=True,
    )


async def is_conn_alive(conn: asyncmy.Connection) -> bool:
    try:
        async with conn.cursor() as cur:
            await cur.execute("SELECT 1")
        return True
    except Exception:
        return False
