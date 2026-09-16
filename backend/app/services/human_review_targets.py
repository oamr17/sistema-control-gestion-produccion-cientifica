from __future__ import annotations

from hashlib import sha256
import json
from typing import Any
import unicodedata

from app.models.entities import (
    ExternalResearcher,
    PersonRole,
    ResearchEntity,
    ScientificProduction,
    ScientificProductionAuthor,
)
from app.models.human_review_enums import ReviewCaseType, ReviewTargetTable
from app.schemas.human_review import StableTargetV1


REVIEW_TARGET_MODELS: dict[ReviewTargetTable, type[Any]] = {
    ReviewTargetTable.PERSON_ROLES: PersonRole,
    ReviewTargetTable.SCIENTIFIC_PRODUCTION_AUTHORS: ScientificProductionAuthor,
    ReviewTargetTable.SCIENTIFIC_PRODUCTIONS: ScientificProduction,
    ReviewTargetTable.RESEARCH_ENTITIES: ResearchEntity,
    ReviewTargetTable.EXTERNAL_RESEARCHERS: ExternalResearcher,
}

REVIEW_CASE_TARGETS: dict[ReviewCaseType, frozenset[ReviewTargetTable]] = {
    ReviewCaseType.PERSON_IDENTITY: frozenset({ReviewTargetTable.PERSON_ROLES}),
    ReviewCaseType.AUTHOR_IDENTITY: frozenset({
        ReviewTargetTable.SCIENTIFIC_PRODUCTION_AUTHORS,
    }),
    ReviewCaseType.PRODUCT: frozenset({ReviewTargetTable.SCIENTIFIC_PRODUCTIONS}),
    ReviewCaseType.PROJECT_DIRECTOR_RELATION: frozenset({
        ReviewTargetTable.RESEARCH_ENTITIES,
    }),
    ReviewCaseType.EXTERNAL_IDENTITY: frozenset({
        ReviewTargetTable.EXTERNAL_RESEARCHERS,
    }),
    ReviewCaseType.POSSIBLE_DUPLICATE: frozenset({
        ReviewTargetTable.PERSON_ROLES,
        ReviewTargetTable.SCIENTIFIC_PRODUCTION_AUTHORS,
    }),
}


def normalize_person_alias(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    characters: list[str] = []
    pending_boundary = False
    for character in normalized:
        if unicodedata.category(character)[0] in {"L", "M", "N"}:
            if pending_boundary and characters:
                characters.append(" ")
            characters.append(character)
            pending_boundary = False
        else:
            pending_boundary = True
    alias = "".join(characters)
    if not alias:
        raise ValueError("normalized alias must not be empty")
    return alias


def raw_value_sha256(value: str | None) -> str:
    return sha256((value or "").encode("utf-8")).hexdigest()


def build_stable_target_key(target: StableTargetV1) -> str:
    material = target.model_dump(mode="json", exclude={"target_pk"})
    canonical_json = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = sha256(canonical_json).hexdigest()
    return f"b2b:v1:{target.case_type.value}:{digest}"
