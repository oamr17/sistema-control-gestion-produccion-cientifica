import asyncio
import logging
import unicodedata
from datetime import datetime, timezone
from io import BytesIO
from pathlib import PurePosixPath
import re
import traceback
from time import perf_counter

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import load_only
from sqlalchemy.orm import Session

from app.api.dependencies import require_roles
from app.core.config import settings
from app.core.database import SessionLocal, get_db
from app.core.security import decode_access_token
from app.services.evidence_service import sanitize_pdf_filename
from app.models.entities import (
    Career,
    ExternalResearcher,
    ImportBatch,
    ImportJob,
    ImportNormalizationAudit,
    ImportedOcrTrace,
    ImportedProgressReport,
    ImportReviewItem,
    PersonRole,
    ResearchEntity,
    ScientificProduction,
    Teacher,
    User,
)
from app.models.enums import UserRole
from app.schemas.imports import (
    DropboxProgressPdfBatchRequest,
    DropboxProgressPdfItem,
    ImportBatchAcceptedRead,
    ImportBatchDiagnosticsRead,
    ImportBatchRead,
    ImportJobDiagnosticsRead,
    ImportJobRead,
    ImportStatusRead,
    ImportResult,
    ImportedProgressReportRead,
    OcrTraceRead,
    OcrTraceReviewUpdate,
    ProgressJsonImportRequest,
    sanitize_public_import_payload,
)
from app.services.import_service import (
    ImportService,
    _production_counts_from_products,
)
from app.services.import_reconciliation import build_product_reconciliation_rows, build_research_entities
from app.services.import_progress_records import progress_projects_count
from app.services.import_batching import dropbox_document_key, dropbox_fingerprint, status_counts
from app.services.pdf_parser import parse_progress_report
from app.services.pdf_parser.participants import build_participant_summary, enrich_participant_summary
from app.services.validated_read_service import ValidatedReadService
from app.services.pdf_parser.section_detector import detect_sections

router = APIRouter()
logger = logging.getLogger(__name__)
_import_pdf_semaphore: asyncio.Semaphore | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _elapsed_ms(start: datetime | None, end: datetime | None = None) -> int | None:
    start_utc = _as_aware_utc(start)
    end_utc = _as_aware_utc(end or _utcnow())
    if not start_utc or not end_utc:
        return None
    return max(0, int((end_utc - start_utc).total_seconds() * 1000))


def _error_message(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    message = str(exc).strip()
    return message or type(exc).__name__


def _classify_import_error(exc: Exception, current_step: str | None) -> str:
    message = _error_message(exc).lower()
    step = (current_step or "").lower()
    system_markers = (
        "offset-naive",
        "offset-aware",
        "column",
        "does not exist",
        "relation",
        "undefinedcolumn",
        "programmingerror",
        "attributeerror",
        "typeerror",
        "nameerror",
    )
    if isinstance(exc, SQLAlchemyError) or "persist" in step:
        return "persistence_error"
    if isinstance(exc, HTTPException) and exc.status_code in {400, 422}:
        return "validation_error"
    if "dropbox" in message or "download" in step or "timeout" in message or "network" in message:
        return "download_error"
    if "parser" in step or "parsing" in step or "classifying" in step:
        return "parser_error"
    if any(marker in message for marker in system_markers):
        return "system_error"
    return "system_error"


def _is_retryable_import_error(error_type: str, exc: Exception) -> bool:
    if error_type != "download_error":
        return False
    if isinstance(exc, HTTPException) and exc.status_code in {400, 401, 403, 404, 422}:
        return False
    return True


def _compact_traceback(exc: Exception, limit: int = 4000) -> str:
    trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return trace[-limit:]


def _get_import_pdf_semaphore() -> asyncio.Semaphore:
    global _import_pdf_semaphore
    if _import_pdf_semaphore is None:
        _import_pdf_semaphore = asyncio.Semaphore(max(1, settings.import_pdf_max_concurrency))
    return _import_pdf_semaphore


def _process_downloaded_pdf_content(job_id: int, item_data: dict, actor: str | None, content: bytes) -> None:
    db = SessionLocal()
    try:
        item = DropboxProgressPdfItem(**item_data)
        service = ImportService(db)
        job = db.get(ImportJob, job_id)
        if not job:
            return
        filename = item.name or PurePosixPath(item.path_lower).name
        source_path = item.path_display or item.path_lower
        # The import implementation is async for UploadFile compatibility, but this path is CPU/DB-bound.
        asyncio.run(
            service._import_progress_pdf_content(
                content=content,
                imported_by=actor,
                source_path=source_path,
                filename=filename,
                dropbox_metadata=service._dropbox_metadata(item, filename, source_path),
                existing_job=job,
            )
        )
    finally:
        db.close()


async def _process_queued_dropbox_pdf(job_id: int, item_data: dict, actor: str | None) -> None:
    semaphore = _get_import_pdf_semaphore()
    async with semaphore:
        db = SessionLocal()
        try:
            item = DropboxProgressPdfItem(**item_data)
            service = ImportService(db)
            job = db.get(ImportJob, job_id)
            if not job or job.status != "QUEUED":
                return

            filename = item.name or PurePosixPath(item.path_lower).name
            source_path = item.path_display or item.path_lower
            max_retries = job.max_retries if job.max_retries is not None else settings.import_pdf_max_retries
            attempt = 0
            while attempt <= max_retries:
                try:
                    started_at = _utcnow()
                    job.status = "PROCESSING"
                    job.current_step = "downloading"
                    job.started_at = started_at
                    job.queue_ms = _elapsed_ms(job.created_at, started_at)
                    job.error_reason = None
                    job.error_type = None
                    job.error_message = None
                    job.error_traceback = None
                    db.commit()
                    download_started = perf_counter()
                    content = await service._download_dropbox_file(item)
                    job = db.get(ImportJob, job_id)
                    if job:
                        job.download_ms = int((perf_counter() - download_started) * 1000)
                        job.current_step = "queued_for_cpu_parse"
                        db.commit()
                    db.close()
                    await asyncio.to_thread(_process_downloaded_pdf_content, job_id, item.model_dump(), actor, content)
                    return
                except Exception as exc:
                    if not db.is_active:
                        db = SessionLocal()
                    else:
                        db.rollback()
                    job = db.get(ImportJob, job_id)
                    if not job:
                        return
                    error_type = _classify_import_error(exc, job.current_step)
                    message = _error_message(exc)
                    retryable = _is_retryable_import_error(error_type, exc)
                    job.retry_count = attempt + 1
                    job.error_type = error_type
                    job.error_message = message
                    job.error_reason = message
                    job.error_traceback = _compact_traceback(exc)
                    if attempt >= max_retries or not retryable:
                        job.status = "ERROR"
                        job.current_step = "failed"
                        job.summary = f"Error en procesamiento asincrono ({error_type}): {message}"
                        job.finished_at = _utcnow()
                        job.duration_ms = _elapsed_ms(job.started_at, job.finished_at)
                        job.processed_at = _utcnow()
                        db.commit()
                        return
                    job.status = "QUEUED"
                    job.summary = f"Reintento {attempt + 1}/{max_retries} programado ({error_type}): {message}"
                    db.commit()
                    await asyncio.sleep(min(2 ** attempt, 10))
                    attempt += 1
        finally:
            db.close()


async def _process_dropbox_batch(batch_id: int, queued_items: list[dict], actor: str | None) -> None:
    db = SessionLocal()
    try:
        batch = db.get(ImportBatch, batch_id)
        if batch:
            batch.status = "PROCESSING"
            db.commit()
    finally:
        db.close()

    await asyncio.gather(
        *[
            _process_queued_dropbox_pdf(item["job_id"], item["dropbox_item"], actor)
            for item in queued_items
        ],
        return_exceptions=True,
    )

    db = SessionLocal()
    try:
        batch = db.get(ImportBatch, batch_id)
        if not batch:
            return
        jobs = db.query(ImportJob).filter(ImportJob.batch_id == batch_id).all()
        counts = status_counts(jobs)
        if counts["queued"] or counts["processing"]:
            batch.status = "PROCESSING"
        elif counts["failed"]:
            batch.status = "COMPLETED_WITH_ERRORS"
            batch.completed_at = datetime.utcnow()
        else:
            batch.status = "COMPLETED"
            batch.completed_at = datetime.utcnow()
        pre_skipped = max(0, batch.total_files - len(jobs))
        batch.summary = (
            f"Total: {batch.total_files}. Procesados: {counts['processed']}. "
            f"Ignorados: {counts['ignored'] + pre_skipped}. "
            f"Revision: {counts['requires_review']}. Fallidos: {counts['failed']}."
        )
        db.commit()
    finally:
        db.close()


def _normalize_key(value: object) -> str:
    text = str(value or "").strip().upper()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.split())


def _progress_dedupe_key(row: ImportedProgressReport, trace: ImportedOcrTrace | None = None) -> str:
    if trace:
        source = trace.source_path or trace.source_filename
        if source:
            return f"SOURCE:{_normalize_key(source)}"

    return f"ROW:{row.id}"


def _dedupe_progress_rows(
    rows: list[ImportedProgressReport],
    traces_by_progress_id: dict[int, ImportedOcrTrace] | None = None,
) -> list[ImportedProgressReport]:
    selected: dict[str, ImportedProgressReport] = {}
    for row in rows:
        key = _progress_dedupe_key(row, (traces_by_progress_id or {}).get(row.id))
        current = selected.get(key)
        if current is None or row.id > current.id:
            selected[key] = row
    return sorted(selected.values(), key=lambda item: item.id, reverse=True)


def _trace_lookup(db: Session, rows: list[ImportedProgressReport]) -> dict[int, ImportedOcrTrace]:
    ids = [row.id for row in rows]
    if not ids:
        return {}
    traces = (
        db.query(ImportedOcrTrace)
        .options(
            load_only(
                ImportedOcrTrace.id,
                ImportedOcrTrace.progress_report_id,
                ImportedOcrTrace.source_filename,
                ImportedOcrTrace.source_path,
            )
        )
        .filter(ImportedOcrTrace.progress_report_id.in_(ids))
        .order_by(ImportedOcrTrace.id.desc())
        .all()
    )
    lookup: dict[int, ImportedOcrTrace] = {}
    for trace in traces:
        if trace.progress_report_id and trace.progress_report_id not in lookup:
            lookup[trace.progress_report_id] = trace
    return lookup


def _research_entities_from_trace(trace: ImportedOcrTrace) -> list[dict]:
    payload = trace.parsed_payload or {}
    existing = payload.get("research_entities")
    if isinstance(existing, list) and existing:
        return existing
    return build_research_entities(
        payload,
        source_filename=trace.source_filename,
        source_path=trace.source_path,
        review_status=trace.review_status,
        confidence_score=trace.confidence_score,
    )


def _participant_reconciliation_rows(trace: ImportedOcrTrace) -> list[dict]:
    summary = _participant_summary_from_payload(trace.parsed_payload or {})
    rows: list[dict] = []
    for participant in summary["normalized_participants"]:
        person_type = participant.get("person_type")
        kpi_eligible = bool(participant.get("kpi_eligible"))
        counts_as_teacher = person_type == "docente_interno" and kpi_eligible
        rows.append(
            {
                "source_file": sanitize_pdf_filename(trace.source_filename),
                "source_path": None,
                "source_page": None,
                "person_key": participant.get("person_key"),
                "canonical_name": participant.get("canonical_name"),
                "person_type": person_type,
                "institutional_roles": participant.get("institutional_roles") or [],
                "production_roles": participant.get("production_roles") or [],
                "validation_status": participant.get("validation_status"),
                "review_bucket": participant.get("review_bucket"),
                "participant_scope": participant.get("participant_scope"),
                "institution_scope": participant.get("institution_scope"),
                "faculty_scope": participant.get("faculty_scope"),
                "detected_faculty": participant.get("detected_faculty"),
                "participant_scope_reason": participant.get("participant_scope_reason"),
                "show_in_participants": participant.get("show_in_participants"),
                "kpi_eligible": kpi_eligible,
                "counts_as_active_teacher": counts_as_teacher,
                "counts_as_internal_fca": counts_as_teacher and participant.get("participant_scope") == "internal_fca",
                "counts_as_internal_other_faculty": counts_as_teacher
                and participant.get("participant_scope") == "internal_other_faculty",
                "dashboard_counted": counts_as_teacher,
                "reason": participant.get("kpi_reason")
                or participant.get("review_reason")
                or participant.get("status_reason"),
                "evidences": participant.get("evidences") or [],
            }
        )
    return rows


def _trace_matches_reconciliation_scope(
    progress_row: ImportedProgressReport | None,
    year_label: str | None,
    cycle: int | None,
) -> bool:
    if not year_label and cycle is None:
        return True
    if progress_row is None:
        return False
    if year_label and progress_row.year_label != year_label:
        return False
    if cycle is not None and progress_row.cycle != cycle:
        return False
    return True


