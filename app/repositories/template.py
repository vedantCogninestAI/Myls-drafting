from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.template import Template


class TemplateRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def count_by_process_type(self, process_type: str) -> int:
        result = await self.session.execute(
            select(func.count()).select_from(Template).where(Template.process_type == process_type)
        )
        return result.scalar_one()

    async def save_templates(self, process_type: str, files: list[dict]) -> list[Template]:
        records = [Template(process_type=process_type, **data) for data in files]
        self.session.add_all(records)
        await self.session.commit()
        return records

    async def get_by_process_type(self, process_type: str) -> list[Template]:
        result = await self.session.execute(
            select(Template).where(Template.process_type == process_type)
        )
        return list(result.scalars().all())

    async def get_distinct_process_types(self) -> list[str]:
        result = await self.session.execute(select(Template.process_type).distinct())
        return list(result.scalars().all())
