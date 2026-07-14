import re
from io import BytesIO

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH

ALIGNMENT_MAP = {
    "[CENTER]": WD_ALIGN_PARAGRAPH.CENTER,
    "[RIGHT]": WD_ALIGN_PARAGRAPH.RIGHT,
    "[JUSTIFY]": WD_ALIGN_PARAGRAPH.JUSTIFY,
}

_TABLE_SEPARATOR_CELL = re.compile(r"^:?-+:?$")


def _split_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _parse_markdown_table(block: str) -> list[list[str]] | None:
    """Return a markdown table's rows (header + body) if `block` is one, else None."""
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    if not all(line.startswith("|") and line.endswith("|") for line in lines[:2]):
        return None

    separator_cells = _split_table_row(lines[1])
    if not separator_cells or not all(
        _TABLE_SEPARATOR_CELL.match(cell) for cell in separator_cells
    ):
        return None

    header = _split_table_row(lines[0])
    if len(header) != len(separator_cells):
        return None

    rows = [header]
    for line in lines[2:]:
        if not (line.startswith("|") and line.endswith("|")):
            return None
        row = _split_table_row(line)
        if len(row) != len(header):
            return None
        rows.append(row)
    return rows


def draft_to_docx_bytes(draft_text: str) -> bytes:
    doc = Document()
    for block in draft_text.split("\n\n"):
        block = block.strip()
        if not block:
            continue

        table_rows = _parse_markdown_table(block)
        if table_rows is not None:
            table = doc.add_table(rows=len(table_rows), cols=len(table_rows[0]))
            table.style = "Table Grid"
            for row_idx, row in enumerate(table_rows):
                for col_idx, cell_text in enumerate(row):
                    table.cell(row_idx, col_idx).text = cell_text
            continue

        alignment = WD_ALIGN_PARAGRAPH.LEFT
        for label, value in ALIGNMENT_MAP.items():
            if block.startswith(label):
                alignment = value
                block = block[len(label):].strip()
                break

        paragraph = doc.add_paragraph(block)
        paragraph.alignment = alignment

    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()
