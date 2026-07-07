import asyncio
import csv
import json
from datetime import date, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from app.core.db import AsyncSessionLocal
from app.models.fee import FormFeeAddress
from app.repositories.fee import FeeRepository
from app.services.fee.fee_service import run_fee_scrape


OUT_DIR = Path("outputs")


def json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"Unsupported type: {type(value)!r}")


def fee_text(row: dict, key: str) -> str | None:
    if key == "paper":
        return row["fee_details"].get("Paper Filing Fee") or row["fee_details"].get("Paper Fee")
    return row["fee_details"].get("Online Filing Fee") or row["fee_details"].get("Online Fee")


def norm(value: str) -> str:
    return (
        value.replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("Non-profit", "Nonprofit")
        .strip()
    )


def has_item(items: list[dict], category_part: str, paper=None, online=None) -> bool:
    for item in items:
        if norm(category_part) not in norm(item["filing_category"]):
            continue
        if paper is not None and item["paper_fee"] != paper:
            continue
        if online is not None and item["online_fee"] != online:
            continue
        return True
    return False


def model_to_dict(row: FormFeeAddress) -> dict:
    return {
        "id": row.id,
        "form_number": row.form_number,
        "form_title": row.form_title,
        "form_url": row.form_url,
        "filing_category": row.filing_category,
        "paper_fee": row.paper_fee,
        "online_fee": row.online_fee,
        "paper_fee_text": fee_text({"fee_details": row.fee_details}, "paper"),
        "online_fee_text": fee_text({"fee_details": row.fee_details}, "online"),
        "fee_details": row.fee_details,
        "scraped_at": row.scraped_at,
        "created_at": row.created_at,
    }


def rows_for(rows: list[dict], form_number: str, title_part: str | None = None) -> list[dict]:
    result = [row for row in rows if row["form_number"] == form_number]
    if title_part:
        result = [row for row in result if title_part in row["form_title"]]
    return sorted(result, key=lambda row: row["filing_category"])


