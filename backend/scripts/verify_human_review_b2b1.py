from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
from importlib import import_module
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from app.core.migrations import MIGRATIONS, run_migrations
from app.schemas.human_review_operations import (
    B2B1BackfillApplyResultV1,
    B2B1BackfillPlanV1,
    B2B1InvariantSnapshotV1,
)
from app.services.human_review_backfill import backfill_plan_sha256
from app.services.human_review_invariants import capture_b2b1_invariants


HISTORICAL_MARKER = "20260629_0002_add_scientific_production_authors"
INITIAL_HEAD = "20260712_0016_canonical_identity_fields"
FINAL_HEAD = "20260713_0020_human_review_audit"
_ALL_REGISTRY_VERSIONS = tuple(version for version, _upgrade in MIGRATIONS)
REGISTRY_VERSIONS = _ALL_REGISTRY_VERSIONS[
    : _ALL_REGISTRY_VERSIONS.index(FINAL_HEAD) + 1
]
EXPECTED_SEMANTIC_PLAN_HASH = (
    "dc2299a29e0485eb8fd9fee712fad7574b875abb535b174169fb4561f5741e41"
)
B2B_VERSIONS = REGISTRY_VERSIONS[16:]

REQUIRED_TOC_TABLES = (
    "schema_migrations",
    "users",
    "academic_periods",
    "person_roles",
    "scientific_production_authors",
    "scientific_productions",
    "research_entities",
    "research_projects",
    "external_researchers",
    "import_jobs",
    "imported_ocr_traces",
)

BACKFILL_CREATED_FIELDS = (
    "created_review_items",
    "created_decisions",
    "created_overrides",
    "created_identities",
    "created_aliases",
    "created_audit_events",
)

_B2B_TABLES = (
    "user_b2b_capabilities",
    "review_items",
    "review_decisions",
    "canonical_identities",
    "person_aliases",
    "field_overrides",
    "audit_events",
)
_B2B_FUNCTIONS = (
    "b2b_reject_career_capability",
    "b2b_reject_career_role_with_capability",
    "b2b_reject_append_only_mutation",
)
_B2B_TRIGGERS = (
    "trg_user_b2b_capabilities_reject_career",
    "trg_users_reject_career_with_b2b_capability",
    "trg_review_decisions_append_only",
    "trg_audit_events_append_only",
)
_MIGRATION_MODULES = {
    "20260713_0017_b2b_capabilities": (
        "app.migrations.versions.20260713_0017_b2b_capabilities"
    ),
    "20260713_0018_human_review_core": (
        "app.migrations.versions.20260713_0018_human_review_core"
    ),
    "20260713_0019_human_review_projection": (
        "app.migrations.versions.20260713_0019_human_review_projection"
    ),
    "20260713_0020_human_review_audit": (
        "app.migrations.versions.20260713_0020_human_review_audit"
    ),
}
_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
_TOC_TABLE_PATTERN = re.compile(r"\bTABLE\s+public\s+(\S+)\s+\S+\s*$")


class VerificationError(RuntimeError):
    pass


class LedgerPhase(str, Enum):
    INITIAL = "initial"
    UPGRADED = "upgraded"
    DOWNGRADED = "downgraded"
    REUPGRADED = "reupgraded"


@dataclass(frozen=True)
class BackupProof:
    size_bytes: int
    sha256: str
    toc_tables: tuple[str, ...]
    toc_entry_count: int


@dataclass(frozen=True)
class LedgerState:
    phase: str
    registry_revision_count: int
    registry_revisions_present: tuple[str, ...]
    historical_marker_count: int
    total_ledger_rows: int
    effective_registry_head: str


@dataclass(frozen=True)
class HistoricalMarkerRow:
    version: str
    applied_at: str


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def _required_file(path: Path | None, message: str) -> Path:
    if path is None or not path.is_file():
        raise VerificationError(message)
    return path


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _toc_tables(toc_text: str) -> tuple[str, ...]:
    names = []
    for line in toc_text.splitlines():
        match = _TOC_TABLE_PATTERN.search(line)
        if match:
            names.append(match.group(1).strip('"'))
    return tuple(sorted(set(names)))


