from __future__ import annotations

import asyncio
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from psycopg.errors import InsufficientPrivilege
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.core import migrations as migration_registry
from app.core.config import Settings
from app.core.database import Base
from app.core.migrations import run_migrations
from app.models import *  # noqa: F401,F403
from app.models.entities import AnnualGoal
from app.models.enums import GoalMetric
import app.services.human_review_audit as human_review_audit_service
from app.services.import_service import ImportService
from tests.support.postgres import (
    remove_0022_domain_artifacts,
    require_b2b1_test_database_url,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
PRIVILEGE_MODULE = "scripts.configure_human_review_privileges"
TABLE_PRIVILEGES = {
    "faculties": ("SELECT",),
    "careers": ("SELECT",),
    "users": ("SELECT",),
    "academic_periods": ("SELECT",),
    "teachers": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "external_researchers": ("SELECT", "INSERT", "DELETE"),
    "research_entities": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "person_roles": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "scientific_productions": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "scientific_production_authors": ("SELECT", "INSERT", "DELETE"),
    "research_projects": ("SELECT", "INSERT", "DELETE"),
    "project_teachers": ("SELECT", "INSERT", "DELETE"),
    "annual_goals": ("SELECT", "INSERT", "UPDATE"),
    "import_batches": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "import_jobs": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "import_review_items": ("SELECT", "INSERT", "DELETE"),
    "import_normalization_audits": ("SELECT", "INSERT", "DELETE"),
    "imported_research_records": ("INSERT", "DELETE"),
    "imported_project_participants": ("INSERT", "DELETE"),
    "imported_progress_reports": ("SELECT", "INSERT", "DELETE"),
    "imported_ocr_traces": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "user_b2b_capabilities": ("SELECT", "INSERT", "UPDATE"),
    "review_items": ("SELECT", "INSERT", "UPDATE"),
    "review_decisions": ("SELECT", "INSERT"),
    "canonical_identities": ("SELECT", "INSERT", "UPDATE"),
    "person_aliases": ("SELECT", "INSERT", "UPDATE"),
    "field_overrides": ("SELECT", "INSERT", "UPDATE"),
    "audit_events": ("SELECT", "INSERT"),
}
B2B_TABLE_NAMES = {
    "user_b2b_capabilities",
    "review_items",
    "review_decisions",
    "canonical_identities",
    "person_aliases",
    "field_overrides",
    "audit_events",
}
OWNER_ONLY_TABLES = ("schema_migrations",)
ALL_TABLE_PRIVILEGES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)
FUNCTION_NAMES = (
    "b2b_reject_career_capability",
    "b2b_reject_career_role_with_capability",
    "b2b_reject_append_only_mutation",
)


def _privilege_module():
    return importlib.import_module(PRIVILEGE_MODULE)


