"""Wipes scraped USCIS reference data (tb_form_fees_draft_ai,
tb_form_address_draft_ai), leaving everything else (cases, ingestion,
templates) intact.

Not wired into the FastAPI app or reachable over HTTP — run manually:

    python reset_scraped_data.py
"""
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection

from app.config import settings
from app.core.db import engine

TABLE_CHOICES = {
    "1": ["tb_form_fees_draft_ai"],
    "2": ["tb_form_address_draft_ai"],
    "3": ["tb_form_fees_draft_ai", "tb_form_address_draft_ai"],
}


async def _print_counts(conn: AsyncConnection, tables: list[str], label: str) -> None:
    print(f"-- Row counts ({label}) --")
    for table in tables:
        result = await conn.execute(text(f"SELECT COUNT(*) FROM {table}"))
        print(f"  {table}: {result.scalar_one()}")


async def wipe_tables(tables: list[str]) -> None:
    db_url = make_url(settings.DATABASE_URL)
    async with engine.begin() as conn:
        live_db_name = (await conn.execute(text("SELECT DATABASE()"))).scalar_one()
        print(f"Connected to: host={db_url.host}  port={db_url.port}  database={live_db_name}")

        result = await conn.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE()")
        )
        existing_tables = {row[0] for row in result}

        targets = [t for t in tables if t in existing_tables]
        if not targets:
            print("No matching tables found — nothing to wipe.")
            return

        await _print_counts(conn, targets, "before")
        # MySQL's TRUNCATE can't take multiple tables in one statement; auto-
        # increment resets automatically (no RESTART IDENTITY needed).
        for table in targets:
            await conn.execute(text(f"TRUNCATE TABLE {table}"))
        await _print_counts(conn, targets, "after")
        print(f"Wiped: {', '.join(targets)}")


if __name__ == "__main__":
    choice = input(
        "Delete data for:\n"
        "  [1] Fees only (tb_form_fees_draft_ai)\n"
        "  [2] Addresses only (tb_form_address_draft_ai)\n"
        "  [3] Both\n"
        "Choice: "
    ).strip()

    tables = TABLE_CHOICES.get(choice)
    if tables is None:
        print("Invalid choice. Aborted.")
        sys.exit(0)

    _db_url = make_url(settings.DATABASE_URL)
    confirm = input(
        f"Target: host={_db_url.host}  port={_db_url.port}  database={_db_url.database}\n"
        f"This will permanently delete data in: {', '.join(tables)}. Type 'yes' to continue: "
    )
    if confirm.strip().lower() != "yes":
        print("Aborted.")
        sys.exit(0)

    asyncio.run(wipe_tables(tables))
