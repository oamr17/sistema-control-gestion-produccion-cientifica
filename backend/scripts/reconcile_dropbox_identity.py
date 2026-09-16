from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.core.database import SessionLocal
from app.models.entities import ImportJob, ImportedOcrTrace
from app.services.dropbox_identity_reconciliation import (
    ReconciliationConflict,
    find_exact_pairs,
    reconcile_exact_pairs,
)


REFERENCE_TABLES = (
    "external_researchers",
    "import_normalization_audits",
    "import_review_items",
    "imported_ocr_traces",
    "imported_progress_reports",
    "imported_project_participants",
    "imported_research_records",
    "person_roles",
    "research_entities",
    "research_projects",
    "scientific_production_authors",
    "scientific_productions",
    "teachers",
)


def _json_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _protected_snapshot(db, job_ids: list[int]) -> dict[str, Any]:
    jobs = db.query(ImportJob).filter(ImportJob.id.in_(job_ids)).order_by(ImportJob.id).all()
    traces = db.query(ImportedOcrTrace).filter(ImportedOcrTrace.import_job_id.in_(job_ids)).order_by(ImportedOcrTrace.id).all()
    relationships: dict[str, dict[str, list[int]]] = {}
    for table in REFERENCE_TABLES:
        rows = db.execute(
            text(f"SELECT id, import_job_id FROM {table} WHERE import_job_id = ANY(:job_ids) ORDER BY id"),
            {"job_ids": job_ids},
        ).all()
        relationships[table] = {}
        for row_id, job_id in rows:
            relationships[table].setdefault(str(job_id), []).append(row_id)
    return {
        "jobs": {
            str(job.id): {
                "id": job.id,
                "batch_id": job.batch_id,
                "source_identifier": job.source_identifier,
                "source_rev": job.source_rev,
                "filename": job.filename,
                "status": job.status,
            }
            for job in jobs
        },
        "traces": {
            str(trace.id): {
                "import_job_id": trace.import_job_id,
                "progress_report_id": trace.progress_report_id,
                "source_filename": trace.source_filename,
                "source_path": trace.source_path,
                "parsed_payload_sha256": _json_hash(trace.parsed_payload),
                "extracted_text_sha256": _json_hash(trace.extracted_text),
            }
            for trace in traces
        },
        "relationships": relationships,
    }


def _verification(db, historical_ids: list[int], canonical_keys: list[str]) -> dict[str, Any]:
    duplicate_current = db.execute(
        text(
            "SELECT document_key, COUNT(*) FROM import_jobs WHERE is_current "
            "GROUP BY document_key HAVING COUNT(*) > 1"
        )
    ).all()
    duplicate_revisions = db.execute(
        text(
            "SELECT document_key, source_rev, COUNT(*) FROM import_jobs "
            "WHERE document_key IS NOT NULL AND source_rev IS NOT NULL "
            "GROUP BY document_key, source_rev HAVING COUNT(*) > 1"
        )
    ).all()
    path_current = db.execute(
        text(
            "SELECT id, document_key FROM import_jobs "
            "WHERE id = ANY(:ids) AND is_current AND document_key LIKE 'dropbox_path:%'"
        ),
        {"ids": historical_ids},
    ).all()
    current_by_key = db.execute(
        text(
            "SELECT document_key, COUNT(*) FROM import_jobs "
            "WHERE document_key = ANY(:keys) AND is_current GROUP BY document_key ORDER BY document_key"
        ),
        {"keys": canonical_keys},
    ).all()
    historical_rows = db.execute(
        text("SELECT COUNT(*) FROM import_jobs WHERE id = ANY(:ids)"),
        {"ids": historical_ids},
    ).scalar_one()
    chain_errors = db.execute(
        text(
            "SELECT current.id, current.supersedes_id, historical.id "
            "FROM import_jobs current JOIN import_jobs historical ON historical.id = current.supersedes_id "
            "WHERE current.document_key = ANY(:keys) "
            "AND (NOT current.is_current OR historical.is_current OR historical.document_key <> current.document_key)"
        ),
        {"keys": canonical_keys},
    ).all()
    result = {
        "duplicate_current": [list(row) for row in duplicate_current],
        "duplicate_document_revision": [list(row) for row in duplicate_revisions],
        "current_historical_path_jobs": [list(row) for row in path_current],
        "current_count_by_canonical_key": [list(row) for row in current_by_key],
        "historical_jobs_still_present": historical_rows,
        "supersedes_chain_errors": [list(row) for row in chain_errors],
    }
    if (
        duplicate_current
        or duplicate_revisions
        or path_current
        or historical_rows != len(historical_ids)
        or len(current_by_key) != len(set(canonical_keys))
        or any(count != 1 for _key, count in current_by_key)
        or chain_errors
    ):
        raise ReconciliationConflict(f"Fallo una invariante posterior: {result}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcilia identidades Dropbox exactas sin borrar historial.")
    parser.add_argument("--historical-batch", type=int, required=True)
    parser.add_argument("--current-batch", type=int, required=True)
    parser.add_argument("--expected-pairs", type=int, default=10)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    output = args.output or Path(
        "reports/dropbox_identity_reconciliation_20260711/"
        + ("apply_result.json" if args.apply else "command_dry_run.json")
    )
    report: dict[str, Any] = {
        "mode": "apply" if args.apply else "dry-run",
        "generated_at": datetime.now(UTC).isoformat(),
        "historical_batch_id": args.historical_batch,
        "current_batch_id": args.current_batch,
    }
    with SessionLocal.begin() as db:
        discovery = find_exact_pairs(db, args.historical_batch, args.current_batch)
        report["pairs"] = [pair.to_dict() for pair in discovery.pairs]
        report["unmatched_historical"] = discovery.unmatched_historical
        report["ambiguous_historical"] = discovery.ambiguous_historical
        if len(discovery.pairs) != args.expected_pairs or discovery.unmatched_historical or discovery.ambiguous_historical:
            raise ReconciliationConflict(
                f"Se esperaban {args.expected_pairs} pares exactos sin excepciones; resultado: {report}"
            )
        if args.apply:
            all_ids = sorted(
                {item for pair in discovery.pairs for item in (pair.historical_job_id, pair.current_job_id)}
            )
            historical_ids = [pair.historical_job_id for pair in discovery.pairs]
            canonical_keys = [pair.canonical_document_key for pair in discovery.pairs]
            before = _protected_snapshot(db, all_ids)
            result = reconcile_exact_pairs(db, discovery.pairs)
            after = _protected_snapshot(db, all_ids)
            if before != after:
                raise ReconciliationConflict("Cambio un campo protegido, una traza o una relacion por import_job_id.")
            report["result"] = result.to_dict()
            report["protected_snapshot"] = after
            report["verification"] = _verification(db, historical_ids, canonical_keys)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
