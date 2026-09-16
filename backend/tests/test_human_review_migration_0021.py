from __future__ import annotations

import importlib
import json
import re
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core import migrations as migration_registry
from app.core.migrations import run_migrations
from app.models.human_review_enums import AuditEventType
from tests.support.postgres import isolated_postgres_schema, require_b2b1_test_database_url


MIGRATION_MODULE = (
    "app.migrations.versions."
    "20260718_0021_human_review_scientific_decision_audit"
)
VERSION = "20260718_0021_human_review_scientific_decision_audit"
DOWN_REVISION = "20260713_0020_human_review_audit"
EXPECTED_0020_EVENT_TYPES = (
    "case_backfilled",
    "locked_decision_imported",
    "identity_created",
    "alias_created",
    "override_created",
    "capability_assigned",
    "capability_revoked",
    "audit_corrected",
    "functional_reversion",
)
EXPECTED_0021_EVENT_TYPES = EXPECTED_0020_EVENT_TYPES + (
    "scientific_decision_applied",
)


class HumanReviewMigration0021PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_url = require_b2b1_test_database_url()

    def test_0020_rejects_scientific_decision_applied(self) -> None:
        with self._prepared_0020("b2b2_0021_red") as engine:
            self._assert_rejected(
                engine,
                event_type="scientific_decision_applied",
                payload_schema="audit.scientific_decision_applied.v1",
                payload=json.dumps({
                    "kind": "scientific_decision_applied",
                    "schema_version": 1,
                }),
            )

    def test_enum_declares_scientific_decision_applied(self) -> None:
        self.assertTrue(
            hasattr(AuditEventType, "SCIENTIFIC_DECISION_APPLIED"),
            "AuditEventType must declare the new scientific audit event",
        )
        self.assertEqual(
            AuditEventType.SCIENTIFIC_DECISION_APPLIED.value,
            "scientific_decision_applied",
        )

    def test_metadata_registry_enum_and_contract_are_exact(self) -> None:
        module = self._migration_module()
        self.assertEqual(
            (module.VERSION, module.revision, module.DOWN_REVISION, module.down_revision),
            (VERSION, VERSION, DOWN_REVISION, DOWN_REVISION),
        )
        self.assertEqual(module.AUDIT_EVENT_TYPES, EXPECTED_0021_EVENT_TYPES)
        self.assertEqual(
            AuditEventType.SCIENTIFIC_DECISION_APPLIED.value,
            "scientific_decision_applied",
        )
        versions = tuple(version for version, _upgrade in migration_registry.MIGRATIONS)
        self.assertEqual(versions[-3:-1], (DOWN_REVISION, VERSION))
        self.assertEqual(versions[-1], "20260827_0022_scoped_human_review_authorization")
        self.assertEqual(len(versions), 22)
        self.assertEqual(len(versions), len(set(versions)))
        self.assertIs(migration_registry.MIGRATIONS[-2][1], module.upgrade)

    def test_upgrade_changes_only_event_check_and_accepts_valid_event(self) -> None:
        module = self._migration_module()
        with self._prepared_0020("b2b2_0021_up") as engine:
            self._insert_event(engine, event_hash="a" * 64)
            before_catalog = self._catalog_snapshot(engine)
            before_rows = self._row_count_snapshot(engine)
            self.assertEqual(self._event_type_literals(engine), EXPECTED_0020_EVENT_TYPES)

            module.upgrade(engine)
            with engine.connect() as connection:
                module.assert_schema(connection)
            self.assertEqual(self._event_type_literals(engine), EXPECTED_0021_EVENT_TYPES)
            self.assertEqual(self._catalog_snapshot(engine), before_catalog)
            self.assertEqual(self._row_count_snapshot(engine), before_rows)

            connection = engine.connect()
            transaction = connection.begin()
            try:
                connection.execute(
                    self._event_insert_sql(),
                    self._event_parameters(
                        event_type="scientific_decision_applied",
                        payload_schema="audit.scientific_decision_applied.v1",
                        payload=json.dumps({
                            "kind": "scientific_decision_applied",
                            "schema_version": 1,
                            "decision_id": str(uuid4()),
                            "decision_type": "validated",
                            "previous_case_status": "pending",
                            "resulting_case_status": "resolved",
                            "kpi_effect": [],
                            "review_item_id": str(uuid4()),
                        }),
                        event_hash="b" * 64,
                    ),
                )
                inserted = connection.execute(text(
                    "SELECT event_type, payload_schema FROM audit_events "
                    "WHERE event_hash = :event_hash"
                ), {"event_hash": "b" * 64}).one()
                self.assertEqual(
                    tuple(inserted),
                    (
                        "scientific_decision_applied",
                        "audit.scientific_decision_applied.v1",
                    ),
                )
            finally:
                transaction.rollback()
                connection.close()

            self.assertEqual(self._row_count_snapshot(engine), before_rows)

    def test_assert_schema_rejects_semantically_weakened_event_check(self) -> None:
        module = self._migration_module()
        with self._prepared_0020("b2b2_0021_drift") as engine:
            module.upgrade(engine)
            literals = ", ".join(f"'{value}'" for value in EXPECTED_0021_EVENT_TYPES)
            with engine.begin() as connection:
                connection.execute(text(
                    "ALTER TABLE audit_events "
                    "DROP CONSTRAINT ck_audit_events_event_type"
                ))
                connection.execute(text(
                    "ALTER TABLE audit_events "
                    "ADD CONSTRAINT ck_audit_events_event_type "
                    f"CHECK (TRUE OR event_type IN ({literals}))"
                ))
            with engine.connect() as connection:
                with self.assertRaisesRegex(RuntimeError, "constraint definition differs"):
                    module.assert_schema(connection)

    def test_downgrade_restores_0020_then_reupgrade_and_runner_are_idempotent(self) -> None:
        module = self._migration_module()
        target_index = next(
            index
            for index, (version, _upgrade) in enumerate(migration_registry.MIGRATIONS)
            if version == VERSION
        )
        migrations_through_0021 = migration_registry.MIGRATIONS[: target_index + 1]
        with self._prepared_0020("b2b2_0021_cycle") as engine:
            before_catalog = self._catalog_snapshot(engine)
            before_rows = self._row_count_snapshot(engine)

            module.upgrade(engine)
            module.downgrade(engine)
            self.assertEqual(self._event_type_literals(engine), EXPECTED_0020_EVENT_TYPES)
            self._assert_rejected(
                engine,
                event_type="scientific_decision_applied",
                payload_schema="audit.scientific_decision_applied.v1",
            )
            self.assertEqual(self._catalog_snapshot(engine), before_catalog)
            self.assertEqual(self._row_count_snapshot(engine), before_rows)

            module.upgrade(engine)
            self.assertEqual(self._event_type_literals(engine), EXPECTED_0021_EVENT_TYPES)
            with patch.object(
                migration_registry,
                "MIGRATIONS",
                migrations_through_0021,
            ):
                self.assertEqual(run_migrations(engine), [VERSION])
                self.assertEqual(run_migrations(engine), [])
            self.assertEqual(self._catalog_snapshot(engine), before_catalog)
            self.assertEqual(self._row_count_snapshot(engine), before_rows)

    @staticmethod
    def _migration_module():
        return importlib.import_module(MIGRATION_MODULE)

    @contextmanager
    def _prepared_0020(self, prefix: str):
        with isolated_postgres_schema(self.database_url, prefix) as engine:
            with engine.begin() as connection:
                connection.execute(text(
                    "CREATE TABLE users ("
                    "id INTEGER PRIMARY KEY, role VARCHAR(40) NOT NULL, "
                    "marker VARCHAR(80) NOT NULL)"
                ))
                connection.execute(text(
                    "INSERT INTO users (id, role, marker) VALUES "
                    "(1, 'FACULTY_ADMIN', 'preserve-one'), "
                    "(2, 'CAREER_MANAGER', 'preserve-career')"
                ))
                connection.execute(text(
                    "CREATE TABLE schema_migrations ("
                    "version VARCHAR(120) PRIMARY KEY, "
                    "applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
                ))
                for version, _upgrade in migration_registry.MIGRATIONS:
                    if version == "20260713_0017_b2b_capabilities":
                        break
                    connection.execute(
                        text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                        {"version": version},
                    )

            for module_name in (
                "20260713_0017_b2b_capabilities",
                "20260713_0018_human_review_core",
                "20260713_0019_human_review_projection",
                "20260713_0020_human_review_audit",
            ):
                predecessor = importlib.import_module(
                    f"app.migrations.versions.{module_name}"
                )
                predecessor.upgrade(engine)
                with engine.begin() as connection:
                    connection.execute(
                        text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                        {"version": predecessor.VERSION},
                    )
            yield engine

    @staticmethod
    def _event_parameters(**changes) -> dict[str, object]:
        parameters: dict[str, object] = {
            "id": uuid4(),
            "event_type": "case_backfilled",
            "aggregate_type": "review_item",
            "aggregate_key": f"aggregate:{uuid4()}",
            "review_item_id": None,
            "actor_user_id": 1,
            "actor_identifier": "b2b2:task1",
            "actor_capability": "RESEARCH_MANAGER",
            "payload_schema": "audit.case_backfilled.v1",
            "payload_version": 1,
            "payload": json.dumps({"kind": "case_backfilled", "schema_version": 1}),
            "correlation_id": uuid4(),
            "request_id": None,
            "previous_event_id": None,
            "corrects_event_id": None,
            "previous_event_hash": None,
            "event_hash": uuid4().hex + uuid4().hex,
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
                :payload_schema, :payload_version, CAST(:payload AS JSONB),
                :correlation_id, :request_id, :previous_event_id,
                :corrects_event_id, :previous_event_hash, :event_hash
            )
            """
        )

    @classmethod
    def _insert_event(cls, engine, **changes) -> None:
        with engine.begin() as connection:
            connection.execute(
                cls._event_insert_sql(),
                cls._event_parameters(**changes),
            )

    def _assert_rejected(self, engine, **changes) -> None:
        with self.assertRaises(DBAPIError):
            with engine.begin() as connection:
                connection.execute(
                    self._event_insert_sql(),
                    self._event_parameters(**changes),
                )

    @staticmethod
    def _event_type_literals(engine) -> tuple[str, ...]:
        with engine.connect() as connection:
            definition = connection.execute(text(
                """
                SELECT pg_get_constraintdef(constraint_row.oid, true)
                FROM pg_constraint AS constraint_row
                JOIN pg_class AS table_row ON table_row.oid = constraint_row.conrelid
                JOIN pg_namespace AS namespace_row
                  ON namespace_row.oid = table_row.relnamespace
                WHERE namespace_row.nspname = current_schema()
                  AND table_row.relname = 'audit_events'
                  AND constraint_row.conname = 'ck_audit_events_event_type'
                """
            )).scalar_one()
        return tuple(re.findall(r"'([^']*)'", definition))

    @staticmethod
    def _catalog_snapshot(engine) -> tuple[tuple[object, ...], ...]:
        with engine.connect() as connection:
            rows = connection.execute(text(
                """
                SELECT object_type, object_name, definition
                FROM (
                    SELECT 'column' AS object_type,
                           table_name || '.' || column_name AS object_name,
                           data_type || ':' || is_nullable || ':' ||
                           COALESCE(character_maximum_length::text, '') AS definition
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                    UNION ALL
                    SELECT 'constraint', constraint_row.conname,
                           pg_get_constraintdef(constraint_row.oid, true)
                    FROM pg_constraint AS constraint_row
                    JOIN pg_class AS table_row
                      ON table_row.oid = constraint_row.conrelid
                    JOIN pg_namespace AS namespace_row
                      ON namespace_row.oid = table_row.relnamespace
                    WHERE namespace_row.nspname = current_schema()
                      AND constraint_row.conname <> 'ck_audit_events_event_type'
                    UNION ALL
                    SELECT 'index', index_row.relname,
                           pg_get_indexdef(index_row.oid)
                    FROM pg_index AS index_catalog
                    JOIN pg_class AS index_row
                      ON index_row.oid = index_catalog.indexrelid
                    JOIN pg_class AS table_row
                      ON table_row.oid = index_catalog.indrelid
                    JOIN pg_namespace AS namespace_row
                      ON namespace_row.oid = table_row.relnamespace
                    WHERE namespace_row.nspname = current_schema()
                    UNION ALL
                    SELECT 'trigger', trigger_row.tgname,
                           pg_get_triggerdef(trigger_row.oid, true)
                    FROM pg_trigger AS trigger_row
                    JOIN pg_class AS table_row
                      ON table_row.oid = trigger_row.tgrelid
                    JOIN pg_namespace AS namespace_row
                      ON namespace_row.oid = table_row.relnamespace
                    WHERE namespace_row.nspname = current_schema()
                      AND NOT trigger_row.tgisinternal
                ) AS catalog
                ORDER BY object_type, object_name, definition
                """
            ))
            return tuple(tuple(row) for row in rows)

    @staticmethod
    def _row_count_snapshot(engine) -> tuple[int, ...]:
        with engine.connect() as connection:
            return tuple(connection.execute(text(
                """
                SELECT
                    (SELECT COUNT(*) FROM users),
                    (SELECT COUNT(*) FROM user_b2b_capabilities),
                    (SELECT COUNT(*) FROM review_items),
                    (SELECT COUNT(*) FROM review_decisions),
                    (SELECT COUNT(*) FROM canonical_identities),
                    (SELECT COUNT(*) FROM person_aliases),
                    (SELECT COUNT(*) FROM field_overrides),
                    (SELECT COUNT(*) FROM audit_events)
                """
            )).one())


if __name__ == "__main__":
    unittest.main()
