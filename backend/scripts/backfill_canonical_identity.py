from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, create_engine, inspect, text
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.services.canonical_identity import CanonicalIdentityResolver
from app.services.investigator_seed import DEFAULT_SEED_PATH, InvestigatorSeedService, normalize_name_key
from scripts.preflight_canonical_identity import (
    PROPOSED_COLUMNS,
    SNAPSHOT_QUERIES,
    assert_snapshot_unchanged,
    enforce_postgres_read_only,
    load_dataset,
    simulate_dataset,
    snapshot_database,
)


MIGRATION_VERSION = "20260712_0016_canonical_identity_fields"
SNAPSHOT_FORMAT = "canonical_identity_backfill_snapshot"
SNAPSHOT_VERSION = 1
TARGET_TABLES = ("person_roles", "scientific_production_authors")
CANONICAL_FIELDS = PROPOSED_COLUMNS
DATABASE_HASH_LABELS = tuple(SNAPSHOT_QUERIES)
BACKUP_HEADER = b"PGDMP"
IMMUTABLE_DATABASE_HASH_LABELS = tuple(
    label for label in DATABASE_HASH_LABELS if label not in {"role_records_full", "author_records_full"}
)
APPROVED_AUTOMATIC_GROUPS = (
    "Fernando Zambrano",
    "Jorge Merchan",
    "Maria Estefania Sanchez",
)
APPROVED_SUMMARY = {
    "identities_before": 85,
    "identities_after": 54,
    "person_document_participations_before": 122,
    "person_document_participations_after": 83,
    "roles_affected": 120,
    "authors_affected": 43,
    "pending_changes": 53,
    "product_kpi_before": 2,
    "product_kpi_after": 2,
}
PENDING_SURNAME_GATES = ("delgado", "ramirez")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Transactional canonical identity backfill.")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--snapshot-only", action="store_true")
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--restore-snapshot", action="store_true")
    parser.add_argument("--database-url")
    parser.add_argument("--seed-file", default=str(DEFAULT_SEED_PATH))
    parser.add_argument("--snapshot-path")
    parser.add_argument("--backup-path")
    parser.add_argument("--backup-sha256")
    parser.add_argument("--confirm-restore", action="store_true")
    return parser


def validate_cli_args(args: argparse.Namespace) -> None:
    if args.snapshot_only and not args.snapshot_path:
        raise ValueError("--snapshot-only requires --snapshot-path")
    if args.apply and (not args.snapshot_path or not args.backup_path or not args.backup_sha256):
        raise ValueError("--apply requires --snapshot-path, --backup-path, and --backup-sha256")
    if args.apply and not args.database_url:
        raise ValueError("--apply requires --database-url")
    if args.restore_snapshot and not args.snapshot_path:
        raise ValueError("--restore-snapshot requires --snapshot-path")
    if args.restore_snapshot and not args.database_url:
        raise ValueError("--restore-snapshot requires --database-url")
    if args.restore_snapshot and not args.confirm_restore:
        raise ValueError("--restore-snapshot requires --confirm-restore")


def snapshot_sidecar_path(snapshot_path: Path) -> Path:
    return Path(f"{snapshot_path}.sha256")


def verify_backup_artifact(backup_path: Path, expected_sha256: str) -> str:
    if not backup_path.exists():
        raise FileNotFoundError(f"backup file does not exist: {backup_path}")
    digest = hashlib.sha256()
    with backup_path.open("rb") as handle:
        header = handle.read(len(BACKUP_HEADER))
        if header != BACKUP_HEADER:
            raise RuntimeError("backup file is not a custom pg_dump archive with PGDMP header")
        digest.update(header)
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    actual_sha256 = digest.hexdigest()
    if actual_sha256 != str(expected_sha256 or "").lower():
        raise RuntimeError("backup sha256 mismatch")
    return actual_sha256


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Unsupported JSON value: {type(value)!r}")


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")