class HumanReviewPrivilegeConfigurationTests(unittest.TestCase):
    def test_settings_and_engines_keep_application_and_migration_urls_distinct(self) -> None:
        application_url = "postgresql+psycopg://app:app@db/app"
        owner_url = "postgresql+psycopg://owner:owner@db/app"
        configured = Settings(
            _env_file=None,
            database_url=application_url,
            migration_database_url=owner_url,
        )
        self.assertEqual(configured.database_url, application_url)
        self.assertEqual(configured.migration_database_url, owner_url)

        environment = os.environ.copy()
        environment.update({
            "DATABASE_URL": application_url,
            "MIGRATION_DATABASE_URL": owner_url,
        })
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import json; "
                    "from app.core.database import engine, migration_engine; "
                    "print(json.dumps({'app': engine.url.render_as_string(hide_password=False), "
                    "'owner': migration_engine.url.render_as_string(hide_password=False)}))"
                ),
            ],
            cwd=BACKEND_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"app": application_url, "owner": owner_url},
        )

    def test_serving_uses_only_application_url_and_bootstrap_requires_both_urls(self) -> None:
        main_source = (BACKEND_ROOT / "app/main.py").read_text(encoding="utf-8")
        self.assertNotIn("run_migrations", main_source)
        self.assertNotIn("migration_engine", main_source)
        compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        backend_block = compose.split("  backend:\n", 1)[1].split(
            "\n  prototype-bootstrap:\n", 1
        )[0]
        self.assertIn(
            "DATABASE_URL: "
            "${COMPOSE_APP_DATABASE_URL:?COMPOSE_APP_DATABASE_URL is required}",
            backend_block,
        )
        self.assertNotIn("MIGRATION_DATABASE_URL", backend_block)

        bootstrap_block = compose.split("  prototype-bootstrap:\n", 1)[1].split(
            "\n  frontend:\n", 1
        )[0]
        self.assertIn(
            "DATABASE_URL: "
            "${COMPOSE_APP_DATABASE_URL:?COMPOSE_APP_DATABASE_URL is required}",
            bootstrap_block,
        )
        self.assertIn(
            "MIGRATION_DATABASE_URL: "
            "${COMPOSE_MIGRATION_DATABASE_URL:"
            "?COMPOSE_MIGRATION_DATABASE_URL is required}",
            bootstrap_block,
        )
        self.assertNotIn(
            "DATABASE_URL: postgresql+psycopg://postgres:postgres@postgres",
            backend_block,
        )

    def test_cli_accepts_only_environment_variable_names_and_explicit_apply(self) -> None:
        parser = _privilege_module().build_parser()
        destinations = {action.dest for action in parser._actions}
        self.assertEqual(
            destinations,
            {"help", "owner_url_env", "application_role_env", "apply"},
        )

    def test_task9_advisory_lock_authorization_preserves_append_only_permissions(self) -> None:
        privilege_module = _privilege_module()
        self.assertEqual(
            privilege_module.B2B_TABLE_PRIVILEGES["audit_events"],
            ("SELECT", "INSERT"),
        )
        self.assertNotIn("audit_events", privilege_module.ROW_LOCK_TABLES)

        executed: list[tuple[object, dict[str, object]]] = []
        postgres_session = SimpleNamespace(
            get_bind=lambda: SimpleNamespace(
                dialect=SimpleNamespace(name="postgresql")
            ),
            execute=lambda statement, parameters: executed.append(
                (statement, parameters)
            ),
        )
        for aggregate_key in ("review-item:one", "review-item:two"):
            human_review_audit_service._acquire_aggregate_lock(
                postgres_session,
                "review_item",
                aggregate_key,
            )

        self.assertEqual(
            [" ".join(str(statement).split()) for statement, _parameters in executed],
            [
                "SELECT pg_advisory_xact_lock(:lock_key)",
                "SELECT pg_advisory_xact_lock(:lock_key)",
            ],
        )
        lock_keys = [parameters["lock_key"] for _statement, parameters in executed]
        self.assertTrue(all(isinstance(lock_key, int) for lock_key in lock_keys))
        self.assertNotEqual(lock_keys[0], lock_keys[1])

        head_sql = " ".join(
            str(
                human_review_audit_service._head_statement(
                    "review_item",
                    "review-item:one",
                )
            ).upper().split()
        )
        for row_lock_clause in (
            "FOR UPDATE",
            "FOR NO KEY UPDATE",
            "FOR SHARE",
            "FOR KEY SHARE",
        ):
            with self.subTest(row_lock_clause=row_lock_clause):
                self.assertNotIn(row_lock_clause, head_sql)

    def test_0021_changes_no_privilege_contract_or_database_object_beyond_check(self) -> None:
        migration_path = (
            BACKEND_ROOT
            / "app/migrations/versions/20260718_0021_human_review_scientific_decision_audit.py"
        )
        source = migration_path.read_text(encoding="utf-8")
        self.assertEqual(TABLE_PRIVILEGES["audit_events"], ("SELECT", "INSERT"))
        self.assertNotIn("GRANT ", source.upper())
        self.assertNotIn("REVOKE ", source.upper())
        self.assertNotIn("CREATE TABLE", source.upper())
        self.assertNotIn("CREATE INDEX", source.upper())
        self.assertNotIn("CREATE TRIGGER", source.upper())
        self.assertEqual(source.upper().count("ALTER TABLE AUDIT_EVENTS"), 4)


class HumanReviewPrivilegePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_url = require_b2b1_test_database_url()
        suffix = uuid4().hex[:12]
        cls.owner_role = f"b2b1_owner_{suffix}"
        cls.app_role = f"b2b1_app_{suffix}"
        cls.schema_name = f"b2b1_priv_{suffix}"
        cls.owner_password = f"owner_{suffix}"
        cls.app_password = f"app_{suffix}"
        cls.sequence_name = "b2b1_review_items_version_seq"
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
                cls._role_url(cls.owner_role, cls.owner_password, "task8_owner"),
                pool_pre_ping=True,
            )
            cls.app_engine = create_engine(
                cls._role_url(cls.app_role, cls.app_password, "task8_app"),
                pool_pre_ping=True,
            )
            cls._apply_b2b_migrations()
            if run_migrations(cls.owner_engine) != []:
                raise AssertionError("the complete migration registry must be idempotent")
            with cls.owner_engine.begin() as connection:
                connection.execute(text(f"CREATE SEQUENCE {cls.sequence_name}"))
                connection.execute(text(
                    f"ALTER SEQUENCE {cls.sequence_name} OWNED BY review_items.version"
                ))
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
            legacy_tables = [
                table
                for table in Base.metadata.sorted_tables
                if table.name not in B2B_TABLE_NAMES
            ]
            Base.metadata.create_all(connection, tables=legacy_tables)
            remove_0022_domain_artifacts(connection)
            connection.execute(text(
                """
                CREATE TABLE schema_migrations (
                    version VARCHAR(120) PRIMARY KEY,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
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

    def _configure(self):
        return _privilege_module().configure_human_review_privileges(
            self.owner_engine,
            self.app_role,
        )

    def _qualified(self, object_name: str) -> str:
        return f'"{self.schema_name}"."{object_name}"'

    def _has_table_privilege(self, table_name: str, privilege: str) -> bool:
        with self.admin_engine.connect() as connection:
            return bool(connection.execute(text(
                "SELECT has_table_privilege(:role, :object_name, :privilege)"
            ), {
                "role": self.app_role,
                "object_name": self._qualified(table_name),
                "privilege": privilege,
            }).scalar_one())

    def _has_sequence_privilege(self, sequence_name: str, privilege: str) -> bool:
        with self.admin_engine.connect() as connection:
            return bool(connection.execute(text(
                "SELECT has_sequence_privilege(:role, :object_name, :privilege)"
            ), {
                "role": self.app_role,
                "object_name": self._qualified(sequence_name),
                "privilege": privilege,
            }).scalar_one())

    def _assert_insufficient(self, statement: str) -> None:
        with self.assertRaises(DBAPIError) as caught:
            with self.app_engine.begin() as connection:
                connection.execute(text(statement))
        self.assertIsInstance(caught.exception.orig, InsufficientPrivilege)

    def test_rejects_invalid_missing_privileged_owner_and_inherited_owner_roles(self) -> None:
        module = _privilege_module()
        for invalid in ("", "9app", "app-role", "app role", "a" * 64):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    module.configure_human_review_privileges(self.owner_engine, invalid)

        with self.assertRaisesRegex(ValueError, "does not exist"):
            module.configure_human_review_privileges(
                self.owner_engine,
                f"b2b1_missing_{uuid4().hex[:8]}",
            )
        with self.assertRaisesRegex(ValueError, "distinct"):
            module.configure_human_review_privileges(
                self.owner_engine,
                self.owner_role,
            )

        with self.admin_engine.begin() as connection:
            connection.execute(text(f'ALTER ROLE "{self.app_role}" CREATEDB'))
        try:
            with self.assertRaisesRegex(ValueError, "CREATEDB"):
                module.configure_human_review_privileges(
                    self.owner_engine,
                    self.app_role,
                )
        finally:
            with self.admin_engine.begin() as connection:
                connection.execute(text(f'ALTER ROLE "{self.app_role}" NOCREATEDB'))

        with self.admin_engine.begin() as connection:
            connection.execute(text(
                f'GRANT "{self.owner_role}" TO "{self.app_role}"'
            ))
        try:
            with self.assertRaisesRegex(ValueError, "owner role"):
                module.configure_human_review_privileges(
                    self.owner_engine,
                    self.app_role,
                )
        finally:
            with self.admin_engine.begin() as connection:
                connection.execute(text(
                    f'REVOKE "{self.owner_role}" FROM "{self.app_role}"'
                ))

    def test_exact_privilege_matrix_is_idempotent_and_has_no_grant_options(self) -> None:
        first = self._configure()
        with self.admin_engine.connect() as connection:
            before_acl = tuple(tuple(row) for row in connection.execute(text(
                """
                SELECT class_name, object_name, acl
                FROM (
                    SELECT 'relation' AS class_name, relation.relname AS object_name,
                           relation.relacl::text AS acl
                    FROM pg_class AS relation
                    JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname = :schema_name
                      AND relation.relname = ANY(:relation_names)
                    UNION ALL
                    SELECT 'function', function_row.proname, function_row.proacl::text
                    FROM pg_proc AS function_row
                    JOIN pg_namespace AS namespace ON namespace.oid = function_row.pronamespace
                    WHERE namespace.nspname = :schema_name
                      AND function_row.proname = ANY(:function_names)
                ) AS objects
                ORDER BY class_name, object_name
                """
            ), {
                "schema_name": self.schema_name,
                "relation_names": list(TABLE_PRIVILEGES) + [self.sequence_name],
                "function_names": list(FUNCTION_NAMES),
            }))
        second = self._configure()
        with self.admin_engine.connect() as connection:
            after_acl = tuple(tuple(row) for row in connection.execute(text(
                """
                SELECT class_name, object_name, acl
                FROM (
                    SELECT 'relation' AS class_name, relation.relname AS object_name,
                           relation.relacl::text AS acl
                    FROM pg_class AS relation
                    JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname = :schema_name
                      AND relation.relname = ANY(:relation_names)
                    UNION ALL
                    SELECT 'function', function_row.proname, function_row.proacl::text
                    FROM pg_proc AS function_row
                    JOIN pg_namespace AS namespace ON namespace.oid = function_row.pronamespace
                    WHERE namespace.nspname = :schema_name
                      AND function_row.proname = ANY(:function_names)
                ) AS objects
                ORDER BY class_name, object_name
                """
            ), {
                "schema_name": self.schema_name,
                "relation_names": list(TABLE_PRIVILEGES) + [self.sequence_name],
                "function_names": list(FUNCTION_NAMES),
            }))
            grantable = connection.execute(text(
                """
                SELECT COUNT(*)
                FROM information_schema.role_table_grants
                WHERE grantee = :role AND is_grantable = 'YES'
                """
            ), {"role": self.app_role}).scalar_one()

        self.assertEqual(first, second)
        self.assertEqual(before_acl, after_acl)
        self.assertEqual(grantable, 0)
        for table_name, allowed in TABLE_PRIVILEGES.items():
            for privilege in ALL_TABLE_PRIVILEGES:
                with self.subTest(table=table_name, privilege=privilege):
                    self.assertEqual(
                        self._has_table_privilege(table_name, privilege),
                        privilege in allowed,
                    )
        for table_name in OWNER_ONLY_TABLES:
            for privilege in ALL_TABLE_PRIVILEGES:
                with self.subTest(table=table_name, privilege=privilege):
                    self.assertFalse(
                        self._has_table_privilege(table_name, privilege)
                    )

    def test_application_role_supports_annual_goal_runtime_and_import_poa(self) -> None:
        self._configure()
        with self.owner_engine.begin() as connection:
            faculty_id = connection.execute(text(
                "INSERT INTO faculties (name) VALUES ('Runtime privilege faculty') "
                "RETURNING id"
            )).scalar_one()
            connection.execute(text(
                "INSERT INTO careers (faculty_id, name, code) "
                "VALUES (:faculty_id, 'Runtime privilege career', 'RPC')"
            ), {"faculty_id": faculty_id})

        app_session = sessionmaker(bind=self.app_engine)()
        try:
            service = ImportService(app_session)
            first_rows = {
                "headers": ["CARRERA", "ANO", "METRICA", "VALOR_PLANIFICADO"],
                "records": [{
                    "CARRERA": "Runtime privilege career",
                    "ANO": "2030-2031",
                    "METRICA": GoalMetric.SCIENTIFIC_OUTPUT.value,
                    "VALOR_PLANIFICADO": 7,
                }],
            }
            with patch.object(service, "_load_rows", return_value=first_rows):
                result = asyncio.run(service.import_poa(
                    SimpleNamespace(filename="poa.xlsx"),
                    "runtime-privilege-test",
                ))
            self.assertEqual(result.imported_rows, 1)

            second_rows = {
                **first_rows,
                "records": [
                    {**first_rows["records"][0], "VALOR_PLANIFICADO": 9}
                ],
            }
            with patch.object(service, "_load_rows", return_value=second_rows):
                result = asyncio.run(service.import_poa(
                    SimpleNamespace(filename="poa-update.xlsx"),
                    "runtime-privilege-test",
                ))
            self.assertEqual(result.imported_rows, 1)

            goal = app_session.query(AnnualGoal).one()
            self.assertEqual(goal.planned_value, 9)
            app_session.execute(text(
                "SELECT id FROM import_jobs ORDER BY id LIMIT 1 FOR UPDATE"
            )).one()
            app_session.rollback()
        finally:
            app_session.close()

        self._assert_insufficient("DELETE FROM annual_goals")
        self._assert_insufficient(
            "INSERT INTO faculties (name) VALUES ('not runtime')"
        )

    def test_owner_owns_b2b_objects_and_app_owns_none_or_schema(self) -> None:
        self._configure()
        with self.admin_engine.connect() as connection:
            relation_owners = tuple(connection.execute(text(
                """
                SELECT relation.relname, owner.rolname
                FROM pg_class AS relation
                JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                JOIN pg_roles AS owner ON owner.oid = relation.relowner
                WHERE namespace.nspname = :schema_name
                  AND (
                      relation.relname = ANY(:table_names)
                      OR relation.relname = :sequence_name
                      OR relation.oid IN (
                          SELECT indexrelid
                          FROM pg_index
                          WHERE indrelid IN (
                              SELECT oid FROM pg_class
                              WHERE relnamespace = namespace.oid
                                AND relname = ANY(:table_names)
                          )
                      )
                  )
                ORDER BY relation.relname
                """
            ), {
                "schema_name": self.schema_name,
                "table_names": list(TABLE_PRIVILEGES),
                "sequence_name": self.sequence_name,
            }))
            function_owners = tuple(connection.execute(text(
                """
                SELECT function_row.proname, owner.rolname
                FROM pg_proc AS function_row
                JOIN pg_namespace AS namespace ON namespace.oid = function_row.pronamespace
                JOIN pg_roles AS owner ON owner.oid = function_row.proowner
                WHERE namespace.nspname = :schema_name
                  AND function_row.proname = ANY(:function_names)
                ORDER BY function_row.proname
                """
            ), {
                "schema_name": self.schema_name,
                "function_names": list(FUNCTION_NAMES),
            }))
            schema_owner = connection.execute(text(
                """
                SELECT owner.rolname
                FROM pg_namespace AS namespace
                JOIN pg_roles AS owner ON owner.oid = namespace.nspowner
                WHERE namespace.nspname = :schema_name
                """
            ), {"schema_name": self.schema_name}).scalar_one()
            app_owned = connection.execute(text(
                """
                SELECT
                    (SELECT COUNT(*) FROM pg_class WHERE relowner = role_row.oid)
                    + (SELECT COUNT(*) FROM pg_proc WHERE proowner = role_row.oid)
                    + (SELECT COUNT(*) FROM pg_namespace WHERE nspowner = role_row.oid)
                FROM pg_roles AS role_row
                WHERE role_row.rolname = :role
                """
            ), {"role": self.app_role}).scalar_one()

        self.assertGreater(len(relation_owners), len(TABLE_PRIVILEGES))
        self.assertTrue(all(owner == self.owner_role for _name, owner in relation_owners))
        self.assertEqual(
            {name for name, owner in function_owners if owner == self.owner_role},
            set(FUNCTION_NAMES),
        )
        self.assertEqual(schema_owner, self.owner_role)
        self.assertEqual(app_owned, 0)

    def test_owner_keeps_administration_while_app_has_usage_without_create(self) -> None:
        self._configure()
        with self.owner_engine.begin() as connection:
            connection.execute(text("CREATE TABLE owner_privilege_probe (id INTEGER)"))
            connection.execute(text("DROP TABLE owner_privilege_probe"))
        with self.admin_engine.connect() as connection:
            owner_create = connection.execute(text(
                "SELECT has_schema_privilege(:role, :schema_name, 'CREATE')"
            ), {"role": self.owner_role, "schema_name": self.schema_name}).scalar_one()
            app_usage = connection.execute(text(
                "SELECT has_schema_privilege(:role, :schema_name, 'USAGE')"
            ), {"role": self.app_role, "schema_name": self.schema_name}).scalar_one()
            app_create = connection.execute(text(
                "SELECT has_schema_privilege(:role, :schema_name, 'CREATE')"
            ), {"role": self.app_role, "schema_name": self.schema_name}).scalar_one()
        self.assertTrue(owner_create)
        self.assertTrue(app_usage)
        self.assertFalse(app_create)

    def test_app_can_insert_and_read_decisions_and_audit_and_update_review_item(self) -> None:
        self._configure()
        item_id = self._insert_review_item()
        decision_id = self._insert_review_decision(item_id)
        event_id = self._insert_audit_event()
        with self.app_engine.begin() as connection:
            connection.execute(text(
                "UPDATE review_items SET manual_priority = 1, version = version + 1 "
                "WHERE id = :id"
            ), {"id": item_id})
            decision_count = connection.execute(text(
                "SELECT COUNT(*) FROM review_decisions WHERE id = :id"
            ), {"id": decision_id}).scalar_one()
            event_count = connection.execute(text(
                "SELECT COUNT(*) FROM audit_events WHERE id = :id"
            ), {"id": event_id}).scalar_one()
        self.assertEqual(decision_count, 1)
        self.assertEqual(event_count, 1)

    def test_audit_events_rejects_destructive_dml_and_every_locking_clause(self) -> None:
        self._configure()
        event_id = self._insert_audit_event()
        self._assert_insufficient(
            f"UPDATE audit_events SET actor_identifier = 'changed' WHERE id = '{event_id}'"
        )
        self._assert_insufficient(f"DELETE FROM audit_events WHERE id = '{event_id}'")
        self._assert_insufficient("TRUNCATE TABLE audit_events")
        for clause in ("FOR UPDATE", "FOR NO KEY UPDATE", "FOR SHARE", "FOR KEY SHARE"):
            with self.subTest(clause=clause):
                self._assert_insufficient(
                    f"SELECT id FROM audit_events WHERE id = '{event_id}' {clause}"
                )
        with self.owner_engine.connect() as connection:
            actor = connection.execute(text(
                "SELECT actor_identifier FROM audit_events WHERE id = :id"
            ), {"id": event_id}).scalar_one()
        self.assertEqual(actor, "task8:app")

    def test_review_decisions_rejects_update_delete_and_truncate(self) -> None:
        self._configure()
        item_id = self._insert_review_item()
        decision_id = self._insert_review_decision(item_id)
        self._assert_insufficient(
            f"UPDATE review_decisions SET reason = 'changed' WHERE id = '{decision_id}'"
        )
        self._assert_insufficient(
            f"DELETE FROM review_decisions WHERE id = '{decision_id}'"
        )
        self._assert_insufficient("TRUNCATE TABLE review_decisions")

    def test_app_cannot_alter_drop_change_ownership_or_escalate_privileges(self) -> None:
        self._configure()
        statements = (
            "ALTER TABLE audit_events DISABLE TRIGGER trg_audit_events_append_only",
            "DROP TRIGGER trg_audit_events_append_only ON audit_events",
            "DROP INDEX ix_audit_events_occurred_at",
            "DROP TABLE audit_events",
            f'ALTER TABLE audit_events OWNER TO "{self.app_role}"',
            "ALTER FUNCTION b2b_reject_append_only_mutation() RENAME TO forbidden",
            "DROP FUNCTION b2b_reject_append_only_mutation()",
            f'SET ROLE "{self.owner_role}"',
            "CREATE TABLE forbidden_by_app (id INTEGER)",
        )
        for statement in statements:
            with self.subTest(statement=statement):
                self._assert_insufficient(statement)

        with self.app_engine.begin() as connection:
            connection.execute(text(
                f'GRANT UPDATE ON audit_events TO "{self.app_role}"'
            ))
        self.assertFalse(self._has_table_privilege("audit_events", "UPDATE"))

    def test_sequences_work_without_setval_and_b2b_functions_are_not_executable(self) -> None:
        self._configure()
        with self.app_engine.begin() as connection:
            first_value = connection.execute(text(
                f"SELECT nextval('{self.sequence_name}')"
            )).scalar_one()
        self.assertEqual(first_value, 1)
        self._assert_insufficient(
            f"SELECT setval('{self.sequence_name}', 50, TRUE)"
        )
        for sequence_name in ("annual_goals_id_seq", "import_jobs_id_seq"):
            with self.subTest(sequence=sequence_name):
                self.assertTrue(
                    self._has_sequence_privilege(sequence_name, "USAGE")
                )
                self.assertFalse(
                    self._has_sequence_privilege(sequence_name, "SELECT")
                )
                self.assertFalse(
                    self._has_sequence_privilege(sequence_name, "UPDATE")
                )
        for privilege in ("SELECT", "USAGE", "UPDATE"):
            with self.subTest(sequence="faculties_id_seq", privilege=privilege):
                self.assertFalse(
                    self._has_sequence_privilege("faculties_id_seq", privilege)
                )
        with self.admin_engine.connect() as connection:
            for function_name in FUNCTION_NAMES:
                with self.subTest(function=function_name):
                    can_execute = connection.execute(text(
                        "SELECT has_function_privilege(:role, :function_name, 'EXECUTE')"
                    ), {
                        "role": self.app_role,
                        "function_name": self._qualified(function_name) + "()",
                    }).scalar_one()
                    self.assertFalse(can_execute)

    def _insert_review_item(self) -> UUID:
        item_id = uuid4()
        with self.app_engine.begin() as connection:
            connection.execute(text(
                """
                INSERT INTO review_items (
                    id, case_type, stable_target_key, target_table, target_pk,
                    document_key, source_revision, source_page, source_section,
                    row_or_block_id, field_path, raw_value_sha256, period_id,
                    relationship_key, case_status, scientific_status,
                    scope_resolution_reason
                ) VALUES (
                    :id, 'person_identity', :stable_target_key, 'person_roles', 10,
                    'document:task8.pdf', 'revision-1', 1, 'faculty',
                    'row-1', 'canonical_name', :raw_hash, 2026,
                    'relationship:1', 'pending', 'pending',
                    'unresolved_no_persisted_scope'
                )
                """
            ), {
                "id": item_id,
                "stable_target_key": f"b2b:v1:person_identity:{item_id.hex:0>64}",
                "raw_hash": "a" * 64,
            })
        return item_id

    def _insert_review_decision(self, item_id: UUID) -> UUID:
        decision_id = uuid4()
        with self.app_engine.begin() as connection:
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
                    'task8 decision', 'legacy', NULL, 'task8:legacy', NULL, 1, FALSE
                )
                """
            ), {
                "id": decision_id,
                "review_item_id": item_id,
                "payload": json.dumps({"value": "accepted"}),
            })
        return decision_id

    def _insert_audit_event(self) -> UUID:
        event_id = uuid4()
        with self.app_engine.begin() as connection:
            connection.execute(text(
                """
                INSERT INTO audit_events (
                    id, event_type, aggregate_type, aggregate_key,
                    review_item_id, actor_user_id, actor_identifier,
                    actor_capability, payload_schema, payload_version, payload,
                    correlation_id, request_id, previous_event_id,
                    corrects_event_id, previous_event_hash, event_hash
                ) VALUES (
                    :id, 'case_backfilled', 'task8', :aggregate_key,
                    NULL, NULL, 'task8:app', NULL,
                    'audit.case_backfilled.v1', 1, CAST(:payload AS JSONB),
                    :correlation_id, NULL, NULL, NULL, NULL, :event_hash
                )
                """
            ), {
                "id": event_id,
                "aggregate_key": f"task8:{event_id}",
                "payload": json.dumps({"kind": "case_backfilled", "schema_version": 1}),
                "correlation_id": uuid4(),
                "event_hash": event_id.hex + uuid4().hex,
            })
        return event_id


if __name__ == "__main__":
    unittest.main()
