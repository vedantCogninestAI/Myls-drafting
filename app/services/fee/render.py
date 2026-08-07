import textwrap

from tabulate import tabulate


def _find_line_index(category_lines: list[str], anchor_text: str | None) -> int | None:
    """Find which raw category line this anchor text belongs to. Returns None if no match (never guess)."""
    anchor_text = (anchor_text or "").strip()
    if not anchor_text:
        return None
    for i, line in enumerate(category_lines):
        if line.strip().lower().startswith(anchor_text.lower()):
            return i
    for i, line in enumerate(category_lines):
        if anchor_text.lower() in line.lower():
            return i
    return None


def render_extracted_data_as_text_tables(
    extracted_data: dict, col_width: int = 45
) -> tuple[list[str], str]:
    rendered_tables = []

    for table in extracted_data.get("tables", []):
        headers = table.get("headers", [])
        rows = table.get("rows", [])
        alignment = table.get("alignment", [])

        alignment_by_row = {}
        for entry in alignment:
            alignment_by_row.setdefault(entry.get("row_index"), []).append(entry)

        clean_rows = []
        for row_idx, row in enumerate(rows):
            if len(row) < len(headers):
                row = row + [""] * (len(headers) - len(row))
            elif len(row) > len(headers):
                row = row[: len(headers)]

            raw_lines = []
            for cell in row:
                cell = cell.replace("<br>", "\n")
                lines = [ln.strip() for ln in cell.split("\n")]
                lines = [ln for ln in lines if ln]
                raw_lines.append(lines)

            entries = alignment_by_row.get(row_idx, [])
            if not entries:
                display_cols = []
                for lines in raw_lines:
                    wrapped = []
                    for ln in lines:
                        wrapped.extend(textwrap.wrap(ln, col_width) or [""])
                    display_cols.append(wrapped)
            else:
                category_lines = raw_lines[0] if raw_lines else []
                category_display = []
                line_start_map = {}
                for i, ln in enumerate(category_lines):
                    line_start_map[i] = len(category_display)
                    wrapped = textwrap.wrap(ln, col_width) or [""]
                    category_display.extend(wrapped)

                display_cols = [category_display]

                for col_i in range(1, len(raw_lines)):
                    entry = next((e for e in entries if e.get("fee_column_index") == col_i), None)
                    if entry is None:
                        wrapped = []
                        for ln in raw_lines[col_i]:
                            wrapped.extend(textwrap.wrap(ln, col_width) or [""])
                        display_cols.append(wrapped)
                        continue

                    padded = [""] * len(category_display)
                    unaligned_extra = []
                    for v in entry.get("values", []):
                        anchor = v.get("aligned_to_text")
                        value_text = v.get("value", "")
                        line_idx = _find_line_index(category_lines, anchor) if anchor else None
                        if line_idx is None:
                            unaligned_extra.append(value_text)
                            continue
                        display_idx = line_start_map[line_idx]
                        if padded[display_idx]:
                            padded[display_idx] += " / " + value_text
                        else:
                            padded[display_idx] = value_text
                    display_cols.append(padded + unaligned_extra)

            max_height = max(len(c) for c in display_cols) if display_cols else 0
            display_cols = [c + [""] * (max_height - len(c)) for c in display_cols]
            clean_row = ["\n".join(c) for c in display_cols]
            clean_rows.append(clean_row)

        text_table = tabulate(clean_rows, headers=headers, tablefmt="grid", preserve_whitespace=True)
        rendered_tables.append(text_table)

    context = extracted_data.get("context", "")
    return rendered_tables, context
