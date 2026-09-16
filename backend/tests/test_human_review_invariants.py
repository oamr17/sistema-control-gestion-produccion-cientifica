from __future__ import annotations

import inspect
import re
import sys
import unittest
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.engine import Connection


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schemas.human_review_operations import (
    B2B1InvariantSnapshotV1,
    ParticipantMetricsV1,
    TableDigestV1,
)
from app.services.human_review_invariants import (
    PROTECTED_TABLES,
    InvariantViolation,
    capture_b2b1_invariants,
    compare_b2b1_invariants,
    snapshot_sha256,
)


EXPECTED_PROTECTED_TABLES = (
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

METRIC_FIELDS = (
    "canonical_identities",
    "participations",
    "roles",
    "authorships",
    "pending",
    "external_detected",
    "external_kpi_eligible",
    "external_pending",
)


class PersistedExample(Enum):
    READY = "ready"


class FakeResult:
    def __init__(self, rows: list[dict[str, object]]):
        self._rows = rows

    def mappings(self) -> FakeResult:
        return self

    def all(self) -> list[dict[str, object]]:
        return deepcopy(self._rows)

    def scalars(self) -> FakeResult:
        values = [{"value": row["version"]} for row in self._rows]
        return FakeResult(values)

    def __iter__(self):
        for row in self._rows:
            yield row.get("value")


class RecordingConnection:
    def __init__(
        self,
        first_rows: dict[str, list[dict[str, object]]],
        *,
        second_rows: dict[str, list[dict[str, object]]] | None = None,
        first_migrations: tuple[str, ...] = ("20260628_0001", "20260712_0016"),
        second_migrations: tuple[str, ...] | None = None,
    ) -> None:
        self._states = (deepcopy(first_rows), deepcopy(second_rows or first_rows))
        self._migrations = (
            first_migrations,
            second_migrations if second_migrations is not None else first_migrations,
        )
        self._state_index = 0
        self.statements: list[str] = []
        self.write_calls: list[str] = []
        self.closed = False

    def exec_driver_sql(self, statement: str) -> None:
        self.statements.append(statement)
        if statement != "SET TRANSACTION READ ONLY":
            raise AssertionError(f"unexpected driver SQL: {statement}")

    def execute(self, statement: object) -> FakeResult:
        sql = str(statement)
        normalized = " ".join(sql.lower().split())
        self.statements.append(sql)
        if not normalized.startswith("select "):
            raise AssertionError(f"write or DDL attempted: {sql}")
        if "schema_migrations" in normalized:
            rows = [{"version": version} for version in self._migrations[self._state_index]]
            return FakeResult(rows)
        for label in EXPECTED_PROTECTED_TABLES:
            if f'from "{label}"' not in normalized and f"from {label}" not in normalized:
                continue
            rows = deepcopy(self._states[self._state_index][label])
            if "order by id" in normalized or f'order by "{label}".id' in normalized:
                rows.sort(key=lambda row: row["id"])
            if label == EXPECTED_PROTECTED_TABLES[-1]:
                self._state_index = min(self._state_index + 1, 1)
            return FakeResult(rows)
        raise AssertionError(f"unrecognized read: {sql}")

    def commit(self) -> None:
        self.write_calls.append("commit")
        raise AssertionError("capture must not commit")

    def rollback(self) -> None:
        self.write_calls.append("rollback")
        raise AssertionError("capture must not rollback")

    def close(self) -> None:
        self.closed = True
        raise AssertionError("capture must not close the supplied connection")


class RecordingSession:
    def __init__(self, *, bind: RecordingConnection, autoflush: bool) -> None:
        self.bind = bind
        self.autoflush = autoflush
        self.closed = False
        self.write_calls: list[str] = []

    def close(self) -> None:
        self.closed = True

    def _forbid(self, label: str) -> None:
        self.write_calls.append(label)
        raise AssertionError(f"session write attempted: {label}")

    def add(self, _value: object) -> None:
        self._forbid("add")

    def delete(self, _value: object) -> None:
        self._forbid("delete")

    def flush(self) -> None:
        self._forbid("flush")

    def commit(self) -> None:
        self._forbid("commit")

    def rollback(self) -> None:
        self._forbid("rollback")


def participant_rows_fixture() -> list[dict[str, object]]:
    return [
        {
            "canonical_identity_key": "external:1",
            "participation_count": 2,
            "role_count": 1,
            "authorship_count": 2,
            "overall_status": "validated",
            "person_type": "investigador_externo",
            "authorships": [{"kpi_eligible": True}, {"kpi_eligible": False}],
        },
        {
            "canonical_identity_key": "pending:external:2",
            "participation_count": 1,
            "role_count": 2,
            "authorship_count": 0,
            "overall_status": "pending_review",
            "person_type": "investigador_externo",
            "authorships": [],
        },
        {
            "canonical_identity_key": "external:3",
            "participation_count": 1,
            "role_count": 1,
            "authorship_count": 1,
            "overall_status": "validated",
            "person_type": "investigador_externo",
            "authorships": [{"kpi_eligible": False}],
        },
        {
            "canonical_identity_key": "teacher:4",
            "participation_count": 3,
            "role_count": 4,
            "authorship_count": 1,
            "overall_status": "validated",
            "person_type": "docente_interno",
            "authorships": [{"kpi_eligible": True}],
        },
    ]


def participant_metric_values_fixture() -> dict[str, object]:
    return {
        "canonical_identities": 4,
        "participations": 7,
        "roles": 8,
        "authorships": 4,
        "pending": 1,
        "external_detected": 3,
        "external_kpi_eligible": 1,
        "external_pending": 1,
    }


class RecordingReadService:
    def __init__(
        self,
        session: RecordingSession,
        participant_rows: list[dict[str, object]],
        metric_values: dict[str, object],
    ) -> None:
        self.session = session
        self.participant_rows = participant_rows
        self.metric_values = metric_values
        self.production_calls: list[dict[str, object]] = []
        self.participant_calls: list[dict[str, object]] = []
        self.metric_calls: list[dict[str, object]] = []

    def production_views(self, **kwargs: object) -> list[dict[str, int]]:
        self.production_calls.append(dict(kwargs))
        return [{"id": 101}, {"id": 202}]

    def canonical_participants(self, **kwargs: object) -> list[dict[str, object]]:
        self.participant_calls.append(dict(kwargs))
        return deepcopy(self.participant_rows)

    def canonical_participant_metrics(self, **kwargs: object) -> dict[str, object]:
        self.metric_calls.append(dict(kwargs))
        return deepcopy(self.metric_values)


def protected_rows_fixture() -> dict[str, list[dict[str, object]]]:
    rows = {label: [{"id": 1}] for label in EXPECTED_PROTECTED_TABLES}
    rows["person_roles"] = [
        {
            "id": 2,
            "raw_value": "  raw role value  ",
            "metadata_json": {"source": {"page": 2, "tokens": ["a", "b"]}},
            "identity_locked": False,
            "identity_decided_by": None,
            "identity_decided_at": None,
        },
        {
            "id": 1,
            "raw_value": "first",
            "metadata_json": None,
            "identity_locked": True,
            "identity_decided_by": "operator@example.edu",
            "identity_decided_at": datetime(2026, 7, 12, 18, 30, tzinfo=timezone.utc),
        },
    ]
    rows["scientific_production_authors"] = [
        {
            "id": 1,
            "raw_author_name": "Raw Author",
            "identity_locked": True,
            "identity_decided_by": "manager@example.edu",
            "identity_decided_at": datetime(2026, 7, 12, 19, 30, tzinfo=timezone.utc),
        }
    ]
    rows["scientific_productions"] = [
        {"id": 1, "raw_title": "RAW TITLE", "normalized_title": "Normalized title"}
    ]
    rows["import_batches"] = [
        {"id": 1, "created_at": None, "completed_at": None}
    ]
    rows["import_jobs"] = [
        {
            "id": 1,
            "status": "SUCCESS",
            "is_current": True,
            "error_details": None,
            "created_at": None,
            "processed_at": None,
            "started_at": None,
            "finished_at": None,
        }
    ]
    rows["imported_research_records"] = [
        {"id": 1, "raw_value": "source record", "metadata_json": {"nested": {"a": 1, "b": [2, 3]}}}
    ]
    rows["imported_ocr_traces"] = [
        {
            "id": 1,
            "extracted_text": "unaltered OCR",
            "parsed_payload": {"outer": {"alpha": 1, "beta": [True, None]}},
            "review_status": "PENDIENTE_REVISION",
        }
    ]
    return rows


def historical_naive_timestamp_rows_fixture() -> dict[str, list[dict[str, object]]]:
    rows = protected_rows_fixture()
    rows["import_batches"][0].update(
        created_at=datetime(2026, 6, 28, 8, 15, 30, 123456),
        completed_at=datetime(2026, 6, 28, 8, 45, 31, 654321),
    )
    rows["import_jobs"][0].update(
        created_at=datetime(2026, 6, 28, 8, 16, 1, 111111),
        processed_at=datetime(2026, 6, 28, 8, 44, 59, 222222),
        started_at=datetime(2026, 6, 28, 8, 16, 2, 333333),
        finished_at=datetime(2026, 6, 28, 8, 44, 58, 444444),
    )
    return rows


def capture_fixture(
    first_rows: dict[str, list[dict[str, object]]] | None = None,
    *,
    second_rows: dict[str, list[dict[str, object]]] | None = None,
    first_migrations: tuple[str, ...] = ("20260628_0001", "20260712_0016"),
    second_migrations: tuple[str, ...] | None = None,
    participant_rows: list[dict[str, object]] | None = None,
    metric_values: dict[str, object] | None = None,
) -> tuple[B2B1InvariantSnapshotV1, RecordingConnection, RecordingReadService, RecordingSession]:
    connection = RecordingConnection(
        first_rows or protected_rows_fixture(),
        second_rows=second_rows,
        first_migrations=first_migrations,
        second_migrations=second_migrations,
    )
    sessions: list[RecordingSession] = []
    readers: list[RecordingReadService] = []

    def session_factory(*, bind: RecordingConnection, autoflush: bool) -> RecordingSession:
        session = RecordingSession(bind=bind, autoflush=autoflush)
        sessions.append(session)
        return session

    def reader_factory(session: RecordingSession) -> RecordingReadService:
        reader = RecordingReadService(
            session,
            participant_rows or participant_rows_fixture(),
            metric_values or participant_metric_values_fixture(),
        )
        readers.append(reader)
        return reader

    with (
        patch("app.services.human_review_invariants.Session", side_effect=session_factory),
        patch("app.services.human_review_invariants.ValidatedReadService", side_effect=reader_factory),
    ):
        snapshot = capture_b2b1_invariants(connection)  # type: ignore[arg-type]
    return snapshot, connection, readers[0], sessions[0]


def snapshot_fixture(*, captured_at: datetime | None = None) -> B2B1InvariantSnapshotV1:
    return B2B1InvariantSnapshotV1(
        schema_version=1,
        captured_at=captured_at or datetime(2026, 7, 13, 10, 0, tzinfo=timezone.utc),
        migration_versions=("20260628_0001", "20260712_0016"),
        digests=tuple(
            TableDigestV1(label=label, row_count=index, sha256=f"{index + 1:064x}")
            for index, label in enumerate(EXPECTED_PROTECTED_TABLES)
        ),
        eligible_products=2,
        participant_metrics=ParticipantMetricsV1(
            canonical_identities=4,
            participations=7,
            roles=8,
            authorships=4,
            pending=1,
            external_detected=3,
            external_kpi_eligible=1,
            external_pending=1,
        ),
    )


class HumanReviewOperationManifestTests(unittest.TestCase):
    def test_models_are_closed_frozen_and_validate_nonnegative_counts(self) -> None:
        digest = TableDigestV1(label="person_roles", row_count=0, sha256="a" * 64)
        with self.assertRaises(ValidationError):
            TableDigestV1.model_validate(digest.model_dump() | {"unexpected": True})
        with self.assertRaises(ValidationError):
            digest.row_count = 1
        with self.assertRaises(ValidationError):
            TableDigestV1(label="person_roles", row_count=-1, sha256="a" * 64)

        metrics_values = {field: 0 for field in METRIC_FIELDS}
        metrics = ParticipantMetricsV1(**metrics_values)
        with self.assertRaises(ValidationError):
            ParticipantMetricsV1.model_validate(metrics.model_dump() | {"unexpected": 1})
        with self.assertRaises(ValidationError):
            metrics.pending = 1
        for field in METRIC_FIELDS:
            with self.subTest(field=field), self.assertRaises(ValidationError):
                ParticipantMetricsV1(**(metrics_values | {field: -1}))

        snapshot = snapshot_fixture()
        with self.assertRaises(ValidationError):
            B2B1InvariantSnapshotV1.model_validate(snapshot.model_dump() | {"unexpected": 1})
        with self.assertRaises(ValidationError):
            snapshot.eligible_products = 3
        with self.assertRaises(ValidationError):
            B2B1InvariantSnapshotV1.model_validate(snapshot.model_dump() | {"eligible_products": -1})

    def test_digest_hash_is_exact_lowercase_sha256_and_labels_are_unique(self) -> None:
        for invalid_hash in ("a" * 63, "a" * 65, "A" * 64, "g" * 64, "0x" + "a" * 64):
            with self.subTest(invalid_hash=invalid_hash), self.assertRaises(ValidationError):
                TableDigestV1(label="person_roles", row_count=0, sha256=invalid_hash)

        snapshot = snapshot_fixture()
        duplicate = snapshot.digests + (snapshot.digests[0],)
        with self.assertRaisesRegex(ValidationError, "duplicate digest labels"):
            B2B1InvariantSnapshotV1.model_validate(snapshot.model_dump() | {"digests": duplicate})


class CaptureInvariantTests(unittest.TestCase):
    def test_protected_set_is_exact_and_every_direct_read_is_ordered(self) -> None:
        snapshot, connection, _reader, _session = capture_fixture()

        self.assertEqual(PROTECTED_TABLES, EXPECTED_PROTECTED_TABLES)
        self.assertEqual(tuple(item.label for item in snapshot.digests), EXPECTED_PROTECTED_TABLES)
        b2b_labels = {
            "user_b2b_capabilities",
            "review_items",
            "review_decisions",
            "canonical_identities",
            "person_aliases",
            "field_overrides",
            "audit_events",
        }
        self.assertFalse(set(PROTECTED_TABLES) & b2b_labels)

        reads = connection.statements[1:]
        self.assertEqual(len(reads), 2 * (len(EXPECTED_PROTECTED_TABLES) + 1))
        for statement in reads:
            normalized = " ".join(statement.lower().split())
            self.assertTrue(normalized.startswith("select "), statement)
            self.assertIn(" order by ", normalized, statement)
        for label in EXPECTED_PROTECTED_TABLES:
            self.assertEqual(sum(f'from "{label}"' in sql.lower() for sql in reads), 2)
        self.assertEqual(sum("schema_migrations" in sql.lower() for sql in reads), 2)

    def test_read_only_is_first_no_writes_and_session_does_not_own_connection(self) -> None:
        snapshot, connection, reader, session = capture_fixture()

        self.assertEqual(connection.statements[0], "SET TRANSACTION READ ONLY")
        self.assertEqual(connection.write_calls, [])
        self.assertFalse(connection.closed)
        self.assertIs(session.bind, connection)
        self.assertFalse(session.autoflush)
        self.assertTrue(session.closed)
        self.assertEqual(session.write_calls, [])
        self.assertEqual(reader.production_calls, [{"visibility": "eligible"}])
        self.assertEqual(reader.metric_calls, [{}])
        self.assertEqual(reader.participant_calls, [])
        self.assertEqual(snapshot.eligible_products, 2)

    def test_participant_metrics_delegate_byte_for_field_to_existing_b2a_contract(self) -> None:
        expected = participant_metric_values_fixture()
        snapshot, _connection, reader, _session = capture_fixture(metric_values=expected)

        self.assertEqual(snapshot.participant_metrics.model_dump(), expected)
        self.assertEqual(reader.metric_calls, [{}])
        self.assertEqual(reader.participant_calls, [])

    def test_metrics_preserve_external_eligible_without_eligible_authorship(self) -> None:
        participant_rows = [
            {
                "canonical_identity_key": "external:eligible",
                "participation_count": 1,
                "role_count": 1,
                "authorship_count": 1,
                "overall_status": "validated",
                "person_type": "investigador_externo",
                "kpi_eligible": True,
                "authorships": [{"kpi_eligible": False}],
            }
        ]
        approved_metrics = {
            "canonical_identities": 1,
            "participations": 1,
            "roles": 1,
            "authorships": 1,
            "pending": 0,
            "external_detected": 1,
            "external_kpi_eligible": 1,
            "external_pending": 0,
        }

        snapshot, _connection, reader, _session = capture_fixture(
            participant_rows=participant_rows,
            metric_values=approved_metrics,
        )

        self.assertEqual(snapshot.participant_metrics.model_dump(), approved_metrics)
        self.assertEqual(reader.metric_calls, [{}])
        self.assertEqual(reader.participant_calls, [])

    def test_metrics_preserve_pending_external_with_eligible_authorship(self) -> None:
        participant_rows = [
            {
                "canonical_identity_key": "pending:external:eligible-author",
                "participation_count": 1,
                "role_count": 1,
                "authorship_count": 1,
                "overall_status": "pending_review",
                "person_type": "investigador_externo",
                "kpi_eligible": False,
                "authorships": [{"kpi_eligible": True}],
            }
        ]
        approved_metrics = {
            "canonical_identities": 1,
            "participations": 1,
            "roles": 1,
            "authorships": 1,
            "pending": 1,
            "external_detected": 1,
            "external_kpi_eligible": 0,
            "external_pending": 1,
        }

        snapshot, _connection, reader, _session = capture_fixture(
            participant_rows=participant_rows,
            metric_values=approved_metrics,
        )

        self.assertEqual(snapshot.participant_metrics.model_dump(), approved_metrics)
        self.assertEqual(reader.metric_calls, [{}])
        self.assertEqual(reader.participant_calls, [])

    def test_metric_delegate_rejects_missing_extra_negative_and_wrong_types(self) -> None:
        valid = participant_metric_values_fixture()
        missing = deepcopy(valid)
        del missing["pending"]
        invalid_cases = {
            "missing": missing,
            "extra": valid | {"unexpected": 1},
            "negative": valid | {"roles": -1},
            "wrong_type": valid | {"participations": "7"},
        }
        for name, metric_values in invalid_cases.items():
            with self.subTest(case=name), self.assertRaises(ValidationError):
                capture_fixture(metric_values=metric_values)

    def test_mapping_and_row_input_order_do_not_change_digest(self) -> None:
        first = protected_rows_fixture()
        second = deepcopy(first)
        second["person_roles"] = list(reversed(second["person_roles"]))
        nested = second["imported_ocr_traces"][0]["parsed_payload"]
        self.assertIsInstance(nested, dict)
        second["imported_ocr_traces"][0]["parsed_payload"] = {
            "outer": {"beta": [True, None], "alpha": 1}
        }

        first_snapshot, *_ = capture_fixture(first)
        second_snapshot, *_ = capture_fixture(second)

        self.assertEqual(
            [(item.label, item.row_count, item.sha256) for item in first_snapshot.digests],
            [(item.label, item.row_count, item.sha256) for item in second_snapshot.digests],
        )

    def test_supported_scalar_types_are_deterministic_and_invalid_values_are_rejected(self) -> None:
        first = protected_rows_fixture()
        first["imported_research_records"][0]["metadata_json"] = {
            "none": None,
            "bool": True,
            "int": 7,
            "float": 1.25,
            "decimal": Decimal("12.340"),
            "string": "  preserve me  ",
            "uuid": UUID("12345678-1234-5678-1234-567812345678"),
            "date": date(2026, 7, 13),
            "datetime": datetime(2026, 7, 13, 5, 30, tzinfo=timezone(timedelta(hours=-5))),
            "enum": PersistedExample.READY,
            "bytes": b"\x00\xab\xff",
            "list": [3, 2, 1],
        }
        second = deepcopy(first)
        second["imported_research_records"][0]["metadata_json"]["datetime"] = datetime(
            2026, 7, 13, 10, 30, tzinfo=timezone.utc
        )
        first_snapshot, *_ = capture_fixture(first)
        second_snapshot, *_ = capture_fixture(second)
        first_digest = next(item for item in first_snapshot.digests if item.label == "imported_research_records")
        second_digest = next(item for item in second_snapshot.digests if item.label == "imported_research_records")
        self.assertEqual(first_digest, second_digest)

        invalid_values = (
            float("nan"),
            float("inf"),
            Decimal("NaN"),
            {1: "non-string nested key"},
            object(),
        )
        for invalid in invalid_values:
            with self.subTest(invalid=repr(invalid)):
                rows = protected_rows_fixture()
                rows["imported_research_records"][0]["metadata_json"] = invalid
                with self.assertRaises(InvariantViolation):
                    capture_fixture(rows)

    def test_historical_naive_timestamps_hash_deterministically_and_change_only_their_table(self) -> None:
        baseline_rows = historical_naive_timestamp_rows_fixture()
        baseline, *_ = capture_fixture(baseline_rows)
        repeated, *_ = capture_fixture(deepcopy(baseline_rows))
        self.assertEqual(baseline.digests, repeated.digests)
        baseline_hashes = {item.label: item.sha256 for item in baseline.digests}

        changes = (
            (
                "import_batches",
                "completed_at",
                datetime(2026, 6, 28, 8, 45, 31, 654322),
            ),
            (
                "import_jobs",
                "started_at",
                datetime(2026, 6, 28, 8, 16, 3, 333333),
            ),
        )
        for label, field, value in changes:
            with self.subTest(table=label, field=field):
                changed_rows = deepcopy(baseline_rows)
                changed_rows[label][0][field] = value
                changed, *_ = capture_fixture(changed_rows)
                changed_labels = [
                    item.label
                    for item in changed.digests
                    if item.sha256 != baseline_hashes[item.label]
                ]
                self.assertEqual(changed_labels, [label])

    def test_aware_and_naive_equal_clock_fields_use_distinct_canonical_types(self) -> None:
        naive_rows = historical_naive_timestamp_rows_fixture()
        aware_rows = deepcopy(naive_rows)
        naive_clock = naive_rows["import_batches"][0]["created_at"]
        self.assertIsInstance(naive_clock, datetime)
        aware_rows["import_batches"][0]["created_at"] = naive_clock.replace(
            tzinfo=timezone.utc
        )

        naive_snapshot, *_ = capture_fixture(naive_rows)
        aware_snapshot, *_ = capture_fixture(aware_rows)
        naive_digest = next(
            item for item in naive_snapshot.digests if item.label == "import_batches"
        )
        aware_digest = next(
            item for item in aware_snapshot.digests if item.label == "import_batches"
        )

        self.assertNotEqual(naive_digest.sha256, aware_digest.sha256)

    def test_each_protected_semantic_change_changes_only_its_exact_table_digest(self) -> None:
        baseline_rows = protected_rows_fixture()
        baseline, *_ = capture_fixture(baseline_rows)
        baseline_hashes = {item.label: item.sha256 for item in baseline.digests}
        mutations = (
            ("nested_payload", "imported_ocr_traces", "parsed_payload", {"outer": {"alpha": 9}}),
            ("nested_metadata", "imported_research_records", "metadata_json", {"nested": {"a": 2}}),
            ("raw_field", "person_roles", "raw_value", "changed raw role"),
            ("job", "import_jobs", "status", "FAILED"),
            ("trace", "imported_ocr_traces", "extracted_text", "changed OCR"),
            ("scientific", "scientific_productions", "normalized_title", "Changed title"),
            ("role_lock", "person_roles", "identity_locked", True),
            ("role_decider", "person_roles", "identity_decided_by", "different@example.edu"),
            (
                "role_decided_at",
                "person_roles",
                "identity_decided_at",
                datetime(2026, 7, 13, 1, 0, tzinfo=timezone.utc),
            ),
            ("author_lock", "scientific_production_authors", "identity_locked", False),
            (
                "author_decider",
                "scientific_production_authors",
                "identity_decided_by",
                "different@example.edu",
            ),
            (
                "author_decided_at",
                "scientific_production_authors",
                "identity_decided_at",
                datetime(2026, 7, 13, 2, 0, tzinfo=timezone.utc),
            ),
        )
        for name, label, field, value in mutations:
            with self.subTest(change=name):
                changed_rows = deepcopy(baseline_rows)
                changed_rows[label][0][field] = value
                changed, *_ = capture_fixture(changed_rows)
                changed_labels = [
                    item.label for item in changed.digests if item.sha256 != baseline_hashes[item.label]
                ]
                self.assertEqual(changed_labels, [label])

    def test_start_end_drift_names_exact_changed_table_and_migration_labels(self) -> None:
        before = protected_rows_fixture()
        after = deepcopy(before)
        after["import_jobs"][0]["status"] = "FAILED"
        connection = RecordingConnection(
            before,
            second_rows=after,
            first_migrations=("m1",),
            second_migrations=("m1", "m2"),
        )

        def session_factory(*, bind: RecordingConnection, autoflush: bool) -> RecordingSession:
            return RecordingSession(bind=bind, autoflush=autoflush)

        with (
            patch("app.services.human_review_invariants.Session", side_effect=session_factory),
            patch(
                "app.services.human_review_invariants.ValidatedReadService",
                side_effect=lambda session: RecordingReadService(
                    session,
                    participant_rows_fixture(),
                    participant_metric_values_fixture(),
                ),
            ),
            self.assertRaisesRegex(
                InvariantViolation,
                r"^B2B\.1 invariant drift detected: changed labels: import_jobs, migration_versions$",
            ),
        ):
            capture_b2b1_invariants(connection)  # type: ignore[arg-type]


class SnapshotComparisonTests(unittest.TestCase):
    def test_public_signatures_and_exception_base_are_exact(self) -> None:
        self.assertTrue(issubclass(InvariantViolation, RuntimeError))
        self.assertEqual(tuple(inspect.signature(snapshot_sha256).parameters), ("snapshot",))
        self.assertEqual(tuple(inspect.signature(capture_b2b1_invariants).parameters), ("connection",))
        self.assertEqual(
            tuple(inspect.signature(compare_b2b1_invariants).parameters),
            ("before", "after"),
        )
        capture_hints = inspect.get_annotations(capture_b2b1_invariants, eval_str=True)
        self.assertIs(capture_hints["connection"], Connection)
        self.assertIs(capture_hints["return"], B2B1InvariantSnapshotV1)

    def test_compare_ignores_capture_time(self) -> None:
        before = snapshot_fixture()
        after = snapshot_fixture(captured_at=before.captured_at + timedelta(days=1))

        compare_b2b1_invariants(before, after)
        self.assertEqual(snapshot_sha256(before), snapshot_sha256(after))

    def test_compare_rejects_each_change_with_exact_label(self) -> None:
        baseline = snapshot_fixture()
        changes: list[tuple[str, B2B1InvariantSnapshotV1]] = [
            ("schema_version", baseline.model_copy(update={"schema_version": 2})),
            (
                "migration_versions",
                baseline.model_copy(update={"migration_versions": baseline.migration_versions + ("new",)}),
            ),
            ("eligible_products", baseline.model_copy(update={"eligible_products": 3})),
        ]
        for index, digest in enumerate(baseline.digests):
            count_digests = list(baseline.digests)
            count_digests[index] = digest.model_copy(update={"row_count": digest.row_count + 1})
            changes.append(
                (
                    f"{digest.label}.row_count",
                    baseline.model_copy(update={"digests": tuple(count_digests)}),
                )
            )
            hash_digests = list(baseline.digests)
            hash_digests[index] = digest.model_copy(
                update={"sha256": ("f" if digest.sha256[0] != "f" else "e") + digest.sha256[1:]}
            )
            changes.append(
                (
                    f"{digest.label}.sha256",
                    baseline.model_copy(update={"digests": tuple(hash_digests)}),
                )
            )
        for field in METRIC_FIELDS:
            metrics = baseline.participant_metrics.model_copy(
                update={field: getattr(baseline.participant_metrics, field) + 1}
            )
            changes.append(
                (
                    f"participant_metrics.{field}",
                    baseline.model_copy(update={"participant_metrics": metrics}),
                )
            )

        for expected_label, changed in changes:
            with self.subTest(label=expected_label), self.assertRaisesRegex(
                InvariantViolation,
                rf"^B2B\.1 invariant mismatch: changed labels: {re.escape(expected_label)}$",
            ):
                compare_b2b1_invariants(baseline, changed)

    def test_compare_exhausts_structure_and_common_content_changes_in_one_pass(self) -> None:
        before = snapshot_fixture()
        digests_by_label = {digest.label: digest for digest in before.digests}
        changed_person_roles = digests_by_label["person_roles"].model_copy(
            update={"sha256": "d" * 64}
        )
        changed_productions = digests_by_label["scientific_productions"].model_copy(
            update={"row_count": 99, "sha256": "e" * 64}
        )
        remaining = [
            digest
            for digest in before.digests
            if digest.label
            not in {"person_roles", "scientific_productions", "imported_ocr_traces"}
        ]
        after = before.model_copy(
            update={
                "digests": (
                    digests_by_label["scientific_production_authors"],
                    changed_person_roles,
                    TableDigestV1(label="custom_added", row_count=9, sha256="c" * 64),
                    changed_productions,
                    *(
                        digest
                        for digest in remaining
                        if digest.label != "scientific_production_authors"
                    ),
                )
            }
        )
        expected_labels = (
            "digests.labels",
            "digests.removed.imported_ocr_traces",
            "digests.added.custom_added",
            "person_roles.sha256",
            "scientific_productions.row_count",
            "scientific_productions.sha256",
        )

        with self.assertRaises(InvariantViolation) as context:
            compare_b2b1_invariants(before, after)

        self.assertEqual(
            str(context.exception),
            "B2B.1 invariant mismatch: changed labels: " + ", ".join(expected_labels),
        )

    def test_snapshot_hash_changes_for_every_semantic_but_not_capture_time(self) -> None:
        baseline = snapshot_fixture()
        baseline_hash = snapshot_sha256(baseline)
        self.assertRegex(baseline_hash, r"^[0-9a-f]{64}$")
        semantic_changes = [
            baseline.model_copy(update={"schema_version": 2}),
            baseline.model_copy(update={"migration_versions": baseline.migration_versions + ("new",)}),
            baseline.model_copy(update={"eligible_products": baseline.eligible_products + 1}),
        ]
        for index, digest in enumerate(baseline.digests):
            for field, value in (
                ("row_count", digest.row_count + 1),
                ("sha256", ("f" if digest.sha256[0] != "f" else "e") + digest.sha256[1:]),
            ):
                digests = list(baseline.digests)
                digests[index] = digest.model_copy(update={field: value})
                semantic_changes.append(baseline.model_copy(update={"digests": tuple(digests)}))
        for field in METRIC_FIELDS:
            metrics = baseline.participant_metrics.model_copy(
                update={field: getattr(baseline.participant_metrics, field) + 1}
            )
            semantic_changes.append(baseline.model_copy(update={"participant_metrics": metrics}))

        for changed in semantic_changes:
            with self.subTest(changed=changed):
                self.assertNotEqual(snapshot_sha256(changed), baseline_hash)
        self.assertEqual(
            snapshot_sha256(baseline.model_copy(update={"captured_at": baseline.captured_at + timedelta(hours=1)})),
            baseline_hash,
        )


if __name__ == "__main__":
    unittest.main()
