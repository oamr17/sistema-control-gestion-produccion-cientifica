from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm.attributes import flag_modified

from app.core.database import SessionLocal
from app.models.entities import ImportJob, ImportedOcrTrace, ImportedProgressReport
from app.services.import_service import ImportService


def main() -> int:
    parser = argparse.ArgumentParser(description="Reprocesa solo jobs vigentes sin eliminar historial normalizado.")
    parser.add_argument("--batch", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {
        "batch_id": args.batch,
        "generated_at": datetime.now(UTC).isoformat(),
        "processed": [],
        "skipped": [],
    }
    with SessionLocal.begin() as db:
        service = ImportService(db)
        jobs = (
            db.query(ImportJob)
            .filter(
                ImportJob.batch_id == args.batch,
                ImportJob.source_type == "PROGRESS_PDF",
                ImportJob.status == "SUCCESS",
                ImportJob.is_current.is_(True),
            )
            .order_by(ImportJob.id.asc())
            .all()
        )
        for job in jobs:
            trace = (
                db.query(ImportedOcrTrace)
                .filter(ImportedOcrTrace.import_job_id == job.id)
                .order_by(ImportedOcrTrace.id.desc())
                .first()
            )
            if not trace or not (trace.extracted_text or "").strip():
                report["skipped"].append({"job_id": job.id, "reason": "missing_extracted_text"})
                continue
            progress = db.get(ImportedProgressReport, trace.progress_report_id) if trace.progress_report_id else None
            if progress and progress.import_job_id != job.id:
                raise RuntimeError(f"La traza {trace.id} apunta a un progress_report de otro job.")
            created_progress = False
            if not progress:
                progress = service._build_progress_record_from_pdf_text(
                    job_id=job.id,
                    filename=trace.source_filename,
                    source_path=trace.source_path,
                    extracted_text=trace.extracted_text,
                )
                if not progress:
                    report["skipped"].append({"job_id": job.id, "reason": "parser_rejected_document"})
                    continue
                db.add(progress)
                db.flush()
                trace.progress_report_id = progress.id
                created_progress = True

            parsed_payload = service._rebuild_trace_payload_from_text(trace)
            persisted_counts = service._persist_normalized_from_payload(parsed_payload, progress)
            parsed_payload["persisted_counts"] = persisted_counts
            trace.parsed_payload = parsed_payload
            flag_modified(trace, "parsed_payload")
            job.current_step = "current_normalized_reprocessed"
            report["processed"].append(
                {
                    "job_id": job.id,
                    "trace_id": trace.id,
                    "progress_report_id": progress.id,
                    "created_progress": created_progress,
                    "persisted_counts": persisted_counts,
                }
            )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
