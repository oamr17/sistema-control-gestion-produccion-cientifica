from __future__ import annotations

import inspect
import hashlib
import os
from typing import get_type_hints
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, text

from app.models.entities import (
    AcademicPeriod,
    ExternalResearcher,
    ImportJob,
    PersonRole,
    ResearchEntity,
    ScientificProduction,
    ScientificProductionAuthor,
)
from app.models.enums import ProductionType
from app.models.human_review_audit import AuditEvent
from app.models.human_review_enums import (
    ReviewCaseStatus,
    ReviewCaseType,
    ReviewTargetTable,
)
from app.models.human_review_core import ReviewDecision, ReviewItem
from app.models.human_review_projection import FieldOverride
from app.core.database import Base
from app.schemas.human_review_api import ReviewQueueQuery
from app.services.human_review_queries import (
    EvidenceUnavailableError,
    HumanReviewQueryInternalError,
    HumanReviewQueryService,
    _QueueItemContext,
    ReviewCaseNotFoundError,
)
from app.services.human_review_state import HumanReviewDomainError, IncompatibleDecisionError
from app.services.human_review_targets import raw_value_sha256


class HumanReviewQueryServiceContractTests(unittest.TestCase):
    """Public read-service behavior, kept database-free for the import contract."""

    def test_service_accepts_session_optional_actor_and_correlation_id(self) -> None:
        signature = inspect.signature(HumanReviewQueryService)
        self.assertEqual(tuple(signature.parameters), ("db", "actor", "correlation_id"))
        self.assertIs(get_type_hints(HumanReviewQueryService.list_cases)["query"], ReviewQueueQuery)
        self.assertNotIn("kwargs", signature.parameters)

    def test_queue_query_keeps_the_filter_surface_closed(self) -> None:
        for forbidden in ("career_id", "confidence", "has_evidence", "document_key"):
            with self.assertRaises(ValueError):
                ReviewQueueQuery.model_validate({forbidden: True})

    def test_queue_query_accepts_only_approved_filters(self) -> None:
        query = ReviewQueueQuery(
            statuses=(ReviewCaseStatus.PENDING,),
            case_types=(ReviewCaseType.PRODUCT,),
            period_id=2026,
            document_id=501,
            source_revision="rev-a",
            created_from=datetime(2026, 7, 1, tzinfo=UTC),
            created_to=datetime(2026, 7, 31, tzinfo=UTC),
            q="needle",
        )
        self.assertEqual(query.sort, "priority_oldest")

    def test_queue_query_rejects_naive_creation_bounds(self) -> None:
        with self.assertRaises(ValueError):
            ReviewQueueQuery(created_from=datetime(2026, 7, 1))
        with self.assertRaises(ValueError):
            ReviewQueueQuery(created_to=datetime(2026, 7, 31))

    def test_public_schema_uses_document_id_and_effective_memberships(self) -> None:
        from app.schemas.human_review_api import ReviewCaseDetail, ReviewQueueItem

        self.assertIn("document_id", ReviewQueueItem.model_fields)
        self.assertIn("document_name", ReviewQueueItem.model_fields)
        self.assertIn("detected_value", ReviewQueueItem.model_fields)
        self.assertIn("normalized_value", ReviewQueueItem.model_fields)
        self.assertIn("canonical_value", ReviewQueueItem.model_fields)
        self.assertNotIn("document_key", ReviewQueueItem.model_fields)
        self.assertIn("effective_memberships", ReviewCaseDetail.model_fields)
        self.assertNotIn("memberships", ReviewCaseDetail.model_fields)
        self.assertIn("counterpart_options", ReviewCaseDetail.model_fields)
        schema = ReviewCaseDetail.model_json_schema()
        self.assertIn("currently derivable", schema["properties"]["effective_memberships"]["description"])

    def test_queue_search_matches_public_case_identifier_and_context(self) -> None:
        service = HumanReviewQueryService(object())

        statement = select(ReviewItem).where(
            *service._queue_predicates(
                ReviewQueueQuery(q="00000000-0000-0000-0000-000000000101")
            )
        )
        compiled = str(statement.compile(compile_kwargs={"literal_binds": True}))

        self.assertIn("review_items.id", compiled)
        self.assertIn("source_revision", compiled)
        self.assertIn("source_section", compiled)
        self.assertIn("import_jobs.filename", compiled)
        self.assertIn("raw_value", compiled)
        self.assertIn("stable_target_key", compiled)

    def test_source_revision_filter_matches_the_exact_revision(self) -> None:
        service = HumanReviewQueryService(object())

        statement = select(ReviewItem).where(
            *service._queue_predicates(
                ReviewQueueQuery(source_revision="01653ebdfcd0d2f000000032b1fbdd1")
            )
        )
        compiled = str(statement.compile(compile_kwargs={"literal_binds": True}))

        self.assertIn("review_items.source_revision =", compiled)
        self.assertNotIn("LIKE", compiled)

    def test_queue_items_expose_author_and_product_context_without_internal_locators(self) -> None:
        service = HumanReviewQueryService(object())

        def public_item(suffix: int, case_type: str, section: str) -> SimpleNamespace:
            return SimpleNamespace(
                id=UUID(f"00000000-0000-0000-0000-{suffix:012d}"),
                case_type=case_type,
                case_status="pending",
                scientific_status="pending",
                source_revision="rev-public",
                source_page=7,
                source_section=section,
                automatic_priority=10,
                manual_priority=None,
                possible_kpi_impact=True,
                version=1,
                created_at=datetime(2026, 8, 24, tzinfo=UTC),
            )

        author = service._queue_item(
            public_item(101, "author_identity", "Autores"),
            _QueueItemContext(
                document_id=501,
                document_name="safe-report.pdf",
                detected_value="Ada Lovelace",
                normalized_value="ada lovelace",
                canonical_value=None,
            ),
        )
        product = service._queue_item(
            public_item(102, "product", "Producción científica"),
            _QueueItemContext(
                document_id=501,
                document_name="safe-report.pdf",
                detected_value="Artículo de investigación",
                normalized_value="artículo de investigación",
                canonical_value=None,
            ),
        )

        self.assertEqual(
            [(item.case_type.value, item.document_name, item.source_page, item.source_section, item.detected_value, item.case_status.value) for item in (author, product)],
            [
                ("author_identity", "safe-report.pdf", 7, "Autores", "Ada Lovelace", "pending"),
                ("product", "safe-report.pdf", 7, "Producción científica", "Artículo de investigación", "pending"),
            ],
        )
        serialized = "".join(item.model_dump_json().casefold() for item in (author, product))
        for forbidden in ("document_key", "stable_target_key", "dropbox_path", "source_path", "object_key", "traceback"):
            self.assertNotIn(forbidden, serialized)

    def test_get_case_builds_public_base_with_queue_context(self) -> None:
        item = SimpleNamespace(
            id=UUID("a7f95f33-c5c7-5817-8b23-b71269734ffa"),
            case_type="author_identity",
            target_table="scientific_production_authors",
            target_pk=668,
            document_key="dropbox:id:Kr7Hn2Cu9k0AAAAAAAAEAg",
            source_revision="01653ebdfcd0d15000000032b1fbdd1",
            source_page=5,
            source_section="produccion_cientifica",
            row_or_block_id="produccion_cientifica:1",
            field_path="case",
            raw_value_sha256=raw_value_sha256("Rafael Apolinario"),
            period_id=2026,
            relationship_key=None,
            case_status="pending",
            scientific_status="pending",
            automatic_priority=10,
            manual_priority=None,
            possible_kpi_impact=True,
            current_decision_id=None,
            version=1,
            created_at=datetime(2026, 7, 27, 10, 0, tzinfo=UTC),
        )
        context = _QueueItemContext(
            document_id=234,
            document_name="GI001-2024 GINFAES InfSem Junio2025-signed-signed.pdf",
            detected_value="Rafael Apolinario",
            normalized_value="Rafael Apolinario",
            canonical_value="Rafael Emiliano Apolinario Quintana",
        )

        class NoAutoflush:
            def __enter__(self):
                return None

            def __exit__(self, exc_type, exc, traceback):
                return False

        class FakeDb:
            no_autoflush = NoAutoflush()

            @staticmethod
            def get(model, key):
                if model is ReviewItem and key == item.id:
                    return item
                return SimpleNamespace(filename=context.document_name)

        service = HumanReviewQueryService(FakeDb())
        with (
            patch.object(service, "_target_for_detail", return_value=(ReviewTargetTable.SCIENTIFIC_PRODUCTION_AUTHORS, object())),
            patch.object(service, "_target_values", return_value=("Rafael Apolinario", "Rafael Apolinario", "Rafael Emiliano Apolinario Quintana")),
            patch.object(service, "_current_decision", return_value=(None, "Rafael Emiliano Apolinario Quintana", False)),
            patch.object(service, "_safe_overrides", return_value=()),
            patch.object(service, "_target_import_job_id", return_value=234),
            patch.object(service, "_queue_context", return_value={item.id: context}),
            patch.object(service, "_resolve_evidence", side_effect=EvidenceUnavailableError(uuid4())),
            patch.object(service, "_evidence_summary", return_value={"available": False, "count": 0}),
            patch("app.services.human_review_queries.public_counterpart_options", return_value=()),
        ):
            detail = service.get_case(item.id)

        self.assertEqual(detail.document_id, 234)
        self.assertEqual(detail.document_name, context.document_name)
        self.assertEqual(detail.detected_value, "Rafael Apolinario")

    def test_public_counterpart_resolver_maps_all_five_target_types(self) -> None:
        from app.services.human_review_counterparts import TARGET_MODELS

        self.assertEqual(set(TARGET_MODELS), set(ReviewTargetTable))

    def test_public_counterpart_options_are_batched_and_capped_deterministically(self) -> None:
        from app.services.human_review_counterparts import public_counterpart_options
        from app.services.validated_read_service import ValidatedReadService

        item = SimpleNamespace(
            id=UUID("00000000-0000-0000-0000-000000000001"),
            case_type="possible_duplicate",
            target_table="person_roles",
            target_pk=1,
        )
        matches = [
            {"canonical_identity_key": f"candidate:{target_id}"}
            for target_id in range(2, 103)
        ]
        participants = [{
            "canonical_identity_key": "owner",
            "canonical_name": "Owner",
            "variants": [{"source_table": "person_roles", "source_id": 1}],
            "possible_matches": matches,
        }]
        participants.extend(
            {
                "canonical_identity_key": f"candidate:{target_id}",
                "canonical_name": f"Candidate {target_id:03d}",
                "variants": [{
                    "source_table": "person_roles",
                    "source_id": target_id,
                    "raw_name": f"Candidate {target_id:03d}",
                    "document": f"dropbox_path:/private/{target_id}.pdf",
                }],
                "possible_matches": [],
            }
            for target_id in range(2, 103)
        )
        duplicate_items = [
            SimpleNamespace(
                id=UUID(f"00000000-0000-0000-0000-{target_id:012d}"),
                target_table="person_roles",
                target_pk=target_id,
                stable_target_key=f"b2b:v1:person_identity:{target_id}",
            )
            for target_id in range(2, 103)
        ]
        db = MagicMock()
        db.scalars.side_effect = [range(2, 103), duplicate_items]

        with patch.object(
            ValidatedReadService,
            "canonical_participants",
            return_value=participants,
        ):
            options = public_counterpart_options(db, item)

        self.assertEqual(len(options), 100)
        self.assertEqual(options[0].counterpart_ref.target_id, 2)
        self.assertEqual(options[-1].counterpart_ref.target_id, 101)
        self.assertEqual(db.scalars.call_count, 2)
        db.get.assert_not_called()

    def test_service_is_read_only_by_source_contract(self) -> None:
        source = inspect.getsource(HumanReviewQueryService)
        for forbidden in ("self._db.add(", "self._db.delete(", "self._db.commit(", "self._db.flush(", "with_for_update"):
            self.assertNotIn(forbidden, source)

    def test_effective_memberships_do_not_retain_resolved_pending_state(self) -> None:
        service = HumanReviewQueryService(object())
        resolved_product = SimpleNamespace(
            case_type="product", case_status="resolved", scientific_status="validated"
        )
        pending_product = SimpleNamespace(
            case_type="product", case_status="pending", scientific_status="pending"
        )
        self.assertEqual(
            service._effective_memberships(
                resolved_product, identity_locked=False
            ),
            (),
        )
        self.assertEqual(
            service._effective_memberships(
                pending_product, identity_locked=False
            ),
            ("product_pending",),
        )

    def test_identity_locked_membership_requires_effective_locked_projection(self) -> None:
        service = HumanReviewQueryService(object())
        resolved_identity = SimpleNamespace(
            case_type="person_identity",
            case_status="resolved",
            scientific_status="validated",
        )
        self.assertEqual(
            service._effective_memberships(
                resolved_identity, identity_locked=False
            ),
            (),
        )
        self.assertEqual(
            service._effective_memberships(
                resolved_identity, identity_locked=True
            ),
            ("identity_locked",),
        )


