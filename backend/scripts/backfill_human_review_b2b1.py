from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.schemas.human_review_operations import (
    B2B1BackfillPlanV1,
    B2B1InvariantSnapshotV1,
    BackfillGateError,
)
from app.services.human_review_backfill import (
    GLOBAL_BACKFILL_LOCK_NAME,
    SOURCE_TABLES,
    apply_backfill_plan,
    backfill_plan_sha256,
    build_backfill_plan,
)
from app.services.human_review_invariants import snapshot_sha256
from scripts.configure_human_review_privileges import (
    B2B_TABLE_PRIVILEGES,
    ROLE_NAME_PATTERN,
)


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_B2B_TABLES = tuple(B2B_TABLE_PRIVILEGES)


class _ApplySQLGuard:
    def __init__(self) -> None:
        self.downgraded = False

    @staticmethod
    def _normalized(statement: str) -> str:
        return " ".join(statement.upper().replace('"', "").split())

    @staticmethod
    def _writes_table(statement: str, table_name: str) -> bool:
        qualified_name = rf"(?:[A-Z_][A-Z0-9_$]*\.)?{re.escape(table_name)}\b"
        return any(
            re.search(pattern, statement) is not None
            for pattern in (
                rf"\bINSERT\s+INTO\s+(?:ONLY\s+)?{qualified_name}",
                rf"\bUPDATE\s+(?:ONLY\s+)?{qualified_name}",
                rf"\bDELETE\s+FROM\s+(?:ONLY\s+)?{qualified_name}",
            )
        )

    def before_cursor_execute(
        self,
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        normalized = self._normalized(statement)
        if not self.downgraded:
            for table_name in _B2B_TABLES:
                if self._writes_table(normalized, table_name.upper()):
                    raise BackfillGateError("owner attempted functional B2B write")
            return

        if re.search(
            r"\b(?:RESET\s+ROLE|SET\s+(?:LOCAL\s+)?ROLE|TRUNCATE|ALTER|DROP|"
            r"GRANT|REVOKE|CREATE)\b",
            normalized,
        ):
            raise BackfillGateError("forbidden SQL appeared after role downgrade")
        for table_name in SOURCE_TABLES:
            if self._writes_table(normalized, table_name.upper()):
                raise BackfillGateError("protected source mutation appeared during apply")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dry-run or apply the B2B.1 human-review foundation backfill."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--output", required=True)
    parser.add_argument("--plan")
    parser.add_argument("--approved-plan-sha256")
    parser.add_argument("--snapshot")
    parser.add_argument("--backup")
    parser.add_argument("--backup-sha256")
    return parser


def _require_sha256(value: str | None, label: str) -> str:
    if value is None or _SHA256_PATTERN.fullmatch(value) is None:
        raise BackfillGateError(f"{label} must be a lowercase SHA-256 hash")
    return value


def _read_required(path_value: str | None, label: str) -> bytes:
    if not path_value:
        raise BackfillGateError(f"{label} path is required")
    path = Path(path_value)
    if not path.is_file():
        raise BackfillGateError(f"{label} file is missing")
    return path.read_bytes()


def _write_model(path_value: str, model) -> None:
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(model.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(path)


def _require_no_hard_blockers(plan: B2B1BackfillPlanV1) -> None:
    if plan.hard_blockers:
        raise BackfillGateError("backfill plan contains hard blockers")


def _plan_summary(plan: B2B1BackfillPlanV1) -> dict[str, int]:
    return {
        "materializable_candidates": len(plan.candidates),
        "stable_targets": plan.union_stable_target_count,
        "deferred_records": len(plan.deferred_records),
        "excluded_records": len(plan.excluded_records),
        "hard_blockers": len(plan.hard_blockers),
    }


def _verify_least_privilege_role(
    connection: Connection,
    role_name: str,
) -> None:
    role = connection.execute(text(
        """
        SELECT rolsuper, rolcreatedb, rolcreaterole
        FROM pg_roles
        WHERE rolname = :role_name
        """
    ), {"role_name": role_name}).one_or_none()
    if role is None or any(role):
        raise BackfillGateError("application role is missing or privileged")
    for table_name in SOURCE_TABLES:
        for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            if connection.execute(text(
                "SELECT has_table_privilege(:role, :table_name, :privilege)"
            ), {
                "role": role_name,
                "table_name": table_name,
                "privilege": privilege,
            }).scalar_one():
                raise BackfillGateError("application role can mutate a protected source")
    for table_name, allowed in B2B_TABLE_PRIVILEGES.items():
        for privilege in (
            "SELECT",
            "INSERT",
            "UPDATE",
            "DELETE",
            "TRUNCATE",
            "REFERENCES",
            "TRIGGER",
        ):
            actual = connection.execute(text(
                "SELECT has_table_privilege(:role, :table_name, :privilege)"
            ), {
                "role": role_name,
                "table_name": table_name,
                "privilege": privilege,
            }).scalar_one()
            if actual != (privilege in allowed):
                raise BackfillGateError("application B2B privilege matrix differs")


def _verify_dry_run_application_role(connection: Connection) -> None:
    current_user = connection.execute(text("SELECT current_user")).scalar_one()
    _verify_least_privilege_role(connection, current_user)


def _verify_owner_and_application_role(
    connection: Connection,
    expected_owner: str,
    application_role: str,
) -> None:
    if not ROLE_NAME_PATTERN.fullmatch(application_role):
        raise BackfillGateError("application role name is invalid")
    current_user = connection.execute(text("SELECT current_user")).scalar_one()
    if current_user != expected_owner:
        raise BackfillGateError("migration connection user differs from its expected owner")
    if current_user == application_role:
        raise BackfillGateError("owner and application roles must be distinct")
    _verify_least_privilege_role(connection, application_role)
    can_set = connection.execute(text(
        "SELECT pg_has_role(current_user, :application_role, 'SET')"
    ), {"application_role": application_role}).scalar_one()
    if not can_set:
        raise BackfillGateError("owner cannot SET LOCAL ROLE to the application role")


def _acquire_and_verify_source_locks(connection: Connection) -> int:
    connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_name))"),
        {"lock_name": GLOBAL_BACKFILL_LOCK_NAME},
    )
    connection.execute(text(
        "LOCK TABLE external_researchers, person_roles, research_entities, "
        "research_projects, scientific_production_authors, scientific_productions "
        "IN SHARE MODE"
    ))
    count = connection.execute(text(
        """
        SELECT COUNT(*)
        FROM pg_locks AS lock_row
        JOIN pg_class AS relation ON relation.oid = lock_row.relation
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE lock_row.pid = pg_backend_pid()
          AND lock_row.locktype = 'relation'
          AND lock_row.mode = 'ShareLock'
          AND lock_row.granted
          AND namespace.nspname = current_schema()
          AND relation.relname = ANY(:source_tables)
        """
    ), {"source_tables": list(SOURCE_TABLES)}).scalar_one()
    if count != len(SOURCE_TABLES):
        raise BackfillGateError("source ShareLock acquisition is incomplete")
    return count


