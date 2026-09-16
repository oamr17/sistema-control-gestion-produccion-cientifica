from __future__ import annotations

import importlib
import os
import threading
import time
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app.core import migrations as migration_registry
from app.core.migrations import run_migrations
from tests.support.postgres import isolated_postgres_schema, require_b2b1_test_database_url


MIGRATION_MODULE = "app.migrations.versions.20260713_0017_b2b_capabilities"
VERSION = "20260713_0017_b2b_capabilities"
DOWN_REVISION = "20260712_0016_canonical_identity_fields"

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
    DOWN_REVISION,
)

CAPABILITY_FUNCTION = "b2b_reject_career_capability"
CAPABILITY_TRIGGER = "trg_user_b2b_capabilities_reject_career"
USER_ROLE_FUNCTION = "b2b_reject_career_role_with_capability"
USER_ROLE_TRIGGER = "trg_users_reject_career_with_b2b_capability"

EXPECTED_COLUMNS = (
    ("id", "uuid", "uuid", None, "NO", None),
    ("user_id", "integer", "int4", None, "NO", None),
    ("capability", "character varying", "varchar", 40, "NO", None),
    ("is_active", "boolean", "bool", None, "NO", "true"),
    ("approval_reference", "character varying", "varchar", 240, "NO", None),
    ("approved_input_sha256", "character", "bpchar", 64, "NO", None),
    ("assigned_by_identifier", "character varying", "varchar", 180, "NO", None),
    (
        "assigned_at",
        "timestamp with time zone",
        "timestamptz",
        None,
        "NO",
        "CURRENT_TIMESTAMP",
    ),
    ("revoked_at", "timestamp with time zone", "timestamptz", None, "YES", None),
    ("revocation_reason", "text", "text", None, "YES", None),
    ("version", "integer", "int4", None, "NO", "1"),
)

EXPECTED_CONSTRAINTS = {
    "pk_user_b2b_capabilities": "p",
    "fk_user_b2b_capabilities_user_id_users": "f",
    "ck_user_b2b_capabilities_capability": "c",
    "ck_user_b2b_capabilities_hash": "c",
    "ck_user_b2b_capabilities_active_not_revoked": "c",
    "ck_user_b2b_capabilities_revoked_complete": "c",
}


