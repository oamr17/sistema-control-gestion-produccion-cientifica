from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from types import MappingProxyType
from typing import Mapping
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import and_, func, inspect, or_, select, tuple_, update
from sqlalchemy.orm import Session

from app.models.entities import User
from app.models.human_review_core import ReviewDecision, ReviewItem
from app.models.human_review_enums import (
    AuditEventType,
    B2BAction,
    B2BCapability,
    CanonicalIdentityOrigin,
    CanonicalIdentityStatus,
    DecisionLifecycle,
    PersonAliasClass,
    PersonAliasScope,
    PersonAliasStatus,
    ReviewActorType,
    ReviewCaseStatus,
    ReviewDecisionType,
    ScientificStatus,
)
from app.models.human_review_projection import (
    CanonicalIdentity,
    FieldOverride,
    PersonAlias,
)
from app.schemas.human_review import (
    DecisionPayloadV1,
    DecisionReversalPayloadV1,
    FieldOverridePayloadV1,
    IdentityDecisionPayloadV1,
    IdentityMergePayloadV1,
    IdentitySeparationPayloadV1,
    MaintainSeparatePayloadV1,
    ProjectionOverrideSnapshotV1,
    ReviewProjectionSnapshotV1,
    ScalarOverrideValueV1,
)
from app.schemas.human_review_operations import (
    AuditEventCommandV1,
    FunctionalReversalAuditPayloadV1,
    FunctionalReversalCommandV1,
    HumanIdentityProjectionV1,
    OptimisticLockError,
)
from app.services.human_review_audit import append_audit_event_at_current_head
from app.services.human_review_authorization import authorize_b2b_action
from app.services.human_review_scope import assert_review_item_scope, review_scope_predicate
from app.services.human_review_state import (
    assert_case_transition,
    assert_expected_version,
)


_STABLE_TARGET_PATTERN = re.compile(r"^b2b:v1:[a-z_]+:[0-9a-f]{64}$")
_DECISION_PAYLOAD_ADAPTER = TypeAdapter(DecisionPayloadV1)
_AUDIT_AGGREGATE_TYPE = "review_item"
_DECISION_PAYLOAD_SCHEMA = "review.decision.v1"
_OVERRIDE_VALUE_SCHEMA = "override.scalar.v1"
_DECISION_PAYLOAD_VERSION = 1
_PREFETCH_OVERRIDE_LIMIT_PER_STABLE_KEY = 16

_FIELDS_BY_TARGET = {
    "person_roles": frozenset({
        "canonical_identity_key",
        "canonical_name",
        "scientific_status",
    }),
    "scientific_production_authors": frozenset({
        "author_identity_key",
        "canonical_name",
        "scientific_status",
    }),
    "scientific_productions": frozenset({
        "product_title",
        "scientific_status",
    }),
    "research_entities": frozenset({
        "project_director_identity_key",
        "project_director_relationship_status",
        "scientific_status",
    }),
    "external_researchers": frozenset({
        "external_identity_key",
        "external_institution",
        "scientific_status",
    }),
}
_IDENTITY_TARGETS_BY_CASE = {
    "person_identity": frozenset({"person_roles"}),
    "author_identity": frozenset({"scientific_production_authors"}),
    "external_identity": frozenset({"external_researchers"}),
    "possible_duplicate": frozenset({
        "person_roles",
        "scientific_production_authors",
    }),
}
_TARGETS_BY_CASE = {
    "person_identity": frozenset({"person_roles"}),
    "author_identity": frozenset({"scientific_production_authors"}),
    "product": frozenset({"scientific_productions"}),
    "project_director_relation": frozenset({"research_entities"}),
    "external_identity": frozenset({"external_researchers"}),
    "possible_duplicate": frozenset({
        "person_roles",
        "scientific_production_authors",
    }),
}
_DECISION_TYPES_BY_CASE = {
    "person_identity": frozenset({
        "validated", "corrected", "linked", "maintained_separate",
        "separated", "discarded", "reverted",
    }),
    "author_identity": frozenset({
        "validated", "corrected", "linked", "maintained_separate",
        "separated", "discarded", "reverted",
    }),
    "product": frozenset({"validated", "corrected", "discarded", "reverted"}),
    "project_director_relation": frozenset({
        "validated", "corrected", "linked", "maintained_separate",
        "separated", "discarded", "reverted",
    }),
    "external_identity": frozenset({
        "validated", "corrected", "linked", "maintained_separate",
        "separated", "discarded", "reverted",
    }),
    "possible_duplicate": frozenset({
        "merged", "maintained_separate", "separated", "discarded", "reverted",
    }),
}


@dataclass(frozen=True)
class EffectiveHumanProjection:
    stable_target_key: str
    target_table: str
    target_pk: int
    case_type: str
    decision_id: UUID
    scientific_status: str
    canonical_identity_key: str | None
    canonical_name: str | None
    identity_type: str | None
    aliases: tuple[str, ...]
    overrides: Mapping[str, object]

    def value(self, field_path: str, fallback: object = None) -> object:
        return self.overrides.get(field_path, fallback)


def _scalar_value(value: ScalarOverrideValueV1) -> object:
    return {
        "string": value.string_value,
        "integer": value.integer_value,
        "decimal": value.decimal_value,
        "boolean": value.boolean_value,
        "null": None,
    }[value.kind]


def _payload_matches_case_and_decision(
    item: ReviewItem,
    decision: ReviewDecision,
    payload: DecisionPayloadV1,
) -> bool:
    if decision.decision_type == ReviewDecisionType.REVERTED.value:
        return isinstance(payload, DecisionReversalPayloadV1)
    if item.case_type == "possible_duplicate":
        expected = {
            ReviewDecisionType.MERGED.value: IdentityMergePayloadV1,
            ReviewDecisionType.MAINTAINED_SEPARATE.value: MaintainSeparatePayloadV1,
            ReviewDecisionType.SEPARATED.value: IdentitySeparationPayloadV1,
            ReviewDecisionType.DISCARDED.value: FieldOverridePayloadV1,
        }.get(decision.decision_type)
        return expected is not None and isinstance(payload, expected)
    if item.case_type in {
        "person_identity", "author_identity", "external_identity",
    }:
        expected = (
            FieldOverridePayloadV1
            if decision.decision_type == ReviewDecisionType.DISCARDED.value
            else IdentityDecisionPayloadV1
        )
        return isinstance(payload, expected)
    return isinstance(payload, FieldOverridePayloadV1)


