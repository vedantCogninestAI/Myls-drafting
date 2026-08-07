import re
from dataclasses import dataclass

from rapidfuzz import fuzz, process

FUZZY_MATCH_THRESHOLD = 80.0

_TOKEN_RE = re.compile(r"[A-Za-z0-9-]+")


@dataclass
class FormNumberMatch:
    form_number: str | None
    score: float


def _tokenize(text: str) -> list[str]:
    """Split on anything that isn't a letter/digit/hyphen — spaces, periods,
    underscores, commas — so a form code embedded in noisier text (a
    filename, a full dropdown label) comes out as its own token, extension
    and all, with no assumption about how many letters/digits a code has."""
    return _TOKEN_RE.findall(text.upper())


def best_form_number_match(query: str, candidates: list[str]) -> FormNumberMatch:
    if not candidates:
        return FormNumberMatch(form_number=None, score=0.0)

    candidates_by_upper = {c.upper(): c for c in candidates}
    for token in _tokenize(query):
        if token in candidates_by_upper:
            return FormNumberMatch(form_number=candidates_by_upper[token], score=100.0)

    match = process.extractOne(query, candidates, scorer=fuzz.WRatio)
    if match is None:
        return FormNumberMatch(form_number=None, score=0.0)

    matched_form_number, score, _ = match
    return FormNumberMatch(form_number=matched_form_number, score=score)
