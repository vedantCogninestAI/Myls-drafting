import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.draft_session import DraftSession


class DraftSessionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        case_id: int,
        process_type: str,
        draft: str,
        filing_fee_data: str | None,
        filing_address_data: str | None,
    ) -> DraftSession:
        record = DraftSession(
            case_id=case_id,
            process_type=process_type,
            initial_draft=draft,
            current_draft=draft,
            revision_count=0,
            filing_fee_data=filing_fee_data,
            filing_address_data=filing_address_data,
        )
        self.session.add(record)
        await self.session.commit()
        await self.session.refresh(record)
        return record

    async def get_by_id(self, session_id: uuid.UUID) -> DraftSession | None:
        result = await self.session.execute(
            select(DraftSession).where(DraftSession.id == session_id)
        )
        return result.scalars().first()

    async def update_after_revision(self, draft_session: DraftSession, new_draft: str) -> DraftSession:
        draft_session.current_draft = new_draft
        draft_session.revision_count += 1
        await self.session.commit()
        await self.session.refresh(draft_session)
        return draft_session