def _scope_exclusion_reason(
    progress_row: ImportedProgressReport | None,
    year_label: str | None,
    cycle: int | None,
    dashboard_progress_ids: set[int] | None = None,
) -> str:
    if not year_label and cycle is None:
        return ""
    if progress_row is None:
        return "La traza no esta vinculada a un registro de avance del periodo filtrado."
    filters = []
    if year_label:
        filters.append(f"periodo {year_label}")
    if cycle is not None:
        filters.append(f"ciclo {cycle}")
    expected = " y ".join(filters)
    if dashboard_progress_ids is not None and progress_row.id not in dashboard_progress_ids:
        return f"Excluido del Dashboard por deduplicacion de registros para {expected}."
    return f"Excluido del Dashboard por filtro de {expected}."


def _exclude_from_dashboard(
    rows: list[dict],
    reason: str,
    *,
    clear_teacher_flags: bool = False,
) -> None:
    for row in rows:
        row["dashboard_counted"] = False
        row["reconciliation_status"] = "excluded_by_filter"
        row["reason"] = reason
        if clear_teacher_flags:
            row["counts_as_active_teacher"] = False


def _participant_summary_from_payload(payload: dict) -> dict:
    normalized = payload.get("normalized_participants")
    audit = payload.get("participants_audit")
    has_review_buckets = (
        isinstance(normalized, list)
        and all(not isinstance(item, dict) or "kpi_eligible" in item for item in normalized)
        and all(not isinstance(item, dict) or "review_bucket" in item for item in normalized)
        and all(not isinstance(item, dict) or "show_in_participants" in item for item in normalized)
        and isinstance(audit, list)
        and all(not isinstance(item, dict) or "kpi_eligible" in item for item in audit)
        and all(not isinstance(item, dict) or "review_bucket" in item for item in audit)
        and all(not isinstance(item, dict) or "show_in_participants" in item for item in audit)
    )
    if has_review_buckets:
        return enrich_participant_summary(
            {
                "normalized_participants": normalized or [],
                "participants_audit": audit or [],
                "person_aliases": payload.get("person_aliases") or [],
                "possible_merge_review": payload.get("possible_merge_review") or [],
                "participants_summary": payload.get("participants_summary") or {},
            }
        )
    return build_participant_summary(payload)


def _with_trace_pdf(audit_rows: list[dict], trace: ImportedOcrTrace) -> list[dict]:
    rows: list[dict] = []
    for row in audit_rows:
        item = dict(row)
        item["pdf"] = sanitize_pdf_filename(trace.source_filename)
        item["source_file"] = sanitize_pdf_filename(trace.source_filename)
        item["source_path"] = None
        item["import_job_id"] = trace.import_job_id
        item["trace_id"] = trace.id
        rows.append(item)
    return rows


def _empty_pending_counts() -> dict[str, int]:
    return {
        "pending_people_count": 0,
        "pending_author_classification_count": 0,
        "pending_product_count": 0,
        "pending_entity_count": 0,
        "pending_ocr_count": 0,
        "pending_merge_count": 0,
        "invalid_text_fragments_count": 0,
        "duplicate_evidence_count": 0,
    }


def _add_pending_counts(target: dict[str, int], summary: dict) -> None:
    for key in _empty_pending_counts():
        target[key] = target.get(key, 0) + int(summary.get(key) or 0)


def _person_tokens(value: object) -> set[str]:
    particles = {"DE", "DEL", "LA", "LAS", "LOS", "Y"}
    return {token for token in _normalize_key(value).split() if token not in particles and len(token) > 1}


def _token_match_score(left: set[str], right: set[str]) -> float:
    if len(left) < 2 or len(right) < 2:
        return 0.0
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    matches = sum(1 for token in shorter if token in longer)
    return matches / len(shorter)


def _possible_person_matches(name: object, candidates: list[dict]) -> list[dict]:
    tokens = _person_tokens(name)
    matches: list[dict] = []
    for candidate in candidates:
        candidate_tokens = _person_tokens(candidate.get("canonical_name"))
        score = _token_match_score(tokens, candidate_tokens)
        if score >= 0.5:
            matches.append(
                {
                    "canonical_name": candidate.get("canonical_name"),
                    "person_type": candidate.get("person_type"),
                    "match_confidence": round(score, 2),
                    "match_reason": "Coincidencia parcial de tokens normalizados; requiere revision manual.",
                }
            )
    return sorted(matches, key=lambda item: item["match_confidence"], reverse=True)[:5]


def _serialize_progress_row(
    row: ImportedProgressReport,
    trace: ImportedOcrTrace | None,
    read_service: ValidatedReadService,
) -> dict[str, object]:
    return read_service.progress_view(row, trace)


def get_current_user_optional(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None

    from app.core.security import decode_access_token

    payload = decode_access_token(authorization.replace("Bearer ", "", 1))
    if not payload:
        return None

    return db.query(User).filter(User.email == payload["sub"], User.is_active.is_(True)).first()


def raise_permission_error() -> None:
    from fastapi import HTTPException, status

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Se requiere un administrador de facultad o una API key valida para importar.",
    )


def get_import_actor(
    x_api_key: str | None = Header(default=None),
    user: User | None = Depends(get_current_user_optional),
) -> str | None:
    if x_api_key and settings.ingest_api_key and x_api_key == settings.ingest_api_key:
        return "n8n"
    if user and user.role == UserRole.FACULTY_ADMIN:
        return user.email
    raise_permission_error()


def get_file_access_user(
    token: str | None = None,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    resolved_token = token
    if not resolved_token and authorization and authorization.startswith("Bearer "):
        resolved_token = authorization.replace("Bearer ", "", 1)
    if not resolved_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token requerido")

    payload = decode_access_token(resolved_token)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token invalido")

    user = db.query(User).filter(User.email == payload["sub"], User.is_active.is_(True)).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario inactivo")
    if user.role not in (UserRole.FACULTY_ADMIN, UserRole.CAREER_MANAGER):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Rol insuficiente")
    return user


@router.get("/jobs", response_model=list[ImportJobRead])
def list_import_jobs(
    batch_id: int | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.FACULTY_ADMIN)),
) -> list[ImportJobRead]:
    query = db.query(ImportJob).order_by(ImportJob.created_at.desc())
    if batch_id:
        query = query.filter(ImportJob.batch_id == batch_id)
    return query.all()


@router.get("/batches", response_model=list[ImportBatchRead])
def list_import_batches(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.FACULTY_ADMIN)),
) -> list[ImportBatch]:
    return (
        db.query(ImportBatch)
        .filter(ImportBatch.source_type == "PROGRESS_PDF")
        .order_by(ImportBatch.created_at.desc())
        .limit(50)
        .all()
    )


@router.get("/batches/{batch_id}/jobs", response_model=list[ImportJobRead])
def list_import_batch_jobs(
    batch_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.FACULTY_ADMIN)),
) -> list[ImportJob]:
    return (
        db.query(ImportJob)
        .filter(ImportJob.source_type == "PROGRESS_PDF", ImportJob.batch_id == batch_id)
        .order_by(ImportJob.created_at.desc())
        .all()
    )


@router.get("/batches/{batch_id}/diagnostics", response_model=ImportBatchDiagnosticsRead)
def import_batch_diagnostics(
    batch_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.FACULTY_ADMIN)),
) -> ImportBatchDiagnosticsRead:
    jobs = (
        db.query(ImportJob)
        .filter(ImportJob.source_type == "PROGRESS_PDF", ImportJob.batch_id == batch_id)
        .order_by(ImportJob.id.asc())
        .all()
    )
    traces = (
        db.query(ImportedOcrTrace)
        .filter(ImportedOcrTrace.import_job_id.in_([job.id for job in jobs]))
        .order_by(ImportedOcrTrace.id.desc())
        .all()
        if jobs
        else []
    )
    trace_by_job: dict[int, ImportedOcrTrace] = {}
    for trace in traces:
        trace_by_job.setdefault(trace.import_job_id, trace)

    diagnostics: list[ImportJobDiagnosticsRead] = []
    for job in jobs:
        trace = trace_by_job.get(job.id)
        payload = trace.parsed_payload if trace and trace.parsed_payload else {}
        extraction_counts = {
            "internos": len(payload.get("integrantes_internos") or []),
            "externos": len(payload.get("integrantes_externos") or payload.get("investigadores_externos") or []),
            "proyectos": len(payload.get("proyectos_fci") or []),
            "produccion": len(payload.get("produccion_cientifica") or []) + len(payload.get("intercambios") or []),
        }
        persisted_counts = payload.get("persisted_counts") or {
            "teachers": 0,
            "external_researchers": extraction_counts["externos"] if trace and trace.progress_report_id else 0,
            "projects": 0,
            "productions": 0,
        }
        diagnostics.append(
            ImportJobDiagnosticsRead(
                id=job.id,
                filename=job.filename,
                status=job.status,
                queue_time=job.queue_ms,
                queue_ms=job.queue_ms,
                duration=job.duration_ms,
                duration_ms=job.duration_ms,
                retry_count=job.retry_count,
                current_step=job.current_step,
                error=job.error_message or job.error_reason or job.summary,
                error_type=job.error_type,
                error_message=job.error_message or job.error_reason,
                traceback=job.error_traceback,
                used_ocr=job.used_ocr,
                page_count=job.page_count,
                extraction_method=job.extraction_method,
                extraction_counts=extraction_counts,
                persisted_counts=persisted_counts,
            )
        )

    return ImportBatchDiagnosticsRead(batch_id=batch_id, total=len(jobs), jobs=diagnostics)


def _batch_progress_jobs(db: Session, batch_id: int) -> list[ImportJob]:
    return (
        db.query(ImportJob)
        .filter(
            ImportJob.source_type == "PROGRESS_PDF",
            or_(ImportJob.batch_id == batch_id, ImportJob.id == batch_id),
        )
        .order_by(ImportJob.id.asc())
        .all()
    )


def _batch_traces(db: Session, job_ids: list[int]) -> list[ImportedOcrTrace]:
    if not job_ids:
        return []
    return (
        db.query(ImportedOcrTrace)
        .filter(ImportedOcrTrace.import_job_id.in_(job_ids))
        .order_by(ImportedOcrTrace.id.asc())
        .all()
    )


@router.get("/batches/{batch_id}/participants-audit")
def batch_participants_audit(
    batch_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.FACULTY_ADMIN)),
) -> dict[str, object]:
    jobs = _batch_progress_jobs(db, batch_id)
    job_ids = [job.id for job in jobs]
    if not job_ids:
        return sanitize_public_import_payload({
            "batch_id": batch_id,
            "jobs": [],
            "items": [],
            "aliases": [],
            "possible_merge_review": [],
            "summary": {
                "participants": 0,
                "autores_pendientes_clasificacion": 0,
                "discarded_invalid": 0,
                "internal_fca_count": 0,
                "internal_other_faculty_count": 0,
                "external_count": 0,
                "pending_scope_count": 0,
                "discarded_scope_count": 0,
                "aliases": 0,
                "possible_merges": 0,
                **_empty_pending_counts(),
            },
        })

    traces = _batch_traces(db, job_ids)
    items: list[dict] = []
    aliases: list[dict] = []
    possible_merges: list[dict] = []
    participant_keys: set[str] = set()
    visible_participant_keys: set[str] = set()
    pending_people_keys: set[str] = set()
    pending_author_keys: set[str] = set()
    pending_merge_keys: set[str] = set()
    scope_keys: dict[str, set[str]] = {
        "internal_fca": set(),
        "internal_other_faculty": set(),
        "external": set(),
        "pending": set(),
        "discarded": set(),
    }
    for trace in traces:
        participant_summary = _participant_summary_from_payload(trace.parsed_payload or {})
        rows = _with_trace_pdf(participant_summary["participants_audit"], trace)
        items.extend(rows)
        for participant in participant_summary["normalized_participants"]:
            key = participant.get("person_key")
            if key:
                participant_keys.add(str(key))
            if key and participant.get("show_in_participants"):
                visible_participant_keys.add(str(key))
            if key and participant.get("review_bucket") == "pending_person":
                pending_people_keys.add(str(key))
            if key and participant.get("review_bucket") == "pending_author_classification":
                pending_author_keys.add(str(key))
            if key and participant.get("review_bucket") == "pending_merge":
                pending_merge_keys.add(str(key))
            scope = participant.get("participant_scope")
            if key and scope in scope_keys:
                scope_keys[str(scope)].add(str(key))
        for alias in participant_summary["person_aliases"]:
            aliases.append({**alias, "pdf": sanitize_pdf_filename(trace.source_filename), "trace_id": trace.id})
        for merge in participant_summary["possible_merge_review"]:
            possible_merges.append({**merge, "pdf": sanitize_pdf_filename(trace.source_filename), "trace_id": trace.id})

    return sanitize_public_import_payload({
        "batch_id": batch_id,
        "jobs": [
            {
                "id": job.id,
                "filename": job.filename,
                "status": job.status,
                "batch_id": job.batch_id,
            }
            for job in jobs
        ],
        "items": items,
        "aliases": aliases,
        "possible_merge_review": possible_merges,
        "summary": {
            "participants": len(participant_keys),
            "show_in_participants_count": len(visible_participant_keys),
            "pending_people_count": len(pending_people_keys),
            "pending_author_classification_count": len(pending_author_keys),
            "autores_pendientes_clasificacion": len(pending_author_keys),
            "discarded_invalid": sum(1 for item in items if item.get("accion_aplicada") == "discarded_invalid"),
            "internal_fca_count": len(scope_keys["internal_fca"]),
            "internal_other_faculty_count": len(scope_keys["internal_other_faculty"]),
            "external_count": len(scope_keys["external"]),
            "pending_scope_count": len(scope_keys["pending"]),
            "discarded_scope_count": sum(
                1 for item in items if item.get("participant_scope") == "discarded" or item.get("review_bucket") == "invalid_text_fragment"
            ),
            "aliases": len(aliases),
            "possible_merges": len(possible_merges),
            "pending_product_count": 0,
            "pending_entity_count": 0,
            "pending_ocr_count": sum(1 for trace in traces if trace.review_status == "PENDIENTE_REVISION"),
            "pending_merge_count": len(pending_merge_keys) + len(possible_merges),
            "invalid_text_fragments_count": sum(
                1 for item in items if item.get("review_bucket") == "invalid_text_fragment"
            ),
            "duplicate_evidence_count": sum(1 for item in items if item.get("review_bucket") == "duplicate_evidence"),
        },
    })