def _payload_matches_projection(
    item: ReviewItem,
    payload: DecisionPayloadV1,
    snapshot: ReviewProjectionSnapshotV1,
) -> bool:
    identity = snapshot.identity
    if isinstance(payload, IdentityDecisionPayloadV1):
        return (
            identity is not None
            and payload.canonical_identity_key == identity.canonical_identity_key
            and payload.canonical_name == identity.canonical_name
            and payload.identity_type is identity.identity_type
        )
    if isinstance(payload, IdentityMergePayloadV1):
        return (
            identity is not None
            and item.stable_target_key in payload.member_stable_target_keys
            and payload.target_identity_key == identity.canonical_identity_key
        )
    if isinstance(payload, IdentitySeparationPayloadV1):
        assignment = next(
            (
                row
                for row in payload.assignments
                if row.stable_target_key == item.stable_target_key
            ),
            None,
        )
        return (
            identity is not None
            and assignment is not None
            and assignment.target_identity_key == identity.canonical_identity_key
            and assignment.target_canonical_name == identity.canonical_name
        )
    if isinstance(payload, MaintainSeparatePayloadV1):
        pairs = dict(zip(payload.stable_target_keys, payload.identity_keys))
        return (
            identity is not None
            and pairs.get(item.stable_target_key) == identity.canonical_identity_key
        )
    if isinstance(payload, FieldOverridePayloadV1):
        return any(
            override.field_path is payload.field_path
            and override.scope is payload.scope
            and override.projected_value == payload.value
            and override.stable_target_key == (
                payload.stable_target_key or item.stable_target_key
            )
            and override.document_key == payload.document_key
            and override.period_id == payload.period_id
            and override.relationship_key == payload.relationship_key
            for override in snapshot.overrides
        )
    return isinstance(payload, DecisionReversalPayloadV1)


