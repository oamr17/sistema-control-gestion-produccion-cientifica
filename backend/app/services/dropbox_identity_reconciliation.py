from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import ImportJob, ImportedOcrTrace


class ReconciliationConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class IdentityPair:
    historical_job_id: int
    historical_batch_id: int
    previous_document_key: str
    current_job_id: int
    current_batch_id: int
    canonical_document_key: str
    path_lower: str
    filename: str
    source_rev: str | None
    content_hash: str | None
    size_bytes: int | None
    match_basis: str = "exact_path_lower_and_filename"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PairDiscoveryResult:
    pairs: list[IdentityPair]
    unmatched_historical: list[int]
    ambiguous_historical: list[int]


@dataclass(frozen=True)
class ReconciliationResult:
    matched_pairs: int
    modified_pairs: int
    already_reconciled_pairs: int

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def _path_lower(value: object) -> str:
    return "/" + "/".join(
        part for part in str(value or "").strip().replace("\\", "/").split("/") if part
    ).casefold()


def _trace_document(trace: ImportedOcrTrace | None) -> dict[str, Any]:
    if not trace or not isinstance(trace.parsed_payload, dict):
        return {}
    document = trace.parsed_payload.get("document")
    return document if isinstance(document, dict) else {}


def find_exact_pairs(
    db: Session,
    historical_batch_id: int,
    current_batch_id: int,
) -> PairDiscoveryResult:
    historical_jobs = (
        db.query(ImportJob)
        .filter(ImportJob.batch_id == historical_batch_id)
        .order_by(ImportJob.id.asc())
        .all()
    )
    current_jobs = (
        db.query(ImportJob)
        .filter(
            ImportJob.batch_id == current_batch_id,
            ImportJob.document_key.like("dropbox:id:%"),
        )
        .order_by(ImportJob.id.asc())
        .all()
    )
    current_ids = [job.id for job in current_jobs]
    traces = (
        db.query(ImportedOcrTrace)
        .filter(ImportedOcrTrace.import_job_id.in_(current_ids))
        .order_by(ImportedOcrTrace.id.asc())
        .all()
        if current_ids
        else []
    )
    latest_trace_by_job = {trace.import_job_id: trace for trace in traces}
    current_by_identity: dict[tuple[str, str], list[tuple[ImportJob, ImportedOcrTrace, dict[str, Any]]]] = {}
    for job in current_jobs:
        trace = latest_trace_by_job.get(job.id)
        document = _trace_document(trace)
        path = document.get("path_lower") or (trace.source_path if trace else None)
        if not path:
            continue
        identity = (_path_lower(path), job.filename)
        current_by_identity.setdefault(identity, []).append((job, trace, document))

    pairs: list[IdentityPair] = []
    unmatched: list[int] = []
    ambiguous: list[int] = []
    for historical in historical_jobs:
        historical_path = historical.source_identifier
        if not historical_path or not str(historical_path).strip().startswith(("/", "\\")):
            continue
        identity = (_path_lower(historical_path), historical.filename)
        candidates = current_by_identity.get(identity, [])
        if not candidates:
            unmatched.append(historical.id)
            continue
        if len(candidates) != 1:
            ambiguous.append(historical.id)
            continue
        current, _trace, document = candidates[0]
        size = document.get("size")
        pairs.append(
            IdentityPair(
                historical_job_id=historical.id,
                historical_batch_id=historical_batch_id,
                previous_document_key=str(historical.document_key or ""),
                current_job_id=current.id,
                current_batch_id=current_batch_id,
                canonical_document_key=str(current.document_key),
                path_lower=identity[0],
                filename=historical.filename,
                source_rev=current.source_rev,
                content_hash=str(document.get("content_hash")) if document.get("content_hash") else None,
                size_bytes=int(size) if size not in (None, "") else None,
            )
        )
    return PairDiscoveryResult(pairs, unmatched, ambiguous)


def reconcile_exact_pairs(db: Session, pairs: list[IdentityPair]) -> ReconciliationResult:
    if len({pair.historical_job_id for pair in pairs}) != len(pairs):
        raise ReconciliationConflict("Un job historico aparece en mas de un par.")
    if len({pair.current_job_id for pair in pairs}) != len(pairs):
        raise ReconciliationConflict("Un job actual aparece en mas de un par.")

    savepoint = db.begin_nested()
    try:
        ids = sorted({item for pair in pairs for item in (pair.historical_job_id, pair.current_job_id)})
        query = db.query(ImportJob).filter(ImportJob.id.in_(ids)).order_by(ImportJob.id.asc())
        if db.get_bind().dialect.name == "postgresql":
            query = query.with_for_update()
        jobs = {job.id: job for job in query.all()}

        already = 0
        pending: list[tuple[IdentityPair, ImportJob, ImportJob]] = []
        for pair in pairs:
            historical = jobs.get(pair.historical_job_id)
            current = jobs.get(pair.current_job_id)
            if not historical or not current:
                raise ReconciliationConflict(f"No existen ambos jobs para el par {pair.historical_job_id}/{pair.current_job_id}.")
            if historical.batch_id != pair.historical_batch_id or current.batch_id != pair.current_batch_id:
                raise ReconciliationConflict("El batch de un job cambio despues del dry-run.")
            if historical.filename != pair.filename or current.filename != pair.filename:
                raise ReconciliationConflict("El nombre exacto de un archivo cambio despues del dry-run.")
            if _path_lower(historical.source_identifier) != pair.path_lower:
                raise ReconciliationConflict("El path_lower historico cambio despues del dry-run.")
            if current.document_key != pair.canonical_document_key or not current.document_key.startswith("dropbox:id:"):
                raise ReconciliationConflict("La identidad canonica actual ya no coincide con el dry-run.")
            if not current.is_current:
                raise ReconciliationConflict("El job canonico dejo de ser vigente antes de reconciliar.")
            if historical.document_key == pair.canonical_document_key:
                if historical.is_current or current.supersedes_id != historical.id:
                    raise ReconciliationConflict("El par esta parcialmente reconciliado o su cadena no es consistente.")
                already += 1
                continue
            if historical.document_key != pair.previous_document_key or not historical.document_key.startswith("dropbox_path:"):
                raise ReconciliationConflict("La identidad historica ya no coincide con el dry-run aprobado.")
            if current.supersedes_id not in (None, historical.id):
                raise ReconciliationConflict(
                    f"El job {current.id} ya apunta mediante supersedes_id a {current.supersedes_id}; no se sobrescribe."
                )
            pending.append((pair, historical, current))

        for pair, historical, current in pending:
            historical.is_current = False
            db.flush([historical])
            historical.document_key = pair.canonical_document_key
            if current.supersedes_id is None:
                current.supersedes_id = historical.id
        db.flush()

        for pair in pairs:
            historical = jobs[pair.historical_job_id]
            current = jobs[pair.current_job_id]
            current_count = (
                db.query(ImportJob)
                .filter(
                    ImportJob.document_key == pair.canonical_document_key,
                    ImportJob.is_current.is_(True),
                )
                .count()
            )
            if current_count != 1 or historical.is_current or not current.is_current:
                raise ReconciliationConflict(f"Fallo la invariante de vigencia para {pair.canonical_document_key}.")
            if historical.document_key != pair.canonical_document_key or current.supersedes_id != historical.id:
                raise ReconciliationConflict(f"Fallo la cadena historica para {pair.canonical_document_key}.")

        savepoint.commit()
        return ReconciliationResult(len(pairs), len(pending), already)
    except Exception:
        savepoint.rollback()
        db.expire_all()
        raise
