from __future__ import annotations

import unittest
from hashlib import sha256
from pathlib import PurePosixPath
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import fitz
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import dependencies
from app.api.v1.endpoints import human_review
from app.core.database import Base
from app.models.entities import (
    AcademicPeriod,
    ExternalResearcher,
    ImportedOcrTrace,
    ImportJob,
    PersonRole,
    ResearchEntity,
    ScientificProduction,
    ScientificProductionAuthor,
)
from app.models.enums import ProductionType
from app.models.human_review_core import ReviewItem
from app.schemas.human_review_api import EvidenceResponse
from app.services.human_review_authorization import B2BAccessDenied
from app.services.human_review_queries import (
    EvidenceUnavailableError,
    HumanReviewQueryInternalError,
    HumanReviewQueryService,
    ResolvedEvidence,
    ReviewCaseNotFoundError,
)
from tests.support.postgres import (
    isolated_postgres_schema,
    require_b2b1_test_database_url,
)


CASE_ID = UUID("70000000-0000-0000-0000-000000000001")
CORRELATION_ID = UUID("71000000-0000-0000-0000-000000000001")
INTERNAL_PATH = "/private/dropbox/reports/evidence.pdf"


def _valid_pdf(page_count: int = 1) -> bytes:
    document = fitz.open()
    try:
        for _index in range(page_count):
            document.new_page()
        return document.tobytes()
    finally:
        document.close()


def _resolved_evidence(
    *,
    filename: str = "public-report.pdf",
    source_path: str = INTERNAL_PATH,
    content: bytes | None = None,
) -> ResolvedEvidence:
    resolved_content = content if content is not None else _valid_pdf(4)
    return ResolvedEvidence(
        public=EvidenceResponse(
            document_name=filename,
            page=4,
            section="Scientific production",
            locator="row-17",
            fragment="A bounded public fragment",
            stream_path=f"human-review/cases/{CASE_ID}/evidence",
            correlation_id=CORRELATION_ID,
        ),
        source_path=source_path,
        expected_size=len(resolved_content),
        expected_page_count=4,
        expected_content_hash=human_review._dropbox_content_hash(resolved_content),
    )


def _error_envelope(response) -> dict[str, object]:
    body = response.json()
    detail = body.get("detail")
    return detail if isinstance(detail, dict) else body


class HumanReviewEvidenceHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI()

        @app.middleware("http")
        async def correlation_id(request, call_next):
            request.state.correlation_id = CORRELATION_ID
            return await call_next(request)

        app.include_router(human_review.router, prefix="/human-review")
        app.dependency_overrides[dependencies.get_db] = lambda: MagicMock()
        app.dependency_overrides[dependencies.get_current_user] = lambda: SimpleNamespace(
            id=17,
            is_active=True,
        )
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self) -> None:
        self.client.close()

    def test_missing_capability_returns_closed_403_without_internal_detail(self) -> None:
        secret = f"{INTERNAL_PATH}:dropbox_refresh_token=secret"
        with patch.object(
            dependencies,
            "authorize_b2b_action",
            side_effect=B2BAccessDenied(secret),
        ):
            response = self.client.get(f"/human-review/cases/{CASE_ID}/evidence")

        self.assertEqual(response.status_code, 403)
        body = _error_envelope(response)
        self.assertEqual(body["code"], "B2B_CAPABILITY_REQUIRED")
        self.assertEqual(body["correlation_id"], str(CORRELATION_ID))
        self.assertNotIn(INTERNAL_PATH, response.text)
        self.assertNotIn("refresh_token", response.text)
        self.assertNotIn("secret", response.text)

    def test_missing_case_returns_safe_404_and_preserves_correlation_id(self) -> None:
        with (
            patch.object(dependencies, "authorize_b2b_action", return_value="RESEARCH_MANAGER"),
            patch.object(
                human_review.HumanReviewQueryService,
                "get_evidence",
                side_effect=ReviewCaseNotFoundError(CORRELATION_ID),
            ),
        ):
            response = self.client.get(f"/human-review/cases/{CASE_ID}/evidence")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(_error_envelope(response)["code"], "REVIEW_CASE_NOT_FOUND")
        self.assertEqual(
            _error_envelope(response)["correlation_id"],
            str(CORRELATION_ID),
        )

    def test_corrupt_case_linkage_returns_safe_503_with_correlation_id(self) -> None:
        with (
            patch.object(dependencies, "authorize_b2b_action", return_value="RESEARCH_MANAGER"),
            patch.object(
                human_review.HumanReviewQueryService,
                "get_evidence",
                side_effect=HumanReviewQueryInternalError(CORRELATION_ID),
            ),
        ):
            response = self.client.get(f"/human-review/cases/{CASE_ID}/evidence")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(_error_envelope(response)["code"], "EVIDENCE_UNAVAILABLE")
        self.assertEqual(response.headers["x-correlation-id"], str(CORRELATION_ID))

    def test_corrupt_or_source_backend_failure_returns_safe_503(self) -> None:
        unsafe = HTTPException(
            status_code=502,
            detail=f"Dropbox failed at {INTERNAL_PATH}; client_secret=top-secret",
        )
        with (
            patch.object(dependencies, "authorize_b2b_action", return_value="RESEARCH_MANAGER"),
            patch.object(
                human_review.HumanReviewQueryService,
                "get_evidence",
                return_value=_resolved_evidence(),
            ),
            patch.object(
                human_review,
                "_download_evidence_pdf",
                side_effect=unsafe,
            ),
        ):
            response = self.client.get(f"/human-review/cases/{CASE_ID}/evidence")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(_error_envelope(response)["code"], "EVIDENCE_UNAVAILABLE")
        self.assertEqual(
            _error_envelope(response)["correlation_id"],
            str(CORRELATION_ID),
        )
        self.assertNotIn(INTERNAL_PATH, response.text)
        self.assertNotIn("client_secret", response.text)
        self.assertNotIn("top-secret", response.text)

    def test_valid_pdf_stream_is_bounded_typed_and_path_free(self) -> None:
        content = _valid_pdf(4)
        with (
            patch.object(dependencies, "authorize_b2b_action", return_value="RESEARCH_MANAGER"),
            patch.object(
                human_review.HumanReviewQueryService,
                "get_evidence",
                return_value=_resolved_evidence(
                    filename='unsafe"\\..\\report.pdf',
                    content=content,
                ),
            ),
            patch.object(
                human_review,
                "_download_evidence_pdf",
                return_value=content,
            ) as download,
        ):
            response = self.client.get(f"/human-review/cases/{CASE_ID}/evidence")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, content)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertIn("evidence.pdf", response.headers["content-disposition"])
        self.assertNotIn(INTERNAL_PATH, repr(dict(response.headers)))
        self.assertEqual(download.call_args.kwargs["source_path"], INTERNAL_PATH)

    def test_oversized_or_non_pdf_download_is_rejected_without_leakage(self) -> None:
        for content in (
            b"not-a-pdf",
            b"%PDF-not-a-structurally-valid-document",
            b"%PDF-" + b"x" * (human_review.MAX_EVIDENCE_BYTES + 1),
        ):
            with (
                self.subTest(size=len(content)),
                patch.object(dependencies, "authorize_b2b_action", return_value="RESEARCH_MANAGER"),
                patch.object(
                    human_review.HumanReviewQueryService,
                    "get_evidence",
                    return_value=_resolved_evidence(content=content),
                ),
                patch.object(
                    human_review,
                    "_download_evidence_pdf",
                    return_value=content,
                ),
            ):
                response = self.client.get(f"/human-review/cases/{CASE_ID}/evidence")
                self.assertEqual(response.status_code, 503)
                self.assertNotIn(INTERNAL_PATH, response.text)

    def test_json_content_negotiation_returns_verified_typed_metadata(self) -> None:
        content = _valid_pdf(4)
        evidence = _resolved_evidence(content=content)
        with (
            patch.object(dependencies, "authorize_b2b_action", return_value="SYSTEM_ADMIN"),
            patch.object(
                human_review.HumanReviewQueryService,
                "get_evidence",
                return_value=evidence,
            ),
            patch.object(
                human_review,
                "_download_evidence_pdf",
                return_value=content,
            ) as download,
        ):
            response = self.client.get(
                f"/human-review/cases/{CASE_ID}/evidence",
                headers={"Accept": "application/json"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), evidence.public.model_dump(mode="json"))
        self.assertEqual(response.headers["x-correlation-id"], str(CORRELATION_ID))
        self.assertEqual(response.headers["cache-control"], "private, no-store, max-age=0")
        self.assertNotIn(INTERNAL_PATH, response.text)
        download.assert_awaited_once()

    def test_same_shape_replacement_is_rejected_by_dropbox_content_hash(self) -> None:
        content = _valid_pdf(4)
        replacement = _resolved_evidence(content=content)
        replacement = ResolvedEvidence(
            public=replacement.public,
            source_path=replacement.source_path,
            expected_size=replacement.expected_size,
            expected_page_count=replacement.expected_page_count,
            expected_content_hash="0" * 64,
        )
        with (
            patch.object(dependencies, "authorize_b2b_action", return_value="RESEARCH_MANAGER"),
            patch.object(
                human_review.HumanReviewQueryService,
                "get_evidence",
                return_value=replacement,
            ),
            patch.object(
                human_review,
                "_download_evidence_pdf",
                return_value=content,
            ),
        ):
            response = self.client.get(f"/human-review/cases/{CASE_ID}/evidence")

        self.assertEqual(response.status_code, 503)
        self.assertNotIn(INTERNAL_PATH, response.text)


