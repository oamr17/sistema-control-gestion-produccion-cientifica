from __future__ import annotations

import ast
from pathlib import Path
import unittest
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from app.schemas.imports import (
    ImportJobDiagnosticsRead,
    ImportJobRead,
    OcrTraceRead,
    sanitize_public_import_payload,
)
from app.services.evidence_service import EvidenceService, settings


class _FakeMinio:
    def __init__(self) -> None:
        self.bucket_checks: list[str] = []
        self.created_buckets: list[str] = []
        self.uploads: list[dict[str, object]] = []

    def bucket_exists(self, bucket: str) -> bool:
        self.bucket_checks.append(bucket)
        return True

    def make_bucket(self, bucket: str) -> None:
        self.created_buckets.append(bucket)

    def put_object(self, bucket: str, object_name: str, data, **kwargs) -> None:
        self.uploads.append({
            "bucket": bucket,
            "object_name": object_name,
            "content": data.read(),
            **kwargs,
        })


def _upload(filename: str, mime: str, content: bytes) -> UploadFile:
    return UploadFile(
        file=BytesIO(content),
        filename=filename,
        size=len(content),
        headers=Headers({"content-type": mime}),
    )


class EvidenceUploadBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _FakeMinio()
        self.minio_patch = patch(
            "app.services.evidence_service.Minio",
            return_value=self.client,
        )
        self.minio_patch.start()
        self.addCleanup(self.minio_patch.stop)

    def test_valid_pdf_is_uploaded_with_bounded_length_and_safe_public_reference(self) -> None:
        service = EvidenceService()

        public_reference = service.upload_pdf(
            _upload("informe académico.pdf", "application/pdf", b"%PDF-1.4\nvalid")
        )

        self.assertRegex(public_reference, r"^evidence:[0-9a-f-]{36}$")
        public_id = UUID(public_reference.removeprefix("evidence:"))
        self.assertEqual(str(public_id), public_reference.removeprefix("evidence:"))
        self.assertNotIn("localhost", public_reference.casefold())
        self.assertNotIn("minio", public_reference.casefold())
        self.assertNotIn("/", public_reference)
        self.assertEqual(len(self.client.uploads), 1)
        uploaded = self.client.uploads[0]
        self.assertRegex(str(uploaded["object_name"]), r"^[0-9a-f-]{36}\.pdf$")
        self.assertNotEqual(str(uploaded["object_name"]).removesuffix(".pdf"), str(public_id))
        self.assertEqual(
            service.object_name_for_public_reference(public_reference),
            uploaded["object_name"],
            "the opaque public reference must resolve server-side without persistence or API widening",
        )
        self.assertEqual(uploaded["content"], b"%PDF-1.4\nvalid")
        self.assertEqual(uploaded["length"], len(b"%PDF-1.4\nvalid"))
        self.assertEqual(uploaded["content_type"], "application/pdf")

    def test_invalid_extension_is_rejected_before_minio(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            EvidenceService().upload_pdf(
                _upload("informe.exe", "application/pdf", b"%PDF-1.4\nvalid")
            )

        self.assertEqual(caught.exception.status_code, 415)
        self.assertEqual(self.client.bucket_checks, [])
        self.assertEqual(self.client.uploads, [])

    def test_invalid_mime_is_rejected_before_minio(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            EvidenceService().upload_pdf(
                _upload("informe.pdf", "text/plain", b"%PDF-1.4\nvalid")
            )

        self.assertEqual(caught.exception.status_code, 415)
        self.assertEqual(self.client.bucket_checks, [])
        self.assertEqual(self.client.uploads, [])

    def test_oversize_is_rejected_before_minio_with_configured_limit(self) -> None:
        original_limit = settings.evidence_max_bytes
        object.__setattr__(settings, "evidence_max_bytes", 8)
        try:
            with self.assertRaises(HTTPException) as caught:
                EvidenceService().upload_pdf(
                    _upload("informe.pdf", "application/pdf", b"%PDF-1.4\n")
                )
        finally:
            object.__setattr__(settings, "evidence_max_bytes", original_limit)

        self.assertEqual(caught.exception.status_code, 413)
        self.assertEqual(self.client.bucket_checks, [])
        self.assertEqual(self.client.uploads, [])
        self.assertNotIn("traceback", str(caught.exception.detail).casefold())

    def test_hostile_filename_is_sanitized_before_storage_metadata(self) -> None:
        EvidenceService().upload_pdf(
            _upload('..\\private/evil\r\nX-Injected: yes.pdf', "application/pdf", b"%PDF-safe")
        )

        self.assertIn("metadata", self.client.uploads[0])
        metadata = self.client.uploads[0]["metadata"]
        self.assertEqual(metadata, {"public-filename": "evil_X-Injected_ yes.pdf"})
        self.assertNotRegex(repr(metadata), r"[\\/\r\n]")


class PublicImportSerializationTests(unittest.TestCase):
    def test_recursive_public_payload_blocks_nested_paths_credentials_and_diagnostics(self) -> None:
        private_payload = {
            "bucket": "private-evidence",
            "dropbox_path": "/secret/file.pdf",
            "connection_string": "mysql://user:password@database/internal",
            "password": "top-secret",
            "minio_endpoint": "http://minio:9000",
            "filename": "C:\\private\\hostile\r\nreport.pdf",
            "nested": {
                "error_message": "Traceback (most recent call last): /app/parser.py",
                "object_key": "private/object.pdf",
                "safe_label": "Proyecto FCI 2026",
            },
        }

        public = sanitize_public_import_payload(private_payload)

        for key in ("bucket", "dropbox_path", "connection_string", "password", "minio_endpoint"):
            self.assertIsNone(public[key])
        self.assertEqual(public["filename"], "hostile_report.pdf")
        self.assertEqual(public["nested"]["error_message"], "No se pudo completar la importación.")
        self.assertIsNone(public["nested"]["object_key"])
        self.assertEqual(public["nested"]["safe_label"], "Proyecto FCI 2026")
        serialized = repr(public).casefold()
        for forbidden in ("private-evidence", "/secret/", "mysql://", "top-secret", "minio:9000", "traceback"):
            self.assertNotIn(forbidden, serialized)

    def test_every_untyped_public_audit_return_uses_recursive_sanitizer(self) -> None:
        source = Path("app/api/v1/endpoints/imports.py").read_text(encoding="utf-8")
        module = ast.parse(source)
        endpoint_names = {
            "batch_participants_audit",
            "import_batch_normalization_audit",
            "batch_reconciliation_audit",
            "import_batch_extraction_by_file_audit",
            "import_batch_golden_audit",
            "import_batch_full_extraction_audit",
            "import_batch_completeness_report",
        }

        class EndpointReturnVisitor(ast.NodeVisitor):
            def __init__(self, root) -> None:
                self.root = root
                self.returns: list[ast.Return] = []

            def visit_FunctionDef(self, node) -> None:
                if node is self.root:
                    self.generic_visit(node)

            def visit_AsyncFunctionDef(self, node) -> None:
                if node is self.root:
                    self.generic_visit(node)

            def visit_Return(self, node) -> None:
                self.returns.append(node)

        found = set()
        for node in module.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name not in endpoint_names:
                continue
            found.add(node.name)
            visitor = EndpointReturnVisitor(node)
            visitor.visit(node)
            self.assertTrue(visitor.returns, node.name)
            for returned in visitor.returns:
                self.assertIsInstance(returned.value, ast.Call, f"{node.name}:{returned.lineno}")
                self.assertEqual(
                    getattr(returned.value.func, "id", None),
                    "sanitize_public_import_payload",
                    f"{node.name}:{returned.lineno}",
                )
        self.assertEqual(found, endpoint_names)

    def test_import_batch_errors_keep_trace_server_side_and_public_detail_generic(self) -> None:
        source = Path("app/api/v1/endpoints/imports.py").read_text(encoding="utf-8")

        self.assertNotIn('"detail": str(exc)', source)
        self.assertNotIn('"detail": str(exc.detail)', source)
        self.assertGreaterEqual(source.count('"detail": "No se pudo registrar el lote de importación."'), 3)
        self.assertIn('logger.exception("Import batch database failure"', source)
        self.assertIn('logger.exception("Unexpected import batch failure"', source)

    def test_job_response_preserves_shape_but_hides_trace_and_storage_locators(self) -> None:
        private_trace = (
            "Traceback (most recent call last): C:/private/import.py\n"
            "postgresql://user:password@database/internal\n"
            "bucket/private-object.pdf token=<REDACTED>"
        )
        job = SimpleNamespace(
            id=7,
            batch_id=3,
            source_type="PROGRESS_PDF",
            filename="../private/hostile\r\nname.pdf",
            status="ERROR",
            imported_by="SESSION_A",
            summary=private_trace,
            source_identifier="/private/dropbox/source.pdf",
            source_rev="revision-public",
            document_key="dropbox_path:/private/dropbox/source.pdf",
            is_current=True,
            supersedes_id=None,
            error_reason=private_trace,
            error_type="system_error",
            error_message=private_trace,
            error_traceback=private_trace,
            retry_count=1,
            max_retries=2,
            current_step="failed",
            started_at=None,
            finished_at=None,
            duration_ms=None,
            queue_ms=None,
            download_ms=None,
            text_extraction_ms=None,
            ocr_ms=None,
            parser_ms=None,
            persistence_ms=None,
            page_count=None,
            used_ocr=False,
            extraction_method=None,
            created_at=datetime.now(timezone.utc),
            processed_at=None,
        )

        public = ImportJobRead.model_validate(job).model_dump(mode="json")

        self.assertIn("error_traceback", public)
        self.assertIsNone(public["error_traceback"])
        self.assertIsNone(public["source_identifier"])
        self.assertIsNone(public["document_key"])
        self.assertEqual(public["filename"], "hostile_name.pdf")
        self.assertEqual(public["error_message"], "No se pudo completar la importación.")
        self.assertEqual(public["error_reason"], "No se pudo completar la importación.")
        self.assertEqual(public["summary"], "No se pudo completar la importación.")
        serialized = repr(public).casefold()
        for forbidden in ("traceback (most", "c:/private", "postgresql://", "bucket/private", "dropbox_path"):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(job.error_traceback, private_trace, "server diagnostic remains intact")

    def test_diagnostics_and_ocr_shapes_keep_safe_null_fields(self) -> None:
        diagnostics = ImportJobDiagnosticsRead(
            id=9,
            filename="C:\\private\\report.pdf",
            status="ERROR",
            error_type="parser_error",
            error_message="Traceback at /app/parser.py",
            traceback="Traceback (most recent call last): /app/parser.py",
            extraction_counts={},
            persisted_counts={},
        ).model_dump(mode="json")
        trace = OcrTraceRead(
            id=4,
            import_job_id=9,
            source_filename="../private/report.pdf",
            source_path="/private/dropbox/report.pdf",
            parsed_payload={
                "source_path": "/private/dropbox/report.pdf",
                "object_key": "bucket/private.pdf",
                "nested": {"traceback": "Traceback (most recent call last): /app/parser.py"},
                "source_filename": "../private/report.pdf",
            },
            review_status="pending",
            created_at=datetime.now(timezone.utc),
        ).model_dump(mode="json")

        self.assertEqual(diagnostics["filename"], "report.pdf")
        self.assertEqual(diagnostics["error_message"], "No se pudo procesar el contenido del PDF.")
        self.assertIsNone(diagnostics["traceback"])
        self.assertEqual(trace["source_filename"], "report.pdf")
        self.assertIsNone(trace["source_path"])
        self.assertIsNone(trace["parsed_payload"]["source_path"])
        self.assertIsNone(trace["parsed_payload"]["object_key"])
        self.assertIsNone(trace["parsed_payload"]["nested"]["traceback"])
        self.assertEqual(trace["parsed_payload"]["source_filename"], "report.pdf")
        self.assertNotIn("/private", repr((diagnostics, trace)))


if __name__ == "__main__":
    unittest.main()