def verify_backup_artifacts(
    backup: Path | str | None,
    sha256_path: Path | str | None,
    toc_path: Path | str | None,
) -> BackupProof:
    backup_file = _required_file(
        Path(backup) if backup is not None else None,
        "backup artifact is required",
    )
    sha_file = _required_file(
        Path(sha256_path) if sha256_path is not None else None,
        "backup SHA-256 artifact is required",
    )
    toc_file = _required_file(
        Path(toc_path) if toc_path is not None else None,
        "backup TOC artifact is required",
    )
    size = backup_file.stat().st_size
    _require(size > 0, "backup artifact is empty")
    with backup_file.open("rb") as stream:
        _require(stream.read(5) == b"PGDMP", "backup artifact is not PostgreSQL custom format")

    expected_hash = sha_file.read_text(encoding="ascii").strip()
    _require(
        bool(_SHA256_PATTERN.fullmatch(expected_hash)),
        "backup SHA-256 artifact is invalid",
    )
    actual_hash = _file_sha256(backup_file)
    _require(actual_hash == expected_hash.lower(), "backup SHA-256 mismatch")

    toc_text = toc_file.read_text(encoding="utf-8-sig")
    toc_tables = _toc_tables(toc_text)
    missing = [table for table in REQUIRED_TOC_TABLES if table not in toc_tables]
    _require(not missing, "backup TOC is missing required tables: " + ", ".join(missing))
    entry_count = sum(1 for line in toc_text.splitlines() if line and not line.startswith(";"))
    _require(entry_count > 0, "backup TOC has no archive entries")
    return BackupProof(size, actual_hash, toc_tables, entry_count)


def effective_registry_head(ledger_versions: Sequence[str]) -> str:
    present = set(ledger_versions)
    matching = [version for version in REGISTRY_VERSIONS if version in present]
    _require(bool(matching), "ledger contains no current registry revisions")
    return matching[-1]


def validate_ledger(
    ledger_versions: Sequence[str],
    phase: LedgerPhase,
) -> LedgerState:
    versions = tuple(ledger_versions)
    counts = Counter(versions)
    duplicate_registry = [
        version for version in REGISTRY_VERSIONS if counts[version] != 0 and counts[version] != 1
    ]
    _require(
        not duplicate_registry,
        "duplicate ledger revision: " + ", ".join(duplicate_registry),
    )
    marker_count = counts[HISTORICAL_MARKER]
    _require(marker_count == 1, "historical marker must appear exactly once")
    allowed = set(REGISTRY_VERSIONS) | {HISTORICAL_MARKER}
    unknown = sorted(set(versions) - allowed)
    _require(not unknown, "unknown ledger revision: " + ", ".join(unknown))

    expected_registry = (
        REGISTRY_VERSIONS[:16]
        if phase in (LedgerPhase.INITIAL, LedgerPhase.DOWNGRADED)
        else REGISTRY_VERSIONS
    )
    actual_registry = tuple(version for version in REGISTRY_VERSIONS if counts[version] == 1)
    _require(
        actual_registry == expected_registry,
        f"registry revisions do not match the {phase.value} phase",
    )
    head = effective_registry_head(versions)
    expected_head = INITIAL_HEAD if len(expected_registry) == 16 else FINAL_HEAD
    _require(head == expected_head, f"effective registry head must be {expected_head}")
    expected_total = len(expected_registry) + 1
    _require(len(versions) == expected_total, f"total ledger rows must be {expected_total}")
    return LedgerState(
        phase=phase.value,
        registry_revision_count=len(actual_registry),
        registry_revisions_present=actual_registry,
        historical_marker_count=marker_count,
        total_ledger_rows=len(versions),
        effective_registry_head=head,
    )


def _ledger_state_evidence(state: LedgerState) -> dict[str, object]:
    evidence = asdict(state)
    evidence["registry_revisions_present"] = list(state.registry_revisions_present)
    return evidence