class EffectiveHumanProjectionSource:
    """Read-only, fail-closed access to the current committed human projection."""

    def __init__(self, db: Session):
        self.db = db
        self._read_scope_depth = 0
        self._record_cache: dict[
            tuple[str, int], EffectiveHumanProjection | None
        ] = {}
        self._stable_key_cache: dict[
            str, EffectiveHumanProjection | None
        ] = {}
        self._decision_cache: dict[UUID, ReviewDecision | None] = {}
        self._override_cache: dict[str, tuple[FieldOverride, ...]] = {}
        self._cache_only_decision_reads = False
        bind = db.get_bind()
        self._available = not (
            bind.dialect.name == "sqlite"
            and not inspect(db.connection()).has_table(ReviewItem.__tablename__)
        )

    @contextmanager
    def read_scope(self):
        """Memoize projection lookups only for one deterministic read operation."""
        outermost = self._read_scope_depth == 0
        if outermost:
            self._record_cache.clear()
            self._stable_key_cache.clear()
            self._decision_cache.clear()
            self._override_cache.clear()
        self._read_scope_depth += 1
        try:
            yield
        finally:
            self._read_scope_depth -= 1
            if outermost:
                self._record_cache.clear()
                self._stable_key_cache.clear()
                self._decision_cache.clear()
                self._override_cache.clear()

    def prefetch_records(
        self,
        records: set[tuple[str, int]],
    ) -> None:
        """Load all projection heads/materializations for a reader batch."""
        if not self._available or self._read_scope_depth <= 0:
            return
        pending = {
            key
            for key in records
            if key[0] in _FIELDS_BY_TARGET
            and isinstance(key[1], int)
            and key not in self._record_cache
        }
        if not pending:
            return
        ranked_ids = (
            select(
                ReviewItem.id.label("review_item_id"),
                func.row_number().over(
                    partition_by=(
                        ReviewItem.target_table,
                        ReviewItem.target_pk,
                    ),
                    order_by=ReviewItem.id,
                ).label("target_row_number"),
            )
            .where(
                tuple_(
                    ReviewItem.target_table,
                    ReviewItem.target_pk,
                ).in_(sorted(pending)),
                ReviewItem.case_status != ReviewCaseStatus.SUPERSEDED.value,
                ReviewItem.current_decision_id.is_not(None),
            )
            .subquery()
        )
        with self.db.no_autoflush:
            rows = tuple(self.db.scalars(
                select(ReviewItem)
                .join(
                    ranked_ids,
                    ReviewItem.id == ranked_ids.c.review_item_id,
                )
                .where(ranked_ids.c.target_row_number <= 2)
            ))
        grouped: dict[tuple[str, int], list[ReviewItem]] = {}
        for item in rows:
            if isinstance(item.target_pk, int):
                grouped.setdefault(
                    (item.target_table, item.target_pk), []
                ).append(item)
        unique_items = tuple(
            values[0] for values in grouped.values() if len(values) == 1
        )
        decision_ids = {
            item.current_decision_id
            for item in unique_items
            if item.current_decision_id is not None
        }
        with self.db.no_autoflush:
            decisions = tuple(self.db.scalars(
                select(ReviewDecision).where(
                    ReviewDecision.id.in_(decision_ids)
                )
            )) if decision_ids else ()
        self._decision_cache.update(
            {decision.id: decision for decision in decisions}
        )
        for decision_id in decision_ids:
            self._decision_cache.setdefault(decision_id, None)
        restore_ids: set[UUID] = set()
        for decision in decisions:
            try:
                payload = _parse_decision_payload(decision)
            except (TypeError, ValueError):
                continue
            if (
                isinstance(payload, DecisionReversalPayloadV1)
                and payload.restore_decision_id is not None
            ):
                restore_ids.add(payload.restore_decision_id)
        missing_restore_ids = restore_ids.difference(self._decision_cache)
        if missing_restore_ids:
            with self.db.no_autoflush:
                restores = tuple(self.db.scalars(
                    select(ReviewDecision).where(
                        ReviewDecision.id.in_(missing_restore_ids)
                    )
                ))
            self._decision_cache.update(
                {decision.id: decision for decision in restores}
            )
        stable_keys = {item.stable_target_key for item in unique_items}
        with self.db.no_autoflush:
            override_rows = tuple(self.db.scalars(
                select(FieldOverride).where(
                    FieldOverride.stable_target_key.in_(stable_keys),
                    FieldOverride.is_active.is_(True),
                )
            )) if stable_keys else ()
        overrides_by_key: dict[str, list[FieldOverride]] = {
            key: [] for key in stable_keys
        }
        for row in override_rows:
            overrides_by_key.setdefault(row.stable_target_key, []).append(row)
        self._override_cache.update(
            {
                key: tuple(values)
                for key, values in overrides_by_key.items()
            }
        )
        for key in pending:
            candidates = grouped.get(key, [])
            projection = (
                self._effective(candidates[0])
                if len(candidates) == 1
                else None
            )
            self._record_cache[key] = projection
            if len(candidates) == 1:
                self._stable_key_cache[
                    candidates[0].stable_target_key
                ] = projection

    def prefetch_stable_target_keys(self, stable_target_keys: set[str]) -> None:
        """Load projection heads/materializations for a bounded stable-key batch."""

        if not self._available or self._read_scope_depth <= 0:
            return
        pending = {
            key
            for key in stable_target_keys
            if isinstance(key, str)
            and len(key) <= 128
            and _STABLE_TARGET_PATTERN.fullmatch(key) is not None
            and key not in self._stable_key_cache
        }
        if not pending:
            return
        ranked_ids = (
            select(
                ReviewItem.id.label("review_item_id"),
                func.row_number().over(
                    partition_by=ReviewItem.stable_target_key,
                    order_by=ReviewItem.id,
                ).label("stable_key_row_number"),
            )
            .where(
                ReviewItem.stable_target_key.in_(pending),
                ReviewItem.case_status != ReviewCaseStatus.SUPERSEDED.value,
                ReviewItem.current_decision_id.is_not(None),
            )
            .subquery()
        )
        with self.db.no_autoflush:
            rows = tuple(self.db.scalars(
                select(ReviewItem)
                .join(
                    ranked_ids,
                    ReviewItem.id == ranked_ids.c.review_item_id,
                )
                .where(ranked_ids.c.stable_key_row_number <= 2)
            ))
        grouped: dict[str, list[ReviewItem]] = {}
        for item in rows:
            grouped.setdefault(item.stable_target_key, []).append(item)
        unique_items = tuple(
            values[0] for values in grouped.values() if len(values) == 1
        )
        decision_ids = {
            item.current_decision_id
            for item in unique_items
            if item.current_decision_id is not None
        }
        with self.db.no_autoflush:
            decisions = tuple(self.db.scalars(
                select(ReviewDecision).where(
                    ReviewDecision.id.in_(decision_ids)
                )
            )) if decision_ids else ()
        self._decision_cache.update(
            {decision.id: decision for decision in decisions}
        )
        for decision_id in decision_ids:
            self._decision_cache.setdefault(decision_id, None)
        restore_ids: set[UUID] = set()
        for decision in decisions:
            try:
                payload = _parse_decision_payload(decision)
            except (TypeError, ValueError):
                continue
            if (
                isinstance(payload, DecisionReversalPayloadV1)
                and payload.restore_decision_id is not None
            ):
                restore_ids.add(payload.restore_decision_id)
        missing_restore_ids = restore_ids.difference(self._decision_cache)
        if missing_restore_ids:
            with self.db.no_autoflush:
                restores = tuple(self.db.scalars(
                    select(ReviewDecision).where(
                        ReviewDecision.id.in_(missing_restore_ids)
                    )
                ))
            self._decision_cache.update(
                {decision.id: decision for decision in restores}
            )
            for restore_id in missing_restore_ids:
                self._decision_cache.setdefault(restore_id, None)

        expected_by_key: dict[str, tuple[object, ...]] = {}
        item_by_key = {
            item.stable_target_key: item for item in unique_items
        }
        for key, item in item_by_key.items():
            decision = self._decision_cache.get(item.current_decision_id)
            if decision is None:
                continue
            try:
                payload = _parse_decision_payload(decision)
            except (TypeError, ValueError):
                continue
            snapshot = payload.projection_after
            if snapshot is not None:
                expected_by_key[key] = tuple(snapshot.overrides)

        with self.db.no_autoflush:
            active_counts = dict(self.db.execute(
                select(
                    FieldOverride.stable_target_key,
                    func.count(FieldOverride.id),
                )
                .where(
                    FieldOverride.stable_target_key.in_(pending),
                    FieldOverride.is_active.is_(True),
                )
                .group_by(FieldOverride.stable_target_key)
            ).tuples().all())

        exact_predicates = []
        loadable_keys: set[str] = set()
        for key, snapshots in expected_by_key.items():
            item = item_by_key[key]
            if (
                len(snapshots) > _PREFETCH_OVERRIDE_LIMIT_PER_STABLE_KEY
                or int(active_counts.get(key, 0)) != len(snapshots)
            ):
                continue
            loadable_keys.add(key)
            for snapshot in snapshots:
                exact_predicates.append(and_(
                    FieldOverride.stable_target_key == key,
                    FieldOverride.review_item_id == item.id,
                    FieldOverride.decision_id == item.current_decision_id,
                    FieldOverride.target_table == item.target_table,
                    FieldOverride.target_pk == item.target_pk,
                    FieldOverride.field_path == snapshot.field_path.value,
                    FieldOverride.scope == snapshot.scope.value,
                    FieldOverride.document_key == snapshot.document_key,
                    FieldOverride.period_id == snapshot.period_id,
                    FieldOverride.relationship_key == snapshot.relationship_key,
                    FieldOverride.is_active.is_(True),
                ))
        with self.db.no_autoflush:
            override_rows = tuple(self.db.scalars(
                select(FieldOverride).where(or_(*exact_predicates))
            )) if exact_predicates else ()
        overrides_by_key: dict[str, list[FieldOverride]] = {
            key: [] for key in pending
        }
        for row in override_rows:
            if row.stable_target_key in loadable_keys:
                overrides_by_key[row.stable_target_key].append(row)
        self._override_cache.update({
            key: tuple(values)
            for key, values in overrides_by_key.items()
        })

        self._cache_only_decision_reads = True
        try:
            for key in pending:
                candidates = grouped.get(key, [])
                projection = (
                    self._effective(candidates[0])
                    if len(candidates) == 1
                    else None
                )
                self._stable_key_cache[key] = projection
                if len(candidates) == 1 and isinstance(candidates[0].target_pk, int):
                    self._record_cache[
                        (candidates[0].target_table, candidates[0].target_pk)
                    ] = projection
        finally:
            self._cache_only_decision_reads = False

    def for_record(
        self,
        target_table: str,
        target_pk: int | None,
    ) -> EffectiveHumanProjection | None:
        if (
            not self._available
            or target_table not in _FIELDS_BY_TARGET
            or not isinstance(target_pk, int)
        ):
            return None
        cache_key = (target_table, target_pk)
        if (
            self._read_scope_depth > 0
            and cache_key in self._record_cache
        ):
            return self._record_cache[cache_key]
        with self.db.no_autoflush:
            items = tuple(self.db.scalars(
                select(ReviewItem)
                .where(
                    ReviewItem.target_table == target_table,
                    ReviewItem.target_pk == target_pk,
                    ReviewItem.case_status != ReviewCaseStatus.SUPERSEDED.value,
                    ReviewItem.current_decision_id.is_not(None),
                )
                .limit(2)
            ))
        projection = self._effective(items[0]) if len(items) == 1 else None
        if self._read_scope_depth > 0:
            self._record_cache[cache_key] = projection
            if len(items) == 1:
                self._stable_key_cache[items[0].stable_target_key] = projection
        return projection

    def for_stable_target_key(
        self,
        stable_target_key: str,
    ) -> EffectiveHumanProjection | None:
        if (
            not self._available
            or not isinstance(stable_target_key, str)
            or len(stable_target_key) > 128
            or _STABLE_TARGET_PATTERN.fullmatch(stable_target_key) is None
        ):
            return None
        if (
            self._read_scope_depth > 0
            and stable_target_key in self._stable_key_cache
        ):
            return self._stable_key_cache[stable_target_key]
        with self.db.no_autoflush:
            items = tuple(self.db.scalars(
                select(ReviewItem)
                .where(
                    ReviewItem.stable_target_key == stable_target_key,
                    ReviewItem.case_status != ReviewCaseStatus.SUPERSEDED.value,
                    ReviewItem.current_decision_id.is_not(None),
                )
                .limit(2)
            ))
        projection = self._effective(items[0]) if len(items) == 1 else None
        if self._read_scope_depth > 0:
            self._stable_key_cache[stable_target_key] = projection
            if len(items) == 1 and isinstance(items[0].target_pk, int):
                self._record_cache[
                    (items[0].target_table, items[0].target_pk)
                ] = projection
        return projection

    def _effective(self, item: ReviewItem) -> EffectiveHumanProjection | None:
        if (
            item.target_table not in _FIELDS_BY_TARGET
            or not isinstance(item.target_pk, int)
            or _STABLE_TARGET_PATTERN.fullmatch(str(item.stable_target_key or "")) is None
            or not item.stable_target_key.startswith(
                f"b2b:v1:{item.case_type}:"
            )
            or item.target_table not in _TARGETS_BY_CASE.get(
                item.case_type, frozenset()
            )
        ):
            return None
        if item.current_decision_id in self._decision_cache:
            decision = self._decision_cache[item.current_decision_id]
        else:
            with self.db.no_autoflush:
                decision = self.db.get(ReviewDecision, item.current_decision_id)
        if (
            decision is None
            or decision.review_item_id != item.id
            or not _approved_locked(decision)
            or decision.payload_schema != _DECISION_PAYLOAD_SCHEMA
            or decision.payload_version != _DECISION_PAYLOAD_VERSION
            or decision.decision_type not in _DECISION_TYPES_BY_CASE.get(
                item.case_type, frozenset()
            )
        ):
            return None
        try:
            payload = _parse_decision_payload(decision)
        except (TypeError, ValueError):
            return None
        if not _payload_matches_case_and_decision(item, decision, payload):
            return None
        snapshot = payload.projection_after
        if snapshot is None or not self._snapshot_matches_current(item, decision, payload, snapshot):
            return None
        if not _payload_matches_projection(item, payload, snapshot):
            return None
        if (
            isinstance(payload, DecisionReversalPayloadV1)
            and payload.restore_decision_id is None
            and snapshot.identity is None
            and not snapshot.overrides
        ):
            return None
        values = self._validated_materialization(item, decision, snapshot)
        if values is None:
            return None
        scientific_values = tuple(
            _scalar_value(override.projected_value)
            for override in snapshot.overrides
            if override.field_path.value == "scientific_status"
        )
        if scientific_values != (snapshot.scientific_status.value,):
            return None
        identity = snapshot.identity
        if identity is not None and item.target_table not in _IDENTITY_TARGETS_BY_CASE.get(
            item.case_type, frozenset()
        ):
            return None
        return EffectiveHumanProjection(
            stable_target_key=item.stable_target_key,
            target_table=item.target_table,
            target_pk=item.target_pk,
            case_type=item.case_type,
            decision_id=decision.id,
            scientific_status=snapshot.scientific_status.value,
            canonical_identity_key=(
                identity.canonical_identity_key if identity is not None else None
            ),
            canonical_name=identity.canonical_name if identity is not None else None,
            identity_type=identity.identity_type.value if identity is not None else None,
            aliases=(
                tuple(alias.alias_original for alias in identity.aliases)
                if identity is not None
                else ()
            ),
            overrides=MappingProxyType(values),
        )

    def _snapshot_matches_current(
        self,
        item: ReviewItem,
        decision: ReviewDecision,
        payload,
        snapshot: ReviewProjectionSnapshotV1,
    ) -> bool:
        if (
            snapshot.case_status.value != item.case_status
            or snapshot.scientific_status.value != item.scientific_status
        ):
            return False
        if isinstance(payload, DecisionReversalPayloadV1):
            if snapshot.current_decision_id != payload.restore_decision_id:
                return False
            if payload.restore_decision_id is None:
                return True
            if payload.restore_decision_id == decision.id:
                return False
            restore = self._load_decision(payload.restore_decision_id)
            return self._historical_snapshot_is_valid(
                item,
                restore,
                snapshot,
                seen={decision.id},
            )
        return snapshot.current_decision_id == decision.id

    def _load_decision(
        self,
        decision_id: UUID,
    ) -> ReviewDecision | None:
        if decision_id in self._decision_cache:
            return self._decision_cache[decision_id]
        if self._cache_only_decision_reads:
            return None
        with self.db.no_autoflush:
            decision = self.db.get(ReviewDecision, decision_id)
        if self._read_scope_depth > 0:
            self._decision_cache[decision_id] = decision
        return decision

    def _historical_snapshot_is_valid(
        self,
        item: ReviewItem,
        decision: ReviewDecision | None,
        snapshot: ReviewProjectionSnapshotV1,
        *,
        seen: set[UUID],
    ) -> bool:
        if (
            decision is None
            or decision.id in seen
            or decision.review_item_id != item.id
            or not _approved_locked(decision)
            or decision.payload_schema != _DECISION_PAYLOAD_SCHEMA
            or decision.payload_version != _DECISION_PAYLOAD_VERSION
            or decision.decision_type not in _DECISION_TYPES_BY_CASE.get(
                item.case_type, frozenset()
            )
        ):
            return False
        seen = {*seen, decision.id}
        try:
            historical_payload = _parse_decision_payload(decision)
        except (TypeError, ValueError):
            return False
        if (
            not _payload_matches_case_and_decision(
                item, decision, historical_payload
            )
            or historical_payload.projection_after != snapshot
            or not _payload_matches_projection(
                item, historical_payload, snapshot
            )
        ):
            return False
        if isinstance(historical_payload, DecisionReversalPayloadV1):
            if (
                historical_payload.restore_decision_id is None
                or snapshot.current_decision_id
                != historical_payload.restore_decision_id
            ):
                return False
            return self._historical_snapshot_is_valid(
                item,
                self._load_decision(
                    historical_payload.restore_decision_id
                ),
                snapshot,
                seen=seen,
            )
        return snapshot.current_decision_id == decision.id

    def _validated_materialization(
        self,
        item: ReviewItem,
        decision: ReviewDecision,
        snapshot: ReviewProjectionSnapshotV1,
    ) -> dict[str, object] | None:
        snapshots = tuple(snapshot.overrides)
        if any(
            override.stable_target_key != item.stable_target_key
            or override.target_table.value != item.target_table
            or override.target_pk != item.target_pk
            or override.locked is not True
            or override.field_path.value not in _FIELDS_BY_TARGET[item.target_table]
            or not self._context_matches(item, override)
            for override in snapshots
        ):
            return None
        if item.stable_target_key in self._override_cache:
            active_rows = self._override_cache[item.stable_target_key]
        else:
            with self.db.no_autoflush:
                active_rows = tuple(self.db.scalars(
                    select(FieldOverride).where(
                        FieldOverride.stable_target_key == item.stable_target_key,
                        FieldOverride.is_active.is_(True),
                    )
                ))
        if len(active_rows) != len(snapshots):
            return None
        rows_by_context = {
            (
                row.field_path,
                row.scope,
                row.document_key,
                row.period_id,
                row.relationship_key,
            ): row
            for row in active_rows
        }
        if len(rows_by_context) != len(active_rows):
            return None
        values: dict[str, object] = {}
        for override in snapshots:
            key = (
                override.field_path.value,
                override.scope.value,
                override.document_key,
                override.period_id,
                override.relationship_key,
            )
            row = rows_by_context.get(key)
            if (
                row is None
                or row.review_item_id != item.id
                or row.decision_id != decision.id
                or row.target_table != item.target_table
                or row.target_pk != item.target_pk
                or row.locked is not True
                or row.value_schema != _OVERRIDE_VALUE_SCHEMA
                or row.value_version != 1
            ):
                return None
            try:
                materialized_value = ScalarOverrideValueV1.model_validate(
                    row.projected_value
                )
            except (TypeError, ValidationError, ValueError):
                return None
            if materialized_value != override.projected_value:
                return None
            values[override.field_path.value] = _scalar_value(materialized_value)
        return values

    @staticmethod
    def _context_matches(
        item: ReviewItem,
        override: ProjectionOverrideSnapshotV1,
    ) -> bool:
        if override.scope.value == "document":
            return override.document_key == item.document_key
        if override.scope.value == "period":
            return override.period_id == item.period_id
        if override.scope.value == "relationship":
            return override.relationship_key == item.relationship_key
        return True


