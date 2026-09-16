from __future__ import annotations

from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import (
    ExternalResearcher,
    PersonRole,
    ResearchEntity,
    ScientificProduction,
    ScientificProductionAuthor,
    Teacher,
)
from app.models.human_review_core import ReviewItem
from app.models.human_review_enums import (
    CanonicalIdentityStatus,
    ReviewCaseStatus,
    ReviewCaseType,
    ReviewTargetTable,
)
from app.models.human_review_projection import CanonicalIdentity
from app.services.human_review_projection import EffectiveHumanProjectionSource
from app.services.human_review_targets import (
    REVIEW_CASE_TARGETS as _CASE_TARGETS,
    REVIEW_TARGET_MODELS as _TARGET_MODELS,
)


@dataclass(frozen=True, slots=True)
class RelatedEntityRef:
    public_type: Literal[
        "person",
        "scientific_product",
        "research_entity",
        "external_researcher",
    ]
    public_id: str
    display_name: str
    match_kind: Literal[
        "canonical_identity",
        "teacher",
        "external_researcher",
        "record",
    ]
    match_id: UUID | int


@dataclass(frozen=True, slots=True)
class TargetRef:
    target_table: ReviewTargetTable
    target_pk: int


class RelatedEntityOutOfScopeError(ValueError):
    """The persisted anchor is outside the approved related-entity matrix."""


_PERSON_TARGETS = frozenset({
    ReviewTargetTable.PERSON_ROLES,
    ReviewTargetTable.SCIENTIFIC_PRODUCTION_AUTHORS,
})
_CANDIDATE_BATCH_SIZE = 32

_RECORD_PUBLIC_TYPES: dict[ReviewTargetTable, Literal[
    "person",
    "scientific_product",
    "research_entity",
    "external_researcher",
]] = {
    ReviewTargetTable.PERSON_ROLES: "person",
    ReviewTargetTable.SCIENTIFIC_PRODUCTION_AUTHORS: "person",
    ReviewTargetTable.SCIENTIFIC_PRODUCTIONS: "scientific_product",
    ReviewTargetTable.RESEARCH_ENTITIES: "research_entity",
    ReviewTargetTable.EXTERNAL_RESEARCHERS: "external_researcher",
}


