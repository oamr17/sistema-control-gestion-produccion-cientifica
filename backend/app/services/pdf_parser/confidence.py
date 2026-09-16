from __future__ import annotations

from difflib import SequenceMatcher

try:
    from rapidfuzz import fuzz
except Exception:  # pragma: no cover - fallback for local envs without rapidfuzz
    fuzz = None

from .normalizer import normalize_key


ACCEPT_THRESHOLD = 90
REVIEW_THRESHOLD = 75


def fuzzy_score(left: object, right: object) -> float:
    left_key = normalize_key(left)
    right_key = normalize_key(right)
    if not left_key or not right_key:
        return 0
    if fuzz:
        return float(fuzz.token_set_ratio(left_key, right_key))
    return SequenceMatcher(None, left_key, right_key).ratio() * 100


def review_status(score: float) -> str:
    if score >= ACCEPT_THRESHOLD:
        return "accepted"
    if score >= REVIEW_THRESHOLD:
        return "requires_review"
    return "discarded"


def best_alias(value: object, aliases: list[str]) -> tuple[str | None, float]:
    scored = [(alias, fuzzy_score(value, alias)) for alias in aliases]
    return max(scored, key=lambda item: item[1], default=(None, 0))
