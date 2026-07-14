from dataclasses import dataclass

from rapidfuzz import fuzz, process

FUZZY_MATCH_THRESHOLD = 80.0


@dataclass
class FormNumberMatch:
    form_number: str | None
    score: float


def best_form_number_match(query: str, candidates: list[str]) -> FormNumberMatch:
    if not candidates:
        return FormNumberMatch(form_number=None, score=0.0)

    match = process.extractOne(query, candidates, scorer=fuzz.WRatio)
    if match is None:
        return FormNumberMatch(form_number=None, score=0.0)

    matched_form_number, score, _ = match
    return FormNumberMatch(form_number=matched_form_number, score=score)
