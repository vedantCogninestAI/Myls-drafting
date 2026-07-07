import asyncio
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
from xml.sax.saxutils import escape

from app.services.fee.scraper import (
    FormListing,
    _fetch_form_fee_rows,
    _row_to_fee_item,
    fetch_form_list,
    new_client,
)


OUT_DIR = Path("outputs")

TARGETS = {
    "g639": "G-639, Freedom of Information/Privacy Act Request",
    "i129_general": "I-129, Petition for a Nonimmigrant Worker",
    "i129_h2a": "I-129, Petition for a Nonimmigrant Worker - H-2A Petitions",
    "i129_h1b": "I-129, Petition for a Nonimmigrant Worker - H-1B and H-1B1 Petitions",
    "i129_l": "I-129, Petition for a Nonimmigrant Worker - L and L-1 Petitions",
    "i131_advance": "I-131, Application for Travel Documents, Parole Documents, and Arrival/Departure Records - Advance Parole Document",
    "i131_initial": "I-131, Application for Travel Documents, Parole Documents, and Arrival/Departure Records - Initial Parole Document (from outside the U.S.)",
}


def norm(value: str) -> str:
    return (
        value.replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("Non-profit", "Nonprofit")
        .strip()
    )


def listing_matches(listing: FormListing, expected: str) -> bool:
    return norm(listing.label).startswith(norm(expected))


def as_fee_items(listing: FormListing, rows: list[dict], form_url: str | None) -> list[dict]:
    number, title = listing.label.split(",", 1)
    return [_row_to_fee_item(number.strip(), title.strip(), form_url, row) for row in rows]


def fee_text(item: dict, key: str) -> str | None:
    if key == "paper":
        return item["fee_details"].get("Paper Filing Fee") or item["fee_details"].get("Paper Fee")
    return item["fee_details"].get("Online Filing Fee") or item["fee_details"].get("Online Fee")


def _cell_ref(row_idx: int, col_idx: int) -> str:
    letters = ""
    while col_idx:
        col_idx, remainder = divmod(col_idx - 1, 26)
        letters = chr(65 + remainder) + letters
    return f"{letters}{row_idx}"


def _xlsx_cell(row_idx: int, col_idx: int, value) -> str:
    ref = _cell_ref(row_idx, col_idx)
    if value is None:
        return f'<c r="{ref}"/>'
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"><v>{value}</v></c>'
    text = escape(str(value))
    return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'


def write_xlsx(path: Path, headers: list[str], rows: list[dict]) -> None:
    sheet_rows = []
    header_cells = "".join(_xlsx_cell(1, idx, header) for idx, header in enumerate(headers, 1))
    sheet_rows.append(f'<row r="1">{header_cells}</row>')
    for row_idx, row in enumerate(rows, 2):
        cells = "".join(_xlsx_cell(row_idx, idx, row.get(header)) for idx, header in enumerate(headers, 1))
        sheet_rows.append(f'<row r="{row_idx}">{cells}</row>')

    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Fee Scrape Rows" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>'
    )

    with ZipFile(path, "w", compression=ZIP_DEFLATED) as xlsx:
        xlsx.writestr("[Content_Types].xml", content_types)
        xlsx.writestr("_rels/.rels", root_rels)
        xlsx.writestr("xl/workbook.xml", workbook)
        xlsx.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        xlsx.writestr("xl/worksheets/sheet1.xml", worksheet)


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