def _parse_decision_payload(decision: ReviewDecision):
    try:
        return _DECISION_PAYLOAD_ADAPTER.validate_python(decision.payload)
    except ValidationError as exc:
        raise ValueError(
            f"decision {decision.id} has an invalid typed payload"
        ) from exc


def _approved_locked(decision: ReviewDecision) -> bool:
    return (
        decision.decision_lifecycle == DecisionLifecycle.APPROVED.value
        and decision.locks_projection is True
    )


def load_identity_projection(
    db: Session,
    stable_target_key: str,
) -> HumanIdentityProjectionV1 | None:
    if (
        not isinstance(stable_target_key, str)
        or len(stable_target_key) > 128
        or _STABLE_TARGET_PATTERN.fullmatch(stable_target_key) is None
    ):
        return None
    with db.no_autoflush:
        items = tuple(db.scalars(
            select(ReviewItem)
            .where(
                ReviewItem.stable_target_key == stable_target_key,
                ReviewItem.case_status
                != ReviewCaseStatus.SUPERSEDED.value,
                ReviewItem.current_decision_id.is_not(None),
            )
            .limit(2)
        ))
    if len(items) != 1:
        return None
    item = items[0]
    if (
        item.target_table not in _IDENTITY_TARGETS_BY_CASE.get(
            item.case_type, frozenset()
        )
        or not item.stable_target_key.startswith(
            f"b2b:v1:{item.case_type}:"
        )
    ):
        return None
    source = EffectiveHumanProjectionSource(db)
    decision = source._load_decision(item.current_decision_id)
    if (
        decision is None
        or decision.review_item_id != item.id
        or not _approved_locked(decision)
        or decision.payload_schema != _DECISION_PAYLOAD_SCHEMA
        or decision.payload_version != _DECISION_PAYLOAD_VERSION
        or decision.decision_type not in _DECISION_TYPES_BY_CASE.get(
            item.case_type, frozenset()
        )
    ):
        return None
    try:
        payload = _parse_decision_payload(decision)
    except (TypeError, ValueError):
        return None
    projection = payload.projection_after
    if (
        projection is None
        or projection.identity is None
        or not _payload_matches_case_and_decision(
            item, decision, payload
        )
        or not _payload_matches_projection(item, payload, projection)
        or not source._snapshot_matches_current(
            item, decision, payload, projection
        )
    ):
        return None
    identity = projection.identity
    return HumanIdentityProjectionV1(
        stable_target_key=stable_target_key,
        canonical_identity_key=identity.canonical_identity_key,
        canonical_name=identity.canonical_name,
        decision_id=decision.id,
        locked=True,
        aliases=tuple(
            alias.alias_original for alias in identity.aliases
        ),
    )