@router.get("/batches/{batch_id}/normalization-audit")
def import_batch_normalization_audit(
    batch_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.FACULTY_ADMIN)),
) -> dict[str, object]:
    jobs = _batch_progress_jobs(db, batch_id)
    traces = _batch_traces(db, [job.id for job in jobs])
    normalized_items: list[dict] = []
    audit_items: list[dict] = []
    aliases: list[dict] = []
    possible_merges: list[dict] = []
    for trace in traces:
        summary = _participant_summary_from_payload(trace.parsed_payload or {})
        audit_items.extend(_with_trace_pdf(summary["participants_audit"], trace))
        for participant in summary["normalized_participants"]:
            normalized_items.append(
                {
                    **participant,
                    "pdf": sanitize_pdf_filename(trace.source_filename),
                    "source_path": None,
                    "trace_id": trace.id,
                    "import_job_id": trace.import_job_id,
                }
            )
        aliases.extend(
            {**alias, "pdf": sanitize_pdf_filename(trace.source_filename), "trace_id": trace.id}
            for alias in summary["person_aliases"]
        )
        possible_merges.extend(
            {**merge, "pdf": sanitize_pdf_filename(trace.source_filename), "trace_id": trace.id}
            for merge in summary["possible_merge_review"]
        )

    person_keys = {str(item.get("person_key")) for item in normalized_items if item.get("person_key")}
    internal_keys = {
        str(item.get("person_key"))
        for item in normalized_items
        if item.get("person_key") and item.get("person_type") == "docente_interno"
    }
    external_keys = {
        str(item.get("person_key"))
        for item in normalized_items
        if item.get("person_key") and item.get("person_type") == "investigador_externo"
    }
    internal_fca_keys = {
        str(item.get("person_key"))
        for item in normalized_items
        if item.get("person_key") and item.get("participant_scope") == "internal_fca"
    }
    internal_other_faculty_keys = {
        str(item.get("person_key"))
        for item in normalized_items
        if item.get("person_key") and item.get("participant_scope") == "internal_other_faculty"
    }
    external_scope_keys = {
        str(item.get("person_key"))
        for item in normalized_items
        if item.get("person_key") and item.get("participant_scope") == "external"
    }
    pending_scope_keys = {
        str(item.get("person_key"))
        for item in normalized_items
        if item.get("person_key") and item.get("participant_scope") == "pending"
    }
    pending_author_keys = {
        str(item.get("person_key"))
        for item in normalized_items
        if item.get("person_key") and item.get("review_bucket") == "pending_author_classification"
    }
    pending_people_keys = {
        str(item.get("person_key"))
        for item in normalized_items
        if item.get("person_key") and item.get("review_bucket") == "pending_person"
    }
    pending_merge_keys = {
        str(item.get("person_key"))
        for item in normalized_items
        if item.get("person_key") and item.get("review_bucket") == "pending_merge"
    }
    visible_keys = {
        str(item.get("person_key"))
        for item in normalized_items
        if item.get("person_key") and item.get("show_in_participants")
    }
    kpi_eligible_keys = {
        str(item.get("person_key"))
        for item in normalized_items
        if item.get("person_key") and item.get("kpi_eligible")
    }
    match_candidates = [
        item
        for item in normalized_items
        if item.get("person_type") in {"docente_interno", "investigador_externo"}
        and item.get("validation_status") == "validado"
    ]
    pending_author_resolution = []
    for item in normalized_items:
        if item.get("review_bucket") != "pending_author_classification":
            continue
        authorships = item.get("authorships") if isinstance(item.get("authorships"), list) else []
        pending_author_resolution.append(
            {
                "original_text": (item.get("original_texts") or [item.get("canonical_name")])[0],
                "canonical_name": item.get("canonical_name"),
                "possible_matches": _possible_person_matches(item.get("canonical_name"), match_candidates),
                "reason_not_matched": item.get("review_reason") or item.get("status_reason"),
                "product_id": None,
                "row_or_block_id": next(
                    (
                        authorship.get("row_or_block_id")
                        for authorship in authorships
                        if isinstance(authorship, dict) and authorship.get("row_or_block_id")
                    ),
                    None,
                ),
                "product_title": next(
                    (
                        authorship.get("product_title")
                        for authorship in authorships
                        if isinstance(authorship, dict) and authorship.get("product_title")
                    ),
                    None,
                ),
                "source_file": item.get("pdf"),
                "source_section": item.get("source_section")
                or next(
                    (
                        authorship.get("source_section")
                        for authorship in authorships
                        if isinstance(authorship, dict) and authorship.get("source_section")
                    ),
                    None,
                ),
            }
        )
    return sanitize_public_import_payload({
        "batch_id": batch_id,
        "items": normalized_items,
        "audit_items": audit_items,
        "aliases": aliases,
        "possible_merge_review": possible_merges,
        "pending_author_resolution": pending_author_resolution,
        "summary": {
            "personas_unicas": len(person_keys),
            "show_in_participants_count": len(visible_keys),
            "docentes_internos": len(internal_keys),
            "investigadores_externos": len(external_keys),
            "internal_fca_count": len(internal_fca_keys),
            "internal_other_faculty_count": len(internal_other_faculty_keys),
            "external_count": len(external_scope_keys),
            "pending_scope_count": len(pending_scope_keys),
            "discarded_scope_count": sum(
                1 for item in audit_items if item.get("participant_scope") == "discarded" or item.get("review_bucket") == "invalid_text_fragment"
            ),
            "autores_pendientes": len(pending_author_keys),
            "pending_people_count": len(pending_people_keys),
            "pending_author_classification_count": len(pending_author_keys),
            "pending_product_count": 0,
            "pending_entity_count": 0,
            "pending_ocr_count": sum(1 for trace in traces if trace.review_status == "PENDIENTE_REVISION"),
            "pending_merge_count": len(pending_merge_keys) + len(possible_merges),
            "invalid_text_fragments_count": sum(
                1 for item in audit_items if item.get("review_bucket") == "invalid_text_fragment"
            ),
            "duplicate_evidence_count": sum(
                1 for item in audit_items if item.get("review_bucket") == "duplicate_evidence"
            ),
            "kpi_eligible": len(kpi_eligible_keys),
            "aliases": len(aliases),
            "possible_merges": len(possible_merges),
        },
    })


@router.get("/batches/{batch_id}/reconciliation-audit")
def batch_reconciliation_audit(
    batch_id: int,
    year_label: str | None = None,
    cycle: int | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.FACULTY_ADMIN)),
) -> dict[str, object]:
    jobs = _batch_progress_jobs(db, batch_id)
    traces = _batch_traces(db, [job.id for job in jobs])
    progress_ids = [trace.progress_report_id for trace in traces if trace.progress_report_id]
    progress_lookup = (
        {
            row.id: row
            for row in db.query(ImportedProgressReport).filter(ImportedProgressReport.id.in_(progress_ids)).all()
        }
        if progress_ids
        else {}
    )
    scope_candidates = [
        row
        for row in progress_lookup.values()
        if _trace_matches_reconciliation_scope(row, year_label, cycle)
    ]
    traces_by_progress_id = {trace.progress_report_id: trace for trace in traces if trace.progress_report_id}
    dashboard_progress_ids = {
        row.id for row in _dedupe_progress_rows(scope_candidates, traces_by_progress_id)
    }
    entities: list[dict] = []
    products: list[dict] = []
    participants: list[dict] = []
    by_pdf: list[dict] = []
    seen_product_keys: set[str] = set()

    for trace in traces:
        payload = trace.parsed_payload or {}
        progress_row = progress_lookup.get(trace.progress_report_id)
        in_filter_scope = _trace_matches_reconciliation_scope(progress_row, year_label, cycle)
        in_dashboard_scope = in_filter_scope and (
            not dashboard_progress_ids or bool(progress_row and progress_row.id in dashboard_progress_ids)
        )
        scope_reason = _scope_exclusion_reason(progress_row, year_label, cycle, dashboard_progress_ids)
        trace_entities = [
            {
                **entity,
                "pdf": sanitize_pdf_filename(trace.source_filename),
                "source_file": sanitize_pdf_filename(entity.get("source_file") or trace.source_filename),
                "source_path": None,
                "trace_id": trace.id,
                "import_job_id": trace.import_job_id,
                "progress_report_id": trace.progress_report_id,
                "year_label": progress_row.year_label if progress_row else None,
                "cycle": progress_row.cycle if progress_row else None,
                "career_name": progress_row.career_name if progress_row else None,
                "dashboard_scope": in_dashboard_scope,
            }
            for entity in _research_entities_from_trace(trace)
        ]
        if not in_dashboard_scope:
            _exclude_from_dashboard(trace_entities, scope_reason)
        trace_products = [
            {
                **product,
                "pdf": sanitize_pdf_filename(trace.source_filename),
                "source_file": sanitize_pdf_filename(product.get("source_file") or trace.source_filename),
                "source_path": None,
                "trace_id": trace.id,
                "import_job_id": trace.import_job_id,
                "progress_report_id": trace.progress_report_id,
                "year_label": progress_row.year_label if progress_row else None,
                "cycle": progress_row.cycle if progress_row else None,
                "career_name": progress_row.career_name if progress_row else None,
                "dashboard_scope": in_dashboard_scope,
            }
            for product in build_product_reconciliation_rows(
                payload,
                source_filename=trace.source_filename,
                source_path=trace.source_path,
                seen_product_keys=seen_product_keys if in_dashboard_scope else set(),
            )
        ]
        if not in_dashboard_scope:
            _exclude_from_dashboard(trace_products, scope_reason)
        trace_participants = [
            {
                **participant,
                "pdf": sanitize_pdf_filename(trace.source_filename),
                "source_path": None,
                "trace_id": trace.id,
                "import_job_id": trace.import_job_id,
                "progress_report_id": trace.progress_report_id,
                "year_label": progress_row.year_label if progress_row else None,
                "cycle": progress_row.cycle if progress_row else None,
                "career_name": progress_row.career_name if progress_row else None,
                "dashboard_scope": in_dashboard_scope,
            }
            for participant in _participant_reconciliation_rows(trace)
        ]
        if not in_dashboard_scope:
            _exclude_from_dashboard(trace_participants, scope_reason, clear_teacher_flags=True)
        entities.extend(trace_entities)
        products.extend(trace_products)
        participants.extend(trace_participants)
        by_pdf.append(
            {
                "pdf": sanitize_pdf_filename(trace.source_filename),
                "source_path": None,
                "trace_id": trace.id,
                "review_status": trace.review_status,
                "confidence_score": trace.confidence_score,
                "progress_report_id": trace.progress_report_id,
                "year_label": progress_row.year_label if progress_row else None,
                "cycle": progress_row.cycle if progress_row else None,
                "career_name": progress_row.career_name if progress_row else None,
                "dashboard_scope": in_dashboard_scope,
                "scope_reason": None if in_dashboard_scope else scope_reason,
                "entities": trace_entities,
                "products": trace_products,
                "participants": trace_participants,
                "summary": {
                    "entities_detected": len(trace_entities),
                    "entities_counted": sum(1 for item in trace_entities if item.get("dashboard_counted")),
                    "products_detected": sum(
                        1
                        for item in trace_products
                        if item.get("reconciliation_status") not in {"discarded_invalid", "duplicate_evidence"}
                    ),
                    "products_counted": sum(1 for item in trace_products if item.get("dashboard_counted")),
                    "participants_detected": len(trace_participants),
                    "active_teachers_counted": sum(
                        1 for item in trace_participants if item.get("counts_as_active_teacher")
                    ),
                    "external_researchers_counted": sum(
                        1
                        for item in trace_participants
                        if item.get("person_type") == "investigador_externo" and item.get("kpi_eligible")
                    ),
                },
            }
        )

    active_teacher_keys = {
        str(item.get("person_key"))
        for item in participants
        if item.get("person_key") and item.get("dashboard_counted") and item.get("person_type") == "docente_interno"
    }
    external_keys = {
        str(item.get("person_key"))
        for item in participants
        if item.get("person_key")
        and item.get("dashboard_scope")
        and item.get("person_type") == "investigador_externo"
        and item.get("kpi_eligible")
    }
    pending_people_keys = {
        str(item.get("person_key"))
        for item in participants
        if item.get("person_key") and item.get("dashboard_scope") and item.get("review_bucket") == "pending_person"
    }
    pending_author_keys = {
        str(item.get("person_key"))
        for item in participants
        if item.get("person_key")
        and item.get("dashboard_scope")
        and item.get("review_bucket") == "pending_author_classification"
    }
    dashboard_trace_ids = {item.get("trace_id") for item in by_pdf if item.get("dashboard_scope")}

    return sanitize_public_import_payload({
        "batch_id": batch_id,
        "jobs": [
            {
                "id": job.id,
                "filename": job.filename,
                "status": job.status,
                "batch_id": job.batch_id,
            }
            for job in jobs
        ],
        "by_pdf": by_pdf,
        "entities": entities,
        "products": products,
        "participants": participants,
        "summary": {
            "jobs": len(jobs),
            "traces": len(traces),
            "dashboard_filter": {"year_label": year_label, "cycle": cycle},
            "traces_in_dashboard_scope": sum(1 for item in by_pdf if item.get("dashboard_scope")),
            "traces_excluded_by_filter": sum(1 for item in by_pdf if not item.get("dashboard_scope")),
            "entities_detected": len(entities),
            "entities_counted": sum(1 for item in entities if item.get("dashboard_counted")),
            "entities_pending_review": sum(
                1 for item in entities if item.get("reconciliation_status") in {"pending_review", "pending_ocr"}
            ),
            "products_detected": sum(
                1
                for item in products
                if item.get("dashboard_scope")
                and item.get("reconciliation_status") not in {"discarded_invalid", "duplicate_evidence"}
            ),
            "products_counted": sum(1 for item in products if item.get("dashboard_counted")),
            "products_pending_review": sum(
                1
                for item in products
                if item.get("dashboard_scope")
                and item.get("reconciliation_status") in {"pending_review", "pending_review_text_fragment"}
            ),
            "products_discarded": sum(
                1
                for item in products
                if item.get("dashboard_scope") and item.get("reconciliation_status") == "discarded_invalid"
            ),
            "duplicate_products": sum(
                1
                for item in products
                if item.get("dashboard_scope") and item.get("reconciliation_status") == "duplicate_evidence"
            ),
            "participants_visible": sum(
                1 for item in participants if item.get("dashboard_scope") and item.get("show_in_participants")
            ),
            "active_teachers_counted": len(active_teacher_keys),
            "external_researchers_counted": len(external_keys),
            "pending_people_count": len(pending_people_keys),
            "pending_author_classification_count": len(pending_author_keys),
            "invalid_text_fragments_count": sum(
                int((_participant_summary_from_payload(trace.parsed_payload or {})["participants_summary"]).get("invalid_text_fragments_count") or 0)
                for trace in traces
                if trace.id in dashboard_trace_ids
            ),
            "pending_ocr_count": sum(
                1
                for trace in traces
                if trace.id in dashboard_trace_ids and trace.review_status == "PENDIENTE_REVISION"
            ),
        },
    })


