from __future__ import annotations

import importlib
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest.mock import patch
from uuid import UUID, uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.core import migrations as migration_registry
from app.core.migrations import run_migrations
from app.models.human_review_enums import OverrideField
from app.models.human_review_projection import CanonicalIdentity, FieldOverride, PersonAlias
from tests.support.postgres import isolated_postgres_schema, require_b2b1_test_database_url


MIGRATION_MODULE = "app.migrations.versions.20260713_0019_human_review_projection"
VERSION = "20260713_0019_human_review_projection"
DOWN_REVISION = "20260713_0018_human_review_core"
LATER_VERSION = "20260713_0020_human_review_audit"
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
    DOWN_REVISION,
)
PROJECTION_TABLES = ("canonical_identities", "field_overrides", "person_aliases")
RAW_FIELD_PATHS = ("raw_name", "raw_author_name", "person_key", "parsed_payload")
FORBIDDEN_ALIAS_COLUMNS = {
    "role", "roles", "product", "products", "period_id", "scientific_status", "kpi",
}


class HumanReviewMigration0019PostgresTests(unittest.TestCase):
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
        self.assertEqual(module.OVERRIDE_FIELDS, tuple(member.value for member in OverrideField))

        versions = tuple(version for version, _upgrade in migration_registry.MIGRATIONS)
        expected_prefix = PREDECESSOR_VERSIONS + (VERSION,)
        self.assertEqual(versions[:len(expected_prefix)], expected_prefix)
        self.assertEqual(versions.count(VERSION), 1)
        self.assertEqual(len(set(versions)), len(versions))
        self.assertIs(migration_registry.MIGRATIONS[len(expected_prefix) - 1][1], module.upgrade)

    def test_upgrade_schema_matches_approved_orm_and_has_no_inheritance_columns(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0019_schema") as engine:
            before = self._predecessor_snapshot(engine)
            module.upgrade(engine)
            with engine.connect() as connection:
                module.assert_schema(connection)
                database_columns = {
                    table_name: tuple(connection.execute(
                        text(
                            """
                            SELECT column_name
                            FROM information_schema.columns
                            WHERE table_schema = current_schema()
                              AND table_name = :table_name
                            ORDER BY ordinal_position
                            """
                        ),
                        {"table_name": table_name},
                    ).scalars())
                    for table_name in PROJECTION_TABLES
                }

            self.assertEqual(
                database_columns["canonical_identities"],
                tuple(CanonicalIdentity.__table__.columns.keys()),
            )
            self.assertEqual(
                database_columns["person_aliases"],
                tuple(PersonAlias.__table__.columns.keys()),
            )
            self.assertEqual(
                database_columns["field_overrides"],
                tuple(FieldOverride.__table__.columns.keys()),
            )
            self.assertTrue(FORBIDDEN_ALIAS_COLUMNS.isdisjoint(database_columns["person_aliases"]))
            self.assertTrue(FORBIDDEN_ALIAS_COLUMNS.isdisjoint(database_columns["canonical_identities"]))
            self.assertEqual(self._predecessor_snapshot(engine), before)

    def test_canonical_identity_key_is_unique_and_supersession_fk_is_restrictive(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0019_identity") as engine:
            module.upgrade(engine)
            first_id = self._insert_identity(engine, key="canonical:person:one")
            second_id = self._insert_identity(engine, key="canonical:person:two")
            self._assert_rejected(
                engine,
                self._identity_insert_sql(),
                self._identity_parameters(key="canonical:person:one"),
            )
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE canonical_identities "
                        "SET status = 'superseded', superseded_by_id = :second_id WHERE id = :first_id"
                    ),
                    {"first_id": first_id, "second_id": second_id},
                )
            self._assert_rejected(
                engine,
                text("DELETE FROM canonical_identities WHERE id = :id"),
                {"id": second_id},
            )

    def test_active_alias_is_globally_unique_and_superseded_alias_can_be_reused(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0019_alias") as engine:
            module.upgrade(engine)
            item_id = self._insert_item(engine)
            decision_id = self._insert_decision(engine, item_id)
            first_identity_id = self._insert_identity(engine, key="canonical:person:one")
            second_identity_id = self._insert_identity(engine, key="canonical:person:two")
            first_alias_id = self._insert_alias(
                engine, first_identity_id, decision_id, normalized="jose alvarez"
            )
            self._assert_rejected(
                engine,
                self._alias_insert_sql(),
                self._alias_parameters(
                    second_identity_id, decision_id, normalized="jose alvarez"
                ),
            )
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE person_aliases SET status = 'superseded' WHERE id = :id"),
                    {"id": first_alias_id},
                )
            self._insert_alias(
                engine, second_identity_id, decision_id, normalized="jose alvarez"
            )

    def test_concurrent_duplicate_alias_requests_commit_exactly_one_active_alias(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0019_concur") as engine:
            module.upgrade(engine)
            item_id = self._insert_item(engine)
            decision_id = self._insert_decision(engine, item_id)
            identity_ids = (
                self._insert_identity(engine, key="canonical:person:one"),
                self._insert_identity(engine, key="canonical:person:two"),
            )
            barrier = threading.Barrier(2)

            def insert_alias(identity_id: UUID) -> str:
                parameters = self._alias_parameters(
                    identity_id,
                    decision_id,
                    normalized="concurrent alias",
                )
                barrier.wait(timeout=10)
                try:
                    with engine.begin() as connection:
                        connection.execute(self._alias_insert_sql(), parameters)
                except DBAPIError:
                    return "duplicate"
                return "inserted"

            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = tuple(executor.map(insert_alias, identity_ids))
            self.assertEqual(sorted(outcomes), ["duplicate", "inserted"])
            with engine.connect() as connection:
                active_count = connection.execute(text(
                    """
                    SELECT COUNT(*)
                    FROM person_aliases
                    WHERE alias_normalized = 'concurrent alias'
                      AND status = 'active'
                    """
                )).scalar_one()
            self.assertEqual(active_count, 1)

    def test_override_active_uniqueness_uses_complete_scope_context(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0019_override") as engine:
            module.upgrade(engine)
            item_id = self._insert_item(engine)
            decision_id = self._insert_decision(engine, item_id)
            parameters = self._override_parameters(item_id, decision_id)
            self._insert_override(engine, **parameters)
            self._assert_rejected(engine, self._override_insert_sql(), parameters)

            inactive_parameters = dict(parameters, id=uuid4(), is_active=False)
            self._insert_override(engine, **inactive_parameters)
            document_parameters = dict(
                parameters,
                id=uuid4(),
                scope="document",
                document_key="document:other.pdf",
            )
            self._insert_override(engine, **document_parameters)

    def test_raw_field_paths_and_invalid_value_shapes_are_rejected_by_database(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0019_fields") as engine:
            module.upgrade(engine)
            item_id = self._insert_item(engine)
            decision_id = self._insert_decision(engine, item_id)
            for raw_field_path in RAW_FIELD_PATHS:
                with self.subTest(field_path=raw_field_path):
                    parameters = self._override_parameters(
                        item_id, decision_id, id=uuid4(), field_path=raw_field_path
                    )
                    self._assert_rejected(engine, self._override_insert_sql(), parameters)

            for marker, changes in (
                ("scalar_json", {"id": uuid4(), "projected_value": json.dumps("raw")}),
                ("wrong_version", {"id": uuid4(), "value_version": 2}),
                ("wrong_target", {"id": uuid4(), "target_table": "raw_import_rows"}),
            ):
                with self.subTest(marker=marker):
                    parameters = self._override_parameters(item_id, decision_id, **changes)
                    self._assert_rejected(engine, self._override_insert_sql(), parameters)

    def test_override_scope_context_is_closed_and_global_scope_is_identity_only(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0019_scope") as engine:
            module.upgrade(engine)
            item_id = self._insert_item(engine)
            decision_id = self._insert_decision(engine, item_id)
            invalid_contexts = (
                {"scope": "record", "document_key": "document:unexpected"},
                {"scope": "document", "document_key": "   "},
                {"scope": "document", "document_key": "document:one", "period_id": 2026},
                {"scope": "period", "period_id": None},
                {"scope": "relationship", "relationship_key": "   "},
                {
                    "scope": "relationship",
                    "relationship_key": "relation:one",
                    "document_key": "document:unexpected",
                },
                {"scope": "global_identity", "field_path": "product_title"},
                {"scope": "global_identity", "field_path": "canonical_name", "period_id": 2026},
            )
            for index, changes in enumerate(invalid_contexts):
                with self.subTest(index=index, changes=changes):
                    parameters = self._override_parameters(
                        item_id, decision_id, id=uuid4(), **changes
                    )
                    self._assert_rejected(engine, self._override_insert_sql(), parameters)

    def test_upgrade_assertion_failure_rolls_back_and_is_not_registered(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0019_rollback") as engine:
            def sabotage(connection):
                connection.execute(text("DROP INDEX ix_field_overrides_target"))
                raise RuntimeError("forced 0019 assertion failure")

            with patch.object(module, "assert_schema", side_effect=sabotage):
                with self.assertRaisesRegex(RuntimeError, "forced 0019 assertion failure"):
                    run_migrations(engine)
            self._assert_0019_absent(engine)
            self.assertEqual(self._applied_versions(engine), PREDECESSOR_VERSIONS)

    def test_runner_records_0019_once_and_preserves_0017_and_0018(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0019_runner") as engine:
            before = self._predecessor_snapshot(engine)
            with patch.object(
                migration_registry, "MIGRATIONS", migration_registry.MIGRATIONS[:19]
            ):
                self.assertEqual(run_migrations(engine), [VERSION])
                self.assertEqual(run_migrations(engine), [])
            self.assertEqual(self._applied_versions(engine), PREDECESSOR_VERSIONS + (VERSION,))
            self.assertEqual(self._predecessor_snapshot(engine), before)
            with engine.connect() as connection:
                module.assert_schema(connection)

    def test_downgrade_blocks_human_decisions_outside_disposable_database(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0019_guard") as engine:
            module.upgrade(engine)
            item_id = self._insert_item(engine)
            self._insert_decision(engine, item_id)
            nondisposable_engine = self._engine_for_same_schema(
                engine, application_name="b2b1_non_disposable_test"
            )
            try:
                with self.assertRaisesRegex(RuntimeError, "human review decisions"):
                    module.downgrade(nondisposable_engine)
            finally:
                nondisposable_engine.dispose()
            with engine.connect() as connection:
                module.assert_schema(connection)

    def test_disposable_upgrade_downgrade_reupgrade_cycle_is_symmetric(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0019_cycle") as engine:
            module.upgrade(engine)
            item_id = self._insert_item(engine)
            self._insert_decision(engine, item_id)
            before_downgrade = self._predecessor_snapshot(engine)
            module.downgrade(engine)
            self._assert_0019_absent(engine)
            self.assertEqual(self._predecessor_snapshot(engine), before_downgrade)
            self.assertEqual(self._applied_versions(engine), PREDECESSOR_VERSIONS)
            module.upgrade(engine)
            with engine.connect() as connection:
                module.assert_schema(connection)

    def test_downgrade_rejects_recorded_0020_without_changes(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0019_later") as engine:
            module.upgrade(engine)
            with engine.begin() as connection:
                connection.execute(
                    text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                    {"version": LATER_VERSION},
                )
            with self.assertRaisesRegex(RuntimeError, LATER_VERSION):
                module.downgrade(engine)
            with engine.connect() as connection:
                module.assert_schema(connection)

    def test_isolated_schema_cleanup_removes_every_projection_object(self) -> None:
        module = self._migration_module()
        schema_name: str | None = None
        with self._prepared_database("b2b1_0019_clean") as engine:
            with engine.connect() as connection:
                schema_name = connection.execute(text("SELECT current_schema()" )).scalar_one()
            module.upgrade(engine)
        self.assertIsNotNone(schema_name)
        admin_engine = create_engine(self.database_url, pool_pre_ping=True)
        try:
            with admin_engine.connect() as connection:
                exists = connection.execute(
                    text("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = :schema_name)"),
                    {"schema_name": schema_name},
                ).scalar_one()
            self.assertFalse(exists)
        finally:
            admin_engine.dispose()

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
                for version in PREDECESSOR_VERSIONS[:-2]:
                    connection.execute(
                        text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                        {"version": version},
                    )

            capability_module = importlib.import_module(
                "app.migrations.versions.20260713_0017_b2b_capabilities"
            )
            capability_module.upgrade(engine)
            with engine.begin() as connection:
                connection.execute(
                    text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                    {"version": PREDECESSOR_VERSIONS[-2]},
                )

            core_module = importlib.import_module(
                "app.migrations.versions.20260713_0018_human_review_core"
            )
            core_module.upgrade(engine)
            with engine.begin() as connection:
                connection.execute(
                    text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                    {"version": DOWN_REVISION},
                )
            yield engine

    def _engine_for_same_schema(self, engine, *, application_name: str):
        with engine.connect() as connection:
            schema_name = connection.execute(text("SELECT current_schema()" )).scalar_one()
        base_url = make_url(self.database_url).set(query={})
        return create_engine(
            base_url,
            pool_pre_ping=True,
            connect_args={
                "options": (
                    f"-csearch_path={schema_name} -capplication_name={application_name}"
                )
            },
        )

    @staticmethod
    def _stable_key(index: int = 0) -> str:
        return f"b2b:v1:person_identity:{index:064x}"

    @classmethod
    def _insert_item(cls, engine) -> UUID:
        item_id = uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
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
                ),
                {
                    "id": item_id,
                    "stable_target_key": cls._stable_key(),
                    "raw_hash": "a" * 64,
                },
            )
        return item_id

    @staticmethod
    def _insert_decision(engine, item_id: UUID) -> UUID:
        decision_id = uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO review_decisions (
                        id, review_item_id, sequence, decision_type, decision_lifecycle,
                        scope, payload_schema, payload_version, payload, reason,
                        actor_type, actor_user_id, actor_identifier, actor_capability,
                        expected_case_version, locks_projection
                    ) VALUES (
                        :id, :review_item_id, 1, 'validated', 'proposed',
                        'record', 'review.decision.v1', 1,
                        CAST(:payload AS JSONB), 'test decision',
                        'human', 1, 'user:1', 'RESEARCH_MANAGER', 1, FALSE
                    )
                    """
                ),
                {
                    "id": decision_id,
                    "review_item_id": item_id,
                    "payload": json.dumps({"value": "accepted"}),
                },
            )
        return decision_id

    @staticmethod
    def _identity_parameters(*, key: str, identity_id: UUID | None = None) -> dict[str, object]:
        return {
            "id": identity_id or uuid4(),
            "canonical_identity_key": key,
            "identity_type": "internal_person",
            "display_name": "Jose Alvarez",
            "status": "active",
            "origin": "human",
        }

    @staticmethod
    def _identity_insert_sql():
        return text(
            """
            INSERT INTO canonical_identities (
                id, canonical_identity_key, identity_type, display_name, status, origin
            ) VALUES (
                :id, :canonical_identity_key, :identity_type, :display_name, :status, :origin
            )
            """
        )

    @classmethod
    def _insert_identity(cls, engine, *, key: str) -> UUID:
        parameters = cls._identity_parameters(key=key)
        with engine.begin() as connection:
            connection.execute(cls._identity_insert_sql(), parameters)
        return parameters["id"]

    @staticmethod
    def _alias_parameters(
        canonical_identity_id: UUID,
        decision_id: UUID,
        *,
        normalized: str,
        alias_id: UUID | None = None,
    ) -> dict[str, object]:
        return {
            "id": alias_id or uuid4(),
            "alias_original": "Jose Alvarez",
            "alias_normalized": normalized,
            "alias_class": "person_name",
            "canonical_identity_id": canonical_identity_id,
            "decision_id": decision_id,
            "scope": "global_identity",
            "status": "active",
        }

    @staticmethod
    def _alias_insert_sql():
        return text(
            """
            INSERT INTO person_aliases (
                id, alias_original, alias_normalized, alias_class,
                canonical_identity_id, decision_id, scope, status
            ) VALUES (
                :id, :alias_original, :alias_normalized, :alias_class,
                :canonical_identity_id, :decision_id, :scope, :status
            )
            """
        )

    @classmethod
    def _insert_alias(
        cls,
        engine,
        canonical_identity_id: UUID,
        decision_id: UUID,
        *,
        normalized: str,
    ) -> UUID:
        parameters = cls._alias_parameters(
            canonical_identity_id, decision_id, normalized=normalized
        )
        with engine.begin() as connection:
            connection.execute(cls._alias_insert_sql(), parameters)
        return parameters["id"]

    @classmethod
    def _override_parameters(
        cls,
        item_id: UUID,
        decision_id: UUID,
        **changes,
    ) -> dict[str, object]:
        parameters: dict[str, object] = {
            "id": uuid4(),
            "review_item_id": item_id,
            "decision_id": decision_id,
            "stable_target_key": cls._stable_key(),
            "target_table": "person_roles",
            "target_pk": 10,
            "field_path": "canonical_name",
            "value_schema": "override.scalar.v1",
            "value_version": 1,
            "projected_value": json.dumps({"string_value": "Corrected Name"}),
            "scope": "record",
            "document_key": None,
            "period_id": None,
            "relationship_key": None,
            "locked": True,
            "is_active": True,
        }
        parameters.update(changes)
        return parameters

    @staticmethod
    def _override_insert_sql():
        return text(
            """
            INSERT INTO field_overrides (
                id, review_item_id, decision_id, stable_target_key,
                target_table, target_pk, field_path, value_schema, value_version,
                projected_value, scope, document_key, period_id, relationship_key,
                locked, is_active
            ) VALUES (
                :id, :review_item_id, :decision_id, :stable_target_key,
                :target_table, :target_pk, :field_path, :value_schema, :value_version,
                CAST(:projected_value AS JSONB), :scope, :document_key, :period_id,
                :relationship_key, :locked, :is_active
            )
            """
        )

    @classmethod
    def _insert_override(cls, engine, **parameters) -> UUID:
        with engine.begin() as connection:
            connection.execute(cls._override_insert_sql(), parameters)
        return parameters["id"]

    @staticmethod
    def _predecessor_snapshot(engine) -> tuple[tuple[object, ...], ...]:
        with engine.connect() as connection:
            users = tuple(tuple(row) for row in connection.execute(
                text("SELECT id, role, marker FROM users ORDER BY id")
            ))
            capabilities = tuple(tuple(row) for row in connection.execute(
                text(
                    "SELECT user_id, capability, is_active "
                    "FROM user_b2b_capabilities ORDER BY user_id, capability"
                )
            ))
            review_counts = connection.execute(
                text(
                    "SELECT (SELECT COUNT(*) FROM review_items), "
                    "(SELECT COUNT(*) FROM review_decisions)"
                )
            ).one()
        return users, capabilities, tuple(review_counts)

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

    def _assert_0019_absent(self, engine) -> None:
        with engine.connect() as connection:
            tables = tuple(connection.execute(
                text(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = current_schema()
                      AND table_name IN (
                          'canonical_identities', 'person_aliases', 'field_overrides'
                      )
                    ORDER BY table_name
                    """
                )
            ).scalars())
        self.assertEqual(tables, ())


if __name__ == "__main__":
    unittest.main()
