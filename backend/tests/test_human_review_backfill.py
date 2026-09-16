from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import hashlib
import io
import importlib
import inspect
import json
import os
from pathlib import Path
from threading import Event
import tempfile
import unittest
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid4, uuid5

from psycopg.errors import InsufficientPrivilege
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

import app.models
from app.core import migrations as migration_registry
from app.core.database import Base
from app.models.entities import (
    AcademicPeriod,
    ExternalResearcher,
    PersonRole,
    ResearchEntity,
    ResearchProject,
    ScientificProduction,
    ScientificProductionAuthor,
)
from app.models.enums import ProductionType, ProjectType
from app.models.human_review_audit import AuditEvent
from app.models.human_review_core import ReviewDecision, ReviewItem
from app.models.human_review_enums import AuditEventType
from app.models.human_review_projection import CanonicalIdentity, FieldOverride, PersonAlias
from app.schemas.human_review import IdentityDecisionPayloadV1
from app.schemas.human_review_operations import (
    AuditEventCommandV1,
    B2B1BackfillApplyResultV1,
    B2B1BackfillPlanV1,
    BackfillGateError,
    CaseBackfilledAuditPayloadV1,
)
from tests.support.backfill_plan import synthetic_backfill_plan
from app.services.human_review_audit import (
    append_audit_event_at_current_head,
    compute_audit_event_hash,
)
from app.services.human_review_invariants import (
    PROTECTED_TABLES,
    capture_b2b1_invariants,
    compare_b2b1_invariants,
    snapshot_sha256,
)
from scripts.configure_human_review_privileges import configure_human_review_privileges
from tests.support.postgres import (
    create_pre_0022_domain_catalog,
    remove_0022_domain_artifacts,
    require_b2b1_test_database_url,
)


SOURCE_TABLES = (
    "external_researchers",
    "person_roles",
    "research_entities",
    "research_projects",
    "scientific_production_authors",
    "scientific_productions",
)
GLOBAL_BACKFILL_LOCK_NAME = "human-review-b2b1-backfill"
B2B_TABLES = (
    "user_b2b_capabilities",
    "review_items",
    "review_decisions",
    "canonical_identities",
    "person_aliases",
    "field_overrides",
    "audit_events",
)


class HumanReviewBackfillServiceSurfaceTests(unittest.TestCase):
    def test_service_exposes_only_the_approved_task12_entry_points(self) -> None:
        service = importlib.import_module("app.services.human_review_backfill")
        signatures = {
            "load_backfill_candidates": ("db",),
            "build_backfill_plan": ("db", "captured_at"),
            "apply_backfill_plan": (
                "db",
                "plan",
                "approved_plan_sha256",
                "expected_invariants_sha256",
            ),
        }
        for name, parameters in signatures.items():
            with self.subTest(function=name):
                function = getattr(service, name)
                self.assertEqual(tuple(inspect.signature(function).parameters), parameters)

    def test_cli_is_closed_environment_only_and_never_accepts_database_urls(self) -> None:
        cli = importlib.import_module("scripts.backfill_human_review_b2b1")
        parser = cli.build_parser()
        destinations = {action.dest for action in parser._actions}
        self.assertEqual(
            destinations,
            {
                "help",
                "dry_run",
                "apply",
                "output",
                "plan",
                "approved_plan_sha256",
                "snapshot",
                "backup",
                "backup_sha256",
            },
        )
        self.assertFalse(any("url" in destination for destination in destinations))
        source = inspect.getsource(cli)
        self.assertIn('os.environ.get("DATABASE_URL"', source)
        self.assertIn('os.environ.get("MIGRATION_DATABASE_URL"', source)
        self.assertIn('os.environ.get("B2B1_APPLICATION_DB_ROLE"', source)
        self.assertNotIn("ValidatedReadService", source)

    def test_apply_sql_guard_rejects_forbidden_owner_and_downgraded_statements(self) -> None:
        cli = importlib.import_module("scripts.backfill_human_review_b2b1")
        guard = cli._ApplySQLGuard()
        with self.assertRaises(BackfillGateError):
            guard.before_cursor_execute(
                None,
                None,
                "INSERT INTO review_items (id) VALUES (NULL)",
                None,
                None,
                False,
            )

        guard.downgraded = True
        forbidden = (
            "UPDATE person_roles SET validation_status = 'validated'",
            "DELETE FROM scientific_productions",
            "INSERT INTO external_researchers (id) VALUES (9)",
            "TRUNCATE research_entities",
            "ALTER TABLE research_projects ADD COLUMN forbidden int",
            "DROP TABLE scientific_production_authors",
            "GRANT UPDATE ON person_roles TO app",
            "REVOKE SELECT ON person_roles FROM app",
            "RESET ROLE",
            "SET ROLE owner",
            "SET LOCAL ROLE owner",
            "CREATE FUNCTION forbidden() RETURNS void LANGUAGE sql AS 'SELECT 1'",
            "CREATE TRIGGER forbidden BEFORE UPDATE ON person_roles EXECUTE FUNCTION forbidden()",
        )
        for statement in forbidden:
            with self.subTest(statement=statement), self.assertRaises(BackfillGateError):
                guard.before_cursor_execute(
                    None,
                    None,
                    statement,
                    None,
                    None,
                    False,
                )
        guard.before_cursor_execute(
            None,
            None,
            "INSERT INTO review_items (id) VALUES (NULL)",
            None,
            None,
            False,
        )

    def test_apply_sql_guard_rejects_schema_qualified_source_and_b2b_writes(self) -> None:
        cli = importlib.import_module("scripts.backfill_human_review_b2b1")
        guard = cli._ApplySQLGuard()
        with self.assertRaisesRegex(BackfillGateError, "owner attempted"):
            guard.before_cursor_execute(
                None,
                None,
                'INSERT INTO "tenant"."review_items" (id) VALUES (NULL)',
                None,
                None,
                False,
            )

        guard.downgraded = True
        qualified_source_writes = (
            "UPDATE public.person_roles SET validation_status = 'validated'",
            "DELETE FROM tenant.research_entities WHERE id = 1",
            "DELETE FROM ONLY archive.person_roles WHERE id = 1",
            'INSERT INTO ONLY "public"."external_researchers" (id) VALUES (1)',
        )
        for statement in qualified_source_writes:
            with self.subTest(statement=statement):
                with self.assertRaisesRegex(BackfillGateError, "protected source"):
                    guard.before_cursor_execute(
                        None,
                        None,
                        statement,
                        None,
                        None,
                        False,
                    )

    def test_cli_sanitizes_database_errors_before_parser_output(self) -> None:
        cli = importlib.import_module("scripts.backfill_human_review_b2b1")
        secret_role = "actual_application_role"
        database_error = OperationalError(
            f"SET LOCAL ROLE {secret_role}",
            {},
            RuntimeError("password=actual-secret"),
        )
        stderr = io.StringIO()
        with patch.object(cli, "_apply", side_effect=database_error):
            with redirect_stderr(stderr), self.assertRaises(SystemExit):
                cli.main([
                    "--apply",
                    "--plan",
                    "plan.json",
                    "--approved-plan-sha256",
                    "0" * 64,
                    "--snapshot",
                    "snapshot.json",
                    "--backup",
                    "backup.dump",
                    "--backup-sha256",
                    "0" * 64,
                    "--output",
                    "result.json",
                ])
        public_error = stderr.getvalue()
        self.assertNotIn(secret_role, public_error)
        self.assertNotIn("actual-secret", public_error)
        self.assertNotIn("SET LOCAL ROLE", public_error)