def _coerce_target_statuses(
    case_status: ReviewCaseStatus,
    scientific_status: ScientificStatus,
) -> tuple[ReviewCaseStatus, ScientificStatus]:
    try:
        return ReviewCaseStatus(case_status), ScientificStatus(scientific_status)
    except (TypeError, ValueError) as exc:
        raise ValueError("unknown case or scientific status") from exc


def set_current_decision(
    db: Session,
    review_item_id: UUID,
    decision_id: UUID,
    expected_version: int,
    case_status: ReviewCaseStatus,
    scientific_status: ScientificStatus,
) -> ReviewItem:
    target_case_status, target_scientific_status = _coerce_target_statuses(
        case_status,
        scientific_status,
    )
    with db.no_autoflush:
        item = db.get(ReviewItem, review_item_id)
        decision = db.get(ReviewDecision, decision_id)
        if decision is None:
            decision = next(
                (
                    candidate
                    for candidate in db.new
                    if isinstance(candidate, ReviewDecision)
                    and candidate.id == decision_id
                ),
                None,
            )
    if item is None:
        raise ValueError("review item does not exist")
    assert_expected_version(item.version, expected_version)
    if decision is None:
        raise ValueError("review decision does not exist")
    if decision.review_item_id != item.id:
        raise ValueError("review decision belongs to another case")
    if not _approved_locked(decision):
        raise ValueError("current decision must be approved and projection-locked")

    payload = _parse_decision_payload(decision)
    projection = payload.projection_after
    if projection is None:
        raise ValueError("current decision requires a complete projection snapshot")
    if (
        projection.case_status is not target_case_status
        or projection.scientific_status is not target_scientific_status
    ):
        raise ValueError(
            "requested case and scientific status do not match decision projection_after"
        )
    assert_case_transition(
        ReviewCaseStatus(item.case_status),
        target_case_status,
    )

    result = db.execute(
        update(ReviewItem)
        .where(
            ReviewItem.id == review_item_id,
            ReviewItem.version == expected_version,
        )
        .values(
            current_decision_id=decision_id,
            case_status=target_case_status.value,
            scientific_status=target_scientific_status.value,
            version=ReviewItem.version + 1,
            updated_at=datetime.now(timezone.utc),
        )
        .execution_options(synchronize_session=False, autoflush=False)
    )
    if result.rowcount != 1:
        raise OptimisticLockError(
            f"case {review_item_id} changed while setting its current decision"
        )
    db.flush()
    db.expire(item)
    db.refresh(item)
    return item


