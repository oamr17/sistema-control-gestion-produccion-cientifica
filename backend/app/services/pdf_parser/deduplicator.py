from __future__ import annotations

from .normalizer import normalize_key
from .validators import person_key


def dedupe_internals(items: list[dict]) -> list[dict]:
    selected: dict[str, dict] = {}
    for item in items:
        key = "|".join([person_key(item.get("name")), normalize_key(item.get("faculty")), normalize_key(item.get("career"))])
        selected[key] = item
    return list(selected.values())


def dedupe_externals(items: list[dict]) -> list[dict]:
    selected: dict[str, dict] = {}
    for item in items:
        key = "|".join([person_key(item.get("name")), normalize_key(item.get("institution"))])
        selected[key] = item
    return list(selected.values())


def dedupe_projects(items: list[dict]) -> list[dict]:
    selected: dict[str, dict] = {}
    for item in items:
        key = normalize_key(item.get("code") or item.get("name"))
        selected[key] = item
    return list(selected.values())


def dedupe_products(items: list[dict]) -> list[dict]:
    selected: dict[str, dict] = {}
    for item in items:
        key = normalize_key(item.get("doi") or item.get("link") or item.get("title"))
        selected[key] = item
    return list(selected.values())
