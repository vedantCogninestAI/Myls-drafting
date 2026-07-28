"""Wipes all case/template data, leaving the schema and scraped reference
data (form_fees, form_address) intact.

Not wired into the FastAPI app or reachable over HTTP — run manually:

    python reset_db.py
"""
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.db import engine

APP_TABLES = [
    "form_fields",
    "ingestion_files",
    "templates",
    "cases",
]

CHECKPOINT_TABLES = [
    "checkpoint_writes",
    "checkpoint_blobs",
    "checkpoints",
    "checkpoint_migrations",
]


async def _print_counts(conn: AsyncConnection, tables: list[str], label: str) -> None:
    print(f"-- Row counts ({label}) --")
    for table in tables:
        result = await conn.execute(text(f"SELECT COUNT(*) FROM {table}"))
        print(f"  {table}: {result.scalar_one()}")


async def reset_db() -> None:
    async with engine.begin() as conn:
        result = await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
        existing_tables = {row[0] for row in result}

        targets = [t for t in APP_TABLES + CHECKPOINT_TABLES if t in existing_tables]
        if not targets:
            print("No tables found — nothing to wipe.")
            return

        await _print_counts(conn, targets, "before")
        await conn.execute(text(f"TRUNCATE TABLE {', '.join(targets)} RESTART IDENTITY CASCADE"))
        await _print_counts(conn, targets, "after")
        print(f"Wiped: {', '.join(targets)}")


if __name__ == "__main__":
    confirm = input(
        "This will permanently delete all case/ingestion/template data "
        "(form_fees and form_address are left untouched). Type 'yes' to continue: "
    )
    if confirm.strip().lower() != "yes":
        print("Aborted.")
        sys.exit(0)
    asyncio.run(reset_db())
