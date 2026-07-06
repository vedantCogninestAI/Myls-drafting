from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ingestion import IngestionResult


class IngestionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save(self, thread_id: str, results: dict, failed: dict) -> IngestionResult:
        record = IngestionResult(thread_id=thread_id, results=results, failed=failed)
        self.session.add(record)
        await self.session.commit()
        await self.session.refresh(record)
        return record