class _FakeDropboxResponse:
    def __init__(self, chunks: tuple[bytes, ...], *, declared_size: int) -> None:
        self.status_code = 200
        self.headers = {"content-length": str(declared_size)}
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, _type, _value, _traceback):
        return False

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


class _FakeDropboxClient:
    def __init__(self, response: _FakeDropboxResponse) -> None:
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, _type, _value, _traceback):
        return False

    def stream(self, *_args, **_kwargs):
        return self.response


class HumanReviewEvidenceBoundedDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_download_reads_incrementally_with_exact_predeclared_bound(self) -> None:
        response = _FakeDropboxResponse((b"12", b"345"), declared_size=5)
        with (
            patch.object(
                human_review.ImportService,
                "_get_dropbox_access_token",
                new=AsyncMock(return_value="private-token"),
            ),
            patch.object(
                human_review.httpx,
                "AsyncClient",
                return_value=_FakeDropboxClient(response),
            ),
        ):
            content = await human_review._download_evidence_pdf(
                MagicMock(),
                source_path=INTERNAL_PATH,
                expected_size=5,
            )

        self.assertEqual(content, b"12345")

    async def test_download_stops_as_soon_as_stream_exceeds_bound(self) -> None:
        response = _FakeDropboxResponse((b"123456",), declared_size=5)
        with (
            patch.object(
                human_review.ImportService,
                "_get_dropbox_access_token",
                new=AsyncMock(return_value="private-token"),
            ),
            patch.object(
                human_review.httpx,
                "AsyncClient",
                return_value=_FakeDropboxClient(response),
            ),
        ):
            with self.assertRaises(ValueError):
                await human_review._download_evidence_pdf(
                    MagicMock(),
                    source_path=INTERNAL_PATH,
                    expected_size=5,
                )

    def test_pdf_validation_matches_existing_import_whitespace_policy(self) -> None:
        content = b"\n\t " + _valid_pdf()
        validated = human_review._validated_pdf(
            content,
            expected_size=len(content),
            expected_pages=1,
        )
        self.assertEqual(validated, content)

    def test_pdf_page_count_mismatch_is_rejected(self) -> None:
        content = _valid_pdf()
        with self.assertRaises(ValueError):
            human_review._validated_pdf(
                content,
                expected_size=len(content),
                expected_pages=4,
            )

    def test_encrypted_pdf_is_rejected(self) -> None:
        document = fitz.open()
        try:
            document.new_page()
            content = document.tobytes(
                encryption=fitz.PDF_ENCRYPT_AES_256,
                owner_pw="owner-secret",
                user_pw="reader-secret",
            )
        finally:
            document.close()

        with self.assertRaises(ValueError):
            human_review._validated_pdf(
                content,
                expected_size=len(content),
                expected_pages=1,
            )

    def test_locator_policy_accepts_domain_locators_and_rejects_storage_or_traversal(self) -> None:
        for locator in (
            "row-17",
            "product:public title",
            "entity:project/code",
            "external:public name|public institution",
            "produccion_cientifica:9",
            "participant:locked-ana-two",
            "row:key",
        ):
            with self.subTest(locator=locator):
                self.assertTrue(HumanReviewQueryService._safe_locator(locator))
        for locator in (
            "../other",
            "row\\other",
            "dropbox_path:/private/report.pdf",
            "file:C:/private/report.pdf",
            "https://storage.internal/report.pdf",
            "row/%2e%2e/other",
            " dropbox_path:/private/report.pdf",
            "postgresql:secret",
            "redis:secret",
            "ftp:private.pdf",
            "sftp:private.pdf",
            "jdbc:private",
            "gs:bucket-object",
            "urn:storage:secret",
            "C:/private/secret.pdf",
            "entity:C:/private/report.pdf",
            "entity:file:C:/private/report.pdf",
            "product:s3:private/object",
            "external:minio:bucket/key",
            "product:Study C:/private/report.pdf",
            "external:Name s3:private/object",
            "product:Study dropbox_path:/private/report.pdf",
        ):
            with self.subTest(locator=locator):
                self.assertFalse(HumanReviewQueryService._safe_locator(locator))


class HumanReviewEvidencePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._schema = isolated_postgres_schema(
            require_b2b1_test_database_url(),
            "b2b2evidence",
        )
        cls.engine = cls._schema.__enter__()
        Base.metadata.create_all(cls.engine)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._schema.__exit__(None, None, None)

    def setUp(self) -> None:
        self.db = Session(self.engine)
        self.db.add(AcademicPeriod(id=2026, year_label="2026", cycle=1))
        self.job = ImportJob(
            id=701,
            source_type="PROGRESS_PDF",
            filename="../unsafe-internal-name.pdf",
            status="SUCCESS",
            source_identifier="id:opaque-dropbox-document",
            source_rev="revision-7",
            document_key="dropbox_path:/private/dropbox/reports/evidence.pdf",
            is_current=True,
            page_count=4,
        )
        self.target = PersonRole(
            id=702,
            period_id=2026,
            import_job_id=701,
            role_type="researcher",
            person_type="teacher",
            raw_value="Detected evidence " + "x" * 5000,
            normalized_value="detected evidence",
            source_page=4,
            source_section="Scientific production",
            metadata_json={"row_or_block_id": "row-17"},
            validation_status="pending_review",
        )
        self.trace = ImportedOcrTrace(
            id=703,
            import_job_id=701,
            source_filename="../unsafe-internal-name.pdf",
            source_path=INTERNAL_PATH,
            extracted_text="Source text is private",
            parsed_payload={
                "document": {
                    "filename": "../unsafe-internal-name.pdf",
                    "name": "../unsafe-internal-name.pdf",
                    "path_lower": INTERNAL_PATH,
                    "rev": "revision-7",
                    "size": 1024,
                    "content_hash": "a" * 64,
                }
            },
        )
        self.item = ReviewItem(
            id=CASE_ID,
            case_type="person_identity",
            stable_target_key=f"b2b:v1:person_identity:{'a' * 64}",
            target_table="person_roles",
            target_pk=702,
            scope_resolution_reason="unresolved_no_persisted_scope",
            document_key=self.job.document_key,
            source_revision="revision-7",
            source_page=4,
            source_section="Scientific production",
            row_or_block_id="row-17",
            field_path="case",
            raw_value_sha256=sha256(self.target.raw_value.encode("utf-8")).hexdigest(),
            period_id=2026,
            case_status="pending",
            scientific_status="pending",
            automatic_priority=50,
            version=1,
        )
        self.db.add_all((self.job, self.target, self.trace, self.item))
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        with self.engine.begin() as connection:
            for table in reversed(Base.metadata.sorted_tables):
                connection.execute(table.delete())

    def test_resolves_only_correlated_current_evidence_and_returns_safe_metadata(self) -> None:
        before = self._counts()
        result = HumanReviewQueryService(
            self.db,
            correlation_id=CORRELATION_ID,
        ).get_evidence(CASE_ID)
        after = self._counts()

        self.assertEqual(result.source_path, INTERNAL_PATH)
        self.assertEqual(result.expected_size, 1024)
        self.assertEqual(result.expected_content_hash, "a" * 64)
        public = result.public.model_dump(mode="json")
        self.assertEqual(public["correlation_id"], str(CORRELATION_ID))
        self.assertEqual(public["page"], 4)
        self.assertEqual(public["section"], "Scientific production")
        self.assertEqual(public["locator"], "row-17")
        self.assertEqual(len(public["fragment"]), 4000)
        self.assertEqual(
            public["stream_path"],
            f"human-review/cases/{CASE_ID}/evidence",
        )
        self.assertNotIn(INTERNAL_PATH, repr(public))
        self.assertNotIn("dropbox_path:", repr(public))
        self.assertEqual(before, after)
        self.assertFalse(self.db.new)
        self.assertFalse(self.db.dirty)
        self.assertFalse(self.db.deleted)

    def test_missing_case_is_distinct_from_unavailable_evidence(self) -> None:
        service = HumanReviewQueryService(self.db, correlation_id=CORRELATION_ID)
        with self.assertRaises(ReviewCaseNotFoundError):
            service.get_evidence(UUID("70000000-0000-0000-0000-000000000099"))

        self.job.is_current = False
        self.db.commit()
        with self.assertRaises(EvidenceUnavailableError):
            service.get_evidence(CASE_ID)

    def test_rejects_cross_document_revision_page_section_and_target_access(self) -> None:
        mutations = (
            (self.job, "document_key", "dropbox_path:/other/document.pdf"),
            (self.job, "source_rev", "other-revision"),
            (self.target, "source_page", 9),
            (self.target, "source_section", "Other section"),
            (self.target, "import_job_id", None),
        )
        service = HumanReviewQueryService(self.db, correlation_id=CORRELATION_ID)
        for row, field, value in mutations:
            original = getattr(row, field)
            with self.subTest(field=field):
                setattr(row, field, value)
                self.db.flush()
                with self.assertRaises(EvidenceUnavailableError):
                    service.get_evidence(CASE_ID)
                setattr(row, field, original)
                self.db.flush()

    def test_rejects_traversal_locator_and_corrupt_trace(self) -> None:
        service = HumanReviewQueryService(self.db, correlation_id=CORRELATION_ID)
        for field, value in (
            ("row_or_block_id", "../../other-document"),
            ("row_or_block_id", "dropbox_path:/private/secret"),
            ("row_or_block_id", "row\\other"),
        ):
            original = getattr(self.item, field)
            with self.subTest(field=field, value=value):
                setattr(self.item, field, value)
                self.db.flush()
                with self.assertRaises(EvidenceUnavailableError):
                    service.get_evidence(CASE_ID)
                setattr(self.item, field, original)
                self.db.flush()

        self.trace.source_path = ""
        self.db.flush()
        with self.assertRaises(EvidenceUnavailableError):
            service.get_evidence(CASE_ID)

    def test_optional_page_remains_valid_while_pdf_page_count_is_bounded(self) -> None:
        self.target.source_page = None
        self.item.source_page = None
        self.db.commit()

        result = HumanReviewQueryService(
            self.db,
            correlation_id=CORRELATION_ID,
        ).get_evidence(CASE_ID)

        self.assertIsNone(result.public.page)
        self.assertEqual(result.expected_page_count, 4)

    def test_case_detail_exposes_only_sanitized_public_evidence_summary(self) -> None:
        detail = HumanReviewQueryService(
            self.db,
            correlation_id=CORRELATION_ID,
        ).get_case(CASE_ID)
        summary = detail.model_dump(mode="json")["evidence_summary"]

        self.assertEqual(len(detail.detected_value), 4000)
        self.assertTrue(summary["available"])
        self.assertEqual(summary["page"], 4)
        self.assertEqual(summary["locator"], "row-17")
        self.assertEqual(
            summary["stream_path"],
            f"human-review/cases/{CASE_ID}/evidence",
        )
        self.assertNotIn(INTERNAL_PATH, repr(summary))
        self.assertNotIn("dropbox_path:", repr(summary))

    def test_resolves_every_materialized_target_type_and_author_job_fallback(self) -> None:
        production = ScientificProduction(
            id=710,
            period_id=2026,
            import_job_id=self.job.id,
            production_type=ProductionType.ARTICLE,
            title="Public title",
            raw_title="Raw public title",
            normalized_title="public title",
            source_page=4,
            source_section="Scientific production",
        )
        author = ScientificProductionAuthor(
            id=711,
            production_id=production.id,
            import_job_id=None,
            author_order=1,
            raw_author_name="Raw author",
            normalized_author_name="raw author",
            author_type="external",
            source_page=4,
            source_section="Scientific production",
            row_or_block_id="produccion_cientifica:1",
        )
        entity = ResearchEntity(
            id=712,
            period_id=2026,
            import_job_id=self.job.id,
            type="project",
            name="Public entity",
            raw_value="Raw entity",
            source_page=4,
            source_section="Scientific production",
            metadata_json={"row_or_block_id": "entity-row-1"},
        )
        external = ExternalResearcher(
            id=713,
            period_id=2026,
            import_job_id=self.job.id,
            full_name="External Name",
            normalized_name="external name",
            institution="External Institution",
            normalized_institution="external institution",
            raw_value="Raw external",
            source_page=4,
            source_section="Scientific production",
        )
        duplicate = PersonRole(
            id=714,
            period_id=2026,
            import_job_id=self.job.id,
            role_type="researcher",
            person_type="teacher",
            raw_value="Duplicate participant",
            source_page=4,
            source_section="Scientific production",
            metadata_json={"row_or_block_id": "participant:duplicate"},
        )
        self.db.add_all((production, author, entity, external, duplicate))
        self.db.flush()

        cases = (
            ("product", "scientific_productions", production, "product:public title", "Raw public title"),
            (
                "author_identity",
                "scientific_production_authors",
                author,
                "produccion_cientifica:1",
                "Raw author",
            ),
            (
                "project_director_relation",
                "research_entities",
                entity,
                "entity:entity-row-1",
                "Raw entity",
            ),
            (
                "external_identity",
                "external_researchers",
                external,
                "external:external name|external institution",
                "Raw external",
            ),
            (
                "possible_duplicate",
                "person_roles",
                duplicate,
                "participant:duplicate",
                "Duplicate participant",
            ),
        )
        service = HumanReviewQueryService(self.db, correlation_id=CORRELATION_ID)
        for index, (case_type, table, target, locator, detected) in enumerate(cases, start=1):
            item_id = UUID(f"70000000-0000-0000-0000-{index + 100:012d}")
            item = ReviewItem(
                id=item_id,
                case_type=case_type,
                stable_target_key=f"b2b:v1:{case_type}:{index:064x}",
                target_table=table,
                target_pk=target.id,
                scope_resolution_reason="unresolved_no_persisted_scope",
                document_key=self.job.document_key,
                source_revision=self.job.source_rev,
                source_page=4,
                source_section="Scientific production",
                row_or_block_id=locator,
                field_path="case",
                raw_value_sha256=sha256(detected.encode("utf-8")).hexdigest(),
                period_id=2026,
                case_status="pending",
                scientific_status="pending",
                automatic_priority=50,
                version=1,
            )
            self.db.add(item)
            self.db.flush()
            with self.subTest(case_type=case_type, target_table=table):
                evidence = service.get_evidence(item_id)
                self.assertEqual(evidence.public.locator, locator)
                self.assertEqual(evidence.source_path, INTERNAL_PATH)

    def test_rejects_missing_integrity_metadata_and_ambiguous_traces(self) -> None:
        service = HumanReviewQueryService(self.db, correlation_id=CORRELATION_ID)
        original_document = dict(self.trace.parsed_payload["document"])

        for missing_field in ("size", "content_hash"):
            with self.subTest(missing_field=missing_field):
                document = dict(original_document)
                document.pop(missing_field)
                self.trace.parsed_payload = {"document": document}
                self.db.flush()
                with self.assertRaises(EvidenceUnavailableError):
                    service.get_evidence(CASE_ID)
                self.trace.parsed_payload = {"document": dict(original_document)}
                self.db.flush()

        oversized_name = dict(original_document)
        oversized_name["name"] = (
            "public-prefix/" * 30 + PurePosixPath(self.trace.source_filename).name
        )
        self.trace.parsed_payload = {"document": oversized_name}
        self.db.flush()
        with self.assertRaises(EvidenceUnavailableError):
            service.get_evidence(CASE_ID)
        self.trace.parsed_payload = {"document": dict(original_document)}
        self.db.flush()

        conflicting_trace = ImportedOcrTrace(
            id=704,
            import_job_id=self.job.id,
            source_filename=self.trace.source_filename,
            source_path=self.trace.source_path,
            extracted_text="Conflicting source",
            parsed_payload={
                "document": {
                    **original_document,
                    "content_hash": "b" * 64,
                }
            },
        )
        self.db.add(conflicting_trace)
        self.db.flush()
        with self.assertRaises(EvidenceUnavailableError):
            service.get_evidence(CASE_ID)

    def _counts(self) -> tuple[int, int, int, int]:
        return tuple(
            int(self.db.scalar(select(func.count()).select_from(model)) or 0)
            for model in (ReviewItem, ImportJob, ImportedOcrTrace, PersonRole)
        )


if __name__ == "__main__":
    unittest.main()
