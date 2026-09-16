from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.entities import (
    ExternalResearcher,
    PersonRole,
    ResearchEntity,
    ScientificProduction,
    ScientificProductionAuthor,
)
from app.models.human_review_core import ReviewItem
from app.models.human_review_enums import (
    ReviewCaseStatus,
    ReviewCaseType,
    ReviewTargetTable,
)
from app.schemas.human_review_api import CounterpartOption, CounterpartReference
from app.services.human_review_state import IncompatibleDecisionError
from app.services.validated_read_service import ValidatedReadService


TARGET_MODELS: dict[ReviewTargetTable, type[Any]] = {
    ReviewTargetTable.PERSON_ROLES: PersonRole,
    ReviewTargetTable.SCIENTIFIC_PRODUCTION_AUTHORS: ScientificProductionAuthor,
    ReviewTargetTable.SCIENTIFIC_PRODUCTIONS: ScientificProduction,
    ReviewTargetTable.RESEARCH_ENTITIES: ResearchEntity,
    ReviewTargetTable.EXTERNAL_RESEARCHERS: ExternalResearcher,
}

MAX_PUBLIC_COUNTERPART_OPTIONS = 100


def _positive_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _public_document_name(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    name = PurePosixPath(value.replace("\\", "/")).name.strip()
    return name or None


def _source_ref(variant: Mapping[str, object]) -> CounterpartReference | None:
    try:
        target_type = ReviewTargetTable(str(variant.get("source_table")))
    except ValueError:
        return None
    target_id = _positive_int(variant.get("source_id"))
    if target_id is None:
        return None
    return CounterpartReference(target_type=target_type, target_id=target_id)


def _duplicate_item_for_ref(
    db: Session,
    counterpart_ref: CounterpartReference,
) -> ReviewItem | None:
    model = TARGET_MODELS[counterpart_ref.target_type]
    if db.get(model, counterpart_ref.target_id) is None:
        return None
    rows = tuple(db.scalars(
        select(ReviewItem).where(
            ReviewItem.case_type == ReviewCaseType.POSSIBLE_DUPLICATE.value,
            ReviewItem.target_table == counterpart_ref.target_type.value,
            ReviewItem.target_pk == counterpart_ref.target_id,
            ReviewItem.case_status != ReviewCaseStatus.SUPERSEDED.value,
        ).limit(2)
    ))
    return rows[0] if len(rows) == 1 else None


def _duplicate_items_for_refs(
    db: Session,
    counterpart_refs: set[tuple[ReviewTargetTable, int]],
) -> dict[tuple[ReviewTargetTable, int], ReviewItem]:
    """Resolve existing targets and active duplicate items in bounded batches."""

    refs_by_table: dict[ReviewTargetTable, set[int]] = {}
    for target_type, target_id in counterpart_refs:
        refs_by_table.setdefault(target_type, set()).add(target_id)

    existing_refs: set[tuple[ReviewTargetTable, int]] = set()
    for target_type in sorted(refs_by_table, key=lambda value: value.value):
        model = TARGET_MODELS[target_type]
        target_ids = refs_by_table[target_type]
        existing_ids = db.scalars(select(model.id).where(model.id.in_(target_ids)))
        existing_refs.update((target_type, target_id) for target_id in existing_ids)
    if not existing_refs:
        return {}

    item_filters = [
        and_(
            ReviewItem.target_table == target_type.value,
            ReviewItem.target_pk.in_(
                target_id
                for candidate_type, target_id in existing_refs
                if candidate_type == target_type
            ),
        )
        for target_type in sorted(
            {target_type for target_type, _ in existing_refs},
            key=lambda value: value.value,
        )
    ]
    rows = db.scalars(
        select(ReviewItem).where(
            ReviewItem.case_type == ReviewCaseType.POSSIBLE_DUPLICATE.value,
            ReviewItem.case_status != ReviewCaseStatus.SUPERSEDED.value,
            or_(*item_filters),
        )
    )
    grouped: dict[tuple[ReviewTargetTable, int], list[ReviewItem]] = {}
    for row in rows:
        try:
            key = (ReviewTargetTable(row.target_table), row.target_pk)
        except ValueError:
            continue
        if key in existing_refs:
            grouped.setdefault(key, []).append(row)
    return {
        key: candidates[0]
        for key, candidates in grouped.items()
        if len(candidates) == 1
    }


def public_counterpart_options(
    db: Session,
    item: ReviewItem,
) -> tuple[CounterpartOption, ...]:
    """Derive current duplicate choices from persisted operational readers."""

    if (
        item.case_type != ReviewCaseType.POSSIBLE_DUPLICATE.value
        or item.target_pk is None
    ):
        return ()
    participants = ValidatedReadService(db).canonical_participants()
    owners = [
        participant
        for participant in participants
        if isinstance(participant, Mapping)
        and any(
            isinstance(variant, Mapping)
            and variant.get("source_table") == item.target_table
            and variant.get("source_id") == item.target_pk
            for variant in participant.get("variants", ())
        )
    ]
    if len(owners) != 1:
        return ()

    participants_by_identity = {
        participant.get("canonical_identity_key"): participant
        for participant in participants
        if isinstance(participant, Mapping)
        and isinstance(participant.get("canonical_identity_key"), str)
    }
    candidate_options: dict[tuple[ReviewTargetTable, int], CounterpartOption] = {}
    possible_matches = owners[0].get("possible_matches", ())
    if not isinstance(possible_matches, (tuple, list)):
        return ()
    for match in possible_matches:
        if not isinstance(match, Mapping):
            continue
        counterpart_identity_key = match.get("canonical_identity_key")
        counterpart_participant = participants_by_identity.get(counterpart_identity_key)
        if not isinstance(counterpart_participant, Mapping):
            continue
        display_name = counterpart_participant.get("canonical_name")
        if not isinstance(display_name, str) or not display_name.strip():
            continue
        variants = counterpart_participant.get("variants", ())
        if not isinstance(variants, (tuple, list)):
            continue
        for variant in variants:
            if not isinstance(variant, Mapping):
                continue
            counterpart_ref = _source_ref(variant)
            if counterpart_ref is None or (
                counterpart_ref.target_type.value == item.target_table
                and counterpart_ref.target_id == item.target_pk
            ):
                continue
            source_label = variant.get("raw_name") or variant.get("normalized_name")
            document_name = _public_document_name(
                variant.get("document") or match.get("document")
            )
            option = CounterpartOption(
                counterpart_ref=counterpart_ref,
                display_name=display_name,
                source_label=(source_label if isinstance(source_label, str) else None),
                document_name=document_name,
            )
            candidate_options[(counterpart_ref.target_type, counterpart_ref.target_id)] = option
    counterparts = _duplicate_items_for_refs(db, set(candidate_options))
    eligible_keys = [
        key
        for key, counterpart in counterparts.items()
        if counterpart.id != item.id
    ]
    return tuple(
        candidate_options[key]
        for key in sorted(eligible_keys, key=lambda value: (value[0].value, value[1]))[
            :MAX_PUBLIC_COUNTERPART_OPTIONS
        ]
    )


def resolve_duplicate_counterpart(
    db: Session,
    item: ReviewItem,
    counterpart_ref: CounterpartReference,
    correlation_id: UUID,
) -> ReviewItem:
    allowed = {
        (option.counterpart_ref.target_type, option.counterpart_ref.target_id)
        for option in public_counterpart_options(db, item)
    }
    selected = (counterpart_ref.target_type, counterpart_ref.target_id)
    if selected not in allowed:
        raise IncompatibleDecisionError(
            "Duplicate counterpart is not valid for this case",
            correlation_id=correlation_id,
        )
    counterpart = _duplicate_item_for_ref(db, counterpart_ref)
    if counterpart is None or counterpart.stable_target_key == item.stable_target_key:
        raise IncompatibleDecisionError(
            "Duplicate counterpart is missing, ambiguous, or self-referential",
            correlation_id=correlation_id,
        )
    return counterpart