def _validate_restore_decision(
    db: Session,
    item: ReviewItem,
    snapshot: ReviewProjectionSnapshotV1,
) -> ReviewDecision | None:
    restore_decision_id = snapshot.current_decision_id
    if restore_decision_id is None:
        return None
    restore_decision = db.get(ReviewDecision, restore_decision_id)
    if restore_decision is None:
        raise ValueError("projection_before restore decision does not exist")
    if restore_decision.review_item_id != item.id:
        raise ValueError("projection_before references a decision from another case")
    if not _approved_locked(restore_decision):
        raise ValueError("restore decision must be approved and projection-locked")
    restore_payload = _parse_decision_payload(restore_decision)
    if restore_payload.projection_after != snapshot:
        raise ValueError("restore decision projection does not match projection_before")
    return restore_decision


def _override_context_predicate(
    snapshot: ProjectionOverrideSnapshotV1,
):
    return and_(
        FieldOverride.stable_target_key == snapshot.stable_target_key,
        FieldOverride.field_path == snapshot.field_path.value,
        FieldOverride.scope == snapshot.scope.value,
        FieldOverride.document_key.is_(None)
        if snapshot.document_key is None
        else FieldOverride.document_key == snapshot.document_key,
        FieldOverride.period_id.is_(None)
        if snapshot.period_id is None
        else FieldOverride.period_id == snapshot.period_id,
        FieldOverride.relationship_key.is_(None)
        if snapshot.relationship_key is None
        else FieldOverride.relationship_key == snapshot.relationship_key,
    )


