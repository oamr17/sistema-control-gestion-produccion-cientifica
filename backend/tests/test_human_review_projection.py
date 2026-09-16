from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import copy
from datetime import datetime, timezone
import importlib
import inspect
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from psycopg.errors import InsufficientPrivilege
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import Engine, URL, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core import migrations as migration_registry
from app.core.database import Base
from app.models.entities import User
from app.models.human_review_access import UserB2BCapability
from app.models.human_review_audit import AuditEvent
from app.models.human_review_core import ReviewDecision, ReviewItem
from app.models.human_review_enums import (
    B2BCapability,
    ReviewCaseStatus,
    ScientificStatus,
)
from app.models.human_review_projection import (
    CanonicalIdentity,
    FieldOverride,
    PersonAlias,
)
from app.schemas.human_review_operations import (
    FunctionalReversalCommandV1,
    HumanIdentityProjectionV1,
    OptimisticLockError,
)
from tests.support.postgres import (
    remove_0022_domain_artifacts,
    require_b2b1_test_database_url,
)


B2B_TABLE_NAMES = {
    "user_b2b_capabilities",
    "review_items",
    "review_decisions",
    "canonical_identities",
    "person_aliases",
    "field_overrides",
    "audit_events",
}


BACKEND_ROOT = Path(__file__).resolve().parents[1]
STATE_MODULE = "app.services.human_review_state"
PROJECTION_MODULE = "app.services.human_review_projection"
STABLE_TARGET_A = f"b2b:v1:person_identity:{'a' * 64}"
STABLE_TARGET_B = f"b2b:v1:person_identity:{'b' * 64}"
APPROVED_TRANSITIONS = {
    (ReviewCaseStatus.PENDING, ReviewCaseStatus.IN_REVIEW),
    (ReviewCaseStatus.PENDING, ReviewCaseStatus.RESOLVED),
    (ReviewCaseStatus.PENDING, ReviewCaseStatus.SUPERSEDED),
    (ReviewCaseStatus.IN_REVIEW, ReviewCaseStatus.AWAITING_GESTOR_APPROVAL),
    (ReviewCaseStatus.IN_REVIEW, ReviewCaseStatus.RESOLVED),
    (ReviewCaseStatus.IN_REVIEW, ReviewCaseStatus.CONFLICTED),
    (ReviewCaseStatus.AWAITING_GESTOR_APPROVAL, ReviewCaseStatus.IN_REVIEW),
    (ReviewCaseStatus.AWAITING_GESTOR_APPROVAL, ReviewCaseStatus.RESOLVED),
    (ReviewCaseStatus.RESOLVED, ReviewCaseStatus.REOPENED),
    (ReviewCaseStatus.RESOLVED, ReviewCaseStatus.CONFLICTED),
    (ReviewCaseStatus.RESOLVED, ReviewCaseStatus.SUPERSEDED),
    (ReviewCaseStatus.REOPENED, ReviewCaseStatus.IN_REVIEW),
    (ReviewCaseStatus.REOPENED, ReviewCaseStatus.RESOLVED),
    (ReviewCaseStatus.CONFLICTED, ReviewCaseStatus.IN_REVIEW),
    (ReviewCaseStatus.CONFLICTED, ReviewCaseStatus.RESOLVED),
}


def _api(testcase: unittest.TestCase):
    try:
        state = importlib.import_module(STATE_MODULE)
        projection = importlib.import_module(PROJECTION_MODULE)
        for name in ("assert_case_transition", "assert_expected_version"):
            getattr(state, name)
        for name in (
            "load_identity_projection",
            "set_current_decision",
            "append_functional_reversal",
        ):
            getattr(projection, name)
        return state, projection
    except (ModuleNotFoundError, AttributeError) as exc:
        testcase.fail(f"Task 11 projection services are absent or incomplete: {exc}")
        raise AssertionError("unreachable")


def _identity(key: str, name: str, alias: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "canonical_identity_key": key,
        "canonical_name": name,
        "identity_type": "internal_person",
        "aliases": [{
            "schema_version": 1,
            "alias_original": alias,
            "alias_normalized": alias.casefold(),
        }],
    }


def _override(stable_target_key: str, value: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "field_path": "canonical_name",
        "projected_value": {
            "kind": "string",
            "string_value": value,
            "integer_value": None,
            "decimal_value": None,
            "boolean_value": None,
        },
        "scope": "record",
        "stable_target_key": stable_target_key,
        "target_table": "person_roles",
        "target_pk": 41,
        "document_key": None,
        "period_id": None,
        "relationship_key": None,
        "locked": True,
    }


def _snapshot(
    *,
    case_status: str,
    scientific_status: str,
    current_decision_id: UUID | None,
    identity: dict[str, object] | None,
    overrides: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "case_status": case_status,
        "scientific_status": scientific_status,
        "current_decision_id": (
            str(current_decision_id) if current_decision_id is not None else None
        ),
        "identity": identity,
        "overrides": overrides or [],
    }


def _identity_payload(
    *,
    before: dict[str, object] | None,
    after: dict[str, object] | None,
    key: str,
    name: str,
) -> dict[str, object]:
    return {
        "kind": "identity",
        "schema_version": 1,
        "canonical_identity_key": key,
        "canonical_name": name,
        "identity_type": "internal_person",
        "alias_original": name,
        "alias_normalized": name.casefold(),
        "projection_before": before,
        "projection_after": after,
    }


