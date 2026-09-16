from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.entities import ImportJob


def dropbox_document_key(metadata: dict | None) -> str:
    if not metadata:
        return ""
    dropbox_id = str(metadata.get("dropbox_id") or "").strip()
    if dropbox_id:
        return f"dropbox:{dropbox_id}"

    source_path = str(
        metadata.get("path_lower")
        or metadata.get("path_display")
        or metadata.get("source_key")
        or ""
    ).strip()
    normalized_path = "/".join(part for part in source_path.replace("\\", "/").split("/") if part).casefold()
    return f"dropbox_path:/{normalized_path}" if normalized_path else ""


def promote_document_version(db: Session, job: ImportJob) -> ImportJob | None:
    if not job.document_key:
        return None

    if db.get_bind().dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:document_key, 0))"),
            {"document_key": job.document_key},
        )

    previous = (
        db.query(ImportJob)
        .filter(
            ImportJob.document_key == job.document_key,
            ImportJob.is_current.is_(True),
            ImportJob.id != job.id,
        )
        .order_by(ImportJob.id.desc())
        .with_for_update()
        .first()
    )
    if previous and previous.id > job.id:
        job.is_current = False
        job.supersedes_id = None
        return previous
    if previous:
        previous.is_current = False
        db.flush([previous])
        job.supersedes_id = previous.id
    job.is_current = True
    return previous


def dropbox_fingerprint(metadata: dict | None) -> str:
    if not metadata:
        return ""
    dropbox_id = metadata.get("dropbox_id")
    rev = metadata.get("rev")
    if dropbox_id and rev:
        return f"dropbox:{dropbox_id}:{rev}"
    document_key = dropbox_document_key(metadata)
    return f"dropbox_fallback:{document_key}" if document_key else ""


def status_counts(jobs: list[Any], trace_requires_review_ids: set[int] | None = None) -> dict[str, int]:
    review_ids = trace_requires_review_ids or set()
    return {
        "queued": sum(1 for job in jobs if job.status == "QUEUED"),
        "processing": sum(1 for job in jobs if job.status == "PROCESSING"),
        "processed": sum(1 for job in jobs if job.status == "SUCCESS"),
        "ignored": sum(1 for job in jobs if job.status == "SKIPPED"),
        "failed": sum(1 for job in jobs if job.status == "ERROR"),
        "requires_review": sum(1 for job in jobs if job.status == "REQUIRES_REVIEW" or job.id in review_ids),
    }