def verify_historical_marker_preserved(
    before: HistoricalMarkerRow,
    after: HistoricalMarkerRow,
) -> None:
    _require(before == after, "historical marker row changed during migration cycle")


def verify_database_access(engine: Engine) -> None:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1")).scalar_one()
    except Exception as error:
        raise VerificationError("restored database is inaccessible") from error


def verify_migration_registration(version: str, objects_complete: bool) -> None:
    _require(objects_complete, f"migration objects are incomplete for {version}")


def verify_no_b2b_objects(objects: Mapping[str, Sequence[str]]) -> None:
    residual = {
        category: tuple(names)
        for category, names in objects.items()
        if tuple(names)
    }
    _require(not residual, "downgrade left B2B.1 objects")


def verify_postgres_test_log(log_text: str) -> None:
    lowered = log_text.lower()
    _require("skipped=" not in lowered and " skipped " not in lowered, "PostgreSQL test suite contains skips")
    _require("failed" not in lowered and "errors=" not in lowered, "PostgreSQL test suite failed")
    _require(bool(re.search(r"Ran\s+\d+\s+tests?", log_text)), "PostgreSQL test count is missing")
    _require(bool(re.search(r"^OK\s*$", log_text, re.MULTILINE)), "PostgreSQL test suite did not finish OK")


def verify_test_log(log_text: str, *, minimum: int) -> int:
    verify_postgres_test_log(log_text)
    match = re.search(r"Ran\s+(\d+)\s+tests?", log_text)
    _require(match is not None, "test count is missing")
    count = int(match.group(1))
    _require(count >= minimum, f"test suite must execute at least {minimum} tests")
    return count


def verify_semantic_plan_hash(
    plan: B2B1BackfillPlanV1,
    expected_hash: str,
) -> str:
    _require(
        re.fullmatch(r"[0-9a-f]{64}", expected_hash) is not None,
        "semantic plan hash artifact is invalid",
    )
    actual_hash = backfill_plan_sha256(plan)
    _require(actual_hash == expected_hash, "semantic backfill plan SHA-256 mismatch")
    _require(
        actual_hash == EXPECTED_SEMANTIC_PLAN_HASH,
        "semantic backfill plan differs from the approved v3 baseline",
    )
    return actual_hash


def verify_kpi(value: int) -> None:
    _require(type(value) is int and value == 2, "eligible_products must remain 2")


def verify_second_backfill(result: Mapping[str, object]) -> None:
    nonzero = [field for field in BACKFILL_CREATED_FIELDS if result.get(field) != 0]
    _require(not nonzero, "second backfill run is not idempotent: " + ", ".join(nonzero))


def verify_completion_evidence(evidence: Mapping[str, object]) -> None:
    _require(evidence.get("initial_head") == INITIAL_HEAD, "initial head evidence differs")
    _require(evidence.get("upgrade_head") == FINAL_HEAD, "upgrade head evidence differs")
    _require(evidence.get("downgrade_head") == INITIAL_HEAD, "downgrade head evidence differs")
    _require(evidence.get("reupgrade_head") == FINAL_HEAD, "re-upgrade head evidence differs")
    _require(evidence.get("postgres_skips") == 0, "PostgreSQL test suite contains skips")
    verify_kpi(evidence.get("kpi_before"))  # type: ignore[arg-type]
    verify_kpi(evidence.get("kpi_after"))  # type: ignore[arg-type]
    _require(evidence.get("residual_objects") == 0, "residual database objects remain")
    _require(evidence.get("residual_containers") == 0, "residual Task 13 containers remain")
    second = evidence.get("second_backfill")
    _require(isinstance(second, Mapping), "second backfill evidence is missing")
    verify_second_backfill(second)


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def _ledger_versions(connection: Connection) -> tuple[str, ...]:
    return tuple(connection.execute(text("SELECT version FROM schema_migrations")).scalars())