class HumanReviewStateContractTests(unittest.TestCase):
    def test_exact_public_signatures_exist(self) -> None:
        state, projection = _api(self)
        expected = {
            state.assert_case_transition: ("current", "target"),
            state.assert_expected_version: ("actual", "expected"),
            projection.load_identity_projection: ("db", "stable_target_key"),
            projection.set_current_decision: (
                "db",
                "review_item_id",
                "decision_id",
                "expected_version",
                "case_status",
                "scientific_status",
            ),
            projection.append_functional_reversal: ("db", "command"),
        }
        for callable_, parameters in expected.items():
            with self.subTest(callable=callable_.__name__):
                self.assertEqual(
                    tuple(inspect.signature(callable_).parameters),
                    parameters,
                )

    def test_transition_graph_is_exact_and_same_state_is_never_allowed(self) -> None:
        state, _projection = _api(self)
        for current in ReviewCaseStatus:
            for target in ReviewCaseStatus:
                with self.subTest(current=current, target=target):
                    if (current, target) in APPROVED_TRANSITIONS:
                        self.assertIsNone(state.assert_case_transition(current, target))
                    else:
                        with self.assertRaises(ValueError):
                            state.assert_case_transition(current, target)

    def test_expected_version_is_strict_and_raises_domain_conflict(self) -> None:
        state, _projection = _api(self)
        self.assertIsNone(state.assert_expected_version(7, 7))
        with self.assertRaises(OptimisticLockError):
            state.assert_expected_version(8, 7)
        for actual, expected in ((True, 1), (1, True), (0, 0), (-1, -1)):
            with self.subTest(actual=actual, expected=expected), self.assertRaises(
                (TypeError, ValueError)
            ):
                state.assert_expected_version(actual, expected)

    def test_projection_service_contains_no_commit_or_row_locking_clause(self) -> None:
        _state, projection = _api(self)
        source = inspect.getsource(projection)
        lowered = source.lower()
        self.assertNotIn(".commit(", lowered)
        self.assertIsNone(re.search(r"for\s+(no\s+key\s+)?update", lowered))
        self.assertIsNone(re.search(r"for\s+(key\s+)?share", lowered))
        self.assertNotIn("_load_head", source)
        self.assertNotIn("_head_statement", source)
        self.assertNotIn("select(AuditEvent", source)
        self.assertEqual(source.count("append_audit_event_at_current_head("), 1)


class HumanReviewProjectionPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_url = require_b2b1_test_database_url()
        suffix = uuid4().hex[:12]
        cls.owner_role = f"b2b1_owner_{suffix}"
        cls.app_role = f"b2b1_app_{suffix}"
        cls.schema_name = f"b2b1_proj_{suffix}"
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
                cls._role_url(cls.owner_role, cls.owner_password, "task11_owner"),
                pool_pre_ping=True,
            )
            cls.app_engine = create_engine(
                cls._role_url(cls.app_role, cls.app_password, "task11_app"),
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
            with cls.owner_engine.begin() as connection:
                connection.execute(text(
                    f'GRANT SELECT ON TABLE users TO "{cls.app_role}"'
                ))
        except Exception:
            cls._cleanup()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        cls._cleanup()

    def setUp(self) -> None:
        with self.owner_engine.begin() as connection:
            for table_name, trigger_name in (
                ("audit_events", "trg_audit_events_append_only"),
                ("review_decisions", "trg_review_decisions_append_only"),
            ):
                connection.execute(text(
                    f"ALTER TABLE {table_name} DISABLE TRIGGER {trigger_name}"
                ))
            try:
                connection.execute(text(
                    "TRUNCATE TABLE audit_events, field_overrides, person_aliases, "
                    "canonical_identities, review_decisions, review_items, "
                    "user_b2b_capabilities, users CASCADE"
                ))
            finally:
                for table_name, trigger_name in (
                    ("audit_events", "trg_audit_events_append_only"),
                    ("review_decisions", "trg_review_decisions_append_only"),
                ):
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
                "options": (
                    f"-csearch_path={cls.schema_name} -csynchronous_commit=off"
                ),
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
        with cls.owner_engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO faculties (id, name) VALUES (1, 'Projection Faculty')"
            ))
            connection.execute(text(
                "INSERT INTO careers (id, faculty_id, name, code) "
                "VALUES (1, 1, 'Projection Career', 'PROJ')"
            ))
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

    def _insert_user_and_capability(
        self,
        *,
        capability: str | None = "RESEARCH_MANAGER",
        role: str = "FACULTY_ADMIN",
        active: bool = True,
        scoped: bool = True,
    ) -> int:
        user_id = int(uuid4().int % 1_000_000_000) + 1
        with self.owner_engine.begin() as connection:
            connection.execute(text(
                """
                INSERT INTO users (
                    id, email, full_name, hashed_password, role, career_id, faculty_id, is_active
                ) VALUES (
                    :id, :email, :name, :password, :role, :career_id, :faculty_id, :active
                )
                """
            ), {
                "id": user_id,
                "email": f"task11-{uuid4().hex}@invalid.example",
                "name": f"Task 11 {uuid4().hex}",
                "password": f"not-a-secret-{uuid4().hex}",
                "role": role,
                "career_id": 1 if scoped and role == "CAREER_MANAGER" else None,
                "faculty_id": 1 if scoped and role == "FACULTY_ADMIN" else None,
                "active": active,
            })
            if capability is not None:
                if role == "CAREER_MANAGER":
                    connection.execute(text(
                        "ALTER TABLE user_b2b_capabilities DISABLE TRIGGER "
                        "trg_user_b2b_capabilities_reject_career"
                    ))
                try:
                    connection.execute(text(
                        """
                        INSERT INTO user_b2b_capabilities (
                            id, user_id, capability, is_active, approval_reference,
                            approved_input_sha256, assigned_by_identifier
                        ) VALUES (
                            :id, :user_id, :capability, TRUE, :reference,
                            :digest, :identifier
                        )
                        """
                    ), {
                        "id": uuid4(),
                        "user_id": user_id,
                        "capability": capability,
                        "reference": f"fixture-{uuid4().hex}",
                        "digest": "a" * 64,
                        "identifier": f"task11:{uuid4().hex}",
                    })
                finally:
                    if role == "CAREER_MANAGER":
                        connection.execute(text(
                            "ALTER TABLE user_b2b_capabilities ENABLE TRIGGER "
                            "trg_user_b2b_capabilities_reject_career"
                        ))
        return user_id

    def _new_item(self, stable_target_key: str, *, version: int = 1) -> ReviewItem:
        return ReviewItem(
            id=uuid4(),
            case_type="person_identity",
            stable_target_key=stable_target_key,
            target_table="person_roles",
            target_pk=41,
            scope_faculty_id=1,
            scope_career_id=1,
            document_key=f"document:{uuid4().hex}.pdf",
            source_revision="revision-1",
            source_page=1,
            source_section="faculty",
            row_or_block_id=f"row-{uuid4().hex}",
            field_path="canonical_name",
            raw_value_sha256="c" * 64,
            period_id=2026,
            relationship_key=None,
            case_status="pending",
            scientific_status="pending",
            version=version,
        )

    def _new_decision(
        self,
        item: ReviewItem,
        *,
        sequence: int,
        payload: dict[str, object],
        lifecycle: str = "approved",
        locked: bool = True,
        decision_type: str = "corrected",
        actor_user_id: int | None = None,
    ) -> ReviewDecision:
        return ReviewDecision(
            id=uuid4(),
            review_item_id=item.id,
            sequence=sequence,
            decision_type=decision_type,
            decision_lifecycle=lifecycle,
            scope="record",
            payload_schema="review.decision.v1",
            payload_version=1,
            payload=payload,
            reason=f"fixture-{uuid4().hex}",
            actor_type="human" if actor_user_id is not None else "legacy",
            actor_user_id=actor_user_id,
            actor_identifier=(
                f"user:{actor_user_id}"
                if actor_user_id is not None
                else f"legacy:{uuid4().hex}"
            ),
            actor_capability=(
                B2BCapability.RESEARCH_MANAGER.value
                if actor_user_id is not None
                else None
            ),
            expected_case_version=max(item.version, 1),
            locks_projection=locked,
        )

    def _seed_reversible_case(
        self,
        stable_target_key: str = STABLE_TARGET_A,
    ) -> tuple[UUID, UUID, UUID]:
        with Session(self.app_engine, expire_on_commit=False) as db, db.begin():
            fixture_suffix = "" if stable_target_key == STABLE_TARGET_A else ":b"
            old_key = f"identity:old{fixture_suffix}"
            current_key = f"identity:current{fixture_suffix}"
            old_name = "Álvaro Ñúñez" if not fixture_suffix else "Old Name B"
            current_name = "Current Name" if not fixture_suffix else "Current Name B"
            item = self._new_item(stable_target_key, version=3)
            item.case_status = "resolved"
            item.scientific_status = "validated"
            db.add(item)
            db.flush()

            old_identity = _identity(old_key, old_name, old_name)
            current_identity = _identity(current_key, current_name, current_name)
            first_id = uuid4()
            first = self._new_decision(
                item,
                sequence=1,
                payload=_identity_payload(
                    before=_snapshot(
                        case_status="reopened",
                        scientific_status="pending",
                        current_decision_id=None,
                        identity=None,
                    ),
                    after=_snapshot(
                        case_status="conflicted",
                        scientific_status="pending",
                        current_decision_id=first_id,
                        identity=old_identity,
                        overrides=[_override(stable_target_key, old_name)],
                    ),
                    key=old_key,
                    name=old_name,
                ),
            )
            first.id = first_id
            db.add(first)
            db.flush()

            second_id = uuid4()
            second = self._new_decision(
                item,
                sequence=2,
                payload=_identity_payload(
                    before=_snapshot(
                        case_status="conflicted",
                        scientific_status="pending",
                        current_decision_id=first.id,
                        identity=old_identity,
                        overrides=[_override(stable_target_key, old_name)],
                    ),
                    after=_snapshot(
                        case_status="resolved",
                        scientific_status="validated",
                        current_decision_id=second_id,
                        identity=current_identity,
                        overrides=[_override(stable_target_key, current_name)],
                    ),
                    key=current_key,
                    name=current_name,
                ),
            )
            second.id = second_id
            second.previous_decision_id = first.id
            db.add(second)
            db.flush()

            old_identity_row = CanonicalIdentity(
                id=uuid4(),
                canonical_identity_key=old_key,
                identity_type="internal_person",
                display_name=old_name,
                status="superseded",
                origin="human",
                created_by_decision_id=first.id,
                version=2,
            )
            current_identity_row = CanonicalIdentity(
                id=uuid4(),
                canonical_identity_key=current_key,
                identity_type="internal_person",
                display_name=current_name,
                status="active",
                origin="human",
                created_by_decision_id=second.id,
            )
            db.add_all((old_identity_row, current_identity_row))
            db.flush()
            old_alias = PersonAlias(
                id=uuid4(),
                alias_original=old_name,
                alias_normalized=old_name.casefold(),
                alias_class="person_name",
                canonical_identity_id=old_identity_row.id,
                decision_id=first.id,
                scope="global_identity",
                status="superseded",
                version=2,
            )
            current_alias = PersonAlias(
                id=uuid4(),
                alias_original=current_name,
                alias_normalized=current_name.casefold(),
                alias_class="person_name",
                canonical_identity_id=current_identity_row.id,
                decision_id=second.id,
                scope="global_identity",
                status="active",
            )
            current_override = FieldOverride(
                id=uuid4(),
                review_item_id=item.id,
                decision_id=second.id,
                stable_target_key=stable_target_key,
                target_table="person_roles",
                target_pk=41,
                field_path="canonical_name",
                value_schema="override.scalar.v1",
                value_version=1,
                projected_value={
                    "kind": "string",
                    "string_value": current_name,
                    "integer_value": None,
                    "decimal_value": None,
                    "boolean_value": None,
                },
                scope="record",
                locked=True,
                is_active=True,
            )
            db.add_all((old_alias, current_alias, current_override))
            item.current_decision_id = second.id
            db.flush()
            return item.id, first.id, second.id

    def _command(
        self,
        item_id: UUID,
        target_decision_id: UUID,
        actor_user_id: int,
        *,
        expected_version: int = 3,
    ) -> FunctionalReversalCommandV1:
        return FunctionalReversalCommandV1(
            review_item_id=item_id,
            decision_id_to_revert=target_decision_id,
            actor_user_id=actor_user_id,
            expected_case_version=expected_version,
            reason="Restore the approved previous projection",
            correlation_id=uuid4(),
            request_id=uuid4(),
        )

    def test_load_identity_projection_reads_only_locked_current_payload_snapshot(self) -> None:
        _state, projection = _api(self)
        item_id, _first_id, current_id = self._seed_reversible_case()
        with Session(self.app_engine) as db:
            actual = projection.load_identity_projection(db, STABLE_TARGET_A)
            self.assertEqual(
                actual,
                HumanIdentityProjectionV1(
                    stable_target_key=STABLE_TARGET_A,
                    canonical_identity_key="identity:current",
                    canonical_name="Current Name",
                    decision_id=current_id,
                    locked=True,
                    aliases=("Current Name",),
                ),
            )
            self.assertEqual(db.get(ReviewItem, item_id).current_decision_id, current_id)

        with self.owner_engine.begin() as connection:
            connection.execute(text(
                "UPDATE field_overrides SET projected_value = CAST(:payload AS JSONB)"
            ), {"payload": json.dumps({
                "kind": "string",
                "string_value": "row trap",
                "integer_value": None,
                "decimal_value": None,
                "boolean_value": None,
            })})
        with Session(self.app_engine) as db:
            self.assertEqual(
                projection.load_identity_projection(db, STABLE_TARGET_A).canonical_name,
                "Current Name",
            )

    def test_load_identity_projection_returns_none_for_absent_ambiguous_or_ineligible_head(self) -> None:
        _state, projection = _api(self)
        with Session(self.app_engine) as db:
            self.assertIsNone(projection.load_identity_projection(db, STABLE_TARGET_A))

        self._seed_reversible_case()
        with Session(self.owner_engine) as db, db.begin():
            item = db.scalar(select(ReviewItem).where(
                ReviewItem.stable_target_key == STABLE_TARGET_A
            ))
            proposed = self._new_decision(
                item,
                sequence=3,
                payload=_identity_payload(
                    before=None,
                    after=None,
                    key="identity:proposed",
                    name="Proposed",
                ),
                lifecycle="proposed",
                locked=False,
            )
            db.add(proposed)
            db.flush()
            item.current_decision_id = proposed.id
        with Session(self.app_engine) as db:
            self.assertIsNone(projection.load_identity_projection(db, STABLE_TARGET_A))

    def test_set_current_decision_is_atomic_cas_flushes_without_commit_and_rolls_back(self) -> None:
        _state, projection = _api(self)
        item_id, _first_id, current_id = self._seed_reversible_case()
        with Session(self.app_engine, expire_on_commit=False) as db:
            item = db.get(ReviewItem, item_id)
            item.case_status = "conflicted"
            item.scientific_status = "pending"
            item.version = 4
            db.commit()

        with Session(self.app_engine, expire_on_commit=False) as db:
            updated = projection.set_current_decision(
                db,
                item_id,
                current_id,
                4,
                ReviewCaseStatus.RESOLVED,
                ScientificStatus.VALIDATED,
            )
            self.assertEqual(updated.version, 5)
            self.assertEqual(updated.current_decision_id, current_id)
            with Session(self.owner_engine) as observer:
                self.assertEqual(observer.get(ReviewItem, item_id).version, 4)
            db.rollback()
        with Session(self.owner_engine) as observer:
            self.assertEqual(observer.get(ReviewItem, item_id).version, 4)

    def test_set_current_decision_rejects_stale_cross_case_unlocked_and_snapshot_mismatch(self) -> None:
        _state, projection = _api(self)
        item_id, _first_id, current_id = self._seed_reversible_case()
        with Session(self.app_engine) as db:
            with self.assertRaises(OptimisticLockError):
                projection.set_current_decision(
                    db,
                    item_id,
                    current_id,
                    2,
                    ReviewCaseStatus.CONFLICTED,
                    ScientificStatus.PENDING,
                )
            db.rollback()
            with self.assertRaises(ValueError):
                projection.set_current_decision(
                    db,
                    item_id,
                    current_id,
                    3,
                    ReviewCaseStatus.REOPENED,
                    ScientificStatus.PENDING,
                )
            db.rollback()

        other_item_id, _other_first, other_current = self._seed_reversible_case(
            STABLE_TARGET_B
        )
        self.assertNotEqual(item_id, other_item_id)
        with Session(self.app_engine) as db:
            with self.assertRaises(ValueError):
                projection.set_current_decision(
                    db,
                    item_id,
                    other_current,
                    3,
                    ReviewCaseStatus.CONFLICTED,
                    ScientificStatus.PENDING,
                )

    def test_set_current_decision_rejects_missing_case_decision_lifecycle_and_lock(self) -> None:
        _state, projection = _api(self)
        item_id, _first_id, current_id = self._seed_reversible_case()
        with Session(self.app_engine) as db:
            with self.assertRaisesRegex(ValueError, "review item"):
                projection.set_current_decision(
                    db,
                    uuid4(),
                    current_id,
                    3,
                    ReviewCaseStatus.CONFLICTED,
                    ScientificStatus.PENDING,
                )
            db.rollback()
            with self.assertRaisesRegex(ValueError, "decision"):
                projection.set_current_decision(
                    db,
                    item_id,
                    uuid4(),
                    3,
                    ReviewCaseStatus.CONFLICTED,
                    ScientificStatus.PENDING,
                )
            db.rollback()

        with Session(self.owner_engine) as db, db.begin():
            item = db.get(ReviewItem, item_id)
            current = db.get(ReviewDecision, current_id)
            proposed = self._new_decision(
                item,
                sequence=3,
                payload=copy.deepcopy(current.payload),
                lifecycle="proposed",
                locked=True,
            )
            db.add(proposed)
            db.flush()
            proposed_id = proposed.id
        with Session(self.app_engine) as db:
            with self.assertRaisesRegex(ValueError, "approved"):
                projection.set_current_decision(
                    db,
                    item_id,
                    proposed_id,
                    3,
                    ReviewCaseStatus.CONFLICTED,
                    ScientificStatus.PENDING,
                )
            db.rollback()

        with Session(self.app_engine) as db:
            item = db.get(ReviewItem, item_id)
            current = db.get(ReviewDecision, current_id)
            unlocked = self._new_decision(
                item,
                sequence=4,
                payload=copy.deepcopy(current.payload),
                lifecycle="approved",
                locked=False,
            )
            db.add(unlocked)
            with self.assertRaisesRegex(ValueError, "locked"):
                projection.set_current_decision(
                    db,
                    item_id,
                    unlocked.id,
                    3,
                    ReviewCaseStatus.CONFLICTED,
                    ScientificStatus.PENDING,
                )
            db.rollback()

    def test_set_current_decision_concurrent_cas_has_exactly_one_winner(self) -> None:
        _state, projection = _api(self)
        item_id, _first_id, current_id = self._seed_reversible_case()
        with Session(self.owner_engine) as db, db.begin():
            item = db.get(ReviewItem, item_id)
            current = db.get(ReviewDecision, current_id)
            target_ids: list[UUID] = []
            for sequence in (3, 4):
                decision_id = uuid4()
                payload = copy.deepcopy(current.payload)
                payload["projection_before"] = copy.deepcopy(
                    current.payload["projection_after"]
                )
                payload["projection_after"]["case_status"] = "conflicted"
                payload["projection_after"]["scientific_status"] = "pending"
                payload["projection_after"]["current_decision_id"] = str(decision_id)
                decision = self._new_decision(
                    item,
                    sequence=sequence,
                    payload=payload,
                )
                decision.id = decision_id
                decision.previous_decision_id = current_id
                db.add(decision)
                target_ids.append(decision_id)

        def attempt(decision_id: UUID) -> tuple[str, UUID]:
            with Session(self.app_engine) as db:
                try:
                    projection.set_current_decision(
                        db,
                        item_id,
                        decision_id,
                        3,
                        ReviewCaseStatus.CONFLICTED,
                        ScientificStatus.PENDING,
                    )
                    db.commit()
                    return "committed", decision_id
                except OptimisticLockError:
                    db.rollback()
                    return "optimistic", decision_id

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(attempt, target_ids))
        self.assertCountEqual(
            tuple(result for result, _decision_id in results),
            ("committed", "optimistic"),
        )
        committed_id = next(
            decision_id
            for result, decision_id in results
            if result == "committed"
        )
        with Session(self.owner_engine) as db:
            item = db.get(ReviewItem, item_id)
            self.assertEqual(item.version, 4)
            self.assertEqual(item.current_decision_id, committed_id)

    def test_reversal_appends_decision_materializes_snapshot_and_writes_one_audit_event(self) -> None:
        _state, projection = _api(self)
        actor_id = self._insert_user_and_capability()
        item_id, first_id, target_id = self._seed_reversible_case()
        with Session(self.owner_engine) as before_db:
            target_payload_before = json.dumps(
                before_db.get(ReviewDecision, target_id).payload,
                ensure_ascii=False,
                sort_keys=True,
            )

        with Session(self.app_engine, expire_on_commit=False) as db:
            decision = projection.append_functional_reversal(
                db,
                self._command(item_id, target_id, actor_id),
            )
            new_decision_id = decision.id
            db.commit()

        with Session(self.owner_engine) as db:
            item = db.get(ReviewItem, item_id)
            decision = db.get(ReviewDecision, new_decision_id)
            self.assertEqual(item.current_decision_id, new_decision_id)
            self.assertEqual((item.case_status, item.scientific_status, item.version), (
                "conflicted",
                "pending",
                4,
            ))
            self.assertEqual((decision.sequence, decision.decision_type), (3, "reverted"))
            self.assertEqual(decision.decision_lifecycle, "approved")
            self.assertTrue(decision.locks_projection)
            self.assertEqual(decision.previous_decision_id, target_id)
            self.assertEqual(decision.corrects_decision_id, target_id)
            self.assertEqual(decision.actor_user_id, actor_id)
            self.assertEqual(decision.actor_capability, "RESEARCH_MANAGER")
            self.assertEqual(decision.payload["decision_id_to_revert"], str(target_id))
            self.assertEqual(decision.payload["restore_decision_id"], str(first_id))
            self.assertEqual(
                decision.payload["projection_before"]["current_decision_id"],
                str(target_id),
            )
            self.assertEqual(
                decision.payload["projection_after"]["current_decision_id"],
                str(first_id),
            )
            self.assertNotEqual(
                decision.payload["projection_after"]["current_decision_id"],
                str(new_decision_id),
            )
            active_override = db.scalar(select(FieldOverride).where(
                FieldOverride.review_item_id == item_id,
                FieldOverride.is_active.is_(True),
            ))
            self.assertEqual(active_override.decision_id, new_decision_id)
            self.assertEqual(active_override.projected_value["string_value"], "Álvaro Ñúñez")
            previous_override = db.scalar(select(FieldOverride).where(
                FieldOverride.review_item_id == item_id,
                FieldOverride.decision_id == target_id,
            ))
            self.assertFalse(previous_override.is_active)
            self.assertEqual(previous_override.superseded_by_id, active_override.id)
            restored_identity = db.scalar(select(CanonicalIdentity).where(
                CanonicalIdentity.canonical_identity_key == "identity:old"
            ))
            current_identity = db.scalar(select(CanonicalIdentity).where(
                CanonicalIdentity.canonical_identity_key == "identity:current"
            ))
            self.assertEqual(restored_identity.status, "active")
            self.assertEqual(current_identity.status, "superseded")
            self.assertEqual(current_identity.superseded_by_id, restored_identity.id)
            alias = db.scalar(select(PersonAlias).where(
                PersonAlias.status == "active"
            ))
            self.assertEqual((alias.alias_original, alias.decision_id), (
                "Álvaro Ñúñez",
                new_decision_id,
            ))
            event = db.scalar(select(AuditEvent))
            self.assertEqual(event.event_type, "functional_reversion")
            self.assertEqual(event.review_item_id, item_id)
            self.assertEqual(event.actor_user_id, actor_id)
            self.assertEqual(event.payload["new_decision_id"], str(new_decision_id))
            self.assertEqual(event.payload["reverted_decision_id"], str(target_id))
            self.assertEqual(event.payload["restored_decision_id"], str(first_id))
            self.assertEqual(
                json.dumps(
                    db.get(ReviewDecision, target_id).payload,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                target_payload_before,
            )

    def test_reversal_participates_in_caller_transaction_and_rollback_is_total(self) -> None:
        _state, projection = _api(self)
        actor_id = self._insert_user_and_capability()
        item_id, _first_id, target_id = self._seed_reversible_case()
        with Session(self.app_engine, expire_on_commit=False) as db:
            projection.append_functional_reversal(
                db,
                self._command(item_id, target_id, actor_id),
            )
            self.assertEqual(db.scalar(select(func.count(ReviewDecision.id))), 3)
            with Session(self.owner_engine) as observer:
                self.assertEqual(observer.scalar(select(func.count(ReviewDecision.id))), 2)
                self.assertEqual(observer.scalar(select(func.count(AuditEvent.id))), 0)
            db.rollback()
        with Session(self.owner_engine) as observer:
            item = observer.get(ReviewItem, item_id)
            self.assertEqual((item.current_decision_id, item.version), (target_id, 3))
            self.assertEqual(observer.scalar(select(func.count(ReviewDecision.id))), 2)
            self.assertEqual(observer.scalar(select(func.count(AuditEvent.id))), 0)
            self.assertEqual(observer.scalar(select(func.count(FieldOverride.id))), 1)

    def test_audit_failure_rolls_back_every_projection_write(self) -> None:
        _state, projection = _api(self)
        actor_id = self._insert_user_and_capability()
        item_id, _first_id, target_id = self._seed_reversible_case()
        with Session(self.app_engine) as db, patch.object(
            projection,
            "append_audit_event_at_current_head",
            side_effect=RuntimeError("simulated Task 9 append failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "Task 9"):
                projection.append_functional_reversal(
                    db,
                    self._command(item_id, target_id, actor_id),
                )
        with Session(self.owner_engine) as observer:
            item = observer.get(ReviewItem, item_id)
            self.assertEqual((item.current_decision_id, item.version), (target_id, 3))
            self.assertEqual(observer.scalar(select(func.count(ReviewDecision.id))), 2)
            self.assertEqual(observer.scalar(select(func.count(AuditEvent.id))), 0)
            self.assertEqual(observer.scalar(select(func.count(FieldOverride.id))), 1)

    def test_reversal_of_reversal_chains_decision_and_audit_history(self) -> None:
        _state, projection = _api(self)
        actor_id = self._insert_user_and_capability()
        item_id, _first_id, target_id = self._seed_reversible_case()
        with Session(self.app_engine, expire_on_commit=False) as db:
            first_reversal = projection.append_functional_reversal(
                db,
                self._command(item_id, target_id, actor_id),
            )
            first_reversal_id = first_reversal.id
            db.commit()
        audit_service = importlib.import_module("app.services.human_review_audit")
        with Session(self.app_engine, expire_on_commit=False) as db, patch.object(
            projection,
            "append_audit_event_at_current_head",
            wraps=audit_service.append_audit_event_at_current_head,
        ) as append_at_head:
            second_reversal = projection.append_functional_reversal(
                db,
                self._command(
                    item_id,
                    first_reversal_id,
                    actor_id,
                    expected_version=4,
                ),
            )
            self.assertEqual(append_at_head.call_count, 1)
            audit_command = append_at_head.call_args.args[1]
            self.assertIsNone(audit_command.previous_event_id)
            second_reversal_id = second_reversal.id
            db.commit()

        with Session(self.owner_engine) as db:
            item = db.get(ReviewItem, item_id)
            second_reversal = db.get(ReviewDecision, second_reversal_id)
            self.assertEqual((item.case_status, item.scientific_status, item.version), (
                "resolved",
                "validated",
                5,
            ))
            self.assertEqual(item.current_decision_id, second_reversal_id)
            self.assertEqual((second_reversal.sequence, second_reversal.previous_decision_id), (
                4,
                first_reversal_id,
            ))
            self.assertEqual(second_reversal.corrects_decision_id, first_reversal_id)
            self.assertEqual(second_reversal.payload["restore_decision_id"], str(target_id))
            events = tuple(db.scalars(select(AuditEvent).order_by(
                AuditEvent.occurred_at,
                AuditEvent.id,
            )))
            self.assertEqual(len(events), 2)
            self.assertIsNone(events[0].previous_event_id)
            self.assertEqual(events[1].previous_event_id, events[0].id)
            self.assertEqual(events[1].previous_event_hash, events[0].event_hash)

    def test_unscoped_inactive_or_missing_actors_cannot_revert_and_leave_no_rows(self) -> None:
        _state, projection = _api(self)
        actors = (
            self._insert_user_and_capability(capability="SYSTEM_ADMIN", scoped=False),
            self._insert_user_and_capability(capability=None, scoped=False),
            self._insert_user_and_capability(active=False),
            self._insert_user_and_capability(role="CAREER_MANAGER", scoped=False),
            2_000_000_001,
        )
        item_id, _first_id, target_id = self._seed_reversible_case()
        for actor_id in actors:
            with self.subTest(actor_id=actor_id), Session(self.app_engine) as db:
                with self.assertRaises(PermissionError):
                    projection.append_functional_reversal(
                        db,
                        self._command(item_id, target_id, actor_id),
                    )
                db.rollback()
        with Session(self.owner_engine) as db:
            self.assertEqual(db.scalar(select(func.count(ReviewDecision.id))), 2)
            self.assertEqual(db.scalar(select(func.count(AuditEvent.id))), 0)
            self.assertEqual(db.get(ReviewItem, item_id).version, 3)

    def test_reversal_rejects_stale_cross_case_incomplete_and_restore_mismatch(self) -> None:
        _state, projection = _api(self)
        actor_id = self._insert_user_and_capability()
        item_id, first_id, target_id = self._seed_reversible_case()
        other_item_id, _other_first, other_target = self._seed_reversible_case(
            STABLE_TARGET_B
        )
        attempts = (
            (self._command(item_id, target_id, actor_id, expected_version=2), OptimisticLockError),
            (self._command(item_id, other_target, actor_id), ValueError),
        )
        for command, error in attempts:
            with self.subTest(error=error.__name__), Session(self.app_engine) as db:
                with self.assertRaises(error):
                    projection.append_functional_reversal(db, command)
                db.rollback()

        with Session(self.owner_engine) as db, db.begin():
            item = db.get(ReviewItem, item_id)
            current = db.get(ReviewDecision, target_id)
            mismatch_payload = copy.deepcopy(current.payload)
            mismatch_payload["projection_before"]["identity"][
                "canonical_name"
            ] = "Mismatched restore"
            mismatch = self._new_decision(
                item,
                sequence=3,
                payload=mismatch_payload,
            )
            db.add(mismatch)
            db.flush()
            mismatch_id = mismatch.id
            incomplete = self._new_decision(
                item,
                sequence=4,
                payload=_identity_payload(
                    before=None,
                    after=None,
                    key="identity:incomplete",
                    name="Incomplete",
                ),
            )
            db.add(incomplete)
            db.flush()
            incomplete_id = incomplete.id
            proposed = self._new_decision(
                item,
                sequence=5,
                payload=copy.deepcopy(current.payload),
                lifecycle="proposed",
                locked=False,
            )
            db.add(proposed)
            db.flush()
            proposed_id = proposed.id
        self.assertIsInstance(first_id, UUID)
        for invalid_target in (mismatch_id, incomplete_id, proposed_id):
            with self.subTest(invalid_target=invalid_target), Session(
                self.app_engine
            ) as db:
                with self.assertRaises(ValueError):
                    projection.append_functional_reversal(
                        db,
                        self._command(item_id, invalid_target, actor_id),
                    )
                db.rollback()
        self.assertNotEqual(item_id, other_item_id)
        with Session(self.owner_engine) as db:
            self.assertEqual(db.scalar(select(func.count(AuditEvent.id))), 0)

    def test_same_case_concurrency_has_one_cas_winner_and_no_duplicate_projection(self) -> None:
        _state, projection = _api(self)
        actor_id = self._insert_user_and_capability()
        item_id, _first_id, target_id = self._seed_reversible_case()

        def attempt() -> str:
            with Session(self.app_engine, expire_on_commit=False) as db:
                try:
                    projection.append_functional_reversal(
                        db,
                        self._command(item_id, target_id, actor_id),
                    )
                    db.commit()
                    return "committed"
                except OptimisticLockError:
                    db.rollback()
                    return "optimistic"

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(lambda _index: attempt(), range(2)))
        self.assertCountEqual(results, ("committed", "optimistic"))
        with Session(self.owner_engine) as db:
            self.assertEqual(db.get(ReviewItem, item_id).version, 4)
            self.assertEqual(db.scalar(select(func.count(ReviewDecision.id))), 3)
            self.assertEqual(db.scalar(select(func.count(AuditEvent.id))), 1)
            self.assertEqual(db.scalar(
                select(func.count(FieldOverride.id)).where(
                    FieldOverride.is_active.is_(True)
                )
            ), 1)

    def test_distinct_cases_do_not_share_the_task9_advisory_lock(self) -> None:
        _state, projection = _api(self)
        audit_service = importlib.import_module("app.services.human_review_audit")
        actor_id = self._insert_user_and_capability()
        first_item_id, _first_restore, _first_target = self._seed_reversible_case()
        second_item_id, _second_restore, second_target = self._seed_reversible_case(
            STABLE_TARGET_B
        )
        with Session(self.app_engine) as blocker:
            blocker.execute(text("SELECT pg_advisory_xact_lock(:key)"), {
                "key": audit_service._advisory_lock_key(
                    "review_item",
                    STABLE_TARGET_A,
                )
            })

            def reverse_second() -> UUID:
                with Session(self.app_engine, expire_on_commit=False) as db:
                    decision = projection.append_functional_reversal(
                        db,
                        self._command(second_item_id, second_target, actor_id),
                    )
                    decision_id = decision.id
                    db.commit()
                    return decision_id

            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(reverse_second)
                try:
                    self.assertIsInstance(future.result(timeout=5), UUID)
                except FutureTimeout:
                    self.fail("a distinct review item was blocked by another aggregate lock")
            blocker.rollback()
        self.assertNotEqual(first_item_id, second_item_id)

    def test_reversal_preserves_append_only_sql_guarantees_for_app_role(self) -> None:
        _state, projection = _api(self)
        actor_id = self._insert_user_and_capability()
        item_id, _first_id, target_id = self._seed_reversible_case()
        with Session(self.app_engine, expire_on_commit=False) as db:
            decision = projection.append_functional_reversal(
                db,
                self._command(item_id, target_id, actor_id),
            )
            decision_id = decision.id
            db.commit()

        statements = (
            f"UPDATE review_decisions SET reason = 'forbidden' WHERE id = '{decision_id}'",
            f"DELETE FROM review_decisions WHERE id = '{decision_id}'",
            f"UPDATE audit_events SET actor_identifier = 'forbidden' WHERE review_item_id = '{item_id}'",
            f"DELETE FROM audit_events WHERE review_item_id = '{item_id}'",
            "TRUNCATE TABLE audit_events",
            f"SELECT id FROM audit_events WHERE review_item_id = '{item_id}' FOR UPDATE",
        )
        for statement in statements:
            with self.subTest(statement=statement), self.app_engine.connect() as connection:
                transaction = connection.begin()
                try:
                    with self.assertRaises(DBAPIError) as raised:
                        connection.execute(text(statement))
                    self.assertIsInstance(raised.exception.orig, InsufficientPrivilege)
                finally:
                    transaction.rollback()


if __name__ == "__main__":
    unittest.main()