class HumanReviewBackfillPlanHashContractTests(unittest.TestCase):
    @classmethod
    def _historical_plan(cls) -> B2B1BackfillPlanV1:
        return synthetic_backfill_plan()

    @classmethod
    def _mutated_plan(cls, mutate) -> B2B1BackfillPlanV1:
        material = cls._historical_plan().model_dump(mode="json")
        mutate(material)
        return B2B1BackfillPlanV1.model_validate(material)

    @classmethod
    def _locked_plan(cls) -> B2B1BackfillPlanV1:
        historical = cls._historical_plan()
        candidate = historical.candidates[0].model_dump(mode="json")
        candidate.update(
            memberships=["canonical_pending", "identity_locked"],
            case_status="resolved",
            scientific_status="validated",
        )
        return B2B1BackfillPlanV1.model_validate(
            {
                "schema_version": 1,
                "captured_at": historical.captured_at,
                "invariant_snapshot_sha256": historical.invariant_snapshot_sha256,
                "source_counts": [],
                "overlaps": [],
                "union_stable_target_count": 1,
                "candidates": [candidate],
                "locked_decisions": [
                    {
                        "candidate": candidate,
                        "canonical_identity_key": "legacy:hash-contract-person",
                        "canonical_name": "Hash Contract Person",
                        "identity_type": "unclassified_person",
                        "legacy_actor_identifier": "legacy-hash-contract",
                        "legacy_decided_at": "2035-01-02T03:04:06+00:00",
                        "alias_original": "Hash Contract Person",
                    }
                ],
                "blockers": [],
            }
        )

    def assert_functional_change_changes_hash(
        self,
        original: B2B1BackfillPlanV1,
        changed: B2B1BackfillPlanV1,
    ) -> None:
        service = importlib.import_module("app.services.human_review_backfill")
        self.assertNotEqual(
            service.backfill_plan_sha256(original),
            service.backfill_plan_sha256(changed),
        )

    def test_same_plan_and_capture_time_is_deterministic(self) -> None:
        service = importlib.import_module("app.services.human_review_backfill")
        plan = self._historical_plan()
        self.assertEqual(
            service.backfill_plan_sha256(plan),
            service.backfill_plan_sha256(plan.model_copy(deep=True)),
        )

    def test_different_capture_time_does_not_change_semantic_hash(self) -> None:
        service = importlib.import_module("app.services.human_review_backfill")
        plan = self._historical_plan()
        changed = plan.model_copy(
            update={"captured_at": plan.captured_at + timedelta(days=1)}
        )
        self.assertEqual(
            service.backfill_plan_sha256(plan),
            service.backfill_plan_sha256(changed),
        )

    def test_one_microsecond_capture_difference_does_not_change_semantic_hash(self) -> None:
        service = importlib.import_module("app.services.human_review_backfill")
        plan = self._historical_plan()
        changed = plan.model_copy(
            update={"captured_at": plan.captured_at + timedelta(microseconds=1)}
        )
        self.assertEqual(
            service.backfill_plan_sha256(plan),
            service.backfill_plan_sha256(changed),
        )

    def test_semantic_material_is_exactly_full_plan_without_top_level_capture_time(self) -> None:
        service = importlib.import_module("app.services.human_review_backfill")
        plan = self._historical_plan()
        material = plan.model_dump(mode="json")
        self.assertIn("captured_at", material)
        material_without_capture = dict(material)
        material_without_capture.pop("captured_at")
        expected = hashlib.sha256(service._canonical_json(material_without_capture)).hexdigest()
        self.assertEqual(service.backfill_plan_sha256(plan), expected)
        self.assertEqual(
            set(material_without_capture),
            set(material) - {"captured_at"},
        )

    def test_candidate_change_changes_semantic_hash(self) -> None:
        original = self._historical_plan()
        changed = self._mutated_plan(
            lambda material: material["candidates"][0].update(
                possible_kpi_impact=not material["candidates"][0]["possible_kpi_impact"]
            )
        )
        self.assert_functional_change_changes_hash(original, changed)

    def test_stable_target_change_changes_semantic_hash(self) -> None:
        original = self._historical_plan()
        changed = self._mutated_plan(
            lambda material: material["candidates"][0]["stable_target"].update(
                target_pk=material["candidates"][0]["stable_target"]["target_pk"] + 100000
            )
        )
        self.assert_functional_change_changes_hash(original, changed)

    def test_membership_change_changes_semantic_hash(self) -> None:
        original = self._historical_plan()

        def mutate(material: dict[str, object]) -> None:
            candidate = material["candidates"][0]
            candidate.update(
                memberships=["identity_locked"],
                case_status="resolved",
                scientific_status="validated",
            )

        changed = self._mutated_plan(mutate)
        self.assert_functional_change_changes_hash(original, changed)

    def test_blocker_change_changes_semantic_hash(self) -> None:
        original = self._historical_plan()

        def mutate(material: dict[str, object]) -> None:
            message = material["blockers"][0]["message"] + " with detail"
            material["blockers"][0]["message"] = message
            material["hard_blockers"][0]["message"] = message

        changed = self._mutated_plan(mutate)
        self.assert_functional_change_changes_hash(original, changed)

    def test_snapshot_change_changes_semantic_hash(self) -> None:
        original = self._historical_plan()
        changed = self._mutated_plan(
            lambda material: material.update(invariant_snapshot_sha256="f" * 64)
        )
        self.assert_functional_change_changes_hash(original, changed)

    def test_locator_change_changes_semantic_hash(self) -> None:
        original = self._historical_plan()
        changed = self._mutated_plan(
            lambda material: material["candidates"][0]["stable_target"].update(
                row_or_block_id=(
                    material["candidates"][0]["stable_target"]["row_or_block_id"]
                    + ":changed"
                )
            )
        )
        self.assert_functional_change_changes_hash(original, changed)

    def test_locked_decision_change_changes_semantic_hash(self) -> None:
        original = self._locked_plan()
        material = original.model_dump(mode="json")
        material["locked_decisions"][0]["alias_original"] = "Changed Alias"
        changed = B2B1BackfillPlanV1.model_validate(material)
        self.assert_functional_change_changes_hash(original, changed)

    def test_nested_timestamp_change_changes_semantic_hash(self) -> None:
        original = self._locked_plan()
        material = original.model_dump(mode="json")
        material["locked_decisions"][0]["legacy_decided_at"] = (
            "2035-01-02T03:04:06.000001+00:00"
        )
        changed = B2B1BackfillPlanV1.model_validate(material)
        self.assert_functional_change_changes_hash(original, changed)

    def test_other_functional_top_level_change_changes_semantic_hash(self) -> None:
        original = self._historical_plan()
        changed = self._mutated_plan(
            lambda material: material["source_counts"][0].update(
                row_count=material["source_counts"][0]["row_count"] + 1
            )
        )
        self.assert_functional_change_changes_hash(original, changed)

    def test_hash_does_not_mutate_input(self) -> None:
        service = importlib.import_module("app.services.human_review_backfill")
        plan = self._historical_plan()
        before = plan.model_dump(mode="json")
        service.backfill_plan_sha256(plan)
        self.assertEqual(plan.model_dump(mode="json"), before)

    def test_input_mapping_key_order_does_not_change_hash(self) -> None:
        service = importlib.import_module("app.services.human_review_backfill")
        plan = self._historical_plan()
        material = plan.model_dump(mode="json")
        reordered = dict(reversed(tuple(material.items())))
        reconstructed = B2B1BackfillPlanV1.model_validate(reordered)
        self.assertEqual(
            service.backfill_plan_sha256(plan),
            service.backfill_plan_sha256(reconstructed),
        )


class HumanReviewBackfillPostgresLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_url = require_b2b1_test_database_url()
        suffix = uuid4().hex[:12]
        cls.owner_role = f"b2b1_backfill_owner_{suffix}"
        cls.app_role = f"b2b1_backfill_app_{suffix}"
        cls.schema_name = f"b2b1_backfill_{suffix}"
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
                    f'GRANT "{cls.app_role}" TO "{cls.owner_role}"'
                ))
                connection.execute(text(
                    f'CREATE SCHEMA "{cls.schema_name}" AUTHORIZATION "{cls.owner_role}"'
                ))

            cls.owner_engine = create_engine(
                cls._role_url(cls.owner_role, cls.owner_password, "task12_owner"),
                pool_pre_ping=True,
            )
            cls.app_engine = create_engine(
                cls._role_url(cls.app_role, cls.app_password, "task12_app"),
                pool_pre_ping=True,
            )
            cls._create_source_fixture()
            cls._apply_b2b_migrations()
            configure_human_review_privileges(cls.owner_engine, cls.app_role)
            cls._configure_source_privileges()
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
    def _create_source_fixture(cls) -> None:
        with cls.owner_engine.begin() as connection:
            create_pre_0022_domain_catalog(connection, seed_lock_rows=True)
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

    @classmethod
    def _apply_b2b_migrations(cls) -> None:
        for version, upgrade in migration_registry.MIGRATIONS[16:]:
            upgrade(cls.owner_engine)
            with cls.owner_engine.begin() as connection:
                connection.execute(
                    text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                    {"version": version},
                )

    @classmethod
    def _configure_source_privileges(cls) -> None:
        tables = ", ".join(SOURCE_TABLES)
        with cls.owner_engine.begin() as connection:
            connection.execute(text(
                f'REVOKE ALL PRIVILEGES ON TABLE {tables} FROM PUBLIC, "{cls.app_role}"'
            ))
            connection.execute(text(
                f'GRANT SELECT ON TABLE {tables} TO "{cls.app_role}"'
            ))

    @classmethod
    def _cleanup(cls) -> None:
        for engine_name in ("app_engine", "owner_engine"):
            engine = getattr(cls, engine_name, None)
            if engine is not None:
                engine.dispose()
        admin_engine = getattr(cls, "admin_engine", None)
        if admin_engine is None:
            return
        try:
            with admin_engine.begin() as connection:
                for role in (getattr(cls, "app_role", ""), getattr(cls, "owner_role", "")):
                    if role:
                        connection.execute(text(f'DROP OWNED BY "{role}" CASCADE'))
                schema_name = getattr(cls, "schema_name", "")
                if schema_name:
                    connection.execute(text(
                        f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'
                    ))
                for role in (getattr(cls, "owner_role", ""), getattr(cls, "app_role", "")):
                    if role:
                        connection.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        finally:
            admin_engine.dispose()

    @staticmethod
    def _acquire_apply_locks(connection: Connection) -> None:
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_name))"),
            {"lock_name": GLOBAL_BACKFILL_LOCK_NAME},
        )
        connection.execute(text(
            "LOCK TABLE external_researchers, person_roles, research_entities, "
            "research_projects, scientific_production_authors, scientific_productions "
            "IN SHARE MODE"
        ))

    @classmethod
    def _share_locked_sources(cls, connection: Connection) -> tuple[str, ...]:
        return tuple(connection.execute(text(
            """
            SELECT relation.relname
            FROM pg_locks AS lock_row
            JOIN pg_class AS relation ON relation.oid = lock_row.relation
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE lock_row.pid = pg_backend_pid()
              AND lock_row.locktype = 'relation'
              AND lock_row.mode = 'ShareLock'
              AND lock_row.granted
              AND namespace.nspname = :schema_name
              AND relation.relname = ANY(:source_tables)
            ORDER BY relation.relname
            """
        ), {
            "schema_name": cls.schema_name,
            "source_tables": list(SOURCE_TABLES),
        }).scalars())

    def _assert_nested_insufficient(self, connection: Connection, statement: str) -> None:
        nested = connection.begin_nested()
        try:
            with self.assertRaises(DBAPIError) as raised:
                connection.execute(text(statement))
            self.assertIsInstance(raised.exception.orig, InsufficientPrivilege)
        finally:
            nested.rollback()
        self.assertEqual(
            connection.execute(text("SELECT current_user")).scalar_one(),
            self.app_role,
        )

    def _assert_backend_locks_released(self, backend_pid: int) -> None:
        with self.admin_engine.connect() as connection:
            remaining = connection.execute(text(
                """
                SELECT COUNT(*)
                FROM pg_locks AS lock_row
                LEFT JOIN pg_class AS relation ON relation.oid = lock_row.relation
                LEFT JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                WHERE lock_row.pid = :backend_pid
                  AND (
                      lock_row.locktype = 'advisory'
                      OR (
                          lock_row.mode = 'ShareLock'
                          AND namespace.nspname = :schema_name
                          AND relation.relname = ANY(:source_tables)
                      )
                  )
                """
            ), {
                "backend_pid": backend_pid,
                "schema_name": self.schema_name,
                "source_tables": list(SOURCE_TABLES),
            }).scalar_one()
        self.assertEqual(remaining, 0)

    def test_application_role_can_read_sources_but_cannot_lock_or_mutate_them(self) -> None:
        for table_name in SOURCE_TABLES:
            with self.subTest(table=table_name):
                with self.app_engine.connect() as connection:
                    transaction = connection.begin()
                    try:
                        self.assertEqual(
                            connection.execute(
                                text(f"SELECT COUNT(*) FROM {table_name}")
                            ).scalar_one(),
                            1,
                        )
                        with self.assertRaises(DBAPIError) as raised:
                            connection.execute(text(
                                f"SELECT id FROM {table_name} FOR SHARE"
                            ))
                        self.assertIsInstance(raised.exception.orig, InsufficientPrivilege)
                    finally:
                        transaction.rollback()

        with self.app_engine.begin() as connection:
            for table_name in SOURCE_TABLES:
                for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                    with self.subTest(table=table_name, privilege=privilege):
                        allowed = connection.execute(text(
                            "SELECT has_table_privilege(current_user, :table_name, :privilege)"
                        ), {"table_name": table_name, "privilege": privilege}).scalar_one()
                        self.assertFalse(allowed)

    def test_owner_acquires_six_share_locks_then_downgrades_before_b2b_insert(self) -> None:
        identity_id = uuid4()
        connection = self.owner_engine.connect()
        transaction = connection.begin()
        try:
            owner_user, session_user = connection.execute(text(
                "SELECT current_user, session_user"
            )).one()
            self.assertEqual((owner_user, session_user), (self.owner_role, self.owner_role))

            self._acquire_apply_locks(connection)
            self.assertEqual(self._share_locked_sources(connection), tuple(sorted(SOURCE_TABLES)))

            connection.execute(text(f'SET LOCAL ROLE "{self.app_role}"'))
            current_user, session_user = connection.execute(text(
                "SELECT current_user, session_user"
            )).one()
            self.assertEqual((current_user, session_user), (self.app_role, self.owner_role))
            self.assertEqual(self._share_locked_sources(connection), tuple(sorted(SOURCE_TABLES)))

            for table_name in SOURCE_TABLES:
                self.assertEqual(
                    connection.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar_one(),
                    1,
                )

            connection.execute(text(
                """
                INSERT INTO canonical_identities (
                    id, canonical_identity_key, identity_type, display_name,
                    status, origin, version
                ) VALUES (
                    :id, :canonical_identity_key, 'unclassified_person',
                    'Disposable identity', 'active', 'b1_locked', 1
                )
                """
            ), {
                "id": identity_id,
                "canonical_identity_key": f"task12:{identity_id}",
            })

            forbidden_statements = (
                "UPDATE person_roles SET marker = 'forbidden' WHERE id = 1",
                "DELETE FROM research_entities WHERE id = 1",
                "INSERT INTO scientific_productions (id, marker) VALUES (2, 'forbidden')",
                "TRUNCATE external_researchers",
                "ALTER TABLE research_projects ADD COLUMN forbidden INTEGER",
                "DROP TABLE scientific_production_authors",
                "CREATE FUNCTION forbidden_task12() RETURNS trigger LANGUAGE plpgsql "
                "AS $$ BEGIN RETURN NEW; END $$",
                "CREATE TRIGGER forbidden_task12 BEFORE UPDATE ON person_roles "
                "FOR EACH ROW EXECUTE FUNCTION pg_catalog.suppress_redundant_updates_trigger()",
            )
            for statement in forbidden_statements:
                with self.subTest(statement=statement):
                    self._assert_nested_insufficient(connection, statement)
            self.assertEqual(self._share_locked_sources(connection), tuple(sorted(SOURCE_TABLES)))
        finally:
            transaction.rollback()
            backend_pid = connection.connection.driver_connection.info.backend_pid
            connection.close()
        self._assert_backend_locks_released(backend_pid)

        with self.app_engine.begin() as app_connection:
            with self.assertRaises(DBAPIError) as raised:
                app_connection.execute(text(f'SET LOCAL ROLE "{self.owner_role}"'))
            self.assertIsInstance(raised.exception.orig, InsufficientPrivilege)

    def test_share_locks_block_all_source_mutations_and_release_on_rollback(self) -> None:
        holder = self.owner_engine.connect()
        holder_transaction = holder.begin()
        self._acquire_apply_locks(holder)
        backend_pid = holder.connection.driver_connection.info.backend_pid

        started = {table_name: Event() for table_name in SOURCE_TABLES}

        def mutate(table_name: str) -> str:
            with self.owner_engine.begin() as connection:
                started[table_name].set()
                connection.execute(text(
                    f"UPDATE {table_name} SET marker = marker WHERE id = 1"
                ))
            return table_name

        with ThreadPoolExecutor(max_workers=len(SOURCE_TABLES)) as executor:
            futures = {
                table_name: executor.submit(mutate, table_name)
                for table_name in SOURCE_TABLES
            }
            for table_name, event in started.items():
                self.assertTrue(event.wait(timeout=5), f"mutation did not start for {table_name}")
            for table_name, future in futures.items():
                with self.subTest(table=table_name), self.assertRaises(FutureTimeout):
                    future.result(timeout=0.15)
            holder_transaction.rollback()
            holder.close()
            for table_name, future in futures.items():
                with self.subTest(released_table=table_name):
                    self.assertEqual(future.result(timeout=5), table_name)

        self._assert_backend_locks_released(backend_pid)

    def test_advisory_lock_serializes_applies_and_commit_releases_all_locks(self) -> None:
        holder = self.owner_engine.connect()
        holder_transaction = holder.begin()
        self._acquire_apply_locks(holder)
        backend_pid = holder.connection.driver_connection.info.backend_pid
        waiter_started = Event()

        def wait_for_global_lock() -> bool:
            with self.owner_engine.begin() as connection:
                waiter_started.set()
                connection.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:lock_name))"),
                    {"lock_name": GLOBAL_BACKFILL_LOCK_NAME},
                )
            return True

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(wait_for_global_lock)
            self.assertTrue(waiter_started.wait(timeout=5))
            with self.assertRaises(FutureTimeout):
                future.result(timeout=0.3)
            holder_transaction.commit()
            holder.close()
            self.assertTrue(future.result(timeout=5))

        self._assert_backend_locks_released(backend_pid)


class HumanReviewBackfillServicePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_url = require_b2b1_test_database_url()
        suffix = uuid4().hex[:12]
        cls.owner_role = f"b2b1_service_owner_{suffix}"
        cls.app_role = f"b2b1_service_app_{suffix}"
        cls.schema_name = f"b2b1_service_{suffix}"
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
                connection.execute(text(f'GRANT "{cls.app_role}" TO "{cls.owner_role}"'))
                connection.execute(text(
                    f'CREATE SCHEMA "{cls.schema_name}" AUTHORIZATION "{cls.owner_role}"'
                ))
            cls.owner_engine = create_engine(
                cls._role_url(cls.owner_role, cls.owner_password, "task12_service_owner"),
                pool_pre_ping=True,
            )
            cls.app_engine = create_engine(
                cls._role_url(cls.app_role, cls.app_password, "task12_service_app"),
                pool_pre_ping=True,
            )
            cls._create_schema()
            configure_human_review_privileges(cls.owner_engine, cls.app_role)
            cls._grant_domain_reads()
            cls._seed_sources()
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
    def _create_schema(cls) -> None:
        domain_tables = [
            table
            for table in Base.metadata.sorted_tables
            if table.name not in set(B2B_TABLES)
        ]
        Base.metadata.create_all(cls.owner_engine, tables=domain_tables)
        with cls.owner_engine.begin() as connection:
            remove_0022_domain_artifacts(connection)
            connection.execute(text(
                "CREATE TABLE schema_migrations ("
                "version VARCHAR(120) PRIMARY KEY, "
                "applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
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
    def _grant_domain_reads(cls) -> None:
        domain_names = sorted(
            table.name
            for table in Base.metadata.sorted_tables
            if table.name not in set(B2B_TABLES)
        )
        with cls.owner_engine.begin() as connection:
            for table_name in domain_names:
                connection.execute(text(
                    f'REVOKE ALL PRIVILEGES ON TABLE {table_name} FROM PUBLIC, '
                    f'"{cls.app_role}"'
                ))
                connection.execute(text(
                    f'GRANT SELECT ON TABLE {table_name} TO "{cls.app_role}"'
                ))
            connection.execute(text(
                f'GRANT SELECT ON TABLE schema_migrations TO "{cls.app_role}"'
            ))

    @classmethod
    def _seed_sources(cls) -> None:
        with Session(cls.owner_engine) as db:
            db.add(AcademicPeriod(id=1, year_label="2026", cycle=1))
            db.add(ResearchProject(
                id=1, period_id=1, name="Stable project", project_type=ProjectType.FCI,
                status="vigente", progress_percentage=25,
                source_file="evidence/research.pdf", source_page=4,
                source_section="research_projects", raw_value="Project evidence",
                raw_project_name="Stable project", normalized_project_name="stable project",
                raw_code="P-001", normalized_code="P-001",
                parser_version="b1-v1",
            ))
            db.add(ResearchEntity(
                id=1, period_id=1, type="project", code="P-001",
                normalized_code="P-001", name="Stable project",
                normalized_name="stable project", director_name="Director pending",
                normalized_director_name="director pending", validation_status="validated",
                source_file="evidence/research.pdf", source_page=4,
                source_section="research_entities",
                raw_value="P-001 Stable project Director pending", parser_version="b1-v1",
                metadata_json={"row_or_block_id": "entity:p-001"},
            ))
            db.add(ExternalResearcher(
                id=1, period_id=1, source_file="evidence/externals.pdf", source_page=2,
                full_name="External Pending", normalized_name="external pending",
                institution="External Institute", normalized_institution="external institute",
                source_section="external_researchers", requires_review=True,
                raw_value="External Pending - External Institute", parser_version="b1-v1",
            ))
            product_rows = (
                (1, ProductionType.ARTICLE, "Pending product", "pending_review", 1),
                (2, ProductionType.ARTICLE, "Eligible product one", "validated", 2),
                (3, ProductionType.BOOK, "Eligible product two", "validated", 3),
            )
            for product_id, product_type, title, status, page in product_rows:
                db.add(ScientificProduction(
                    id=product_id, period_id=1, production_type=product_type,
                    title=title, normalized_title=title.casefold(), status="published",
                    validation_status=status, source_file="evidence/products.pdf",
                    source_page=page, source_section="scientific_productions",
                    raw_value=title, parser_version="b1-v1",
                ))
            db.flush()
            for author_id, product_id, name in (
                (11, 1, "Product Author"),
                (12, 2, "Eligible Author One"),
                (13, 3, "Eligible Author Two"),
            ):
                db.add(ScientificProductionAuthor(
                    id=author_id, production_id=product_id, author_order=1,
                    raw_author_name=name, normalized_author_name=name.casefold(),
                    canonical_identity_key=f"human:author:{author_id}", canonical_name=name,
                    author_type="internal", validation_status="validated",
                    source_file="evidence/products.pdf", source_page=product_id,
                    source_section="scientific_production_authors",
                    row_or_block_id=f"product:{product_id}:author:1", parser_version="b1-v1",
                ))
            role_rows = (
                (1, "legacy:locked-person", "Ana Perez", "Ána Pérez", "locked-ana", True, "evidence/participants.pdf"),
                (2, "pending:person-two", "Coincident Name", "Coincident Name", "two", False, "evidence/participants.pdf"),
                (3, "pending:person-three", "Coincident Name", "Coincident Name", "three", False, "evidence/participants.pdf"),
                (4, "pending:no-locator", "No Locator", "No Locator", "missing", False, None),
            )
            for role_id, key, canonical_name, raw_name, block_id, locked, source_file in role_rows:
                db.add(PersonRole(
                    id=role_id, period_id=1, role_type="researcher",
                    person_type="pendiente_clasificacion", canonical_identity_key=key,
                    canonical_name=canonical_name, identity_source="b1_pending",
                    identity_locked=locked, identity_decided_by=None,
                    identity_decided_at=(
                        datetime(2026, 7, 1, 14, 30, tzinfo=timezone.utc)
                        if locked else None
                    ),
                    raw_name=raw_name, normalized_name=canonical_name.casefold(),
                    source_file=source_file, source_page=role_id,
                    source_section="participants", raw_value=f"{raw_name} researcher",
                    parser_version="b1-v1",
                    metadata_json={"row_or_block_id": f"participant:{block_id}"},
                    validation_status="pending_review",
                ))
            db.commit()

    @classmethod
    def _cleanup(cls) -> None:
        for engine_name in ("app_engine", "owner_engine"):
            engine = getattr(cls, engine_name, None)
            if engine is not None:
                engine.dispose()
        admin_engine = getattr(cls, "admin_engine", None)
        if admin_engine is None:
            return
        try:
            with admin_engine.begin() as connection:
                for role in (getattr(cls, "app_role", ""), getattr(cls, "owner_role", "")):
                    if role:
                        connection.execute(text(f'DROP OWNED BY "{role}" CASCADE'))
                schema_name = getattr(cls, "schema_name", "")
                if schema_name:
                    connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
                for role in (getattr(cls, "owner_role", ""), getattr(cls, "app_role", "")):
                    if role:
                        connection.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        finally:
            admin_engine.dispose()

    @classmethod
    def _build_plan_as_app(cls, captured_at: datetime) -> B2B1BackfillPlanV1:
        service = importlib.import_module("app.services.human_review_backfill")
        with cls.app_engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                with Session(bind=connection, autoflush=False) as db:
                    return service.build_backfill_plan(db, captured_at)
            finally:
                transaction.rollback()

    @classmethod
    def _capture_as_app(cls):
        with cls.app_engine.connect() as connection:
            transaction = connection.begin()
            try:
                return capture_b2b1_invariants(connection)
            finally:
                transaction.rollback()

    @classmethod
    def _begin_apply(cls):
        connection = cls.owner_engine.connect()
        transaction = connection.begin()
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_name))"),
            {"lock_name": GLOBAL_BACKFILL_LOCK_NAME},
        )
        connection.execute(text(
            "LOCK TABLE external_researchers, person_roles, research_entities, "
            "research_projects, scientific_production_authors, scientific_productions "
            "IN SHARE MODE"
        ))
        connection.execute(text(f'SET LOCAL ROLE "{cls.app_role}"'))
        return connection, transaction, Session(bind=connection, expire_on_commit=False)

    @classmethod
    def _b2b_counts(cls) -> dict[str, int]:
        with cls.app_engine.connect() as connection:
            return {
                table_name: connection.execute(
                    text(f"SELECT COUNT(*) FROM {table_name}")
                ).scalar_one()
                for table_name in B2B_TABLES
            }

    def test_director_candidate_requires_exact_research_project_evidence(self) -> None:
        captured_at = datetime(2026, 7, 15, 15, 30, tzinfo=timezone.utc)
        with self.owner_engine.begin() as connection:
            connection.execute(text(
                "UPDATE research_projects "
                "SET normalized_code = 'OTHER', normalized_project_name = 'other project' "
                "WHERE id = 1"
            ))
        try:
            plan = self._build_plan_as_app(captured_at)
            director_cases = tuple(
                candidate
                for candidate in plan.candidates
                if candidate.case_type.value == "project_director_relation"
            )
            self.assertEqual(director_cases, ())
            self.assertIn(
                ("research_entities", 1, "invariant_mismatch"),
                {
                    (blocker.source_table.value, blocker.source_id, blocker.code)
                    for blocker in plan.blockers
                },
            )
        finally:
            with self.owner_engine.begin() as connection:
                connection.execute(text(
                    "UPDATE research_projects "
                    "SET normalized_code = 'P-001', "
                    "normalized_project_name = 'stable project' WHERE id = 1"
                ))

    def test_ambiguous_project_evidence_is_materialized_as_a_hard_blocker(self) -> None:
        with Session(self.owner_engine) as db:
            db.add(ResearchProject(
                id=2,
                period_id=1,
                name="Stable project",
                project_type=ProjectType.FCI,
                status="vigente",
                progress_percentage=25,
                source_file="evidence/research-duplicate.pdf",
                source_page=7,
                source_section="research_projects",
                raw_value="Duplicate project evidence",
                raw_project_name="Stable project",
                normalized_project_name="stable project",
                raw_code="P-001",
                normalized_code="P-001",
                parser_version="b1-v1",
            ))
            db.commit()
        try:
            plan = self._build_plan_as_app(
                datetime(2026, 7, 15, 15, 31, tzinfo=timezone.utc)
            )
            self.assertFalse(any(
                candidate.case_type.value == "project_director_relation"
                for candidate in plan.candidates
            ))
            expected = ("research_entities", 1, "ambiguous_project_evidence")
            actual = {
                (blocker.source_table.value, blocker.source_id, blocker.code)
                for blocker in plan.hard_blockers
            }
            self.assertIn(expected, actual)
        finally:
            with self.owner_engine.begin() as connection:
                connection.execute(text("DELETE FROM research_projects WHERE id = 2"))

    def test_contradictory_project_evidence_is_materialized_as_a_hard_blocker(self) -> None:
        with self.owner_engine.begin() as connection:
            connection.execute(text(
                "UPDATE research_projects "
                "SET normalized_project_name = 'contradictory project' WHERE id = 1"
            ))
        try:
            plan = self._build_plan_as_app(
                datetime(2026, 7, 15, 15, 32, tzinfo=timezone.utc)
            )
            self.assertFalse(any(
                candidate.case_type.value == "project_director_relation"
                for candidate in plan.candidates
            ))
            expected = ("research_entities", 1, "contradictory_project_evidence")
            actual = {
                (blocker.source_table.value, blocker.source_id, blocker.code)
                for blocker in plan.hard_blockers
            }
            self.assertIn(expected, actual)
        finally:
            with self.owner_engine.begin() as connection:
                connection.execute(text(
                    "UPDATE research_projects "
                    "SET normalized_project_name = 'stable project' WHERE id = 1"
                ))

    def test_duplicate_locked_sources_share_one_identity_and_alias_creator(self) -> None:
        service = importlib.import_module("app.services.human_review_backfill")
        decided_at = datetime(2026, 7, 1, 14, 30, tzinfo=timezone.utc)
        with Session(self.owner_engine) as db:
            db.execute(text(
                "UPDATE person_roles SET identity_decided_by = 'legacy-reviewer' "
                "WHERE id = 1"
            ))
            db.execute(text(
                "UPDATE person_roles SET source_file = 'evidence/participants.pdf' "
                "WHERE id = 4"
            ))
            db.add(PersonRole(
                id=5,
                period_id=1,
                role_type="researcher",
                person_type="pendiente_clasificacion",
                canonical_identity_key="legacy:locked-person",
                canonical_name="Ana Perez",
                identity_source="b1_pending",
                identity_locked=True,
                identity_decided_by="legacy-reviewer-two",
                identity_decided_at=decided_at,
                raw_name="Ána Pérez",
                normalized_name="ana perez",
                source_file="evidence/participants.pdf",
                source_page=5,
                source_section="participants",
                raw_value="Ána Pérez researcher",
                parser_version="b1-v1",
                metadata_json={"row_or_block_id": "participant:locked-ana-two"},
                validation_status="pending_review",
            ))
            db.commit()
        try:
            plan = self._build_plan_as_app(
                datetime(2026, 7, 15, 15, 45, tzinfo=timezone.utc)
            )
            self.assertEqual(plan.blockers, ())
            self.assertEqual(len(plan.locked_decisions), 2)
            self.assertEqual(
                {item.canonical_identity_key for item in plan.locked_decisions},
                {"legacy:locked-person"},
            )
            approved_hash = service.backfill_plan_sha256(plan)
            connection, transaction, db = self._begin_apply()
            try:
                first = service.apply_backfill_plan(
                    db,
                    plan,
                    approved_hash,
                    plan.invariant_snapshot_sha256,
                )
                self.assertEqual(first.created_review_items, 10)
                self.assertEqual(first.created_decisions, 2)
                self.assertEqual(first.created_overrides, 4)
                self.assertEqual(first.created_identities, 1)
                self.assertEqual(first.created_aliases, 1)
                self.assertEqual(first.created_audit_events, 18)
                self.assertEqual(db.query(CanonicalIdentity).count(), 1)
                self.assertEqual(db.query(PersonAlias).count(), 1)
                self.assertEqual(db.query(ReviewDecision).count(), 2)
                self.assertEqual(db.query(FieldOverride).count(), 4)
                self.assertEqual(db.query(AuditEvent).count(), 18)

                second = service.apply_backfill_plan(
                    db,
                    plan,
                    approved_hash,
                    plan.invariant_snapshot_sha256,
                )
                self.assertEqual(second.created_review_items, 0)
                self.assertEqual(second.created_decisions, 0)
                self.assertEqual(second.created_overrides, 0)
                self.assertEqual(second.created_identities, 0)
                self.assertEqual(second.created_aliases, 0)
                self.assertEqual(second.created_audit_events, 0)
            finally:
                db.close()
                transaction.rollback()
                connection.close()
        finally:
            with self.owner_engine.begin() as connection:
                connection.execute(text("DELETE FROM person_roles WHERE id = 5"))
                connection.execute(text(
                    "UPDATE person_roles SET identity_decided_by = NULL WHERE id = 1"
                ))
                connection.execute(text(
                    "UPDATE person_roles SET source_file = NULL WHERE id = 4"
                ))

    def test_full_backfill_blocker_rollback_apply_idempotence_and_snapshots(self) -> None:
        service = importlib.import_module("app.services.human_review_backfill")
        captured_at = datetime(2026, 7, 15, 16, 0, tzinfo=timezone.utc)

        blocked = self._build_plan_as_app(captured_at)
        self.assertEqual(
            tuple(blocker.code for blocker in blocked.blockers),
            ("incomplete_locked_decision", "missing_stable_locator"),
        )
        self.assertTrue(all(count == 0 for count in self._b2b_counts().values()))

        with self.owner_engine.begin() as connection:
            connection.execute(text(
                "UPDATE person_roles SET identity_decided_by = 'legacy-reviewer' WHERE id = 1"
            ))
            connection.execute(text("DELETE FROM person_roles WHERE id = 4"))

        before_snapshot = self._capture_as_app()
        self.assertEqual(before_snapshot.eligible_products, 2)
        plan = self._build_plan_as_app(captured_at)
        self.assertEqual(plan.blockers, ())
        self.assertEqual(plan.invariant_snapshot_sha256, snapshot_sha256(before_snapshot))
        counts = {item.population.value: item.row_count for item in plan.source_counts}
        self.assertEqual(counts, {
            "canonical_pending": 3,
            "product_pending": 1,
            "director_relation_pending": 1,
            "external_pending": 1,
            "possible_match": 2,
            "identity_locked": 1,
        })
        self.assertEqual(len(plan.candidates), 8)
        self.assertEqual(plan.union_stable_target_count, 6)
        self.assertEqual(len(plan.locked_decisions), 1)
        overlap_counts = {
            tuple(population.value for population in item.populations): item.stable_target_count
            for item in plan.overlaps
        }
        self.assertEqual(overlap_counts[("canonical_pending", "possible_match")], 2)
        self.assertEqual(overlap_counts[("canonical_pending", "identity_locked")], 1)

        with self.app_engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                with Session(bind=connection, autoflush=False) as db:
                    self.assertEqual(service.load_backfill_candidates(db), plan.candidates)
            finally:
                transaction.rollback()

        approved_hash = service.backfill_plan_sha256(plan)
        connection, transaction, db = self._begin_apply()
        try:
            with patch.object(
                service,
                "append_audit_event_at_current_head",
                side_effect=RuntimeError("injected audit failure"),
            ):
                with self.assertRaisesRegex(RuntimeError, "injected audit failure"):
                    service.apply_backfill_plan(
                        db,
                        plan,
                        approved_hash,
                        plan.invariant_snapshot_sha256,
                    )
        finally:
            db.close()
            transaction.rollback()
            connection.close()
        self.assertTrue(all(count == 0 for count in self._b2b_counts().values()))

    def test_aa_product_locator_248_is_applied_exactly_and_rollback_remains_total(self) -> None:
        service = importlib.import_module("app.services.human_review_backfill")
        prefix = "product:scientific-productions:id=400:"
        locator = prefix + "x" * (248 - len(prefix))
        long_title = locator.removeprefix("product:")
        self.assertEqual(len(locator), 248)

        with self.owner_engine.begin() as connection:
            connection.execute(text(
                "UPDATE scientific_productions "
                "SET title = :title, normalized_title = :title WHERE id = 1"
            ), {"title": long_title})
            connection.execute(text(
                "UPDATE person_roles SET identity_decided_by = 'legacy-reviewer' WHERE id = 1"
            ))
            connection.execute(text(
                "UPDATE person_roles SET source_file = 'evidence/participants.pdf' WHERE id = 4"
            ))

        failure: DBAPIError | None = None
        try:
            plan = self._build_plan_as_app(
                datetime(2026, 7, 15, 16, 10, tzinfo=timezone.utc)
            )
            candidate = next(
                item
                for item in plan.candidates
                if item.source_table.value == "scientific_productions" and item.source_id == 1
            )
            self.assertEqual(candidate.stable_target.row_or_block_id, locator)
            stable_key_before = service.build_stable_target_key(candidate.stable_target)
            stable_key_after = service.build_stable_target_key(
                type(candidate.stable_target).model_validate_json(
                    candidate.stable_target.model_dump_json()
                )
            )
            self.assertEqual(stable_key_after, stable_key_before)

            approved_hash = service.backfill_plan_sha256(plan)
            connection, transaction, db = self._begin_apply()
            try:
                result = service.apply_backfill_plan(
                    db,
                    plan,
                    approved_hash,
                    plan.invariant_snapshot_sha256,
                )
                db.flush()
                stored = db.execute(text(
                    "SELECT row_or_block_id FROM review_items "
                    "WHERE target_table = 'scientific_productions' AND target_pk = 1"
                )).scalar_one()
                self.assertEqual(stored.encode("utf-8"), locator.encode("utf-8"))
                self.assertGreater(result.created_review_items, 0)
            except DBAPIError as error:
                failure = error
            finally:
                db.close()
                transaction.rollback()
                connection.close()

            rollback_counts = self._b2b_counts()
            self.assertTrue(all(count == 0 for count in rollback_counts.values()))
            if failure is not None:
                self.assertEqual(type(failure.orig).__name__, "StringDataRightTruncation")
                self.fail(
                    "StringDataRightTruncation on the 248-character id=400-equivalent "
                    f"locator; rollback_counts={rollback_counts}"
                )
        finally:
            with self.owner_engine.begin() as connection:
                connection.execute(text(
                    "UPDATE scientific_productions "
                    "SET title = 'Pending product', normalized_title = 'pending product' "
                    "WHERE id = 1"
                ))
                connection.execute(text(
                    "UPDATE person_roles SET identity_decided_by = NULL WHERE id = 1"
                ))
                connection.execute(text(
                    "UPDATE person_roles SET source_file = NULL WHERE id = 4"
                ))

    def test_y_dry_run_writes_diagnostic_plan_then_fails_when_blocked(self) -> None:
        cli = importlib.import_module("scripts.backfill_human_review_b2b1")
        app_url = self._role_url(
            self.app_role,
            self.app_password,
            "task12_cli_blocked_dry",
        ).render_as_string(hide_password=False)
        with Session(self.owner_engine) as db:
            db.add(PersonRole(
                id=99,
                period_id=1,
                role_type="researcher",
                person_type="pendiente_clasificacion",
                canonical_identity_key="pending:blocked-dry",
                canonical_name="Blocked Dry",
                identity_source="b1_pending",
                identity_locked=False,
                raw_name="Blocked Dry",
                normalized_name="blocked dry",
                source_file=None,
                source_page=99,
                source_section="participants",
                raw_value="Blocked Dry researcher",
                parser_version="b1-v1",
                metadata_json={"row_or_block_id": "participant:blocked-dry"},
                validation_status="pending_review",
            ))
            db.commit()
        try:
            before_counts = self._b2b_counts()
            with tempfile.TemporaryDirectory(prefix="b2b1-task12-blocked-") as directory:
                plan_path = Path(directory) / "blocked-plan.json"
                stderr = io.StringIO()
                with patch.dict(os.environ, {"DATABASE_URL": app_url}, clear=False):
                    with redirect_stderr(stderr), self.assertRaises(SystemExit):
                        cli.main(["--dry-run", "--output", str(plan_path)])
                self.assertTrue(plan_path.is_file())
                plan = B2B1BackfillPlanV1.model_validate_json(
                    plan_path.read_text(encoding="utf-8")
                )
                self.assertIn(
                    ("person_roles", 99, "missing_stable_locator"),
                    {
                        (blocker.source_table.value, blocker.source_id, blocker.code)
                        for blocker in plan.blockers
                    },
                )
                self.assertNotIn(self.app_role, stderr.getvalue())
                self.assertEqual(self._b2b_counts(), before_counts)
        finally:
            with self.owner_engine.begin() as connection:
                connection.execute(text("DELETE FROM person_roles WHERE id = 99"))

    def test_ya_dry_run_rejects_a_non_application_database_role(self) -> None:
        cli = importlib.import_module("scripts.backfill_human_review_b2b1")
        owner_url = self._role_url(
            self.owner_role,
            self.owner_password,
            "task12_cli_owner_dry",
        ).render_as_string(hide_password=False)
        with tempfile.TemporaryDirectory(prefix="b2b1-task12-owner-") as directory:
            plan_path = Path(directory) / "owner-plan.json"
            stderr = io.StringIO()
            with patch.dict(os.environ, {"DATABASE_URL": owner_url}, clear=False):
                with redirect_stderr(stderr), self.assertRaises(SystemExit):
                    cli.main(["--dry-run", "--output", str(plan_path)])
            self.assertFalse(plan_path.exists())
            self.assertNotIn(self.owner_role, stderr.getvalue())
            self.assertNotIn(self.owner_password, stderr.getvalue())

    def test_z_cli_dry_run_and_apply_noop_use_the_authorized_role_pattern(self) -> None:
        cli = importlib.import_module("scripts.backfill_human_review_b2b1")
        service = importlib.import_module("app.services.human_review_backfill")
        app_url = self._role_url(
            self.app_role,
            self.app_password,
            "task12_cli_dry",
        ).render_as_string(hide_password=False)
        owner_url = self._role_url(
            self.owner_role,
            self.owner_password,
            "task12_cli_apply",
        ).render_as_string(hide_password=False)
        environment = {
            "DATABASE_URL": app_url,
            "MIGRATION_DATABASE_URL": owner_url,
            "B2B1_APPLICATION_DB_ROLE": self.app_role,
        }
        before_counts = self._b2b_counts()
        snapshot = self._capture_as_app()
        before_snapshot = snapshot

        with tempfile.TemporaryDirectory(prefix="b2b1-task12-") as directory:
            root = Path(directory)
            plan_path = root / "plan.json"
            snapshot_path = root / "snapshot.json"
            backup_path = root / "backup.dump"
            result_path = root / "result.json"
            snapshot_path.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
            backup_path.write_bytes(b"restored disposable backup evidence")
            backup_hash = hashlib.sha256(backup_path.read_bytes()).hexdigest()

            dry_output = io.StringIO()
            with patch.dict(os.environ, environment, clear=False), redirect_stdout(dry_output):
                self.assertEqual(
                    cli.main(["--dry-run", "--output", str(plan_path)]),
                    0,
                )
            plan = B2B1BackfillPlanV1.model_validate_json(
                plan_path.read_text(encoding="utf-8")
            )
            self.assertEqual(plan.blockers, ())
            self.assertEqual(plan.invariant_snapshot_sha256, snapshot_sha256(snapshot))
            self.assertEqual(self._b2b_counts(), before_counts)
            approved_hash = service.backfill_plan_sha256(plan)

            apply_output = io.StringIO()
            with patch.dict(os.environ, environment, clear=False), redirect_stdout(apply_output):
                self.assertEqual(
                    cli.main([
                        "--apply",
                        "--plan",
                        str(plan_path),
                        "--approved-plan-sha256",
                        approved_hash,
                        "--snapshot",
                        str(snapshot_path),
                        "--backup",
                        str(backup_path),
                        "--backup-sha256",
                        backup_hash,
                        "--output",
                        str(result_path),
                    ]),
                    0,
                )
            result = B2B1BackfillApplyResultV1.model_validate_json(
                result_path.read_text(encoding="utf-8")
            )
            self.assertEqual(result.created_review_items, 8)
            self.assertEqual(result.created_decisions, 1)
            self.assertEqual(result.created_overrides, 2)
            self.assertEqual(result.created_identities, 1)
            self.assertEqual(result.created_aliases, 1)
            self.assertEqual(result.created_audit_events, 13)
            self.assertEqual(result.eligible_products_before, 2)
            self.assertEqual(result.eligible_products_after, 2)
            self.assertEqual(
                result.before_invariants_sha256,
                result.after_invariants_sha256,
            )
            verification = json.loads(apply_output.getvalue())
            self.assertEqual(verification["owner_role_verified"], True)
            self.assertEqual(verification["app_role_verified"], True)
            self.assertEqual(verification["source_share_locks"], 6)
            self.assertEqual(verification["role_downgrade_verified"], True)
            for secret in (
                app_url,
                owner_url,
                self.app_role,
                self.owner_role,
                self.app_password,
                self.owner_password,
            ):
                self.assertNotIn(secret, dry_output.getvalue())
                self.assertNotIn(secret, apply_output.getvalue())

        after_snapshot = self._capture_as_app()
        compare_b2b1_invariants(before_snapshot, after_snapshot)
        self.assertEqual(after_snapshot.eligible_products, 2)

        locked = plan.locked_decisions[0]
        stable_target_key = service.build_stable_target_key(locked.candidate.stable_target)
        expected_item_id = uuid5(
            NAMESPACE_URL,
            "human-review-b2b1:review-item:"
            f"{locked.candidate.case_type.value}:{stable_target_key}",
        )
        expected_decision_id = uuid5(
            NAMESPACE_URL,
            "human-review-b2b1:review-decision:"
            f"{locked.candidate.case_type.value}:{stable_target_key}",
        )
        with Session(self.app_engine) as db:
            item = db.get(ReviewItem, expected_item_id)
            self.assertIsNotNone(item)
            self.assertEqual(item.case_status, "resolved")
            self.assertEqual(item.scientific_status, "validated")
            self.assertEqual(item.current_decision_id, expected_decision_id)
            self.assertEqual(item.version, 1)
            decision = db.get(ReviewDecision, expected_decision_id)
            self.assertEqual(decision.decision_type, "validated")
            self.assertEqual(decision.decision_lifecycle, "approved")
            self.assertEqual(decision.actor_type, "legacy")
            self.assertEqual(decision.actor_identifier, "legacy-reviewer")
            self.assertEqual(decision.sequence, 1)
            self.assertEqual(decision.expected_case_version, 1)
            self.assertTrue(decision.locks_projection)
            self.assertIsNone(decision.previous_decision_id)
            self.assertIsNone(decision.corrects_decision_id)

            payload = IdentityDecisionPayloadV1.model_validate(decision.payload)
            self.assertEqual(payload.projection_before.case_status.value, "reopened")
            self.assertEqual(payload.projection_before.scientific_status.value, "pending")
            self.assertIsNone(payload.projection_before.current_decision_id)
            self.assertIsNone(payload.projection_before.identity)
            self.assertEqual(payload.projection_before.overrides, ())
            self.assertEqual(payload.projection_after.case_status.value, "resolved")
            self.assertEqual(payload.projection_after.scientific_status.value, "validated")
            self.assertEqual(payload.projection_after.current_decision_id, expected_decision_id)
            self.assertEqual(
                payload.projection_after.identity.canonical_identity_key,
                "legacy:locked-person",
            )
            self.assertEqual(payload.projection_after.identity.canonical_name, "Ana Perez")
            self.assertEqual(
                tuple(
                    alias.alias_normalized
                    for alias in payload.projection_after.identity.aliases
                ),
                ("ána pérez",),
            )
            self.assertEqual(
                tuple(
                    override.field_path.value
                    for override in payload.projection_after.overrides
                ),
                ("canonical_identity_key", "canonical_name"),
            )
            self.assertTrue(
                all(override.locked for override in payload.projection_after.overrides)
            )
            self.assertTrue(
                all(
                    override.scope.value == "record"
                    for override in payload.projection_after.overrides
                )
            )
            self.assertEqual(db.query(CanonicalIdentity).count(), 1)
            self.assertEqual(db.query(PersonAlias).count(), 1)
            self.assertEqual(db.query(FieldOverride).count(), 2)
            self.assertEqual(db.query(AuditEvent).count(), 13)
            self.assertEqual(
                {event_type for (event_type,) in db.query(AuditEvent.event_type).all()},
                {
                    "case_backfilled",
                    "identity_created",
                    "alias_created",
                    "override_created",
                    "locked_decision_imported",
                },
            )
            locked_by_key = {
                (
                    locked_item.candidate.case_type,
                    service.build_stable_target_key(locked_item.candidate.stable_target),
                ): locked_item
                for locked_item in plan.locked_decisions
            }
            expected_event_shapes = []
            for candidate in plan.candidates:
                candidate_key = service.build_stable_target_key(candidate.stable_target)
                object_key = f"{candidate.case_type.value}:{candidate_key}"
                candidate_item_id = uuid5(
                    NAMESPACE_URL,
                    f"human-review-b2b1:review-item:{object_key}",
                )
                correlation_id = uuid5(
                    NAMESPACE_URL,
                    f"human-review-b2b1:correlation:{object_key}",
                )
                event_types = ["case_backfilled"]
                locked_item = locked_by_key.get((candidate.case_type, candidate_key))
                if locked_item is not None:
                    event_types.append("identity_created")
                    if locked_item.alias_original is not None:
                        event_types.append("alias_created")
                    event_types.extend((
                        "override_created",
                        "override_created",
                        "locked_decision_imported",
                    ))
                expected_event_shapes.extend(
                    (candidate_key, event_type, candidate_item_id, correlation_id)
                    for event_type in event_types
                )
            events = db.query(AuditEvent).order_by(
                AuditEvent.occurred_at,
                AuditEvent.id,
            ).all()
            self.assertEqual(len(events), len(expected_event_shapes))
            self.assertEqual(
                tuple(
                    (
                        event.aggregate_key,
                        event.event_type,
                        event.review_item_id,
                        event.correlation_id,
                    )
                    for event in events
                ),
                tuple(expected_event_shapes),
            )
            previous_by_aggregate: dict[str, AuditEvent] = {}
            for event in events:
                predecessor = previous_by_aggregate.get(event.aggregate_key)
                self.assertEqual(
                    event.previous_event_id,
                    predecessor.id if predecessor is not None else None,
                )
                expected_previous_hash = (
                    predecessor.event_hash.strip()
                    if predecessor is not None
                    else None
                )
                self.assertEqual(
                    event.previous_event_hash.strip()
                    if event.previous_event_hash is not None
                    else None,
                    expected_previous_hash,
                )
                command = AuditEventCommandV1.model_validate({
                    "id": event.id,
                    "event_type": event.event_type,
                    "aggregate_type": event.aggregate_type,
                    "aggregate_key": event.aggregate_key,
                    "review_item_id": event.review_item_id,
                    "actor_user_id": event.actor_user_id,
                    "actor_identifier": event.actor_identifier,
                    "actor_capability": event.actor_capability,
                    "occurred_at": event.occurred_at,
                    "payload": event.payload,
                    "correlation_id": event.correlation_id,
                    "request_id": event.request_id,
                    "previous_event_id": event.previous_event_id,
                    "corrects_event_id": event.corrects_event_id,
                })
                self.assertEqual(
                    event.payload_schema,
                    f"audit.{event.event_type}.v1",
                )
                self.assertEqual(event.payload_version, 1)
                self.assertEqual(
                    event.event_hash.strip(),
                    compute_audit_event_hash(command, expected_previous_hash),
                )
                previous_by_aggregate[event.aggregate_key] = event

        connection, transaction, db = self._begin_apply()
        try:
            second = service.apply_backfill_plan(
                db,
                plan,
                approved_hash,
                plan.invariant_snapshot_sha256,
            )
            transaction.commit()
        finally:
            db.close()
            if transaction.is_active:
                transaction.rollback()
            connection.close()
        self.assertEqual(second.created_review_items, 0)
        self.assertEqual(second.created_decisions, 0)
        self.assertEqual(second.created_overrides, 0)
        self.assertEqual(second.created_identities, 0)
        self.assertEqual(second.created_aliases, 0)
        self.assertEqual(second.created_audit_events, 0)

        connection, transaction, db = self._begin_apply()
        try:
            with self.assertRaisesRegex(Exception, "plan hash"):
                service.apply_backfill_plan(
                    db,
                    plan,
                    "0" * 64,
                    plan.invariant_snapshot_sha256,
                )
        finally:
            db.close()
            transaction.rollback()
            connection.close()

    def test_zx_existing_locked_projection_and_audit_drift_are_rejected(self) -> None:
        service = importlib.import_module("app.services.human_review_backfill")
        plan = self._build_plan_as_app(datetime.now(timezone.utc))
        approved_hash = service.backfill_plan_sha256(plan)
        mutations = (
            "UPDATE canonical_identities SET version = version + 1",
            "UPDATE person_aliases SET version = version + 1",
            "UPDATE field_overrides SET locked = FALSE",
        )
        for statement in mutations:
            with self.subTest(statement=statement):
                connection, transaction, db = self._begin_apply()
                try:
                    db.execute(text(statement))
                    with self.assertRaisesRegex(BackfillGateError, "semantic divergence"):
                        service.apply_backfill_plan(
                            db,
                            plan,
                            approved_hash,
                            plan.invariant_snapshot_sha256,
                        )
                finally:
                    db.close()
                    transaction.rollback()
                    connection.close()

        locked = plan.locked_decisions[0]
        stable_target_key = service.build_stable_target_key(locked.candidate.stable_target)
        object_key = f"{locked.candidate.case_type.value}:{stable_target_key}"
        item_id = uuid5(
            NAMESPACE_URL,
            f"human-review-b2b1:review-item:{object_key}",
        )
        connection, transaction, db = self._begin_apply()
        try:
            append_audit_event_at_current_head(db, AuditEventCommandV1(
                id=uuid4(),
                event_type=AuditEventType.CASE_BACKFILLED,
                aggregate_type="review_item",
                aggregate_key=stable_target_key,
                review_item_id=item_id,
                actor_user_id=None,
                actor_identifier="b2b1-backfill",
                actor_capability=None,
                occurred_at=datetime.now(timezone.utc),
                payload=CaseBackfilledAuditPayloadV1(
                    kind="case_backfilled",
                    schema_version=1,
                    review_item_id=item_id,
                    case_type=locked.candidate.case_type,
                    stable_target_key=stable_target_key,
                ),
                correlation_id=uuid4(),
                request_id=None,
                previous_event_id=None,
                corrects_event_id=None,
            ))
            with self.assertRaisesRegex(BackfillGateError, "audit.*semantic divergence"):
                service.apply_backfill_plan(
                    db,
                    plan,
                    approved_hash,
                    plan.invariant_snapshot_sha256,
                )
        finally:
            db.close()
            transaction.rollback()
            connection.close()

    def test_zy_apply_requires_the_exact_global_advisory_lock(self) -> None:
        service = importlib.import_module("app.services.human_review_backfill")
        plan = self._build_plan_as_app(datetime.now(timezone.utc))
        approved_hash = service.backfill_plan_sha256(plan)
        connection = self.owner_engine.connect()
        transaction = connection.begin()
        try:
            connection.execute(text(
                "SELECT pg_advisory_xact_lock(hashtext('different-backfill-lock'))"
            ))
            connection.execute(text(
                "LOCK TABLE external_researchers, person_roles, research_entities, "
                "research_projects, scientific_production_authors, scientific_productions "
                "IN SHARE MODE"
            ))
            connection.execute(text(f'SET LOCAL ROLE "{self.app_role}"'))
            with Session(bind=connection, expire_on_commit=False) as db:
                with self.assertRaisesRegex(BackfillGateError, "global backfill advisory lock"):
                    service.apply_backfill_plan(
                        db,
                        plan,
                        approved_hash,
                        plan.invariant_snapshot_sha256,
                    )
        finally:
            transaction.rollback()
            connection.close()

    def test_zz_concurrent_apply_waits_then_rebuilds_as_noop(self) -> None:
        service = importlib.import_module("app.services.human_review_backfill")
        plan = self._build_plan_as_app(datetime.now(timezone.utc))
        approved_hash = service.backfill_plan_sha256(plan)
        holder = self.owner_engine.connect()
        holder_transaction = holder.begin()
        holder.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_name))"),
            {"lock_name": GLOBAL_BACKFILL_LOCK_NAME},
        )
        started = Event()

        def apply_after_lock() -> B2B1BackfillApplyResultV1:
            started.set()
            connection, transaction, db = self._begin_apply()
            try:
                result = service.apply_backfill_plan(
                    db,
                    plan,
                    approved_hash,
                    plan.invariant_snapshot_sha256,
                )
                transaction.commit()
                return result
            finally:
                db.close()
                if transaction.is_active:
                    transaction.rollback()
                connection.close()

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(apply_after_lock)
            self.assertTrue(started.wait(timeout=5))
            with self.assertRaises(FutureTimeout):
                future.result(timeout=0.3)
            holder_transaction.rollback()
            holder.close()
            result = future.result(timeout=15)
        self.assertEqual(result.created_review_items, 0)
        self.assertEqual(result.created_decisions, 0)
        self.assertEqual(result.created_overrides, 0)
        self.assertEqual(result.created_identities, 0)
        self.assertEqual(result.created_aliases, 0)
        self.assertEqual(result.created_audit_events, 0)

        candidate = plan.candidates[0]
        stable_target_key = service.build_stable_target_key(candidate.stable_target)
        item_id = uuid5(
            NAMESPACE_URL,
            "human-review-b2b1:review-item:"
            f"{candidate.case_type.value}:{stable_target_key}",
        )
        connection, transaction, db = self._begin_apply()
        try:
            db.execute(text(
                "UPDATE review_items "
                "SET possible_kpi_impact = NOT possible_kpi_impact WHERE id = :item_id"
            ), {"item_id": item_id})
            with self.assertRaisesRegex(BackfillGateError, "semantic divergence"):
                service.apply_backfill_plan(
                    db,
                    plan,
                    approved_hash,
                    plan.invariant_snapshot_sha256,
                )
        finally:
            db.close()
            transaction.rollback()
            connection.close()


if __name__ == "__main__":
    unittest.main()
