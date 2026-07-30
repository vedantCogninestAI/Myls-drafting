from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.address import FormAddress
from app.repositories.fuzzy_match import FUZZY_MATCH_THRESHOLD, FormNumberMatch, best_form_number_match


class AddressRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def bulk_upsert(self, rows: list[dict]) -> int:
        if not rows:
            return 0

        deduped = {
            (row["form_number"], row["filing_scenario"], row["lockbox_name"]): row for row in rows
        }
        rows = list(deduped.values())

        stmt = insert(FormAddress).values(rows)
        # MySQL's ON DUPLICATE KEY UPDATE has no `index_elements` — it fires
        # whenever any unique constraint on the row is violated (here,
        # uq_tb_form_address_draft_ai_form_scenario_lockbox), so the columns
        # that make up that constraint are implicit rather than named.
        stmt = stmt.on_duplicate_key_update(
            form_title=stmt.inserted.form_title,
            form_url=stmt.inserted.form_url,
            applies_to=stmt.inserted.applies_to,
            usps_address=stmt.inserted.usps_address,
            courier_address=stmt.inserted.courier_address,
            address_details=stmt.inserted.address_details,
            scraped_at=func.now(),
        )
        await self.session.execute(stmt)
        await self.session.commit()
        return len(rows)

    async def list_all(self) -> list[FormAddress]:
        result = await self.session.execute(
            select(FormAddress).order_by(
                FormAddress.form_number, FormAddress.filing_scenario
            )
        )
        return list(result.scalars().all())

    async def get_by_form_number(self, form_number: str) -> tuple[list[FormAddress], FormNumberMatch]:
        all_rows = await self.list_all()
        distinct_numbers = sorted({row.form_number for row in all_rows})
        match = best_form_number_match(form_number, distinct_numbers)

        if match.form_number is None or match.score < FUZZY_MATCH_THRESHOLD:
            return [], match

        matched_rows = [row for row in all_rows if row.form_number == match.form_number]
        return matched_rows, match
