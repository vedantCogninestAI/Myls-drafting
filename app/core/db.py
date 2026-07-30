from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# asyncmy requires the mysql+asyncmy:// scheme
_url = settings.DATABASE_URL.replace("mysql://", "mysql+asyncmy://", 1)

engine = create_async_engine(_url)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
