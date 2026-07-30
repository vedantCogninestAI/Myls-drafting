import re

_VALID_ALIGNMENT_LABELS = {"CENTER", "RIGHT", "JUSTIFY"}
_ALIGNMENT_LABEL_RE = re.compile(r"^\[([A-Za-z][A-Za-z ]{1,20})\]")
_ORPHAN_BRACKET_RE = re.compile(r"^[:)]$")
_CODE_FENCE_RE = re.compile(r"```")
_EXAMPLE_LEAK_RE = re.compile(r"\bExample\s+(\d+)\b")
_FILENAME_RE = re.compile(r'"([^"\n]+\.[A-Za-z0-9]{2,5})"')


def check_formatting(draft_text: str, available_files: list[dict]) -> list[str]:
    """Deterministic, non-LLM checks for formatting/leakage mistakes in a
    drafting agent's output. Returns a list of human-readable issues — empty
    means the draft passed. Used both as the check_draft_formatting tool
    result and as the harness's own gate on the model's final answer."""
    issues: list[str] = []

    for line_no, raw_line in enumerate(draft_text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        if _ORPHAN_BRACKET_RE.match(line):
            issues.append(f"line {line_no}: orphaned bracket-only line ('{line}')")
            continue

        label_match = _ALIGNMENT_LABEL_RE.match(line)
        if label_match:
            label = label_match.group(1).strip().upper()
            if label.startswith("GAP"):
                continue
            if label not in _VALID_ALIGNMENT_LABELS:
                issues.append(
                    f"line {line_no}: unrecognized formatting label "
                    f"'[{label_match.group(1)}]' (expected one of "
                    f"{sorted(_VALID_ALIGNMENT_LABELS)}, or a [GAP: ...] marker)"
                )

    if _CODE_FENCE_RE.search(draft_text):
        issues.append("draft contains a markdown code fence ('```'), which must never appear in the output")

    example_leaks = sorted(set(_EXAMPLE_LEAK_RE.findall(draft_text)), key=int)
    if example_leaks:
        issues.append(
            "draft references reference-template labels that must never appear in real "
            "output: " + ", ".join(f"'Example {n}'" for n in example_leaks)
        )

    known_filenames = {f["filename"] for f in available_files}
    for filename in sorted(set(_FILENAME_RE.findall(draft_text))):
        if filename not in known_filenames:
            issues.append(
                f"draft names a file ('{filename}') that is not in this case's Available Files list"
            )

    return issues
