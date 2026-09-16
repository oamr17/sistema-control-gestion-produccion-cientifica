from __future__ import annotations

import importlib
import json
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core import migrations as migration_registry
from app.core.migrations import run_migrations
from app.models.human_review_audit import AuditEvent
from app.models.human_review_enums import AuditEventType
from tests.support.postgres import isolated_postgres_schema, require_b2b1_test_database_url


MIGRATION_MODULE = "app.migrations.versions.20260713_0020_human_review_audit"
VERSION = "20260713_0020_human_review_audit"
DOWN_REVISION = "20260713_0019_human_review_projection"
PREDECESSOR_VERSIONS = (
    "20260628_0001_add_import_jobs_batch_id",
    "20260628_0002_import_batches_and_job_state",
    "20260628_0003_fix_import_batches_sequence",
    "20260628_0004_normalized_dashboard_status_fields",
    "20260628_0005_import_job_diagnostics",
    "20260628_0006_import_job_error_details",
    "20260628_0007_external_researchers_and_review_items",
    "20260628_0008_normalization_traceability",
    "20260629_0009_scientific_production_authors",
    "20260629_0010_person_roles",
    "20260629_0011_research_entities_and_pending_products",
    "20260629_0012_author_traceability",
    "20260710_0013_teacher_person_role_validation",
    "20260711_0014_dropbox_document_versioning",
    "20260711_0015_dropbox_revision_uniqueness",
    "20260712_0016_canonical_identity_fields",
    "20260713_0017_b2b_capabilities",
    "20260713_0018_human_review_core",
    DOWN_REVISION,
)
EXPECTED_INDEX_NAMES = {
    "pk_audit_events",
    "uq_audit_events_event_hash",
    "ix_audit_events_occurred_at",
    "ix_audit_events_review_item",
    "ix_audit_events_actor",
    "ix_audit_events_event_type",
    "ix_audit_events_aggregate",
    "ix_audit_events_correlation",
    "ix_audit_events_corrects",
}


