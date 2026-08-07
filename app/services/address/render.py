import textwrap

from tabulate import tabulate


def render_extracted_data_as_text_tables(
    extracted_data: dict, col_width: int = 45
) -> tuple[list[str], str]:
    """
    Render the address-extraction blob:
        {"tables": [{"headers": [...], "rows": [[...], ...]}, ...], "context": "..."}
    into grid-style text tables using tabulate.

    Simpler than the fee-schedule renderer: no per-cell alignment metadata
    here, just headers + rows + free-text context. Cells may contain literal
    "\\n" for multi-line addresses, which we wrap per-line to col_width.
    """
    rendered_tables = []

    for table in extracted_data.get("tables", []):
        headers = table.get("headers", [])
        rows = table.get("rows", [])

        clean_rows = []
        for row in rows:
            if len(row) < len(headers):
                row = row + [""] * (len(headers) - len(row))
            elif len(row) > len(headers):
                row = row[: len(headers)]

            clean_cells = []
            for cell in row:
                cell = (cell or "").replace("<br/>", "\n").replace("<br>", "\n")
                lines = [ln.strip() for ln in cell.split("\n")]
                lines = [ln for ln in lines if ln]

                wrapped = []
                for ln in lines:
                    wrapped.extend(textwrap.wrap(ln, col_width) or [""])

                clean_cells.append("\n".join(wrapped))

            clean_rows.append(clean_cells)

        text_table = tabulate(clean_rows, headers=headers, tablefmt="grid", preserve_whitespace=True)
        rendered_tables.append(text_table)

    context = extracted_data.get("context", "")
    return rendered_tables, context
