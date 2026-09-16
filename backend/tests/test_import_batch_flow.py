import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.entities import ImportJob, ImportedOcrTrace, ImportedProgressReport
from app.schemas.imports import DropboxProgressPdfItem
from app.services import import_batching
from app.services.import_batching import dropbox_fingerprint, status_counts
from app.services.import_service import ImportService
from app.services.import_progress_records import progress_projects_count
from app.services.pdf_parser import classify_document
from app.services.pdf_parser.pdf_text_extractor import PageExtractionLog, PdfTextExtraction
from tests.support.sqlite import create_sqlite_compatible_schema


def make_jobs(count: int, status: str = "QUEUED") -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            id=index + 1,
            batch_id=100,
            source_type="PROGRESS_PDF",
            filename=f"informe_{index + 1}.pdf",
            status=status,
        )
        for index in range(count)
    ]


class ImportBatchFlowTest(unittest.TestCase):
    def test_batch_with_one_file_counts_total_and_queued(self):
        jobs = make_jobs(1)
        counts = status_counts(jobs)

        self.assertEqual(len(jobs), 1)
        self.assertEqual(counts["queued"], 1)

    def test_batch_with_two_files_counts_both(self):
        jobs = make_jobs(2)
        counts = status_counts(jobs)

        self.assertEqual(len(jobs), 2)
        self.assertEqual(counts["queued"], 2)

    def test_batch_with_twenty_files_counts_all_without_dropping_any(self):
        jobs = make_jobs(20)
        counts = status_counts(jobs)

        self.assertEqual(len(jobs), 20)
        self.assertEqual(counts["queued"], 20)

    def test_invalid_file_inside_batch_is_failed_file_not_failed_batch(self):
        jobs = [*make_jobs(2, "SUCCESS"), *make_jobs(1, "ERROR")]
        jobs[-1].error_reason = "Solo se permiten archivos PDF."
        counts = status_counts(jobs)

        self.assertEqual(counts["processed"], 2)
        self.assertEqual(counts["failed"], 1)
        self.assertEqual(jobs[-1].error_reason, "Solo se permiten archivos PDF.")

    def test_outlook_pdf_is_ignored(self):
        classification = classify_document(
            """
            Outlook
            Bandeja de entrada
            Desde: coordinacion@example.edu
            Para: docente@example.edu
            CC: investigacion@example.edu
            Asunto: RE: informes semestrales de proyectos
            Enviado: jueves, 26 de junio de 2026
            1 archivo adjunto
            https://outlook.cloud.microsoft/mail/id/example
            """,
            "captura_outlook.pdf",
        )

        self.assertEqual(classification.status, "ignored")
        self.assertEqual(classification.document_type, "email_capture")

    def test_pdf_requires_review_has_own_status_bucket(self):
        classification = classify_document("Documento ambiguo con poca informacion", "archivo.pdf")
        jobs = make_jobs(1, "REQUIRES_REVIEW")
        counts = status_counts(jobs)

        self.assertEqual(classification.status, "requires_review")
        self.assertEqual(counts["requires_review"], 1)

    def test_progress_records_projects_use_parsed_pdf_payload(self):
        payload = {"document": {"type": "progress_report"}}
        group_projects = [{"name": "Proyecto detectado en PDF", "director": "Director no detectado"}]

        projects_count = progress_projects_count(9, payload, group_projects)

        self.assertEqual(projects_count, 1)

    def test_duplicate_file_uses_id_and_rev_fingerprint(self):
        metadata = {"dropbox_id": "id:abc", "rev": "015abc", "path_lower": "/informes/a.pdf", "name": "a.pdf", "size": 123}

        self.assertEqual(dropbox_fingerprint(metadata), "dropbox:id:abc:015abc")

    def test_same_id_rev_and_content_hash_has_same_fingerprint(self):
        first = {"dropbox_id": "id:abc", "rev": "rev-1", "content_hash": "hash-a"}
        repeated = {"dropbox_id": "id:abc", "rev": "rev-1", "content_hash": "hash-a"}

        self.assertEqual(dropbox_fingerprint(first), dropbox_fingerprint(repeated))

    def test_new_rev_changes_fingerprint_even_when_content_hash_is_unchanged(self):
        first = {"dropbox_id": "id:abc", "rev": "rev-1", "content_hash": "hash-a"}
        revised = {"dropbox_id": "id:abc", "rev": "rev-2", "content_hash": "hash-a"}

        self.assertNotEqual(dropbox_fingerprint(first), dropbox_fingerprint(revised))

    def test_same_id_and_rev_ignores_path_changes_for_identity(self):
        first = {"dropbox_id": "id:abc", "rev": "rev-1", "path_lower": "/old/a.pdf"}
        renamed = {"dropbox_id": "id:abc", "rev": "rev-1", "path_lower": "/new/a.pdf"}

        self.assertEqual(dropbox_fingerprint(first), dropbox_fingerprint(renamed))

    def test_fallback_content_hash_is_audit_only(self):
        first = {"path_lower": "/informes/a.pdf", "name": "a.pdf", "size": 123, "content_hash": "hash-a"}
        changed = {"path_lower": "/informes/a.pdf", "name": "a.pdf", "size": 123, "content_hash": "hash-b"}

        self.assertEqual(dropbox_fingerprint(first), dropbox_fingerprint(changed))

    def test_duplicate_file_falls_back_to_path_name_and_size(self):
        metadata = {"path_lower": "/informes/a.pdf", "name": "a.pdf", "size": 123}

        self.assertEqual(dropbox_fingerprint(metadata), "dropbox_fallback:dropbox_path:/informes/a.pdf")

    def test_fallback_fingerprint_normalizes_path_case_and_separators(self):
        first = {"path_lower": "/informes/a.pdf"}
        repeated = {"path_lower": "\\INFORMES\\A.PDF"}

        self.assertEqual(dropbox_fingerprint(first), dropbox_fingerprint(repeated))

    def test_dropbox_item_preserves_content_hash(self):
        item = DropboxProgressPdfItem(
            id="id:abc",
            rev="015abc",
            path_lower="/informes/a.pdf",
            content_hash="sha256-from-dropbox",
        )

        self.assertEqual(item.model_dump()["content_hash"], "sha256-from-dropbox")

    def test_document_key_uses_dropbox_id(self):
        builder = getattr(import_batching, "dropbox_document_key", None)

        self.assertIsNotNone(builder)
        self.assertEqual(
            builder({"dropbox_id": "id:abc", "path_lower": "/informes/a.pdf"}),
            "dropbox:id:abc",
        )

    def test_document_key_falls_back_to_normalized_path(self):
        builder = getattr(import_batching, "dropbox_document_key", None)

        self.assertIsNotNone(builder)
        self.assertEqual(
            builder({"path_lower": "\\Informes\\A.PDF"}),
            "dropbox_path:/informes/a.pdf",
        )

    def test_new_dropbox_revision_is_allowed_to_create_a_progress_record(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        create_sqlite_compatible_schema(engine)
        db = sessionmaker(bind=engine)()
        try:
            job = ImportJob(
                source_type="PROGRESS_PDF",
                filename="zambrano.pdf",
                status="SUCCESS",
            )
            db.add(job)
            db.flush()
            progress = ImportedProgressReport(
                import_job_id=job.id,
                year_label="2025-2026",
                cycle=1,
                teacher_name="Fernando Zambrano Farías",
                notes="Investigacion reportada desde PDF.",
            )
            db.add(progress)
            db.flush()
            db.add(
                ImportedOcrTrace(
                    import_job_id=job.id,
                    progress_report_id=progress.id,
                    source_filename="zambrano.pdf",
                    source_path="/informes/zambrano.pdf",
                    parsed_payload={"document": {"dropbox_id": "id:zambrano", "rev": "rev-1"}},
                )
            )
            db.flush()

            service = ImportService(db)
            self.assertFalse(
                service._progress_exists_for_source(
                    "zambrano.pdf",
                    "/informes/zambrano.pdf",
                    {"dropbox_id": "id:zambrano", "rev": "rev-2"},
                )
            )
        finally:
            db.close()
            engine.dispose()

    def test_ocr_failure_with_zero_text_requires_review_instead_of_success(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        create_sqlite_compatible_schema(engine)
        db = sessionmaker(bind=engine)()
        try:
            extraction = PdfTextExtraction(
                text="",
                provider="PyMuPDF",
                issue="Tesseract OCR no esta instalado o no esta disponible en el contenedor.",
                page_logs=[
                    PageExtractionLog(
                        page=1,
                        method="OCR_FAILED",
                        chars=0,
                        requires_review=True,
                        warning="Tesseract OCR no esta instalado o no esta disponible en el contenedor.",
                    )
                ],
                page_count=1,
                used_ocr=False,
            )
            service = ImportService(db)
            with patch.object(service, "_is_pdf_content", return_value=True), patch.object(
                service,
                "_extract_text_from_pdf",
                return_value=extraction,
            ):
                result = asyncio.run(
                    service._import_progress_pdf_content(
                        content=b"%PDF-operational-test",
                        imported_by="test",
                        filename="familia-e.pdf",
                    )
                )

            job = db.get(ImportJob, result.job_id)
            trace = db.query(ImportedOcrTrace).filter_by(import_job_id=result.job_id).one()
            self.assertEqual(result.status, "REQUIRES_REVIEW")
            self.assertEqual(job.status, "REQUIRES_REVIEW")
            self.assertEqual(trace.review_status, "OMITIDO_SIN_TEXTO")
            self.assertIn("Tesseract OCR no esta instalado", trace.review_notes)
        finally:
            db.close()
            engine.dispose()

    def test_operational_error_message_ignores_non_text_detail(self):
        from app.api.v1.endpoints.imports import _error_message

        class DatabaseConflict(Exception):
            detail = []

        self.assertEqual(_error_message(DatabaseConflict("database conflict")), "database conflict")

    def test_download_failure_and_retry_are_tracked_per_file(self):
        job = make_jobs(1, "ERROR")[0]
        job.retry_count = 2
        job.max_retries = 2
        job.error_reason = "Dropbox timeout"
        counts = status_counts([job])

        self.assertEqual(counts["failed"], 1)
        self.assertEqual(job.retry_count, job.max_retries)
        self.assertEqual(job.error_reason, "Dropbox timeout")


if __name__ == "__main__":
    unittest.main()