class HumanReviewMigration0020PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_url = require_b2b1_test_database_url()

    def test_metadata_registry_order_and_uniqueness_are_exact(self) -> None:
        module = self._migration_module()
        self.assertEqual(
            (module.VERSION, module.revision, module.down_revision),
            (VERSION, VERSION, DOWN_REVISION),
        )
        self.assertTrue(callable(module.upgrade))
        self.assertTrue(callable(module.downgrade))
        self.assertTrue(callable(module.assert_schema))
        self.assertEqual(
            module.AUDIT_EVENT_TYPES,
            tuple(member.value for member in AuditEventType)[:-1],
        )

        versions = tuple(version for version, _upgrade in migration_registry.MIGRATIONS)
        self.assertEqual(versions[:-2], PREDECESSOR_VERSIONS + (VERSION,))
        self.assertEqual(
            versions[-2],
            "20260718_0021_human_review_scientific_decision_audit",
        )
        self.assertEqual(versions[-1], "20260827_0022_scoped_human_review_authorization")
        self.assertEqual(len(versions), 22)
        self.assertEqual(len(versions), len(set(versions)))
        self.assertIs(migration_registry.MIGRATIONS[-3][1], module.upgrade)

    def test_upgrade_schema_matches_approved_orm_and_has_all_indexes_and_trigger(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0020_schema") as engine:
            before = self._predecessor_snapshot(engine)
            module.upgrade(engine)
            with engine.connect() as connection:
                module.assert_schema(connection)
                columns = tuple(connection.execute(text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'audit_events'
                    ORDER BY ordinal_position
                    """
                )).scalars())
                index_names = set(connection.execute(text(
                    """
                    SELECT index_row.relname
                    FROM pg_index AS index_catalog
                    JOIN pg_class AS table_row ON table_row.oid = index_catalog.indrelid
                    JOIN pg_class AS index_row ON index_row.oid = index_catalog.indexrelid
                    JOIN pg_namespace AS namespace_row ON namespace_row.oid = table_row.relnamespace
                    WHERE namespace_row.nspname = current_schema()
                      AND table_row.relname = 'audit_events'
                    """
                )).scalars())
                trigger = connection.execute(text(
                    """
                    SELECT trigger_row.tgenabled, function_row.proname
                    FROM pg_trigger AS trigger_row
                    JOIN pg_class AS table_row ON table_row.oid = trigger_row.tgrelid
                    JOIN pg_namespace AS namespace_row ON namespace_row.oid = table_row.relnamespace
                    JOIN pg_proc AS function_row ON function_row.oid = trigger_row.tgfoid
                    WHERE namespace_row.nspname = current_schema()
                      AND table_row.relname = 'audit_events'
                      AND trigger_row.tgname = 'trg_audit_events_append_only'
                      AND NOT trigger_row.tgisinternal
                    """
                )).one()

            self.assertEqual(columns, tuple(AuditEvent.__table__.columns.keys()))
            self.assertEqual(index_names, EXPECTED_INDEX_NAMES)
            self.assertEqual(tuple(trigger), ("O", "b2b_reject_append_only_mutation"))
            self.assertEqual(self._predecessor_snapshot(engine), before)

    def test_closed_event_payload_actor_hash_and_correction_constraints(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0020_checks") as engine:
            module.upgrade(engine)
            self._insert_event(engine, event_hash="a" * 64)
            previous_self_id = uuid4()
            corrects_self_id = uuid4()

            invalid_cases = (
                {"id": uuid4(), "event_type": "unknown_event", "event_hash": "b" * 64},
                {"id": uuid4(), "actor_capability": "CAREER_MANAGER", "event_hash": "c" * 64},
                {"id": uuid4(), "payload": json.dumps(["not", "object"]), "event_hash": "d" * 64},
                {"id": uuid4(), "payload_version": 2, "event_hash": "e" * 64},
                {"id": uuid4(), "event_hash": "F" * 64},
                {"id": uuid4(), "event_hash": "f" * 63},
                {
                    "id": uuid4(),
                    "event_type": "audit_corrected",
                    "event_hash": "1" * 64,
                    "corrects_event_id": None,
                },
                {
                    "id": previous_self_id,
                    "event_hash": "2" * 64,
                    "previous_event_id": previous_self_id,
                },
                {
                    "id": corrects_self_id,
                    "event_hash": "3" * 64,
                    "corrects_event_id": corrects_self_id,
                },
            )
            for index, changes in enumerate(invalid_cases):
                with self.subTest(index=index, changes=changes):
                    parameters = self._event_parameters(**changes)
                    self._assert_rejected(engine, self._event_insert_sql(), parameters)

    def test_correction_is_a_new_linked_event_and_event_hash_is_unique(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0020_correct") as engine:
            module.upgrade(engine)
            original_id = self._insert_event(engine, event_hash="a" * 64)
            correction_id = self._insert_event(
                engine,
                event_type="audit_corrected",
                event_hash="b" * 64,
                previous_event_id=original_id,
                previous_event_hash="a" * 64,
                corrects_event_id=original_id,
                payload=json.dumps({
                    "kind": "audit_correction",
                    "schema_version": 1,
                    "corrects_event_id": str(original_id),
                }),
            )
            self._assert_rejected(
                engine,
                self._event_insert_sql(),
                self._event_parameters(id=uuid4(), event_hash="b" * 64),
            )
            with engine.connect() as connection:
                rows = tuple(connection.execute(
                    text(
                        "SELECT id, event_type, corrects_event_id, event_hash "
                        "FROM audit_events ORDER BY created_at, id"
                    )
                ))
            self.assertEqual(len(rows), 2)
            correction = next(row for row in rows if row.id == correction_id)
            original = next(row for row in rows if row.id == original_id)
            self.assertEqual(correction.corrects_event_id, original_id)
            self.assertEqual(correction.event_type, "audit_corrected")
            self.assertEqual(original.event_type, "case_backfilled")
            self.assertEqual(original.event_hash.strip(), "a" * 64)

    def test_append_only_trigger_allows_insert_and_rejects_update_delete_truncate(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0020_append") as engine:
            module.upgrade(engine)
            event_id = self._insert_event(engine, event_hash="a" * 64)
            for statement in (
                text("UPDATE audit_events SET aggregate_key = 'changed' WHERE id = :id"),
                text("DELETE FROM audit_events WHERE id = :id"),
                text("TRUNCATE TABLE audit_events"),
            ):
                with self.subTest(statement=str(statement)):
                    with self.assertRaisesRegex(DBAPIError, "append-only"):
                        with engine.begin() as connection:
                            connection.execute(statement, {"id": event_id})
            with engine.connect() as connection:
                row = connection.execute(
                    text("SELECT aggregate_key, COUNT(*) OVER () FROM audit_events WHERE id = :id"),
                    {"id": event_id},
                ).one()
            self.assertEqual(tuple(row), ("aggregate:one", 1))

    def test_assert_schema_rejects_disabled_trigger_and_missing_index(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0020_assert") as engine:
            module.upgrade(engine)
            with engine.begin() as connection:
                connection.execute(text(
                    "ALTER TABLE audit_events DISABLE TRIGGER trg_audit_events_append_only"
                ))
            with engine.connect() as connection:
                with self.assertRaisesRegex(RuntimeError, "0020 schema assertion failed"):
                    module.assert_schema(connection)
            with engine.begin() as connection:
                connection.execute(text(
                    "ALTER TABLE audit_events ENABLE TRIGGER trg_audit_events_append_only"
                ))
                connection.execute(text("DROP INDEX ix_audit_events_correlation"))
            with engine.connect() as connection:
                with self.assertRaisesRegex(RuntimeError, "0020 schema assertion failed"):
                    module.assert_schema(connection)

    def test_retention_contract_has_no_purge_function_or_extra_trigger(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0020_retain") as engine:
            module.upgrade(engine)
            with engine.connect() as connection:
                trigger_names = tuple(connection.execute(text(
                    """
                    SELECT trigger_row.tgname
                    FROM pg_trigger AS trigger_row
                    JOIN pg_class AS table_row ON table_row.oid = trigger_row.tgrelid
                    JOIN pg_namespace AS namespace_row ON namespace_row.oid = table_row.relnamespace
                    WHERE namespace_row.nspname = current_schema()
                      AND table_row.relname = 'audit_events'
                      AND NOT trigger_row.tgisinternal
                    ORDER BY trigger_row.tgname
                    """
                )).scalars())
                purge_functions = tuple(connection.execute(text(
                    """
                    SELECT function_row.proname
                    FROM pg_proc AS function_row
                    JOIN pg_namespace AS namespace_row
                      ON namespace_row.oid = function_row.pronamespace
                    WHERE namespace_row.nspname = current_schema()
                      AND function_row.proname ~ '(purge|delete).*audit|audit.*(purge|delete)'
                    ORDER BY function_row.proname
                    """
                )).scalars())
            self.assertEqual(trigger_names, ("trg_audit_events_append_only",))
            self.assertEqual(purge_functions, ())

    def test_upgrade_assertion_failure_rolls_back_and_is_not_registered(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0020_rollback") as engine:
            def sabotage(connection):
                connection.execute(text("DROP INDEX ix_audit_events_corrects"))
                raise RuntimeError("forced 0020 assertion failure")

            with patch.object(module, "assert_schema", side_effect=sabotage):
                with self.assertRaisesRegex(RuntimeError, "forced 0020 assertion failure"):
                    run_migrations(engine)
            self._assert_0020_absent(engine)
            self.assertEqual(self._applied_versions(engine), PREDECESSOR_VERSIONS)

    def test_runner_records_0020_then_authorized_0021_and_preserves_predecessors(self) -> None:
        successor = importlib.import_module(
            "app.migrations.versions."
            "20260718_0021_human_review_scientific_decision_audit"
        )
        target_index = next(
            index
            for index, (version, _upgrade) in enumerate(migration_registry.MIGRATIONS)
            if version == successor.VERSION
        )
        migrations_through_0021 = migration_registry.MIGRATIONS[: target_index + 1]
        with self._prepared_database("b2b1_0020_runner") as engine:
            before = self._predecessor_snapshot(engine)
            with patch.object(
                migration_registry,
                "MIGRATIONS",
                migrations_through_0021,
            ):
                self.assertEqual(run_migrations(engine), [VERSION, successor.VERSION])
                self.assertEqual(run_migrations(engine), [])
            self.assertEqual(
                self._applied_versions(engine),
                PREDECESSOR_VERSIONS + (VERSION, successor.VERSION),
            )
            self.assertEqual(self._predecessor_snapshot(engine), before)
            with engine.connect() as connection:
                successor.assert_schema(connection)

    def test_upgrade_downgrade_reupgrade_is_symmetric_and_preserves_decisions(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0020_cycle") as engine:
            item_id = self._insert_item(engine)
            decision_id = self._insert_decision(engine, item_id)
            before = self._predecessor_snapshot(engine)
            module.upgrade(engine)
            self._insert_event(
                engine,
                event_hash="a" * 64,
                review_item_id=item_id,
            )
            module.downgrade(engine)
            self._assert_0020_absent(engine)
            self.assertEqual(self._predecessor_snapshot(engine), before)
            with engine.connect() as connection:
                preserved = connection.execute(
                    text("SELECT id FROM review_decisions WHERE id = :id"),
                    {"id": decision_id},
                ).scalar_one()
            self.assertEqual(preserved, decision_id)
            module.upgrade(engine)
            with engine.connect() as connection:
                module.assert_schema(connection)

    def test_isolated_schema_cleanup_removes_audit_objects(self) -> None:
        module = self._migration_module()
        schema_name: str | None = None
        with self._prepared_database("b2b1_0020_clean") as engine:
            with engine.connect() as connection:
                schema_name = connection.execute(text("SELECT current_schema()" )).scalar_one()
            module.upgrade(engine)
        self.assertIsNotNone(schema_name)
        with isolated_postgres_schema(self.database_url, "b2b1_0020_probe") as probe_engine:
            with probe_engine.connect() as connection:
                exists = connection.execute(
                    text("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = :schema_name)"),
                    {"schema_name": schema_name},
                ).scalar_one()
            self.assertFalse(exists)

    @staticmethod
    def _migration_module():
        return importlib.import_module(MIGRATION_MODULE)

    @contextmanager
    def _prepared_database(self, prefix: str):
        with isolated_postgres_schema(self.database_url, prefix) as engine:
            with engine.begin() as connection:
                connection.execute(text(
                    """
                    CREATE TABLE users (
                        id INTEGER PRIMARY KEY,
                        role VARCHAR(40) NOT NULL,
                        marker VARCHAR(80) NOT NULL
                    )
                    """
                ))
                connection.execute(text(
                    """
                    INSERT INTO users (id, role, marker) VALUES
                        (1, 'FACULTY_ADMIN', 'preserve-one'),
                        (2, 'CAREER_MANAGER', 'preserve-career')
                    """
                ))
                connection.execute(text(
                    """
                    CREATE TABLE schema_migrations (
                        version VARCHAR(120) PRIMARY KEY,
                        applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                ))
                for version in PREDECESSOR_VERSIONS[:-3]:
                    connection.execute(
                        text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                        {"version": version},
                    )

            for module_name, version in (
                ("20260713_0017_b2b_capabilities", PREDECESSOR_VERSIONS[-3]),
                ("20260713_0018_human_review_core", PREDECESSOR_VERSIONS[-2]),
                ("20260713_0019_human_review_projection", PREDECESSOR_VERSIONS[-1]),
            ):
                module = importlib.import_module(f"app.migrations.versions.{module_name}")
                module.upgrade(engine)
                with engine.begin() as connection:
                    connection.execute(
                        text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                        {"version": version},
                    )
            yield engine

    @staticmethod
    def _event_parameters(**changes) -> dict[str, object]:
        parameters: dict[str, object] = {
            "id": uuid4(),
            "event_type": "case_backfilled",
            "aggregate_type": "review_item",
            "aggregate_key": "aggregate:one",
            "review_item_id": None,
            "actor_user_id": 1,
            "actor_identifier": "user:1",
            "actor_capability": "RESEARCH_MANAGER",
            "payload_schema": "audit.case_backfilled.v1",
            "payload_version": 1,
            "payload": json.dumps({"kind": "case_backfilled", "schema_version": 1}),
            "correlation_id": uuid4(),
            "request_id": None,
            "previous_event_id": None,
            "corrects_event_id": None,
            "previous_event_hash": None,
            "event_hash": "a" * 64,
        }
        parameters.update(changes)
        return parameters

    @staticmethod
    def _event_insert_sql():
        return text(
            """
            INSERT INTO audit_events (
                id, event_type, aggregate_type, aggregate_key, review_item_id,
                actor_user_id, actor_identifier, actor_capability,
                payload_schema, payload_version, payload, correlation_id,
                request_id, previous_event_id, corrects_event_id,
                previous_event_hash, event_hash
            ) VALUES (
                :id, :event_type, :aggregate_type, :aggregate_key, :review_item_id,
                :actor_user_id, :actor_identifier, :actor_capability,
                :payload_schema, :payload_version, CAST(:payload AS JSONB), :correlation_id,
                :request_id, :previous_event_id, :corrects_event_id,
                :previous_event_hash, :event_hash
            )
            """
        )

    @classmethod
    def _insert_event(cls, engine, **changes) -> UUID:
        parameters = cls._event_parameters(**changes)
        with engine.begin() as connection:
            connection.execute(cls._event_insert_sql(), parameters)
        return parameters["id"]

    @staticmethod
    def _stable_key(index: int = 0) -> str:
        return f"b2b:v1:person_identity:{index:064x}"

    @classmethod
    def _insert_item(cls, engine) -> UUID:
        item_id = uuid4()
        with engine.begin() as connection:
            connection.execute(text(
                """
                INSERT INTO review_items (
                    id, case_type, stable_target_key, target_table, target_pk,
                    document_key, source_revision, source_page, source_section,
                    row_or_block_id, field_path, raw_value_sha256, period_id,
                    relationship_key, case_status, scientific_status
                ) VALUES (
                    :id, 'person_identity', :stable_target_key, 'person_roles', 10,
                    'document:test.pdf', 'revision-1', 1, 'faculty',
                    'row-1', 'canonical_name', :raw_hash, 2026,
                    'relationship:1', 'pending', 'pending'
                )
                """
            ), {"id": item_id, "stable_target_key": cls._stable_key(), "raw_hash": "a" * 64})
        return item_id

    @staticmethod
    def _insert_decision(engine, item_id: UUID) -> UUID:
        decision_id = uuid4()
        with engine.begin() as connection:
            connection.execute(text(
                """
                INSERT INTO review_decisions (
                    id, review_item_id, sequence, decision_type, decision_lifecycle,
                    scope, payload_schema, payload_version, payload, reason,
                    actor_type, actor_user_id, actor_identifier, actor_capability,
                    expected_case_version, locks_projection
                ) VALUES (
                    :id, :review_item_id, 1, 'validated', 'proposed',
                    'record', 'review.decision.v1', 1, CAST(:payload AS JSONB),
                    'test decision', 'human', 1, 'user:1', 'RESEARCH_MANAGER', 1, FALSE
                )
                """
            ), {
                "id": decision_id,
                "review_item_id": item_id,
                "payload": json.dumps({"value": "accepted"}),
            })
        return decision_id

    @staticmethod
    def _predecessor_snapshot(engine) -> tuple[tuple[object, ...], ...]:
        with engine.connect() as connection:
            users = tuple(tuple(row) for row in connection.execute(
                text("SELECT id, role, marker FROM users ORDER BY id")
            ))
            capabilities = tuple(tuple(row) for row in connection.execute(text(
                "SELECT user_id, capability, is_active "
                "FROM user_b2b_capabilities ORDER BY user_id, capability"
            )))
            counts = tuple(connection.execute(text(
                """
                SELECT
                    (SELECT COUNT(*) FROM review_items),
                    (SELECT COUNT(*) FROM review_decisions),
                    (SELECT COUNT(*) FROM canonical_identities),
                    (SELECT COUNT(*) FROM person_aliases),
                    (SELECT COUNT(*) FROM field_overrides)
                """
            )).one())
        return users, capabilities, counts

    @staticmethod
    def _applied_versions(engine) -> tuple[str, ...]:
        with engine.connect() as connection:
            return tuple(connection.execute(text(
                "SELECT version FROM schema_migrations ORDER BY applied_at, version"
            )).scalars())

    def _assert_rejected(self, engine, statement, parameters=None) -> None:
        with self.assertRaises(DBAPIError):
            with engine.begin() as connection:
                connection.execute(statement, parameters or {})

    def _assert_0020_absent(self, engine) -> None:
        with engine.connect() as connection:
            table_name = connection.execute(text(
                "SELECT to_regclass(current_schema() || '.audit_events')::text"
            )).scalar_one()
            trigger_count = connection.execute(text(
                """
                SELECT COUNT(*)
                FROM pg_trigger AS trigger_row
                JOIN pg_class AS table_row ON table_row.oid = trigger_row.tgrelid
                JOIN pg_namespace AS namespace_row ON namespace_row.oid = table_row.relnamespace
                WHERE namespace_row.nspname = current_schema()
                  AND trigger_row.tgname = 'trg_audit_events_append_only'
                  AND NOT trigger_row.tgisinternal
                """
            )).scalar_one()
        self.assertIsNone(table_name)
        self.assertEqual(trigger_count, 0)


if __name__ == "__main__":
    unittest.main()