@router.post("/batches/{batch_id}/rebuild-normalization")
def rebuild_batch_normalization(
    batch_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.FACULTY_ADMIN)),
) -> dict[str, object]:
    jobs = _batch_progress_jobs(db, batch_id)
    traces = _batch_traces(db, [job.id for job in jobs])
    updated = 0
    skipped = 0
    before_participants = 0
    after_participants = 0
    before_discarded = 0
    after_discarded = 0
    before_pending_counts = _empty_pending_counts()
    after_pending_counts = _empty_pending_counts()

    for trace in traces:
        if not trace.parsed_payload:
            skipped += 1
            continue
        payload = dict(trace.parsed_payload)
        previous_summary = _participant_summary_from_payload(payload)
        _add_pending_counts(before_pending_counts, previous_summary.get("participants_summary") or {})
        before_participants += len(previous_summary["normalized_participants"])
        before_discarded += sum(
            1 for item in previous_summary["participants_audit"] if item.get("accion_aplicada") == "discarded_invalid"
        )

        rebuilt = build_participant_summary(payload)
        _add_pending_counts(after_pending_counts, rebuilt["participants_summary"])
        payload["normalized_participants"] = rebuilt["normalized_participants"]
        payload["participants_audit"] = rebuilt["participants_audit"]
        payload["person_aliases"] = rebuilt["person_aliases"]
        payload["possible_merge_review"] = rebuilt["possible_merge_review"]
        payload["participants_summary"] = rebuilt["participants_summary"]
        payload["research_entities"] = build_research_entities(
            payload,
            source_filename=trace.source_filename,
            source_path=trace.source_path,
            review_status=trace.review_status,
            confidence_score=trace.confidence_score,
        )
        trace.parsed_payload = payload

        after_participants += len(rebuilt["normalized_participants"])
        after_discarded += sum(
            1 for item in rebuilt["participants_audit"] if item.get("accion_aplicada") == "discarded_invalid"
        )
        updated += 1

    db.commit()
    return sanitize_public_import_payload({
        "batch_id": batch_id,
        "jobs": len(jobs),
        "traces": len(traces),
        "updated": updated,
        "skipped": skipped,
        "before": {
            "participants": before_participants,
            "discarded_invalid": before_discarded,
            **before_pending_counts,
        },
        "after": {
            "participants": after_participants,
            "discarded_invalid": after_discarded,
            **after_pending_counts,
        },
        "preserved": ["import_jobs", "PDFs", "imported_ocr_traces", "historial de importacion"],
    })


@router.get("/batches/{batch_id}/extraction-by-file-audit")
def import_batch_extraction_by_file_audit(
    batch_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.FACULTY_ADMIN)),
) -> dict:
    batch = db.get(ImportBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch de importacion no encontrado.")

    jobs = (
        db.query(ImportJob)
        .filter(ImportJob.source_type == "PROGRESS_PDF", ImportJob.batch_id == batch_id)
        .order_by(ImportJob.id.asc())
        .all()
    )
    job_ids = [job.id for job in jobs]
    traces = (
        db.query(ImportedOcrTrace)
        .filter(ImportedOcrTrace.import_job_id.in_(job_ids))
        .all()
        if job_ids
        else []
    )
    audits = (
        db.query(ImportNormalizationAudit)
        .filter(ImportNormalizationAudit.import_job_id.in_(job_ids))
        .order_by(ImportNormalizationAudit.id.asc())
        .all()
        if job_ids
        else []
    )
    reviews = (
        db.query(ImportReviewItem)
        .filter(ImportReviewItem.import_job_id.in_(job_ids))
        .order_by(ImportReviewItem.id.asc())
        .all()
        if job_ids
        else []
    )
    roles = (
        db.query(PersonRole)
        .filter(PersonRole.import_job_id.in_(job_ids))
        .order_by(PersonRole.id.asc())
        .all()
        if job_ids
        else []
    )
    research_entities = (
        db.query(ResearchEntity)
        .filter(ResearchEntity.import_job_id.in_(job_ids))
        .order_by(ResearchEntity.id.asc())
        .all()
        if job_ids
        else []
    )

    trace_by_job = {trace.import_job_id: trace for trace in traces}
    audits_by_job: dict[int, list[ImportNormalizationAudit]] = {}
    reviews_by_job: dict[int, list[ImportReviewItem]] = {}
    roles_by_job: dict[int, list[PersonRole]] = {}
    for row in audits:
        audits_by_job.setdefault(row.import_job_id, []).append(row)
    for row in reviews:
        reviews_by_job.setdefault(row.import_job_id, []).append(row)
    for row in roles:
        roles_by_job.setdefault(row.import_job_id, []).append(row)

    def audit_summary(rows: list[ImportNormalizationAudit]) -> dict[str, dict[str, int]]:
        summary: dict[str, dict[str, int]] = {}
        for row in rows:
            summary.setdefault(row.entity_type, {})
            summary[row.entity_type][row.action] = summary[row.entity_type].get(row.action, 0) + 1
        return summary

    def role_summary(rows: list[PersonRole]) -> dict[str, int]:
        summary: dict[str, int] = {}
        for row in rows:
            summary[row.role_type] = summary.get(row.role_type, 0) + 1
        return summary

    def teacher_audit_items(rows: list[ImportNormalizationAudit]) -> list[dict]:
        items = []
        for row in rows:
            if row.entity_type != "teacher":
                continue
            details = row.metadata_json or {}
            items.append(
                {
                    "archivo": row.source_file,
                    "seccion": row.source_section,
                    "pagina": row.source_page,
                    "raw_name": details.get("raw_name") or row.raw_value,
                    "normalized_name": details.get("normalized_name") or row.normalized_value,
                    "raw_career": details.get("raw_career"),
                    "normalized_career": details.get("normalized_career"),
                    "action": row.action,
                    "motivo": row.reason,
                    "matched_teacher_id": details.get("matched_teacher_id") or row.normalized_record_id,
                    "dedupe_signature": details.get("dedupe_signature"),
                    "dedupe_strategy": details.get("dedupe_strategy"),
                }
            )
        return items

    def external_audit_items(rows: list[ImportNormalizationAudit]) -> list[dict]:
        items = []
        for row in rows:
            if row.entity_type != "external_researcher":
                continue
            details = row.metadata_json or {}
            items.append(
                {
                    "archivo": row.source_file,
                    "seccion": row.source_section,
                    "pagina": row.source_page,
                    "raw_name": details.get("raw_name"),
                    "normalized_name": details.get("normalized_name"),
                    "raw_institution": details.get("raw_institution"),
                    "normalized_institution": details.get("normalized_institution"),
                    "participant_type": details.get("participant_type"),
                    "action": row.action,
                    "motivo": row.reason,
                    "matched_external_researcher_id": details.get("matched_external_researcher_id") or row.normalized_record_id,
                    "dedupe_signature": details.get("dedupe_signature"),
                }
            )
        return items

    def product_audit_items(rows: list[ImportNormalizationAudit]) -> list[dict]:
        items = []
        for row in rows:
            if row.entity_type != "scientific_production":
                continue
            details = row.metadata_json or {}
            items.append(
                {
                    "archivo": row.source_file,
                    "seccion": row.source_section,
                    "pagina": row.source_page,
                    "raw_title": details.get("raw_title") or row.raw_value,
                    "normalized_title": details.get("normalized_title") or row.normalized_value,
                    "raw_status": details.get("raw_status"),
                    "normalized_status": details.get("normalized_status"),
                    "raw_impact": details.get("raw_impact"),
                    "normalized_impact": details.get("normalized_impact"),
                    "authors": details.get("authors") or [],
                    "action": row.action,
                    "motivo": row.reason,
                    "production_id": row.normalized_record_id,
                    "confidence_score": row.confidence_score,
                }
            )
        return items

    def project_audit_items(rows: list[ImportNormalizationAudit]) -> list[dict]:
        items = []
        for row in rows:
            if row.entity_type != "research_project":
                continue
            details = row.metadata_json or {}
            items.append(
                {
                    "archivo": row.source_file,
                    "seccion": row.source_section,
                    "pagina": row.source_page,
                    "section_detected": details.get("section_detected") or row.raw_value,
                    "candidates_found": details.get("candidates_found"),
                    "persisted": details.get("persisted"),
                    "requires_review": details.get("requires_review"),
                    "discarded": details.get("discarded"),
                    "action": row.action,
                    "motivo": row.reason,
                    "project_id": row.normalized_record_id,
                }
            )
        return items

    def role_items(rows: list[PersonRole]) -> list[dict]:
        return [
            {
                "id": row.id,
                "role_type": row.role_type,
                "person_type": row.person_type,
                "person_key": row.person_key,
                "raw_name": row.raw_name,
                "normalized_name": row.normalized_name,
                "teacher_id": row.teacher_id,
                "external_researcher_id": row.external_researcher_id,
                "scientific_production_id": row.scientific_production_id,
                "research_project_id": row.research_project_id,
                "research_entity_id": row.research_entity_id,
                "source_file": row.source_file,
                "source_page": row.source_page,
                "source_section": row.source_section,
                "confidence_score": row.confidence_score,
                "reason": row.reason,
                "details": row.metadata_json or {},
            }
            for row in rows
        ]

    files = []
    unique_internal_people: set[str] = set()
    unique_external_people: set[str] = set()
    unique_students_graduates: set[str] = set()
    unique_directors: set[str] = set()
    unique_product_authors: set[str] = set()
    total_internal_mentions = 0
    total_external_mentions = 0
    total_product_author_mentions = 0

    for job in jobs:
        trace = trace_by_job.get(job.id)
        payload = trace.parsed_payload if trace and trace.parsed_payload else {}
        job_audits = audits_by_job.get(job.id, [])
        job_roles = roles_by_job.get(job.id, [])
        internals = payload.get("integrantes_internos") or []
        externals = payload.get("integrantes_externos") or payload.get("investigadores_externos") or []
        products = payload.get("produccion_cientifica") or []
        projects = payload.get("proyectos_fci") or []
        sections = [
            {
                "raw_value": log.get("value"),
                "section": log.get("table") or log.get("section"),
                "score": log.get("score"),
                "requires_review": log.get("requires_review"),
            }
            for log in payload.get("logs") or []
            if isinstance(log, dict) and log.get("field") == "section"
        ]
        total_internal_mentions += len(internals)
        total_external_mentions += len(externals)
        for item in internals:
            key = _normalize_key(f"{item.get('name')}|{item.get('career')}")
            if key:
                unique_internal_people.add(key)
        for item in externals:
            key = _normalize_key(f"{item.get('name')}|{item.get('institution')}")
            if key:
                unique_external_people.add(key)
        for product in products:
            authors = product.get("authors") if isinstance(product, dict) else []
            if isinstance(authors, list):
                total_product_author_mentions += len(authors)
                for author in authors:
                    key = _normalize_key(author)
                    if key:
                        unique_product_authors.add(key)
        for role in job_roles:
            if role.role_type == "director":
                unique_directors.add(role.person_key or _normalize_key(role.normalized_name))
            if role.role_type == "autor_producto":
                unique_product_authors.add(role.person_key or _normalize_key(role.normalized_name))
            if role.person_type in {"estudiante", "graduado"}:
                unique_students_graduates.add(role.person_key or _normalize_key(role.normalized_name))

        files.append(
            {
                "job_id": job.id,
                "filename": job.filename,
                "status": job.status,
                "current_step": job.current_step,
                "document_status": (payload.get("document") or {}).get("status") if isinstance(payload.get("document"), dict) else None,
                "sections_detected": sections,
                "integrantes_internos_detected": internals,
                "integrantes_internos_audit": teacher_audit_items(job_audits),
                "director_responsable_detected": [
                    item for item in role_items(job_roles) if item["role_type"] in {"director", "responsable_informe"}
                ],
                "externos_detected": externals,
                "externos_audit": external_audit_items(job_audits),
                "estudiantes_graduados_detected": [
                    item for item in role_items(job_roles) if item["person_type"] in {"estudiante", "graduado"}
                ],
                "productos_cientificos_detected": products,
                "productos_cientificos_audit": product_audit_items(job_audits),
                "autores_por_producto": [
                    item for item in role_items(job_roles) if item["role_type"] == "autor_producto"
                ],
                "proyectos_fci_detected": projects,
                "proyectos_fci_audit": project_audit_items(job_audits),
                "normalization_actions": audit_summary(job_audits),
                "roles_summary": role_summary(job_roles),
                "review_items": [
                    {
                        "field": item.field,
                        "source_page": item.source_page,
                        "source_section": item.source_section,
                        "raw_value": item.raw_value,
                        "normalized_value": item.normalized_value,
                        "confidence_score": item.confidence_score,
                        "reason": item.reason,
                    }
                    for item in reviews_by_job.get(job.id, [])
                ],
            }
        )

    all_audits_summary = audit_summary(audits)
    all_roles_summary = role_summary(roles)
    return sanitize_public_import_payload({
        "batch_id": batch_id,
        "total_files": len(jobs),
        "totals": {
            "internal_member_mentions_detected": total_internal_mentions,
            "unique_internal_people_detected": len(unique_internal_people),
            "external_mentions_detected": total_external_mentions,
            "unique_external_people_detected": len(unique_external_people),
            "roles_detected": len(roles),
            "directors_detected": len(unique_directors),
            "product_author_mentions_detected": total_product_author_mentions,
            "unique_product_authors_detected": len(unique_product_authors),
            "students_graduates_detected": len(unique_students_graduates),
            "review_items": len(reviews),
            "discarded_items": sum(actions.get("discarded_invalid", 0) for actions in all_audits_summary.values()),
        },
        "normalization_summary": all_audits_summary,
        "roles_summary": all_roles_summary,
        "files": files,
    })


GOLDEN_BATCH_EXPECTATIONS = {
    167: {
        "min_jobs": 10,
        "min_internal_mentions": 28,
        "min_external_mentions": 12,
        "min_product_candidates": 3,
        "min_research_entities": 1,
        "forbidden_teacher_markers": ("AUTOR NO DETECTADO", "DOCENTE NO DETECTADO"),
        "forbidden_product_markers": ("EVIDENCIA", "ANEXO", "OBSERVACION", "PIRAMIDE CIENTIFICA"),
    }
}


@router.get("/batches/{batch_id}/golden-audit")
def import_batch_golden_audit(
    batch_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.FACULTY_ADMIN)),
) -> dict:
    expectation = GOLDEN_BATCH_EXPECTATIONS.get(batch_id)
    if not expectation:
        return sanitize_public_import_payload({
            "ok": False,
            "batch_id": batch_id,
            "message": "No existe fixture dorado para este lote; el endpoint no aplica reglas al parser.",
        })

    jobs = (
        db.query(ImportJob)
        .filter(ImportJob.source_type == "PROGRESS_PDF", ImportJob.batch_id == batch_id)
        .order_by(ImportJob.id.asc())
        .all()
    )
    job_ids = [job.id for job in jobs]
    traces = (
        db.query(ImportedOcrTrace)
        .filter(ImportedOcrTrace.import_job_id.in_(job_ids))
        .all()
        if job_ids
        else []
    )
    internal_mentions = 0
    external_mentions = 0
    product_candidates = 0
    research_entity_candidates = 0
    for trace in traces:
        payload = trace.parsed_payload or {}
        internal_mentions += _payload_count(payload, "integrantes_internos")
        external_mentions += _payload_count(payload, "integrantes_externos")
        product_candidates += _payload_count(payload, "produccion_cientifica")
        research_entity_candidates += _payload_count(payload, "research_entities")

    teachers = db.query(Teacher).filter(Teacher.import_batch_id == batch_id).all()
    productions = db.query(ScientificProduction).filter(ScientificProduction.import_batch_id == batch_id).all()
    research_entities = db.query(ResearchEntity).filter(ResearchEntity.import_batch_id == batch_id).all()
    forbidden_teachers = [
        {"id": item.id, "name": item.full_name}
        for item in teachers
        if any(marker in _normalize_key(item.full_name) for marker in expectation["forbidden_teacher_markers"])
    ]
    forbidden_products = [
        {"id": item.id, "title": item.title}
        for item in productions
        if any(marker in _normalize_key(item.title) for marker in expectation["forbidden_product_markers"])
    ]
    checks = [
        {
            "name": "jobs_received",
            "expected": f">= {expectation['min_jobs']}",
            "actual": len(jobs),
            "ok": len(jobs) >= expectation["min_jobs"],
        },
        {
            "name": "internal_mentions",
            "expected": f">= {expectation['min_internal_mentions']}",
            "actual": internal_mentions,
            "ok": internal_mentions >= expectation["min_internal_mentions"],
        },
        {
            "name": "external_mentions",
            "expected": f">= {expectation['min_external_mentions']}",
            "actual": external_mentions,
            "ok": external_mentions >= expectation["min_external_mentions"],
        },
        {
            "name": "product_candidates",
            "expected": f">= {expectation['min_product_candidates']}",
            "actual": product_candidates,
            "ok": product_candidates >= expectation["min_product_candidates"],
        },
        {
            "name": "research_entities",
            "expected": f">= {expectation['min_research_entities']}",
            "actual": len(research_entities) or research_entity_candidates,
            "ok": (len(research_entities) or research_entity_candidates) >= expectation["min_research_entities"],
        },
        {
            "name": "no_placeholder_teachers",
            "expected": "0",
            "actual": len(forbidden_teachers),
            "ok": not forbidden_teachers,
        },
        {
            "name": "no_evidence_as_product",
            "expected": "0",
            "actual": len(forbidden_products),
            "ok": not forbidden_products,
        },
    ]
    return sanitize_public_import_payload({
        "ok": all(item["ok"] for item in checks),
        "batch_id": batch_id,
        "fixture": "batch_167_control",
        "checks": checks,
        "counts": {
            "jobs": len(jobs),
            "internal_mentions": internal_mentions,
            "external_mentions": external_mentions,
            "product_candidates": product_candidates,
            "research_entity_candidates": research_entity_candidates,
            "persisted_teachers": len(teachers),
            "persisted_products": len(productions),
            "persisted_research_entities": len(research_entities),
        },
        "forbidden_records": {
            "teachers": forbidden_teachers,
            "products": forbidden_products,
        },
    })