def _sha256_hex(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _name_tokens(value: Any) -> set[str]:
    return {token.lower() for token in normalize_name_key(value).split()}


def _automatic_group_label(canonical_name: Any) -> str | None:
    tokens = _name_tokens(canonical_name)
    if {"fernando", "zambrano"} <= tokens:
        return "Fernando Zambrano"
    if {"jorge", "merchan"} <= tokens:
        return "Jorge Merchan"
    if {"maria", "estefania", "sanchez"} <= tokens:
        return "Maria Estefania Sanchez"
    return None


def assert_approved_gates(result: dict[str, Any]) -> dict[str, Any]:
    summary = result.get("summary", {})
    for key, expected in (
        ("identities_before", APPROVED_SUMMARY["identities_before"]),
        ("identities_after", APPROVED_SUMMARY["identities_after"]),
        (
            "person_document_participations_before",
            APPROVED_SUMMARY["person_document_participations_before"],
        ),
        (
            "person_document_participations_after",
            APPROVED_SUMMARY["person_document_participations_after"],
        ),
        ("roles_affected", APPROVED_SUMMARY["roles_affected"]),
        ("authors_affected", APPROVED_SUMMARY["authors_affected"]),
        ("pending_changes", APPROVED_SUMMARY["pending_changes"]),
    ):
        if summary.get(key) != expected:
            raise RuntimeError(f"approved metric mismatch for {key}: {summary.get(key)!r} != {expected!r}")

    product_kpi = summary.get("product_kpi_impact", {})
    if product_kpi.get("before_eligible_products") != APPROVED_SUMMARY["product_kpi_before"]:
        raise RuntimeError("approved metric mismatch for product_kpi_before")
    if product_kpi.get("expected_eligible_products") != APPROVED_SUMMARY["product_kpi_after"]:
        raise RuntimeError("approved metric mismatch for product_kpi_after")
    if summary.get("consistency_errors"):
        raise RuntimeError("consistency errors detected during backfill gating")

    automatic_groups = [_automatic_group_label(item.get("canonical_name")) for item in result.get("automatic_merges", [])]
    if any(group is None for group in automatic_groups):
        raise RuntimeError("automatic canonical groups include an unapproved name")
    if sorted(automatic_groups) != sorted(APPROVED_AUTOMATIC_GROUPS):
        raise RuntimeError("automatic canonical groups drifted from approved set")

    found_pending = {surname: False for surname in PENDING_SURNAME_GATES}
    for row in result.get("row_changes", []):
        source_name = row.get("raw_name") or row.get("raw_author_name") or row.get("canonical_name") or ""
        tokens = _name_tokens(source_name) | _name_tokens(row.get("canonical_name"))
        for surname in PENDING_SURNAME_GATES:
            if surname in tokens:
                found_pending[surname] = True
                if row.get("identity_status") != "pending":
                    raise RuntimeError(f"{surname.title()} rows must remain pending")
    missing_pending = sorted(surname for surname, present in found_pending.items() if not present)
    if missing_pending:
        raise RuntimeError(f"missing required pending surname coverage: {', '.join(missing_pending)}")

    return {
        "identities_before": APPROVED_SUMMARY["identities_before"],
        "identities_after": APPROVED_SUMMARY["identities_after"],
        "person_document_participations_before": APPROVED_SUMMARY["person_document_participations_before"],
        "person_document_participations_after": APPROVED_SUMMARY["person_document_participations_after"],
        "roles": APPROVED_SUMMARY["roles_affected"],
        "authors": APPROVED_SUMMARY["authors_affected"],
        "total_targets": APPROVED_SUMMARY["roles_affected"] + APPROVED_SUMMARY["authors_affected"],
        "automatic_groups": APPROVED_AUTOMATIC_GROUPS,
        "pending_rows": APPROVED_SUMMARY["pending_changes"],
        "product_kpi_before": APPROVED_SUMMARY["product_kpi_before"],
        "product_kpi_after": APPROVED_SUMMARY["product_kpi_after"],
    }


def _normalize_rows_by_table(rows_by_table: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    if "roles" in rows_by_table or "authors" in rows_by_table:
        return {
            "person_roles": sorted(
                [dict(row) for row in rows_by_table.get("roles", [])],
                key=lambda row: int(row["id"]),
            ),
            "scientific_production_authors": sorted(
                [dict(row) for row in rows_by_table.get("authors", [])],
                key=lambda row: int(row["id"]),
            ),
        }
    return {
        table: sorted([dict(row) for row in rows_by_table.get(table, [])], key=lambda row: int(row["id"]))
        for table in TARGET_TABLES
    }


def _rows_from_simulation(result: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    rows_by_table: dict[str, list[dict[str, Any]]] = {table: [] for table in TARGET_TABLES}
    for row in result.get("row_changes", []):
        source_table = row["source_table"]
        if source_table in rows_by_table:
            rows_by_table[source_table].append(dict(row))
    for table in TARGET_TABLES:
        rows_by_table[table].sort(key=lambda row: int(row["id"]))
    return rows_by_table


def _normalize_scalar(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_normalize_scalar(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_scalar(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_scalar(item) for key, item in value.items()}
    return value


def _snapshot_immutable_fields(table: str) -> tuple[str, ...]:
    if table == "person_roles":
        return (
            "id",
            "raw_name",
            "person_key",
            "import_job_id",
            "scientific_production_id",
            "validation_status",
        )
    return (
        "id",
        "raw_author_name",
        "import_job_id",
        "production_id",
        "validation_status",
    )


def _immutable_payload(table: str, row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _normalize_scalar(value)
        for key, value in sorted(row.items())
        if key not in CANONICAL_FIELDS
    }


def _evidence_hash(table: str, row: dict[str, Any]) -> str:
    return _sha256_hex({"table": table, "immutable_payload": _immutable_payload(table, row)})


def _snapshot_row(table: str, row: dict[str, Any]) -> dict[str, Any]:
    snapshot = {field: _normalize_scalar(row.get(field)) for field in _snapshot_immutable_fields(table)}
    snapshot.update({field: _normalize_scalar(row.get(field)) for field in CANONICAL_FIELDS})
    snapshot["evidence_hash"] = _evidence_hash(table, row)
    return snapshot


def _snapshot_rows_by_table(snapshot_document: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    rows = snapshot_document.get("rows", {})
    return {
        table: sorted([dict(row) for row in rows.get(table, [])], key=lambda row: int(row["id"]))
        for table in TARGET_TABLES
    }


def calculate_manifest_sha256(snapshot_document: dict[str, Any]) -> str:
    manifest_source = {
        key: value
        for key, value in snapshot_document.items()
        if key not in {"captured_at_utc", "manifest_sha256"}
    }
    return _sha256_hex(manifest_source)


def build_snapshot_document(
    rows_by_table: dict[str, Any],
    database_hashes: dict[str, Any],
    *,
    captured_at_utc: str | None = None,
    approved_metrics: dict[str, Any],
) -> dict[str, Any]:
    normalized = _normalize_rows_by_table(rows_by_table)
    snapshot_document = {
        "format": SNAPSHOT_FORMAT,
        "version": SNAPSHOT_VERSION,
        "captured_at_utc": captured_at_utc or datetime.now(timezone.utc).isoformat(),
        "migration_version": MIGRATION_VERSION,
        "approved_metrics": approved_metrics,
        "counts": {
            "person_roles": len(normalized["person_roles"]),
            "scientific_production_authors": len(normalized["scientific_production_authors"]),
            "total_targets": len(normalized["person_roles"]) + len(normalized["scientific_production_authors"]),
        },
        "target_ids": {
            table: [int(row["id"]) for row in normalized[table]]
            for table in TARGET_TABLES
        },
        "database_hashes": _normalize_scalar(database_hashes),
        "rows": {
            table: [_snapshot_row(table, row) for row in normalized[table]]
            for table in TARGET_TABLES
        },
    }
    snapshot_document["manifest_sha256"] = calculate_manifest_sha256(snapshot_document)
    return snapshot_document


def write_snapshot_document(snapshot_document: dict[str, Any], snapshot_path: Path) -> None:
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(
        json.dumps(snapshot_document, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )
    snapshot_sidecar_path(snapshot_path).write_text(
        snapshot_document["manifest_sha256"] + "\n",
        encoding="utf-8",
    )


def load_snapshot_document(snapshot_path: Path) -> dict[str, Any]:
    if not snapshot_path.exists():
        raise FileNotFoundError(f"snapshot file does not exist: {snapshot_path}")
    sidecar = snapshot_sidecar_path(snapshot_path)
    if not sidecar.exists():
        raise FileNotFoundError(f"snapshot checksum file does not exist: {sidecar}")
    snapshot_document = json.loads(snapshot_path.read_text(encoding="utf-8"))
    expected_manifest = calculate_manifest_sha256(snapshot_document)
    if snapshot_document.get("manifest_sha256") != expected_manifest:
        raise RuntimeError("bad manifest in snapshot document")
    if sidecar.read_text(encoding="utf-8").strip() != expected_manifest:
        raise RuntimeError("snapshot checksum sidecar does not match manifest")
    return snapshot_document


def _assert_database_hashes_match(
    snapshot_document: dict[str, Any],
    current_database_hashes: dict[str, Any],
    *,
    allow_canonical_mismatch: bool,
) -> None:
    snapshot_hashes = snapshot_document.get("database_hashes", {})
    labels = IMMUTABLE_DATABASE_HASH_LABELS if allow_canonical_mismatch else DATABASE_HASH_LABELS
    for label in labels:
        if snapshot_hashes.get(label) != current_database_hashes.get(label):
            raise RuntimeError(f"database hash mismatch for {label}")
    for label in ("canonical_columns", "migration_table", "schema_migrations"):
        if snapshot_hashes.get(label) != current_database_hashes.get(label):
            raise RuntimeError(f"database hash mismatch for {label}")


def verify_snapshot(
    snapshot_document: dict[str, Any],
    current_rows_by_table: dict[str, Any],
    current_database_hashes: dict[str, Any],
    *,
    allow_canonical_mismatch: bool = False,
) -> None:
    if snapshot_document.get("format") != SNAPSHOT_FORMAT:
        raise RuntimeError("bad manifest: unexpected snapshot format")
    if snapshot_document.get("version") != SNAPSHOT_VERSION:
        raise RuntimeError("bad manifest: unexpected snapshot version")
    if snapshot_document.get("migration_version") != MIGRATION_VERSION:
        raise RuntimeError("bad manifest: unexpected migration version")
    if snapshot_document.get("manifest_sha256") != calculate_manifest_sha256(snapshot_document):
        raise RuntimeError("bad manifest in snapshot document")

    normalized_current = _normalize_rows_by_table(current_rows_by_table)
    snapshot_rows = _snapshot_rows_by_table(snapshot_document)

    for table in TARGET_TABLES:
        snapshot_ids = snapshot_document.get("target_ids", {}).get(table, [])
        snapshot_row_ids = [int(row["id"]) for row in snapshot_rows[table]]
        if snapshot_row_ids != snapshot_ids:
            raise RuntimeError(f"snapshot rows do not match target_ids for {table}")
        if snapshot_document.get("counts", {}).get(table) != len(snapshot_rows[table]):
            raise RuntimeError(f"snapshot rows do not match counts for {table}")
        current_ids = [int(row["id"]) for row in normalized_current[table]]
        if snapshot_ids != current_ids:
            raise RuntimeError(f"{table} ids changed: snapshot={snapshot_ids} current={current_ids}")
        if snapshot_document.get("counts", {}).get(table) != len(snapshot_ids):
            raise RuntimeError(f"{table} counts changed")
    if snapshot_document.get("counts", {}).get("total_targets") != sum(
        snapshot_document.get("counts", {}).get(table, 0) for table in TARGET_TABLES
    ):
        raise RuntimeError("snapshot total_targets does not match table counts")

    for table in TARGET_TABLES:
        current_by_id = {int(row["id"]): row for row in normalized_current[table]}
        for snapshot_row in snapshot_rows[table]:
            row_id = int(snapshot_row["id"])
            current_row = current_by_id[row_id]
            for field in _snapshot_immutable_fields(table):
                if _normalize_scalar(current_row.get(field)) != snapshot_row.get(field):
                    raise RuntimeError(f"{table}:{row_id} immutable field mismatch: {field}")
            if _evidence_hash(table, current_row) != snapshot_row.get("evidence_hash"):
                raise RuntimeError(f"{table}:{row_id} evidence hash mismatch")
            if not allow_canonical_mismatch:
                for field in CANONICAL_FIELDS:
                    if _normalize_scalar(current_row.get(field)) != snapshot_row.get(field):
                        raise RuntimeError(f"{table}:{row_id} canonical field mismatch: {field}")

    _assert_database_hashes_match(
        snapshot_document,
        _normalize_scalar(current_database_hashes),
        allow_canonical_mismatch=allow_canonical_mismatch,
    )


def _rows_by_id(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {int(row["id"]): row for row in rows}


def _canonical_values(row: dict[str, Any]) -> dict[str, Any]:
    return {field: _normalize_scalar(row.get(field)) for field in CANONICAL_FIELDS}


def build_apply_plan(
    current_rows_by_table: dict[str, Any],
    proposed_rows_by_table: dict[str, Any],
) -> dict[str, Any]:
    current = _normalize_rows_by_table(current_rows_by_table)
    proposed = _normalize_rows_by_table(proposed_rows_by_table)
    updates: dict[str, list[dict[str, Any]]] = {table: [] for table in TARGET_TABLES}
    changed_rows = 0
    unchanged_rows = 0
    locked_rows = 0

    for table in TARGET_TABLES:
        current_by_id = _rows_by_id(current[table])
        proposed_by_id = _rows_by_id(proposed[table])
        if sorted(current_by_id) != sorted(proposed_by_id):
            raise RuntimeError(f"proposed rows do not match current rows for {table}")
        for row_id in sorted(current_by_id):
            current_row = current_by_id[row_id]
            proposed_row = proposed_by_id[row_id]
            if current_row.get("identity_locked"):
                locked_rows += 1
                continue
            desired_values = _canonical_values(proposed_row)
            if _canonical_values(current_row) == desired_values:
                unchanged_rows += 1
                continue
            updates[table].append({"id": row_id, **desired_values})
            changed_rows += 1

    return {
        "updates": updates,
        "total_targets": changed_rows + unchanged_rows + locked_rows,
        "changed_rows": changed_rows,
        "unchanged_rows": unchanged_rows,
        "locked_rows": locked_rows,
    }


def assert_applyable_canonical_states(
    snapshot_document: dict[str, Any],
    current_rows_by_table: dict[str, Any],
    proposed_rows_by_table: dict[str, Any],
) -> None:
    current = _normalize_rows_by_table(current_rows_by_table)
    proposed = _normalize_rows_by_table(proposed_rows_by_table)
    snapshot_rows = _snapshot_rows_by_table(snapshot_document)

    for table in TARGET_TABLES:
        current_by_id = _rows_by_id(current[table])
        proposed_by_id = _rows_by_id(proposed[table])
        snapshot_by_id = _rows_by_id(snapshot_rows[table])
        expected_ids = sorted(snapshot_by_id)
        if sorted(current_by_id) != expected_ids:
            raise RuntimeError(f"current rows do not match snapshot ids for {table}")
        if sorted(proposed_by_id) != expected_ids:
            raise RuntimeError(f"proposed rows do not match snapshot ids for {table}")

        for row_id in expected_ids:
            current_values = _canonical_values(current_by_id[row_id])
            snapshot_values = _canonical_values(snapshot_by_id[row_id])
            proposed_values = _canonical_values(proposed_by_id[row_id])
            if current_values == snapshot_values or current_values == proposed_values:
                continue
            raise RuntimeError(
                f"{table}:{row_id} canonical drift: current state matches neither snapshot nor proposed"
            )


def build_restore_plan(
    current_rows_by_table: dict[str, Any],
    snapshot_document: dict[str, Any],
) -> dict[str, Any]:
    current = _normalize_rows_by_table(current_rows_by_table)
    snapshot_rows = _snapshot_rows_by_table(snapshot_document)
    updates: dict[str, list[dict[str, Any]]] = {table: [] for table in TARGET_TABLES}
    changed_rows = 0

    for table in TARGET_TABLES:
        current_by_id = _rows_by_id(current[table])
        snapshot_by_id = _rows_by_id(snapshot_rows[table])
        if sorted(current_by_id) != sorted(snapshot_by_id):
            raise RuntimeError(f"restore rows do not match current rows for {table}")
        for row_id in sorted(snapshot_by_id):
            snapshot_row = snapshot_by_id[row_id]
            desired_values = {field: snapshot_row.get(field) for field in CANONICAL_FIELDS}
            if _canonical_values(current_by_id[row_id]) == desired_values:
                continue
            updates[table].append({"id": row_id, **desired_values})
            changed_rows += 1

    total_targets = sum(len(snapshot_rows[table]) for table in TARGET_TABLES)
    return {
        "updates": updates,
        "changed_rows": changed_rows,
        "restored_rows": total_targets,
        "total_targets": total_targets,
    }


def _write_value(field: str, value: Any) -> Any:
    return value


def apply_updates(connection: Any, updates: dict[str, list[dict[str, Any]]]) -> int:
    changed_rows = 0
    for table in TARGET_TABLES:
        for row in sorted(updates.get(table, []), key=lambda item: int(item["id"])):
            params = {
                "id": int(row["id"]),
                **{field: _write_value(field, row.get(field)) for field in CANONICAL_FIELDS},
            }
            result = connection.execute(
                text(
                    f"""
                    UPDATE {table}
                    SET canonical_identity_key = :canonical_identity_key,
                        canonical_name = :canonical_name,
                        identity_source = :identity_source,
                        identity_confidence = :identity_confidence,
                        identity_reason = :identity_reason,
                        identity_locked = :identity_locked,
                        identity_decided_by = :identity_decided_by,
                        identity_decided_at = :identity_decided_at
                    WHERE id = :id
                    """
                ),
                params,
            )
            if result.rowcount != 1:
                raise RuntimeError(f"expected exactly one updated row for {table}:{row['id']}")
            changed_rows += 1
    return changed_rows


def run_update_batch(
    engine: Any,
    updates: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    with engine.begin() as connection:
        changed_rows = apply_updates(connection, updates)
    return {"changed_rows": changed_rows}


def _resolver(seed_file: str) -> CanonicalIdentityResolver:
    return CanonicalIdentityResolver(seed_service=InvestigatorSeedService(seed_file))


def _database_hashes_with_schema_migrations(connection: Any) -> dict[str, Any]:
    hashes = _normalize_scalar(snapshot_database(connection))
    inspector = inspect(connection)
    if inspector.has_table("schema_migrations"):
        hashes["schema_migrations"] = [
            row[0]
            for row in connection.execute(
                text("SELECT version FROM schema_migrations ORDER BY applied_at, version")
            ).fetchall()
        ]
    else:
        hashes["schema_migrations"] = []
    return hashes


def _assert_write_prerequisites(connection: Any, database_hashes: dict[str, Any]) -> None:
    if connection.dialect.name != "postgresql":
        raise RuntimeError("writes require PostgreSQL")
    present_columns = {
        f"{item['table_name']}.{item['column_name']}"
        for item in database_hashes.get("canonical_columns", [])
    }
    required_columns = {
        f"{table}.{column}"
        for table in TARGET_TABLES
        for column in CANONICAL_FIELDS
    }
    missing_columns = sorted(required_columns - present_columns)
    if missing_columns:
        raise RuntimeError(f"0016 schema missing canonical columns: {', '.join(missing_columns)}")
    if MIGRATION_VERSION not in database_hashes.get("schema_migrations", []):
        raise RuntimeError(f"schema_migrations is missing {MIGRATION_VERSION}")


def _lock_table_rows(connection: Any, table: str, ids: list[int]) -> None:
    if not ids:
        return
    statement = text(
        f"SELECT id FROM {table} WHERE id IN :ids ORDER BY id FOR UPDATE NOWAIT"
    ).bindparams(bindparam("ids", expanding=True))
    try:
        locked_ids = [int(row[0]) for row in connection.execute(statement, {"ids": ids}).fetchall()]
    except OperationalError as exc:
        message = str(exc).lower()
        if "lock" in message or "nowait" in message:
            raise RuntimeError(f"conflicting locks prevented updating {table}") from exc
        raise
    if locked_ids != ids:
        raise RuntimeError(f"missing locked rows for {table}: expected {ids} got {locked_ids}")


def _lock_snapshot_targets(connection: Any, snapshot_document: dict[str, Any]) -> None:
    for table in TARGET_TABLES:
        ids = [int(row_id) for row_id in snapshot_document.get("target_ids", {}).get(table, [])]
        _lock_table_rows(connection, table, ids)


def _assert_immutable_hashes_unchanged(before: dict[str, Any], after: dict[str, Any]) -> None:
    for label in (*IMMUTABLE_DATABASE_HASH_LABELS, "canonical_columns", "migration_table", "schema_migrations"):
        if before.get(label) != after.get(label):
            raise RuntimeError(f"immutable database hash mismatch after write: {label}")


def _immutable_hash_report(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {
        label: {"before": before.get(label), "after": after.get(label)}
        for label in (*IMMUTABLE_DATABASE_HASH_LABELS, "canonical_columns", "migration_table", "schema_migrations")
    }


def _assert_rows_match_plan(
    current_rows_by_table: dict[str, Any],
    proposed_rows_by_table: dict[str, Any],
    *,
    preserve_locked: bool,
) -> None:
    current = _normalize_rows_by_table(current_rows_by_table)
    proposed = _normalize_rows_by_table(proposed_rows_by_table)
    for table in TARGET_TABLES:
        current_by_id = _rows_by_id(current[table])
        proposed_by_id = _rows_by_id(proposed[table])
        if sorted(current_by_id) != sorted(proposed_by_id):
            raise RuntimeError(f"post-write ids changed for {table}")
        for row_id in sorted(current_by_id):
            current_row = current_by_id[row_id]
            expected_row = proposed_by_id[row_id]
            if preserve_locked and current_row.get("identity_locked"):
                continue
            if _canonical_values(current_row) != _canonical_values(expected_row):
                raise RuntimeError(f"post-write canonical mismatch for {table}:{row_id}")


def _assert_rows_match_snapshot(current_rows_by_table: dict[str, Any], snapshot_document: dict[str, Any]) -> None:
    current = _normalize_rows_by_table(current_rows_by_table)
    snapshot_rows = _snapshot_rows_by_table(snapshot_document)
    for table in TARGET_TABLES:
        current_by_id = _rows_by_id(current[table])
        snapshot_by_id = _rows_by_id(snapshot_rows[table])
        if sorted(current_by_id) != sorted(snapshot_by_id):
            raise RuntimeError(f"restore ids changed for {table}")
        for row_id in sorted(snapshot_by_id):
            if _canonical_values(current_by_id[row_id]) != _canonical_values(snapshot_by_id[row_id]):
                raise RuntimeError(f"restore canonical mismatch for {table}:{row_id}")


def _run_read_only(database_url: str, seed_file: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                if connection.dialect.name == "postgresql":
                    enforce_postgres_read_only(connection)
                before = _database_hashes_with_schema_migrations(connection)
                dataset = load_dataset(connection)
                result = simulate_dataset(dataset, resolver=_resolver(seed_file))
                after = _database_hashes_with_schema_migrations(connection)
                assert_snapshot_unchanged(before, after)
            finally:
                transaction.rollback()
        approved_metrics = assert_approved_gates(result)
        return dataset, result, approved_metrics
    finally:
        engine.dispose()


def run_dry_run(database_url: str, seed_file: str = str(DEFAULT_SEED_PATH)) -> dict[str, Any]:
    _dataset, result, approved_metrics = _run_read_only(database_url, seed_file)
    return {
        "mode": "dry-run",
        "approved_metrics": approved_metrics,
        "automatic_groups": APPROVED_AUTOMATIC_GROUPS,
        "pending_rows": approved_metrics["pending_rows"],
    }


def run_snapshot_only(
    database_url: str,
    snapshot_path: Path,
    seed_file: str = str(DEFAULT_SEED_PATH),
) -> dict[str, Any]:
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                if connection.dialect.name == "postgresql":
                    enforce_postgres_read_only(connection)
                before = _database_hashes_with_schema_migrations(connection)
                dataset = load_dataset(connection)
                result = simulate_dataset(dataset, resolver=_resolver(seed_file))
                after = _database_hashes_with_schema_migrations(connection)
                assert_snapshot_unchanged(before, after)
            finally:
                transaction.rollback()
        approved_metrics = assert_approved_gates(result)
        snapshot_document = build_snapshot_document(
            _normalize_rows_by_table(dataset),
            before,
            approved_metrics=approved_metrics,
        )
        write_snapshot_document(snapshot_document, snapshot_path)
        return {
            "mode": "snapshot-only",
            "snapshot_path": str(snapshot_path),
            "counts": snapshot_document["counts"],
            "manifest_sha256": snapshot_document["manifest_sha256"],
            "approved_metrics": approved_metrics,
        }
    finally:
        engine.dispose()


def run_apply(
    database_url: str,
    *,
    snapshot_path: Path,
    backup_path: Path,
    backup_sha256: str,
    seed_file: str = str(DEFAULT_SEED_PATH),
) -> dict[str, Any]:
    if not snapshot_path.exists():
        raise FileNotFoundError(f"snapshot file does not exist: {snapshot_path}")
    verify_backup_artifact(backup_path, backup_sha256)
    snapshot_document = load_snapshot_document(snapshot_path)

    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            before = _database_hashes_with_schema_migrations(connection)
            _assert_write_prerequisites(connection, before)
            _lock_snapshot_targets(connection, snapshot_document)

            dataset = load_dataset(connection)
            result = simulate_dataset(dataset, resolver=_resolver(seed_file))
            approved_metrics = assert_approved_gates(result)
            current_rows = _normalize_rows_by_table(dataset)
            proposed_rows = _rows_from_simulation(result)
            verify_snapshot(
                snapshot_document,
                current_rows,
                before,
                allow_canonical_mismatch=True,
            )
            assert_applyable_canonical_states(snapshot_document, current_rows, proposed_rows)

            plan = build_apply_plan(current_rows, proposed_rows)
            apply_updates(connection, plan["updates"])

            post_dataset = load_dataset(connection)
            post_rows = _normalize_rows_by_table(post_dataset)
            post_result = simulate_dataset(post_dataset, resolver=_resolver(seed_file))
            post_metrics = assert_approved_gates(post_result)
            after = _database_hashes_with_schema_migrations(connection)
            _assert_immutable_hashes_unchanged(before, after)
            _assert_rows_match_plan(post_rows, proposed_rows, preserve_locked=True)
            if post_metrics != approved_metrics:
                raise RuntimeError("post-write metric mismatch")

        return {
            "mode": "apply",
            "total_targets": plan["total_targets"],
            "changed_rows": plan["changed_rows"],
            "unchanged_rows": plan["unchanged_rows"],
            "locked_rows": plan["locked_rows"],
            "approved_metrics": approved_metrics,
            "automatic_groups": APPROVED_AUTOMATIC_GROUPS,
            "pending_rows": approved_metrics["pending_rows"],
            "product_kpi": {
                "before": approved_metrics["product_kpi_before"],
                "after": approved_metrics["product_kpi_after"],
            },
            "immutable_hashes": _immutable_hash_report(before, after),
        }
    finally:
        engine.dispose()


def run_restore_snapshot(
    database_url: str,
    *,
    snapshot_path: Path,
    confirm_restore: bool,
) -> dict[str, Any]:
    if not confirm_restore:
        raise ValueError("--restore-snapshot requires --confirm-restore")
    snapshot_document = load_snapshot_document(snapshot_path)
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            before = _database_hashes_with_schema_migrations(connection)
            _assert_write_prerequisites(connection, before)
            _lock_snapshot_targets(connection, snapshot_document)

            dataset = load_dataset(connection)
            current_rows = _normalize_rows_by_table(dataset)
            verify_snapshot(
                snapshot_document,
                current_rows,
                before,
                allow_canonical_mismatch=True,
            )

            plan = build_restore_plan(current_rows, snapshot_document)
            apply_updates(connection, plan["updates"])

            post_rows = _normalize_rows_by_table(load_dataset(connection))
            after = _database_hashes_with_schema_migrations(connection)
            _assert_immutable_hashes_unchanged(before, after)
            _assert_rows_match_snapshot(post_rows, snapshot_document)

        return {
            "mode": "restore-snapshot",
            "total_targets": plan["total_targets"],
            "changed_rows": plan["changed_rows"],
            "restored_rows": plan["restored_rows"],
            "immutable_hashes": _immutable_hash_report(before, after),
        }
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> None:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    try:
        validate_cli_args(args)
    except ValueError as exc:
        parser.error(str(exc))
    database_url = args.database_url or settings.database_url

    if args.dry_run:
        result = run_dry_run(database_url, args.seed_file)
    elif args.snapshot_only:
        result = run_snapshot_only(database_url, Path(args.snapshot_path), args.seed_file)
    elif args.apply:
        result = run_apply(
            database_url,
            snapshot_path=Path(args.snapshot_path),
            backup_path=Path(args.backup_path),
            backup_sha256=args.backup_sha256,
            seed_file=args.seed_file,
        )
    else:
        result = run_restore_snapshot(
            database_url,
            snapshot_path=Path(args.snapshot_path),
            confirm_restore=args.confirm_restore,
        )
    print(json.dumps(result, ensure_ascii=False, default=_json_default))


if __name__ == "__main__":
    main()
