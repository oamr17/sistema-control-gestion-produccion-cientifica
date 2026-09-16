from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from psycopg.errors import InsufficientPrivilege
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core import migrations as migration_registry
from tests.support.postgres import (
    create_pre_0022_domain_catalog,
    require_b2b1_test_database_url,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
SERVICE_MODULE = "app.services.human_review_audit"
SCHEMA_MODULE = "app.schemas.human_review_operations"
LOCK_VECTOR_MATERIAL = (
    '["b2b:audit-lock:v1","review_item",'
    '"b2b:v1:person_identity:0123456789abcdef"]'
)
LOCK_VECTOR_SHA256 = "66268e7e115a6b6b7d69d7ad7bf4e3c2d2572f04a8af34055f72c65f3662d393"
LOCK_VECTOR_BIGINT = 7360727313091816299
MIGRATION_HASHES = {
    "20260713_0017_b2b_capabilities.py":
        "d7c495f0271ff989abc25b3b446781441ce68d72069c0c5ac8cece91847608b6",
    "20260713_0018_human_review_core.py":
        "f5deec37ecc277f0cdb6ba4e413a6f4c0912aeb901b9ce56d80cd9deecc9100e",
    "20260713_0019_human_review_projection.py":
        "6741935a9326001b317517928eb7281d06de5463a8e035283b66bad1676fcd1c",
    "20260713_0020_human_review_audit.py":
        "6d6dae32afe775e81ee6d5aca1172304676eb111ad2c756f6b50fdfb9ca41d4b",
}


def _api(testcase: unittest.TestCase):
    try:
        service = importlib.import_module(SERVICE_MODULE)
        schemas = importlib.import_module(SCHEMA_MODULE)
        required = (
            "compute_audit_event_hash",
            "append_audit_event",
            "append_audit_correction",
        )
        for name in required:
            getattr(service, name)
        for name in (
            "AuditPayloadV1",
            "AuditEventCommandV1",
            "AuditCorrectionCommandV1",
            "CaseBackfilledAuditPayloadV1",
            "LockedDecisionImportedAuditPayloadV1",
            "IdentityCreatedAuditPayloadV1",
            "AliasCreatedAuditPayloadV1",
            "OverrideCreatedAuditPayloadV1",
            "CapabilityChangedAuditPayloadV1",
            "AuditCorrectionPayloadV1",
            "FunctionalReversalAuditPayloadV1",
            "ScientificDecisionAppliedAuditPayloadV1",
        ):
            getattr(schemas, name)
        return service, schemas
    except (ModuleNotFoundError, AttributeError) as exc:
        testcase.fail(f"Task 9 audit service is absent or incomplete: {exc}")
        raise AssertionError("unreachable")


def _case_payload(schemas, *, suffix: str = "0"):
    return schemas.CaseBackfilledAuditPayloadV1(
        kind="case_backfilled",
        schema_version=1,
        review_item_id=uuid4(),
        case_type="person_identity",
        stable_target_key=f"b2b:v1:person_identity:{suffix:0>64}",
    )


def _event_command(
    schemas,
    *,
    event_id: UUID | None = None,
    aggregate_type: str = "review_item",
    aggregate_key: str = "aggregate:unit",
    occurred_at: datetime | None = None,
    previous_event_id: UUID | None = None,
    payload=None,
):
    return schemas.AuditEventCommandV1(
        id=event_id or uuid4(),
        event_type="case_backfilled",
        aggregate_type=aggregate_type,
        aggregate_key=aggregate_key,
        review_item_id=None,
        actor_user_id=None,
        actor_identifier="task9:test",
        actor_capability=None,
        occurred_at=occurred_at or datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc),
        payload=payload or _case_payload(schemas),
        correlation_id=uuid4(),
        request_id=None,
        previous_event_id=previous_event_id,
        corrects_event_id=None,
    )