def _payload_count(payload: dict, key: str) -> int:
    value = payload.get(key)
    return len(value) if isinstance(value, list) else 0


def _safe_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _section_dicts(text: str | None) -> list[dict]:
    return [
        {
            "kind": section.kind,
            "title": section.title,
            "page": section.page,
            "score": section.score,
            "requires_review": section.requires_review,
            "line_count": len(section.lines),
            "preview": section.lines[:12],
        }
        for section in detect_sections(text or "")
    ]


def _collect_field_from_lines(lines: list[str], labels: tuple[str, ...], stop_prefixes: tuple[str, ...]) -> str | None:
    for index, line in enumerate(lines):
        key = _normalize_key(line)
        if not any(label in key for label in labels):
            continue
        parts: list[str] = []
        for next_line in lines[index + 1 :]:
            next_key = _normalize_key(next_line)
            if any(next_key.startswith(stop) for stop in stop_prefixes):
                break
            if re.fullmatch(r"\d+\s+DE\s+\d+", next_key) or "FORMULARIO DE INFORME" in next_key:
                continue
            parts.append(next_line)
            if len(parts) >= 5:
                break
        value = _safe_text(" ".join(parts))
        if value:
            return value
    return None


def _general_data_from_sections(sections: list[dict], payload: dict, trace: ImportedOcrTrace | None) -> dict:
    group_lines: list[str] = []
    director_lines: list[str] = []
    for section in sections:
        if section["kind"] == "grupo":
            group_lines.extend(section.get("preview") or [])
        if section["kind"] == "director_responsable":
            director_lines.extend(section.get("preview") or [])

    all_lines = [line for section in sections for line in (section.get("preview") or [])]
    document = payload.get("document") if isinstance(payload.get("document"), dict) else {}
    code = _collect_field_from_lines(
        group_lines,
        ("CODIGO DE PROYECTO", "CODIGO DE SEMILLERO", "CODIGO"),
        ("ANO", "AÑO", "TITULO", "TIPO", "DIRECTOR", "TUTOR", "CORREO", "FECHA", "CICLO"),
    )
    title = _collect_field_from_lines(
        group_lines,
        ("TITULO DEL PROYECTO", "TITULO DEL SEMILLERO", "NOMBRE DEL GRUPO"),
        ("DIRECTOR", "TUTOR", "CORREO", "FECHA", "CICLO", "UNIDAD", "CARRERA", "FACULTADES"),
    )
    year = _collect_field_from_lines(group_lines, ("ANO DE CONVOCATORIA", "AÑO DE CONVOCATORIA"), ("TITULO", "DIRECTOR", "TUTOR"))
    director = _collect_field_from_lines(director_lines, ("DIRECTOR DEL PROYECTO", "TUTOR DEL SEMILLERO", "COORDINADOR"), ("CORREO", "FECHA", "CICLO", "UNIDAD", "CARRERA"))
    email = _collect_field_from_lines(director_lines, ("CORREO ELECTRONICO",), ("FECHA", "CICLO", "UNIDAD", "CARRERA"))
    cycle = _collect_field_from_lines(director_lines, ("CICLO ACADEMICO",), ("UNIDAD", "CARRERA", "FACULTADES"))
    academic_unit = _collect_field_from_lines(director_lines, ("UNIDAD ACADEMICA",), ("CARRERA", "FACULTADES"))
    career = _collect_field_from_lines(director_lines, ("CARRERA DEL DIRECTOR", "CARRERA DEL TUTOR", "CARRERA DEL COORDINADOR"), ("FACULTADES", "FACULTAD"))

    for section in sections:
        title_key = _normalize_key(section.get("title"))
        preview = section.get("preview") or []
        if section.get("kind") == "director_responsable":
            if not director and any(marker in title_key for marker in ("DIRECTOR DEL PROYECTO", "TUTOR DEL SEMILLERO", "COORDINADOR DEL GRUPO")) and "CARRERA" not in title_key and "FIRMA" not in title_key:
                director = next(
                    (
                        line
                        for line in preview
                        if line
                        and "@" not in line
                        and not re.search(r"\d{1,4}[-/]\d{1,2}[-/]\d{1,4}", line)
                        and "FORMULARIO" not in _normalize_key(line)
                    ),
                    None,
                )
            if not career and "CARRERA DEL" in title_key:
                career = next(
                    (
                        line
                        for line in preview
                        if line and "FORMULARIO" not in _normalize_key(line) and "HTTP" not in _normalize_key(line)
                    ),
                    None,
                )
        if not email:
            email = next((line for line in preview if "@" in line and "@local.import" not in line.lower()), None)

    return sanitize_public_import_payload({
        "codigo": code,
        "titulo": title,
        "year": year,
        "cycle": cycle,
        "unidad_academica": academic_unit,
        "career": career,
        "director_coordinador_responsable": director,
        "correo_real": email if email and "@" in email and "@local.import" not in email.lower() else None,
        "source_filename": sanitize_pdf_filename(trace.source_filename if trace else document.get("filename")),
        "source_path": None,
        "raw_document": document,
        "raw_general_preview": all_lines[:25],
    })


def _document_type(filename: str, general_data: dict, sections: list[dict], job: ImportJob) -> str:
    key = _normalize_key(" ".join([filename, _safe_text(general_data.get("codigo")), _safe_text(general_data.get("titulo"))]))
    section_kinds = {section["kind"] for section in sections}
    if job.status == "SKIPPED" or "DUPLIC" in _normalize_key(job.summary):
        return "duplicado"
    if "SEMILLERO" in key:
        return "semillero"
    if "GRUPO" in key or "GI" in key or "GRUPOS DE" in key:
        return "grupo_de_investigacion"
    if "PROYECTO" in key or "FCI" in key or "proyectos_fci" in section_kinds:
        return "proyecto"
    if job.used_ocr:
        return "informe_escaneado"
    return "otro"


