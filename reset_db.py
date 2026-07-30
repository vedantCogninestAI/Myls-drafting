"""Wipes all case/template data, leaving the schema and scraped reference
data (tb_form_fees_draft_ai, tb_form_address_draft_ai) intact.

APP_TABLES are truncated (schema stays, rows emptied). CHECKPOINT_TABLES
are dropped entirely rather than truncated — checkpoint_migrations is a
version-tracking table AsyncMySaver.setup() reads on next app startup to
decide which of its internal migrations still need to run; truncating it
alone (while the other checkpoint tables' indexes survive) makes setup()
try to recreate an already-existing index and crash. Dropping all 4
together gives setup() a genuinely clean slate — it recreates them on the
next app startup automatically, nothing to run manually here for that.

Not wired into the FastAPI app or reachable over HTTP — run manually:

    python reset_db.py
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

APP_TABLES = [
    "tb_form_fields_draft_ai",
    "tb_ingestion_files_draft_ai",
    "tb_templates_draft_ai",
    "tb_cases_draft_ai",
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
    db_url = make_url(settings.DATABASE_URL)
    async with engine.begin() as conn:
        live_db_name = (await conn.execute(text("SELECT DATABASE()"))).scalar_one()
        print(f"Connected to: host={db_url.host}  port={db_url.port}  database={live_db_name}")

        result = await conn.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE()")
        )
        existing_tables = {row[0] for row in result}

        app_targets = [t for t in APP_TABLES if t in existing_tables]
        checkpoint_targets = [t for t in CHECKPOINT_TABLES if t in existing_tables]
        targets = app_targets + checkpoint_targets
        if not targets:
            print("No tables found — nothing to wipe.")
            return

        await _print_counts(conn, targets, "before")
        # MySQL's TRUNCATE can't take multiple tables in one statement, has no
        # RESTART IDENTITY (auto-increment resets automatically on TRUNCATE),
        # and enforces FK checks by default with no CASCADE option — so checks
        # are disabled for the duration instead, same net effect as before.
        await conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        for table in app_targets:
            await conn.execute(text(f"TRUNCATE TABLE {table}"))
        # Checkpoint tables are DROPPED, not truncated: checkpoint_migrations
        # is a version-tracking table AsyncMySaver.setup() reads on startup —
        # truncating just that one (while leaving the other tables' indexes
        # in place) makes it think nothing was ever set up and try to
        # recreate an already-existing index, crashing app startup. Dropping
        # all 4 together gives setup() a genuinely clean slate to recreate
        # from, avoiding that mismatch entirely.
        for table in checkpoint_targets:
            await conn.execute(text(f"DROP TABLE {table}"))
        await conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
        await _print_counts(conn, app_targets, "after")
        print(f"Wiped (truncated): {', '.join(app_targets) or '(none)'}")
        print(f"Wiped (dropped, will be recreated on next app startup): {', '.join(checkpoint_targets) or '(none)'}")


if __name__ == "__main__":
    _db_url = make_url(settings.DATABASE_URL)
    confirm = input(
        f"Target: host={_db_url.host}  port={_db_url.port}  database={_db_url.database}\n"
        "This will permanently delete all case/ingestion/template data on that database "
        "(tb_form_fees_draft_ai and tb_form_address_draft_ai are left untouched). "
        "Type 'yes' to continue: "
    )
    if confirm.strip().lower() != "yes":
        print("Aborted.")
        sys.exit(0)
    asyncio.run(reset_db())