class HumanReviewAuditContractTests(unittest.TestCase):
    def test_exact_public_service_signatures_exist(self) -> None:
        service, _schemas = _api(self)
        expected = {
            "compute_audit_event_hash": ("command", "previous_hash"),
            "append_audit_event": ("db", "command"),
            "append_audit_event_at_current_head": ("db", "command"),
            "append_audit_correction": ("db", "command"),
        }
        for name, parameters in expected.items():
            with self.subTest(name=name):
                self.assertTrue(
                    hasattr(service, name),
                    f"missing public audit operation: {name}",
                )
                if not hasattr(service, name):
                    continue
                signature = inspect.signature(getattr(service, name))
                self.assertEqual(tuple(signature.parameters), parameters)

    def test_payloads_are_closed_discriminated_versioned_and_event_matched(self) -> None:
        _service, schemas = _api(self)
        adapter = TypeAdapter(schemas.AuditPayloadV1)
        payload = _case_payload(schemas)
        self.assertIsInstance(
            adapter.validate_python(payload.model_dump(mode="json")),
            schemas.CaseBackfilledAuditPayloadV1,
        )
        for changes in (
            {"unexpected": True},
            {"schema_version": 2},
            {"kind": "unknown_audit_kind"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                adapter.validate_python(payload.model_dump(mode="json") | changes)

        command_data = _event_command(schemas).model_dump(mode="json")
        with self.assertRaises(ValidationError):
            schemas.AuditEventCommandV1.model_validate(command_data | {"event_hash": "a" * 64})
        with self.assertRaises(ValidationError):
            schemas.AuditEventCommandV1.model_validate(
                command_data | {"event_type": "identity_created"}
            )

    def test_scientific_decision_payload_is_closed_typed_and_hashes_with_exact_schema(self) -> None:
        service, schemas = _api(self)
        payload_type = getattr(schemas, "ScientificDecisionAppliedAuditPayloadV1")
        payload = payload_type(
            kind="scientific_decision_applied",
            schema_version=1,
            decision_id=uuid4(),
            decision_type="validated",
            previous_case_status="pending",
            resulting_case_status="resolved",
            kpi_effect=({"metric": "eligible_products", "before": 2, "after": 3, "delta": 1},),
            review_item_id=uuid4(),
        )
        adapter = TypeAdapter(schemas.AuditPayloadV1)
        self.assertIsInstance(
            adapter.validate_python(payload.model_dump(mode="json")),
            payload_type,
        )
        for changes in (
            {"unexpected": True},
            {"schema_version": 2},
            {"kpi_effect": [{"metric": "eligible_products", "before": 2, "after": 3, "delta": 1, "extra": 0}]},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                adapter.validate_python(payload.model_dump(mode="json") | changes)

        command = schemas.AuditEventCommandV1(
            id=uuid4(),
            event_type="scientific_decision_applied",
            aggregate_type="review_item",
            aggregate_key="aggregate:scientific",
            review_item_id=payload.review_item_id,
            actor_user_id=1,
            actor_identifier="b2b2:task1",
            actor_capability="RESEARCH_MANAGER",
            occurred_at=datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
            payload=payload,
            correlation_id=uuid4(),
            request_id=None,
            previous_event_id=None,
            corrects_event_id=None,
        )
        self.assertEqual(
            service._payload_schema(command.event_type),
            "audit.scientific_decision_applied.v1",
        )
        self.assertRegex(service.compute_audit_event_hash(command, None), r"^[0-9a-f]{64}$")

        incompatible = _event_command(schemas).model_dump(mode="json") | {
            "event_type": "scientific_decision_applied"
        }
        with self.assertRaises(ValidationError):
            schemas.AuditEventCommandV1.model_validate(incompatible)

    def test_scientific_decision_payload_requires_matching_review_item(self) -> None:
        _service, schemas = _api(self)
        payload = schemas.ScientificDecisionAppliedAuditPayloadV1(
            kind="scientific_decision_applied",
            schema_version=1,
            decision_id=uuid4(),
            decision_type="validated",
            previous_case_status="pending",
            resulting_case_status="resolved",
            kpi_effect=(),
            review_item_id=uuid4(),
        )
        command = {
            "id": uuid4(),
            "event_type": "scientific_decision_applied",
            "aggregate_type": "review_item",
            "aggregate_key": "aggregate:scientific",
            "review_item_id": payload.review_item_id,
            "actor_user_id": 1,
            "actor_identifier": "b2b2:task1",
            "actor_capability": "RESEARCH_MANAGER",
            "occurred_at": datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
            "payload": payload,
            "correlation_id": uuid4(),
            "request_id": None,
            "previous_event_id": None,
            "corrects_event_id": None,
        }
        schemas.AuditEventCommandV1.model_validate(command)
        for envelope_review_item_id in (None, uuid4()):
            with self.subTest(review_item_id=envelope_review_item_id):
                with self.assertRaises(ValidationError):
                    schemas.AuditEventCommandV1.model_validate(
                        command | {"review_item_id": envelope_review_item_id}
                    )

    def test_lock_key_derivation_matches_exact_vector_and_is_process_stable(self) -> None:
        service, _schemas = _api(self)
        self.assertEqual(
            hashlib.sha256(LOCK_VECTOR_MATERIAL.encode("utf-8")).hexdigest(),
            LOCK_VECTOR_SHA256,
        )
        actual = service._advisory_lock_key(
            "review_item",
            "b2b:v1:person_identity:0123456789abcdef",
        )
        self.assertEqual(actual, LOCK_VECTOR_BIGINT)
        self.assertGreaterEqual(actual, -(2 ** 63))
        self.assertLessEqual(actual, 2 ** 63 - 1)
        self.assertNotEqual(actual, service._advisory_lock_key("review_item", "different"))
        self.assertNotEqual(actual, service._advisory_lock_key("different", "b2b:v1:person_identity:0123456789abcdef"))

        code = (
            "from app.services.human_review_audit import _advisory_lock_key; "
            "print(_advisory_lock_key('review_item', "
            "'b2b:v1:person_identity:0123456789abcdef'))"
        )
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(BACKEND_ROOT)
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=BACKEND_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(int(result.stdout.strip()), LOCK_VECTOR_BIGINT)

    def test_event_hash_is_deterministic_lowercase_and_protects_all_command_fields(self) -> None:
        service, schemas = _api(self)
        base = _event_command(schemas)
        base_hash = service.compute_audit_event_hash(base, None)
        self.assertEqual(base_hash, service.compute_audit_event_hash(base, None))
        self.assertRegex(base_hash, r"^[0-9a-f]{64}$")

        base_data = base.model_dump(mode="json")
        mutations = (
            {"id": str(uuid4())},
            {"aggregate_type": "canonical_identity"},
            {"aggregate_key": "aggregate:changed"},
            {"review_item_id": str(uuid4())},
            {"actor_user_id": 7},
            {"actor_identifier": "task9:changed"},
            {"actor_capability": "SYSTEM_ADMIN"},
            {"occurred_at": "2026-07-14T12:00:01Z"},
            {
                "payload": base.payload.model_dump(mode="json")
                | {"stable_target_key": f"b2b:v1:person_identity:{'f' * 64}"}
            },
            {"correlation_id": str(uuid4())},
            {"request_id": str(uuid4())},
        )
        for changes in mutations:
            with self.subTest(changes=tuple(changes)):
                changed = schemas.AuditEventCommandV1.model_validate(base_data | changes)
                self.assertNotEqual(
                    service.compute_audit_event_hash(changed, None),
                    base_hash,
                )

        previous_id = uuid4()
        linked = schemas.AuditEventCommandV1.model_validate(
            base_data | {"previous_event_id": str(previous_id)}
        )
        first_previous_hash = service.compute_audit_event_hash(linked, "a" * 64)
        self.assertNotEqual(first_previous_hash, base_hash)
        self.assertNotEqual(
            first_previous_hash,
            service.compute_audit_event_hash(linked, "b" * 64),
        )

        correction_payload = schemas.AuditCorrectionPayloadV1(
            kind="audit_correction",
            schema_version=1,
            corrects_event_id=base.id,
            reason="correct metadata",
            replacement_payload_schema="audit.case_backfilled.v1",
            replacement_payload_version=1,
            replacement_payload_sha256="c" * 64,
        )
        correction = schemas.AuditEventCommandV1.model_validate(
            base_data
            | {
                "id": str(uuid4()),
                "event_type": "audit_corrected",
                "payload": correction_payload.model_dump(mode="json"),
                "previous_event_id": str(base.id),
                "corrects_event_id": str(base.id),
            }
        )
        self.assertNotEqual(
            service.compute_audit_event_hash(correction, base_hash),
            base_hash,
        )

    def test_hash_rejects_malformed_or_structurally_inconsistent_previous_hash(self) -> None:
        service, schemas = _api(self)
        unlinked = _event_command(schemas)
        linked = _event_command(schemas, previous_event_id=uuid4())
        with self.assertRaisesRegex(ValueError, "previous"):
            service.compute_audit_event_hash(unlinked, "a" * 64)
        with self.assertRaisesRegex(ValueError, "previous"):
            service.compute_audit_event_hash(linked, None)
        for invalid in ("", "a" * 63, "A" * 64, "g" * 64):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "previous"):
                service.compute_audit_event_hash(linked, invalid)

    def test_source_uses_only_transaction_advisory_lock_and_no_local_lock(self) -> None:
        service, _schemas = _api(self)
        source = inspect.getsource(service)
        lowered = source.lower()
        self.assertIn("pg_advisory_xact_lock", lowered)
        for forbidden in (
            " for update",
            " for no key update",
            " for share",
            " for key share",
            "threading.lock",
            "multiprocessing.lock",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, lowered)
        native_hash_calls = tuple(
            node.lineno
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "hash"
        )
        self.assertEqual(native_hash_calls, ())

    def test_task1_registers_only_0021_and_preserves_approved_migrations(self) -> None:
        _service, _schemas = _api(self)
        migrations_dir = BACKEND_ROOT / "app/migrations/versions"
        self.assertEqual(
            tuple(path.name for path in migrations_dir.glob("*0021*")),
            ("20260718_0021_human_review_scientific_decision_audit.py",),
        )
        for name, expected_hash in MIGRATION_HASHES.items():
            with self.subTest(name=name):
                actual = hashlib.sha256((migrations_dir / name).read_bytes()).hexdigest()
                self.assertEqual(actual, expected_hash)
        self.assertEqual(len(migration_registry.MIGRATIONS), 22)
        self.assertEqual(len({item[0] for item in migration_registry.MIGRATIONS}), 22)
        self.assertEqual(
            migration_registry.MIGRATIONS[-1][0],
            "20260827_0022_scoped_human_review_authorization",
        )


class HumanReviewAuditPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_url = require_b2b1_test_database_url()
        suffix = uuid4().hex[:12]
        cls.owner_role = f"b2b1_owner_{suffix}"
        cls.app_role = f"b2b1_app_{suffix}"
        cls.schema_name = f"b2b1_audit_{suffix}"
        cls.owner_password = f"owner_{suffix}"
        cls.app_password = f"app_{suffix}"
        cls.admin_engine = create_engine(cls.database_url, pool_pre_ping=True)
        cls.owner_engine: Engine | None = None
        cls.app_engine: Engine | None = None
        try:
            with cls.admin_engine.begin() as connection:
                connection.execute(text(
                    f'CREATE ROLE "{cls.owner_role}" LOGIN PASSWORD '
                    f"'{cls.owner_password}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
                ))
                connection.execute(text(
                    f'CREATE ROLE "{cls.app_role}" LOGIN PASSWORD '
                    f"'{cls.app_password}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
                ))
                connection.execute(text(
                    f'CREATE SCHEMA "{cls.schema_name}" AUTHORIZATION "{cls.owner_role}"'
                ))
            cls.owner_engine = create_engine(
                cls._role_url(cls.owner_role, cls.owner_password, "task9_owner"),
                pool_pre_ping=True,
            )
            cls.app_engine = create_engine(
                cls._role_url(cls.app_role, cls.app_password, "task9_app"),
                pool_pre_ping=True,
            )
            cls._apply_b2b_migrations()
            privilege_module = importlib.import_module(
                "scripts.configure_human_review_privileges"
            )
            privilege_module.configure_human_review_privileges(
                cls.owner_engine,
                cls.app_role,
            )
        except Exception:
            cls._cleanup()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        cls._cleanup()

    @classmethod
    def _role_url(cls, role: str, password: str, application_name: str):
        return (
            make_url(cls.database_url)
            .set(username=role, password=password)
            .update_query_dict({
                "application_name": application_name,
                "options": f"-csearch_path={cls.schema_name}",
            })
        )

    @classmethod
    def _apply_b2b_migrations(cls) -> None:
        with cls.owner_engine.begin() as connection:
            create_pre_0022_domain_catalog(connection, seed_lock_rows=True)
            connection.execute(text(
                "ALTER TABLE users ADD COLUMN marker VARCHAR(80) NOT NULL"
            ))
            connection.execute(text(
                """
                CREATE TABLE schema_migrations (
                    version VARCHAR(120) PRIMARY KEY,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            ))
            connection.execute(text(
                "INSERT INTO users "
                "(id, email, full_name, hashed_password, role, career_id, is_active, marker) "
                "VALUES (1, 'reviewer@example.test', 'Task 9 Reviewer', 'not-used', "
                "'FACULTY_ADMIN', 1, TRUE, 'task9-reviewer')"
            ))
            for version, _upgrade in migration_registry.MIGRATIONS[:16]:
                connection.execute(
                    text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                    {"version": version},
                )
        for version, upgrade in migration_registry.MIGRATIONS[16:]:
            upgrade(cls.owner_engine)
            with cls.owner_engine.begin() as connection:
                connection.execute(
                    text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                    {"version": version},
                )

    @classmethod
    def _cleanup(cls) -> None:
        if getattr(cls, "app_engine", None) is not None:
            cls.app_engine.dispose()
        if getattr(cls, "owner_engine", None) is not None:
            cls.owner_engine.dispose()
        admin_engine = getattr(cls, "admin_engine", None)
        if admin_engine is None:
            return
        try:
            with admin_engine.begin() as connection:
                for role in (getattr(cls, "app_role", ""), getattr(cls, "owner_role", "")):
                    if role:
                        connection.execute(text(f'DROP OWNED BY "{role}" CASCADE'))
                schema = getattr(cls, "schema_name", "")
                if schema:
                    connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
                for role in (getattr(cls, "app_role", ""), getattr(cls, "owner_role", "")):
                    if role:
                        connection.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        finally:
            admin_engine.dispose()

    def _new_app_engine(self, application_name: str) -> Engine:
        return create_engine(
            self._role_url(self.app_role, self.app_password, application_name),
            pool_pre_ping=True,
        )

    def _command(
        self,
        schemas,
        *,
        aggregate_key: str,
        event_id: UUID | None = None,
        previous_event_id: UUID | None = None,
        occurred_at: datetime | None = None,
    ):
        return _event_command(
            schemas,
            event_id=event_id,
            aggregate_key=aggregate_key,
            previous_event_id=previous_event_id,
            occurred_at=occurred_at,
            payload=_case_payload(schemas, suffix=uuid4().hex),
        )

    def _wait_for_advisory(self, application_name: str, timeout: float = 3.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.admin_engine.connect() as connection:
                waiting = connection.execute(text(
                    """
                    SELECT COUNT(*)
                    FROM pg_stat_activity
                    WHERE datname = current_database()
                      AND application_name = :application_name
                      AND wait_event_type = 'Lock'
                      AND wait_event = 'advisory'
                    """
                ), {"application_name": application_name}).scalar_one()
            if waiting:
                return
            time.sleep(0.05)
        self.fail(f"{application_name} did not wait on an advisory lock")

    def _assert_insufficient(self, statement: str) -> None:
        with self.assertRaises(DBAPIError) as caught:
            with self.app_engine.begin() as connection:
                connection.execute(text(statement))
        self.assertIsInstance(caught.exception.orig, InsufficientPrivilege)

    def test_append_participates_in_transaction_flushes_and_never_commits_internally(self) -> None:
        service, schemas = _api(self)
        aggregate_key = f"transaction:{uuid4()}"
        command = self._command(schemas, aggregate_key=aggregate_key)
        session = Session(self.app_engine, expire_on_commit=False)
        try:
            event = service.append_audit_event(session, command)
            self.assertEqual(event.id, command.id)
            with self.owner_engine.connect() as connection:
                visible_before_commit = connection.execute(text(
                    "SELECT COUNT(*) FROM audit_events WHERE id = :id"
                ), {"id": command.id}).scalar_one()
            self.assertEqual(visible_before_commit, 0)
            session.commit()
        finally:
            session.close()
        with self.owner_engine.connect() as connection:
            row = connection.execute(text(
                """
                SELECT previous_event_id, previous_event_hash, event_hash, payload_schema
                FROM audit_events WHERE id = :id
                """
            ), {"id": command.id}).one()
        self.assertIsNone(row.previous_event_id)
        self.assertIsNone(row.previous_event_hash)
        self.assertRegex(row.event_hash.strip(), r"^[0-9a-f]{64}$")
        self.assertEqual(row.payload_schema, "audit.case_backfilled.v1")

    def test_append_at_current_head_resolves_first_and_later_event_with_one_lock_and_head_read(self) -> None:
        service, schemas = _api(self)
        aggregate_key = f"current-head:{uuid4()}"
        base_time = datetime(2026, 7, 14, 12, 30, tzinfo=timezone.utc)
        first_command = self._command(
            schemas,
            aggregate_key=aggregate_key,
            occurred_at=base_time,
        )
        second_command = self._command(
            schemas,
            aggregate_key=aggregate_key,
            occurred_at=base_time + timedelta(seconds=1),
        )

        with Session(self.app_engine, expire_on_commit=False) as session:
            with patch.object(
                service,
                "_acquire_aggregate_lock",
                wraps=service._acquire_aggregate_lock,
            ) as acquire_lock, patch.object(
                service,
                "_load_head",
                wraps=service._load_head,
            ) as load_head:
                first = service.append_audit_event_at_current_head(
                    session,
                    first_command,
                )
                self.assertEqual(acquire_lock.call_count, 1)
                self.assertEqual(load_head.call_count, 1)
            self.assertIsNone(first.previous_event_id)
            self.assertIsNone(first.previous_event_hash)
            session.commit()

        with Session(self.app_engine, expire_on_commit=False) as session:
            with patch.object(
                service,
                "_acquire_aggregate_lock",
                wraps=service._acquire_aggregate_lock,
            ) as acquire_lock, patch.object(
                service,
                "_load_head",
                wraps=service._load_head,
            ) as load_head:
                second = service.append_audit_event_at_current_head(
                    session,
                    second_command,
                )
                self.assertEqual(acquire_lock.call_count, 1)
                self.assertEqual(load_head.call_count, 1)
            self.assertEqual(second.previous_event_id, first.id)
            self.assertEqual(second.previous_event_hash, first.event_hash)
            session.commit()

        with self.owner_engine.connect() as connection:
            rows = tuple(connection.execute(text(
                """
                SELECT id, previous_event_id, previous_event_hash, event_hash
                FROM audit_events
                WHERE aggregate_type = 'review_item' AND aggregate_key = :aggregate_key
                ORDER BY occurred_at, id
                """
            ), {"aggregate_key": aggregate_key}))
        self.assertEqual(tuple(row.id for row in rows), (first.id, second.id))
        self.assertIsNone(rows[0].previous_event_id)
        self.assertEqual(rows[1].previous_event_id, rows[0].id)
        self.assertEqual(rows[1].previous_event_hash.strip(), rows[0].event_hash.strip())

    def test_append_at_current_head_rejects_caller_supplied_predecessor_before_locking(self) -> None:
        service, schemas = _api(self)
        command = self._command(
            schemas,
            aggregate_key=f"caller-predecessor:{uuid4()}",
            previous_event_id=uuid4(),
        )
        with Session(self.app_engine) as session, patch.object(
            service,
            "_acquire_aggregate_lock",
            wraps=service._acquire_aggregate_lock,
        ) as acquire_lock, patch.object(
            service,
            "_load_head",
            wraps=service._load_head,
        ) as load_head:
            with self.assertRaisesRegex(ValueError, "previous_event_id"):
                service.append_audit_event_at_current_head(session, command)
            self.assertEqual(acquire_lock.call_count, 0)
            self.assertEqual(load_head.call_count, 0)
            session.rollback()

    def test_rejects_stale_cross_aggregate_and_corrupt_previous_chain(self) -> None:
        service, schemas = _api(self)
        base_time = datetime(2026, 7, 14, 13, 0, tzinfo=timezone.utc)
        aggregate_a = f"chain-a:{uuid4()}"
        aggregate_b = f"chain-b:{uuid4()}"
        with Session(self.app_engine, expire_on_commit=False) as session:
            first_a = service.append_audit_event(
                session,
                self._command(schemas, aggregate_key=aggregate_a, occurred_at=base_time),
            )
            session.commit()
            first_b = service.append_audit_event(
                session,
                self._command(schemas, aggregate_key=aggregate_b, occurred_at=base_time),
            )
            session.commit()

        stale = self._command(
            schemas,
            aggregate_key=aggregate_a,
            previous_event_id=uuid4(),
            occurred_at=base_time + timedelta(seconds=1),
        )
        cross = self._command(
            schemas,
            aggregate_key=aggregate_a,
            previous_event_id=first_b.id,
            occurred_at=base_time + timedelta(seconds=1),
        )
        for command in (stale, cross):
            with self.subTest(previous_event_id=command.previous_event_id):
                with Session(self.app_engine) as session:
                    with self.assertRaisesRegex(RuntimeError, "previous_event_id"):
                        service.append_audit_event(session, command)
                    session.rollback()

        corrupt_id = uuid4()
        with self.owner_engine.begin() as connection:
            connection.execute(text(
                """
                INSERT INTO audit_events (
                    id, event_type, aggregate_type, aggregate_key,
                    actor_identifier, payload_schema, payload_version, payload,
                    correlation_id, previous_event_id, previous_event_hash, event_hash,
                    occurred_at
                ) VALUES (
                    :id, 'case_backfilled', 'review_item', :aggregate_key,
                    'task9:corrupt-fixture', 'audit.case_backfilled.v1', 1,
                    CAST(:payload AS JSONB), :correlation_id, :previous_event_id,
                    :previous_event_hash, :event_hash, :occurred_at
                )
                """
            ), {
                "id": corrupt_id,
                "aggregate_key": aggregate_a,
                "payload": json.dumps({"kind": "case_backfilled", "schema_version": 1}),
                "correlation_id": uuid4(),
                "previous_event_id": first_a.id,
                "previous_event_hash": "0" * 64,
                "event_hash": "f" * 64,
                "occurred_at": base_time + timedelta(seconds=2),
            })
        next_command = self._command(
            schemas,
            aggregate_key=aggregate_a,
            previous_event_id=corrupt_id,
            occurred_at=base_time + timedelta(seconds=3),
        )
        with Session(self.app_engine) as session:
            with self.assertRaisesRegex(RuntimeError, "previous_event_hash"):
                service.append_audit_event(session, next_command)
            session.rollback()

    def test_correction_appends_new_event_and_preserves_original(self) -> None:
        service, schemas = _api(self)
        aggregate_key = f"correction:{uuid4()}"
        base_time = datetime(2026, 7, 14, 14, 0, tzinfo=timezone.utc)
        with Session(self.app_engine, expire_on_commit=False) as session:
            original = service.append_audit_event(
                session,
                self._command(schemas, aggregate_key=aggregate_key, occurred_at=base_time),
            )
            session.commit()
            head = service.append_audit_event(
                session,
                self._command(
                    schemas,
                    aggregate_key=aggregate_key,
                    previous_event_id=original.id,
                    occurred_at=base_time + timedelta(seconds=1),
                ),
            )
            session.commit()

        with self.owner_engine.connect() as connection:
            original_before = tuple(connection.execute(text(
                "SELECT * FROM audit_events WHERE id = :id"
            ), {"id": original.id}).one())

        correction_command = schemas.AuditCorrectionCommandV1(
            original_event_id=original.id,
            actor_user_id=1,
            actor_identifier="task9:reviewer",
            actor_capability="RESEARCH_MANAGER",
            reason="correct the audit description without mutation",
            replacement_payload_schema="audit.case_backfilled.v1",
            replacement_payload_version=1,
            replacement_payload_sha256="c" * 64,
            occurred_at=base_time + timedelta(seconds=2),
            correlation_id=uuid4(),
            request_id=uuid4(),
        )
        with Session(self.app_engine, expire_on_commit=False) as session:
            correction = service.append_audit_correction(session, correction_command)
            self.assertEqual(correction.corrects_event_id, original.id)
            self.assertEqual(correction.previous_event_id, head.id)
            session.commit()

        with self.owner_engine.connect() as connection:
            original_after = tuple(connection.execute(text(
                "SELECT * FROM audit_events WHERE id = :id"
            ), {"id": original.id}).one())
            correction_row = connection.execute(text(
                """
                SELECT event_type, aggregate_type, aggregate_key, previous_event_id,
                       corrects_event_id, payload
                FROM audit_events WHERE id = :id
                """
            ), {"id": correction.id}).one()
        self.assertEqual(original_after, original_before)
        self.assertEqual(correction_row.event_type, "audit_corrected")
        self.assertEqual(correction_row.aggregate_type, "review_item")
        self.assertEqual(correction_row.aggregate_key, aggregate_key)
        self.assertEqual(correction_row.previous_event_id, head.id)
        self.assertEqual(correction_row.corrects_event_id, original.id)
        self.assertEqual(correction_row.payload["corrects_event_id"], str(original.id))

    def test_same_aggregate_transactions_wait_then_form_one_linear_chain(self) -> None:
        service, schemas = _api(self)
        aggregate_key = f"same:{uuid4()}"
        base_time = datetime(2026, 7, 14, 15, 0, tzinfo=timezone.utc)
        first_command = self._command(
            schemas,
            aggregate_key=aggregate_key,
            occurred_at=base_time,
        )
        second_command = self._command(
            schemas,
            aggregate_key=aggregate_key,
            occurred_at=base_time + timedelta(seconds=1),
        )
        first_session = Session(self.app_engine, expire_on_commit=False)
        waiter_engine = self._new_app_engine("task9_same_waiter")
        started = threading.Event()

        def append_second() -> tuple[UUID, UUID | None, str | None]:
            with Session(waiter_engine, expire_on_commit=False) as session:
                started.set()
                event = service.append_audit_event_at_current_head(session, second_command)
                session.commit()
                return event.id, event.previous_event_id, event.previous_event_hash

        try:
            first = service.append_audit_event_at_current_head(
                first_session,
                first_command,
            )
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(append_second)
                self.assertTrue(started.wait(timeout=2))
                self._wait_for_advisory("task9_same_waiter")
                with self.assertRaises(FutureTimeout):
                    future.result(timeout=0.2)
                first_session.commit()
                second_id, previous_id, previous_hash = future.result(timeout=5)
            self.assertEqual(previous_id, first.id)
            self.assertEqual(previous_hash, first.event_hash)
        finally:
            first_session.rollback()
            first_session.close()
            waiter_engine.dispose()

        with self.owner_engine.connect() as connection:
            rows = tuple(connection.execute(text(
                """
                SELECT id, previous_event_id, previous_event_hash, event_hash
                FROM audit_events
                WHERE aggregate_type = 'review_item' AND aggregate_key = :aggregate_key
                ORDER BY occurred_at, id
                """
            ), {"aggregate_key": aggregate_key}))
            fork_count = connection.execute(text(
                """
                SELECT COUNT(*) FROM (
                    SELECT previous_event_id
                    FROM audit_events
                    WHERE aggregate_type = 'review_item'
                      AND aggregate_key = :aggregate_key
                      AND previous_event_id IS NOT NULL
                    GROUP BY previous_event_id
                    HAVING COUNT(*) > 1
                ) AS forks
                """
            ), {"aggregate_key": aggregate_key}).scalar_one()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].id, first_command.id)
        self.assertEqual(rows[1].id, second_id)
        self.assertEqual(rows[1].previous_event_id, rows[0].id)
        self.assertEqual(rows[1].previous_event_hash.strip(), rows[0].event_hash.strip())
        self.assertEqual(fork_count, 0)

    def test_distinct_aggregates_do_not_block_each_other(self) -> None:
        service, schemas = _api(self)
        base_time = datetime(2026, 7, 14, 16, 0, tzinfo=timezone.utc)
        aggregate_a = f"independent-a:{uuid4()}"
        aggregate_b = f"independent-b:{uuid4()}"
        first_command = self._command(
            schemas,
            aggregate_key=aggregate_a,
            occurred_at=base_time,
        )
        second_command = self._command(
            schemas,
            aggregate_key=aggregate_b,
            occurred_at=base_time,
        )
        self.assertNotEqual(
            service._advisory_lock_key("review_item", aggregate_a),
            service._advisory_lock_key("review_item", aggregate_b),
        )
        first_session = Session(self.app_engine, expire_on_commit=False)
        independent_engine = self._new_app_engine("task9_independent")

        def append_independent() -> UUID:
            with Session(independent_engine, expire_on_commit=False) as session:
                event = service.append_audit_event_at_current_head(session, second_command)
                session.commit()
                return event.id

        try:
            service.append_audit_event_at_current_head(first_session, first_command)
            with ThreadPoolExecutor(max_workers=1) as executor:
                second_id = executor.submit(append_independent).result(timeout=2)
            with self.owner_engine.connect() as connection:
                visible = connection.execute(text(
                    "SELECT COUNT(*) FROM audit_events WHERE id = :id"
                ), {"id": second_id}).scalar_one()
                first_visible = connection.execute(text(
                    "SELECT COUNT(*) FROM audit_events WHERE id = :id"
                ), {"id": first_command.id}).scalar_one()
            self.assertEqual(visible, 1)
            self.assertEqual(first_visible, 0)
            first_session.commit()
        finally:
            first_session.rollback()
            first_session.close()
            independent_engine.dispose()

    def test_rollback_releases_lock_and_leaves_no_partial_event(self) -> None:
        service, schemas = _api(self)
        aggregate_key = f"rollback:{uuid4()}"
        base_time = datetime(2026, 7, 14, 17, 0, tzinfo=timezone.utc)
        rolled_back_command = self._command(
            schemas,
            aggregate_key=aggregate_key,
            occurred_at=base_time,
        )
        surviving_command = self._command(
            schemas,
            aggregate_key=aggregate_key,
            occurred_at=base_time + timedelta(seconds=1),
        )
        first_session = Session(self.app_engine, expire_on_commit=False)
        waiter_engine = self._new_app_engine("task9_rollback_waiter")
        started = threading.Event()

        def append_after_rollback() -> UUID:
            with Session(waiter_engine, expire_on_commit=False) as session:
                started.set()
                event = service.append_audit_event_at_current_head(
                    session,
                    surviving_command,
                )
                session.commit()
                return event.id

        try:
            service.append_audit_event_at_current_head(
                first_session,
                rolled_back_command,
            )
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(append_after_rollback)
                self.assertTrue(started.wait(timeout=2))
                self._wait_for_advisory("task9_rollback_waiter")
                first_session.rollback()
                surviving_id = future.result(timeout=5)
        finally:
            first_session.rollback()
            first_session.close()
            waiter_engine.dispose()

        with self.owner_engine.connect() as connection:
            rows = tuple(connection.execute(text(
                """
                SELECT id, previous_event_id, previous_event_hash
                FROM audit_events
                WHERE aggregate_type = 'review_item' AND aggregate_key = :aggregate_key
                """
            ), {"aggregate_key": aggregate_key}))
        self.assertEqual(tuple(row.id for row in rows), (surviving_id,))
        self.assertIsNone(rows[0].previous_event_id)
        self.assertIsNone(rows[0].previous_event_hash)

    def test_application_role_keeps_exact_audit_permissions_with_advisory_lock(self) -> None:
        service, _schemas = _api(self)
        with self.app_engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": service._advisory_lock_key("permission", "probe")},
            )
            connection.execute(text("SELECT COUNT(*) FROM audit_events"))

        with self.admin_engine.connect() as connection:
            privileges = {
                privilege: bool(connection.execute(text(
                    "SELECT has_table_privilege(:role, :object_name, :privilege)"
                ), {
                    "role": self.app_role,
                    "object_name": f'"{self.schema_name}"."audit_events"',
                    "privilege": privilege,
                }).scalar_one())
                for privilege in (
                    "SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE",
                    "REFERENCES", "TRIGGER",
                )
            }
            app_owned = connection.execute(text(
                """
                SELECT
                    (SELECT COUNT(*) FROM pg_class WHERE relowner = role_row.oid)
                    + (SELECT COUNT(*) FROM pg_proc WHERE proowner = role_row.oid)
                    + (SELECT COUNT(*) FROM pg_namespace WHERE nspowner = role_row.oid)
                FROM pg_roles AS role_row WHERE role_row.rolname = :role
                """
            ), {"role": self.app_role}).scalar_one()
            grantable = connection.execute(text(
                """
                SELECT COUNT(*) FROM information_schema.role_table_grants
                WHERE grantee = :role AND is_grantable = 'YES'
                """
            ), {"role": self.app_role}).scalar_one()
            protected_execute = connection.execute(text(
                """
                SELECT COUNT(*)
                FROM pg_proc AS function_row
                JOIN pg_namespace AS namespace ON namespace.oid = function_row.pronamespace
                WHERE namespace.nspname = :schema_name
                  AND function_row.proname = ANY(:function_names)
                  AND has_function_privilege(
                      :role,
                      format('%I.%I()', namespace.nspname, function_row.proname),
                      'EXECUTE'
                  )
                """
            ), {
                "schema_name": self.schema_name,
                "function_names": [
                    "b2b_reject_career_capability",
                    "b2b_reject_career_role_with_capability",
                    "b2b_reject_append_only_mutation",
                ],
                "role": self.app_role,
            }).scalar_one()
        self.assertEqual(privileges, {
            "SELECT": True,
            "INSERT": True,
            "UPDATE": False,
            "DELETE": False,
            "TRUNCATE": False,
            "REFERENCES": False,
            "TRIGGER": False,
        })
        self.assertEqual(app_owned, 0)
        self.assertEqual(grantable, 0)
        self.assertEqual(protected_execute, 0)

        self._assert_insufficient("UPDATE audit_events SET actor_identifier = 'forbidden'")
        self._assert_insufficient("DELETE FROM audit_events")
        self._assert_insufficient("TRUNCATE TABLE audit_events")
        for clause in ("FOR UPDATE", "FOR NO KEY UPDATE", "FOR SHARE", "FOR KEY SHARE"):
            with self.subTest(clause=clause):
                self._assert_insufficient(f"SELECT id FROM audit_events {clause}")


if __name__ == "__main__":
    unittest.main()
