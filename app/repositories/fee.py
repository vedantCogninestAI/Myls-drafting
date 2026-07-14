from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fee import FormFee
from app.repositories.fuzzy_match import FUZZY_MATCH_THRESHOLD, FormNumberMatch, best_form_number_match


class FeeRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def bulk_upsert(self, rows: list[dict]) -> int:
        if not rows:
            return 0

        deduped = {(row["form_number"], row["filing_category"]): row for row in rows}
        rows = list(deduped.values())

        stmt = insert(FormFee).values(rows)
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

    async def clear_all(self) -> None:
        await self.session.execute(delete(FormFee))
        await self.session.commit()

    async def list_all(self) -> list[FormFee]:
        result = await self.session.execute(
            select(FormFee).order_by(FormFee.form_number, FormFee.filing_category)
        )
        return list(result.scalars().all())

    async def get_by_form_number(self, form_number: str) -> tuple[list[FormFee], FormNumberMatch]:
        all_rows = await self.list_all()
        distinct_numbers = sorted({row.form_number for row in all_rows})
        match = best_form_number_match(form_number, distinct_numbers)

        if match.form_number is None or match.score < FUZZY_MATCH_THRESHOLD:
            return [], match

        matched_rows = [row for row in all_rows if row.form_number == match.form_number]
        return matched_rows, match
