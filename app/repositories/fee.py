from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fee import FormFeeAddress


class FeeRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def bulk_upsert(self, rows: list[dict]) -> int:
        if not rows:
            return 0

        deduped = {(row["form_number"], row["filing_category"]): row for row in rows}
        rows = list(deduped.values())

        stmt = insert(FormFeeAddress).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["form_number", "filing_category"],
            set_={
                "form_title": stmt.excluded.form_title,
                "form_url": stmt.excluded.form_url,
                "paper_fee": stmt.excluded.paper_fee,
                "online_fee": stmt.excluded.online_fee,
                "fee_details": stmt.excluded.fee_details,
                "scraped_at": func.now(),
            },
        )
        await self.session.execute(stmt)
        await self.session.commit()
        return len(rows)

    async def list_all(self) -> list[FormFeeAddress]:
        result = await self.session.execute(
            select(FormFeeAddress).order_by(FormFeeAddress.form_number, FormFeeAddress.filing_category)
        )
        return list(result.scalars().all())
