from __future__ import annotations

from typing import Any
import re
import unicodedata


NAME_PARTICLES = {"DE", "DEL", "LA", "LAS", "LOS", "Y"}


def normalized_name_tokens(value: Any) -> tuple[str, ...]:
    text = unicodedata.normalize("NFKD", str(value or "").strip().upper())
    text = "".join(char for char in text if not unicodedata.combining(char))
    tokens = re.sub(r"[^A-Z0-9]+", " ", text).split()
    return tuple(token for token in tokens if token not in NAME_PARTICLES)


def has_two_matching_surnames(left: Any, right: Any) -> bool:
    left_tokens = normalized_name_tokens(left)
    right_tokens = normalized_name_tokens(right)
    if len(left_tokens) < 3 or len(right_tokens) < 3:
        return False
    return len(set(left_tokens[-2:]).intersection(right_tokens[-2:])) >= 2


def heuristic_name_merge_allowed(left: Any, right: Any) -> bool:
    left_tokens = normalized_name_tokens(left)
    right_tokens = normalized_name_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    if set(left_tokens) == set(right_tokens):
        return True
    return has_two_matching_surnames(left, right)