def _verify_downgraded_role(
    connection: Connection,
    application_role: str,
    expected_owner: str,
) -> None:
    current_user, session_user = connection.execute(text(
        "SELECT current_user, session_user"
    )).one()
    if current_user != application_role or session_user != expected_owner:
        raise BackfillGateError("SET LOCAL ROLE verification failed")
    _verify_least_privilege_role(connection, current_user)


def _dry_run(output: str) -> int:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise BackfillGateError("DATABASE_URL is required for dry-run")
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                _verify_dry_run_application_role(connection)
                with Session(bind=connection, autoflush=False) as db:
                    plan = build_backfill_plan(db, datetime.now(timezone.utc))
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
    _write_model(output, plan)
    _require_no_hard_blockers(plan)
    print(json.dumps({
        "dry_run": True,
        **_plan_summary(plan),
    }, sort_keys=True))
    return 0


def _apply(args) -> int:
    migration_database_url = os.environ.get("MIGRATION_DATABASE_URL", "").strip()
    application_role = os.environ.get("B2B1_APPLICATION_DB_ROLE", "").strip()
    if not migration_database_url:
        raise BackfillGateError("MIGRATION_DATABASE_URL is required for apply")
    if not application_role:
        raise BackfillGateError("B2B1_APPLICATION_DB_ROLE is required for apply")

    plan = B2B1BackfillPlanV1.model_validate_json(
        _read_required(args.plan, "plan")
    )
    approved_hash = _require_sha256(
        args.approved_plan_sha256,
        "approved plan hash",
    )
    if backfill_plan_sha256(plan) != approved_hash:
        raise BackfillGateError("approved plan hash does not match the plan file")
    snapshot = B2B1InvariantSnapshotV1.model_validate_json(
        _read_required(args.snapshot, "snapshot")
    )
    if snapshot_sha256(snapshot) != plan.invariant_snapshot_sha256:
        raise BackfillGateError("snapshot hash does not match the approved plan")
    backup_bytes = _read_required(args.backup, "backup")
    backup_hash = _require_sha256(args.backup_sha256, "backup hash")
    if hashlib.sha256(backup_bytes).hexdigest() != backup_hash:
        raise BackfillGateError("backup hash does not match the backup file")
    _require_no_hard_blockers(plan)

    engine = create_engine(migration_database_url, pool_pre_ping=True)
    expected_owner = engine.url.username or ""
    guard = _ApplySQLGuard()
    result = None
    try:
        with engine.connect() as connection:
            event.listen(connection, "before_cursor_execute", guard.before_cursor_execute)
            transaction = connection.begin()
            try:
                _verify_owner_and_application_role(
                    connection,
                    expected_owner,
                    application_role,
                )
                lock_count = _acquire_and_verify_source_locks(connection)
                quoted_role = connection.dialect.identifier_preparer.quote(application_role)
                connection.exec_driver_sql(f"SET LOCAL ROLE {quoted_role}")
                guard.downgraded = True
                _verify_downgraded_role(
                    connection,
                    application_role,
                    expected_owner,
                )
                with Session(bind=connection, expire_on_commit=False) as db:
                    result = apply_backfill_plan(
                        db,
                        plan,
                        approved_hash,
                        plan.invariant_snapshot_sha256,
                    )
                transaction.commit()
            except Exception:
                transaction.rollback()
                raise
            finally:
                event.remove(
                    connection,
                    "before_cursor_execute",
                    guard.before_cursor_execute,
                )
    finally:
        engine.dispose()

    _write_model(args.output, result)
    print(json.dumps({
        "owner_role_verified": True,
        "app_role_verified": True,
        "source_share_locks": lock_count,
        "role_downgrade_verified": True,
        "created_review_items": result.created_review_items,
    }, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.dry_run:
            return _dry_run(args.output)
        required = (
            "plan",
            "approved_plan_sha256",
            "snapshot",
            "backup",
            "backup_sha256",
        )
        missing = [name for name in required if not getattr(args, name)]
        if missing:
            parser.error("--apply requires " + ", ".join(f"--{name.replace('_', '-')}" for name in missing))
        return _apply(args)
    except BackfillGateError as error:
        parser.error(str(error))
    except SQLAlchemyError:
        parser.error("database operation failed during human-review backfill")


if __name__ == "__main__":
    raise SystemExit(main())