def build_verdicts(results: dict) -> dict:
    g639 = results["g639"]["items"]
    i129_general = results["i129_general"]["items"]
    h2a = results["i129_h2a"]["items"]
    h1b = results["i129_h1b"]["items"]
    l_rows = results["i129_l"]["items"]
    advance = results["i131_advance"]["items"]
    initial = results["i131_initial"]["items"]

    issue_1_ok = len(g639) == 1 and g639[0]["filing_category"] == ""

    issue_2_ok = (
        len(i129_general) == 16
        and has_item(i129_general, "Additional Fees: Asylum Program Fee", 600.0, 600.0)
        and has_item(i129_general, "If you are filing as a Nonprofit;", 0.0, 0.0)
        and has_item(i129_general, "If you are filing as a Small Employer.", 300.0, 300.0)
    )

    issue_3_ok = (
        len(h2a) == 7
        and has_item(h2a, "H-2A petition with named workers", 1090.0, 1040.0)
        and has_item(h2a, "H-2A petition with named workers", 545.0, 545.0)
        and has_item(h2a, "H-2A petition with unnamed workers", 530.0, 480.0)
        and has_item(h2a, "H-2A petition with unnamed workers", 460.0, 460.0)
    )

    issue_4_ok = (
        len(h1b) == 9
        and has_item(h1b, "Asylum Program Fee", 600.0, 600.0)
        and has_item(h1b, "If you are filing as a Nonprofit", 0.0, 0.0)
        and has_item(h1b, "If you are filing as a Small Employer", 300.0, 300.0)
        and has_item(h1b, "Fraud Prevention and Detection fee", 500.0, 500.0)
        and has_item(h1b, "American Competitiveness and Workforce Improvement Act", 1500.0, 1500.0)
        and has_item(h1b, "Presidential Proclamation", None, 100000.0)
    )

    issue_5_ok = (
        len(l_rows) == 7
        and len(l_rows) > 1
        and "If you are filing L petitions." in l_rows[1]["filing_category"]
        and "Small Employer or Nonprofit" in l_rows[1]["filing_category"]
        and l_rows[1]["paper_fee"] == 695.0
        and has_item(l_rows, "Fraud Prevention and Detection fee", 500.0)
        and has_item(l_rows, "Public Law 114-113", 4500.0)
    )

    issue_6_ok = (
        len(advance) == 14
        and has_item(advance, "Part 1. Item 5.A.", 630.0, 580.0)
        and has_item(advance, "Part 1. Item 5.B.", 630.0)
        and has_item(advance, "Immigration Parole Fee", 1020.0, 1020.0)
    )

    issue_7_ok = (
        len(initial) == 7
        and has_item(initial, "Immigration Parole Fee", 1020.0, 1020.0)
    )

    return {
        "issue_1": {
            "resolved": issue_1_ok,
            "summary": "G-639 category is blank; scraper no longer invents General Filing.",
            "evidence": g639,
        },
        "issue_2": {
            "resolved": issue_2_ok,
            "summary": "I-129 general last Additional Fees block is split into $600/$0/$300 rows.",
            "row_count": len(i129_general),
            "last_rows": i129_general[-3:],
        },
        "issue_3": {
            "resolved": issue_3_ok,
            "summary": "I-129 H-2A named/unnamed worker rows keep correct paper and online fees.",
            "row_count": len(h2a),
            "items": h2a,
        },
        "issue_4": {
            "resolved": issue_4_ok,
            "summary": "I-129 H-1B/H-1B1 explanatory fee rows are not incorrectly split; asylum rows are separate.",
            "row_count": len(h1b),
            "items": h1b,
        },
        "issue_5": {
            "resolved": issue_5_ok,
            "summary": "I-129 L/L-1 second row and additional condition rows match the form structure.",
            "row_count": len(l_rows),
            "items": l_rows,
        },
        "issue_6": {
            "resolved": issue_6_ok,
            "summary": "I-131 Advance Parole keeps first rows distinct and includes the final $1,020 parole fee row.",
            "row_count": len(advance),
            "items": advance,
        },
        "issue_7": {
            "resolved": issue_7_ok,
            "summary": "I-131 Initial Parole final block is extracted as the $1,020 parole fee row.",
            "row_count": len(initial),
            "last_row": initial[-1] if initial else None,
        },
    }


async def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    client = new_client()
    print("fetching form list", flush=True)
    listings = await asyncio.wait_for(fetch_form_list(client), timeout=60)
    selected = {}
    for key, expected in TARGETS.items():
        selected[key] = next((listing for listing in listings if listing_matches(listing, expected)), None)
        if selected[key] is None:
            raise RuntimeError(f"Could not find target listing for {key}: {expected}")

    results = {}
    for key, listing in selected.items():
        print(f"fetching {key}: {listing.label}", flush=True)
        rows, form_url = await asyncio.wait_for(
            _fetch_form_fee_rows(client, listing), timeout=60
        )
        items = as_fee_items(listing, rows, form_url)
        results[key] = {
            "topic_id": listing.topic_id,
            "label": listing.label,
            "form_url": form_url,
            "row_count": len(items),
            "items": items,
        }

    verdicts = build_verdicts(results)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "live Firecrawl scrape of USCIS fee calculator",
        "all_resolved": all(issue["resolved"] for issue in verdicts.values()),
        "verdicts": verdicts,
        "raw_results": results,
    }

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = OUT_DIR / f"fee_scrape_issue_check_{timestamp}.json"
    csv_path = OUT_DIR / f"fee_scrape_issue_rows_{timestamp}.csv"
    xlsx_path = OUT_DIR / f"fee_scrape_issue_rows_{timestamp}.xlsx"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    headers = [
        "issue_key",
        "topic_id",
        "form_label",
        "filing_category",
        "paper_fee",
        "online_fee",
        "paper_fee_text",
        "online_fee_text",
    ]
    output_rows = []
    for issue_key, result in results.items():
        for item in result["items"]:
            output_rows.append(
                {
                    "issue_key": issue_key,
                    "topic_id": result["topic_id"],
                    "form_label": result["label"],
                    "filing_category": item["filing_category"],
                    "paper_fee": item["paper_fee"],
                    "online_fee": item["online_fee"],
                    "paper_fee_text": fee_text(item, "paper"),
                    "online_fee_text": fee_text(item, "online"),
                }
            )

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=headers,
        )
        writer.writeheader()
        writer.writerows(output_rows)

    write_xlsx(xlsx_path, headers, output_rows)

    print(
        json.dumps(
            {
                "json": str(json_path),
                "csv": str(csv_path),
                "xlsx": str(xlsx_path),
                "all_resolved": payload["all_resolved"],
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