def _marker_row(connection: Connection) -> HistoricalMarkerRow:
    rows = tuple(connection.execute(text(
        """
        SELECT version, to_char(applied_at, 'YYYY-MM-DD"T"HH24:MI:SS.US')
        FROM schema_migrations
        WHERE version = :version
        """
    ), {"version": HISTORICAL_MARKER}))
    _require(len(rows) == 1, "historical marker must appear exactly once")
    return HistoricalMarkerRow(str(rows[0][0]), str(rows[0][1]))


def _database_ledger_state(engine: Engine, phase: LedgerPhase) -> tuple[LedgerState, HistoricalMarkerRow]:
    with engine.connect() as connection:
        return validate_ledger(_ledger_versions(connection), phase), _marker_row(connection)


def _b2b_objects(connection: Connection) -> dict[str, tuple[str, ...]]:
    tables = tuple(connection.execute(text(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = current_schema() AND table_name = ANY(:names)
        ORDER BY table_name
        """
    ), {"names": list(_B2B_TABLES)}).scalars())
    functions = tuple(connection.execute(text(
        """
        SELECT DISTINCT routine_name
        FROM information_schema.routines
        WHERE routine_schema = current_schema() AND routine_name = ANY(:names)
        ORDER BY routine_name
        """
    ), {"names": list(_B2B_FUNCTIONS)}).scalars())
    triggers = tuple(connection.execute(text(
        """
        SELECT trigger_row.tgname
        FROM pg_trigger AS trigger_row
        JOIN pg_class AS table_row ON table_row.oid = trigger_row.tgrelid
        JOIN pg_namespace AS namespace ON namespace.oid = table_row.relnamespace
        WHERE namespace.nspname = current_schema()
          AND NOT trigger_row.tgisinternal
          AND trigger_row.tgname = ANY(:names)
        ORDER BY trigger_row.tgname
        """
    ), {"names": list(_B2B_TRIGGERS)}).scalars())
    indexes = tuple(connection.execute(text(
        """
        SELECT indexname
        FROM pg_indexes
        WHERE schemaname = current_schema() AND tablename = ANY(:tables)
        ORDER BY indexname
        """
    ), {"tables": list(_B2B_TABLES)}).scalars())
    return {"tables": tables, "functions": functions, "triggers": triggers, "indexes": indexes}


def _capture_invariants(engine: Engine) -> B2B1InvariantSnapshotV1:
    with engine.connect() as connection:
        return capture_b2b1_invariants(connection)


def _immutable_material(snapshot: B2B1InvariantSnapshotV1) -> dict[str, object]:
    return snapshot.model_dump(
        mode="json",
        exclude={"captured_at", "migration_versions"},
    )


def _immutable_sha256(snapshot: B2B1InvariantSnapshotV1) -> str:
    return hashlib.sha256(_json_bytes(_immutable_material(snapshot))).hexdigest()


def _verify_immutable(reference: B2B1InvariantSnapshotV1, candidate: B2B1InvariantSnapshotV1) -> None:
    _require(
        _immutable_material(reference) == _immutable_material(candidate),
        "protected B1/B2A invariants changed",
    )
    verify_kpi(candidate.eligible_products)


def _assert_all_b2b_schemas(engine: Engine, versions: Sequence[str]) -> None:
    for version in versions:
        module = import_module(_MIGRATION_MODULES[version])
        with engine.connect() as connection:
            module.assert_schema(connection)


def _apply_revision(engine: Engine, version: str) -> dict[str, tuple[str, ...]]:
    module = import_module(_MIGRATION_MODULES[version])
    module.upgrade(engine)
    with engine.connect() as connection:
        module.assert_schema(connection)
        objects = _b2b_objects(connection)
    verify_migration_registration(version, True)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO schema_migrations (version) VALUES (:version)"),
            {"version": version},
        )
    return objects


def _remove_revision(engine: Engine, version: str) -> None:
    module = import_module(_MIGRATION_MODULES[version])
    module.downgrade(engine)
    with engine.begin() as connection:
        deleted = connection.execute(
            text("DELETE FROM schema_migrations WHERE version = :version"),
            {"version": version},
        ).rowcount
    _require(deleted == 1, f"downgrade ledger removal failed for {version}")


def _restore_proof(
    engine: Engine,
    state: LedgerState,
    marker: HistoricalMarkerRow,
    snapshot: B2B1InvariantSnapshotV1,
) -> str:
    with engine.connect() as connection:
        server_version = connection.execute(text("SHOW server_version")).scalar_one()
        database_name = connection.execute(text("SELECT current_database()" )).scalar_one()
        period_relation = connection.execute(
            text("SELECT to_regclass('public.academic_periods')::text")
        ).scalar_one()
        counts = {
            table_name: connection.execute(
                text(f'SELECT COUNT(*) FROM "{table_name}"')
            ).scalar_one()
            for table_name in REQUIRED_TOC_TABLES
        }
        constraints = connection.execute(text(
            """
            SELECT contype, COUNT(*)
            FROM pg_constraint AS constraint_row
            JOIN pg_namespace AS namespace ON namespace.oid = constraint_row.connamespace
            WHERE namespace.nspname = current_schema()
            GROUP BY contype ORDER BY contype
            """
        )).all()
        objects = _b2b_objects(connection)
    verify_no_b2b_objects(objects)
    verify_kpi(snapshot.eligible_products)
    lines = [
        "STATUS=PASS",
        f"postgresql_version={server_version}",
        f"database={database_name}",
        f"effective_registry_head={state.effective_registry_head}",
        f"registry_revision_count={state.registry_revision_count}",
        f"historical_marker={marker.version}",
        f"historical_marker_count={state.historical_marker_count}",
        f"total_ledger_rows={state.total_ledger_rows}",
        f"academic_periods_relation={period_relation}",
        "periods_mapping=The conceptual periods requirement corresponds to public.academic_periods.",
        f"eligible_products={snapshot.eligible_products}",
        f"protected_invariants_sha256={_immutable_sha256(snapshot)}",
        "b2b_objects_before_upgrade=0",
    ]
    lines.extend(f"row_count.{name}={counts[name]}" for name in REQUIRED_TOC_TABLES)
    lines.extend(f"constraint_count.{kind}={count}" for kind, count in constraints)
    return "\n".join(lines) + "\n"


def _run_migration_cycle(
    engine: Engine,
    report_dir: Path,
    backup_proof: BackupProof,
) -> dict[str, object]:
    initial_state, marker_before = _database_ledger_state(engine, LedgerPhase.INITIAL)
    initial_snapshot = _capture_invariants(engine)
    verify_kpi(initial_snapshot.eligible_products)
    with engine.connect() as connection:
        verify_no_b2b_objects(_b2b_objects(connection))
        _require(
            connection.execute(text("SELECT to_regclass('public.academic_periods') IS NOT NULL")).scalar_one(),
            "public.academic_periods is missing from restored database",
        )

    snapshot_path = report_dir / "pre-b2b1-invariants.json"
    snapshot_hash_path = report_dir / "pre-b2b1-invariants.json.sha256"
    if snapshot_path.is_file():
        try:
            source_snapshot = B2B1InvariantSnapshotV1.model_validate_json(
                snapshot_path.read_bytes()
            )
        except Exception as error:
            raise VerificationError("source invariant snapshot is invalid") from error
        _verify_immutable(source_snapshot, initial_snapshot)
        _require(
            Counter(source_snapshot.migration_versions)
            == Counter(initial_snapshot.migration_versions),
            "restored migration ledger differs from source snapshot",
        )
        _require(snapshot_hash_path.is_file(), "source invariant SHA-256 artifact is required")
        _require(
            snapshot_hash_path.read_text(encoding="ascii").strip().lower()
            == _file_sha256(snapshot_path),
            "source invariant SHA-256 mismatch",
        )
        reference_snapshot = source_snapshot
    else:
        snapshot_bytes = initial_snapshot.model_dump_json(indent=2).encode("utf-8") + b"\n"
        snapshot_path.write_bytes(snapshot_bytes)
        _write_text(
            snapshot_hash_path,
            hashlib.sha256(snapshot_bytes).hexdigest() + "\n",
        )
        reference_snapshot = initial_snapshot
    _write_text(
        report_dir / "restore-proof.txt",
        _restore_proof(engine, initial_state, marker_before, initial_snapshot),
    )

    objects_after_each: dict[str, object] = {}
    for version in B2B_VERSIONS:
        objects_after_each[version] = _apply_revision(engine, version)
    upgraded_state, upgraded_marker = _database_ledger_state(engine, LedgerPhase.UPGRADED)
    verify_historical_marker_preserved(marker_before, upgraded_marker)
    _assert_all_b2b_schemas(engine, B2B_VERSIONS)
    upgraded_snapshot = _capture_invariants(engine)
    _verify_immutable(reference_snapshot, upgraded_snapshot)

    for version in reversed(B2B_VERSIONS):
        _remove_revision(engine, version)
    downgraded_state, downgraded_marker = _database_ledger_state(engine, LedgerPhase.DOWNGRADED)
    verify_historical_marker_preserved(marker_before, downgraded_marker)
    with engine.connect() as connection:
        downgraded_objects = _b2b_objects(connection)
    verify_no_b2b_objects(downgraded_objects)
    downgraded_snapshot = _capture_invariants(engine)
    _verify_immutable(reference_snapshot, downgraded_snapshot)

    for version in B2B_VERSIONS:
        _apply_revision(engine, version)
    reupgraded_state, reupgraded_marker = _database_ledger_state(engine, LedgerPhase.REUPGRADED)
    verify_historical_marker_preserved(marker_before, reupgraded_marker)
    _assert_all_b2b_schemas(engine, B2B_VERSIONS)
    reupgraded_snapshot = _capture_invariants(engine)
    _verify_immutable(reference_snapshot, reupgraded_snapshot)
    runner_second = run_migrations(engine)
    _require(runner_second == [], "idempotent migration runner applied revisions")

    result: dict[str, object] = {
        "status": "PASS",
        "backup_sha256": backup_proof.sha256,
        "periods_mapping": (
            "El requisito conceptual periods corresponde en el esquema real del proyecto "
            "a public.academic_periods."
        ),
        "historical_marker": asdict(marker_before),
        "initial": _ledger_state_evidence(initial_state),
        "upgrade": _ledger_state_evidence(upgraded_state),
        "downgrade": _ledger_state_evidence(downgraded_state),
        "reupgrade": _ledger_state_evidence(reupgraded_state),
        "objects_created_after_each_revision": objects_after_each,
        "objects_after_downgrade": downgraded_objects,
        "runner_second_execution": runner_second,
        "immutable_sha256": {
            "source": _immutable_sha256(reference_snapshot),
            "initial_restore": _immutable_sha256(initial_snapshot),
            "upgrade": _immutable_sha256(upgraded_snapshot),
            "downgrade": _immutable_sha256(downgraded_snapshot),
            "reupgrade": _immutable_sha256(reupgraded_snapshot),
        },
        "eligible_products": {
            "initial": initial_snapshot.eligible_products,
            "upgrade": upgraded_snapshot.eligible_products,
            "downgrade": downgraded_snapshot.eligible_products,
            "reupgrade": reupgraded_snapshot.eligible_products,
        },
    }
    _write_json(report_dir / "migration-upgrade-downgrade-reupgrade.json", result)
    return result


def _load_json(path: Path, label: str) -> dict[str, Any]:
    _require(path.is_file(), f"{label} artifact is required")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise VerificationError(f"{label} artifact is invalid") from error
    _require(isinstance(value, dict), f"{label} artifact must be an object")
    return value


def _validate_existing_cycle(
    engine: Engine,
    report_dir: Path,
    backup_proof: BackupProof,
) -> dict[str, Any]:
    cycle = _load_json(
        report_dir / "migration-upgrade-downgrade-reupgrade.json",
        "migration cycle",
    )
    _require(cycle.get("status") == "PASS", "migration cycle did not pass")
    _require(cycle.get("backup_sha256") == backup_proof.sha256, "migration cycle backup hash differs")
    current_state, current_marker = _database_ledger_state(engine, LedgerPhase.REUPGRADED)
    _require(
        cycle.get("reupgrade") == _ledger_state_evidence(current_state),
        "current ledger differs from re-upgrade evidence",
    )
    marker_data = cycle.get("historical_marker")
    _require(isinstance(marker_data, dict), "historical marker evidence is missing")
    original_marker = HistoricalMarkerRow(
        version=str(marker_data.get("version")),
        applied_at=str(marker_data.get("applied_at")),
    )
    verify_historical_marker_preserved(original_marker, current_marker)
    _assert_all_b2b_schemas(engine, B2B_VERSIONS)
    return cycle


def _verify_backfill_artifacts(report_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    dry_run_path = report_dir / "restored-backfill-dry-run.json"
    _require(dry_run_path.is_file(), "backfill dry-run artifact is required")
    try:
        plan = B2B1BackfillPlanV1.model_validate_json(dry_run_path.read_bytes())
    except Exception as error:
        raise VerificationError("backfill dry-run artifact is invalid") from error
    _require(not plan.hard_blockers, "backfill dry-run contains hard blockers")
    dry_hash_path = report_dir / "restored-backfill-dry-run.sha256"
    _require(dry_hash_path.is_file(), "backfill dry-run SHA-256 artifact is required")
    expected_hash = dry_hash_path.read_text(encoding="ascii").strip().lower()
    verify_semantic_plan_hash(plan, expected_hash)
    file_hash_path = report_dir / "restored-backfill-dry-run.file.sha256"
    _require(file_hash_path.is_file(), "backfill dry-run file SHA-256 artifact is required")
    _require(
        _file_sha256(dry_run_path)
        == file_hash_path.read_text(encoding="ascii").strip().lower(),
        "backfill dry-run file SHA-256 mismatch",
    )
    _require(len(plan.candidates) == 79, "backfill candidate count differs")
    _require(plan.union_stable_target_count == 72, "stable target count differs")
    _require(len(plan.deferred_records) == 19, "deferred record count differs")
    _require(len(plan.excluded_records) == 2, "excluded record count differs")
    _require(len(plan.hard_blockers) == 0, "backfill dry-run contains hard blockers")
    _require(
        sum(candidate.case_type.value == "project_director_relation" for candidate in plan.candidates) == 5,
        "self-evidence case count differs",
    )
    id400 = next(
        (
            candidate
            for candidate in plan.candidates
            if candidate.source_table.value == "scientific_productions"
            and candidate.source_id == 400
        ),
        None,
    )
    _require(id400 is not None, "scientific_productions.id=400 is missing from the plan")
    _require(
        len(id400.stable_target.row_or_block_id) == 248,
        "scientific_productions.id=400 locator length differs",
    )

    apply_data = _load_json(report_dir / "restored-backfill-apply.json", "backfill apply")
    second_data = _load_json(
        report_dir / "restored-backfill-second-run.json",
        "second backfill run",
    )
    try:
        first = B2B1BackfillApplyResultV1.model_validate(apply_data)
        second = B2B1BackfillApplyResultV1.model_validate(second_data)
    except Exception as error:
        raise VerificationError("backfill apply artifact is invalid") from error
    _require(first.plan_sha256 == expected_hash, "backfill apply plan hash differs")
    _require(second.plan_sha256 == expected_hash, "second backfill plan hash differs")
    _require(first.created_review_items == 79, "first backfill review item count differs")
    _require(first.created_audit_events == 79, "first backfill audit count differs")
    _require(
        all(
            getattr(first, field) == 0
            for field in BACKFILL_CREATED_FIELDS
            if field not in ("created_review_items", "created_audit_events")
        ),
        "first backfill created unauthorized projections",
    )
    verify_second_backfill(second_data)
    verify_kpi(first.eligible_products_before)
    verify_kpi(first.eligible_products_after)
    verify_kpi(second.eligible_products_before)
    verify_kpi(second.eligible_products_after)
    return apply_data, second_data


def _verify_final_artifacts(report_dir: Path) -> None:
    _verify_backfill_artifacts(report_dir)
    for filename in (
        "plan-semantic-comparison.json",
        "concurrency-result.json",
        "rollback-result.json",
        "audit-verification.json",
        "privilege-verification.json",
        "invariant-comparison.json",
        "kpi-comparison.json",
    ):
        evidence = _load_json(report_dir / filename, filename)
        _require(evidence.get("status") == "PASS", f"{filename} did not pass")
    immutable = _load_json(report_dir / "immutable-comparison.json", "immutable comparison")
    _require(immutable.get("status") == "PASS", "immutable comparison did not pass")
    hashes = immutable.get("protected_sha256")
    _require(isinstance(hashes, dict) and len(set(hashes.values())) == 1, "protected invariants changed")
    kpi = _load_json(report_dir / "kpi-comparison.json", "KPI comparison")
    verify_kpi(kpi.get("before"))  # type: ignore[arg-type]
    verify_kpi(kpi.get("after_first_apply"))  # type: ignore[arg-type]
    verify_kpi(kpi.get("after_second_apply"))  # type: ignore[arg-type]
    _require(kpi.get("status") == "PASS", "KPI comparison did not pass")

    focused = report_dir / "focused-tests.txt"
    related = report_dir / "related-regressions.txt"
    task13a = report_dir / "task13a-tests.txt"
    verifier_tests = report_dir / "verifier-tests.txt"
    postgres = report_dir / "postgres-tests.txt"
    compileall = (report_dir / "compileall.txt")
    _require(focused.is_file(), "focused test log is required")
    _require(related.is_file(), "related regression log is required")
    _require(task13a.is_file(), "Task 13A test log is required")
    _require(verifier_tests.is_file(), "verifier test log is required")
    _require(postgres.is_file(), "PostgreSQL test log is required")
    _require(compileall.is_file(), "compileall log is required")
    verify_test_log(focused.read_text(encoding="utf-8-sig"), minimum=68)
    verify_test_log(related.read_text(encoding="utf-8-sig"), minimum=244)
    verify_test_log(task13a.read_text(encoding="utf-8-sig"), minimum=32)
    verify_test_log(verifier_tests.read_text(encoding="utf-8-sig"), minimum=29)
    verify_test_log(postgres.read_text(encoding="utf-8-sig"), minimum=1)
    compile_text = compileall.read_text(encoding="utf-8-sig")
    _require("COMPILEALL_EXIT_CODE=0" in compile_text, "compileall did not finish successfully")
    _require("*** Error compiling" not in compile_text, "compileall contains errors")


def run_verification(args: argparse.Namespace) -> None:
    report_dir = Path(args.report_dir).resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    backup_proof = verify_backup_artifacts(
        args.backup,
        args.backup_sha256,
        args.backup_toc,
    )
    _require(args.expected_head == FINAL_HEAD, f"expected head must be {FINAL_HEAD}")
    database_url = os.environ.get(args.database_url_env, "").strip()
    _require(bool(database_url), f"environment variable {args.database_url_env!r} is required")

    engine = create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"application_name": "b2b1_disposable_migration_test"},
    )
    try:
        verify_database_access(engine)
        cycle_path = report_dir / "migration-upgrade-downgrade-reupgrade.json"
        if cycle_path.is_file():
            _validate_existing_cycle(engine, report_dir, backup_proof)
        else:
            _run_migration_cycle(engine, report_dir, backup_proof)
        _verify_final_artifacts(report_dir)
    finally:
        engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify the complete disposable B2B.1 restore and migration cycle."
    )
    parser.add_argument("--database-url-env", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--backup")
    parser.add_argument("--backup-sha256")
    parser.add_argument("--backup-toc")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        run_verification(args)
    except (VerificationError, SQLAlchemyError, OSError) as error:
        parser.error(str(error))
    print("STATUS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
