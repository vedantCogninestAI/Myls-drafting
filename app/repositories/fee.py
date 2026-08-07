from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fee import FormFee
from app.repositories.fuzzy_match import FUZZY_MATCH_THRESHOLD, FormNumberMatch, best_form_number_match


class FeeRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_all_hashes(self) -> dict[str, str]:
        result = await self.session.execute(select(FormFee.topic_id, FormFee.hash_id))
        return dict(result.all())

    async def get_by_form_number(self, query: str) -> tuple[FormFee | None, FormNumberMatch]:
        """Fuzzy-match `query` against just the form-number segment of each row's
        dropdown `label` (e.g. 'N-400' out of 'N-400, Application for
        Naturalization') — fee rows have no separate form-number column. Matching
        against the full label instead of just this prefix skews scores against
        forms with long titles, since rapidfuzz's WRatio discounts matches more
        heavily as the length gap between query and candidate grows."""
        result = await self.session.execute(select(FormFee.topic_id, FormFee.label))
        pairs = result.all()
        form_numbers = [label.split(",", 1)[0].strip() for _, label in pairs]
        match = best_form_number_match(query, form_numbers)
        if match.form_number is None or match.score < FUZZY_MATCH_THRESHOLD:
            return None, match

        topic_id = next(
            tid for (tid, _), form_number in zip(pairs, form_numbers) if form_number == match.form_number
        )
        result = await self.session.execute(select(FormFee).where(FormFee.topic_id == topic_id))
        return result.scalar_one_or_none(), match

    async def upsert(
        self, topic_id: str, label: str, form_url: str, extracted_data: dict, hash_id: str
    ) -> None:
        result = await self.session.execute(select(FormFee).where(FormFee.topic_id == topic_id))
        row = result.scalar_one_or_none()
        if row is None:
            self.session.add(
                FormFee(
                    topic_id=topic_id,
                    label=label,
                    form_url=form_url,
                    extracted_data=extracted_data,
                    hash_id=hash_id,
                )
            )
        else:
            row.label = label
            row.form_url = form_url
            row.extracted_data = extracted_data
            row.hash_id = hash_id
        await self.session.commit()

    async def list_all(self) -> list[FormFee]:
        result = await self.session.execute(select(FormFee).order_by(FormFee.label))
        return list(result.scalars().all())