def build_verdicts(rows: list[dict]) -> dict:
    g639 = rows_for(rows, "G-639")
    i129_general = rows_for(rows, "I-129", "Petition for a Nonimmigrant Worker")
    i129_general = [
        row for row in i129_general
        if not any(part in row["form_title"] for part in ["H-2A", "H-1B", "H-1B1", "L and L-1"])
    ]
    h2a = rows_for(rows, "I-129", "H-2A Petitions")
    h1b = rows_for(rows, "I-129", "H-1B and H-1B1 Petitions")
    l_rows = rows_for(rows, "I-129", "L and L-1 Petitions")
    advance = rows_for(rows, "I-131", "Advance Parole Document")
    initial = rows_for(rows, "I-131", "Initial Parole Document")

    verdicts = {
        "issue_1": {
            "resolved": len(g639) == 1 and g639[0]["filing_category"] == "",
            "summary": "G-639 category is blank; DB no longer stores fake General Filing.",
            "row_count": len(g639),
            "evidence": g639,
        },
        "issue_2": {
            "resolved": (
                len(i129_general) == 16
                and has_item(i129_general, "Additional Fees: Asylum Program Fee", 600.0, 600.0)
                and has_item(i129_general, "If you are filing as a Nonprofit;", 0.0, 0.0)
                and has_item(i129_general, "If you are filing as a Small Employer.", 300.0, 300.0)
            ),
            "summary": "I-129 general last Additional Fees block is split into $600/$0/$300 rows.",
            "row_count": len(i129_general),
            "last_rows": i129_general[-3:],
        },
        "issue_3": {
            "resolved": (
                len(h2a) == 7
                and has_item(h2a, "H-2A petition with named workers", 1090.0, 1040.0)
                and has_item(h2a, "H-2A petition with named workers", 545.0, 545.0)
                and has_item(h2a, "H-2A petition with unnamed workers", 530.0, 480.0)
                and has_item(h2a, "H-2A petition with unnamed workers", 460.0, 460.0)
            ),
            "summary": "I-129 H-2A named/unnamed worker rows match expected form values.",
            "row_count": len(h2a),
            "items": h2a,
        },
        "issue_4": {
            "resolved": (
                len(h1b) == 9
                and has_item(h1b, "Asylum Program Fee", 600.0, 600.0)
                and has_item(h1b, "If you are filing as a Nonprofit", 0.0, 0.0)
                and has_item(h1b, "If you are filing as a Small Employer", 300.0, 300.0)
                and has_item(h1b, "Fraud Prevention and Detection fee", 500.0, 500.0)
                and has_item(h1b, "American Competitiveness and Workforce Improvement Act", 1500.0, 1500.0)
                and has_item(h1b, "Presidential Proclamation", None, 100000.0)
            ),
            "summary": "I-129 H-1B/H-1B1 rows are no longer wrongly filled/split.",
            "row_count": len(h1b),
            "items": h1b,
        },
        "issue_5": {
            "resolved": (
                len(l_rows) == 7
                and has_item(l_rows, "If you are filing L petitions. \u2014 If you are filing as a Small Employer or Nonprofit.", 695.0)
                and has_item(l_rows, "Fraud Prevention and Detection fee", 500.0)
                and has_item(l_rows, "Public Law 114-113", 4500.0)
            ),
            "summary": "I-129 L/L-1 second row and additional condition rows match the form.",
            "row_count": len(l_rows),
            "items": l_rows,
        },
        "issue_6": {
            "resolved": (
                len(advance) == 14
                and has_item(advance, "Part 1. Item 5.A.", 630.0, 580.0)
                and has_item(advance, "Part 1. Item 5.B.", 630.0)
                and has_item(advance, "Immigration Parole Fee", 1020.0, 1020.0)
            ),
            "summary": "I-131 Advance Parole rows are distinct and final $1,020 row is present.",
            "row_count": len(advance),
            "items": advance,
        },
        "issue_7": {
            "resolved": len(initial) == 7 and has_item(initial, "Immigration Parole Fee", 1020.0, 1020.0),
            "summary": "I-131 Initial Parole final block is stored in DB.",
            "row_count": len(initial),
            "last_row": initial[-1] if initial else None,
        },
    }
    return verdicts


async def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    async with AsyncSessionLocal() as session:
        repository = FeeRepository(session)
        print("starting DB scrape; this clears and repopulates form_fees_address", flush=True)
        upserted = await run_fee_scrape(repository)
        result = await session.execute(select(FormFeeAddress))
        rows = [model_to_dict(row) for row in result.scalars().all()]

    verdicts = build_verdicts(rows)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = OUT_DIR / f"fee_db_scrape_check_{timestamp}.json"
    csv_path = OUT_DIR / f"fee_db_scrape_rows_{timestamp}.csv"

    payload = {
        "generated_at": datetime.now().isoformat(),
        "source": "DB after run_fee_scrape(repository)",
        "table": "form_fees_address",
        "upserted_count": upserted,
        "db_row_count": len(rows),
        "all_resolved": all(issue["resolved"] for issue in verdicts.values()),
        "verdicts": verdicts,
    }
    json_path.write_text(json.dumps(payload, indent=2, default=json_default), encoding="utf-8")

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "form_number",
                "form_title",
                "form_url",
                "filing_category",
                "paper_fee",
                "online_fee",
                "paper_fee_text",
                "online_fee_text",
                "scraped_at",
            ],
        )
        writer.writeheader()
        for row in sorted(rows, key=lambda item: (item["form_number"], item["form_title"], item["filing_category"])):
            writer.writerow({key: row[key] for key in writer.fieldnames})

    print(json.dumps({
        "json": str(json_path),
        "csv": str(csv_path),
        "upserted_count": upserted,
        "db_row_count": len(rows),
        "all_resolved": payload["all_resolved"],
    }))


if __name__ == "__main__":
    asyncio.run(main())