def _materialize_overrides(
    db: Session,
    item: ReviewItem,
    decision: ReviewDecision,
    snapshot: ReviewProjectionSnapshotV1,
) -> None:
    if any(not override.locked for override in snapshot.overrides):
        raise ValueError("restored projection overrides must be locked")
    predicates = tuple(
        _override_context_predicate(override)
        for override in snapshot.overrides
    )
    affected_predicate = FieldOverride.review_item_id == item.id
    if predicates:
        affected_predicate = or_(affected_predicate, *predicates)
    active_rows = tuple(db.scalars(
        select(FieldOverride).where(
            FieldOverride.is_active.is_(True),
            affected_predicate,
        )
    ))
    now = datetime.now(timezone.utc)
    for row in active_rows:
        row.is_active = False
        row.superseded_by_id = None
        row.version += 1
        row.updated_at = now
    db.flush()

    replacements: list[FieldOverride] = []
    for override in snapshot.overrides:
        replacement = FieldOverride(
            id=uuid4(),
            review_item_id=item.id,
            decision_id=decision.id,
            stable_target_key=override.stable_target_key,
            target_table=override.target_table.value,
            target_pk=override.target_pk,
            field_path=override.field_path.value,
            value_schema=_OVERRIDE_VALUE_SCHEMA,
            value_version=1,
            projected_value=override.projected_value.model_dump(mode="json"),
            scope=override.scope.value,
            document_key=override.document_key,
            period_id=override.period_id,
            relationship_key=override.relationship_key,
            locked=True,
            is_active=True,
            valid_from=now,
            version=1,
        )
        replacements.append(replacement)
    db.add_all(replacements)
    db.flush()

    replacement_by_context = {
        (
            row.stable_target_key,
            row.field_path,
            row.scope,
            row.document_key,
            row.period_id,
            row.relationship_key,
        ): row
        for row in replacements
    }
    for row in active_rows:
        replacement = replacement_by_context.get((
            row.stable_target_key,
            row.field_path,
            row.scope,
            row.document_key,
            row.period_id,
            row.relationship_key,
        ))
        if replacement is not None:
            row.superseded_by_id = replacement.id
    db.flush()


def _materialize_identity(
    db: Session,
    decision: ReviewDecision,
    current_snapshot: ReviewProjectionSnapshotV1,
    restored_snapshot: ReviewProjectionSnapshotV1,
) -> None:
    current_identity_snapshot = current_snapshot.identity
    restored_identity_snapshot = restored_snapshot.identity
    current_identity = None
    if current_identity_snapshot is not None:
        current_identity = db.scalar(select(CanonicalIdentity).where(
            CanonicalIdentity.canonical_identity_key
            == current_identity_snapshot.canonical_identity_key
        ))

    restored_identity = None
    if restored_identity_snapshot is not None:
        restored_identity = db.scalar(select(CanonicalIdentity).where(
            CanonicalIdentity.canonical_identity_key
            == restored_identity_snapshot.canonical_identity_key
        ))
        if restored_identity is None:
            restored_identity = CanonicalIdentity(
                id=uuid4(),
                canonical_identity_key=restored_identity_snapshot.canonical_identity_key,
                identity_type=restored_identity_snapshot.identity_type.value,
                display_name=restored_identity_snapshot.canonical_name,
                status=CanonicalIdentityStatus.ACTIVE.value,
                origin=CanonicalIdentityOrigin.HUMAN.value,
                created_by_decision_id=decision.id,
                superseded_by_id=None,
                version=1,
            )
            db.add(restored_identity)
            db.flush()
        elif restored_identity.identity_type != restored_identity_snapshot.identity_type.value:
            raise ValueError("restored canonical identity type does not match its snapshot")
        else:
            restored_identity.display_name = restored_identity_snapshot.canonical_name
            restored_identity.status = CanonicalIdentityStatus.ACTIVE.value
            restored_identity.superseded_by_id = None
            restored_identity.version += 1

    if current_identity is not None and (
        restored_identity is None or current_identity.id != restored_identity.id
    ):
        current_identity.status = CanonicalIdentityStatus.SUPERSEDED.value
        current_identity.superseded_by_id = (
            restored_identity.id if restored_identity is not None else None
        )
        current_identity.version += 1
    db.flush()

    identity_ids = {
        identity.id
        for identity in (current_identity, restored_identity)
        if identity is not None
    }
    restored_aliases = (
        restored_identity_snapshot.aliases
        if restored_identity_snapshot is not None
        else ()
    )
    restored_normalized = tuple(alias.alias_normalized for alias in restored_aliases)
    alias_predicates = []
    if identity_ids:
        alias_predicates.append(PersonAlias.canonical_identity_id.in_(identity_ids))
    if restored_normalized:
        alias_predicates.append(PersonAlias.alias_normalized.in_(restored_normalized))
    active_aliases = ()
    if alias_predicates:
        active_aliases = tuple(db.scalars(
            select(PersonAlias).where(
                PersonAlias.status == PersonAliasStatus.ACTIVE.value,
                or_(*alias_predicates),
            )
        ))
    for alias in active_aliases:
        alias.status = PersonAliasStatus.SUPERSEDED.value
        alias.superseded_by_id = None
        alias.version += 1
    db.flush()

    new_aliases: list[PersonAlias] = []
    if restored_identity is not None:
        for alias in restored_aliases:
            new_aliases.append(PersonAlias(
                id=uuid4(),
                alias_original=alias.alias_original,
                alias_normalized=alias.alias_normalized,
                alias_class=PersonAliasClass.PERSON_NAME.value,
                canonical_identity_id=restored_identity.id,
                decision_id=decision.id,
                scope=PersonAliasScope.GLOBAL_IDENTITY.value,
                status=PersonAliasStatus.ACTIVE.value,
                superseded_by_id=None,
                version=1,
            ))
    db.add_all(new_aliases)
    db.flush()
    replacement_by_normalized = {
        alias.alias_normalized: alias
        for alias in new_aliases
    }
    for alias in active_aliases:
        replacement = replacement_by_normalized.get(alias.alias_normalized)
        if replacement is not None:
            alias.superseded_by_id = replacement.id
    db.flush()