def _nonblank(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def _active_identity_for_key(
    db: Session,
    canonical_identity_key: object,
) -> CanonicalIdentity | None:
    key = _nonblank(canonical_identity_key)
    if key is None:
        return None
    with db.no_autoflush:
        identity = db.scalar(select(CanonicalIdentity).where(
            CanonicalIdentity.canonical_identity_key == key,
            CanonicalIdentity.status == CanonicalIdentityStatus.ACTIVE.value,
        ))
    if identity is None or _nonblank(identity.display_name) is None:
        return None
    return identity


def _canonical_ref(identity: CanonicalIdentity) -> RelatedEntityRef:
    return RelatedEntityRef(
        public_type="person",
        public_id=str(identity.id),
        display_name=identity.display_name,
        match_kind="canonical_identity",
        match_id=identity.id,
    )


def _teacher_ref(db: Session, teacher_id: object) -> RelatedEntityRef | None:
    if not isinstance(teacher_id, int) or isinstance(teacher_id, bool):
        return None
    with db.no_autoflush:
        teacher = db.get(Teacher, teacher_id)
    if teacher is None or _nonblank(teacher.full_name) is None:
        return None
    return RelatedEntityRef(
        public_type="person",
        public_id=str(teacher.id),
        display_name=teacher.full_name,
        match_kind="teacher",
        match_id=teacher.id,
    )


def _external_ref(
    db: Session,
    external_researcher_id: object,
) -> RelatedEntityRef | None:
    if (
        not isinstance(external_researcher_id, int)
        or isinstance(external_researcher_id, bool)
    ):
        return None
    with db.no_autoflush:
        researcher = db.get(ExternalResearcher, external_researcher_id)
    if researcher is None or _nonblank(researcher.full_name) is None:
        return None
    return RelatedEntityRef(
        public_type="external_researcher",
        public_id=str(researcher.id),
        display_name=researcher.full_name,
        match_kind="external_researcher",
        match_id=researcher.id,
    )


def _record_display_name(target_table: ReviewTargetTable, target: Any) -> str:
    candidates: tuple[object, ...]
    if target_table is ReviewTargetTable.PERSON_ROLES:
        candidates = (target.canonical_name, target.raw_name, target.normalized_name)
    elif target_table is ReviewTargetTable.SCIENTIFIC_PRODUCTION_AUTHORS:
        candidates = (
            target.canonical_name,
            target.raw_author_name,
            target.normalized_author_name,
        )
    elif target_table is ReviewTargetTable.SCIENTIFIC_PRODUCTIONS:
        candidates = (target.title,)
    elif target_table is ReviewTargetTable.RESEARCH_ENTITIES:
        candidates = (target.name, target.code)
    else:
        candidates = (target.full_name,)
    return next(
        (value for value in (_nonblank(candidate) for candidate in candidates) if value),
        f"{target_table.value}:{target.id}",
    )


def _record_ref(target_table: ReviewTargetTable, target: Any) -> RelatedEntityRef:
    public_id = (
        f"{target_table.value}:{target.id}"
        if target_table in _PERSON_TARGETS
        else str(target.id)
    )
    return RelatedEntityRef(
        public_type=_RECORD_PUBLIC_TYPES[target_table],
        public_id=public_id,
        display_name=_record_display_name(target_table, target),
        match_kind="record",
        match_id=target.id,
    )


def _resolve_person_target(
    db: Session,
    target_table: ReviewTargetTable,
    target_pk: int,
    projection_source: EffectiveHumanProjectionSource,
) -> RelatedEntityRef | None:
    with db.no_autoflush:
        target = db.get(_TARGET_MODELS[target_table], target_pk)
    if target is None:
        return None

    projection = projection_source.for_record(target_table.value, target_pk)
    if projection is not None:
        identity = _active_identity_for_key(
            db,
            projection.canonical_identity_key,
        )
        if identity is not None:
            return _canonical_ref(identity)

    identity = _active_identity_for_key(db, target.canonical_identity_key)
    if identity is not None:
        return _canonical_ref(identity)

    teacher = _teacher_ref(db, target.teacher_id)
    if teacher is not None:
        return teacher

    external = _external_ref(db, target.external_researcher_id)
    if external is not None:
        return external

    return _record_ref(target_table, target)


def resolve_related_entity(db: Session, item: ReviewItem) -> RelatedEntityRef:
    with db.no_autoflush:
        if not isinstance(item.target_pk, int) or isinstance(item.target_pk, bool):
            raise ValueError("review item target_pk must be an integer")
        try:
            case_type = ReviewCaseType(item.case_type)
            target_table = ReviewTargetTable(item.target_table)
        except ValueError as error:
            raise RelatedEntityOutOfScopeError(
                "review item case or target table is unsupported"
            ) from error
        if target_table not in _CASE_TARGETS.get(case_type, frozenset()):
            raise RelatedEntityOutOfScopeError(
                "review item case and target table are incompatible"
            )

        if target_table in _PERSON_TARGETS:
            entity = _resolve_person_target(
                db,
                target_table,
                item.target_pk,
                EffectiveHumanProjectionSource(db),
            )
            if entity is None:
                raise LookupError("review item target record does not exist")
            return entity

        target = db.get(_TARGET_MODELS[target_table], item.target_pk)
        if target is None:
            raise LookupError("review item target record does not exist")
        return _record_ref(target_table, target)


def _raw_person_target_id_page(
    db: Session,
    target_table: ReviewTargetTable,
    column_name: Literal[
        "canonical_identity_key",
        "teacher_id",
        "external_researcher_id",
    ],
    value: str | int,
    after_pk: int | None,
) -> tuple[int, ...]:
    model = _TARGET_MODELS[target_table]
    statement = select(model.id).where(getattr(model, column_name) == value)
    if after_pk is not None:
        statement = statement.where(model.id > after_pk)
    with db.no_autoflush:
        return tuple(db.scalars(
            statement.order_by(model.id).limit(_CANDIDATE_BATCH_SIZE)
        ))


def _projected_person_target_id_page(
    db: Session,
    target_table: ReviewTargetTable,
    after_pk: int | None,
) -> tuple[int, ...]:
    statement = select(ReviewItem.target_pk).where(
        ReviewItem.target_table == target_table.value,
        ReviewItem.target_pk.is_not(None),
        ReviewItem.current_decision_id.is_not(None),
        ReviewItem.case_status != ReviewCaseStatus.SUPERSEDED.value,
    )
    if after_pk is not None:
        statement = statement.where(ReviewItem.target_pk > after_pk)
    with db.no_autoflush:
        return tuple(db.scalars(
            statement.distinct()
            .order_by(ReviewItem.target_pk)
            .limit(_CANDIDATE_BATCH_SIZE)
        ))


def _ordered_person_target_candidates(
    db: Session,
    target_table: ReviewTargetTable,
    column_name: Literal[
        "canonical_identity_key",
        "teacher_id",
        "external_researcher_id",
    ],
    value: str | int,
    *,
    include_projected: bool,
) -> Iterator[TargetRef]:
    raw_ids: deque[int] = deque()
    projected_ids: deque[int] = deque()
    raw_after: int | None = None
    projected_after: int | None = None
    raw_done = False
    projected_done = not include_projected

    while True:
        if not raw_ids and not raw_done:
            page = _raw_person_target_id_page(
                db,
                target_table,
                column_name,
                value,
                raw_after,
            )
            raw_ids.extend(page)
            raw_done = len(page) < _CANDIDATE_BATCH_SIZE
            if page:
                raw_after = page[-1]
        if not projected_ids and not projected_done:
            page = _projected_person_target_id_page(
                db,
                target_table,
                projected_after,
            )
            projected_ids.extend(page)
            projected_done = len(page) < _CANDIDATE_BATCH_SIZE
            if page:
                projected_after = page[-1]
        if not raw_ids and not projected_ids:
            return
        if not projected_ids or raw_ids and raw_ids[0] < projected_ids[0]:
            target_pk = raw_ids.popleft()
        elif not raw_ids or projected_ids[0] < raw_ids[0]:
            target_pk = projected_ids.popleft()
        else:
            target_pk = raw_ids.popleft()
            projected_ids.popleft()
        yield TargetRef(target_table, target_pk)


def _bounded_effective_person_matches(
    db: Session,
    entity: RelatedEntityRef,
    column_name: Literal[
        "canonical_identity_key",
        "teacher_id",
        "external_researcher_id",
    ],
    value: str | int,
    limit: int,
    *,
    include_projected: bool,
) -> tuple[TargetRef, ...]:
    matches: list[TargetRef] = []
    for target_table in sorted(_PERSON_TARGETS, key=lambda table: table.value):
        candidates = _ordered_person_target_candidates(
            db,
            target_table,
            column_name,
            value,
            include_projected=include_projected,
        )
        while len(matches) < limit:
            batch: list[TargetRef] = []
            for _index in range(_CANDIDATE_BATCH_SIZE):
                try:
                    batch.append(next(candidates))
                except StopIteration:
                    break
            if not batch:
                break
            source = EffectiveHumanProjectionSource(db)
            with source.read_scope():
                source.prefetch_records({
                    (candidate.target_table.value, candidate.target_pk)
                    for candidate in batch
                })
                for candidate in batch:
                    resolved = _resolve_person_target(
                        db,
                        candidate.target_table,
                        candidate.target_pk,
                        source,
                    )
                    if (
                        resolved is not None
                        and resolved.match_kind == entity.match_kind
                        and resolved.match_id == entity.match_id
                    ):
                        matches.append(candidate)
                        if len(matches) == limit:
                            return tuple(matches)
    return tuple(matches)


def _record_target_table(entity: RelatedEntityRef) -> ReviewTargetTable | None:
    if entity.public_type == "person":
        prefix, separator, value = entity.public_id.partition(":")
        if not separator or value != str(entity.match_id):
            return None
        try:
            target_table = ReviewTargetTable(prefix)
        except ValueError:
            return None
        return target_table if target_table in _PERSON_TARGETS else None
    return {
        "scientific_product": ReviewTargetTable.SCIENTIFIC_PRODUCTIONS,
        "research_entity": ReviewTargetTable.RESEARCH_ENTITIES,
        "external_researcher": ReviewTargetTable.EXTERNAL_RESEARCHERS,
    }.get(entity.public_type)


def find_related_target_refs(
    db: Session,
    entity: RelatedEntityRef,
    limit: int,
) -> tuple[TargetRef, ...]:
    with db.no_autoflush:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit must be a positive integer")

        refs: list[TargetRef] = []
        if entity.match_kind == "canonical_identity" and isinstance(entity.match_id, UUID):
            identity = db.get(CanonicalIdentity, entity.match_id)
            if (
                identity is not None
                and identity.status == CanonicalIdentityStatus.ACTIVE.value
                and _nonblank(identity.display_name) is not None
                and entity.public_id == str(identity.id)
            ):
                refs.extend(_bounded_effective_person_matches(
                    db,
                    entity,
                    "canonical_identity_key",
                    identity.canonical_identity_key,
                    limit,
                    include_projected=True,
                ))
        elif entity.match_kind == "teacher" and isinstance(entity.match_id, int):
            teacher = db.get(Teacher, entity.match_id)
            if teacher is not None and entity.public_id == str(teacher.id):
                refs.extend(_bounded_effective_person_matches(
                    db,
                    entity,
                    "teacher_id",
                    teacher.id,
                    limit,
                    include_projected=False,
                ))
        elif (
            entity.match_kind == "external_researcher"
            and isinstance(entity.match_id, int)
        ):
            researcher = db.get(ExternalResearcher, entity.match_id)
            if researcher is not None and entity.public_id == str(researcher.id):
                refs.append(TargetRef(
                    ReviewTargetTable.EXTERNAL_RESEARCHERS,
                    researcher.id,
                ))
                if len(refs) < limit:
                    refs.extend(_bounded_effective_person_matches(
                        db,
                        entity,
                        "external_researcher_id",
                        researcher.id,
                        limit - len(refs),
                        include_projected=False,
                    ))
        elif entity.match_kind == "record" and isinstance(entity.match_id, int):
            target_table = _record_target_table(entity)
            if (
                target_table is not None
                and db.get(_TARGET_MODELS[target_table], entity.match_id) is not None
            ):
                refs.append(TargetRef(target_table, entity.match_id))

        return tuple(refs[:limit])
