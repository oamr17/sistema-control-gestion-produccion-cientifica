from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.schemas.human_review_operations import (
    B2B1InvariantSnapshotV1,
    ParticipantMetricsV1,
    TableDigestV1,
)
from app.services.validated_read_service import ValidatedReadService


PROTECTED_TABLES: tuple[str, ...] = (
    "person_roles",
    "scientific_production_authors",
    "scientific_productions",
    "research_entities",
    "research_projects",
    "external_researchers",
    "import_batches",
    "import_jobs",
    "import_review_items",
    "import_normalization_audits",
    "imported_research_records",
    "imported_project_participants",
    "imported_progress_reports",
    "imported_ocr_traces",
)

_PARTICIPANT_METRIC_FIELDS: tuple[str, ...] = (
    "canonical_identities",
    "participations",
    "roles",
    "authorships",
    "pending",
    "external_detected",
    "external_kpi_eligible",
    "external_pending",
)


class InvariantViolation(RuntimeError):
    pass


@dataclass(frozen=True)
class _DatabaseState:
    migration_versions: tuple[str, ...]
    digests: tuple[TableDigestV1, ...]


def _canonicalize(value: object) -> object:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Enum):
        return _canonicalize(value.value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InvariantViolation("non-finite floats cannot be hashed")
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise InvariantViolation("non-finite decimals cannot be hashed")
        return {"$decimal": str(value)}
    if isinstance(value, UUID):
        return {"$uuid": str(value)}
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return {"$datetime_naive": value.isoformat()}
        return {"$datetime": value.astimezone(timezone.utc).isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, bytes):
        return {"$bytes": value.hex()}
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise InvariantViolation("canonical mappings require string keys")
        return {key: _canonicalize(value[key]) for key in sorted(value)}
    if isinstance(value, Sequence):
        return [_canonicalize(item) for item in value]
    raise InvariantViolation(f"unsupported invariant value type: {type(value).__name__}")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        _canonicalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _table_digest(label: str, rows: list[Mapping[str, object]]) -> TableDigestV1:
    digest = hashlib.sha256(_canonical_json(rows)).hexdigest()
    return TableDigestV1(label=label, row_count=len(rows), sha256=digest)


def _capture_database_state(connection: Connection) -> _DatabaseState:
    migration_versions = tuple(
        connection.execute(
            text("SELECT version FROM schema_migrations ORDER BY version ASC")
        ).scalars()
    )
    digests: list[TableDigestV1] = []
    for label in PROTECTED_TABLES:
        rows = connection.execute(
            text(f'SELECT * FROM "{label}" ORDER BY id ASC')
        ).mappings().all()
        digests.append(_table_digest(label, rows))
    return _DatabaseState(migration_versions=migration_versions, digests=tuple(digests))


def _participant_metrics(service: ValidatedReadService) -> ParticipantMetricsV1:
    return ParticipantMetricsV1.model_validate(
        service.canonical_participant_metrics(),
        strict=True,
    )


def _database_state_changes(
    before: _DatabaseState,
    after: _DatabaseState,
) -> list[str]:
    before_digests = {digest.label: digest for digest in before.digests}
    after_digests = {digest.label: digest for digest in after.digests}
    changes = [
        label
        for label in PROTECTED_TABLES
        if before_digests.get(label) != after_digests.get(label)
    ]
    if before.migration_versions != after.migration_versions:
        changes.append("migration_versions")
    return changes


def snapshot_sha256(snapshot: B2B1InvariantSnapshotV1) -> str:
    material = snapshot.model_dump(mode="python", exclude={"captured_at"})
    return hashlib.sha256(_canonical_json(material)).hexdigest()


def capture_b2b1_invariants(connection: Connection) -> B2B1InvariantSnapshotV1:
    connection.exec_driver_sql("SET TRANSACTION READ ONLY")
    before = _capture_database_state(connection)

    session = Session(bind=connection, autoflush=False)
    try:
        service = ValidatedReadService(session)
        eligible_products = len(service.production_views(visibility="eligible"))
        participant_metrics = _participant_metrics(service)
    finally:
        session.close()

    after = _capture_database_state(connection)
    changed_labels = _database_state_changes(before, after)
    if changed_labels:
        raise InvariantViolation(
            "B2B.1 invariant drift detected: changed labels: " + ", ".join(changed_labels)
        )

    return B2B1InvariantSnapshotV1(
        schema_version=1,
        captured_at=datetime.now(timezone.utc),
        migration_versions=before.migration_versions,
        digests=before.digests,
        eligible_products=eligible_products,
        participant_metrics=participant_metrics,
    )


def _comparison_changes(
    before: B2B1InvariantSnapshotV1,
    after: B2B1InvariantSnapshotV1,
) -> list[str]:
    changes: list[str] = []
    if before.schema_version != after.schema_version:
        changes.append("schema_version")
    if before.migration_versions != after.migration_versions:
        changes.append("migration_versions")

    before_labels = tuple(digest.label for digest in before.digests)
    after_labels = tuple(digest.label for digest in after.digests)
    if before_labels != after_labels:
        changes.append("digests.labels")

    before_by_label = {digest.label: digest for digest in before.digests}
    after_by_label = {digest.label: digest for digest in after.digests}
    changes.extend(
        f"digests.removed.{label}"
        for label in before_labels
        if label not in after_by_label
    )
    changes.extend(
        f"digests.added.{label}"
        for label in after_labels
        if label not in before_by_label
    )
    for label in before_labels:
        if label not in after_by_label:
            continue
        before_digest = before_by_label[label]
        after_digest = after_by_label[label]
        if before_digest.row_count != after_digest.row_count:
            changes.append(f"{label}.row_count")
        if before_digest.sha256 != after_digest.sha256:
            changes.append(f"{label}.sha256")

    if before.eligible_products != after.eligible_products:
        changes.append("eligible_products")
    for field in _PARTICIPANT_METRIC_FIELDS:
        if getattr(before.participant_metrics, field) != getattr(after.participant_metrics, field):
            changes.append(f"participant_metrics.{field}")
    return changes


def compare_b2b1_invariants(
    before: B2B1InvariantSnapshotV1,
    after: B2B1InvariantSnapshotV1,
) -> None:
    changed_labels = _comparison_changes(before, after)
    if changed_labels:
        raise InvariantViolation(
            "B2B.1 invariant mismatch: changed labels: " + ", ".join(changed_labels)
        )
