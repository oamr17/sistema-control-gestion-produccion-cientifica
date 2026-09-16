from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.entities import Career, Faculty, User
from app.models.enums import UserRole
from app.models.human_review_access import UserB2BCapability
from app.models.human_review_audit import AuditEvent
from app.models.human_review_core import ReviewDecision, ReviewItem
from app.models.human_review_projection import FieldOverride
from app.schemas.human_review_api import ApplyDecisionRequest, ReviewCaseDetail
from app.services import human_review_commands as commands
from app.services.human_review_state import HumanReviewDomainError
from tests.support.postgres import isolated_postgres_schema, require_b2b1_test_database_url


@unittest.skipUnless(
    os.environ.get("B2B1_TEST_DATABASE_URL"),
    "requires disposable PostgreSQL 16 via B2B1_TEST_DATABASE_URL",
)
class HumanReviewCommandConcurrencyPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._schema = isolated_postgres_schema(
            require_b2b1_test_database_url(), "b2b2_task4"
        )
        cls.engine = cls._schema.__enter__()
        with cls.engine.connect() as connection:
            version = connection.execute(
                text("SELECT current_setting('server_version_num')::integer")
            ).scalar_one()
        if not 160000 <= version < 170000:
            cls._schema.__exit__(None, None, None)
            raise RuntimeError("Task 4 concurrency tests require PostgreSQL 16")
        Base.metadata.create_all(cls.engine)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._schema.__exit__(None, None, None)

    def setUp(self) -> None:
        with Session(self.engine) as db, db.begin():
            faculty = Faculty(name=f"Concurrency Faculty {uuid4().hex}")
            career = Career(
                name=f"Concurrency Career {uuid4().hex}",
                code=uuid4().hex[:12],
                faculty=faculty,
            )
            actor = User(
                email=f"manager-{uuid4().hex}@example.invalid",
                full_name="Research Manager",
                hashed_password="not-used",
                role=UserRole.FACULTY_ADMIN,
                faculty=faculty,
                is_active=True,
            )
            db.add_all((faculty, career, actor))
            db.flush()
            db.add(UserB2BCapability(
                user_id=actor.id,
                capability="RESEARCH_MANAGER",
                approval_reference="task-4-test",
                approved_input_sha256="a" * 64,
                assigned_by_identifier="task-4-test",
            ))
            item = ReviewItem(
                case_type="product",
                stable_target_key=f"b2b:v1:product:{uuid4().hex * 2}",
                target_table="scientific_productions",
                target_pk=41,
                scope_faculty_id=faculty.id,
                scope_career_id=career.id,
                document_key="document:fixture.pdf",
                source_revision="revision-1",
                source_page=1,
                source_section="fixture",
                row_or_block_id="row-41",
                field_path="product_title",
                raw_value_sha256="b" * 64,
                period_id=2026,
                case_status="pending",
                scientific_status="pending",
                version=1,
            )
            db.add(item)
            db.flush()
            self.actor_id = actor.id
            self.item_id = item.id

    def _request(self) -> ApplyDecisionRequest:
        return ApplyDecisionRequest(
            expected_version=1,
            expected_current_decision_id=None,
            action="approve",
            scope="record",
            payload={
                "case_type": "product",
                "product_title": "Concurrency-safe title",
                "scientific_status": "validated",
            },
            reason="Checked by two concurrent sessions",
            correlation_id=uuid4(),
        )

    def _detail(self, decision_id) -> ReviewCaseDetail:
        return ReviewCaseDetail(
            id=self.item_id,
            case_type="product",
            case_status="resolved",
            scientific_status="validated",
            document_id=None,
            source_revision="revision-1",
            source_page=1,
            source_section="fixture",
            automatic_priority=0,
            manual_priority=None,
            possible_kpi_impact=False,
            version=2,
            created_at=datetime.now(timezone.utc),
            target_table="scientific_productions",
            target_pk=41,
            field_path="product_title",
            detected_value="Old title",
            normalized_value="Old title",
            canonical_value="Concurrency-safe title",
            current_decision_id=decision_id,
            overrides=(),
            effective_memberships=(),
            evidence_summary={"available": False, "count": 0},
        )

    def test_same_version_has_one_winner_one_event_and_one_increment(self) -> None:
        ready = Barrier(2)
        def projected_get_case(service, item_id):
            return self._detail(
                service._db.get(ReviewItem, item_id).current_decision_id
            )

        def run(request):
            with Session(self.engine) as db:
                actor = db.get(User, self.actor_id)
                # Synchronize before the command acquires its advisory lock.
                ready.wait(timeout=15)
                try:
                    return commands.apply_decision(
                        db, actor, self.item_id, request
                    )
                except HumanReviewDomainError as error:
                    return error

        with (
            patch.object(
                commands.HumanReviewQueryService,
                "get_case",
                new=projected_get_case,
            ),
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = tuple(
                    executor.map(
                        run,
                        (self._request(), self._request()),
                    )
                )

        self.assertEqual(sum(not isinstance(row, Exception) for row in results), 1)
        loser = next(row for row in results if isinstance(row, Exception))
        self.assertEqual(loser.code, "REVIEW_CASE_VERSION_CONFLICT")
        with Session(self.engine) as observer:
            item = observer.get(ReviewItem, self.item_id)
            self.assertEqual((item.version, item.case_status), (2, "resolved"))
            self.assertEqual(
                observer.scalar(
                    select(func.count()).select_from(ReviewDecision).where(
                        ReviewDecision.review_item_id == self.item_id
                    )
                ),
                1,
            )
            self.assertEqual(
                observer.scalar(
                    select(func.count()).select_from(AuditEvent).where(
                        AuditEvent.review_item_id == self.item_id,
                        AuditEvent.event_type == "scientific_decision_applied"
                    )
                ),
                1,
            )

    def test_audit_failure_rolls_back_cas_decision_and_projection(self) -> None:
        request = self._request()
        with Session(self.engine) as db, patch.object(
            commands,
            "append_audit_event_at_current_head",
            side_effect=RuntimeError("injected audit failure"),
        ):
            actor = db.get(User, self.actor_id)
            with self.assertRaises(HumanReviewDomainError) as raised:
                commands.apply_decision(db, actor, self.item_id, request)
        self.assertEqual(raised.exception.code, "HUMAN_REVIEW_INTERNAL_ERROR")
        self.assertEqual(raised.exception.correlation_id, request.correlation_id)
        with Session(self.engine) as observer:
            item = observer.get(ReviewItem, self.item_id)
            self.assertEqual(
                (item.version, item.case_status, item.current_decision_id),
                (1, "pending", None),
            )
            self.assertEqual(
                observer.scalar(
                    select(func.count()).select_from(ReviewDecision).where(
                        ReviewDecision.review_item_id == self.item_id
                    )
                ),
                0,
            )
            self.assertEqual(
                observer.scalar(
                    select(func.count()).select_from(FieldOverride).where(
                        FieldOverride.review_item_id == self.item_id
                    )
                ),
                0,
            )
            self.assertEqual(
                observer.scalar(
                    select(func.count()).select_from(AuditEvent).where(
                        AuditEvent.review_item_id == self.item_id
                    )
                ),
                0,
            )

    def test_stale_current_pointer_and_missing_persisted_scope_leave_no_writes(self) -> None:
        stale = self._request().model_copy(
            update={"expected_current_decision_id": uuid4()}
        )
        with Session(self.engine) as db:
            actor = db.get(User, self.actor_id)
            with self.assertRaises(HumanReviewDomainError) as raised:
                commands.apply_decision(db, actor, self.item_id, stale)
        self.assertEqual(raised.exception.code, "REVIEW_CASE_VERSION_CONFLICT")

        with Session(self.engine) as db, db.begin():
            assignment = db.scalar(select(UserB2BCapability).where(
                UserB2BCapability.user_id == self.actor_id
            ))
            assignment.is_active = False
            assignment.revoked_at = datetime.now(timezone.utc)
            assignment.revocation_reason = "test denial"
            db.get(User, self.actor_id).faculty_id = None
        denied = self._request()
        with Session(self.engine) as db:
            actor = db.get(User, self.actor_id)
            with self.assertRaises(HumanReviewDomainError) as raised:
                commands.apply_decision(db, actor, self.item_id, denied)
        self.assertEqual(raised.exception.code, "B2B_CAPABILITY_REQUIRED")
        with Session(self.engine) as observer:
            item = observer.get(ReviewItem, self.item_id)
            self.assertEqual((item.version, item.current_decision_id), (1, None))
            self.assertEqual(
                observer.scalar(
                    select(func.count()).select_from(ReviewDecision).where(
                        ReviewDecision.review_item_id == self.item_id
                    )
                ),
                0,
            )

    def test_success_commits_once_and_returns_a_closed_typed_response(self) -> None:
        with Session(self.engine) as setup, setup.begin():
            setup.get(ReviewItem, self.item_id).possible_kpi_impact = True
        request = self._request()
        with Session(self.engine) as db:
            actor = db.get(User, self.actor_id)
            with (
                patch.object(db, "commit", wraps=db.commit) as commit,
                patch.object(
                    commands.HumanReviewQueryService,
                    "get_case",
                    side_effect=lambda item_id: self._detail(
                        db.get(ReviewItem, item_id).current_decision_id
                    ),
                ),
            ):
                response = commands.apply_decision(
                    db, actor, self.item_id, request
                )
            self.assertEqual(commit.call_count, 1)
        self.assertEqual(response.correlation_id, request.correlation_id)
        self.assertEqual(
            response.kpi_effect.model_dump(mode="json"),
            {"affected": []},
        )
        serialized = repr(response.model_dump(mode="json")).lower()
        for forbidden in ("document:fixture", "storage_path", "actor_identifier"):
            self.assertNotIn(forbidden, serialized)
        with Session(self.engine) as observer:
            event = observer.scalar(select(AuditEvent).where(
                AuditEvent.review_item_id == self.item_id,
                AuditEvent.event_type == "scientific_decision_applied",
            ))
            self.assertEqual(
                event.payload["kpi_effect"],
                response.kpi_effect.model_dump(mode="json")["affected"],
            )

    def test_response_projection_failure_rolls_back_every_pending_write(self) -> None:
        request = self._request()
        with Session(self.engine) as db, patch.object(
            commands.HumanReviewQueryService,
            "get_case",
            side_effect=RuntimeError("injected response projection failure"),
        ):
            actor = db.get(User, self.actor_id)
            with self.assertRaises(HumanReviewDomainError) as raised:
                commands.apply_decision(db, actor, self.item_id, request)
        self.assertEqual(raised.exception.code, "HUMAN_REVIEW_INTERNAL_ERROR")
        with Session(self.engine) as observer:
            item = observer.get(ReviewItem, self.item_id)
            self.assertEqual(
                (item.version, item.case_status, item.current_decision_id),
                (1, "pending", None),
            )
            self.assertEqual(
                observer.scalar(
                    select(func.count()).select_from(ReviewDecision).where(
                        ReviewDecision.review_item_id == self.item_id
                    )
                ),
                0,
            )
            self.assertEqual(
                observer.scalar(
                    select(func.count()).select_from(FieldOverride).where(
                        FieldOverride.review_item_id == self.item_id
                    )
                ),
                0,
            )
            self.assertEqual(
                observer.scalar(
                    select(func.count()).select_from(AuditEvent).where(
                        AuditEvent.review_item_id == self.item_id
                    )
                ),
                0,
            )


if __name__ == "__main__":
    unittest.main()
