from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib
import inspect
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from psycopg.errors import InsufficientPrivilege
from pydantic import ValidationError
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine, URL, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core import migrations as migration_registry
from app.models.entities import User
from app.models.human_review_access import UserB2BCapability
from app.models.human_review_audit import AuditEvent
from app.models.human_review_enums import B2BAction, B2BCapability
from tests.support.postgres import (
    create_pre_0022_domain_catalog,
    require_b2b1_test_database_url,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
AUTHORIZATION_MODULE = "app.services.human_review_authorization"
CAPABILITIES_MODULE = "app.services.human_review_capabilities"
SCHEMA_MODULE = "app.schemas.human_review_operations"
CLI_MODULE = "scripts.assign_b2b_capabilities"
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


def _email(label: str = "account") -> str:
    suffix = uuid4().hex
    return f"{label}-{suffix}" + chr(64) + f"{suffix}.com"


def _api(testcase: unittest.TestCase):
    try:
        authorization = importlib.import_module(AUTHORIZATION_MODULE)
        capabilities = importlib.import_module(CAPABILITIES_MODULE)
        schemas = importlib.import_module(SCHEMA_MODULE)
        cli = importlib.import_module(CLI_MODULE)
        for name in (
            "resolve_b2b_capability",
            "authorize_b2b_action",
            "B2BAccessDenied",
        ):
            getattr(authorization, name)
        for name in (
            "plan_capability_assignments",
            "apply_capability_assignments",
            "CapabilityAssignmentConflict",
        ):
            getattr(capabilities, name)
        for name in (
            "ApprovedAccountAssignmentV1",
            "ApprovedAccountAssignmentsV1",
            "CapabilityAssignmentPlanEntryV1",
            "CapabilityAssignmentPlanV1",
            "CapabilityAssignmentResultV1",
        ):
            getattr(schemas, name)
        getattr(cli, "main")
        return authorization, capabilities, schemas, cli
    except (ModuleNotFoundError, AttributeError) as exc:
        testcase.fail(f"Task 10 authorization/capability surface is absent: {exc}")
        raise AssertionError("unreachable")


def _manifest(schemas, assignments, approval_reference: str | None = None):
    return schemas.ApprovedAccountAssignmentsV1(
        schema_version=1,
        approval_reference=approval_reference or f"approval-{uuid4().hex}",
        assignments=tuple(assignments),
    )


def _assignment(schemas, user_id: int, email: str, capability: B2BCapability):
    return schemas.ApprovedAccountAssignmentV1(
        user_id=user_id,
        email=email,
        capability=capability,
    )


class HumanReviewAuthorizationContractTests(unittest.TestCase):
    def test_exact_public_signatures_and_closed_contract_fields(self) -> None:
        authorization, capabilities, schemas, _cli = _api(self)
        expected_signatures = {
            authorization.resolve_b2b_capability: ("db", "user_id"),
            authorization.authorize_b2b_action: ("db", "user", "action"),
            capabilities.plan_capability_assignments: ("db", "manifest"),
            capabilities.apply_capability_assignments: (
                "db",
                "manifest",
                "approved_manifest_sha256",
                "operator_identifier",
            ),
        }
        for function, parameters in expected_signatures.items():
            with self.subTest(function=function.__name__):
                self.assertEqual(tuple(inspect.signature(function).parameters), parameters)

        expected_fields = {
            schemas.ApprovedAccountAssignmentV1:
                ("user_id", "email", "capability"),
            schemas.ApprovedAccountAssignmentsV1:
                ("schema_version", "approval_reference", "assignments"),
            schemas.CapabilityAssignmentPlanEntryV1:
                ("user_id", "email", "capability", "action", "reason"),
            schemas.CapabilityAssignmentPlanV1: (
                "schema_version",
                "manifest_sha256",
                "entries",
                "insert_count",
                "unchanged_count",
                "conflict_count",
            ),
            schemas.CapabilityAssignmentResultV1: (
                "schema_version",
                "manifest_sha256",
                "inserted",
                "unchanged",
                "audit_events_created",
            ),
        }
        for contract, fields in expected_fields.items():
            with self.subTest(contract=contract.__name__):
                self.assertEqual(tuple(contract.model_fields), fields)
                self.assertEqual(contract.model_config.get("extra"), "forbid")
                self.assertTrue(contract.model_config.get("frozen"))

    def test_manifest_rejects_invalid_version_empty_blank_duplicate_and_contradictory_entries(self) -> None:
        _authorization, _capabilities, schemas, _cli = _api(self)
        email_one = _email("one")
        email_two = _email("two")
        base_assignment = {
            "user_id": 1,
            "email": email_one,
            "capability": B2BCapability.RESEARCH_MANAGER.value,
        }
        invalid_manifests = (
            {
                "schema_version": 2,
                "approval_reference": "approved",
                "assignments": [base_assignment],
            },
            {
                "schema_version": 1,
                "approval_reference": "approved",
                "assignments": [],
            },
            {
                "schema_version": 1,
                "approval_reference": "   ",
                "assignments": [base_assignment],
            },
            {
                "schema_version": 1,
                "approval_reference": "approved",
                "assignments": [
                    base_assignment,
                    base_assignment | {
                        "email": email_two,
                        "capability": B2BCapability.SYSTEM_ADMIN.value,
                    },
                ],
            },
            {
                "schema_version": 1,
                "approval_reference": "approved",
                "assignments": [
                    base_assignment,
                    base_assignment | {"user_id": 2},
                ],
            },
        )
        for value in invalid_manifests:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                schemas.ApprovedAccountAssignmentsV1.model_validate(value)

        with self.assertRaises(ValidationError):
            schemas.ApprovedAccountAssignmentV1(
                user_id=0,
                email=email_one,
                capability=B2BCapability.SYSTEM_ADMIN,
            )

    def test_cli_surface_is_environment_only_closed_and_deterministic(self) -> None:
        _authorization, _capabilities, _schemas, cli = _api(self)
        parser = cli.build_parser()
        option_strings = {
            option
            for action in parser._actions
            for option in action.option_strings
        }
        self.assertTrue({
            "--dry-run",
            "--apply",
            "--manifest",
            "--approved-manifest-sha256",
            "--operator-identifier",
            "--output",
        }.issubset(option_strings))
        for forbidden in ("--database-url", "--db-url", "--email", "--user-id"):
            self.assertNotIn(forbidden, option_strings)
        source = inspect.getsource(cli)
        self.assertIn("DATABASE_URL", source)
        self.assertIn("sort_keys=True", source)
        self.assertNotIn("MIGRATION_DATABASE_URL", source)

    def test_new_sources_have_no_literal_email_accounts_or_internal_commit(self) -> None:
        authorization, capabilities, _schemas, cli = _api(self)
        sources = {
            "authorization": inspect.getsource(authorization),
            "capabilities": inspect.getsource(capabilities),
            "cli": inspect.getsource(cli),
            "tests": Path(__file__).read_text(encoding="utf-8"),
        }
        literal_email_pattern = re.compile(
            r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
            r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
            r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+"
        )
        for name, source in sources.items():
            with self.subTest(source=name):
                self.assertIsNone(literal_email_pattern.search(source))
        self.assertNotIn(".commit(", sources["capabilities"])
        self.assertNotIn("users.role =", sources["authorization"])
        self.assertNotIn("users.role =", sources["capabilities"])

    def test_approved_migrations_remain_unchanged_and_registry_appends_only_0022(self) -> None:
        _api(self)
        migrations_dir = BACKEND_ROOT / "app/migrations/versions"
        for name, expected_hash in MIGRATION_HASHES.items():
            with self.subTest(name=name):
                self.assertEqual(
                    hashlib.sha256((migrations_dir / name).read_bytes()).hexdigest(),
                    expected_hash,
                )
        self.assertEqual(len(migration_registry.MIGRATIONS), 22)
        self.assertEqual(len({version for version, _upgrade in migration_registry.MIGRATIONS}), 22)
        self.assertEqual(
            migration_registry.MIGRATIONS[-1][0],
            "20260827_0022_scoped_human_review_authorization",
        )
        self.assertEqual(
            hashlib.sha256(
                (migrations_dir / "20260827_0022_scoped_human_review_authorization.py").read_bytes()
            ).hexdigest(),
            "2f6b9ace942723f5a2095d1673e3f8051c5cb81e58e45141c4b4a31f4c4ddfcf",
        )
        privilege_hash = hashlib.sha256(
            (BACKEND_ROOT / "scripts/configure_human_review_privileges.py").read_bytes()
        ).hexdigest()
        self.assertEqual(
            privilege_hash,
            "d955fe447a56e0a494d4f4bc8cf56e622a82c03506f6b19ddffe394d4c7fdec6",
        )


class HumanReviewAuthorizationPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_url = require_b2b1_test_database_url()
        suffix = uuid4().hex[:12]
        cls.owner_role = f"b2b1_owner_{suffix}"
        cls.app_role = f"b2b1_app_{suffix}"
        cls.schema_name = f"b2b1_auth_{suffix}"
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
            cls.owner_url = cls._role_url(
                cls.owner_role,
                cls.owner_password,
                "task10_owner",
            )
            cls.app_url = cls._role_url(
                cls.app_role,
                cls.app_password,
                "task10_app",
            )
            cls.owner_engine = create_engine(cls.owner_url, pool_pre_ping=True)
            cls.app_engine = create_engine(cls.app_url, pool_pre_ping=True)
            cls._apply_b2b_migrations()
            privilege_module = importlib.import_module(
                "scripts.configure_human_review_privileges"
            )
            privilege_module.configure_human_review_privileges(
                cls.owner_engine,
                cls.app_role,
            )
            with cls.owner_engine.begin() as connection:
                connection.execute(text(
                    f'GRANT SELECT, UPDATE ON TABLE users TO "{cls.app_role}"'
                ))
        except Exception:
            cls._cleanup()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        cls._cleanup()

    def setUp(self) -> None:
        with self.owner_engine.begin() as connection:
            protected_triggers = (
                ("audit_events", "trg_audit_events_append_only"),
                ("review_decisions", "trg_review_decisions_append_only"),
            )
            for table_name, trigger_name in protected_triggers:
                connection.execute(text(
                    f"ALTER TABLE {table_name} DISABLE TRIGGER {trigger_name}"
                ))
            try:
                connection.execute(text(
                    "TRUNCATE TABLE audit_events, user_b2b_capabilities, users CASCADE"
                ))
            finally:
                for table_name, trigger_name in protected_triggers:
                    connection.execute(text(
                        f"ALTER TABLE {table_name} ENABLE TRIGGER {trigger_name}"
                    ))

    @classmethod
    def _role_url(
        cls,
        role: str,
        password: str,
        application_name: str,
    ) -> URL:
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
                for role in (
                    getattr(cls, "app_role", ""),
                    getattr(cls, "owner_role", ""),
                ):
                    if role:
                        connection.execute(text(f'DROP OWNED BY "{role}" CASCADE'))
                schema_name = getattr(cls, "schema_name", "")
                if schema_name:
                    connection.execute(text(
                        f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'
                    ))
                for role in (
                    getattr(cls, "app_role", ""),
                    getattr(cls, "owner_role", ""),
                ):
                    if role:
                        connection.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        finally:
            admin_engine.dispose()

    def _insert_user(
        self,
        *,
        role: str = "FACULTY_ADMIN",
        is_active: bool = True,
        label: str = "account",
        faculty_id: int | None = None,
        career_id: int | None = None,
    ) -> tuple[int, str]:
        user_id = int(uuid4().int % 1_000_000_000) + 1
        email = _email(label)
        with self.owner_engine.begin() as connection:
            connection.execute(text(
                """
                INSERT INTO users (
                    id, email, full_name, hashed_password, role,
                    faculty_id, career_id, is_active
                ) VALUES (
                    :id, :email, :full_name, :hashed_password, :role,
                    :faculty_id, :career_id, :is_active
                )
                """
            ), {
                "id": user_id,
                "email": email,
                "full_name": f"Test Account {uuid4().hex}",
                "hashed_password": f"not-a-real-secret-{uuid4().hex}",
                "role": role,
                "faculty_id": faculty_id,
                "career_id": career_id,
                "is_active": is_active,
            })
        return user_id, email

    def _insert_capability(
        self,
        user_id: int,
        capability: B2BCapability,
        *,
        corrupt_career_fixture: bool = False,
    ) -> UUID:
        assignment_id = uuid4()
        with self.owner_engine.begin() as connection:
            if corrupt_career_fixture:
                connection.execute(text(
                    "ALTER TABLE user_b2b_capabilities "
                    "DISABLE TRIGGER trg_user_b2b_capabilities_reject_career"
                ))
            try:
                connection.execute(text(
                    """
                    INSERT INTO user_b2b_capabilities (
                        id, user_id, capability, is_active, approval_reference,
                        approved_input_sha256, assigned_by_identifier
                    ) VALUES (
                        :id, :user_id, :capability, TRUE, :approval_reference,
                        :approved_input_sha256, :assigned_by_identifier
                    )
                    """
                ), {
                    "id": assignment_id,
                    "user_id": user_id,
                    "capability": capability.value,
                    "approval_reference": f"fixture-{uuid4().hex}",
                    "approved_input_sha256": "a" * 64,
                    "assigned_by_identifier": f"fixture:{uuid4().hex}",
                })
            finally:
                if corrupt_career_fixture:
                    connection.execute(text(
                        "ALTER TABLE user_b2b_capabilities "
                        "ENABLE TRIGGER trg_user_b2b_capabilities_reject_career"
                    ))
        return assignment_id

    def _load_user(self, db: Session, user_id: int) -> User:
        user = db.get(User, user_id)
        self.assertIsNotNone(user)
        return user

    def _plan(self, capabilities, manifest):
        with Session(self.app_engine) as db:
            return capabilities.plan_capability_assignments(db, manifest)

    def _counts(self) -> tuple[int, int]:
        with self.owner_engine.connect() as connection:
            return (
                connection.execute(text(
                    "SELECT COUNT(*) FROM user_b2b_capabilities"
                )).scalar_one(),
                connection.execute(text(
                    "SELECT COUNT(*) FROM audit_events"
                )).scalar_one(),
            )

    def test_deny_by_default_and_exact_research_and_system_admin_matrices(self) -> None:
        authorization, _capabilities, _schemas, _cli = _api(self)
        unassigned_id, _unassigned_email = self._insert_user(label="unassigned")
        research_id, _research_email = self._insert_user(label="research", faculty_id=1)
        system_id, _system_email = self._insert_user(label="system", faculty_id=1)
        self._insert_capability(research_id, B2BCapability.RESEARCH_MANAGER)
        self._insert_capability(system_id, B2BCapability.SYSTEM_ADMIN)

        expected = {
            B2BCapability.RESEARCH_MANAGER: {
                B2BAction.VIEW_FOUNDATIONS,
                B2BAction.VIEW_AUDIT,
                B2BAction.APPLY_SCIENTIFIC,
                B2BAction.PROPOSE_SCIENTIFIC,
                B2BAction.REVERT_SCIENTIFIC,
            },
            B2BCapability.SYSTEM_ADMIN: {
                B2BAction.VIEW_FOUNDATIONS,
                B2BAction.VIEW_AUDIT,
                B2BAction.APPLY_SCIENTIFIC,
                B2BAction.REVERT_SCIENTIFIC,
                B2BAction.MANAGE_TECHNICAL_ACCESS,
            },
        }
        with Session(self.app_engine) as db:
            unassigned = self._load_user(db, unassigned_id)
            self.assertIsNone(
                authorization.resolve_b2b_capability(db, unassigned_id)
            )
            for action in B2BAction:
                with self.subTest(capability="none", action=action):
                    with self.assertRaises(authorization.B2BAccessDenied):
                        authorization.authorize_b2b_action(db, unassigned, action)

            for user_id, capability in (
                (research_id, B2BCapability.RESEARCH_MANAGER),
                (system_id, B2BCapability.SYSTEM_ADMIN),
            ):
                user = self._load_user(db, user_id)
                self.assertEqual(
                    authorization.resolve_b2b_capability(db, user_id),
                    capability,
                )
                for action in B2BAction:
                    with self.subTest(capability=capability, action=action):
                        if action in expected[capability]:
                            expected_capability = (
                                B2BCapability.RESEARCH_MANAGER
                                if capability is B2BCapability.SYSTEM_ADMIN
                                and action in {
                                    B2BAction.APPLY_SCIENTIFIC,
                                    B2BAction.REVERT_SCIENTIFIC,
                                }
                                else capability
                            )
                            self.assertEqual(
                                authorization.authorize_b2b_action(db, user, action),
                                expected_capability,
                            )
                        else:
                            with self.assertRaises(authorization.B2BAccessDenied):
                                authorization.authorize_b2b_action(db, user, action)

    def test_scoped_roles_are_authorized_and_unassigned_users_fail_closed(self) -> None:
        authorization, _capabilities, _schemas, _cli = _api(self)
        career_id, _career_email = self._insert_user(
            role="CAREER_MANAGER",
            label="career",
            career_id=1,
        )
        faculty_id, _faculty_email = self._insert_user(label="faculty", faculty_id=1)
        seed_probe_id, _seed_probe_email = self._insert_user(label="seed-probe")
        self._insert_capability(
            career_id,
            B2BCapability.RESEARCH_MANAGER,
            corrupt_career_fixture=True,
        )
        with Session(self.app_engine) as db:
            scoped_actions = {
                B2BAction.VIEW_FOUNDATIONS,
                B2BAction.VIEW_AUDIT,
                B2BAction.APPLY_SCIENTIFIC,
                B2BAction.REVERT_SCIENTIFIC,
            }
            for user_id in (career_id, faculty_id):
                user = self._load_user(db, user_id)
                self.assertIsNone(
                    authorization.resolve_b2b_capability(db, user_id)
                )
                for action in B2BAction:
                    with self.subTest(user_id=user_id, action=action):
                        if action in scoped_actions:
                            self.assertEqual(
                                authorization.authorize_b2b_action(db, user, action),
                                B2BCapability.RESEARCH_MANAGER,
                            )
                        else:
                            with self.assertRaises(authorization.B2BAccessDenied):
                                authorization.authorize_b2b_action(db, user, action)

            seed_probe = self._load_user(db, seed_probe_id)
            self.assertIsNone(authorization.resolve_b2b_capability(db, seed_probe_id))
            for action in B2BAction:
                with self.subTest(user_id=seed_probe_id, action=action):
                    with self.assertRaises(authorization.B2BAccessDenied):
                        authorization.authorize_b2b_action(db, seed_probe, action)

    def test_resolver_is_read_only_and_denies_inactive_user_or_invalid_action(self) -> None:
        authorization, _capabilities, _schemas, _cli = _api(self)
        active_id, _active_email = self._insert_user(label="read-only")
        inactive_id, _inactive_email = self._insert_user(
            is_active=False,
            label="inactive",
        )
        self._insert_capability(active_id, B2BCapability.SYSTEM_ADMIN)
        before = self._counts()
        with Session(self.app_engine) as db:
            active = self._load_user(db, active_id)
            self.assertEqual(
                authorization.resolve_b2b_capability(db, active_id),
                B2BCapability.SYSTEM_ADMIN,
            )
            self.assertIsNone(
                authorization.resolve_b2b_capability(db, inactive_id)
            )
            with self.assertRaises(authorization.B2BAccessDenied):
                authorization.authorize_b2b_action(db, active, "unknown_action")
            db.rollback()
        self.assertEqual(self._counts(), before)

    def test_plan_classifies_insert_unchanged_and_every_data_conflict_without_writes(self) -> None:
        _authorization, capabilities, schemas, _cli = _api(self)
        insert_id, insert_email = self._insert_user(label="insert")
        unchanged_id, unchanged_email = self._insert_user(label="unchanged")
        different_id, different_email = self._insert_user(label="different")
        mismatch_id, _mismatch_email = self._insert_user(label="mismatch")
        inactive_id, inactive_email = self._insert_user(
            is_active=False,
            label="inactive",
        )
        career_id, career_email = self._insert_user(
            role="CAREER_MANAGER",
            label="career",
        )
        missing_id = int(uuid4().int % 1_000_000_000) + 1
        self._insert_capability(
            unchanged_id,
            B2BCapability.RESEARCH_MANAGER,
        )
        self._insert_capability(
            different_id,
            B2BCapability.SYSTEM_ADMIN,
        )
        manifest = _manifest(schemas, (
            _assignment(
                schemas,
                insert_id,
                insert_email,
                B2BCapability.RESEARCH_MANAGER,
            ),
            _assignment(
                schemas,
                unchanged_id,
                unchanged_email,
                B2BCapability.RESEARCH_MANAGER,
            ),
            _assignment(
                schemas,
                different_id,
                different_email,
                B2BCapability.RESEARCH_MANAGER,
            ),
            _assignment(
                schemas,
                mismatch_id,
                _email("wrong-email"),
                B2BCapability.SYSTEM_ADMIN,
            ),
            _assignment(
                schemas,
                inactive_id,
                inactive_email,
                B2BCapability.SYSTEM_ADMIN,
            ),
            _assignment(
                schemas,
                career_id,
                career_email,
                B2BCapability.RESEARCH_MANAGER,
            ),
            _assignment(
                schemas,
                missing_id,
                _email("missing"),
                B2BCapability.SYSTEM_ADMIN,
            ),
        ))
        before = self._counts()
        plan = self._plan(capabilities, manifest)
        second_plan = self._plan(capabilities, manifest)
        self.assertEqual(
            tuple(entry.action for entry in plan.entries),
            (
                "insert",
                "unchanged",
                "conflict",
                "conflict",
                "conflict",
                "conflict",
                "conflict",
            ),
        )
        self.assertEqual((plan.insert_count, plan.unchanged_count, plan.conflict_count), (1, 1, 5))
        self.assertEqual(plan, second_plan)
        self.assertRegex(plan.manifest_sha256, r"^[0-9a-f]{64}$")
        self.assertEqual(self._counts(), before)

    def test_canonical_manifest_hash_ignores_json_object_key_order_but_protects_fields(self) -> None:
        _authorization, capabilities, schemas, _cli = _api(self)
        user_id, email = self._insert_user(label="canonical-hash")
        approval_reference = f"approved-{uuid4().hex}"
        first = schemas.ApprovedAccountAssignmentsV1.model_validate({
            "schema_version": 1,
            "approval_reference": approval_reference,
            "assignments": [{
                "user_id": user_id,
                "email": email,
                "capability": B2BCapability.RESEARCH_MANAGER.value,
            }],
        })
        second = schemas.ApprovedAccountAssignmentsV1.model_validate({
            "assignments": [{
                "capability": B2BCapability.RESEARCH_MANAGER.value,
                "email": email,
                "user_id": user_id,
            }],
            "approval_reference": approval_reference,
            "schema_version": 1,
        })
        changed = first.model_copy(update={
            "approval_reference": f"approved-{uuid4().hex}",
        })
        first_plan = self._plan(capabilities, first)
        self.assertEqual(first_plan.manifest_sha256, self._plan(capabilities, second).manifest_sha256)
        self.assertNotEqual(first_plan.manifest_sha256, self._plan(capabilities, changed).manifest_sha256)

    def test_apply_is_atomic_audited_preserves_role_and_is_idempotent(self) -> None:
        _authorization, capabilities, schemas, _cli = _api(self)
        first_id, first_email = self._insert_user(label="apply-one")
        second_id, second_email = self._insert_user(label="apply-two")
        manifest = _manifest(schemas, (
            _assignment(
                schemas,
                first_id,
                first_email,
                B2BCapability.RESEARCH_MANAGER,
            ),
            _assignment(
                schemas,
                second_id,
                second_email,
                B2BCapability.SYSTEM_ADMIN,
            ),
        ))
        manifest_hash = self._plan(capabilities, manifest).manifest_sha256
        operator = f"approved-operator:{uuid4().hex}"
        with Session(self.app_engine) as db, db.begin():
            first = capabilities.apply_capability_assignments(
                db,
                manifest,
                manifest_hash,
                operator,
            )
            self.assertEqual((first.inserted, first.unchanged, first.audit_events_created), (2, 0, 2))
        with Session(self.app_engine) as db, db.begin():
            second = capabilities.apply_capability_assignments(
                db,
                manifest,
                manifest_hash,
                operator,
            )
            self.assertEqual((second.inserted, second.unchanged, second.audit_events_created), (0, 2, 0))

        with Session(self.app_engine) as db:
            assignments = tuple(db.scalars(
                select(UserB2BCapability).order_by(UserB2BCapability.user_id)
            ))
            events = tuple(db.scalars(
                select(AuditEvent).order_by(AuditEvent.aggregate_key)
            ))
            roles = tuple(db.execute(
                select(User.id, User.role).order_by(User.id)
            ))
        self.assertEqual(len(assignments), 2)
        self.assertEqual(len(events), 2)
        self.assertTrue(all(row.approval_reference == manifest.approval_reference for row in assignments))
        self.assertTrue(all(row.approved_input_sha256.strip() == manifest_hash for row in assignments))
        self.assertTrue(all(row.assigned_by_identifier == operator for row in assignments))
        self.assertTrue(all(role.value == "FACULTY_ADMIN" for _user_id, role in roles))
        self.assertTrue(all(event.event_type == "capability_assigned" for event in events))
        self.assertTrue(all(event.actor_identifier == operator for event in events))
        self.assertTrue(all(event.previous_event_id is None for event in events))
        self.assertTrue(all(event.payload["action"] == "assigned" for event in events))
        self.assertEqual(
            {event.payload["assignment_id"] for event in events},
            {str(row.id) for row in assignments},
        )

    def test_wrong_hash_and_manifest_conflict_rollback_the_whole_apply(self) -> None:
        _authorization, capabilities, schemas, _cli = _api(self)
        wrong_hash_id, wrong_hash_email = self._insert_user(label="wrong-hash")
        wrong_hash_manifest = _manifest(schemas, (
            _assignment(
                schemas,
                wrong_hash_id,
                wrong_hash_email,
                B2BCapability.RESEARCH_MANAGER,
            ),
        ))
        with Session(self.app_engine) as db:
            with self.assertRaises(capabilities.CapabilityAssignmentConflict):
                capabilities.apply_capability_assignments(
                    db,
                    wrong_hash_manifest,
                    "f" * 64,
                    f"operator:{uuid4().hex}",
                )
            self.assertFalse(db.in_transaction())
        self.assertEqual(self._counts(), (0, 0))

        insert_id, insert_email = self._insert_user(label="atomic-insert")
        conflict_id, conflict_email = self._insert_user(label="atomic-conflict")
        self._insert_capability(conflict_id, B2BCapability.SYSTEM_ADMIN)
        conflict_manifest = _manifest(schemas, (
            _assignment(
                schemas,
                insert_id,
                insert_email,
                B2BCapability.RESEARCH_MANAGER,
            ),
            _assignment(
                schemas,
                conflict_id,
                conflict_email,
                B2BCapability.RESEARCH_MANAGER,
            ),
        ))
        manifest_hash = self._plan(capabilities, conflict_manifest).manifest_sha256
        before = self._counts()
        with Session(self.app_engine) as db:
            with self.assertRaises(capabilities.CapabilityAssignmentConflict):
                capabilities.apply_capability_assignments(
                    db,
                    conflict_manifest,
                    manifest_hash,
                    f"operator:{uuid4().hex}",
                )
            self.assertFalse(db.in_transaction())
        self.assertEqual(self._counts(), before)
        with self.owner_engine.connect() as connection:
            inserted = connection.execute(text(
                "SELECT COUNT(*) FROM user_b2b_capabilities WHERE user_id = :user_id"
            ), {"user_id": insert_id}).scalar_one()
        self.assertEqual(inserted, 0)

    def test_concurrent_same_manifest_has_one_assignment_and_translates_integrity_error(self) -> None:
        _authorization, capabilities, schemas, _cli = _api(self)
        user_id, email = self._insert_user(label="concurrent")
        manifest = _manifest(schemas, (
            _assignment(
                schemas,
                user_id,
                email,
                B2BCapability.RESEARCH_MANAGER,
            ),
        ))
        manifest_hash = self._plan(capabilities, manifest).manifest_sha256
        barrier = threading.Barrier(2)
        original_plan = capabilities.plan_capability_assignments
        outcomes: queue.Queue[tuple[str, object]] = queue.Queue()

        def synchronized_plan(db, current_manifest):
            plan = original_plan(db, current_manifest)
            if plan.insert_count:
                barrier.wait(timeout=15)
            return plan

        def worker(number: int) -> None:
            try:
                with Session(self.app_engine) as db, db.begin():
                    result = capabilities.apply_capability_assignments(
                        db,
                        manifest,
                        manifest_hash,
                        f"concurrent-operator-{number}:{uuid4().hex}",
                    )
                outcomes.put(("success", result))
            except Exception as exc:
                outcomes.put(("error", exc))

        with patch.object(
            capabilities,
            "plan_capability_assignments",
            new=synchronized_plan,
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = tuple(
                    executor.submit(worker, number) for number in (1, 2)
                )
                for future in futures:
                    future.result(timeout=30)

        actual = (outcomes.get_nowait(), outcomes.get_nowait())
        successes = tuple(value for kind, value in actual if kind == "success")
        errors = tuple(value for kind, value in actual if kind == "error")
        self.assertEqual(len(successes), 1, actual)
        self.assertEqual(len(errors), 1, actual)
        self.assertEqual(successes[0].inserted, 1)
        self.assertIsInstance(errors[0], capabilities.CapabilityAssignmentConflict)
        self.assertIn("integrity", str(errors[0]).lower())
        self.assertEqual(self._counts(), (1, 1))

    def test_audit_chain_payload_and_application_privileges_remain_valid(self) -> None:
        _authorization, capabilities, schemas, _cli = _api(self)
        user_id, email = self._insert_user(label="audit")
        manifest = _manifest(schemas, (
            _assignment(
                schemas,
                user_id,
                email,
                B2BCapability.SYSTEM_ADMIN,
            ),
        ))
        manifest_hash = self._plan(capabilities, manifest).manifest_sha256
        operator = f"audit-operator:{uuid4().hex}"
        with Session(self.app_engine) as db, db.begin():
            capabilities.apply_capability_assignments(
                db,
                manifest,
                manifest_hash,
                operator,
            )
        audit_service = importlib.import_module("app.services.human_review_audit")
        with Session(self.app_engine) as db:
            event = db.scalars(select(AuditEvent)).one()
            command = audit_service._row_as_command(event)
            self.assertEqual(
                audit_service.compute_audit_event_hash(command, None),
                event.event_hash.strip(),
            )
            self.assertEqual(event.payload["user_id"], user_id)
            self.assertEqual(event.payload["capability"], "SYSTEM_ADMIN")
            self.assertEqual(event.payload["approved_input_sha256"], manifest_hash)

        qualified_audit = f'"{self.schema_name}"."audit_events"'
        qualified_capabilities = f'"{self.schema_name}"."user_b2b_capabilities"'
        with self.admin_engine.connect() as connection:
            for privilege, expected in (
                ("SELECT", True),
                ("INSERT", True),
                ("UPDATE", False),
                ("DELETE", False),
                ("TRUNCATE", False),
            ):
                with self.subTest(table="audit_events", privilege=privilege):
                    self.assertEqual(
                        connection.execute(text(
                            "SELECT has_table_privilege(:role, :object_name, :privilege)"
                        ), {
                            "role": self.app_role,
                            "object_name": qualified_audit,
                            "privilege": privilege,
                        }).scalar_one(),
                        expected,
                    )
            for privilege, expected in (
                ("SELECT", True),
                ("INSERT", True),
                ("UPDATE", True),
                ("DELETE", False),
                ("TRUNCATE", False),
            ):
                with self.subTest(table="user_b2b_capabilities", privilege=privilege):
                    self.assertEqual(
                        connection.execute(text(
                            "SELECT has_table_privilege(:role, :object_name, :privilege)"
                        ), {
                            "role": self.app_role,
                            "object_name": qualified_capabilities,
                            "privilege": privilege,
                        }).scalar_one(),
                        expected,
                    )

        for statement in (
            "UPDATE audit_events SET actor_identifier = 'forbidden'",
            "DELETE FROM audit_events",
            "TRUNCATE TABLE audit_events",
            f'SET ROLE "{self.owner_role}"',
        ):
            with self.subTest(statement=statement), self.assertRaises(DBAPIError) as caught:
                with self.app_engine.begin() as connection:
                    connection.execute(text(statement))
            self.assertIsInstance(caught.exception.orig, InsufficientPrivilege)

    def test_cli_dry_run_apply_and_second_apply_use_only_environment_connection(self) -> None:
        _authorization, _capabilities, schemas, _cli = _api(self)
        user_id, email = self._insert_user(label="cli")
        manifest = _manifest(schemas, (
            _assignment(
                schemas,
                user_id,
                email,
                B2BCapability.RESEARCH_MANAGER,
            ),
        ))
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(BACKEND_ROOT)
        environment["DATABASE_URL"] = self.app_url.render_as_string(
            hide_password=False
        )
        with tempfile.TemporaryDirectory(prefix="b2b1-task10-") as directory:
            root = Path(directory)
            manifest_path = root / "manifest.json"
            dry_path = root / "dry.json"
            first_apply_path = root / "apply-first.json"
            second_apply_path = root / "apply-second.json"
            manifest_path.write_text(
                json.dumps(
                    manifest.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            dry = subprocess.run(
                [
                    sys.executable,
                    "scripts/assign_b2b_capabilities.py",
                    "--dry-run",
                    "--manifest",
                    str(manifest_path),
                    "--output",
                    str(dry_path),
                ],
                cwd=BACKEND_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertEqual(self._counts(), (0, 0))
            dry_output = json.loads(dry_path.read_text(encoding="utf-8"))
            self.assertEqual((dry_output["insert_count"], dry_output["conflict_count"]), (1, 0))
            manifest_hash = dry_output["manifest_sha256"]

            def run_apply(output_path: Path):
                return subprocess.run(
                    [
                        sys.executable,
                        "scripts/assign_b2b_capabilities.py",
                        "--apply",
                        "--manifest",
                        str(manifest_path),
                        "--approved-manifest-sha256",
                        manifest_hash,
                        "--operator-identifier",
                        f"cli-operator:{uuid4().hex}",
                        "--output",
                        str(output_path),
                    ],
                    cwd=BACKEND_ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                )

            first_apply = run_apply(first_apply_path)
            self.assertEqual(first_apply.returncode, 0, first_apply.stderr)
            self.assertEqual(
                json.loads(first_apply_path.read_text(encoding="utf-8")),
                {
                    "schema_version": 1,
                    "manifest_sha256": manifest_hash,
                    "inserted": 1,
                    "unchanged": 0,
                    "audit_events_created": 1,
                },
            )
            second_apply = run_apply(second_apply_path)
            self.assertEqual(second_apply.returncode, 0, second_apply.stderr)
            second_output = json.loads(second_apply_path.read_text(encoding="utf-8"))
            self.assertEqual(
                (second_output["inserted"], second_output["unchanged"], second_output["audit_events_created"]),
                (0, 1, 0),
            )
            self.assertEqual(self._counts(), (1, 1))


if __name__ == "__main__":
    unittest.main()