def _audit_rows_by_job(rows: list[ImportNormalizationAudit]) -> dict[int, list[ImportNormalizationAudit]]:
    grouped: dict[int, list[ImportNormalizationAudit]] = {}
    for row in rows:
        grouped.setdefault(row.import_job_id, []).append(row)
    return grouped


def _review_rows_by_job(rows: list[ImportReviewItem]) -> dict[int, list[ImportReviewItem]]:
    grouped: dict[int, list[ImportReviewItem]] = {}
    for row in rows:
        grouped.setdefault(row.import_job_id, []).append(row)
    return grouped


def _role_rows_by_job(rows: list[PersonRole]) -> dict[int, list[PersonRole]]:
    grouped: dict[int, list[PersonRole]] = {}
    for row in rows:
        grouped.setdefault(row.import_job_id, []).append(row)
    return grouped


def _action_for_candidate(
    audits: list[ImportNormalizationAudit],
    entity_type: str,
    raw_value: str | None,
    normalized_value: str | None,
) -> dict:
    raw_key = _normalize_key(raw_value)
    normalized_key = _normalize_key(normalized_value)
    for row in audits:
        if row.entity_type != entity_type:
            continue
        details = row.metadata_json or {}
        candidate_values = [
            row.raw_value,
            row.normalized_value,
            details.get("raw_name"),
            details.get("normalized_name"),
            details.get("raw_title"),
            details.get("normalized_title"),
            details.get("raw_project_name"),
            details.get("normalized_project_name"),
        ]
        candidate_keys = {_normalize_key(value) for value in candidate_values if value}
        if (raw_key and raw_key in candidate_keys) or (normalized_key and normalized_key in candidate_keys):
            return {
                "action": row.action,
                "reason": row.reason,
                "confidence_score": row.confidence_score,
                "normalized_record_id": row.normalized_record_id,
            }
    return sanitize_public_import_payload({
        "action": "detected_not_persisted_yet",
        "reason": "Candidato detectado por el parser actual; reprocesar el batch para persistir/validar accion final.",
        "confidence_score": None,
        "normalized_record_id": None,
    })


def _full_internal_items(payload: dict, audits: list[ImportNormalizationAudit]) -> list[dict]:
    items = []
    for member in payload.get("integrantes_internos") or []:
        raw_name = _safe_text(member.get("name"))
        action = _action_for_candidate(audits, "teacher", raw_name, raw_name)
        items.append(
            {
                "raw_name": raw_name,
                "normalized_name": raw_name,
                "raw_faculty": member.get("faculty"),
                "normalized_faculty": member.get("faculty"),
                "raw_career": member.get("career"),
                "normalized_career": member.get("career"),
                "source_page": member.get("source_page"),
                "source_section": member.get("source_section") or "integrantes_internos",
                **action,
            }
        )
    return items


def _full_external_items(payload: dict, audits: list[ImportNormalizationAudit]) -> list[dict]:
    items = []
    for researcher in payload.get("integrantes_externos") or []:
        raw_name = _safe_text(researcher.get("name"))
        raw_institution = _safe_text(researcher.get("institution"))
        action = _action_for_candidate(audits, "external_researcher", raw_name, raw_name)
        items.append(
            {
                "raw_name": raw_name,
                "normalized_name": raw_name,
                "institution": raw_institution,
                "normalized_institution": raw_institution,
                "tipo": researcher.get("participant_type") or "investigador_externo",
                "source_page": researcher.get("source_page"),
                "source_section": researcher.get("source_section") or "integrantes_externos",
                **action,
            }
        )
    return items


def _full_product_items(payload: dict, audits: list[ImportNormalizationAudit], roles: list[PersonRole]) -> list[dict]:
    product_roles = [row for row in roles if row.role_type == "autor_producto"]
    items = []
    for product in payload.get("produccion_cientifica") or []:
        raw_title = _safe_text(product.get("title"))
        action = _action_for_candidate(audits, "scientific_production", raw_title, raw_title)
        authors = []
        for index, author in enumerate(product.get("authors") or [], start=1):
            author_key = _normalize_key(author)
            role = next((row for row in product_roles if author_key and author_key in _normalize_key(row.raw_name)), None)
            authors.append(
                {
                    "author_order": index,
                    "raw_name": author,
                    "normalized_name": role.normalized_name if role else author,
                    "author_type": role.person_type if role else "unresolved",
                    "teacher_id": role.teacher_id if role else None,
                    "external_researcher_id": role.external_researcher_id if role else None,
                    "reason": role.reason if role else "Autor detectado en bloque/fila de producto, pendiente de relacion.",
                }
            )
        items.append(
            {
                "raw_title": raw_title,
                "normalized_title": raw_title,
                "tipo": product.get("type"),
                "estado": product.get("status"),
                "impacto": product.get("impact"),
                "link_doi": product.get("link") or product.get("doi"),
                "source_page": product.get("source_page"),
                "source_section": product.get("source_section") or "produccion_cientifica",
                "authors": authors,
                "requires_review": bool(product.get("requires_review")),
                **action,
            }
        )
    return items


def _full_project_items(payload: dict, audits: list[ImportNormalizationAudit]) -> list[dict]:
    items = []
    for project in payload.get("proyectos_fci") or []:
        raw_name = _safe_text(project.get("name") or project.get("title"))
        action = _action_for_candidate(audits, "research_project", raw_name, raw_name)
        items.append(
            {
                "raw_project_name": raw_name,
                "normalized_project_name": raw_name,
                "raw_code": project.get("code"),
                "normalized_code": project.get("code"),
                "raw_status": project.get("status"),
                "normalized_status": project.get("status"),
                "raw_progress": project.get("progress"),
                "normalized_progress": project.get("progress"),
                "director": project.get("director"),
                "source_page": project.get("source_page"),
                "source_section": project.get("source_section") or "proyectos_fci",
                **action,
            }
        )
    return items


def _loss_causes(stored_payload: dict, current_payload: dict, sections: list[dict], trace: ImportedOcrTrace | None, job: ImportJob) -> list[str]:
    causes: list[str] = []
    section_kinds = {section["kind"] for section in sections}
    if "integrantes_internos" in section_kinds and _payload_count(current_payload, "integrantes_internos") == 0:
        causes.append("seccion_integrantes_detectada_sin_candidatos_parseados")
    if "produccion_cientifica" in section_kinds and _payload_count(current_payload, "produccion_cientifica") == 0:
        causes.append("seccion_produccion_detectada_sin_productos_parseados")
    if "proyectos_fci" in section_kinds and _payload_count(current_payload, "proyectos_fci") == 0:
        causes.append("seccion_proyectos_detectada_sin_filas_fci_validas")
    for key in ("integrantes_internos", "integrantes_externos", "produccion_cientifica", "proyectos_fci", "research_entities"):
        if _payload_count(current_payload, key) > _payload_count(stored_payload, key):
            causes.append(f"parser_anterior_perdia_{key}")
    if trace and len(trace.extracted_text or "") < 1000:
        causes.append("texto_extraido_insuficiente")
    if job.used_ocr:
        causes.append("ocr_aplicado_texto_ruidoso")
    if job.status in {"ERROR", "REQUIRES_REVIEW"}:
        causes.append("job_no_validado_automaticamente")
    return list(dict.fromkeys(causes))