def _append_reversal_audit(
    db: Session,
    *,
    item: ReviewItem,
    decision: ReviewDecision,
    reverted_decision: ReviewDecision,
    restore_decision: ReviewDecision | None,
    actor: User,
    command: FunctionalReversalCommandV1,
) -> None:
    append_audit_event_at_current_head(
        db,
        AuditEventCommandV1(
            id=uuid4(),
            event_type=AuditEventType.FUNCTIONAL_REVERSION,
            aggregate_type=_AUDIT_AGGREGATE_TYPE,
            aggregate_key=item.stable_target_key,
            review_item_id=item.id,
            actor_user_id=actor.id,
            actor_identifier=actor.email,
            actor_capability=B2BCapability.RESEARCH_MANAGER,
            occurred_at=datetime.now(timezone.utc),
            payload=FunctionalReversalAuditPayloadV1(
                kind="functional_reversal",
                schema_version=1,
                new_decision_id=decision.id,
                reverted_decision_id=reverted_decision.id,
                restored_decision_id=(
                    restore_decision.id if restore_decision is not None else None
                ),
                review_item_id=item.id,
            ),
            correlation_id=command.correlation_id,
            request_id=command.request_id,
            previous_event_id=None,
            corrects_event_id=None,
        ),
    )


def _append_functional_reversal(
    db: Session,
    command: FunctionalReversalCommandV1,
) -> ReviewDecision:
    actor = db.get(User, command.actor_user_id)
    if actor is None:
        raise PermissionError("B2B action is not authorized")
    capability = authorize_b2b_action(db, actor, B2BAction.REVERT_SCIENTIFIC)
    if capability is not B2BCapability.RESEARCH_MANAGER:
        raise PermissionError("functional reversal requires RESEARCH_MANAGER")

    item = db.scalars(
        select(ReviewItem)
        .where(
            ReviewItem.id == command.review_item_id,
            review_scope_predicate(actor),
        )
        .with_for_update()
    ).first()
    if item is None:
        if db.get(ReviewItem, command.review_item_id) is not None:
            raise PermissionError("B2B action is not authorized")
        raise ValueError("review item does not exist")
    assert_review_item_scope(actor, item, correlation_id=command.correlation_id)
    assert_expected_version(item.version, command.expected_case_version)

    reverted_decision = db.get(ReviewDecision, command.decision_id_to_revert)
    if reverted_decision is None:
        raise ValueError("decision to revert does not exist")
    if reverted_decision.review_item_id != item.id:
        raise ValueError("decision to revert belongs to another case")
    if not _approved_locked(reverted_decision):
        raise ValueError("decision to revert must be approved and projection-locked")
    reverted_payload = _parse_decision_payload(reverted_decision)
    source_restored_snapshot = reverted_payload.projection_before
    if source_restored_snapshot is None or reverted_payload.projection_after is None:
        raise ValueError("decision to revert has no complete restorable snapshot pair")
    if source_restored_snapshot.current_decision_id == reverted_decision.id:
        raise ValueError("projection_before cannot restore the decision being reverted")
    restore_decision = _validate_restore_decision(
        db,
        item,
        source_restored_snapshot,
    )

    effective_restored_snapshot = source_restored_snapshot
    if source_restored_snapshot.case_status == ReviewCaseStatus.PENDING:
        effective_restored_snapshot = source_restored_snapshot.model_copy(
            update={"case_status": ReviewCaseStatus.REOPENED}
        )

    if item.current_decision_id is None:
        raise ValueError("review item has no current decision")
    current_decision = db.get(ReviewDecision, item.current_decision_id)
    if current_decision is None or current_decision.review_item_id != item.id:
        raise ValueError("review item current decision is invalid")
    if not _approved_locked(current_decision):
        raise ValueError("review item current decision must be approved and locked")
    current_payload = _parse_decision_payload(current_decision)
    current_snapshot = current_payload.projection_after
    if current_snapshot is None:
        raise ValueError("current decision has no complete projection snapshot")
    if (
        current_snapshot.case_status.value != item.case_status
        or current_snapshot.scientific_status.value != item.scientific_status
    ):
        raise ValueError("review item state does not match its current projection")
    assert_case_transition(
        ReviewCaseStatus(item.case_status),
        effective_restored_snapshot.case_status,
    )

    current_snapshot = current_snapshot.model_copy(
        update={"current_decision_id": current_decision.id}
    )
    reversal_payload = DecisionReversalPayloadV1(
        kind="decision_reversal",
        schema_version=1,
        decision_id_to_revert=reverted_decision.id,
        restore_decision_id=(
            restore_decision.id if restore_decision is not None else None
        ),
        projection_before=current_snapshot,
        projection_after=effective_restored_snapshot,
    )
    next_sequence = db.scalar(select(
        func.coalesce(func.max(ReviewDecision.sequence), 0) + 1
    ).where(ReviewDecision.review_item_id == item.id))
    decision = ReviewDecision(
        id=uuid4(),
        review_item_id=item.id,
        sequence=next_sequence,
        decision_type=ReviewDecisionType.REVERTED.value,
        decision_lifecycle=DecisionLifecycle.APPROVED.value,
        scope=reverted_decision.scope,
        payload_schema=_DECISION_PAYLOAD_SCHEMA,
        payload_version=1,
        payload=reversal_payload.model_dump(mode="json"),
        reason=command.reason,
        actor_type=ReviewActorType.HUMAN.value,
        actor_user_id=actor.id,
        actor_identifier=actor.email,
        actor_capability=B2BCapability.RESEARCH_MANAGER.value,
        decided_at=datetime.now(timezone.utc),
        expected_case_version=command.expected_case_version,
        previous_decision_id=current_decision.id,
        corrects_decision_id=reverted_decision.id,
        locks_projection=True,
    )
    db.add(decision)
    set_current_decision(
        db,
        item.id,
        decision.id,
        command.expected_case_version,
        effective_restored_snapshot.case_status,
        effective_restored_snapshot.scientific_status,
    )
    _materialize_overrides(db, item, decision, effective_restored_snapshot)
    _materialize_identity(
        db,
        decision,
        current_snapshot,
        effective_restored_snapshot,
    )
    _append_reversal_audit(
        db,
        item=item,
        decision=decision,
        reverted_decision=reverted_decision,
        restore_decision=restore_decision,
        actor=actor,
        command=command,
    )
    db.flush()
    return decision


def append_functional_reversal(
    db: Session,
    command: FunctionalReversalCommandV1,
) -> ReviewDecision:
    command = FunctionalReversalCommandV1.model_validate(command)
    try:
        return _append_functional_reversal(db, command)
    except Exception:
        db.rollback()
        raise