@unittest.skipUnless(
    os.environ.get("B2B1_TEST_DATABASE_URL"),
    "requires the disposable PostgreSQL URL described by the Task 3 brief",
)
class HumanReviewQueryServicePostgresTests(unittest.TestCase):
    """Runs only against an explicitly supplied disposable PostgreSQL database."""

    @classmethod
    def setUpClass(cls) -> None:
        from tests.support.postgres import isolated_postgres_schema, require_b2b1_test_database_url

        cls._schema = isolated_postgres_schema(require_b2b1_test_database_url(), "b2b2_queries")
        cls.engine = cls._schema.__enter__()
        with cls.engine.connect() as connection:
            settings = connection.execute(text(
                "SELECT current_database(), current_setting('application_name'), "
                "current_setting('server_version_num')::integer"
            )).one()
        if (
            settings[0] != "b2b2_task3"
            or settings[1] not in {
                "b2b2_task3_disposable",
                "b2b1_disposable_migration_test",
            }
            or not 160000 <= settings[2] < 170000
        ):
            cls._schema.__exit__(None, None, None)
            raise RuntimeError(
                "Task 3 tests require the guarded disposable PostgreSQL 16 database"
            )
        Base.metadata.create_all(cls.engine)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._schema.__exit__(None, None, None)

    def setUp(self) -> None:
        from sqlalchemy.orm import Session

        self.db = Session(self.engine)

    def tearDown(self) -> None:
        self.db.rollback()
        self.db.close()

    @staticmethod
    def _item(
        suffix: int,
        *,
        case_type: str = "person_identity",
        target_table: str = "person_roles",
        target_pk: int | None = None,
        document_key: str | None = None,
        source_revision: str | None = "rev-a",
        period_id: int | None = 2026,
        case_status: str = "pending",
        scientific_status: str = "pending",
        automatic_priority: int = 10,
        manual_priority: int | None = None,
        created_at: datetime | None = None,
        detected: str = "source",
    ) -> ReviewItem:
        item_id = UUID(f"00000000-0000-0000-0000-{suffix:012d}")
        stable_case = (
            case_type
            if case_type
            in {
                "person_identity",
                "author_identity",
                "product",
                "project_director_relation",
                "external_identity",
            }
            else "person_identity"
        )
        return ReviewItem(
            id=item_id,
            case_type=case_type,
            stable_target_key=f"b2b:v1:{stable_case}:{suffix:x}".ljust(64 + len(f"b2b:v1:{stable_case}:"), "a"),
            target_table=target_table,
            target_pk=target_pk,
            scope_resolution_reason="unresolved_no_persisted_scope",
            document_key=document_key or f"dropbox_path:/reports/{suffix}.pdf",
            source_revision=source_revision,
            source_page=1,
            source_section="public section",
            row_or_block_id=f"row-{suffix}",
            field_path="case",
            raw_value_sha256=raw_value_sha256(detected),
            period_id=period_id,
            case_status=case_status,
            scientific_status=scientific_status,
            automatic_priority=automatic_priority,
            manual_priority=manual_priority,
            possible_kpi_impact=False,
            version=1,
            created_at=created_at or datetime(2026, 7, 27, 10, 0, tzinfo=UTC),
        )

    def _seed_queue_matrix(self) -> tuple[UUID, ...]:
        rows = (
            self._item(1, manual_priority=9, automatic_priority=10, created_at=datetime(2026, 7, 27, 10, 5, tzinfo=UTC), document_key="dropbox_path:/reports/needle-doc.pdf"),
            self._item(2, case_type="product", target_table="scientific_productions", manual_priority=9, automatic_priority=90, created_at=datetime(2026, 7, 27, 10, 4, tzinfo=UTC), case_status="in_review"),
            self._item(3, manual_priority=7, automatic_priority=80, created_at=datetime(2026, 7, 27, 10, 3, tzinfo=UTC), case_status="resolved", scientific_status="validated", period_id=2025, document_key="dropbox_path:/reports/100%_done.pdf", source_revision="rev-b"),
            self._item(4, case_type="project_director_relation", target_table="research_entities", manual_priority=7, automatic_priority=80, created_at=datetime(2026, 7, 27, 10, 1, tzinfo=UTC), document_key="dropbox_path:/reports/shared.pdf", source_revision="rev-b"),
            self._item(5, case_type="product", target_table="scientific_productions", manual_priority=7, automatic_priority=80, created_at=datetime(2026, 7, 27, 10, 1, tzinfo=UTC), case_status="conflicted", document_key="dropbox_path:/reports/shared.pdf", source_revision="rev-b"),
            self._item(6, case_type="invalid_text", manual_priority=0, automatic_priority=100, period_id=None, source_revision=None),
            self._item(7, manual_priority=None, automatic_priority=100, created_at=datetime(2026, 7, 27, 9, 0, tzinfo=UTC), case_status="reopened", period_id=2025, source_revision="rev-c"),
            self._item(8, case_type="external_identity", target_table="external_researchers", manual_priority=None, automatic_priority=100, created_at=datetime(2026, 7, 27, 9, 0, tzinfo=UTC), case_status="superseded", scientific_status="discarded", period_id=2025, source_revision="rev-c"),
            self._item(9, case_type="new_evidence_conflict", manual_priority=None, automatic_priority=99, created_at=datetime(2026, 7, 27, 8, 0, tzinfo=UTC), case_status="awaiting_gestor_approval", document_key="dropbox_path:/reports/100XXdone.pdf"),
        )
        self.db.add_all(rows)
        self.db.flush()
        return tuple(row.id for row in rows)

    def test_list_orders_all_keys_and_paginates_after_counting(self) -> None:
        ids = self._seed_queue_matrix()
        service = HumanReviewQueryService(self.db)

        first = service.list_cases(ReviewQueueQuery(page=1, page_size=3))
        second = service.list_cases(ReviewQueueQuery(page=2, page_size=3))
        fourth = service.list_cases(ReviewQueueQuery(page=4, page_size=3))

        expected = (ids[1], ids[0], ids[3], ids[4], ids[2], ids[5], ids[6], ids[7], ids[8])
        self.assertEqual(tuple(item.id for item in first.items), expected[:3])
        self.assertEqual(tuple(item.id for item in second.items), expected[3:6])
        self.assertEqual(fourth.items, ())
        self.assertEqual((first.total, second.total, fourth.total), (9, 9, 9))

    def test_list_applies_only_approved_filters_and_literal_search(self) -> None:
        ids = self._seed_queue_matrix()
        service = HumanReviewQueryService(self.db)

        cases = (
            (ReviewQueueQuery(statuses=(ReviewCaseStatus.PENDING, ReviewCaseStatus.CONFLICTED), period_id=2026), (ids[0], ids[3], ids[4])),
            (ReviewQueueQuery(case_types=(ReviewCaseType.PRODUCT,)), (ids[1], ids[4])),
            (ReviewQueueQuery(period_id=2025), (ids[2], ids[6], ids[7])),
            (ReviewQueueQuery(source_revision="rev-b"), (ids[3], ids[4], ids[2])),
            (ReviewQueueQuery(created_from=datetime(2026, 7, 27, 10, 1, tzinfo=UTC), created_to=datetime(2026, 7, 27, 10, 3, tzinfo=UTC)), (ids[3], ids[4], ids[2])),
            (ReviewQueueQuery(q="NEEDLE"), (ids[0],)),
            (ReviewQueueQuery(q="project_director"), (ids[3],)),
            (ReviewQueueQuery(q="100%_"), (ids[2],)),
            (ReviewQueueQuery(q="x%' --"), ()),
        )
        for query, expected in cases:
            with self.subTest(query=query):
                actual = service.list_cases(query)
                self.assertEqual(tuple(item.id for item in actual.items), expected)
                self.assertNotIn("document_key", repr(actual.model_dump(mode="json")))
        with self.assertRaises(HumanReviewDomainError):
            service.list_cases(ReviewQueueQuery(
                created_from=datetime(2026, 7, 28, tzinfo=UTC),
                created_to=datetime(2026, 7, 27, tzinfo=UTC),
            ))

    def test_list_filters_source_revision_by_exact_value(self) -> None:
        matching = self._item(
            31,
            source_revision="01653ebdfcd0d2f000000032b1fbdd1",
            document_key="dropbox_path:/reports/matching.pdf",
        )
        unrelated = self._item(
            32,
            source_revision="rev-unrelated",
            document_key="dropbox_path:/reports/unrelated.pdf",
        )
        self.db.add_all((matching, unrelated))
        self.db.flush()

        response = HumanReviewQueryService(self.db).list_cases(
            ReviewQueueQuery(source_revision="01653ebdfcd0d2f000000032b1fbdd1")
        )

        self.assertEqual(tuple(item.id for item in response.items), (matching.id,))

        partial = HumanReviewQueryService(self.db).list_cases(
            ReviewQueueQuery(source_revision="53EBDFC")
        )
        self.assertEqual(partial.items, ())

    def test_facets_cover_the_fully_filtered_population_with_zeros(self) -> None:
        self._seed_queue_matrix()
        service = HumanReviewQueryService(self.db)
        first = service.list_cases(ReviewQueueQuery(period_id=2026, page=1, page_size=2))
        second = service.list_cases(ReviewQueueQuery(period_id=2026, page=2, page_size=2))

        self.assertEqual(first.total, 5)
        self.assertEqual(first.facets, second.facets)
        self.assertEqual(
            dict(first.facets["statuses"]),
            {
                "pending": 2,
                "in_review": 1,
                "awaiting_gestor_approval": 1,
                "resolved": 0,
                "reopened": 0,
                "conflicted": 1,
                "superseded": 0,
            },
        )
        self.assertEqual(first.facets["case_types"]["product"], 2)
        self.assertEqual(first.facets["case_types"]["invalid_text"], 0)

    def _seed_typed_targets(self) -> tuple[ReviewItem, ...]:
        self.db.add(AcademicPeriod(id=2026, year_label="2026", cycle=1))
        self.db.add(ImportJob(
            id=501,
            source_type="dropbox",
            filename="safe-report.pdf",
            status="COMPLETED",
            document_key="dropbox_path:/private/safe-report.pdf",
            is_current=True,
        ))
        production = ScientificProduction(
            id=102,
            period_id=2026,
            production_type=ProductionType.ARTICLE,
            title="Product canonical",
            raw_title="Product detected",
            normalized_title="product normalized",
            import_job_id=501,
        )
        targets = (
            PersonRole(
                id=101,
                period_id=2026,
                import_job_id=501,
                role_type="researcher",
                person_type="teacher",
                raw_value="Person detected",
                normalized_value="person normalized",
                canonical_name="Person canonical",
                validation_status="pending_review",
                source_page=9,
                source_section="target person section",
            ),
            production,
            ScientificProductionAuthor(
                id=103,
                production=production,
                author_order=1,
                author_type="internal",
                raw_author_name="Author detected",
                normalized_author_name="author normalized",
                canonical_name="Author canonical",
                import_job_id=501,
            ),
            ResearchEntity(
                id=104,
                period_id=2026,
                type="project",
                raw_value="Director detected",
                normalized_value="director normalized",
                director_name="Director canonical",
                import_job_id=501,
            ),
            ExternalResearcher(
                id=105,
                period_id=2026,
                import_job_id=501,
                full_name="External canonical",
                normalized_name="external normalized",
                institution="Public University",
                normalized_institution="public university",
                raw_value="External detected",
                normalized_value="external normalized",
                requires_review=True,
            ),
        )
        self.db.add_all(targets)
        self.db.flush()
        item_specs = (
            (101, "person_identity", "person_roles", 101, "Person detected"),
            (102, "product", "scientific_productions", 102, "Product detected"),
            (103, "author_identity", "scientific_production_authors", 103, "Author detected"),
            (104, "project_director_relation", "research_entities", 104, "Director detected"),
            (105, "external_identity", "external_researchers", 105, "External detected"),
        )
        items = tuple(
            self._item(
                suffix,
                case_type=case_type,
                target_table=target_table,
                target_pk=target_pk,
                document_key="dropbox_path:/private/safe-report.pdf",
                detected=detected,
            )
            for suffix, case_type, target_table, target_pk, detected in item_specs
        )
        self.db.add_all(items)
        self.db.flush()
        return items

    def test_list_filters_all_target_types_by_public_document_id(self) -> None:
        items = self._seed_typed_targets()
        service = HumanReviewQueryService(self.db)

        matching = service.list_cases(ReviewQueueQuery(document_id=501))
        missing = service.list_cases(ReviewQueueQuery(document_id=999))

        self.assertEqual({item.id for item in matching.items}, {item.id for item in items})
        self.assertEqual(matching.total, 5)
        self.assertEqual(missing.items, ())
        self.assertEqual(missing.total, 0)

    def test_get_case_maps_all_five_typed_targets_without_internal_paths(self) -> None:
        items = self._seed_typed_targets()
        service = HumanReviewQueryService(self.db)

        details = tuple(service.get_case(item.id) for item in items)

        self.assertEqual(
            tuple(detail.target_table.value for detail in details),
            (
                "person_roles",
                "scientific_productions",
                "scientific_production_authors",
                "research_entities",
                "external_researchers",
            ),
        )
        self.assertEqual(
            tuple(detail.detected_value for detail in details),
            (
                "Person detected",
                "Product detected",
                "Author detected",
                "Director detected",
                "External detected",
            ),
        )
        for detail in details:
            dumped = detail.model_dump(mode="json")
            self.assertEqual(dumped["document_id"], 501)
            self.assertEqual(dumped["evidence_summary"]["document_name"], "safe-report.pdf")
            self.assertNotIn("document_key", dumped)
            self.assertNotIn("dropbox_path:", repr(dumped))
        self.assertEqual(details[0].evidence_summary["page"], 9)
        self.assertEqual(
            details[0].evidence_summary["section"], "target person section"
        )

    def test_get_case_derives_public_duplicate_options_without_stable_keys(self) -> None:
        self.db.add(AcademicPeriod(id=2031, year_label="2031", cycle=1))
        self.db.add_all((
            ImportJob(
                id=601,
                source_type="dropbox",
                filename="first.pdf",
                status="SUCCESS",
                document_key="dropbox_path:/private/first.pdf",
                is_current=True,
            ),
            ImportJob(
                id=602,
                source_type="dropbox",
                filename="second.pdf",
                status="SUCCESS",
                document_key="dropbox_path:/private/second.pdf",
                is_current=True,
            ),
        ))
        roles = (
            PersonRole(
                id=601,
                period_id=2031,
                import_job_id=601,
                role_type="researcher",
                person_type="teacher",
                raw_value="Ada Example",
                normalized_value="ada example",
                canonical_name="Ada Example",
                canonical_identity_key="pending:first",
                validation_status="pending_review",
                source_page=1,
                source_section="People",
            ),
            PersonRole(
                id=602,
                period_id=2031,
                import_job_id=602,
                role_type="researcher",
                person_type="teacher",
                raw_value="Ada Example",
                normalized_value="ada example",
                canonical_name="Ada Example",
                canonical_identity_key="pending:second",
                validation_status="pending_review",
                source_page=2,
                source_section="People",
            ),
        )
        self.db.add_all(roles)
        self.db.flush()
        items = (
            self._item(
                601,
                case_type="possible_duplicate",
                target_table="person_roles",
                target_pk=601,
                document_key="dropbox_path:/private/first.pdf",
                period_id=2031,
                detected="Ada Example",
            ),
            self._item(
                602,
                case_type="possible_duplicate",
                target_table="person_roles",
                target_pk=602,
                document_key="dropbox_path:/private/second.pdf",
                period_id=2031,
                detected="Ada Example",
            ),
        )
        self.db.add_all(items)
        self.db.flush()

        detail = HumanReviewQueryService(self.db).get_case(items[0].id)

        self.assertEqual(len(detail.counterpart_options), 1)
        option = detail.counterpart_options[0]
        self.assertEqual(option.counterpart_ref.target_type.value, "person_roles")
        self.assertEqual(option.counterpart_ref.target_id, 602)
        self.assertEqual(option.display_name, "Ada Example")
        serialized = detail.model_dump_json()
        self.assertNotIn("stable_target_key", serialized)
        self.assertNotIn("dropbox_path", serialized)

        from app.schemas.human_review_api import CounterpartReference
        from app.services.human_review_counterparts import resolve_duplicate_counterpart

        resolved = resolve_duplicate_counterpart(
            self.db,
            items[0],
            option.counterpart_ref,
            UUID("00000000-0000-0000-0000-000000009901"),
        )
        self.assertEqual(resolved.id, items[1].id)
        with self.assertRaises(IncompatibleDecisionError) as raised:
            resolve_duplicate_counterpart(
                self.db,
                items[0],
                CounterpartReference(target_type="person_roles", target_id=999999),
                UUID("00000000-0000-0000-0000-000000009902"),
            )
        self.assertEqual(
            raised.exception.correlation_id,
            UUID("00000000-0000-0000-0000-000000009902"),
        )

    def test_get_case_reads_effective_identity_and_cross_case_active_override(self) -> None:
        person, product, *_rest = self._seed_typed_targets()
        identity_decision_id = UUID("30000000-0000-0000-0000-000000000001")
        identity_key = "identity:effective"
        identity_name = "Effective Public Name"
        before = {
            "schema_version": 1,
            "case_status": "pending",
            "scientific_status": "pending",
            "current_decision_id": None,
            "identity": None,
            "overrides": [],
        }
        after = {
            "schema_version": 1,
            "case_status": "resolved",
            "scientific_status": "validated",
            "current_decision_id": str(identity_decision_id),
            "identity": {
                "schema_version": 1,
                "canonical_identity_key": identity_key,
                "canonical_name": identity_name,
                "identity_type": "internal_person",
                "aliases": [{
                    "schema_version": 1,
                    "alias_original": identity_name,
                    "alias_normalized": identity_name.casefold(),
                }],
            },
            "overrides": [],
        }
        identity_decision = ReviewDecision(
            id=identity_decision_id,
            review_item_id=person.id,
            sequence=1,
            decision_type="corrected",
            decision_lifecycle="approved",
            scope="record",
            payload_schema="review.decision.v1",
            payload_version=1,
            payload={
                "kind": "identity",
                "schema_version": 1,
                "canonical_identity_key": identity_key,
                "canonical_name": identity_name,
                "identity_type": "internal_person",
                "alias_original": identity_name,
                "alias_normalized": identity_name.casefold(),
                "projection_before": before,
                "projection_after": after,
            },
            reason="verified correction",
            actor_type="legacy",
            actor_user_id=None,
            actor_identifier="legacy:task3",
            actor_capability=None,
            expected_case_version=1,
            locks_projection=True,
        )
        self.db.add(identity_decision)
        self.db.flush()
        person.current_decision_id = identity_decision.id
        person.case_status = "resolved"
        person.scientific_status = "validated"

        product_decision_id = UUID("30000000-0000-0000-0000-000000000004")
        product_decision = ReviewDecision(
            id=product_decision_id,
            review_item_id=product.id,
            sequence=1,
            decision_type="corrected",
            decision_lifecycle="approved",
            scope="record",
            payload_schema="review.decision.v1",
            payload_version=1,
            payload={
                "kind": "field_override",
                "schema_version": 1,
                "field_path": "product_title",
                "value": {
                    "kind": "string",
                    "string_value": "Command value",
                    "integer_value": None,
                    "decimal_value": None,
                    "boolean_value": None,
                },
                "scope": "record",
                "stable_target_key": product.stable_target_key,
                "projection_before": {
                    "schema_version": 1,
                    "case_status": "pending",
                    "scientific_status": "pending",
                    "current_decision_id": None,
                    "identity": None,
                    "overrides": [],
                },
                "projection_after": {
                    "schema_version": 1,
                    "case_status": "resolved",
                    "scientific_status": "validated",
                    "current_decision_id": str(product_decision_id),
                    "identity": None,
                    "overrides": [],
                },
            },
            reason="fallback fixture",
            actor_type="legacy",
            actor_user_id=None,
            actor_identifier="legacy:task3",
            actor_capability=None,
            expected_case_version=1,
            locks_projection=True,
        )
        self.db.add(product_decision)
        self.db.flush()
        product.current_decision_id = product_decision.id
        product.case_status = "resolved"
        product.scientific_status = "validated"

        override_owner = self._item(
            130,
            case_type="product",
            target_table="scientific_productions",
            target_pk=102,
            case_status="resolved",
            scientific_status="validated",
            detected="Product detected",
        )
        override_owner.stable_target_key = product.stable_target_key
        override_decision = ReviewDecision(
            id=UUID("30000000-0000-0000-0000-000000000002"),
            review_item_id=override_owner.id,
            sequence=1,
            decision_type="corrected",
            decision_lifecycle="approved",
            scope="record",
            payload_schema="review.decision.v1",
            payload_version=1,
            payload={},
            reason="cross-case fixture",
            actor_type="legacy",
            actor_user_id=None,
            actor_identifier="legacy:task3",
            actor_capability=None,
            expected_case_version=1,
            locks_projection=True,
        )
        self.db.add(override_owner)
        self.db.flush()
        self.db.add(override_decision)
        self.db.flush()
        override = FieldOverride(
            id=UUID("30000000-0000-0000-0000-000000000003"),
            review_item_id=override_owner.id,
            decision_id=override_decision.id,
            stable_target_key=product.stable_target_key,
            target_table="scientific_productions",
            target_pk=102,
            field_path="product_title",
            value_schema="override.scalar.v1",
            value_version=1,
            projected_value={
                "kind": "string",
                "string_value": "Effective Product Override",
                "integer_value": None,
                "decimal_value": None,
                "boolean_value": None,
            },
            scope="record",
            locked=True,
            is_active=True,
        )
        self.db.add(override)
        self.db.flush()
        counts_before = (
            self.db.query(ReviewItem).count(),
            self.db.query(ReviewDecision).count(),
            self.db.query(FieldOverride).count(),
        )

        service = HumanReviewQueryService(self.db)
        person_detail = service.get_case(person.id)
        product_detail = service.get_case(product.id)

        self.assertEqual(person_detail.current_decision_id, identity_decision.id)
        self.assertEqual(person_detail.canonical_value, identity_name)
        self.assertEqual(person_detail.effective_memberships, ("identity_locked",))
        self.assertEqual(product_detail.current_decision_id, product_decision.id)
        self.assertEqual(product_detail.canonical_value, "Product canonical")
        self.assertEqual(
            product_detail.overrides[0]["value"], "Effective Product Override"
        )
        self.assertEqual(
            product_detail.overrides[0]["decision_id"], str(override_decision.id)
        )
        self.assertNotIn("field", product_detail.overrides[0])
        self.assertEqual(
            counts_before,
            (
                self.db.query(ReviewItem).count(),
                self.db.query(ReviewDecision).count(),
                self.db.query(FieldOverride).count(),
            ),
        )
        self.assertFalse(self.db.new)
        self.assertFalse(self.db.dirty)
        self.assertFalse(self.db.deleted)
        override.value_schema = "override.unapproved.v1"
        self.db.flush()
        with self.assertRaises(HumanReviewQueryInternalError):
            service.get_case(product.id)

    def test_get_case_fails_closed_for_missing_case_target_and_hash_mismatch(self) -> None:
        item = self._item(120, target_pk=999)
        self.db.add(item)
        self.db.flush()
        service = HumanReviewQueryService(self.db)

        with self.assertRaises(ReviewCaseNotFoundError):
            service.get_case(uuid4())
        with self.assertRaises(HumanReviewQueryInternalError):
            service.get_case(item.id)

        typed = self._seed_typed_targets()[0]
        typed.raw_value_sha256 = raw_value_sha256("tampered")
        self.db.flush()
        with self.assertRaises(HumanReviewQueryInternalError):
            service.get_case(typed.id)

    def _seed_audit_timeline(self) -> tuple[ReviewItem, tuple[UUID, ...]]:
        item = self._item(200)
        self.db.add(item)
        self.db.flush()
        event_ids = tuple(
            UUID(f"10000000-0000-0000-0000-{suffix:012d}")
            for suffix in range(1, 6)
        )
        moments = (
            datetime(2026, 7, 27, 12, 0, tzinfo=UTC),
            datetime(2026, 7, 27, 12, 1, tzinfo=UTC),
            datetime(2026, 7, 27, 12, 1, tzinfo=UTC),
            datetime(2026, 7, 27, 12, 2, tzinfo=UTC),
            datetime(2026, 7, 27, 12, 3, tzinfo=UTC),
        )
        for event_id, occurred_at in reversed(tuple(zip(event_ids, moments, strict=True))):
            self.db.add(AuditEvent(
                id=event_id,
                event_type="case_backfilled",
                aggregate_type="review_item",
                aggregate_key=f"private:{item.id}",
                review_item_id=item.id,
                actor_user_id=None,
                actor_identifier="private-actor@example.invalid",
                actor_capability=None,
                occurred_at=occurred_at,
                payload_schema="audit.case_backfilled.v1",
                payload_version=1,
                payload={
                    "kind": "case_backfilled",
                    "schema_version": 1,
                    "review_item_id": str(item.id),
                    "case_type": item.case_type,
                    "stable_target_key": item.stable_target_key,
                },
                correlation_id=UUID("20000000-0000-0000-0000-000000000001"),
                event_hash=hashlib.sha256(str(event_id).encode()).hexdigest(),
                created_at=datetime(2026, 7, 28, 1, 0, tzinfo=UTC),
            ))
        self.db.flush()
        return item, event_ids

    def test_get_audit_pages_chronologically_and_exposes_only_allowlisted_data(self) -> None:
        item, event_ids = self._seed_audit_timeline()
        service = HumanReviewQueryService(self.db)

        pages = tuple(service.get_audit(item.id, page, 2) for page in (1, 2, 3))

        self.assertEqual(
            tuple(entry["id"] for page in pages for entry in page.items),
            tuple(str(event_id) for event_id in event_ids),
        )
        self.assertEqual(tuple(page.total for page in pages), (5, 5, 5))
        self.assertEqual(
            pages[0].items[0]["created_at"],
            "2026-07-27T12:00:00+00:00",
        )
        dumped = pages[0].model_dump(mode="json")
        self.assertNotIn("private-actor", repr(dumped))
        self.assertNotIn("aggregate_key", repr(dumped))
        self.assertNotIn("stable_target_key", repr(dumped))
        with self.assertRaises(ReviewCaseNotFoundError):
            service.get_audit(uuid4(), 1, 10)
        for invalid in ((0, 10), (1, 0), (1, 101), (1.5, 10), (1, True)):
            with self.subTest(invalid=invalid), self.assertRaises(HumanReviewDomainError):
                service.get_audit(item.id, *invalid)

    def test_get_audit_fails_closed_for_invalid_schema_envelope(self) -> None:
        item, _event_ids = self._seed_audit_timeline()
        event = self.db.scalar(select(AuditEvent).where(AuditEvent.review_item_id == item.id))
        event.payload_schema = "audit.unapproved.v1"
        self.db.flush()
        with self.assertRaises(HumanReviewQueryInternalError):
            HumanReviewQueryService(self.db).get_audit(item.id, 1, 10)
        event.payload_schema = "audit.case_backfilled.v1"
        event.payload = {
            "kind": "case_backfilled",
            "schema_version": 1,
            "review_item_id": str(uuid4()),
            "case_type": item.case_type,
            "stable_target_key": item.stable_target_key,
        }
        self.db.flush()
        with self.assertRaises(HumanReviewQueryInternalError):
            HumanReviewQueryService(self.db).get_audit(item.id, 1, 10)

    def _explain_index_names(self, statement) -> set[str]:
        sql = str(statement.compile(
            dialect=self.engine.dialect,
            compile_kwargs={"literal_binds": True},
        ))
        plan = self.db.execute(text(
            f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}"
        )).scalar_one()
        names: set[str] = set()

        def visit(node) -> None:
            if isinstance(node, dict):
                name = node.get("Index Name")
                if isinstance(name, str):
                    names.add(name)
                for value in node.values():
                    visit(value)
            elif isinstance(node, list):
                for value in node:
                    visit(value)

        visit(plan)
        return names

    def test_explain_uses_existing_selective_indexes_without_schema_changes(self) -> None:
        now = datetime(2026, 7, 27, 10, 0, tzinfo=UTC)
        noise = []
        for index in range(5000):
            item_id = UUID(int=10000 + index)
            noise.append({
                "id": item_id,
                "case_type": "product",
                "stable_target_key": f"b2b:v1:product:{index:064x}",
                "target_table": "scientific_productions",
                "target_pk": None,
                "scope_resolution_reason": "unresolved_no_persisted_scope",
                "document_key": f"dropbox_path:/noise/{index}.pdf",
                "source_revision": "noise",
                "source_page": 1,
                "source_section": "noise",
                "row_or_block_id": f"noise-{index}",
                "field_path": "case",
                "raw_value_sha256": hashlib.sha256(f"noise-{index}".encode()).hexdigest(),
                "period_id": 1000 + (index % 20),
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
            })
        self.db.bulk_insert_mappings(ReviewItem, noise)
        document_item = self._item(
            301,
            document_key="dropbox_path:/selective/document.pdf",
            case_status="resolved",
            scientific_status="validated",
        )
        period_item = self._item(
            302,
            period_id=777777,
            case_status="resolved",
            scientific_status="validated",
        )
        queue_item = self._item(
            303,
            case_type="external_identity",
            target_table="external_researchers",
            case_status="resolved",
            scientific_status="validated",
        )
        audit_item = self._item(
            304,
            case_status="resolved",
            scientific_status="validated",
        )
        self.db.add_all((document_item, period_item, queue_item, audit_item))
        self.db.flush()
        audit_noise = []
        for index, row in enumerate(noise):
            event_id = UUID(int=20000 + index)
            audit_noise.append({
                "id": event_id,
                "event_type": "case_backfilled",
                "aggregate_type": "review_item",
                "aggregate_key": f"noise:{index}",
                "review_item_id": row["id"],
                "actor_user_id": None,
                "actor_identifier": "planner-noise",
                "actor_capability": None,
                "occurred_at": now,
                "payload_schema": "audit.case_backfilled.v1",
                "payload_version": 1,
                "payload": {},
                "correlation_id": UUID(int=30000 + index),
                "request_id": None,
                "previous_event_id": None,
                "corrects_event_id": None,
                "previous_event_hash": None,
                "event_hash": hashlib.sha256(f"event-{index}".encode()).hexdigest(),
                "created_at": now,
            })
        self.db.bulk_insert_mappings(AuditEvent, audit_noise)
        target_event = AuditEvent(
            id=UUID(int=99999),
            event_type="case_backfilled",
            aggregate_type="review_item",
            aggregate_key="target",
            review_item_id=audit_item.id,
            actor_identifier="planner-target",
            occurred_at=now,
            payload_schema="audit.case_backfilled.v1",
            payload_version=1,
            payload={},
            correlation_id=UUID(int=99998),
            event_hash=hashlib.sha256(b"target-event").hexdigest(),
        )
        self.db.add(target_event)
        self.db.flush()
        self.db.execute(text("ANALYZE review_items"))
        self.db.execute(text("ANALYZE audit_events"))

        order = (
            ReviewItem.manual_priority.desc().nulls_last(),
            ReviewItem.automatic_priority.desc(),
            ReviewItem.created_at.asc(),
            ReviewItem.id.asc(),
        )
        statements = {
            "ix_review_items_document": select(ReviewItem).where(
                ReviewItem.document_key == document_item.document_key
            ).order_by(*order).limit(25),
            "ix_review_items_period": select(ReviewItem).where(
                ReviewItem.period_id == period_item.period_id
            ).order_by(*order).limit(25),
            "ix_review_items_queue": select(ReviewItem).where(
                ReviewItem.case_type == queue_item.case_type,
                ReviewItem.case_status == queue_item.case_status,
            ).order_by(*order).limit(25),
            "ix_audit_events_review_item": select(AuditEvent).where(
                AuditEvent.review_item_id == audit_item.id
            ).order_by(AuditEvent.occurred_at.asc(), AuditEvent.id.asc()).limit(25),
        }
        for expected_index, statement in statements.items():
            with self.subTest(index=expected_index):
                self.assertIn(
                    expected_index,
                    self._explain_index_names(statement),
                )