class HumanReviewMigration0017PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_url = require_b2b1_test_database_url()

    def test_support_requires_explicit_postgresql_url_without_skip(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "may not skip"):
                require_b2b1_test_database_url()

        with patch.dict(os.environ, {"B2B1_TEST_DATABASE_URL": "sqlite:///:memory:"}):
            with self.assertRaisesRegex(RuntimeError, "must use PostgreSQL"):
                require_b2b1_test_database_url()

    def test_support_uses_exact_isolated_search_path_and_cleans_schema(self):
        schema_name: str
        with isolated_postgres_schema(self.database_url, "b2b1_0017_support") as engine:
            with engine.connect() as connection:
                schema_name = connection.execute(text("SELECT current_schema()" )).scalar_one()
                self.assertEqual(
                    connection.execute(text("SHOW search_path")).scalar_one(),
                    schema_name,
                )
                connection.execute(text("CREATE TABLE cleanup_probe (id INTEGER PRIMARY KEY)"))

        admin_engine = create_engine(self.database_url, pool_pre_ping=True)
        try:
            with admin_engine.connect() as connection:
                remaining = connection.execute(
                    text("SELECT COUNT(*) FROM pg_namespace WHERE nspname = :schema_name"),
                    {"schema_name": schema_name},
                ).scalar_one()
            self.assertEqual(remaining, 0)
        finally:
            admin_engine.dispose()

    def test_metadata_and_registry_order_are_exact(self):
        module = self._migration_module()

        self.assertEqual(module.VERSION, VERSION)
        self.assertEqual(module.revision, VERSION)
        self.assertEqual(module.down_revision, DOWN_REVISION)
        self.assertTrue(callable(module.upgrade))
        self.assertTrue(callable(module.downgrade))
        self.assertTrue(callable(module.assert_schema))

        versions = tuple(version for version, _upgrade in migration_registry.MIGRATIONS)
        self.assertEqual(versions[:17], PREDECESSOR_VERSIONS + (VERSION,))
        self.assertEqual(versions.count(VERSION), 1)
        self.assertIs(migration_registry.MIGRATIONS[16][1], module.upgrade)

    def test_upgrade_creates_exact_catalog_objects_and_zero_rows(self):
        module = self._migration_module()
        with self._prepared_database("b2b1_0017_catalog") as engine:
            module.upgrade(engine)

            with engine.connect() as connection:
                module.assert_schema(connection)
                columns = tuple(
                    tuple(row)
                    for row in connection.execute(
                        text(
                            """
                            SELECT column_name, data_type, udt_name,
                                   character_maximum_length, is_nullable, column_default
                            FROM information_schema.columns
                            WHERE table_schema = current_schema()
                              AND table_name = 'user_b2b_capabilities'
                            ORDER BY ordinal_position
                            """
                        )
                    )
                )
                constraints = {
                    row.conname: row.contype
                    for row in connection.execute(
                        text(
                            """
                            SELECT constraint_row.conname, constraint_row.contype
                            FROM pg_constraint AS constraint_row
                            JOIN pg_class AS table_row
                              ON table_row.oid = constraint_row.conrelid
                            JOIN pg_namespace AS namespace_row
                              ON namespace_row.oid = table_row.relnamespace
                            WHERE namespace_row.nspname = current_schema()
                              AND table_row.relname = 'user_b2b_capabilities'
                            """
                        )
                    )
                }
                indexes = {
                    row.indexname: row.indexdef
                    for row in connection.execute(
                        text(
                            """
                            SELECT indexname, indexdef
                            FROM pg_indexes
                            WHERE schemaname = current_schema()
                              AND tablename = 'user_b2b_capabilities'
                            """
                        )
                    )
                }
                functions = {
                    row.proname: (row.language_name, row.result_type)
                    for row in connection.execute(
                        text(
                            """
                            SELECT procedure_row.proname,
                                   language_row.lanname AS language_name,
                                   pg_catalog.format_type(procedure_row.prorettype, NULL) AS result_type
                            FROM pg_proc AS procedure_row
                            JOIN pg_namespace AS namespace_row
                              ON namespace_row.oid = procedure_row.pronamespace
                            JOIN pg_language AS language_row
                              ON language_row.oid = procedure_row.prolang
                            WHERE namespace_row.nspname = current_schema()
                              AND procedure_row.proname IN (:capability_function, :user_role_function)
                            """
                        ),
                        {
                            "capability_function": CAPABILITY_FUNCTION,
                            "user_role_function": USER_ROLE_FUNCTION,
                        },
                    )
                }
                triggers = {
                    row.trigger_name: (row.table_name, row.tgenabled, row.definition)
                    for row in connection.execute(
                        text(
                            """
                            SELECT trigger_row.tgname AS trigger_name,
                                   table_row.relname AS table_name,
                                   trigger_row.tgenabled,
                                   pg_get_triggerdef(trigger_row.oid, true) AS definition
                            FROM pg_trigger AS trigger_row
                            JOIN pg_class AS table_row ON table_row.oid = trigger_row.tgrelid
                            JOIN pg_namespace AS namespace_row
                              ON namespace_row.oid = table_row.relnamespace
                            WHERE namespace_row.nspname = current_schema()
                              AND NOT trigger_row.tgisinternal
                            """
                        )
                    )
                }
                row_count = connection.execute(
                    text("SELECT COUNT(*) FROM user_b2b_capabilities")
                ).scalar_one()

            self.assertEqual(columns, EXPECTED_COLUMNS)
            self.assertEqual(constraints, EXPECTED_CONSTRAINTS)
            self.assertEqual(
                set(indexes),
                {
                    "pk_user_b2b_capabilities",
                    "uq_user_b2b_capabilities_active_user",
                    "ix_user_b2b_capabilities_capability_active",
                },
            )
            self.assertIn("CREATE UNIQUE INDEX", indexes["uq_user_b2b_capabilities_active_user"])
            self.assertIn("(user_id)", indexes["uq_user_b2b_capabilities_active_user"])
            self.assertIn("WHERE is_active", indexes["uq_user_b2b_capabilities_active_user"])
            self.assertIn(
                "(capability, is_active)",
                indexes["ix_user_b2b_capabilities_capability_active"],
            )
            self.assertEqual(
                functions,
                {
                    CAPABILITY_FUNCTION: ("plpgsql", "trigger"),
                    USER_ROLE_FUNCTION: ("plpgsql", "trigger"),
                },
            )
            self.assertEqual(
                {name: (value[0], value[1]) for name, value in triggers.items()},
                {
                    CAPABILITY_TRIGGER: ("user_b2b_capabilities", "O"),
                    USER_ROLE_TRIGGER: ("users", "O"),
                },
            )
            self.assertIn(CAPABILITY_FUNCTION, triggers[CAPABILITY_TRIGGER][2])
            self.assertIn(USER_ROLE_FUNCTION, triggers[USER_ROLE_TRIGGER][2])
            self.assertEqual(row_count, 0)

    def test_valid_explicit_assignments_leave_user_roles_unchanged(self):
        module = self._migration_module()
        with self._prepared_database("b2b1_0017_explicit") as engine:
            module.upgrade(engine)
            self._insert_active_capability(engine, 1, "RESEARCH_MANAGER")
            self._insert_active_capability(engine, 2, "SYSTEM_ADMIN")

            with engine.connect() as connection:
                rows = connection.execute(
                    text(
                        """
                        SELECT users.id, users.role, capability_row.capability
                        FROM users
                        JOIN user_b2b_capabilities AS capability_row
                          ON capability_row.user_id = users.id
                        ORDER BY users.id
                        """
                    )
                ).all()

            self.assertEqual(
                rows,
                [
                    (1, "FACULTY_ADMIN", "RESEARCH_MANAGER"),
                    (2, "FACULTY_ADMIN", "SYSTEM_ADMIN"),
                ],
            )

    def test_career_manager_active_insert_and_activation_are_rejected(self):
        module = self._migration_module()
        with self._prepared_database("b2b1_0017_career") as engine:
            module.upgrade(engine)

            self._assert_statement_rejected(
                engine,
                self._active_insert_sql(),
                self._active_insert_parameters(3, "RESEARCH_MANAGER"),
            )
            inactive_id = self._insert_revoked_capability(engine, 3, "SYSTEM_ADMIN")
            self._assert_statement_rejected(
                engine,
                text(
                    """
                    UPDATE user_b2b_capabilities
                    SET is_active = true,
                        revoked_at = NULL,
                        revocation_reason = NULL,
                        version = version + 1
                    WHERE id = :capability_id
                    """
                ),
                {"capability_id": inactive_id},
            )

            with engine.connect() as connection:
                state = connection.execute(
                    text(
                        """
                        SELECT is_active, revoked_at IS NOT NULL, revocation_reason
                        FROM user_b2b_capabilities WHERE id = :capability_id
                        """
                    ),
                    {"capability_id": inactive_id},
                ).one()
            self.assertEqual(state, (False, True, "historical revocation"))

    def test_active_capability_blocks_changing_user_to_career(self):
        module = self._migration_module()
        with self._prepared_database("b2b1_0017_role") as engine:
            module.upgrade(engine)
            self._insert_active_capability(engine, 1, "RESEARCH_MANAGER")

            self._assert_statement_rejected(
                engine,
                text("UPDATE users SET role = 'CAREER_MANAGER' WHERE id = 1"),
            )
            with engine.connect() as connection:
                role = connection.execute(text("SELECT role FROM users WHERE id = 1")).scalar_one()
            self.assertEqual(role, "FACULTY_ADMIN")

    def test_revoked_capability_permits_later_career_role_without_reactivation(self):
        module = self._migration_module()
        with self._prepared_database("b2b1_0017_revoked") as engine:
            module.upgrade(engine)
            capability_id = self._insert_active_capability(engine, 1, "RESEARCH_MANAGER")
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE user_b2b_capabilities
                        SET is_active = false,
                            revoked_at = CURRENT_TIMESTAMP,
                            revocation_reason = 'scope ended',
                            version = version + 1
                        WHERE id = :capability_id
                        """
                    ),
                    {"capability_id": capability_id},
                )
                connection.execute(text("UPDATE users SET role = 'CAREER_MANAGER' WHERE id = 1"))

            with engine.connect() as connection:
                state = connection.execute(
                    text(
                        """
                        SELECT users.role, capability_row.is_active, capability_row.revoked_at IS NOT NULL,
                               capability_row.revocation_reason
                        FROM users
                        JOIN user_b2b_capabilities AS capability_row
                          ON capability_row.user_id = users.id
                        WHERE users.id = 1
                        """
                    )
                ).one()
            self.assertEqual(state, ("CAREER_MANAGER", False, True, "scope ended"))

    def test_duplicate_active_capability_per_user_is_rejected(self):
        module = self._migration_module()
        with self._prepared_database("b2b1_0017_unique") as engine:
            module.upgrade(engine)
            self._insert_active_capability(engine, 1, "RESEARCH_MANAGER")
            self._assert_statement_rejected(
                engine,
                self._active_insert_sql(),
                self._active_insert_parameters(1, "SYSTEM_ADMIN"),
            )

    def test_closed_capability_hash_and_revocation_shapes_are_enforced(self):
        module = self._migration_module()
        with self._prepared_database("b2b1_0017_checks") as engine:
            module.upgrade(engine)

            invalid_active_rows = (
                ("CAREER_MANAGER", "a" * 64, None, None),
                ("RESEARCH_MANAGER", "", None, None),
                ("RESEARCH_MANAGER", "a" * 63, None, None),
                ("RESEARCH_MANAGER", "a" * 65, None, None),
                ("RESEARCH_MANAGER", "A" * 64, None, None),
                ("RESEARCH_MANAGER", "g" * 64, None, None),
                ("RESEARCH_MANAGER", "a" * 64, "CURRENT_TIMESTAMP", None),
            )
            for capability, digest, revoked_at_expression, reason in invalid_active_rows:
                with self.subTest(capability=capability, digest=digest, active=True):
                    statement = self._active_insert_sql(
                        revoked_at_expression=revoked_at_expression,
                        revocation_reason_expression=":revocation_reason" if reason is not None else "NULL",
                    )
                    parameters = self._active_insert_parameters(2, capability, digest=digest)
                    parameters["revocation_reason"] = reason
                    self._assert_statement_rejected(engine, statement, parameters)

            invalid_revoked_rows = (
                (None, "revoked"),
                ("CURRENT_TIMESTAMP", None),
                ("CURRENT_TIMESTAMP", ""),
                ("CURRENT_TIMESTAMP", "   "),
            )
            for revoked_at_expression, reason in invalid_revoked_rows:
                with self.subTest(revoked_at=revoked_at_expression, reason=reason, active=False):
                    statement = text(
                        f"""
                        INSERT INTO user_b2b_capabilities (
                            id, user_id, capability, is_active, approval_reference,
                            approved_input_sha256, assigned_by_identifier, revoked_at,
                            revocation_reason
                        ) VALUES (
                            :id, 2, 'RESEARCH_MANAGER', false, 'approved-ref',
                            :digest, 'test-operator', {revoked_at_expression or 'NULL'},
                            :revocation_reason
                        )
                        """
                    )
                    self._assert_statement_rejected(
                        engine,
                        statement,
                        {"id": uuid4(), "digest": "a" * 64, "revocation_reason": reason},
                    )

    def test_named_foreign_key_restricts_user_delete(self):
        module = self._migration_module()
        with self._prepared_database("b2b1_0017_fk") as engine:
            module.upgrade(engine)
            self._insert_revoked_capability(engine, 1, "RESEARCH_MANAGER")

            self._assert_statement_rejected(engine, text("DELETE FROM users WHERE id = 1"))
            with engine.connect() as connection:
                self.assertEqual(
                    connection.execute(text("SELECT COUNT(*) FROM users WHERE id = 1")).scalar_one(),
                    1,
                )

    def test_upgrade_downgrade_reupgrade_removes_only_0017_objects(self):
        module = self._migration_module()
        with self._prepared_database("b2b1_0017_cycle") as engine:
            original_users = self._users(engine)
            original_versions = self._applied_versions(engine)

            module.upgrade(engine)
            self._insert_active_capability(engine, 1, "RESEARCH_MANAGER")
            module.downgrade(engine)

            self._assert_0017_objects_absent(engine)
            self.assertEqual(self._users(engine), original_users)
            self.assertEqual(self._applied_versions(engine), original_versions)

            module.upgrade(engine)
            with engine.connect() as connection:
                module.assert_schema(connection)
                self.assertEqual(
                    connection.execute(text("SELECT COUNT(*) FROM user_b2b_capabilities")).scalar_one(),
                    0,
                )

    def test_runner_records_0017_exactly_once_and_preserves_predecessors(self):
        with self._prepared_database("b2b1_0017_runner") as engine:
            original_users = self._users(engine)

            with patch.object(
                migration_registry, "MIGRATIONS", migration_registry.MIGRATIONS[:17]
            ):
                self.assertEqual(run_migrations(engine), [VERSION])
                self.assertEqual(run_migrations(engine), [])

            versions = self._applied_versions(engine)
            self.assertEqual(versions, PREDECESSOR_VERSIONS + (VERSION,))
            self.assertEqual(versions.count(VERSION), 1)
            self.assertEqual(self._users(engine), original_users)

    def test_assert_schema_failure_rolls_back_objects_and_prevents_registration(self):
        module = self._migration_module()
        with self._prepared_database("b2b1_0017_rollback") as engine:
            original_users = self._users(engine)
            with patch.object(
                module,
                "assert_schema",
                side_effect=RuntimeError("injected assert_schema failure"),
            ):
                with self.assertRaisesRegex(RuntimeError, "injected assert_schema failure"):
                    run_migrations(engine)

            self._assert_0017_objects_absent(engine)
            self.assertEqual(self._users(engine), original_users)
            self.assertEqual(self._applied_versions(engine), PREDECESSOR_VERSIONS)

    def test_role_to_career_then_active_insert_serializes_on_user_row(self):
        module = self._migration_module()
        with self._prepared_database("b2b1_0017_role_ins") as engine:
            module.upgrade(engine)
            for iteration in range(self._concurrency_iterations()):
                with self.subTest(iteration=iteration):
                    self._reset_concurrency_user(engine)
                    outcome = self._run_first_then_second(
                        engine,
                        text("UPDATE users SET role = 'CAREER_MANAGER' WHERE id = 1"),
                        {},
                        self._active_insert_sql(),
                        self._active_insert_parameters(1, "RESEARCH_MANAGER"),
                    )
                    final_state = self._user_capability_state(engine)
                    self.assertNotEqual(
                        final_state,
                        ("CAREER_MANAGER", True),
                        "FORBIDDEN_FINAL_CAREER_ACTIVE: role then insert",
                    )
                    self.assertEqual(outcome["wait_state"], "lock")
                    self.assertTrue(outcome["first_committed"])
                    self.assertFalse(outcome["second_committed"])
                    self.assertEqual(outcome["second_sqlstate"], "23514")
                    self.assertEqual(final_state, ("CAREER_MANAGER", False))

    def test_active_insert_then_role_to_career_serializes_on_user_row(self):
        module = self._migration_module()
        with self._prepared_database("b2b1_0017_ins_role") as engine:
            module.upgrade(engine)
            for iteration in range(self._concurrency_iterations()):
                with self.subTest(iteration=iteration):
                    self._reset_concurrency_user(engine)
                    outcome = self._run_first_then_second(
                        engine,
                        self._active_insert_sql(),
                        self._active_insert_parameters(1, "RESEARCH_MANAGER"),
                        text("UPDATE users SET role = 'CAREER_MANAGER' WHERE id = 1"),
                        {},
                    )
                    final_state = self._user_capability_state(engine)
                    self.assertNotEqual(
                        final_state,
                        ("CAREER_MANAGER", True),
                        "FORBIDDEN_FINAL_CAREER_ACTIVE: insert then role",
                    )
                    self.assertEqual(outcome["wait_state"], "lock")
                    self.assertTrue(outcome["first_committed"])
                    self.assertFalse(outcome["second_committed"])
                    self.assertEqual(outcome["second_sqlstate"], "23514")
                    self.assertEqual(final_state, ("FACULTY_ADMIN", True))

    def test_role_to_career_then_activation_serializes_on_user_row(self):
        module = self._migration_module()
        with self._prepared_database("b2b1_0017_role_act") as engine:
            module.upgrade(engine)
            for iteration in range(self._concurrency_iterations()):
                with self.subTest(iteration=iteration):
                    self._reset_concurrency_user(engine)
                    capability_id = self._insert_revoked_capability(
                        engine, 1, "RESEARCH_MANAGER"
                    )
                    outcome = self._run_first_then_second(
                        engine,
                        text("UPDATE users SET role = 'CAREER_MANAGER' WHERE id = 1"),
                        {},
                        self._activate_capability_sql(),
                        {"capability_id": capability_id},
                    )
                    final_state = self._user_capability_state(engine)
                    self.assertNotEqual(
                        final_state,
                        ("CAREER_MANAGER", True),
                        "FORBIDDEN_FINAL_CAREER_ACTIVE: role then activation",
                    )
                    self.assertEqual(outcome["wait_state"], "lock")
                    self.assertFalse(outcome["second_committed"])
                    self.assertEqual(outcome["second_sqlstate"], "23514")
                    self.assertEqual(final_state, ("CAREER_MANAGER", False))

    def test_activation_then_role_to_career_serializes_on_user_row(self):
        module = self._migration_module()
        with self._prepared_database("b2b1_0017_act_role") as engine:
            module.upgrade(engine)
            for iteration in range(self._concurrency_iterations()):
                with self.subTest(iteration=iteration):
                    self._reset_concurrency_user(engine)
                    capability_id = self._insert_revoked_capability(
                        engine, 1, "RESEARCH_MANAGER"
                    )
                    outcome = self._run_first_then_second(
                        engine,
                        self._activate_capability_sql(),
                        {"capability_id": capability_id},
                        text("UPDATE users SET role = 'CAREER_MANAGER' WHERE id = 1"),
                        {},
                    )
                    final_state = self._user_capability_state(engine)
                    self.assertNotEqual(
                        final_state,
                        ("CAREER_MANAGER", True),
                        "FORBIDDEN_FINAL_CAREER_ACTIVE: activation then role",
                    )
                    self.assertEqual(outcome["wait_state"], "lock")
                    self.assertFalse(outcome["second_committed"])
                    self.assertEqual(outcome["second_sqlstate"], "23514")
                    self.assertEqual(final_state, ("FACULTY_ADMIN", True))

    def test_revocation_then_role_to_career_serializes_and_permits_role(self):
        module = self._migration_module()
        with self._prepared_database("b2b1_0017_rev_role") as engine:
            module.upgrade(engine)
            for iteration in range(self._concurrency_iterations()):
                with self.subTest(iteration=iteration):
                    self._reset_concurrency_user(engine)
                    capability_id = self._insert_active_capability(
                        engine, 1, "RESEARCH_MANAGER"
                    )
                    outcome = self._run_first_then_second(
                        engine,
                        self._revoke_capability_sql(),
                        {"capability_id": capability_id},
                        text("UPDATE users SET role = 'CAREER_MANAGER' WHERE id = 1"),
                        {},
                    )
                    self.assertEqual(outcome["wait_state"], "lock")
                    self.assertTrue(outcome["first_committed"])
                    self.assertTrue(outcome["second_committed"])
                    self.assertIsNone(outcome["second_sqlstate"])
                    self.assertEqual(
                        self._user_capability_state(engine),
                        ("CAREER_MANAGER", False),
                    )

    def test_role_lock_then_revocation_serializes_without_deadlock(self):
        module = self._migration_module()
        with self._prepared_database("b2b1_0017_role_rev") as engine:
            module.upgrade(engine)
            for iteration in range(self._concurrency_iterations()):
                with self.subTest(iteration=iteration):
                    self._reset_concurrency_user(engine)
                    capability_id = self._insert_active_capability(
                        engine, 1, "RESEARCH_MANAGER"
                    )
                    outcome = self._run_role_lock_then_revocation(
                        engine, capability_id
                    )
                    self.assertEqual(outcome["wait_state"], "lock")
                    self.assertEqual(outcome["role_sqlstate"], "23514")
                    self.assertTrue(outcome["revocation_committed"])
                    self.assertEqual(
                        self._user_capability_state(engine),
                        ("FACULTY_ADMIN", False),
                    )

    def test_assert_schema_rejects_broadened_capability_check(self):
        module = self._migration_module()
        with self._prepared_database("b2b1_0017_mut_cap") as engine:
            module.upgrade(engine)
            self._assert_catalog_mutation_rejected(
                module,
                engine,
                (
                    "ALTER TABLE user_b2b_capabilities "
                    "DROP CONSTRAINT ck_user_b2b_capabilities_capability",
                    "ALTER TABLE user_b2b_capabilities ADD CONSTRAINT "
                    "ck_user_b2b_capabilities_capability CHECK "
                    "(capability IN ('RESEARCH_MANAGER', 'SYSTEM_ADMIN', 'UNAPPROVED'))",
                ),
                "BROADER_CHECK_ACCEPTED",
            )

    def test_assert_schema_rejects_weakened_check(self):
        module = self._migration_module()
        with self._prepared_database("b2b1_0017_mut_check") as engine:
            module.upgrade(engine)
            self._assert_catalog_mutation_rejected(
                module,
                engine,
                (
                    "ALTER TABLE user_b2b_capabilities "
                    "DROP CONSTRAINT ck_user_b2b_capabilities_active_not_revoked",
                    "ALTER TABLE user_b2b_capabilities ADD CONSTRAINT "
                    "ck_user_b2b_capabilities_active_not_revoked CHECK "
                    "((NOT is_active OR revoked_at IS NULL) OR TRUE)",
                ),
                "WEAKENED_CHECK_ACCEPTED",
            )

    def test_assert_schema_preserves_case_sensitive_constraint_literals(self):
        module = self._migration_module()
        with self._prepared_database("b2b1_0017_mut_case") as engine:
            module.upgrade(engine)
            self._assert_catalog_mutation_rejected(
                module,
                engine,
                (
                    "ALTER TABLE user_b2b_capabilities "
                    "DROP CONSTRAINT ck_user_b2b_capabilities_hash",
                    "ALTER TABLE user_b2b_capabilities ADD CONSTRAINT "
                    "ck_user_b2b_capabilities_hash CHECK "
                    "(approved_input_sha256 ~ '^[0-9A-F]{64}$')",
                ),
                "CASE_CHANGED_CONSTRAINT_ACCEPTED",
            )

    def test_assert_schema_rejects_changed_active_index_predicate(self):
        module = self._migration_module()
        with self._prepared_database("b2b1_0017_mut_pred") as engine:
            module.upgrade(engine)
            self._assert_catalog_mutation_rejected(
                module,
                engine,
                (
                    "DROP INDEX uq_user_b2b_capabilities_active_user",
                    "CREATE UNIQUE INDEX uq_user_b2b_capabilities_active_user "
                    "ON user_b2b_capabilities (user_id) "
                    "WHERE (is_active OR capability = 'SYSTEM_ADMIN')",
                ),
                "WIDENED_PREDICATE_ACCEPTED",
            )

    def test_assert_schema_rejects_noop_career_guard_function(self):
        module = self._migration_module()
        with self._prepared_database("b2b1_0017_mut_func") as engine:
            module.upgrade(engine)
            self._assert_catalog_mutation_rejected(
                module,
                engine,
                (
                    """
                    CREATE OR REPLACE FUNCTION b2b_reject_career_capability()
                    RETURNS TRIGGER LANGUAGE plpgsql AS $$
                    BEGIN
                        RETURN NEW;
                    END;
                    $$
                    """,
                ),
                "NOOP_FUNCTION_ACCEPTED",
            )

    def test_assert_schema_rejects_altered_same_name_trigger(self):
        module = self._migration_module()
        with self._prepared_database("b2b1_0017_mut_trig") as engine:
            module.upgrade(engine)
            self._assert_catalog_mutation_rejected(
                module,
                engine,
                (
                    "DROP TRIGGER trg_user_b2b_capabilities_reject_career "
                    "ON user_b2b_capabilities",
                    "CREATE TRIGGER trg_user_b2b_capabilities_reject_career "
                    "BEFORE INSERT OR UPDATE ON user_b2b_capabilities "
                    "FOR EACH STATEMENT EXECUTE FUNCTION b2b_reject_career_capability()",
                ),
                "ALTERED_TRIGGER_ACCEPTED",
            )

    def test_assert_schema_rejects_same_name_trigger_on_another_table_for_all_plans(self):
        module = self._migration_module()
        with self._prepared_database("b2b1_0017_dup_trig") as engine:
            module.upgrade(engine)
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        CREATE FUNCTION b2b_harmful_same_name_trigger()
                        RETURNS TRIGGER LANGUAGE plpgsql AS $$
                        BEGIN
                            NEW.role := 'CAREER_MANAGER';
                            RETURN NEW;
                        END;
                        $$
                        """
                    )
                )

            for iteration in range(self._collision_iterations()):
                with self.subTest(iteration=iteration):
                    with engine.begin() as connection:
                        connection.execute(
                            text(
                                """
                                CREATE TRIGGER trg_user_b2b_capabilities_reject_career
                                BEFORE UPDATE OF marker ON users
                                FOR EACH ROW
                                EXECUTE FUNCTION b2b_harmful_same_name_trigger()
                                """
                            )
                        )
                        connection.execute(
                            text(
                                """
                                DROP TRIGGER trg_user_b2b_capabilities_reject_career
                                ON user_b2b_capabilities
                                """
                            )
                        )
                        connection.execute(text(self._capability_trigger_ddl()))

                    accepted_plans = self._catalog_accepting_plans(module, engine)
                    if accepted_plans:
                        with engine.connect() as connection:
                            trigger_count = connection.execute(
                                text(
                                    """
                                    SELECT COUNT(*)
                                    FROM pg_trigger AS trigger_row
                                    JOIN pg_class AS table_row
                                      ON table_row.oid = trigger_row.tgrelid
                                    JOIN pg_namespace AS namespace_row
                                      ON namespace_row.oid = table_row.relnamespace
                                    WHERE namespace_row.nspname = current_schema()
                                      AND NOT trigger_row.tgisinternal
                                      AND trigger_row.tgname =
                                          'trg_user_b2b_capabilities_reject_career'
                                    """
                                )
                            ).scalar_one()
                        self._insert_active_capability(engine, 1, "RESEARCH_MANAGER")
                        with engine.begin() as connection:
                            connection.execute(
                                text("UPDATE users SET marker = 'harmful-fired' WHERE id = 1")
                            )
                        self.assertEqual(trigger_count, 2)
                        self.assertEqual(
                            self._user_capability_state(engine),
                            ("CAREER_MANAGER", True),
                        )
                        self.fail(
                            "HARMFUL_SAME_NAME_TRIGGER_ACCEPTED: "
                            f"plans={accepted_plans}, count={trigger_count}"
                        )

                    with engine.begin() as connection:
                        connection.execute(
                            text(
                                """
                                DROP TRIGGER trg_user_b2b_capabilities_reject_career
                                ON users
                                """
                            )
                        )

    def test_assert_schema_rejects_function_overload_for_all_plans(self):
        module = self._migration_module()
        with self._prepared_database("b2b1_0017_overload") as engine:
            module.upgrade(engine)

            for iteration in range(self._collision_iterations()):
                with self.subTest(iteration=iteration):
                    with engine.begin() as connection:
                        connection.execute(
                            text(
                                """
                                DROP TRIGGER trg_user_b2b_capabilities_reject_career
                                ON user_b2b_capabilities
                                """
                            )
                        )
                        connection.execute(
                            text("DROP FUNCTION b2b_reject_career_capability()")
                        )
                        connection.execute(
                            text(
                                """
                                CREATE FUNCTION b2b_reject_career_capability(integer)
                                RETURNS integer
                                LANGUAGE plpgsql
                                IMMUTABLE
                                AS $$
                                BEGIN
                                    RETURN $1 + 1;
                                END;
                                $$
                                """
                            )
                        )
                        connection.execute(text(self._capability_function_ddl()))
                        connection.execute(text(self._capability_trigger_ddl()))

                    accepted_plans = self._catalog_accepting_plans(module, engine)
                    if accepted_plans:
                        with engine.connect() as connection:
                            signatures = tuple(
                                connection.execute(
                                    text(
                                        """
                                        SELECT pg_get_function_identity_arguments(
                                                   procedure_row.oid
                                               ) AS identity_arguments
                                        FROM pg_proc AS procedure_row
                                        JOIN pg_namespace AS namespace_row
                                          ON namespace_row.oid = procedure_row.pronamespace
                                        WHERE namespace_row.nspname = current_schema()
                                          AND procedure_row.proname =
                                              'b2b_reject_career_capability'
                                        ORDER BY identity_arguments
                                        """
                                    )
                                ).scalars()
                            )
                        self.assertEqual(signatures, ("", "integer"))
                        self.fail(
                            "EXTRA_OVERLOAD_SEQPLAN_ACCEPTED: "
                            f"plans={accepted_plans}, signatures={signatures}"
                        )

                    with engine.begin() as connection:
                        connection.execute(
                            text(
                                "DROP FUNCTION b2b_reject_career_capability(integer)"
                            )
                        )

    @staticmethod
    def _migration_module():
        return importlib.import_module(MIGRATION_MODULE)

    @staticmethod
    def _concurrency_iterations() -> int:
        iterations = int(os.environ.get("B2B1_CONCURRENCY_ITERATIONS", "1"))
        if not 1 <= iterations <= 25:
            raise AssertionError("B2B1_CONCURRENCY_ITERATIONS must be between 1 and 25")
        return iterations

    @staticmethod
    def _collision_iterations() -> int:
        iterations = int(os.environ.get("B2B1_COLLISION_ITERATIONS", "1"))
        if not 1 <= iterations <= 25:
            raise AssertionError("B2B1_COLLISION_ITERATIONS must be between 1 and 25")
        return iterations

    @contextmanager
    def _prepared_database(self, prefix: str):
        with isolated_postgres_schema(self.database_url, prefix) as engine:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        CREATE TABLE users (
                            id INTEGER PRIMARY KEY,
                            role VARCHAR(40) NOT NULL,
                            marker VARCHAR(80) NOT NULL
                        )
                        """
                    )
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO users (id, role, marker) VALUES
                            (1, 'FACULTY_ADMIN', 'preserve-one'),
                            (2, 'FACULTY_ADMIN', 'preserve-two'),
                            (3, 'CAREER_MANAGER', 'preserve-career')
                        """
                    )
                )
                connection.execute(
                    text(
                        """
                        CREATE TABLE schema_migrations (
                            version VARCHAR(120) PRIMARY KEY,
                            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                        """
                    )
                )
                for version in PREDECESSOR_VERSIONS:
                    connection.execute(
                        text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                        {"version": version},
                    )
            yield engine

    @staticmethod
    def _active_insert_sql(
        *,
        revoked_at_expression: str | None = None,
        revocation_reason_expression: str = "NULL",
    ):
        return text(
            f"""
            INSERT INTO user_b2b_capabilities (
                id, user_id, capability, approval_reference, approved_input_sha256,
                assigned_by_identifier, revoked_at, revocation_reason
            ) VALUES (
                :id, :user_id, :capability, 'approved-ref', :digest,
                'test-operator', {revoked_at_expression or 'NULL'},
                {revocation_reason_expression}
            )
            """
        )

    @staticmethod
    def _active_insert_parameters(
        user_id: int,
        capability: str,
        *,
        digest: str = "a" * 64,
    ) -> dict[str, object]:
        return {
            "id": uuid4(),
            "user_id": user_id,
            "capability": capability,
            "digest": digest,
        }

    @staticmethod
    def _activate_capability_sql():
        return text(
            """
            UPDATE user_b2b_capabilities
            SET is_active = true,
                revoked_at = NULL,
                revocation_reason = NULL,
                version = version + 1
            WHERE id = :capability_id
            """
        )

    @staticmethod
    def _revoke_capability_sql():
        return text(
            """
            UPDATE user_b2b_capabilities
            SET is_active = false,
                revoked_at = CURRENT_TIMESTAMP,
                revocation_reason = 'concurrency revocation',
                version = version + 1
            WHERE id = :capability_id
            """
        )

    def _insert_active_capability(self, engine, user_id: int, capability: str):
        parameters = self._active_insert_parameters(user_id, capability)
        with engine.begin() as connection:
            connection.execute(self._active_insert_sql(), parameters)
        return parameters["id"]

    @staticmethod
    def _insert_revoked_capability(engine, user_id: int, capability: str):
        capability_id = uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO user_b2b_capabilities (
                        id, user_id, capability, is_active, approval_reference,
                        approved_input_sha256, assigned_by_identifier, revoked_at,
                        revocation_reason
                    ) VALUES (
                        :id, :user_id, :capability, false, 'approved-ref',
                        :digest, 'test-operator', CURRENT_TIMESTAMP,
                        'historical revocation'
                    )
                    """
                ),
                {
                    "id": capability_id,
                    "user_id": user_id,
                    "capability": capability,
                    "digest": "a" * 64,
                },
            )
        return capability_id

    def _assert_statement_rejected(self, engine, statement, parameters=None) -> None:
        with self.assertRaises(DBAPIError):
            with engine.begin() as connection:
                connection.execute(statement, parameters or {})

    @staticmethod
    def _applied_versions(engine) -> tuple[str, ...]:
        with engine.connect() as connection:
            return tuple(
                connection.execute(
                    text("SELECT version FROM schema_migrations ORDER BY applied_at, version")
                ).scalars()
            )

    @staticmethod
    def _users(engine) -> list[tuple[int, str, str]]:
        with engine.connect() as connection:
            return [
                tuple(row)
                for row in connection.execute(
                    text("SELECT id, role, marker FROM users ORDER BY id")
                )
            ]

    @staticmethod
    def _reset_concurrency_user(engine) -> None:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM user_b2b_capabilities"))
            connection.execute(
                text("UPDATE users SET role = 'FACULTY_ADMIN' WHERE id = 1")
            )

    @staticmethod
    def _user_capability_state(engine) -> tuple[str, bool]:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT users.role,
                           COALESCE(BOOL_OR(capability_row.is_active), false) AS has_active
                    FROM users
                    LEFT JOIN user_b2b_capabilities AS capability_row
                      ON capability_row.user_id = users.id
                    WHERE users.id = 1
                    GROUP BY users.id, users.role
                    """
                )
            ).one()
        return row.role, row.has_active

    def _start_transaction_worker(self, engine, statement, parameters) -> dict[str, object]:
        result: dict[str, object] = {
            "pid_ready": threading.Event(),
            "statement_finished": threading.Event(),
            "allow_commit": threading.Event(),
            "committed": False,
            "sqlstate": None,
        }

        def run() -> None:
            connection = None
            transaction = None
            try:
                connection = engine.connect()
                transaction = connection.begin()
                connection.execute(text("SET LOCAL statement_timeout = '5s'"))
                connection.execute(text("SET LOCAL lock_timeout = '4s'"))
                result["pid"] = connection.execute(
                    text("SELECT pg_backend_pid()")
                ).scalar_one()
                result["pid_ready"].set()
                try:
                    connection.execute(statement, parameters)
                except DBAPIError as error:
                    result["sqlstate"] = getattr(error.orig, "sqlstate", None)
                    transaction.rollback()
                else:
                    result["statement_succeeded"] = True
                    result["statement_finished"].set()
                    if result["allow_commit"].wait(6.0):
                        transaction.commit()
                        result["committed"] = True
                    else:
                        transaction.rollback()
                        result["harness_error"] = "worker commit release timed out"
            except BaseException as error:  # captured and asserted by the controlling test
                if transaction is not None and transaction.is_active:
                    transaction.rollback()
                result["harness_error"] = repr(error)
            finally:
                result["statement_finished"].set()
                result["pid_ready"].set()
                if connection is not None:
                    connection.close()

        thread = threading.Thread(target=run, daemon=True)
        result["thread"] = thread
        thread.start()
        if not result["pid_ready"].wait(2.0):
            self.fail("concurrency worker did not expose its PostgreSQL backend PID")
        if "pid" not in result:
            self.fail(f"concurrency worker failed before SQL: {result.get('harness_error')}")
        return result

    def _wait_for_worker_lock_or_completion(self, engine, worker: dict[str, object]) -> str:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if worker["statement_finished"].is_set():
                return "finished"
            with engine.connect() as observer:
                activity = observer.execute(
                    text(
                        """
                        SELECT wait_event_type, wait_event
                        FROM pg_stat_activity
                        WHERE pid = :pid
                        """
                    ),
                    {"pid": worker["pid"]},
                ).one_or_none()
            if activity is not None and activity.wait_event_type == "Lock":
                return "lock"
            time.sleep(0.01)
        self.fail("concurrency worker neither blocked on a lock nor completed within 2s")

    def _finish_worker(self, worker: dict[str, object]) -> None:
        worker["allow_commit"].set()
        thread = worker["thread"]
        thread.join(6.0)
        if thread.is_alive():
            self.fail("concurrency worker hung beyond the bounded timeout")
        if worker.get("harness_error"):
            self.fail(f"concurrency worker harness error: {worker['harness_error']}")
        sqlstate = worker.get("sqlstate")
        if sqlstate in {"55P03", "57014"}:
            self.fail(f"PostgreSQL lock/statement timeout occurred: SQLSTATE {sqlstate}")

    def _run_first_then_second(
        self,
        engine,
        first_statement,
        first_parameters,
        second_statement,
        second_parameters,
    ) -> dict[str, object]:
        first_connection = engine.connect()
        first_transaction = first_connection.begin()
        worker: dict[str, object] | None = None
        first_committed = False
        try:
            first_connection.execute(text("SET LOCAL statement_timeout = '5s'"))
            first_connection.execute(text("SET LOCAL lock_timeout = '4s'"))
            first_connection.execute(first_statement, first_parameters)
            worker = self._start_transaction_worker(
                engine, second_statement, second_parameters
            )
            wait_state = self._wait_for_worker_lock_or_completion(engine, worker)
            first_transaction.commit()
            first_committed = True
            self._finish_worker(worker)
            return {
                "wait_state": wait_state,
                "first_committed": first_committed,
                "second_committed": worker["committed"],
                "second_sqlstate": worker["sqlstate"],
            }
        finally:
            if first_transaction.is_active:
                first_transaction.rollback()
            if worker is not None:
                worker["allow_commit"].set()
                thread = worker["thread"]
                thread.join(6.0)
            first_connection.close()

    def _run_role_lock_then_revocation(self, engine, capability_id) -> dict[str, object]:
        role_connection = engine.connect()
        role_transaction = role_connection.begin()
        worker: dict[str, object] | None = None
        role_sqlstate = None
        try:
            role_connection.execute(text("SET LOCAL statement_timeout = '5s'"))
            role_connection.execute(text("SET LOCAL lock_timeout = '4s'"))
            role_connection.execute(
                text("SELECT id FROM users WHERE id = 1 FOR UPDATE")
            ).one()
            worker = self._start_transaction_worker(
                engine,
                self._revoke_capability_sql(),
                {"capability_id": capability_id},
            )
            wait_state = self._wait_for_worker_lock_or_completion(engine, worker)
            try:
                role_connection.execute(
                    text("UPDATE users SET role = 'CAREER_MANAGER' WHERE id = 1")
                )
            except DBAPIError as error:
                role_sqlstate = getattr(error.orig, "sqlstate", None)
                role_transaction.rollback()
            else:
                role_transaction.commit()
            self._finish_worker(worker)
            return {
                "wait_state": wait_state,
                "role_sqlstate": role_sqlstate,
                "revocation_committed": worker["committed"],
            }
        finally:
            if role_transaction.is_active:
                role_transaction.rollback()
            if worker is not None:
                worker["allow_commit"].set()
                thread = worker["thread"]
                thread.join(6.0)
            role_connection.close()

    def _assert_catalog_mutation_rejected(
        self,
        module,
        engine,
        statements: tuple[str, ...],
        accepted_marker: str,
    ) -> None:
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
        with engine.connect() as connection:
            try:
                module.assert_schema(connection)
            except RuntimeError:
                return
        self.fail(accepted_marker)

    @staticmethod
    def _capability_function_ddl() -> str:
        return """
            CREATE FUNCTION b2b_reject_career_capability()
            RETURNS TRIGGER
            LANGUAGE plpgsql
            AS $$
            DECLARE
                referenced_role text;
            BEGIN
                SELECT users.role::text
                INTO referenced_role
                FROM users
                WHERE users.id = NEW.user_id
                FOR UPDATE;
                IF referenced_role = 'CAREER_MANAGER' AND NEW.is_active
                THEN
                    RAISE EXCEPTION
                        'CAREER_MANAGER users cannot receive active B2B capabilities'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $$
        """

    @staticmethod
    def _capability_trigger_ddl() -> str:
        return """
            CREATE TRIGGER trg_user_b2b_capabilities_reject_career
            BEFORE INSERT OR UPDATE ON user_b2b_capabilities
            FOR EACH ROW
            EXECUTE FUNCTION b2b_reject_career_capability()
        """

    @staticmethod
    def _catalog_accepting_plans(module, engine) -> tuple[str, ...]:
        planner_profiles = (
            (
                "seqplan",
                (
                    "SET LOCAL enable_seqscan = on",
                    "SET LOCAL enable_indexscan = off",
                    "SET LOCAL enable_indexonlyscan = off",
                    "SET LOCAL enable_bitmapscan = off",
                ),
            ),
            (
                "indexplan",
                (
                    "SET LOCAL enable_seqscan = off",
                    "SET LOCAL enable_indexscan = on",
                    "SET LOCAL enable_indexonlyscan = on",
                    "SET LOCAL enable_bitmapscan = off",
                ),
            ),
            (
                "bitmapplan",
                (
                    "SET LOCAL enable_seqscan = off",
                    "SET LOCAL enable_indexscan = off",
                    "SET LOCAL enable_indexonlyscan = off",
                    "SET LOCAL enable_bitmapscan = on",
                ),
            ),
        )
        accepted = []
        for profile_name, settings in planner_profiles:
            with engine.connect() as connection:
                with connection.begin():
                    connection.execute(text("SET LOCAL statement_timeout = '5s'"))
                    connection.execute(text("SET LOCAL lock_timeout = '4s'"))
                    for setting in settings:
                        connection.execute(text(setting))
                    try:
                        module.assert_schema(connection)
                    except RuntimeError:
                        continue
                    accepted.append(profile_name)
        return tuple(accepted)

    def _assert_0017_objects_absent(self, engine) -> None:
        with engine.connect() as connection:
            table_count = connection.execute(
                text(
                    """
                    SELECT COUNT(*) FROM information_schema.tables
                    WHERE table_schema = current_schema()
                      AND table_name = 'user_b2b_capabilities'
                    """
                )
            ).scalar_one()
            function_count = connection.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM pg_proc AS procedure_row
                    JOIN pg_namespace AS namespace_row
                      ON namespace_row.oid = procedure_row.pronamespace
                    WHERE namespace_row.nspname = current_schema()
                      AND procedure_row.proname IN (:capability_function, :user_role_function)
                    """
                ),
                {
                    "capability_function": CAPABILITY_FUNCTION,
                    "user_role_function": USER_ROLE_FUNCTION,
                },
            ).scalar_one()
            trigger_count = connection.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM pg_trigger AS trigger_row
                    JOIN pg_class AS table_row ON table_row.oid = trigger_row.tgrelid
                    JOIN pg_namespace AS namespace_row
                      ON namespace_row.oid = table_row.relnamespace
                    WHERE namespace_row.nspname = current_schema()
                      AND NOT trigger_row.tgisinternal
                      AND trigger_row.tgname IN (:capability_trigger, :user_role_trigger)
                    """
                ),
                {
                    "capability_trigger": CAPABILITY_TRIGGER,
                    "user_role_trigger": USER_ROLE_TRIGGER,
                },
            ).scalar_one()

        self.assertEqual(table_count, 0)
        self.assertEqual(function_count, 0)
        self.assertEqual(trigger_count, 0)


if __name__ == "__main__":
    unittest.main()
