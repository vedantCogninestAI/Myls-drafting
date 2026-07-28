"""Wipes scraped USCIS reference data (form_fees, form_address), leaving
everything else (cases, ingestion, templates) intact.

Not wired into the FastAPI app or reachable over HTTP — run manually:

    python reset_scraped_data.py
"""
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.db import engine

TABLE_CHOICES = {
    "1": ["form_fees"],
    "2": ["form_address"],
    "3": ["form_fees", "form_address"],
}


async def _print_counts(conn: AsyncConnection, tables: list[str], label: str) -> None:
    print(f"-- Row counts ({label}) --")
    for table in tables:
        result = await conn.execute(text(f"SELECT COUNT(*) FROM {table}"))
        print(f"  {table}: {result.scalar_one()}")


async def wipe_tables(tables: list[str]) -> None:
    async with engine.begin() as conn:
        result = await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
        existing_tables = {row[0] for row in result}

        targets = [t for t in tables if t in existing_tables]
        if not targets:
            print("No matching tables found — nothing to wipe.")
            return

        await _print_counts(conn, targets, "before")
        await conn.execute(text(f"TRUNCATE TABLE {', '.join(targets)} RESTART IDENTITY"))
        await _print_counts(conn, targets, "after")
        print(f"Wiped: {', '.join(targets)}")


if __name__ == "__main__":
    choice = input(
        "Delete data for:\n"
        "  [1] Fees only (form_fees)\n"
        "  [2] Addresses only (form_address)\n"
        "  [3] Both\n"
        "Choice: "
    ).strip()

    tables = TABLE_CHOICES.get(choice)
    if tables is None:
        print("Invalid choice. Aborted.")
        sys.exit(0)

    confirm = input(f"This will permanently delete data in: {', '.join(tables)}. Type 'yes' to continue: ")
    if confirm.strip().lower() != "yes":
        print("Aborted.")
        sys.exit(0)

    asyncio.run(wipe_tables(tables))
