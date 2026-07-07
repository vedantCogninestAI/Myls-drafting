import asyncio

from app.services.fee import fee_service


class _FakeRepository:
    def __init__(self):
        self.events = []

    async def clear_all(self) -> None:
        self.events.append("clear_all")

    async def bulk_upsert(self, rows: list[dict]) -> int:
        self.events.append(("bulk_upsert", len(rows)))
        assert self.events[0] == "clear_all"
        return len(rows)


def test_run_fee_scrape_clears_existing_rows_before_upserting(monkeypatch):
    repository = _FakeRepository()

    async def fake_scrape_all_fees(on_batch=None):
        assert on_batch is not None
        await on_batch([{"form_number": "I-129", "filing_category": "A"}])
        await on_batch([{"form_number": "I-131", "filing_category": "B"}])
        return [{"form_number": "I-129"}, {"form_number": "I-131"}]

    monkeypatch.setattr(fee_service, "scrape_all_fees", fake_scrape_all_fees)

    total_upserted = asyncio.run(fee_service.run_fee_scrape(repository))

    assert total_upserted == 2
    assert repository.events == ["clear_all", ("bulk_upsert", 1), ("bulk_upsert", 1)]