@router.get("/batches/{batch_id}/full-extraction-audit")
def import_batch_full_extraction_audit(
    batch_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.FACULTY_ADMIN)),
) -> dict:
    batch = db.get(ImportBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch de importacion no encontrado.")

    jobs = (
        db.query(ImportJob)
        .filter(ImportJob.source_type == "PROGRESS_PDF", ImportJob.batch_id == batch_id)
        .order_by(ImportJob.id.asc())
        .all()
    )
    job_ids = [job.id for job in jobs]
    traces = (
        db.query(ImportedOcrTrace)
        .filter(ImportedOcrTrace.import_job_id.in_(job_ids))
        .order_by(ImportedOcrTrace.id.desc())
        .all()
        if job_ids
        else []
    )
    audits = (
        db.query(ImportNormalizationAudit)
        .filter(ImportNormalizationAudit.import_job_id.in_(job_ids))
        .order_by(ImportNormalizationAudit.id.asc())
        .all()
        if job_ids
        else []
    )
    reviews = (
        db.query(ImportReviewItem)
        .filter(ImportReviewItem.import_job_id.in_(job_ids))
        .order_by(ImportReviewItem.id.asc())
        .all()
        if job_ids
        else []
    )
    roles = (
        db.query(PersonRole)
        .filter(PersonRole.import_job_id.in_(job_ids))
        .order_by(PersonRole.id.asc())
        .all()
        if job_ids
        else []
    )
    research_entities = (
        db.query(ResearchEntity)
        .filter(ResearchEntity.import_job_id.in_(job_ids))
        .order_by(ResearchEntity.id.asc())
        .all()
        if job_ids
        else []
    )
    trace_by_job: dict[int, ImportedOcrTrace] = {}
    for trace in traces:
        trace_by_job.setdefault(trace.import_job_id, trace)
    audits_by_job = _audit_rows_by_job(audits)
    reviews_by_job = _review_rows_by_job(reviews)
    roles_by_job = _role_rows_by_job(roles)
    research_entities_by_job: dict[int, list[ResearchEntity]] = {}
    for entity in research_entities:
        if entity.import_job_id:
            research_entities_by_job.setdefault(entity.import_job_id, []).append(entity)

    files = []
    comparison = []
    totals = {
        "stored_internal_members": 0,
        "current_internal_members": 0,
        "stored_external_members": 0,
        "current_external_members": 0,
        "stored_products": 0,
        "current_products": 0,
        "stored_projects": 0,
        "current_projects": 0,
        "review_items": len(reviews),
    }

    for job in jobs:
        trace = trace_by_job.get(job.id)
        stored_payload = trace.parsed_payload if trace and trace.parsed_payload else {}
        current_payload = parse_progress_report(trace.extracted_text or "").to_public_dict() if trace else {}
        sections = _section_dicts(trace.extracted_text if trace else "")
        job_audits = audits_by_job.get(job.id, [])
        job_reviews = reviews_by_job.get(job.id, [])
        job_roles = roles_by_job.get(job.id, [])
        job_research_entities = research_entities_by_job.get(job.id, [])
        general_data = _general_data_from_sections(sections, current_payload, trace)
        preferred_responsible = next(
            (
                row.normalized_name
                for row in job_roles
                if row.role_type in {"director", "coordinador", "responsable_informe"} and row.normalized_name
            ),
            None,
        )
        if preferred_responsible:
            general_data["director_coordinador_responsable"] = preferred_responsible
        document_type = _document_type(job.filename, general_data, sections, job)
        text_len = len(trace.extracted_text or "") if trace else 0
        stored_counts = {
            "internos": _payload_count(stored_payload, "integrantes_internos"),
            "externos": _payload_count(stored_payload, "integrantes_externos"),
            "productos": _payload_count(stored_payload, "produccion_cientifica"),
            "proyectos": _payload_count(stored_payload, "proyectos_fci") + _payload_count(stored_payload, "research_entities"),
        }
        current_counts = {
            "internos": _payload_count(current_payload, "integrantes_internos"),
            "externos": _payload_count(current_payload, "integrantes_externos"),
            "productos": _payload_count(current_payload, "produccion_cientifica"),
            "proyectos": _payload_count(current_payload, "proyectos_fci") + _payload_count(current_payload, "research_entities"),
        }
        totals["stored_internal_members"] += stored_counts["internos"]
        totals["current_internal_members"] += current_counts["internos"]
        totals["stored_external_members"] += stored_counts["externos"]
        totals["current_external_members"] += current_counts["externos"]
        totals["stored_products"] += stored_counts["productos"]
        totals["current_products"] += current_counts["productos"]
        totals["stored_projects"] += stored_counts["proyectos"]
        totals["current_projects"] += current_counts["proyectos"]
        student_graduate_count = sum(
            1
            for item in current_payload.get("integrantes_externos") or []
            if item.get("participant_type") in {"estudiante", "graduado"}
        )
        file_review_items = [
            {
                "field": item.field,
                "source_page": item.source_page,
                "source_section": item.source_section,
                "raw_value": item.raw_value,
                "normalized_value": item.normalized_value,
                "confidence_score": item.confidence_score,
                "reason": item.reason,
            }
            for item in job_reviews
        ]
        loss_causes = _loss_causes(stored_payload, current_payload, sections, trace, job)
        comparison.append(
            {
                "archivo": job.filename,
                "internos_detectados": current_counts["internos"],
                "director_coordinador": general_data.get("director_coordinador_responsable"),
                "externos": current_counts["externos"],
                "estudiantes_graduados": student_graduate_count,
                "productos_cientificos": current_counts["productos"],
                "autores_por_producto": sum(len(item.get("authors") or []) for item in current_payload.get("produccion_cientifica") or []),
                "proyectos_grupos_semilleros": current_counts["proyectos"],
                "pendientes_revision": len(file_review_items),
                "datos_no_detectados_previamente": {
                    "internos": max(0, current_counts["internos"] - stored_counts["internos"]),
                    "externos": max(0, current_counts["externos"] - stored_counts["externos"]),
                    "productos": max(0, current_counts["productos"] - stored_counts["productos"]),
                    "proyectos": max(0, current_counts["proyectos"] - stored_counts["proyectos"]),
                },
                "causa_perdida": loss_causes,
            }
        )
        files.append(
            {
                "job_id": job.id,
                "filename": job.filename,
                "document_info": {
                    "document_type": document_type,
                    "status": job.status,
                    "detected_status": (stored_payload.get("document") or {}).get("status") if isinstance(stored_payload.get("document"), dict) else None,
                    "page_count": job.page_count,
                    "extraction_method": job.extraction_method or ("ocr" if job.used_ocr else None),
                    "used_ocr": job.used_ocr,
                    "text_length": text_len,
                    "text_sufficient": text_len >= 1000 and bool(sections),
                    "requires_ocr": not job.used_ocr and text_len < 1000,
                    "requires_review": bool(file_review_items) or (trace.review_status.startswith("PENDIENTE") if trace else True),
                    "current_step": job.current_step,
                    "error_type": job.error_type,
                    "error_message": job.error_message,
                },
                "general_data": general_data,
                "sections_detected": sections,
                "stored_payload_counts": stored_counts,
                "current_parser_counts": current_counts,
                "integrantes_internos": _full_internal_items(current_payload, job_audits),
                "directores_coordinadores_responsables": [
                    {
                        "raw_name": row.raw_name,
                        "normalized_name": row.normalized_name,
                        "rol_detectado": row.role_type,
                        "fuente": row.source_section,
                        "teacher_id": row.teacher_id,
                        "external_researcher_id": row.external_researcher_id,
                        "person_type": row.person_type,
                        "reason": row.reason,
                    }
                    for row in job_roles
                    if row.role_type in {"director", "coordinador", "responsable_informe"}
                ],
                "investigadores_externos": _full_external_items(current_payload, job_audits),
                "estudiantes_graduados": [
                    item
                    for item in _full_external_items(current_payload, job_audits)
                    if item.get("tipo") in {"estudiante", "graduado", "participante_estudiante", "participante_graduado"}
                ],
                "produccion_cientifica": _full_product_items(current_payload, job_audits, job_roles),
                "proyectos_grupos_semilleros": {
                    "detected_entities": current_payload.get("research_entities") or [],
                    "persisted_entities": [
                        {
                            "id": entity.id,
                            "type": entity.type,
                            "code": entity.code,
                            "normalized_code": entity.normalized_code,
                            "name": entity.name,
                            "director": entity.director_name,
                            "status": entity.status,
                            "validation_status": entity.validation_status,
                            "source_section": entity.source_section,
                            "reason": entity.reason,
                        }
                        for entity in job_research_entities
                    ],
                    "proyectos_fci": _full_project_items(current_payload, job_audits),
                    "reason": "Se registra como entidad detectada/revision si no cumple estructura de proyecto FCI normalizado.",
                },
                "review_items": file_review_items,
                "loss_causes": loss_causes,
            }
        )

    return sanitize_public_import_payload({
        "batch_id": batch_id,
        "total_files": len(jobs),
        "totals": totals,
        "comparison_table": comparison,
        "files": files,
    })


@router.get("/batches/{batch_id}/completeness-report")
def import_batch_completeness_report(
    batch_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.FACULTY_ADMIN)),
) -> dict:
    jobs = (
        db.query(ImportJob)
        .filter(ImportJob.source_type == "PROGRESS_PDF", ImportJob.batch_id == batch_id)
        .order_by(ImportJob.id.asc())
        .all()
    )
    traces = (
        db.query(ImportedOcrTrace)
        .filter(ImportedOcrTrace.import_job_id.in_([job.id for job in jobs]))
        .all()
        if jobs
        else []
    )
    trace_by_job = {trace.import_job_id: trace for trace in traces}
    review_items = (
        db.query(ImportReviewItem)
        .filter(ImportReviewItem.import_job_id.in_([job.id for job in jobs]))
        .order_by(ImportReviewItem.id.asc())
        .all()
        if jobs
        else []
    )
    reviews_by_job: dict[int, list[ImportReviewItem]] = {}
    for item in review_items:
        reviews_by_job.setdefault(item.import_job_id, []).append(item)
    audit_rows = (
        db.query(ImportNormalizationAudit)
        .filter(ImportNormalizationAudit.import_batch_id == batch_id)
        .all()
    )
    audit_summary: dict[str, dict[str, int]] = {}
    audit_by_job: dict[int, dict[str, dict[str, int]]] = {}
    for row in audit_rows:
        audit_summary.setdefault(row.entity_type, {})
        audit_summary[row.entity_type][row.action] = audit_summary[row.entity_type].get(row.action, 0) + 1
        if row.import_job_id:
            audit_by_job.setdefault(row.import_job_id, {}).setdefault(row.entity_type, {})
            audit_by_job[row.import_job_id][row.entity_type][row.action] = (
                audit_by_job[row.import_job_id][row.entity_type].get(row.action, 0) + 1
            )

    files: list[dict] = []
    totals = {
        "detected_teachers": 0,
        "persisted_teachers": 0,
        "merged_duplicate_teachers": 0,
        "teachers_requires_review": 0,
        "discarded_teachers": 0,
        "detected_teacher_mentions": 0,
        "persisted_teacher_mentions": 0,
        "detected_external_researchers": 0,
        "persisted_external_researchers": 0,
        "merged_duplicate_external_researchers": 0,
        "external_researchers_requires_review": 0,
        "discarded_external_researchers": 0,
        "detected_external_researcher_mentions": 0,
        "persisted_external_researcher_mentions": 0,
        "detected_products": 0,
        "persisted_products": 0,
        "merged_duplicate_products": 0,
        "products_requires_review": 0,
        "discarded_products": 0,
        "detected_product_mentions": 0,
        "persisted_product_mentions": 0,
        "discarded_product_candidates": 0,
        "detected_projects": 0,
        "persisted_projects": 0,
        "merged_duplicate_projects": 0,
        "projects_requires_review": 0,
        "discarded_projects": 0,
        "detected_project_mentions": 0,
        "persisted_project_mentions": 0,
        "review_items": len(review_items),
    }
    detected_teacher_keys: set[str] = set()
    detected_external_keys: set[str] = set()
    detected_product_keys: set[str] = set()
    detected_project_keys: set[str] = set()
    for job in jobs:
        trace = trace_by_job.get(job.id)
        payload = trace.parsed_payload if trace and trace.parsed_payload else {}
        internals = payload.get("integrantes_internos") or []
        externals = payload.get("integrantes_externos") or payload.get("investigadores_externos") or []
        raw_products = (payload.get("produccion_cientifica") or []) + (payload.get("intercambios") or [])
        products = [
            item
            for item in raw_products
            if ImportService._is_real_scientific_product_title(str(item.get("title") or ""))
        ]
        discarded_product_candidates = [
            item
            for item in raw_products
            if not ImportService._is_real_scientific_product_title(str(item.get("title") or ""))
        ]
        projects = payload.get("proyectos_fci") or []
        persisted = payload.get("persisted_counts") or {}
        file_reviews = reviews_by_job.get(job.id, [])
        detected = {
            "teachers": len(internals),
            "external_researchers": len(externals),
            "products": len(products),
            "projects": len(projects),
            "careers": sorted({str(item.get("career")) for item in internals if item.get("career")}),
            "universities": sorted({str(item.get("institution")) for item in externals if item.get("institution")}),
            "discarded_product_candidates": len(discarded_product_candidates),
        }
        persisted_counts = {
            "teachers": int(persisted.get("teachers") or 0),
            "external_researchers": int(persisted.get("external_researchers") or 0),
            "products": int(persisted.get("productions") or 0),
            "projects": int(persisted.get("projects") or 0),
        }
        differences = {
            key: detected[key] - persisted_counts[key]
            for key in ("teachers", "external_researchers", "products", "projects")
        }
        possible_losses = [
            f"{key}: {value} detectado(s) no persistido(s)"
            for key, value in differences.items()
            if value > 0
        ]
        for item in internals:
            name_key = _normalize_key(item.get("name"))
            career_key = _normalize_key(item.get("career"))
            if name_key:
                detected_teacher_keys.add(f"{name_key}|{career_key}")
        for item in externals:
            name_key = _normalize_key(item.get("name"))
            institution_key = _normalize_key(item.get("institution"))
            if name_key and institution_key:
                detected_external_keys.add(f"{name_key}|{institution_key}")
        for item in products:
            title_key = _normalize_key(item.get("title"))
            if title_key:
                detected_product_keys.add(title_key)
        for item in projects:
            project_key = _normalize_key(item.get("name") or item.get("title") or item.get("code"))
            if project_key:
                detected_project_keys.add(project_key)

        totals["detected_teacher_mentions"] += detected["teachers"]
        totals["persisted_teacher_mentions"] += persisted_counts["teachers"]
        totals["detected_external_researcher_mentions"] += detected["external_researchers"]
        totals["persisted_external_researcher_mentions"] += persisted_counts["external_researchers"]
        totals["detected_product_mentions"] += detected["products"]
        totals["persisted_product_mentions"] += persisted_counts["products"]
        totals["discarded_product_candidates"] += detected["discarded_product_candidates"]
        totals["detected_project_mentions"] += detected["projects"]
        totals["persisted_project_mentions"] += persisted_counts["projects"]
        files.append(
            {
                "job_id": job.id,
                "filename": job.filename,
                "status": job.status,
                "document_type": payload.get("document", {}).get("type") if isinstance(payload.get("document"), dict) else None,
                "current_step": job.current_step,
                "error_type": job.error_type,
                "error_message": job.error_message,
                "requires_review": bool(trace and trace.review_status in {"PENDIENTE_REVISION", "REQUIERE_REVISION_TIPO_DOCUMENTO"}),
                "review_status": trace.review_status if trace else None,
                "review_notes": trace.review_notes if trace else None,
                "extraction_method": job.extraction_method,
                "used_ocr": job.used_ocr,
                "page_count": job.page_count,
                "detected": detected,
                "persisted": persisted_counts,
                "differences": differences,
                "discarded_product_candidates": [
                    {
                        "title": item.get("title"),
                        "reason": "No parece titulo real de produccion cientifica.",
                    }
                    for item in discarded_product_candidates
                ],
                "normalization_actions": audit_by_job.get(job.id, {}),
                "review_items": [
                    {
                        "field": item.field,
                        "source_page": item.source_page,
                        "source_section": item.source_section,
                        "raw_value": item.raw_value,
                        "normalized_value": item.normalized_value,
                        "confidence_score": item.confidence_score,
                        "reason": item.reason,
                    }
                    for item in file_reviews
                ],
                "possible_losses": possible_losses,
            }
        )
    totals["detected_teachers"] = len(detected_teacher_keys)
    totals["persisted_teachers"] = audit_summary.get("teacher", {}).get("persisted", 0)
    totals["merged_duplicate_teachers"] = audit_summary.get("teacher", {}).get("merged_duplicate", 0)
    totals["teachers_requires_review"] = audit_summary.get("teacher", {}).get("requires_review", 0)
    totals["discarded_teachers"] = audit_summary.get("teacher", {}).get("discarded_invalid", 0)
    totals["detected_external_researchers"] = len(detected_external_keys)
    totals["persisted_external_researchers"] = audit_summary.get("external_researcher", {}).get("persisted", 0)
    totals["merged_duplicate_external_researchers"] = audit_summary.get("external_researcher", {}).get(
        "merged_duplicate",
        0,
    )
    totals["external_researchers_requires_review"] = audit_summary.get("external_researcher", {}).get(
        "requires_review",
        0,
    )
    totals["discarded_external_researchers"] = audit_summary.get("external_researcher", {}).get(
        "discarded_invalid",
        0,
    )
    totals["detected_products"] = len(detected_product_keys)
    totals["persisted_products"] = audit_summary.get("scientific_production", {}).get("persisted", 0)
    totals["merged_duplicate_products"] = audit_summary.get("scientific_production", {}).get("merged_duplicate", 0)
    totals["products_requires_review"] = audit_summary.get("scientific_production", {}).get("requires_review", 0)
    totals["discarded_products"] = audit_summary.get("scientific_production", {}).get("discarded_invalid", 0)
    totals["detected_projects"] = len(detected_project_keys)
    totals["persisted_projects"] = audit_summary.get("research_project", {}).get("persisted", 0)
    totals["merged_duplicate_projects"] = audit_summary.get("research_project", {}).get("merged_duplicate", 0)
    totals["projects_requires_review"] = audit_summary.get("research_project", {}).get("requires_review", 0)
    totals["discarded_projects"] = audit_summary.get("research_project", {}).get("discarded_invalid", 0)
    return sanitize_public_import_payload(
        {"batch_id": batch_id, "total_files": len(jobs), "totals": totals, "files": files}
    )


