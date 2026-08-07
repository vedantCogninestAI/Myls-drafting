from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.address import FormAddress
from app.repositories.fuzzy_match import FUZZY_MATCH_THRESHOLD, FormNumberMatch, best_form_number_match


class AddressRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_all_hashes(self) -> dict[str, str]:
        result = await self.session.execute(select(FormAddress.form_number, FormAddress.hash_id))
        return dict(result.all())

    async def get_by_form_number(self, query: str) -> tuple[FormAddress | None, FormNumberMatch]:
        result = await self.session.execute(select(FormAddress.form_number))
        candidates = [row[0] for row in result.all()]
        match = best_form_number_match(query, candidates)
        if match.form_number is None or match.score < FUZZY_MATCH_THRESHOLD:
            return None, match

        result = await self.session.execute(
            select(FormAddress).where(FormAddress.form_number == match.form_number)
        )
        return result.scalar_one_or_none(), match

    async def upsert(
        self, form_number: str, form_title: str, form_url: str, extracted_data: dict, hash_id: str
    ) -> None:
        result = await self.session.execute(
            select(FormAddress).where(FormAddress.form_number == form_number)
        )
        row = result.scalar_one_or_none()
        if row is None:
            self.session.add(
                FormAddress(
                    form_number=form_number,
                    form_title=form_title,
                    form_url=form_url,
                    extracted_data=extracted_data,
                    hash_id=hash_id,
                )
            )
        else:
            row.form_title = form_title
            row.form_url = form_url
            row.extracted_data = extracted_data
            row.hash_id = hash_id
        await self.session.commit()

    async def list_all(self) -> list[FormAddress]:
        result = await self.session.execute(select(FormAddress).order_by(FormAddress.form_number))
        return list(result.scalars().all())
