from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

import fitz
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session

from app.api import dependencies
from app.api.v1.endpoints import human_review
from app.api.v1.router import api_router
from app.core.database import Base
from app.models.entities import (
    AcademicPeriod,
    Career,
    Faculty,
    ImportJob,
    ImportedOcrTrace,
    ScientificProduction,
    ScientificProductionAuthor,
    Teacher,
    User,
)
from app.models.enums import ProductionType, UserRole
from app.models.human_review_access import UserB2BCapability
from app.models.human_review_audit import AuditEvent
from app.models.human_review_core import ReviewDecision, ReviewItem
from app.models.human_review_projection import FieldOverride
from app.schemas.human_review_api import ReviewQueueQuery
from app.services import human_review_commands as commands
from app.services.human_review_queries import HumanReviewQueryService
from app.services.human_review_targets import raw_value_sha256
from app.services.kpi_service import KpiService
from app.services.validated_read_service import ValidatedReadService
from tests.support.postgres import isolated_postgres_schema, require_b2b1_test_database_url


CASE_ID = UUID("e2000000-0000-0000-0000-000000000001")
CORRELATION_ID = UUID("e2000000-0000-0000-0000-000000000002")
INTERNAL_PATH = "/private/dropbox/task14/evidence.pdf"
FORBIDDEN_PUBLIC_TEXT = (
    "document_key",
    "stable_target_key",
    "dropbox_path",
    "source_path",
    "bucket",
    "object_key",
    "access_token",
    "postgresql://",
    INTERNAL_PATH,
)


def _pdf() -> bytes:
    document = fitz.open()
    try:
        document.new_page().insert_text((72, 72), "Task 14 synthetic evidence")
        return document.tobytes()
    finally:
        document.close()


def _dropbox_content_hash(content: bytes) -> str:
    blocks = b"".join(
        sha256(content[offset : offset + 4 * 1024 * 1024]).digest()
        for offset in range(0, len(content), 4 * 1024 * 1024)
    )
    return sha256(blocks).hexdigest()


