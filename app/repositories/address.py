from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.address import FormFilingAddress


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

        stmt = insert(FormFilingAddress).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["form_number", "filing_scenario", "lockbox_name"],
            set_={
                "form_title": stmt.excluded.form_title,
                "form_url": stmt.excluded.form_url,
                "applies_to": stmt.excluded.applies_to,
                "usps_address": stmt.excluded.usps_address,
                "courier_address": stmt.excluded.courier_address,
                "address_details": stmt.excluded.address_details,
                "scraped_at": func.now(),
            },
        )
        await self.session.execute(stmt)
        await self.session.commit()
        return len(rows)

    async def list_all(self) -> list[FormFilingAddress]:
        result = await self.session.execute(
            select(FormFilingAddress).order_by(
                FormFilingAddress.form_number, FormFilingAddress.filing_scenario
            )
        )
        return list(result.scalars().all())
