from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from importlib import import_module
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from app.core.migrations import MIGRATIONS, run_migrations
from app.models.human_review_audit import AuditEvent
from app.services.human_review_audit import (
    _row_as_command,
    compute_audit_event_hash,
)
from app.services.human_review_invariants import (
    capture_b2b1_invariants,
    snapshot_sha256,
)

migration_0021 = import_module(
    "app.migrations.versions.20260718_0021_human_review_scientific_decision_audit"
)


VERSION_0020 = "20260713_0020_human_review_audit"
VERSION_0021 = "20260718_0021_human_review_scientific_decision_audit"
HISTORICAL_MARKER = "20260629_0002_add_scientific_production_authors"
EXPECTED_0020_FINGERPRINT = (
    "22d02feda03bc685f739e4d2c4c5d12eaef8ba4e20801e2a31bf981f491fb8de"
)
EXPECTED_COUNTS = {
    "review_items": 79,
    "audit_events": 80,
    "user_b2b_capabilities": 1,
    "review_decisions": 0,
    "field_overrides": 0,
    "canonical_identities": 0,
    "person_aliases": 0,
}
EXPECTED_0020_TYPES = (
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
EXPECTED_0021_TYPES = EXPECTED_0020_TYPES + ("scientific_decision_applied",)


class GateError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GateError(message)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def scalar(connection: Connection, sql: str, **parameters: object) -> Any:
    return connection.execute(text(sql), parameters).scalar_one()


def constraint_state(connection: Connection) -> dict[str, object]:
    rows = tuple(
        connection.execute(
            text(
                """
                SELECT pg_get_constraintdef(constraint_row.oid, true)
                FROM pg_constraint AS constraint_row
                JOIN pg_class AS table_row ON table_row.oid = constraint_row.conrelid
                JOIN pg_namespace AS namespace_row
                  ON namespace_row.oid = table_row.relnamespace
                WHERE namespace_row.nspname = current_schema()
                  AND table_row.relname = 'audit_events'
                  AND constraint_row.conname = 'ck_audit_events_event_type'
                  AND constraint_row.contype = 'c'
                """
            )
        ).scalars()
    )
    literals = tuple(re.findall(r"'([^']*)'", rows[0])) if len(rows) == 1 else ()
    return {
        "multiplicity": len(rows),
        "definition": rows[0] if len(rows) == 1 else None,
        "literals": list(literals),
    }


def audit_state(connection: Connection) -> dict[str, object]:
    session = Session(bind=connection, autoflush=False)
    try:
        events = tuple(session.scalars(select(AuditEvent).order_by(AuditEvent.id)))
        by_id = {event.id: event for event in events}
        invalid_hashes: list[str] = []
        invalid_payloads: list[str] = []
        broken_links: list[str] = []
        for event in events:
            try:
                command = _row_as_command(event)
                previous_hash = event.previous_event_hash.strip() if event.previous_event_hash else None
                expected_hash = compute_audit_event_hash(command, previous_hash)
                if event.event_hash.strip() != expected_hash:
                    invalid_hashes.append(str(event.id))
            except Exception:
                invalid_payloads.append(str(event.id))
            if event.previous_event_id is not None:
                predecessor = by_id.get(event.previous_event_id)
                if (
                    predecessor is None
                    or predecessor.aggregate_type != event.aggregate_type
                    or predecessor.aggregate_key != event.aggregate_key
                    or predecessor.event_hash.strip()
                    != (event.previous_event_hash or "").strip()
                ):
                    broken_links.append(str(event.id))
        return {
            "events": len(events),
            "valid_hashes": len(events) - len(set(invalid_hashes + invalid_payloads)),
            "invalid_content_hashes": invalid_hashes,
            "invalid_payload_envelopes": invalid_payloads,
            "roots": sum(event.previous_event_id is None for event in events),
            "predecessor_links": sum(event.previous_event_id is not None for event in events),
            "broken_predecessor_links": broken_links,
        }
    finally:
        session.close()


def privilege_state(connection: Connection) -> dict[str, object]:
    audit_privileges = {
        privilege.lower(): bool(
            scalar(
                connection,
                "SELECT has_table_privilege('b2b1_app', 'public.audit_events', :privilege)",
                privilege=privilege,
            )
        )
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
    }
    role = connection.execute(
        text(
            """
            SELECT rolsuper, rolinherit, rolcreaterole, rolcreatedb, rolcanlogin,
                   rolreplication, rolbypassrls
            FROM pg_roles WHERE rolname = 'b2b1_app'
            """
        )
    ).mappings().one()
    return {
        "audit_events": audit_privileges,
        "app_role": dict(role),
        "app_owned_objects": int(
            scalar(
                connection,
                """
                SELECT count(*) FROM pg_class AS class_row
                JOIN pg_roles AS role_row ON role_row.oid = class_row.relowner
                WHERE role_row.rolname = 'b2b1_app'
                """,
            )
        ),
        "app_can_create_public": bool(
            scalar(connection, "SELECT has_schema_privilege('b2b1_app', 'public', 'CREATE')")
        ),
        "app_can_set_role_owner": bool(
            scalar(connection, "SELECT pg_has_role('b2b1_app', current_user, 'SET')")
        ),
    }


def ledger_state(connection: Connection) -> dict[str, object]:
    versions = tuple(connection.execute(text("SELECT version FROM schema_migrations")).scalars())
    registry = tuple(version for version, _upgrade in MIGRATIONS)
    counts = Counter(versions)
    present = tuple(version for version in registry if counts[version] == 1)
    unknown = sorted(set(versions) - set(registry) - {HISTORICAL_MARKER})
    duplicate = sorted(version for version, count in counts.items() if count != 1)
    return {
        "effective_head": present[-1] if present else None,
        "registry_revision_count": len(present),
        "registry_revisions_present": list(present),
        "historical_marker": HISTORICAL_MARKER,
        "historical_marker_count": counts[HISTORICAL_MARKER],
        "total_rows": len(versions),
        "migration_0021_count": counts[VERSION_0021],
        "unknown_revisions": unknown,
        "duplicate_revisions": duplicate,
    }


def capture(engine: Engine) -> dict[str, object]:
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            ledger = ledger_state(connection)
            counts = {
                table: int(scalar(connection, f'SELECT count(*) FROM "{table}"'))
                for table in EXPECTED_COUNTS
            }
            capabilities = [
                dict(row)
                for row in connection.execute(
                    text(
                        """
                        SELECT user_id, capability, revoked_at IS NULL AS active
                        FROM user_b2b_capabilities ORDER BY user_id, capability, id
                        """
                    )
                ).mappings()
            ]
            locator = [
                dict(row)
                for row in connection.execute(
                    text(
                        """
                        SELECT stable_target_key, length(row_or_block_id) AS chars,
                               octet_length(row_or_block_id) AS octets,
                               pg_typeof(row_or_block_id)::text AS storage_type
                        FROM review_items
                        WHERE target_table = 'scientific_productions' AND target_pk = 400
                        ORDER BY id
                        """
                    )
                ).mappings()
            ]
            invariant = capture_b2b1_invariants(connection)
            triggers = [
                dict(row)
                for row in connection.execute(
                    text(
                        """
                        SELECT table_row.relname AS table_name, trigger_row.tgname,
                               trigger_row.tgenabled
                        FROM pg_trigger AS trigger_row
                        JOIN pg_class AS table_row ON table_row.oid = trigger_row.tgrelid
                        JOIN pg_namespace AS namespace_row ON namespace_row.oid = table_row.relnamespace
                        WHERE namespace_row.nspname = current_schema()
                          AND trigger_row.tgname IN (
                            'trg_audit_events_append_only', 'trg_review_decisions_append_only'
                          )
                        ORDER BY table_row.relname, trigger_row.tgname
                        """
                    )
                ).mappings()
            ]
            result = {
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "transaction_read_only": bool(scalar(connection, "SHOW transaction_read_only") == "on"),
                "postgresql_version": str(scalar(connection, "SHOW server_version")),
                "database": str(scalar(connection, "SELECT current_database()")),
                "ledger": ledger,
                "counts": counts,
                "capabilities": capabilities,
                "research_manager_user_1": sum(
                    item["user_id"] == 1 and item["capability"] == "RESEARCH_MANAGER" and item["active"]
                    for item in capabilities
                ),
                "system_admin_active": sum(
                    item["capability"] == "SYSTEM_ADMIN" and item["active"] for item in capabilities
                ),
                "locator_400": locator,
                "constraint": constraint_state(connection),
                "audit": audit_state(connection),
                "triggers": triggers,
                "privileges": privilege_state(connection),
                "invariant": invariant.model_dump(mode="json"),
                "invariant_sha256": snapshot_sha256(invariant),
                "eligible_products": invariant.eligible_products,
                "advisory_locks": int(
                    scalar(connection, "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'")
                ),
                "other_client_sessions": int(
                    scalar(
                        connection,
                        """
                        SELECT count(*) FROM pg_stat_activity
                        WHERE datname = current_database() AND pid <> pg_backend_pid()
                          AND backend_type = 'client backend'
                        """,
                    )
                ),
            }
            return result
        finally:
            transaction.rollback()


def validate_phase(state: dict[str, object], phase: str) -> None:
    expected_version = VERSION_0020 if phase == "0020" else VERSION_0021
    expected_registry = 20 if phase == "0020" else 21
    expected_total = 21 if phase == "0020" else 22
    expected_types = EXPECTED_0020_TYPES if phase == "0020" else EXPECTED_0021_TYPES
    ledger = state["ledger"]
    require(isinstance(ledger, dict), "ledger evidence is invalid")
    require(ledger["effective_head"] == expected_version, f"effective head must be {expected_version}")
    require(ledger["registry_revision_count"] == expected_registry, "registry count differs")
    require(ledger["historical_marker_count"] == 1, "historical marker count differs")
    require(ledger["total_rows"] == expected_total, "ledger row count differs")
    require(ledger["migration_0021_count"] == (0 if phase == "0020" else 1), "0021 count differs")
    require(not ledger["unknown_revisions"], "unknown ledger revisions exist")
    require(not ledger["duplicate_revisions"], "duplicate ledger revisions exist")
    require(state["postgresql_version"] == "16.13", "PostgreSQL version differs")
    require(state["database"] == "science_faculty", "database target differs")
    require(state["transaction_read_only"] is True, "capture was not read-only")
    require(state["counts"] == EXPECTED_COUNTS, "B2B counts differ")
    require(state["research_manager_user_1"] == 1, "user 1 RESEARCH_MANAGER differs")
    require(state["system_admin_active"] == 0, "SYSTEM_ADMIN must be NONE")
    require(state["eligible_products"] == 2, "eligible_products KPI differs")
    locator = state["locator_400"]
    require(isinstance(locator, list) and len(locator) == 1, "locator 400 multiplicity differs")
    require(locator[0]["chars"] == 248 and locator[0]["octets"] == 248, "locator 400 is not intact")
    constraint = state["constraint"]
    require(isinstance(constraint, dict), "constraint evidence is invalid")
    require(constraint["multiplicity"] == 1, "constraint multiplicity differs")
    require(tuple(constraint["literals"]) == expected_types, "constraint literals differ")
    audit = state["audit"]
    require(isinstance(audit, dict), "audit evidence is invalid")
    require(audit["events"] == 80 and audit["valid_hashes"] == 80, "audit hashes differ")
    require(not audit["broken_predecessor_links"], "audit predecessor links are broken")
    require(len(state["triggers"]) == 2 and all(t["tgenabled"] == "O" for t in state["triggers"]), "append-only triggers differ")
    privileges = state["privileges"]
    require(isinstance(privileges, dict), "privilege evidence is invalid")
    require(privileges["audit_events"]["select"] and privileges["audit_events"]["insert"], "audit SELECT/INSERT missing")
    for forbidden in ("update", "delete", "truncate", "references", "trigger"):
        require(not privileges["audit_events"][forbidden], f"forbidden audit privilege: {forbidden}")
    require(privileges["app_owned_objects"] == 0, "app owns database objects")
    require(not privileges["app_can_create_public"], "app can create in public")
    require(not privileges["app_can_set_role_owner"], "app can set role owner")
    require(state["advisory_locks"] == 0, "advisory locks exist")
    require(state["other_client_sessions"] == 0, "unexpected client sessions exist")
    if phase == "0020":
        require(state["invariant_sha256"] == EXPECTED_0020_FINGERPRINT, "0020 fingerprint differs")


def apply_real(engine: Engine) -> dict[str, object]:
    before = capture(engine)
    validate_phase(before, "0020")
    executed = run_migrations(engine)
    require(executed == [VERSION_0021], "runner did not execute exclusively 0021")
    after = capture(engine)
    validate_phase(after, "0021")
    return {"status": "PASS", "executed": executed, "before": before, "after": after}


def cycle(engine: Engine) -> dict[str, object]:
    initial = capture(engine)
    validate_phase(initial, "0020")
    first = run_migrations(engine)
    require(first == [VERSION_0021], "first runner did not execute exclusively 0021")
    upgraded = capture(engine)
    validate_phase(upgraded, "0021")
    second = run_migrations(engine)
    require(second == [], "second runner was not idempotent")
    migration_0021.downgrade(engine)
    with engine.begin() as connection:
        deleted = connection.execute(
            text("DELETE FROM schema_migrations WHERE version = :version"),
            {"version": VERSION_0021},
        ).rowcount
    require(deleted == 1, "disposable downgrade ledger deletion differs")
    downgraded = capture(engine)
    validate_phase(downgraded, "0020")
    rejected = False
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO audit_events (
                        id, event_type, aggregate_type, aggregate_key, actor_identifier,
                        occurred_at, payload_schema, payload_version, payload,
                        correlation_id, request_id, event_hash
                    ) VALUES (
                        gen_random_uuid(), 'scientific_decision_applied', 'probe', 'probe',
                        'gate-b-disposable', now(), 'audit.scientific_decision_applied.v1',
                        1, '{}'::jsonb, gen_random_uuid(), 'gate-b-probe', repeat('0', 64)
                    )
                    """
                )
            )
    except Exception:
        rejected = True
    require(rejected, "0020 did not reject scientific_decision_applied")
    reupgrade = run_migrations(engine)
    require(reupgrade == [VERSION_0021], "re-upgrade did not execute exclusively 0021")
    final = capture(engine)
    validate_phase(final, "0021")
    third = run_migrations(engine)
    require(third == [], "post-reupgrade runner was not idempotent")
    return {
        "status": "PASS",
        "first_runner": first,
        "second_runner": second,
        "downgrade_rejected_scientific_event": rejected,
        "reupgrade_runner": reupgrade,
        "post_reupgrade_runner": third,
        "initial": initial,
        "upgraded": upgraded,
        "downgraded": downgraded,
        "final": final,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="B2B.2 Gate B controlled operator")
    parser.add_argument("command", choices=("capture", "validate-0020", "validate-0021", "apply-real", "cycle"))
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(bool(args.database_url.strip()), "database URL is required")
    engine = create_engine(args.database_url, pool_pre_ping=True)
    try:
        if args.command in ("capture", "validate-0020", "validate-0021"):
            result = capture(engine)
            if args.command != "capture":
                validate_phase(result, args.command.removeprefix("validate-"))
                result = {"status": "PASS", **result}
        elif args.command == "apply-real":
            result = apply_real(engine)
        else:
            result = cycle(engine)
        write_json(args.output, result)
        print(json.dumps({"status": result.get("status", "CAPTURED"), "output": str(args.output)}))
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"GATE_B_ERROR: {type(error).__name__}: {error}", file=sys.stderr)
        raise