@router.get("/latest/status", response_model=ImportStatusRead)
def latest_import_status(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.FACULTY_ADMIN, UserRole.CAREER_MANAGER)),
) -> ImportStatusRead:
    latest_batch = (
        db.query(ImportBatch)
        .filter(ImportBatch.source_type == "PROGRESS_PDF")
        .order_by(ImportBatch.id.desc())
        .first()
    )
    latest_job = None
    if not latest_batch:
        latest_job = (
            db.query(ImportJob)
            .filter(ImportJob.source_type == "PROGRESS_PDF")
            .order_by(ImportJob.id.desc())
            .first()
        )
    if not latest_batch and not latest_job:
        return ImportStatusRead(
            active=False,
            batch_id=None,
            total=0,
            queued=0,
            processing=0,
            processed=0,
            failed=0,
            ignored=0,
            requires_review=0,
        )

    batch_id = latest_batch.id if latest_batch else (latest_job.batch_id or latest_job.id)
    jobs_query = db.query(ImportJob).filter(ImportJob.source_type == "PROGRESS_PDF")
    if batch_id:
        jobs_query = jobs_query.filter(ImportJob.batch_id == batch_id)
    else:
        jobs_query = jobs_query.filter(ImportJob.id == latest_job.id)
    jobs = jobs_query.order_by(ImportJob.id.asc()).all()

    trace_job_ids = [job.id for job in jobs]
    review_statuses = {"PENDIENTE_REVISION", "REQUIERE_REVISION_TIPO_DOCUMENTO"}
    review_trace_rows = (
        db.query(ImportedOcrTrace.import_job_id)
        .filter(
            ImportedOcrTrace.import_job_id.in_(trace_job_ids),
            ImportedOcrTrace.review_status.in_(review_statuses),
        )
        .all()
        if trace_job_ids
        else []
    )
    counts = status_counts(jobs, {row[0] for row in review_trace_rows})
    total = latest_batch.total_files if latest_batch else len(jobs)
    counts["ignored"] += max(0, total - len(jobs))
    return ImportStatusRead(
        active=(counts["queued"] + counts["processing"]) > 0,
        batch_id=batch_id,
        total=total,
        queued=counts["queued"],
        processing=counts["processing"],
        processed=counts["processed"],
        failed=counts["failed"],
        ignored=counts["ignored"],
        requires_review=counts["requires_review"],
    )


@router.get("/ocr-traces", response_model=list[OcrTraceRead])
def list_ocr_traces(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.FACULTY_ADMIN)),
) -> list[dict[str, object]]:
    traces = (
        db.query(ImportedOcrTrace)
        .options(
            load_only(
                ImportedOcrTrace.id,
                ImportedOcrTrace.import_job_id,
                ImportedOcrTrace.progress_report_id,
                ImportedOcrTrace.source_filename,
                ImportedOcrTrace.source_path,
                ImportedOcrTrace.ocr_provider,
                ImportedOcrTrace.confidence_score,
                ImportedOcrTrace.review_status,
                ImportedOcrTrace.reviewed_by,
                ImportedOcrTrace.review_notes,
                ImportedOcrTrace.reviewed_at,
                ImportedOcrTrace.created_at,
            )
        )
        .order_by(ImportedOcrTrace.created_at.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "id": trace.id,
            "import_job_id": trace.import_job_id,
            "progress_report_id": trace.progress_report_id,
            "source_filename": sanitize_pdf_filename(trace.source_filename),
            "source_path": None,
            "ocr_provider": trace.ocr_provider,
            "extracted_text": None,
            "parsed_payload": None,
            "confidence_score": trace.confidence_score,
            "review_status": trace.review_status,
            "reviewed_by": trace.reviewed_by,
            "review_notes": trace.review_notes,
            "reviewed_at": trace.reviewed_at,
            "created_at": trace.created_at,
        }
        for trace in traces
    ]


@router.get("/progress-records", response_model=list[ImportedProgressReportRead])
def list_imported_progress_records(
    year_label: str | None = None,
    cycle: int | None = None,
    career_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.FACULTY_ADMIN, UserRole.CAREER_MANAGER)),
) -> list[dict[str, object]]:
    read_service = ValidatedReadService(db)

    selected_career_id = user.career_id if user.role == UserRole.CAREER_MANAGER else career_id
    selected_career_name: str | None = None
    if selected_career_id:
        career = db.get(Career, selected_career_id)
        if not career:
            return []
        selected_career_name = career.name

    rows = read_service.progress_rows(year_label, cycle)
    if selected_career_name:
        selected_key = _normalize_key(selected_career_name)
        rows = [row for row in rows if _normalize_key(row.career_name) == selected_key]
    traces = _trace_lookup(db, rows)
    rows = _dedupe_progress_rows(rows, traces)
    return [_serialize_progress_row(row, traces.get(row.id), read_service) for row in rows]


async def _stream_dropbox_pdf(
    db: Session,
    *,
    filename: str | None,
    source_path: str | None,
    not_found_detail: str,
) -> StreamingResponse:
    if not source_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=not_found_detail)

    safe_filename = sanitize_pdf_filename(filename or PurePosixPath(source_path).name)
    service = ImportService(db)
    candidates = []
    for candidate in (source_path, source_path.lower()):
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    last_error: HTTPException | None = None
    content: bytes | None = None
    for candidate in candidates:
        try:
            content = await service._download_dropbox_file(
                DropboxProgressPdfItem(path_lower=candidate, name=safe_filename, path_display=source_path)
            )
            break
        except HTTPException as exc:
            last_error = exc

    if content is None:
        raise last_error or HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo descargar el archivo origen desde Dropbox.",
        )

    return StreamingResponse(
        BytesIO(content),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{safe_filename}"'},
    )


@router.get("/jobs/{job_id}/file")
async def download_import_job_file(
    job_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_file_access_user),
) -> StreamingResponse:
    job = db.get(ImportJob, job_id)
    if not job or job.source_type != "PROGRESS_PDF":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Archivo importado no encontrado.")

    trace = (
        db.query(ImportedOcrTrace)
        .filter(ImportedOcrTrace.import_job_id == job_id)
        .order_by(ImportedOcrTrace.id.desc())
        .first()
    )
    return await _stream_dropbox_pdf(
        db,
        filename=trace.source_filename if trace else job.filename,
        source_path=(trace.source_path if trace and trace.source_path else job.source_identifier),
        not_found_detail="No se encontro un archivo PDF asociado a este trabajo de importacion.",
    )


@router.get("/progress-records/{progress_id}/file")
async def download_imported_progress_file(
    progress_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_file_access_user),
) -> StreamingResponse:
    trace = (
        db.query(ImportedOcrTrace)
        .filter(ImportedOcrTrace.progress_report_id == progress_id)
        .order_by(ImportedOcrTrace.id.desc())
        .first()
    )
    if not trace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No se encontro un archivo origen asociado a esta investigacion.",
        )

    return await _stream_dropbox_pdf(
        db,
        filename=trace.source_filename,
        source_path=trace.source_path,
        not_found_detail="No se encontro un archivo origen asociado a esta investigacion.",
    )


@router.post("/ocr-traces/sync-progress")
def sync_ocr_traces_to_progress(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.FACULTY_ADMIN)),
) -> dict[str, int]:
    return ImportService(db).sync_ocr_traces_to_progress()


@router.post("/ocr-traces/backfill-external-researchers")
def backfill_external_researchers(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.FACULTY_ADMIN)),
) -> dict[str, int]:
    return ImportService(db).backfill_missing_external_researchers()


@router.delete("/progress-records/generated")
def clear_generated_progress_records(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.FACULTY_ADMIN)),
) -> dict[str, int]:
    return ImportService(db).clear_generated_progress_records()


@router.patch("/ocr-traces/{trace_id}", response_model=OcrTraceRead)
def review_ocr_trace(
    trace_id: int,
    payload: OcrTraceReviewUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.FACULTY_ADMIN)),
) -> ImportedOcrTrace:
    return ImportService(db).review_ocr_trace(trace_id, payload, user.email)


@router.post("/research-base", response_model=ImportResult)
async def import_research_base(
    file: UploadFile = File(...),
    actor: str | None = Depends(get_import_actor),
    db: Session = Depends(get_db),
) -> ImportResult:
    return await ImportService(db).import_research_base(file, actor)


@router.post("/project-participants", response_model=ImportResult)
async def import_project_participants(
    file: UploadFile = File(...),
    actor: str | None = Depends(get_import_actor),
    db: Session = Depends(get_db),
) -> ImportResult:
    return await ImportService(db).import_project_participants(file, actor)


@router.post("/progress", response_model=ImportResult)
async def import_progress(
    file: UploadFile = File(...),
    actor: str | None = Depends(get_import_actor),
    db: Session = Depends(get_db),
) -> ImportResult:
    return await ImportService(db).import_progress_report(file, actor)


@router.post("/progress-pdf", response_model=ImportResult)
async def import_progress_pdf(
    file: UploadFile = File(...),
    source_path: str | None = Form(default=None),
    actor: str | None = Depends(get_import_actor),
    db: Session = Depends(get_db),
) -> ImportResult:
    return await ImportService(db).import_progress_pdf(file, actor, source_path)


@router.post("/progress-pdf-batch", response_model=ImportBatchAcceptedRead)
async def import_progress_pdf_batch(
    payload: DropboxProgressPdfBatchRequest,
    background_tasks: BackgroundTasks,
    actor: str | None = Depends(get_import_actor),
    db: Session = Depends(get_db),
) -> ImportBatchAcceptedRead | JSONResponse:
    batch_id: int | None = None
    if not payload.files:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "ok": False,
                "error": "empty_batch",
                "detail": "No se recibieron archivos de Dropbox para importar.",
                "batch_id": None,
            },
        )

    try:
        service = ImportService(db)
        batch = ImportBatch(
            source_type="PROGRESS_PDF",
            status="QUEUED",
            total_files=len(payload.files),
            created_by=actor,
            summary=f"Lote recibido desde n8n con {len(payload.files)} archivo(s).",
        )
        db.add(batch)
        db.flush()
        batch_id = batch.id

        queued_items: list[dict] = []
        skipped = 0
        fingerprints_in_payload: set[str] = set()
        for item in payload.files:
            filename = item.name or PurePosixPath(item.path_lower).name
            source_path = item.path_display or item.path_lower
            metadata = service._dropbox_metadata(item, filename, source_path)
            fingerprint = dropbox_fingerprint(metadata)
            duplicate_in_payload = bool(fingerprint and fingerprint in fingerprints_in_payload)
            duplicate_processed = service._dropbox_revision_exists(metadata)
            fingerprints_in_payload.add(fingerprint)

            if duplicate_in_payload or duplicate_processed:
                skipped += 1
                continue

            job = ImportJob(
                batch_id=batch.id,
                source_type="PROGRESS_PDF",
                filename=filename,
                imported_by=actor,
                status="QUEUED",
                summary="Archivo recibido desde n8n; procesamiento PDF en segundo plano.",
                source_identifier=metadata.get("dropbox_id") or metadata.get("path_lower") or source_path,
                source_rev=metadata.get("rev"),
                source_fingerprint=fingerprint or None,
                document_key=dropbox_document_key(metadata) or None,
                is_current=False,
                retry_count=0,
                max_retries=settings.import_pdf_max_retries,
            )
            try:
                with db.begin_nested():
                    db.add(job)
                    db.flush()
            except IntegrityError as exc:
                constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
                if constraint_name != "uq_import_jobs_document_revision":
                    raise
                skipped += 1
                continue
            queued_items.append({"job_id": job.id, "dropbox_item": item.model_dump()})

        batch.summary = (
            f"Lote recibido desde n8n: {len(payload.files)} archivo(s), "
            f"{len(queued_items)} en cola y {skipped} omitido(s) por revision duplicada."
        )
        db.commit()
        if queued_items:
            background_tasks.add_task(_process_dropbox_batch, batch.id, queued_items, actor)
        else:
            batch.status = "COMPLETED"
            batch.completed_at = datetime.utcnow()
            db.commit()

        return ImportBatchAcceptedRead(
            ok=True,
            batch_id=batch.id,
            total=len(payload.files),
            queued=len(queued_items),
            skipped=skipped,
            message="Batch registrado correctamente",
        )
    except HTTPException as exc:
        db.rollback()
        logger.warning("Import batch rejected", extra={"batch_id": batch_id, "status_code": exc.status_code})
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "ok": False,
                "error": "import_batch_error",
                "detail": "No se pudo registrar el lote de importación.",
                "batch_id": batch_id,
            },
        )
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Import batch database failure", extra={"batch_id": batch_id})
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "ok": False,
                "error": "database_error",
                "detail": "No se pudo registrar el lote de importación.",
                "batch_id": batch_id,
            },
        )
    except Exception as exc:
        db.rollback()
        logger.exception("Unexpected import batch failure", extra={"batch_id": batch_id})
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "ok": False,
                "error": "unexpected_error",
                "detail": "No se pudo registrar el lote de importación.",
                "batch_id": batch_id,
            },
        )


@router.post("/progress-json", response_model=ImportResult)
def import_progress_json(
    payload: ProgressJsonImportRequest,
    actor: str | None = Depends(get_import_actor),
    db: Session = Depends(get_db),
) -> ImportResult:
    return ImportService(db).import_progress_json(payload, actor)


@router.post("/poa", response_model=ImportResult)
async def import_poa(
    file: UploadFile = File(...),
    actor: str | None = Depends(get_import_actor),
    db: Session = Depends(get_db),
) -> ImportResult:
    return await ImportService(db).import_poa(file, actor)