@unittest.skipUnless(
    os.environ.get("B2B1_TEST_DATABASE_URL"),
    "requires disposable PostgreSQL 16 via B2B1_TEST_DATABASE_URL",
)
class HumanReviewTask14PostgresE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._schema = isolated_postgres_schema(
            require_b2b1_test_database_url(), "b2b2_task14"
        )
        cls.engine = cls._schema.__enter__()
        with cls.engine.connect() as connection:
            version = connection.execute(
                text("SELECT current_setting('server_version_num')::integer")
            ).scalar_one()
        if not 160000 <= version < 170000:
            cls._schema.__exit__(None, None, None)
            raise RuntimeError("Task 14 E2E tests require disposable PostgreSQL 16")
        Base.metadata.create_all(cls.engine)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._schema.__exit__(None, None, None)

    def setUp(self) -> None:
        self.evidence = _pdf()
        with Session(self.engine) as db, db.begin():
            faculty = Faculty(name=f"Task14 Faculty {uuid4().hex}")
            career = Career(
                name=f"Task14 Career {uuid4().hex}",
                code=uuid4().hex[:12],
                faculty=faculty,
            )
            period = AcademicPeriod(year_label="2099", cycle=1)
            job = ImportJob(
                source_type="PROGRESS_PDF",
                filename="task14-public-evidence.pdf",
                status="SUCCESS",
                source_identifier=f"task14:{uuid4().hex}",
                source_rev="task14-revision-1",
                document_key=f"dropbox_path:{INTERNAL_PATH}",
                is_current=True,
                page_count=1,
            )
            teacher = Teacher(
                career=career,
                full_name="Task 14 Researcher",
                institutional_email=f"task14-{uuid4().hex}@example.invalid",
                validation_status="validated",
            )
            actor = User(
                email=f"task14-manager-{uuid4().hex}@example.invalid",
                full_name="Task 14 Research Manager",
                hashed_password="not-used",
                role=UserRole.FACULTY_ADMIN,
                faculty=faculty,
                is_active=True,
            )
            db.add_all((faculty, career, period, job, teacher, actor))
            db.flush()
            production = ScientificProduction(
                teacher_id=teacher.id,
                period_id=period.id,
                production_type=ProductionType.ARTICLE,
                title="Automatic title",
                raw_title="Detected Task 14 title",
                normalized_title="detected task 14 title",
                raw_value="Detected Task 14 title",
                normalized_value="detected task 14 title",
                status="published",
                import_job_id=job.id,
                source_page=1,
                source_section="Scientific production",
                validation_status="pending_review",
            )
            db.add(production)
            db.flush()
            db.add(ScientificProductionAuthor(
                production_id=production.id,
                author_order=1,
                normalized_author_name="Task 14 Researcher",
                canonical_identity_key="human:task14:researcher",
                canonical_name="Task 14 Researcher",
                teacher_id=teacher.id,
                author_type="internal",
                validation_status="validated",
                import_job_id=job.id,
            ))
            trace = ImportedOcrTrace(
                import_job_id=job.id,
                source_filename=job.filename,
                source_path=INTERNAL_PATH,
                extracted_text="Task 14 synthetic evidence",
                parsed_payload={
                    "document": {
                        "filename": job.filename,
                        "name": job.filename,
                        "path_lower": INTERNAL_PATH,
                        "rev": job.source_rev,
                        "size": len(self.evidence),
                        "content_hash": _dropbox_content_hash(self.evidence),
                    }
                },
            )
            item = ReviewItem(
                id=CASE_ID,
                case_type="product",
                stable_target_key=f"b2b:v1:product:{'e' * 64}",
                target_table="scientific_productions",
                target_pk=production.id,
                scope_faculty_id=faculty.id,
                scope_career_id=career.id,
                document_key=job.document_key,
                source_revision=job.source_rev,
                source_page=1,
                source_section="Scientific production",
                row_or_block_id="product:detected task 14 title",
                field_path="product_title",
                raw_value_sha256=raw_value_sha256(production.raw_value),
                period_id=period.id,
                case_status="pending",
                scientific_status="pending",
                automatic_priority=90,
                manual_priority=7,
                possible_kpi_impact=True,
                version=1,
            )
            db.add_all((trace, item))
            db.flush()
            db.add(UserB2BCapability(
                user_id=actor.id,
                capability="RESEARCH_MANAGER",
                approval_reference="task14-e2e",
                approved_input_sha256="e" * 64,
                assigned_by_identifier="task14-e2e",
            ))
            self.actor_id = actor.id
            self.period_id = period.id
            self.career_id = career.id
            self.faculty_id = faculty.id
            self.job_id = job.id
            self.production_id = production.id

        self.active_actor_id: int | None = self.actor_id
        app = FastAPI()
        app.include_router(api_router, prefix="/api/v1")

        def database_dependency():
            with Session(self.engine) as db:
                yield db

        def actor_dependency():
            if self.active_actor_id is None:
                raise AssertionError("authentication override is intentionally absent")
            with Session(self.engine) as db:
                actor = db.get(User, self.active_actor_id)
                assert actor is not None
                db.expunge(actor)
                return actor

        app.dependency_overrides[dependencies.get_db] = database_dependency
        app.dependency_overrides[dependencies.get_current_user] = actor_dependency
        self.app = app
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self) -> None:
        self.client.close()
        with self.engine.begin() as connection:
            connection.execute(
                ReviewItem.__table__.update().values(current_decision_id=None)
            )
            for table in reversed(Base.metadata.sorted_tables):
                connection.execute(table.delete())

    @staticmethod
    def _assert_sanitized(value: object) -> None:
        serialized = json.dumps(value, sort_keys=True, default=str).casefold()
        for forbidden in FORBIDDEN_PUBLIC_TEXT:
            if forbidden:
                assert forbidden.casefold() not in serialized, serialized

    def _apply_body(self, *, correlation_id: UUID = CORRELATION_ID) -> dict[str, object]:
        return {
            "expected_version": 1,
            "expected_current_decision_id": None,
            "action": "correct",
            "scope": "record",
            "payload": {
                "case_type": "product",
                "product_title": "Human validated Task 14 title",
                "scientific_status": "validated",
            },
            "reason": "Verified against the synthetic Task 14 evidence",
            "correlation_id": str(correlation_id),
        }

    def _counts(self) -> tuple[int, int, int]:
        with Session(self.engine) as db:
            return (
                int(db.scalar(select(func.count()).select_from(ReviewDecision).where(ReviewDecision.review_item_id == CASE_ID)) or 0),
                int(db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.review_item_id == CASE_ID)) or 0),
                int(db.scalar(select(func.count()).select_from(FieldOverride).where(FieldOverride.review_item_id == CASE_ID)) or 0),
            )

    def test_b2b2_static_sources_have_no_embedded_secret_or_host_path(self) -> None:
        project_root = Path(__file__).resolve().parents[2]
        sources = [
            project_root / "backend/app/api/v1/endpoints/human_review.py",
            project_root / "backend/app/schemas/human_review_api.py",
            *sorted((project_root / "backend/app/services").glob("human_review*.py")),
            *sorted((project_root / "backend/app/models").glob("human_review*.py")),
            *sorted((project_root / "frontend/app/human-review").rglob("*.tsx")),
            *sorted((project_root / "frontend/components/human-review").glob("*.tsx")),
            project_root / "frontend/hooks/useHumanReview.ts",
            *sorted((project_root / "frontend/lib").glob("human-review*.ts")),
            Path(__file__).resolve(),
            project_root / "frontend/tests/human-review-e2e.test.mjs",
        ]
        patterns = {
            "private key": re.compile(
                r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
                re.IGNORECASE,
            ),
            "literal credential": re.compile(
                r"(?<![A-Za-z0-9_])(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*"
                r"['\"][A-Za-z0-9/+_.-]{20,}['\"]",
                re.IGNORECASE,
            ),
            "host user path": re.compile(
                r"(?:[A-Za-z]:" + re.escape("\\") + r"Users" + re.escape("\\")
                + r"[^\\\s]+|/" + "home" + r"/[^/\s]+|/" + "Users"
                + r"/[^/\s]+)",
                re.IGNORECASE,
            ),
        }
        findings: list[str] = []
        for source in sources:
            self.assertTrue(source.is_file(), str(source))
            content = source.read_text(encoding="utf-8")
            for label, pattern in patterns.items():
                if pattern.search(content):
                    findings.append(f"{label}: {source.relative_to(project_root)}")
        self.assertEqual(findings, [])

    def test_queue_detail_evidence_apply_readers_kpi_and_revert_persist_across_sessions(self) -> None:
        queue = self.client.get(
            "/api/v1/human-review/cases?status=pending&page=1&page_size=10",
            headers={"X-Correlation-ID": str(CORRELATION_ID)},
        )
        self.assertEqual(queue.status_code, 200, queue.text)
        self.assertEqual(queue.json()["items"][0]["id"], str(CASE_ID))
        self.assertEqual(queue.json()["total"], 1)
        self._assert_sanitized(queue.json())

        detail = self.client.get(f"/api/v1/human-review/cases/{CASE_ID}")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertTrue(detail.json()["evidence_summary"]["available"])
        self._assert_sanitized(detail.json())

        with patch.object(
            human_review, "_download_evidence_pdf", return_value=self.evidence
        ):
            metadata = self.client.get(
                f"/api/v1/human-review/cases/{CASE_ID}/evidence",
                headers={"Accept": "application/json", "X-Correlation-ID": str(CORRELATION_ID)},
            )
            streamed = self.client.get(
                f"/api/v1/human-review/cases/{CASE_ID}/evidence",
                headers={"Accept": "application/pdf", "X-Correlation-ID": str(CORRELATION_ID)},
            )
        self.assertEqual(metadata.status_code, 200, metadata.text)
        self.assertEqual(streamed.status_code, 200, streamed.text)
        self.assertEqual(streamed.content, self.evidence)
        self.assertEqual(streamed.headers["content-type"], "application/pdf")
        self.assertEqual(streamed.headers["cache-control"], "private, no-store, max-age=0")
        self._assert_sanitized(metadata.json())
        self._assert_sanitized(dict(streamed.headers))

        applied = self.client.post(
            f"/api/v1/human-review/cases/{CASE_ID}/apply",
            json=self._apply_body(),
        )
        self.assertEqual(applied.status_code, 200, applied.text)
        applied_body = applied.json()
        decision_id = applied_body["decision_id"]
        self.assertEqual(applied_body["case"]["version"], 2)
        self.assertEqual(applied_body["case"]["case_status"], "resolved")
        self.assertEqual(applied_body["case"]["canonical_value"], "Human validated Task 14 title")
        self.assertEqual(applied_body["correlation_id"], str(CORRELATION_ID))
        self._assert_sanitized(applied_body)

        with Session(self.engine) as observer:
            item = observer.get(ReviewItem, CASE_ID)
            original = observer.get(ReviewDecision, UUID(decision_id))
            self.assertEqual((item.version, item.case_status), (2, "resolved"))
            self.assertEqual(original.decision_lifecycle, "approved")
            original_payload = json.dumps(
                original.payload,
                ensure_ascii=False,
                sort_keys=True,
            )
            self.assertEqual(self._counts()[:2], (1, 1))
            views = ValidatedReadService(observer).production_views(
                period_id=self.period_id, visibility="eligible"
            )
            self.assertEqual(len(views), 1)
            self.assertEqual(views[0]["title"], "Human validated Task 14 title")
            dashboard = KpiService(observer).dashboard("2099", 1, self.career_id)
            self.assertEqual(dashboard.scientific_output_total, 1)
            self.assertEqual(dashboard.scientific_output_pending_review, 0)

        audit = self.client.get(f"/api/v1/human-review/cases/{CASE_ID}/audit")
        self.assertEqual(audit.status_code, 200, audit.text)
        self.assertEqual(audit.json()["total"], 1)
        self.assertEqual(audit.json()["items"][0]["event_type"], "scientific_decision_applied")
        self._assert_sanitized(audit.json())

        reverted = self.client.post(
            f"/api/v1/human-review/cases/{CASE_ID}/revert",
            json={
                "expected_version": 2,
                "expected_current_decision_id": decision_id,
                "decision_id_to_revert": decision_id,
                "reason": "Restore the automatic projection after a controlled review",
                "correlation_id": str(CORRELATION_ID),
            },
        )
        if reverted.status_code != 200:
            with Session(self.engine) as observer:
                unchanged = observer.get(ReviewItem, CASE_ID)
                self.assertEqual(
                    (unchanged.version, unchanged.case_status),
                    (2, "resolved"),
                    "a rejected reversal must leave the winning apply intact",
                )
                self.assertEqual(self._counts()[:2], (1, 1))
            self.fail(
                "functional reversal of a prior pending snapshot must reopen the "
                f"case, but the installed endpoint returned {reverted.status_code}: "
                f"{reverted.text}"
            )
        self.assertEqual(reverted.json()["case"]["version"], 3)
        self.assertEqual(reverted.json()["case"]["case_status"], "reopened")
        self._assert_sanitized(reverted.json())

        with Session(self.engine) as observer:
            item = observer.get(ReviewItem, CASE_ID)
            original = observer.get(ReviewDecision, UUID(decision_id))
            decisions = tuple(observer.scalars(
                select(ReviewDecision)
                .where(ReviewDecision.review_item_id == CASE_ID)
                .order_by(ReviewDecision.sequence)
            ))
            self.assertEqual((item.version, item.case_status, item.scientific_status), (3, "reopened", "pending"))
            self.assertEqual(original.decision_lifecycle, "approved")
            self.assertEqual(
                json.dumps(original.payload, ensure_ascii=False, sort_keys=True),
                original_payload,
            )
            self.assertEqual(len(decisions), 2)
            reversal = decisions[1]
            self.assertEqual((reversal.sequence, reversal.decision_type), (2, "reverted"))
            self.assertEqual(reversal.payload["restore_decision_id"], None)
            self.assertEqual(
                reversal.payload["projection_before"]["case_status"],
                "resolved",
            )
            self.assertEqual(
                reversal.payload["projection_after"]["case_status"],
                "reopened",
            )
            self.assertEqual(item.current_decision_id, reversal.id)
            self.assertEqual(self._counts()[:2], (2, 2))
            self.assertEqual(
                ValidatedReadService(observer).production_views(
                    period_id=self.period_id, visibility="eligible"
                ),
                [],
            )
            dashboard = KpiService(observer).dashboard("2099", 1, self.career_id)
            self.assertEqual(dashboard.scientific_output_total, 0)
            self.assertEqual(dashboard.scientific_output_pending_review, 1)
            events = tuple(observer.scalars(
                select(AuditEvent)
                .where(AuditEvent.review_item_id == CASE_ID)
                .order_by(AuditEvent.occurred_at, AuditEvent.id)
            ))
            self.assertEqual(
                tuple(event.event_type for event in events),
                ("scientific_decision_applied", "functional_reversion"),
            )

    def test_task9_public_commands_persist_kpi_neutral_and_kpi_affecting_corrections_then_restore_exactly(self) -> None:
        """Task 9 closure proof: public HTTP commands, fresh sessions and immutable sources."""
        source_before: dict[str, object]
        with Session(self.engine) as observer:
            source = observer.get(ScientificProduction, self.production_id)
            item = observer.get(ReviewItem, CASE_ID)
            source_before = {
                "title": source.title,
                "raw_title": source.raw_title,
                "normalized_title": source.normalized_title,
                "raw_value": source.raw_value,
                "normalized_value": source.normalized_value,
                "validation_status": source.validation_status,
            }
            initial_kpi = KpiService(observer).dashboard("2099", 1, self.career_id)
            self.assertEqual(initial_kpi.scientific_output_kpi_eligible, 0)
            self.assertEqual((item.version, item.current_decision_id), (1, None))

        case_a = self.client.post(
            f"/api/v1/human-review/cases/{CASE_ID}/apply",
            json=self._apply_body(),
        )
        self.assertEqual(case_a.status_code, 200, case_a.text)
        decision_a = UUID(case_a.json()["decision_id"])
        kpi_effect_a = {
            delta["metric"]: delta
            for delta in case_a.json()["kpi_effect"]["affected"]
        }
        self.assertEqual(
            kpi_effect_a["scientific_output_kpi_eligible"],
            {
                "metric": "scientific_output_kpi_eligible",
                "before": 0,
                "after": 1,
                "delta": 1,
            },
        )
        detail_after_a = self.client.get(f"/api/v1/human-review/cases/{CASE_ID}")
        self.assertEqual(detail_after_a.status_code, 200, detail_after_a.text)
        self.assertEqual(detail_after_a.json()["canonical_value"], "Human validated Task 14 title")

        with Session(self.engine) as fresh_after_a:
            source = fresh_after_a.get(ScientificProduction, self.production_id)
            item = fresh_after_a.get(ReviewItem, CASE_ID)
            self.assertEqual(
                {
                    "title": source.title,
                    "raw_title": source.raw_title,
                    "normalized_title": source.normalized_title,
                    "raw_value": source.raw_value,
                    "normalized_value": source.normalized_value,
                    "validation_status": source.validation_status,
                },
                source_before,
            )
            self.assertEqual((item.version, item.current_decision_id), (2, decision_a))
            self.assertEqual(self._counts(), (1, 1, 2))
            active_overrides = tuple(fresh_after_a.scalars(
                select(FieldOverride)
                .where(FieldOverride.review_item_id == CASE_ID, FieldOverride.is_active.is_(True))
                .order_by(FieldOverride.field_path)
            ))
            self.assertEqual(
                {override.field_path for override in active_overrides},
                {"product_title", "scientific_status"},
            )
            effective = ValidatedReadService(fresh_after_a).production_views(
                period_id=self.period_id, visibility="eligible"
            )
            self.assertEqual(effective[0]["title"], "Human validated Task 14 title")
            applied_kpi = KpiService(fresh_after_a).dashboard("2099", 1, self.career_id)
            self.assertEqual(applied_kpi.scientific_output_kpi_eligible, 1)

        with Session(self.engine) as db, db.begin():
            neutral_period = AcademicPeriod(year_label="2098", cycle=2)
            db.add(neutral_period)
            db.flush()
            neutral = ScientificProduction(
                teacher_id=db.get(ScientificProduction, self.production_id).teacher_id,
                period_id=neutral_period.id,
                production_type=ProductionType.ARTICLE,
                title="Neutral automatic title",
                raw_title="Neutral detected title",
                normalized_title="neutral detected title",
                raw_value="Neutral detected title",
                normalized_value="neutral detected title",
                status="published",
                validation_status="validated",
                import_job_id=self.job_id,
                source_page=1,
                source_section="Scientific production",
            )
            db.add(neutral)
            db.flush()
            db.add(ScientificProductionAuthor(
                production_id=neutral.id,
                author_order=1,
                normalized_author_name="Task 14 Researcher",
                canonical_identity_key="human:task14:neutral",
                canonical_name="Task 14 Researcher",
                teacher_id=neutral.teacher_id,
                author_type="internal",
                validation_status="validated",
                import_job_id=self.job_id,
            ))
            neutral_case = ReviewItem(
                case_type="product",
                stable_target_key=f"b2b:v1:product:{'d' * 64}",
                target_table="scientific_productions",
                target_pk=neutral.id,
                scope_faculty_id=self.faculty_id,
                scope_career_id=self.career_id,
                document_key=f"dropbox_path:/private/task9/neutral.pdf",
                source_revision="task9-neutral",
                source_page=1,
                source_section="Scientific production",
                row_or_block_id="product:neutral detected title",
                field_path="product_title",
                raw_value_sha256=raw_value_sha256(neutral.raw_value),
                period_id=neutral_period.id,
                case_status="pending",
                scientific_status="pending",
                automatic_priority=1,
                possible_kpi_impact=True,
                version=1,
            )
            db.add(neutral_case)
            db.flush()
            neutral_case_id = neutral_case.id
            neutral_product_id = neutral.id
            neutral_period_id = neutral_period.id
            neutral_source_before = neutral.title

        with Session(self.engine) as before_b:
            kpi_before_b = KpiService(before_b).dashboard("2098", 2, self.career_id)
            self.assertEqual(kpi_before_b.scientific_output_kpi_eligible, 1)

        case_b = self.client.post(
            f"/api/v1/human-review/cases/{neutral_case_id}/apply",
            json={
                "expected_version": 1,
                "expected_current_decision_id": None,
                "action": "correct",
                "scope": "record",
                "payload": {
                    "case_type": "product",
                    "product_title": "Neutral human title",
                    "scientific_status": "validated",
                },
                "reason": "Visible title correction without eligibility change",
                "correlation_id": str(CORRELATION_ID),
            },
        )
        self.assertEqual(case_b.status_code, 200, case_b.text)
        self.assertEqual(case_b.json()["kpi_effect"]["affected"], [])
        detail_after_b = self.client.get(f"/api/v1/human-review/cases/{neutral_case_id}")
        self.assertEqual(detail_after_b.status_code, 200, detail_after_b.text)
        self.assertEqual(detail_after_b.json()["canonical_value"], "Neutral human title")

        with Session(self.engine) as fresh_after_b:
            source = fresh_after_b.get(ScientificProduction, neutral_product_id)
            item = fresh_after_b.get(ReviewItem, neutral_case_id)
            self.assertEqual(source.title, neutral_source_before)
            self.assertEqual(item.version, 2)
            self.assertEqual(
                int(fresh_after_b.scalar(select(func.count()).select_from(ReviewDecision).where(
                    ReviewDecision.review_item_id == neutral_case_id
                )) or 0),
                1,
            )
            self.assertEqual(
                int(fresh_after_b.scalar(select(func.count()).select_from(AuditEvent).where(
                    AuditEvent.review_item_id == neutral_case_id
                )) or 0),
                1,
            )
            effective = ValidatedReadService(fresh_after_b).production_views(
                period_id=neutral_period_id, visibility="eligible"
            )
            self.assertIn("Neutral human title", {row["title"] for row in effective})
            kpi_after_b = KpiService(fresh_after_b).dashboard("2098", 2, self.career_id)
            self.assertEqual(
                kpi_after_b.scientific_output_kpi_eligible,
                kpi_before_b.scientific_output_kpi_eligible,
            )

        case_c = self.client.post(
            f"/api/v1/human-review/cases/{CASE_ID}/revert",
            json={
                "expected_version": 2,
                "expected_current_decision_id": str(decision_a),
                "decision_id_to_revert": str(decision_a),
                "reason": "Restore the initial effective projection",
                "correlation_id": str(CORRELATION_ID),
            },
        )
        self.assertEqual(case_c.status_code, 200, case_c.text)
        detail_after_c = self.client.get(f"/api/v1/human-review/cases/{CASE_ID}")
        self.assertEqual(detail_after_c.status_code, 200, detail_after_c.text)
        self.assertEqual(detail_after_c.json()["canonical_value"], "Automatic title")

        with Session(self.engine) as fresh_after_c:
            source = fresh_after_c.get(ScientificProduction, self.production_id)
            item = fresh_after_c.get(ReviewItem, CASE_ID)
            self.assertEqual(
                {
                    "title": source.title,
                    "raw_title": source.raw_title,
                    "normalized_title": source.normalized_title,
                    "raw_value": source.raw_value,
                    "normalized_value": source.normalized_value,
                    "validation_status": source.validation_status,
                },
                source_before,
            )
            self.assertEqual(item.version, 3)
            self.assertEqual(self._counts()[:2], (2, 2))
            decisions = tuple(fresh_after_c.scalars(
                select(ReviewDecision)
                .where(ReviewDecision.review_item_id == CASE_ID)
                .order_by(ReviewDecision.sequence)
            ))
            self.assertEqual([row.decision_type for row in decisions], ["corrected", "reverted"])
            self.assertEqual(
                ValidatedReadService(fresh_after_c).production_views(
                    period_id=self.period_id, visibility="eligible"
                ),
                [],
            )
            reverted_kpi = KpiService(fresh_after_c).dashboard("2099", 1, self.career_id)
            self.assertEqual(reverted_kpi.scientific_output_kpi_eligible, 0)
            self.assertEqual(
                (initial_kpi.scientific_output_kpi_eligible,
                 applied_kpi.scientific_output_kpi_eligible,
                 reverted_kpi.scientific_output_kpi_eligible),
                (0, 1, 0),
            )

    def test_two_real_sessions_have_one_http_winner_and_one_closed_409_loser(self) -> None:
        ready = Barrier(2)
        def submit(index: int):
            body = self._apply_body(correlation_id=UUID(int=100 + index))
            with TestClient(self.app, raise_server_exceptions=False) as client:
                # Both clients are ready before either command can acquire locks.
                ready.wait(timeout=15)
                return client.post(
                    f"/api/v1/human-review/cases/{CASE_ID}/apply", json=body
                )

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = tuple(executor.map(submit, (1, 2)))

        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        loser = next(response for response in responses if response.status_code == 409)
        self.assertEqual(loser.json()["code"], "REVIEW_CASE_VERSION_CONFLICT")
        self._assert_sanitized(loser.json())
        with Session(self.engine) as observer:
            item = observer.get(ReviewItem, CASE_ID)
            self.assertEqual((item.version, item.case_status), (2, "resolved"))
            self.assertEqual(self._counts()[:2], (1, 1))

    def test_committed_decision_changes_global_revision_for_a_second_session(self) -> None:
        first = self.client.get("/api/v1/human-review/effective-data-revision")
        self.assertEqual(first.status_code, 200, first.text)
        before = first.json()["revision"]

        applied = self.client.post(
            f"/api/v1/human-review/cases/{CASE_ID}/apply",
            json=self._apply_body(),
        )
        self.assertEqual(applied.status_code, 200, applied.text)

        with Session(self.engine) as db, db.begin():
            observer = User(
                email=f"task14-observer-{uuid4().hex}@example.invalid",
                full_name="Task 14 Dashboard Observer",
                hashed_password="not-used",
                role=UserRole.FACULTY_ADMIN,
                faculty_id=self.faculty_id,
                is_active=True,
            )
            db.add(observer)
            db.flush()
            observer_id = observer.id

        self.active_actor_id = observer_id
        try:
            with TestClient(self.app, raise_server_exceptions=False) as second_session:
                observed = second_session.get("/api/v1/human-review/effective-data-revision")
        finally:
            self.active_actor_id = self.actor_id
        self.assertEqual(observed.status_code, 200, observed.text)
        self.assertEqual(observed.json(), {"revision": before + 1})

    def test_rollback_does_not_change_global_revision(self) -> None:
        before = self.client.get("/api/v1/human-review/effective-data-revision")
        self.assertEqual(before.status_code, 200, before.text)
        with patch.object(
            commands,
            "append_audit_event_at_current_head",
            side_effect=RuntimeError("controlled Task 14 failure"),
        ):
            failed = self.client.post(
                f"/api/v1/human-review/cases/{CASE_ID}/apply",
                json=self._apply_body(),
            )
        self.assertEqual(failed.status_code, 500, failed.text)
        after = self.client.get("/api/v1/human-review/effective-data-revision")
        self.assertEqual(after.status_code, 200, after.text)
        self.assertEqual(after.json(), before.json())

    def test_controlled_audit_failure_rolls_back_cas_decision_projection_and_kpi(self) -> None:
        before = self._counts()
        with patch.object(
            commands,
            "append_audit_event_at_current_head",
            side_effect=RuntimeError("controlled Task 14 failure"),
        ):
            response = self.client.post(
                f"/api/v1/human-review/cases/{CASE_ID}/apply",
                json=self._apply_body(),
            )
        self.assertEqual(response.status_code, 500, response.text)
        self.assertEqual(response.json()["code"], "HUMAN_REVIEW_INTERNAL_ERROR")
        self._assert_sanitized(response.json())
        self.assertEqual(self._counts(), before)
        with Session(self.engine) as observer:
            item = observer.get(ReviewItem, CASE_ID)
            self.assertEqual((item.version, item.case_status, item.current_decision_id), (1, "pending", None))
            self.assertEqual(
                ValidatedReadService(observer).production_views(
                    period_id=self.period_id, visibility="eligible"
                ),
                [],
            )
            dashboard = KpiService(observer).dashboard("2099", 1, self.career_id)
            self.assertEqual(dashboard.scientific_output_total, 0)
            self.assertEqual(dashboard.scientific_output_pending_review, 1)

    def test_authority_openapi_and_closed_error_contracts(self) -> None:
        schema = self.app.openapi()
        human_paths = {
            path: set(operations)
            for path, operations in schema["paths"].items()
            if path.startswith("/api/v1/human-review")
        }
        self.assertEqual(
            human_paths,
            {
                "/api/v1/human-review/cases": {"get"},
                "/api/v1/human-review/effective-data-revision": {"get"},
                "/api/v1/human-review/cases/{review_item_id}": {"get"},
                "/api/v1/human-review/cases/{review_item_id}/audit": {"get"},
                "/api/v1/human-review/cases/{review_item_id}/evidence": {"get"},
                "/api/v1/human-review/cases/{review_item_id}/related": {"get"},
                "/api/v1/human-review/me": {"get"},
                "/api/v1/human-review/cases/{review_item_id}/apply": {"post"},
                "/api/v1/human-review/cases/{review_item_id}/discard": {"post"},
                "/api/v1/human-review/cases/{review_item_id}/revert": {"post"},
            },
        )
        human_schema_markers = (
            "applydecision",
            "audit",
            "capabilit",
            "counterpart",
            "discard",
            "duplicate",
            "effective",
            "evidence",
            "humanreview",
            "kpieffect",
            "persondecision",
            "productdecision",
            "relationdecision",
            "revert",
            "reviewcase",
            "reviewqueue",
        )
        human_schemas = {
            name: value
            for name, value in schema["components"]["schemas"].items()
            if any(marker in name.casefold() for marker in human_schema_markers)
        }
        serialized = json.dumps(
            {
                "paths": {
                    path: schema["paths"][path]
                    for path in human_paths
                },
                "schemas": human_schemas,
            },
            sort_keys=True,
        ).casefold()
        for forbidden in ("proposals", "drafts", "reservations", "ownership", "document_key", "stable_target_key"):
            self.assertNotIn(forbidden, serialized)
        self.assertIn("document_id", serialized)
        self.assertIn("counterpart_ref", serialized)

        with Session(self.engine) as db, db.begin():
            out_of_scope_career = Career(
                name=f"Task14 Out-of-scope Career {uuid4().hex}",
                code=uuid4().hex[:12],
                faculty_id=self.faculty_id,
            )
            admin = User(
                email=f"task14-admin-{uuid4().hex}@example.invalid",
                full_name="Task 14 System Admin",
                hashed_password="not-used",
                role=UserRole.FACULTY_ADMIN,
                faculty_id=self.faculty_id,
                is_active=True,
            )
            career_manager = User(
                email=f"task14-career-{uuid4().hex}@example.invalid",
                full_name="Task 14 Career Manager",
                hashed_password="not-used",
                role=UserRole.CAREER_MANAGER,
                career=out_of_scope_career,
                is_active=True,
            )
            db.add_all((out_of_scope_career, admin, career_manager))
            db.flush()
            db.add(UserB2BCapability(
                user_id=admin.id,
                capability="SYSTEM_ADMIN",
                approval_reference="task14-admin",
                approved_input_sha256="a" * 64,
                assigned_by_identifier="task14-e2e",
            ))
            admin_id, career_id = admin.id, career_manager.id

        self.active_actor_id = admin_id
        self.assertEqual(self.client.get(f"/api/v1/human-review/cases/{CASE_ID}").status_code, 200)
        self.assertEqual(self.client.get(f"/api/v1/human-review/cases/{CASE_ID}/audit").status_code, 200)
        scoped_apply = self.client.post(
            f"/api/v1/human-review/cases/{CASE_ID}/apply", json=self._apply_body()
        )
        self.assertEqual(scoped_apply.status_code, 200)

        self.active_actor_id = career_id
        career_denied = self.client.get(f"/api/v1/human-review/cases/{CASE_ID}")
        self.assertEqual(career_denied.status_code, 403)
        self._assert_sanitized(career_denied.json())

        self.app.dependency_overrides.pop(dependencies.get_current_user)
        unauthenticated = self.client.get("/api/v1/human-review/cases")
        self.assertEqual(unauthenticated.status_code, 401)
        self._assert_sanitized(unauthenticated.json())

        self.active_actor_id = self.actor_id
        self.app.dependency_overrides[dependencies.get_current_user] = lambda: self._detached_actor(self.actor_id)
        missing = self.client.get(f"/api/v1/human-review/cases/{uuid4()}")
        invalid = self.client.post(
            f"/api/v1/human-review/cases/{CASE_ID}/apply",
            json={**self._apply_body(), "unexpected": "dropbox_path:/private/secret"},
        )
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(invalid.status_code, 422)
        self._assert_sanitized(missing.json())
        self._assert_sanitized(invalid.json())

    def _detached_actor(self, actor_id: int) -> User:
        with Session(self.engine) as db:
            actor = db.get(User, actor_id)
            assert actor is not None
            db.expunge(actor)
            return actor

    def test_evidence_cross_access_corruption_and_unavailability_are_safe_and_read_only(self) -> None:
        before = self._counts()
        missing = self.client.get(f"/api/v1/human-review/cases/{uuid4()}/evidence")
        self.assertEqual(missing.status_code, 404)
        self._assert_sanitized(missing.json())

        with Session(self.engine) as db, db.begin():
            item = db.get(ReviewItem, CASE_ID)
            item.row_or_block_id = "../../cross-document"
        traversal = self.client.get(f"/api/v1/human-review/cases/{CASE_ID}/evidence")
        self.assertEqual(traversal.status_code, 503)
        self._assert_sanitized(traversal.json())

        with Session(self.engine) as db, db.begin():
            item = db.get(ReviewItem, CASE_ID)
            item.row_or_block_id = "product:detected task 14 title"
            job = db.get(ImportJob, self.job_id)
            job.source_rev = "cross-revision"
        crossed = self.client.get(f"/api/v1/human-review/cases/{CASE_ID}/evidence")
        self.assertEqual(crossed.status_code, 503)
        self._assert_sanitized(crossed.json())
        self.assertEqual(self._counts(), before)

    def test_queue_explain_preserves_exact_order_pagination_and_existing_index_applicability(self) -> None:
        now = datetime(2099, 1, 1, tzinfo=UTC)
        with Session(self.engine) as db, db.begin():
            db.bulk_insert_mappings(ReviewItem, [
                {
                    "id": UUID(int=10_000 + index),
                    "case_type": "product",
                    "stable_target_key": f"b2b:v1:product:{index:064x}",
                    "target_table": "scientific_productions",
                    "target_pk": None,
                    "scope_faculty_id": self.faculty_id,
                    "scope_career_id": self.career_id,
                    "document_key": f"dropbox_path:/noise/{index}.pdf",
                    "source_revision": "noise",
                    "source_page": 1,
                    "source_section": "noise",
                    "row_or_block_id": f"noise-{index}",
                    "field_path": "case",
                    "raw_value_sha256": sha256(f"noise-{index}".encode()).hexdigest(),
                    "period_id": 1000 + (index % 10),
                    "relationship_key": None,
                    "case_status": "resolved",
                    "scientific_status": "validated",
                    "automatic_priority": index % 100,
                    "manual_priority": None,
                    "possible_kpi_impact": False,
                    "current_decision_id": None,
                    "version": 1,
                    "created_at": now,
                    "updated_at": now,
                }
                for index in range(1200)
            ])
            db.execute(text("ANALYZE review_items"))
            item = db.get(ReviewItem, CASE_ID)
            captured: list[tuple[str, object]] = []

            def capture_queue_statement(
                _connection, _cursor, statement, parameters, _context, _many
            ) -> None:
                normalized = " ".join(statement.casefold().split())
                if (
                    " from review_items " in f" {normalized} "
                    and "order by review_items.manual_priority desc nulls last" in normalized
                ):
                    captured.append((statement, parameters))

            event.listen(self.engine, "before_cursor_execute", capture_queue_statement)
            try:
                service = HumanReviewQueryService(db)
                page = service.list_cases(ReviewQueueQuery(
                    statuses=("pending",),
                    case_types=("product",),
                    period_id=self.period_id,
                    document_id=self.job_id,
                    source_revision="task14-revision-1",
                    created_from=item.created_at - timedelta(seconds=1),
                    created_to=item.created_at + timedelta(seconds=1),
                    q="task14",
                    page=1,
                    page_size=25,
                ))
            finally:
                event.remove(self.engine, "before_cursor_execute", capture_queue_statement)
            self.assertEqual(page.total, 1)
            self.assertEqual(page.items[0].id, CASE_ID)
            self.assertEqual(len(captured), 1)
            service_sql, service_parameters = captured[0]
            normalized_service_sql = " ".join(service_sql.casefold().split())
            for clause in (
                "review_items.case_status in",
                "review_items.case_type in",
                "review_items.period_id =",
                "review_items.target_table =",
                "review_items.source_revision =",
                "review_items.created_at >=",
                "review_items.created_at <=",
                "review_items.document_key ilike",
                "review_items.stable_target_key ilike",
                "manual_priority desc nulls last",
                "automatic_priority desc",
                "created_at asc",
                "review_items.id asc",
                " limit ",
                " offset ",
            ):
                self.assertIn(clause, f" {normalized_service_sql} ")

            order = (
                ReviewItem.manual_priority.desc().nulls_last(),
                ReviewItem.automatic_priority.desc(),
                ReviewItem.created_at.asc(),
                ReviewItem.id.asc(),
            )
            statements = {
                "ix_review_items_queue": select(ReviewItem).where(
                    ReviewItem.case_type == "product",
                    ReviewItem.case_status == "pending",
                ).order_by(*order).offset(0).limit(25),
                "ix_review_items_period": select(ReviewItem).where(
                    ReviewItem.period_id == self.period_id
                ).order_by(*order).offset(0).limit(25),
                "ix_review_items_document": select(ReviewItem).where(
                    ReviewItem.document_key == db.get(ReviewItem, CASE_ID).document_key
                ).order_by(*order).offset(0).limit(25),
            }
            db.execute(text("SET LOCAL enable_seqscan = off"))
            raw_connection = db.connection().connection
            with raw_connection.cursor() as cursor:
                cursor.execute(
                    f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {service_sql}",
                    service_parameters,
                )
                service_plan = cursor.fetchone()[0]
            # The full service query can legitimately choose another applicable
            # index as statistics and PostgreSQL versions change.  The focused
            # plans below prove each intended queue/filter index remains usable.
            service_plan_text = json.dumps(service_plan)
            self.assertTrue(
                any(index_name in service_plan_text for index_name in (
                    "ix_review_items_queue",
                    "ix_review_items_period",
                    "ix_review_items_document",
                    "ix_review_items_target",
                    "uq_review_items_active_case_target",
                    "ix_review_items_scope_faculty_queue",
                    "ix_review_items_scope_career_queue",
                )),
                service_plan_text,
            )
            for expected_index, statement in statements.items():
                sql = str(statement.compile(
                    dialect=self.engine.dialect,
                    compile_kwargs={"literal_binds": True},
                ))
                normalized = " ".join(sql.casefold().split())
                self.assertIn("manual_priority desc nulls last", normalized)
                self.assertIn("automatic_priority desc", normalized)
                self.assertIn("created_at asc", normalized)
                self.assertIn("review_items.id asc", normalized)
                plan = db.execute(text(
                    f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}"
                )).scalar_one()
                names: set[str] = set()

                def visit(node: object) -> None:
                    if isinstance(node, dict):
                        if isinstance(node.get("Index Name"), str):
                            names.add(node["Index Name"])
                        for value in node.values():
                            visit(value)
                    elif isinstance(node, list):
                        for value in node:
                            visit(value)

                visit(plan)
                self.assertIn(expected_index, names)


if __name__ == "__main__":
    unittest.main()
