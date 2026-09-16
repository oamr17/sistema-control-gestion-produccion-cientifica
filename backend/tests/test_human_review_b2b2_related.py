from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from itertools import count
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import event, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

import app.models  # noqa: F401  # Register every FK dependency with Base.metadata.
from app.api import dependencies
from app.api.v1.router import api_router
from app.core.database import Base
from app.models.entities import (
    AcademicPeriod,
    Career,
    ExternalResearcher,
    Faculty,
    ImportedOcrTrace,
    ImportJob,
    PersonRole,
    ResearchEntity,
    ScientificProduction,
    ScientificProductionAuthor,
    Teacher,
)
from app.models.enums import ProductionType
from app.models.human_review_audit import AuditEvent
from app.models.human_review_core import ReviewDecision, ReviewItem
from app.models.human_review_enums import (
    ReviewCaseStatus,
    ReviewCaseType,
    ReviewTargetTable,
    ScientificStatus,
    B2BCapability,
)
from app.models.human_review_projection import CanonicalIdentity, FieldOverride
from app.schemas.human_review_api import (
    CASE_ACTION_DECISION_TYPES,
    RelatedReviewEntity,
    RelatedReviewItem,
    RelatedReviewResponse,
)
from app.services.human_review_projection import EffectiveHumanProjectionSource
from app.services.human_review_queries import HumanReviewQueryService
from app.services.human_review_related import (
    RelatedEntityRef,
    TargetRef,
    find_related_target_refs,
    resolve_related_entity,
)
from app.services.human_review_targets import raw_value_sha256
from tests.support.postgres import (
    isolated_postgres_schema,
    require_b2b1_test_database_url,
)


SUPPORTED_CASE_TARGET_PAIRS = (
    (ReviewCaseType.PERSON_IDENTITY, ReviewTargetTable.PERSON_ROLES),
    (
        ReviewCaseType.AUTHOR_IDENTITY,
        ReviewTargetTable.SCIENTIFIC_PRODUCTION_AUTHORS,
    ),
    (ReviewCaseType.PRODUCT, ReviewTargetTable.SCIENTIFIC_PRODUCTIONS),
    (
        ReviewCaseType.PROJECT_DIRECTOR_RELATION,
        ReviewTargetTable.RESEARCH_ENTITIES,
    ),
    (ReviewCaseType.EXTERNAL_IDENTITY, ReviewTargetTable.EXTERNAL_RESEARCHERS),
    (ReviewCaseType.POSSIBLE_DUPLICATE, ReviewTargetTable.PERSON_ROLES),
    (
        ReviewCaseType.POSSIBLE_DUPLICATE,
        ReviewTargetTable.SCIENTIFIC_PRODUCTION_AUTHORS,
    ),
)


class RelatedEntityResolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._schema = isolated_postgres_schema(
            require_b2b1_test_database_url(),
            "b2b2_related",
        )
        cls.engine = cls._schema.__enter__()
        try:
            Base.metadata.create_all(cls.engine)
        except Exception:
            cls._schema.__exit__(*__import__("sys").exc_info())
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()
        cls._schema.__exit__(None, None, None)

    def setUp(self) -> None:
        table_names = ", ".join(
            self.engine.dialect.identifier_preparer.quote(table.name)
            for table in reversed(Base.metadata.sorted_tables)
        )
        with self.engine.begin() as connection:
            connection.execute(text(
                f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"
            ))
        self._stable_key_counter = count(1)
        self._seed_dimensions()

    def _seed_dimensions(self) -> None:
        with Session(self.engine) as db, db.begin():
            faculty = Faculty(id=1, name="Engineering")
            career = Career(id=1, faculty_id=1, name="Computing", code="CS")
            db.add_all((faculty, career))
            db.flush()
            db.add_all((
                Teacher(
                    id=11,
                    career_id=1,
                    full_name="Ada Teacher",
                    institutional_email="ada.teacher@example.invalid",
                ),
                Teacher(
                    id=12,
                    career_id=1,
                    full_name="Grace Teacher",
                    institutional_email="grace.teacher@example.invalid",
                ),
                AcademicPeriod(id=21, year_label="2026", cycle=1),
                AcademicPeriod(id=22, year_label="2026", cycle=2),
            ))

    def _canonical(
        self,
        key: str,
        name: str,
        *,
        status: str = "active",
    ) -> CanonicalIdentity:
        row = CanonicalIdentity(
            id=uuid4(),
            canonical_identity_key=key,
            identity_type="internal_person",
            display_name=name,
            status=status,
            origin="human",
        )
        with Session(self.engine, expire_on_commit=False) as db, db.begin():
            db.add(row)
        return row

    def _external(self, row_id: int, name: str) -> ExternalResearcher:
        row = ExternalResearcher(
            id=row_id,
            period_id=21,
            full_name=name,
            normalized_name=name.casefold(),
            institution="External University",
            normalized_institution="external university",
            created_at=datetime.now(timezone.utc),
        )
        with Session(self.engine, expire_on_commit=False) as db, db.begin():
            db.add(row)
        return row

    def _production(self, row_id: int, title: str) -> ScientificProduction:
        row = ScientificProduction(
            id=row_id,
            period_id=21,
            production_type=ProductionType.ARTICLE,
            title=title,
            created_at=datetime.now(timezone.utc),
        )
        with Session(self.engine, expire_on_commit=False) as db, db.begin():
            db.add(row)
        return row

    def _person_role(
        self,
        row_id: int,
        name: str,
        *,
        canonical_key: str | None = None,
        teacher_id: int | None = None,
        external_researcher_id: int | None = None,
    ) -> PersonRole:
        row = PersonRole(
            id=row_id,
            period_id=21,
            teacher_id=teacher_id,
            external_researcher_id=external_researcher_id,
            role_type="researcher",
            person_type="internal" if teacher_id else "external",
            canonical_identity_key=canonical_key,
            canonical_name=name,
            raw_name=name,
            normalized_name=name.casefold(),
            created_at=datetime.now(timezone.utc),
        )
        with Session(self.engine, expire_on_commit=False) as db, db.begin():
            db.add(row)
        return row

    def _person_roles_bulk(
        self,
        row_ids: range,
        *,
        canonical_key: str,
    ) -> tuple[PersonRole, ...]:
        rows = tuple(
            PersonRole(
                id=row_id,
                period_id=21,
                role_type="researcher",
                person_type="internal",
                canonical_identity_key=canonical_key,
                canonical_name=f"Bounded Person {row_id}",
                raw_name=f"Bounded Person {row_id}",
                normalized_name=f"bounded person {row_id}",
                created_at=datetime.now(timezone.utc),
            )
            for row_id in row_ids
        )
        with Session(self.engine, expire_on_commit=False) as db, db.begin():
            db.add_all(rows)
        return rows

    def _author(
        self,
        row_id: int,
        production_id: int,
        name: str,
        *,
        canonical_key: str | None = None,
        teacher_id: int | None = None,
        external_researcher_id: int | None = None,
    ) -> ScientificProductionAuthor:
        row = ScientificProductionAuthor(
            id=row_id,
            production_id=production_id,
            author_order=1,
            raw_author_name=name,
            normalized_author_name=name.casefold(),
            canonical_identity_key=canonical_key,
            canonical_name=name,
            teacher_id=teacher_id,
            external_researcher_id=external_researcher_id,
            author_type="internal" if teacher_id else "external",
            created_at=datetime.now(timezone.utc),
        )
        with Session(self.engine, expire_on_commit=False) as db, db.begin():
            db.add(row)
        return row

    def _item(
        self,
        case_type: ReviewCaseType,
        target_table: ReviewTargetTable,
        target_pk: int | None,
        *,
        document_key: str = "document:a.pdf",
        persist: bool = False,
    ) -> ReviewItem:
        digest = f"{next(self._stable_key_counter):064x}"
        item = ReviewItem(
            id=uuid4(),
            case_type=case_type.value,
            stable_target_key=f"b2b:v1:{case_type.value}:{digest}",
            target_table=target_table.value,
            target_pk=target_pk,
            scope_faculty_id=1,
            scope_career_id=1,
            document_key=document_key,
            source_revision="revision-1",
            source_page=1,
            source_section="fixture",
            row_or_block_id=f"row-{digest}",
            field_path="canonical_name",
            raw_value_sha256=digest,
            period_id=21,
            case_status="pending",
            scientific_status="pending",
        )
        if persist:
            with Session(self.engine, expire_on_commit=False) as db, db.begin():
                db.add(item)
        return item

    def _seed_effective_projection(
        self,
        target: PersonRole | ScientificProductionAuthor,
        canonical_key: str,
        canonical_name: str,
        *,
        case_type: ReviewCaseType = ReviewCaseType.PERSON_IDENTITY,
        target_table: ReviewTargetTable = ReviewTargetTable.PERSON_ROLES,
    ) -> ReviewItem:
        item = self._item(
            case_type,
            target_table,
            target.id,
            document_key="document:projection.pdf",
        )
        item.case_status = "resolved"
        item.scientific_status = "validated"
        decision_id = uuid4()
        scalar = {
            "kind": "string",
            "string_value": "validated",
            "integer_value": None,
            "decimal_value": None,
            "boolean_value": None,
        }
        override = {
            "schema_version": 1,
            "field_path": "scientific_status",
            "projected_value": scalar,
            "scope": "record",
            "stable_target_key": item.stable_target_key,
            "target_table": item.target_table,
            "target_pk": item.target_pk,
            "document_key": None,
            "period_id": None,
            "relationship_key": None,
            "locked": True,
        }
        identity = {
            "schema_version": 1,
            "canonical_identity_key": canonical_key,
            "canonical_name": canonical_name,
            "identity_type": "internal_person",
            "aliases": [],
        }
        payload = {
            "kind": "identity",
            "schema_version": 1,
            "canonical_identity_key": canonical_key,
            "canonical_name": canonical_name,
            "identity_type": "internal_person",
            "alias_original": None,
            "alias_normalized": None,
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
                "current_decision_id": str(decision_id),
                "identity": identity,
                "overrides": [override],
            },
        }
        decision = ReviewDecision(
            id=decision_id,
            review_item_id=item.id,
            sequence=1,
            decision_type="corrected",
            decision_lifecycle="approved",
            scope="record",
            payload_schema="review.decision.v1",
            payload_version=1,
            payload=payload,
            actor_type="legacy",
            actor_user_id=None,
            actor_identifier="legacy:test-related",
            actor_capability=None,
            expected_case_version=1,
            locks_projection=True,
        )
        materialized = FieldOverride(
            id=uuid4(),
            review_item_id=item.id,
            decision_id=decision.id,
            stable_target_key=item.stable_target_key,
            target_table=item.target_table,
            target_pk=item.target_pk,
            field_path="scientific_status",
            value_schema="override.scalar.v1",
            value_version=1,
            projected_value=scalar,
            scope="record",
            locked=True,
            is_active=True,
        )
        with Session(self.engine, expire_on_commit=False) as db, db.begin():
            db.add(item)
            db.flush()
            db.add(decision)
            db.flush()
            item.current_decision_id = decision.id
            db.add(materialized)
        return item

    def _seed_ambiguous_projection_heads(
        self,
        target: PersonRole,
        *,
        count: int,
    ) -> None:
        items: list[ReviewItem] = []
        decisions: list[ReviewDecision] = []
        for index in range(count):
            item = self._item(
                ReviewCaseType.PERSON_IDENTITY,
                ReviewTargetTable.PERSON_ROLES,
                target.id,
                document_key=f"document:ambiguous-{index}.pdf",
            )
            item.case_status = "resolved"
            decision = ReviewDecision(
                id=uuid4(),
                review_item_id=item.id,
                sequence=1,
                decision_type="corrected",
                decision_lifecycle="proposed",
                scope="record",
                payload_schema="review.decision.v1",
                payload_version=1,
                payload={},
                actor_type="legacy",
                actor_user_id=None,
                actor_identifier=f"legacy:ambiguous-{index}",
                actor_capability=None,
                expected_case_version=1,
                locks_projection=False,
            )
            items.append(item)
            decisions.append(decision)
        with Session(self.engine) as db, db.begin():
            db.add_all(items)
            db.flush()
            db.add_all(decisions)
            db.flush()
            for item, decision in zip(items, decisions):
                item.current_decision_id = decision.id

    def test_every_supported_case_target_pair_resolves_unambiguously(self) -> None:
        product = self._production(101, "Exact Product")
        role = self._person_role(201, "Exact Role")
        author = self._author(301, product.id, "Exact Author")
        with Session(self.engine) as db, db.begin():
            db.add(ResearchEntity(
                id=401,
                period_id=21,
                type="project",
                name="Exact Project",
                created_at=datetime.now(timezone.utc),
            ))
        external = self._external(501, "Exact External")
        target_ids = {
            ReviewTargetTable.PERSON_ROLES: role.id,
            ReviewTargetTable.SCIENTIFIC_PRODUCTION_AUTHORS: author.id,
            ReviewTargetTable.SCIENTIFIC_PRODUCTIONS: product.id,
            ReviewTargetTable.RESEARCH_ENTITIES: 401,
            ReviewTargetTable.EXTERNAL_RESEARCHERS: external.id,
        }
        expected_public_types = {
            ReviewTargetTable.PERSON_ROLES: "person",
            ReviewTargetTable.SCIENTIFIC_PRODUCTION_AUTHORS: "person",
            ReviewTargetTable.SCIENTIFIC_PRODUCTIONS: "scientific_product",
            ReviewTargetTable.RESEARCH_ENTITIES: "research_entity",
            ReviewTargetTable.EXTERNAL_RESEARCHERS: "external_researcher",
        }

        with Session(self.engine) as db:
            for case_type, target_table in SUPPORTED_CASE_TARGET_PAIRS:
                with self.subTest(case_type=case_type, target_table=target_table):
                    item = self._item(case_type, target_table, target_ids[target_table])
                    entity = resolve_related_entity(db, item)
                    self.assertEqual(entity.public_type, expected_public_types[target_table])
                    self.assertEqual(entity.match_kind, "record")
                    self.assertEqual(entity.match_id, target_ids[target_table])
                    self.assertEqual(
                        find_related_target_refs(db, entity, limit=10),
                        (TargetRef(target_table, target_ids[target_table]),),
                    )

    def test_effective_human_projection_precedes_persisted_identity_and_fks(self) -> None:
        projected = self._canonical("identity:projected", "Projected Person")
        self._canonical("identity:persisted", "Persisted Person")
        self._external(51, "External Fallback")
        role = self._person_role(
            61,
            "Source Person",
            canonical_key="identity:persisted",
            teacher_id=11,
            external_researcher_id=51,
        )
        item = self._seed_effective_projection(
            role,
            projected.canonical_identity_key,
            projected.display_name,
        )

        with Session(self.engine) as db:
            self.assertEqual(
                resolve_related_entity(db, item),
                RelatedEntityRef(
                    public_type="person",
                    public_id=str(projected.id),
                    display_name="Projected Person",
                    match_kind="canonical_identity",
                    match_id=projected.id,
                ),
            )

    def test_projected_candidates_are_included_and_projected_away_raw_matches_excluded(self) -> None:
        identity_a = self._canonical("identity:candidate-a", "Candidate A")
        identity_b = self._canonical("identity:candidate-b", "Candidate B")
        identity_c = self._canonical("identity:candidate-c", "Candidate C")
        product_anchor = self._production(131, "Projected Anchor")
        product_persisted = self._production(132, "Persisted Match")
        anchor = self._author(
            131,
            product_anchor.id,
            "Projected Anchor",
            canonical_key=identity_a.canonical_identity_key,
        )
        projected_match = self._person_role(
            132,
            "Projected Match",
            canonical_key=identity_a.canonical_identity_key,
        )
        projected_away = self._person_role(
            133,
            "Projected Away",
            canonical_key=identity_b.canonical_identity_key,
        )
        persisted_match = self._author(
            134,
            product_persisted.id,
            "Persisted Match",
            canonical_key=identity_b.canonical_identity_key,
        )
        anchor_item = self._seed_effective_projection(
            anchor,
            identity_b.canonical_identity_key,
            identity_b.display_name,
            case_type=ReviewCaseType.AUTHOR_IDENTITY,
            target_table=ReviewTargetTable.SCIENTIFIC_PRODUCTION_AUTHORS,
        )
        self._seed_effective_projection(
            projected_match,
            identity_b.canonical_identity_key,
            identity_b.display_name,
        )
        self._seed_effective_projection(
            projected_away,
            identity_c.canonical_identity_key,
            identity_c.display_name,
        )

        with Session(self.engine) as db:
            entity = resolve_related_entity(db, anchor_item)
            self.assertEqual(entity.match_id, identity_b.id)
            self.assertEqual(
                find_related_target_refs(db, entity, limit=10),
                (
                    TargetRef(ReviewTargetTable.PERSON_ROLES, projected_match.id),
                    TargetRef(
                        ReviewTargetTable.SCIENTIFIC_PRODUCTION_AUTHORS,
                        anchor.id,
                    ),
                    TargetRef(
                        ReviewTargetTable.SCIENTIFIC_PRODUCTION_AUTHORS,
                        persisted_match.id,
                    ),
                ),
            )

    def test_public_read_helpers_do_not_autoflush_unrelated_pending_state(self) -> None:
        role = self._person_role(135, "No Autoflush")
        item = self._item(
            ReviewCaseType.PERSON_IDENTITY,
            ReviewTargetTable.PERSON_ROLES,
            role.id,
        )
        record_ref = RelatedEntityRef(
            public_type="person",
            public_id=f"person_roles:{role.id}",
            display_name="No Autoflush",
            match_kind="record",
            match_id=role.id,
        )

        with Session(self.engine) as db:
            pending_before_resolve = Faculty(id=901, name=None)
            db.add(pending_before_resolve)
            try:
                self.assertEqual(resolve_related_entity(db, item), record_ref)
            except SQLAlchemyError as error:
                self.fail(f"resolve_related_entity autoflushed pending state: {error}")
            self.assertIn(pending_before_resolve, db.new)
            db.expunge(pending_before_resolve)

            pending_before_find = Faculty(id=902, name=None)
            db.add(pending_before_find)
            try:
                self.assertEqual(
                    find_related_target_refs(db, record_ref, limit=10),
                    (TargetRef(ReviewTargetTable.PERSON_ROLES, role.id),),
                )
            except SQLAlchemyError as error:
                self.fail(f"find_related_target_refs autoflushed pending state: {error}")
            self.assertIn(pending_before_find, db.new)

    def test_author_input_uses_projection_canonical_teacher_external_precedence(self) -> None:
        projected = self._canonical("identity:author-projected", "Author Projected")
        persisted = self._canonical("identity:author-persisted", "Author Persisted")
        external = self._external(151, "Author External")
        products = tuple(
            self._production(row_id, f"Author Product {row_id}")
            for row_id in (141, 142, 143, 144)
        )
        projected_author = self._author(
            141,
            products[0].id,
            "Projected Author",
            canonical_key=persisted.canonical_identity_key,
            teacher_id=11,
            external_researcher_id=external.id,
        )
        projected_item = self._seed_effective_projection(
            projected_author,
            projected.canonical_identity_key,
            projected.display_name,
            case_type=ReviewCaseType.AUTHOR_IDENTITY,
            target_table=ReviewTargetTable.SCIENTIFIC_PRODUCTION_AUTHORS,
        )
        persisted_author = self._author(
            142,
            products[1].id,
            "Persisted Author",
            canonical_key=persisted.canonical_identity_key,
            teacher_id=11,
            external_researcher_id=external.id,
        )
        teacher_author = self._author(
            143,
            products[2].id,
            "Teacher Author",
            canonical_key="identity:author-missing",
            teacher_id=11,
            external_researcher_id=external.id,
        )
        external_author = self._author(
            144,
            products[3].id,
            "External Author",
            external_researcher_id=external.id,
        )
        cases = (
            (projected_item, "canonical_identity", projected.id),
            (
                self._item(
                    ReviewCaseType.AUTHOR_IDENTITY,
                    ReviewTargetTable.SCIENTIFIC_PRODUCTION_AUTHORS,
                    persisted_author.id,
                ),
                "canonical_identity",
                persisted.id,
            ),
            (
                self._item(
                    ReviewCaseType.AUTHOR_IDENTITY,
                    ReviewTargetTable.SCIENTIFIC_PRODUCTION_AUTHORS,
                    teacher_author.id,
                ),
                "teacher",
                11,
            ),
            (
                self._item(
                    ReviewCaseType.AUTHOR_IDENTITY,
                    ReviewTargetTable.SCIENTIFIC_PRODUCTION_AUTHORS,
                    external_author.id,
                ),
                "external_researcher",
                external.id,
            ),
        )

        with Session(self.engine) as db:
            for item, expected_kind, expected_id in cases:
                with self.subTest(target_pk=item.target_pk, match_kind=expected_kind):
                    entity = resolve_related_entity(db, item)
                    self.assertEqual(entity.match_kind, expected_kind)
                    self.assertEqual(entity.match_id, expected_id)

    def test_persisted_canonical_identity_groups_cross_document_targets(self) -> None:
        identity = self._canonical("identity:shared", "Shared Person")
        product = self._production(71, "Cross-document Product")
        role = self._person_role(
            72,
            "Shared Person",
            canonical_key=identity.canonical_identity_key,
            teacher_id=11,
        )
        author = self._author(
            73,
            product.id,
            "Shared Person",
            canonical_key=identity.canonical_identity_key,
            teacher_id=12,
        )
        anchor = self._item(
            ReviewCaseType.PERSON_IDENTITY,
            ReviewTargetTable.PERSON_ROLES,
            role.id,
            document_key="document:first.pdf",
            persist=True,
        )
        self._item(
            ReviewCaseType.AUTHOR_IDENTITY,
            ReviewTargetTable.SCIENTIFIC_PRODUCTION_AUTHORS,
            author.id,
            document_key="document:second.pdf",
            persist=True,
        )

        with Session(self.engine) as db:
            entity = resolve_related_entity(db, anchor)
            self.assertEqual(entity.match_kind, "canonical_identity")
            self.assertEqual(entity.match_id, identity.id)
            self.assertEqual(
                find_related_target_refs(db, entity, limit=10),
                (
                    TargetRef(ReviewTargetTable.PERSON_ROLES, role.id),
                    TargetRef(
                        ReviewTargetTable.SCIENTIFIC_PRODUCTION_AUTHORS,
                        author.id,
                    ),
                ),
            )

    def test_merged_or_missing_canonical_identity_falls_through_to_teacher(self) -> None:
        self._canonical("identity:merged", "Merged Person", status="merged")
        external = self._external(84, "Lower-priority External")
        product = self._production(81, "Teacher Product")
        role = self._person_role(
            82,
            "Teacher Person",
            canonical_key="identity:merged",
            teacher_id=11,
            external_researcher_id=external.id,
        )
        author = self._author(
            83,
            product.id,
            "Teacher Person",
            canonical_key="identity:missing",
            teacher_id=11,
        )
        anchor = self._item(
            ReviewCaseType.PERSON_IDENTITY,
            ReviewTargetTable.PERSON_ROLES,
            role.id,
            document_key="document:teacher-a.pdf",
            persist=True,
        )
        self._item(
            ReviewCaseType.AUTHOR_IDENTITY,
            ReviewTargetTable.SCIENTIFIC_PRODUCTION_AUTHORS,
            author.id,
            document_key="document:teacher-b.pdf",
            persist=True,
        )

        with Session(self.engine) as db:
            entity = resolve_related_entity(db, anchor)
            self.assertEqual(
                entity,
                RelatedEntityRef(
                    public_type="person",
                    public_id="11",
                    display_name="Ada Teacher",
                    match_kind="teacher",
                    match_id=11,
                ),
            )
            self.assertEqual(
                find_related_target_refs(db, entity, limit=10),
                (
                    TargetRef(ReviewTargetTable.PERSON_ROLES, role.id),
                    TargetRef(
                        ReviewTargetTable.SCIENTIFIC_PRODUCTION_AUTHORS,
                        author.id,
                    ),
                ),
            )

    def test_corrupt_projected_identity_falls_through_to_persisted_identity(self) -> None:
        self._canonical("identity:bad-projection", "   ")
        persisted = self._canonical("identity:safe", "Safe Persisted Person")
        role = self._person_role(
            91,
            "Safe Person",
            canonical_key=persisted.canonical_identity_key,
            teacher_id=11,
        )
        item = self._seed_effective_projection(
            role,
            "identity:bad-projection",
            "   ",
        )

        with Session(self.engine) as db:
            entity = resolve_related_entity(db, item)
            self.assertEqual(entity.match_kind, "canonical_identity")
            self.assertEqual(entity.match_id, persisted.id)

    def test_external_researcher_fk_is_used_after_teacher_fallback(self) -> None:
        external = self._external(101, "External Identity")
        product = self._production(102, "External Product")
        role = self._person_role(
            103,
            "External Identity",
            canonical_key="identity:missing",
            external_researcher_id=external.id,
        )
        author = self._author(
            104,
            product.id,
            "External Identity",
            external_researcher_id=external.id,
        )

        with Session(self.engine) as db:
            entity = resolve_related_entity(
                db,
                self._item(
                    ReviewCaseType.PERSON_IDENTITY,
                    ReviewTargetTable.PERSON_ROLES,
                    role.id,
                ),
            )
            self.assertEqual(entity.match_kind, "external_researcher")
            self.assertEqual(entity.match_id, external.id)
            self.assertEqual(
                find_related_target_refs(db, entity, limit=10),
                (
                    TargetRef(ReviewTargetTable.EXTERNAL_RESEARCHERS, external.id),
                    TargetRef(ReviewTargetTable.PERSON_ROLES, role.id),
                    TargetRef(
                        ReviewTargetTable.SCIENTIFIC_PRODUCTION_AUTHORS,
                        author.id,
                    ),
                ),
            )

    def test_same_normalized_name_without_shared_identity_returns_only_anchor(self) -> None:
        first = self._person_role(111, "Same Name")
        self._person_role(112, "Same Name")
        item = self._item(
            ReviewCaseType.PERSON_IDENTITY,
            ReviewTargetTable.PERSON_ROLES,
            first.id,
            document_key="document:name-a.pdf",
        )

        with Session(self.engine) as db:
            entity = resolve_related_entity(db, item)
            self.assertEqual(entity.match_kind, "record")
            self.assertEqual(entity.public_id, f"person_roles:{first.id}")
            self.assertEqual(
                find_related_target_refs(db, entity, limit=10),
                (TargetRef(ReviewTargetTable.PERSON_ROLES, first.id),),
            )

    def test_related_target_refs_are_sorted_stably_then_limited(self) -> None:
        identity = self._canonical("identity:ordered", "Ordered Person")
        product_a = self._production(121, "Ordered A")
        product_b = self._production(122, "Ordered B")
        self._person_role(125, "Ordered Person", canonical_key=identity.canonical_identity_key)
        self._person_role(123, "Ordered Person", canonical_key=identity.canonical_identity_key)
        self._author(126, product_a.id, "Ordered Person", canonical_key=identity.canonical_identity_key)
        self._author(124, product_b.id, "Ordered Person", canonical_key=identity.canonical_identity_key)
        entity = RelatedEntityRef(
            public_type="person",
            public_id=str(identity.id),
            display_name=identity.display_name,
            match_kind="canonical_identity",
            match_id=identity.id,
        )

        with Session(self.engine) as db:
            self.assertEqual(
                find_related_target_refs(db, entity, limit=3),
                (
                    TargetRef(ReviewTargetTable.PERSON_ROLES, 123),
                    TargetRef(ReviewTargetTable.PERSON_ROLES, 125),
                    TargetRef(ReviewTargetTable.SCIENTIFIC_PRODUCTION_AUTHORS, 124),
                ),
            )

    def test_small_limit_bounds_ordered_candidate_batches_and_stops_early(self) -> None:
        raw_identity = self._canonical("identity:bounded-raw", "Bounded Raw")
        projected_identity = self._canonical(
            "identity:bounded-projected",
            "Bounded Projected",
        )
        rows = self._person_roles_bulk(
            range(160, 200),
            canonical_key=projected_identity.canonical_identity_key,
        )
        rows[0].canonical_identity_key = raw_identity.canonical_identity_key
        with Session(self.engine) as db, db.begin():
            persisted = db.get(PersonRole, rows[0].id)
            persisted.canonical_identity_key = raw_identity.canonical_identity_key
        self._seed_effective_projection(
            rows[0],
            projected_identity.canonical_identity_key,
            projected_identity.display_name,
        )
        entity = RelatedEntityRef(
            public_type="person",
            public_id=str(projected_identity.id),
            display_name=projected_identity.display_name,
            match_kind="canonical_identity",
            match_id=projected_identity.id,
        )
        observed: list[tuple[str, int]] = []

        def record_sql(
            _connection,
            _cursor,
            statement: str,
            parameters,
            _context,
            _executemany: bool,
        ) -> None:
            parameter_count = len(parameters) if parameters is not None else 0
            observed.append((statement.lower(), parameter_count))

        event.listen(self.engine, "before_cursor_execute", record_sql)
        try:
            with Session(self.engine) as db:
                self.assertEqual(
                    find_related_target_refs(db, entity, limit=1),
                    (TargetRef(ReviewTargetTable.PERSON_ROLES, 160),),
                )
        finally:
            event.remove(self.engine, "before_cursor_execute", record_sql)

        self.assertTrue(observed)
        self.assertLessEqual(
            max(parameter_count for _statement, parameter_count in observed),
            70,
            "candidate projection prefetch exceeded one bounded 32-row batch",
        )
        self.assertFalse(
            any(
                "select scientific_production_authors.id" in statement
                for statement, _parameter_count in observed
            ),
            "limit=1 performed unnecessary work in the later author table",
        )

    def test_projection_prefetch_caps_each_ambiguous_target_at_two_rows(self) -> None:
        identity = self._canonical("identity:ambiguous-cap", "Ambiguous Cap")
        role = self._person_role(
            220,
            "Ambiguous Cap",
            canonical_key=identity.canonical_identity_key,
        )
        self._seed_ambiguous_projection_heads(role, count=12)
        entity = RelatedEntityRef(
            public_type="person",
            public_id=str(identity.id),
            display_name=identity.display_name,
            match_kind="canonical_identity",
            match_id=identity.id,
        )
        prefetch_row_counts: list[int] = []

        def record_prefetch_rows(
            _connection,
            cursor,
            statement: str,
            _parameters,
            _context,
            _executemany: bool,
        ) -> None:
            lowered = statement.lower()
            if (
                "select review_items.id" in lowered
                and "review_items.current_decision_id is not null" in lowered
            ):
                prefetch_row_counts.append(cursor.rowcount)

        event.listen(self.engine, "after_cursor_execute", record_prefetch_rows)
        try:
            with Session(self.engine) as db:
                self.assertEqual(
                    find_related_target_refs(db, entity, limit=1),
                    (TargetRef(ReviewTargetTable.PERSON_ROLES, role.id),),
                )
        finally:
            event.remove(self.engine, "after_cursor_execute", record_prefetch_rows)

        self.assertTrue(prefetch_row_counts)
        self.assertGreaterEqual(min(prefetch_row_counts), 0)
        self.assertLessEqual(
            max(prefetch_row_counts),
            2,
            "ambiguous projection prefetch materialized more than two rows for one target",
        )

    def test_invalid_or_missing_target_is_rejected_without_inference(self) -> None:
        cases = (
            self._item(
                ReviewCaseType.PERSON_IDENTITY,
                ReviewTargetTable.PERSON_ROLES,
                None,
            ),
            self._item(
                ReviewCaseType.PERSON_IDENTITY,
                ReviewTargetTable.SCIENTIFIC_PRODUCTIONS,
                999,
            ),
            self._item(
                ReviewCaseType.PERSON_IDENTITY,
                ReviewTargetTable.PERSON_ROLES,
                999,
            ),
        )
        with Session(self.engine) as db:
            for item in cases:
                with self.subTest(target_table=item.target_table, target_pk=item.target_pk):
                    with self.assertRaises((LookupError, ValueError)):
                        resolve_related_entity(db, item)


class RelatedQueryServiceTests(unittest.TestCase):
    """Behavior contract for the closed, read-only related-case projection."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._schema = isolated_postgres_schema(
            require_b2b1_test_database_url(),
            "b2b2_related_query",
        )
        cls.engine = cls._schema.__enter__()
        try:
            Base.metadata.create_all(cls.engine)
        except Exception:
            cls._schema.__exit__(*__import__("sys").exc_info())
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()
        cls._schema.__exit__(None, None, None)

    def setUp(self) -> None:
        table_names = ", ".join(
            self.engine.dialect.identifier_preparer.quote(table.name)
            for table in reversed(Base.metadata.sorted_tables)
        )
        with self.engine.begin() as connection:
            connection.execute(text(
                f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"
            ))
        self._seed_dimensions_and_target()

    def _seed_dimensions_and_target(self) -> None:
        with Session(self.engine) as db, db.begin():
            db.add_all((
                Faculty(id=1, name="Engineering"),
                Career(id=1, faculty_id=1, name="Computing", code="CS"),
                AcademicPeriod(id=21, year_label="2026", cycle=1),
            ))
            db.flush()
            db.add(PersonRole(
                id=701,
                period_id=21,
                role_type="researcher",
                person_type="internal",
                canonical_name="Related Person",
                raw_name="Related Person",
                normalized_name="related person",
                raw_value=r"C:\private\dropbox\related-person.pdf",
                normalized_value="related person",
                source_page=3,
                source_section=r"C:\private\source",
                created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            ))

    def _http_client(self, db: Session) -> TestClient:
        app = FastAPI()
        app.include_router(api_router, prefix="/api/v1")
        app.dependency_overrides[dependencies.get_db] = lambda: db
        app.dependency_overrides[dependencies.get_current_user] = lambda: SimpleNamespace(
            id=17,
            is_active=True,
            role="FACULTY_ADMIN",
            faculty_id=1,
        )
        return TestClient(app, raise_server_exceptions=False)

    def test_http_missing_and_existing_out_of_scope_anchors_are_indistinguishable(self) -> None:
        unsupported = self._review_item(991)
        unsupported.case_type = ReviewCaseType.INVALID_TEXT.value
        unsupported.stable_target_key = f"b2b:v1:invalid_text:{991:064x}"
        missing_id = UUID("80000000-0000-0000-0000-000000000099")
        correlation_id = UUID("80000000-0000-0000-0000-000000000003")
        with Session(self.engine, expire_on_commit=False) as db:
            db.add(unsupported)
            db.commit()
            with self._http_client(db) as client, patch.object(
                dependencies,
                "authorize_b2b_action",
                return_value=B2BCapability.RESEARCH_MANAGER,
            ):
                out_of_scope = client.get(
                    f"/api/v1/human-review/cases/{unsupported.id}/related",
                    headers={"X-Correlation-ID": str(correlation_id)},
                )
                missing = client.get(
                    f"/api/v1/human-review/cases/{missing_id}/related",
                    headers={"X-Correlation-ID": str(correlation_id)},
                )

        self.assertEqual(out_of_scope.status_code, 404)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(out_of_scope.content, missing.content)
        for response in (out_of_scope, missing):
            self.assertEqual(
                response.headers["cache-control"],
                "private, no-store, max-age=0",
            )
            self.assertEqual(
                response.json()["correlation_id"],
                response.headers["x-correlation-id"],
            )

    def test_http_orphan_anchor_remains_a_sanitized_internal_503(self) -> None:
        orphan = self._review_item(992, target_pk=999999)
        correlation_id = UUID("80000000-0000-0000-0000-000000000003")
        with Session(self.engine, expire_on_commit=False) as db:
            db.add(orphan)
            db.commit()
            with self._http_client(db) as client, patch.object(
                dependencies,
                "authorize_b2b_action",
                return_value=B2BCapability.RESEARCH_MANAGER,
            ):
                response = client.get(
                    f"/api/v1/human-review/cases/{orphan.id}/related",
                    headers={"X-Correlation-ID": str(correlation_id)},
                )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "HUMAN_REVIEW_INTERNAL_ERROR")
        self.assertEqual(response.headers["x-correlation-id"], str(correlation_id))
        self.assertEqual(
            response.headers["cache-control"],
            "private, no-store, max-age=0",
        )
        self.assertNotIn("target record", response.text.casefold())

    def test_http_corrupt_anchor_remains_a_sanitized_internal_503(self) -> None:
        corrupt = self._review_item(993)
        corrupt.target_pk = None
        correlation_id = UUID("80000000-0000-0000-0000-000000000003")
        with Session(self.engine, expire_on_commit=False) as db:
            db.add(corrupt)
            db.commit()
            with self._http_client(db) as client, patch.object(
                dependencies,
                "authorize_b2b_action",
                return_value=B2BCapability.RESEARCH_MANAGER,
            ):
                response = client.get(
                    f"/api/v1/human-review/cases/{corrupt.id}/related",
                    headers={"X-Correlation-ID": str(correlation_id)},
                )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "HUMAN_REVIEW_INTERNAL_ERROR")
        self.assertEqual(response.headers["x-correlation-id"], str(correlation_id))
        self.assertEqual(
            response.headers["cache-control"],
            "private, no-store, max-age=0",
        )
        self.assertNotIn("target_pk", response.text.casefold())

    @staticmethod
    def _schema_item(*, case_id: UUID | None = None) -> RelatedReviewItem:
        return RelatedReviewItem(
            case_id=case_id or uuid4(),
            case_type=ReviewCaseType.PERSON_IDENTITY,
            case_status=ReviewCaseStatus.PENDING,
            scientific_status=ScientificStatus.PENDING,
            version=1,
            current_decision_id=None,
            detected_value="Detected",
            normalized_value="detected",
            canonical_value="Canonical",
            possible_kpi_impact=False,
            allowed_actions=("approve", "correct", "link"),
            evidence_summary={"available": False, "count": 0},
        )

    def _review_item(
        self,
        ordinal: int,
        *,
        case_type: ReviewCaseType = ReviewCaseType.PERSON_IDENTITY,
        case_status: ReviewCaseStatus = ReviewCaseStatus.PENDING,
        manual_priority: int | None = None,
        automatic_priority: int = 0,
        created_at: datetime | None = None,
        target_pk: int = 701,
    ) -> ReviewItem:
        raw_value = r"C:\private\dropbox\related-person.pdf"
        return ReviewItem(
            id=UUID(int=ordinal),
            case_type=case_type.value,
            stable_target_key=f"b2b:v1:{case_type.value}:{ordinal:064x}",
            target_table=ReviewTargetTable.PERSON_ROLES.value,
            target_pk=target_pk,
            scope_faculty_id=1,
            scope_career_id=1,
            document_key=r"dropbox:C:\private\related.pdf",
            source_revision="revision-1",
            source_page=3,
            source_section=r"C:\private\source",
            row_or_block_id=f"row-{ordinal}",
            field_path="canonical_name",
            raw_value_sha256=raw_value_sha256(raw_value),
            period_id=21,
            case_status=case_status.value,
            scientific_status=ScientificStatus.PENDING.value,
            automatic_priority=automatic_priority,
            manual_priority=manual_priority,
            possible_kpi_impact=ordinal % 2 == 0,
            version=ordinal,
            created_at=created_at or datetime(2026, 8, 2, tzinfo=timezone.utc),
            updated_at=created_at or datetime(2026, 8, 2, tzinfo=timezone.utc),
        )

    def _seed_ordered_cases(self) -> tuple[ReviewItem, tuple[ReviewItem, ...]]:
        anchor = self._review_item(
            100,
            case_status=ReviewCaseStatus.RESOLVED,
        )
        created = datetime(2026, 8, 3, tzinfo=timezone.utc)
        pending = (
            self._review_item(11, manual_priority=8, automatic_priority=4, created_at=created),
            self._review_item(12, manual_priority=8, automatic_priority=4, created_at=created),
            self._review_item(13, manual_priority=8, automatic_priority=1, created_at=created),
            self._review_item(
                14,
                case_type=ReviewCaseType.POSSIBLE_DUPLICATE,
                manual_priority=None,
                automatic_priority=99,
                created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            ),
            self._review_item(21, case_status=ReviewCaseStatus.REOPENED),
        )
        excluded = (
            self._review_item(20, case_status=ReviewCaseStatus.IN_REVIEW),
            self._review_item(22, case_type=ReviewCaseType.INVALID_TEXT),
            self._review_item(23, target_pk=9999),
        )
        with Session(self.engine, expire_on_commit=False) as db, db.begin():
            db.add_all((anchor, *pending, *excluded))
        return anchor, pending

    def _seed_many_distinct_related_targets(
        self,
        related_count: int,
    ) -> UUID:
        identity = CanonicalIdentity(
            id=UUID(int=700),
            canonical_identity_key="identity:related-query-budget",
            identity_type="internal_person",
            display_name="Related Query Budget",
            status="active",
            origin="human",
        )
        roles = tuple(
            PersonRole(
                id=800 + index,
                period_id=21,
                role_type="researcher",
                person_type="internal",
                canonical_identity_key=identity.canonical_identity_key,
                canonical_name=f"Related Query Budget {index}",
                raw_name=f"Related Query Budget {index}",
                raw_value=f"Related Query Budget {index}",
                normalized_name=f"related query budget {index}",
                normalized_value=f"related query budget {index}",
                created_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
            )
            for index in range(related_count + 1)
        )
        anchor = self._review_item(
            700,
            case_status=ReviewCaseStatus.RESOLVED,
            target_pk=roles[0].id,
        )
        anchor.raw_value_sha256 = raw_value_sha256(roles[0].raw_value)
        pending = tuple(
            self._review_item(
                701 + index,
                target_pk=role.id,
                automatic_priority=related_count - index,
            )
            for index, role in enumerate(roles[1:])
        )
        for row, role in zip(pending, roles[1:]):
            row.raw_value_sha256 = raw_value_sha256(role.raw_value)
        with Session(self.engine, expire_on_commit=False) as db, db.begin():
            db.add(identity)
            db.add_all(roles)
            db.add(anchor)
            db.add_all(pending)
        return anchor.id

    def _seed_large_related_evidence_set(self, trace_count: int) -> tuple[UUID, UUID]:
        job = ImportJob(
            id=900,
            source_type="PROGRESS_PDF",
            filename="safe-list-evidence.pdf",
            status="SUCCESS",
            source_identifier="id:list-evidence",
            source_rev="revision-1",
            document_key="dropbox_path:/private/list-evidence.pdf",
            is_current=True,
            page_count=3,
            created_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
        )
        target = PersonRole(
            id=901,
            period_id=21,
            import_job_id=job.id,
            role_type="researcher",
            person_type="internal",
            canonical_name="Bounded List Evidence",
            raw_name="Bounded List Evidence",
            raw_value="Bounded list evidence",
            normalized_name="bounded list evidence",
            normalized_value="bounded list evidence",
            source_page=3,
            source_section="List evidence",
            metadata_json={"row_or_block_id": "row-901"},
            created_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
        )
        anchor = self._review_item(
            900,
            case_status=ReviewCaseStatus.RESOLVED,
            target_pk=target.id,
        )
        pending = self._review_item(901, target_pk=target.id)
        for item in (anchor, pending):
            item.document_key = job.document_key
            item.source_revision = job.source_rev
            item.source_page = target.source_page
            item.source_section = target.source_section
            item.row_or_block_id = "row-901"
            item.raw_value_sha256 = raw_value_sha256(target.raw_value)
        traces = tuple(
            ImportedOcrTrace(
                id=10_000 + index,
                import_job_id=job.id,
                source_filename="safe-list-evidence.pdf",
                source_path="/private/list-evidence.pdf",
                extracted_text="Private extracted source text",
                parsed_payload={
                    "document": {
                        "name": "safe-list-evidence.pdf",
                        "path_lower": "/private/list-evidence.pdf",
                        "rev": "revision-1",
                        "size": 2048,
                        "content_hash": "a" * 64,
                    }
                },
                created_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
            )
            for index in range(trace_count)
        )
        with Session(self.engine, expire_on_commit=False) as db, db.begin():
            db.add_all((job, target, anchor, pending, *traces))
        return anchor.id, pending.id

    def _seed_projection_with_irrelevant_active_overrides(
        self,
        irrelevant_count: int,
    ) -> ReviewItem:
        item = self._review_item(
            950,
            case_status=ReviewCaseStatus.RESOLVED,
        )
        item.scientific_status = ScientificStatus.VALIDATED.value
        first_id = uuid4()
        second_id = uuid4()
        reversal_id = uuid4()
        scalar = {
            "kind": "string",
            "string_value": ScientificStatus.VALIDATED.value,
            "integer_value": None,
            "decimal_value": None,
            "boolean_value": None,
        }
        override = {
            "schema_version": 1,
            "field_path": "scientific_status",
            "projected_value": scalar,
            "scope": "record",
            "stable_target_key": item.stable_target_key,
            "target_table": item.target_table,
            "target_pk": item.target_pk,
            "document_key": None,
            "period_id": None,
            "relationship_key": None,
            "locked": True,
        }
        identity = {
            "schema_version": 1,
            "canonical_identity_key": "identity:bounded-prefetch",
            "canonical_name": "Bounded Prefetch",
            "identity_type": "internal_person",
            "aliases": [],
        }
        snapshot = {
            "schema_version": 1,
            "case_status": ReviewCaseStatus.RESOLVED.value,
            "scientific_status": ScientificStatus.VALIDATED.value,
            "current_decision_id": str(first_id),
            "identity": identity,
            "overrides": [override],
        }
        first = ReviewDecision(
            id=first_id,
            review_item_id=item.id,
            sequence=1,
            decision_type="corrected",
            decision_lifecycle="approved",
            scope="record",
            payload_schema="review.decision.v1",
            payload_version=1,
            payload={
                "kind": "identity",
                "schema_version": 1,
                "canonical_identity_key": identity["canonical_identity_key"],
                "canonical_name": identity["canonical_name"],
                "identity_type": identity["identity_type"],
                "alias_original": None,
                "alias_normalized": None,
                "projection_before": {
                    "schema_version": 1,
                    "case_status": "pending",
                    "scientific_status": "pending",
                    "current_decision_id": None,
                    "identity": None,
                    "overrides": [],
                },
                "projection_after": snapshot,
            },
            actor_type="legacy",
            actor_identifier="legacy:test-bounded-prefetch",
            expected_case_version=1,
            locks_projection=True,
        )
        second_identity = {
            **identity,
            "canonical_identity_key": "identity:bounded-prefetch-second",
            "canonical_name": "Bounded Prefetch Second",
        }
        second_snapshot = {
            **snapshot,
            "current_decision_id": str(second_id),
            "identity": second_identity,
        }
        second = ReviewDecision(
            id=second_id,
            review_item_id=item.id,
            sequence=2,
            decision_type="corrected",
            decision_lifecycle="approved",
            scope="record",
            payload_schema="review.decision.v1",
            payload_version=1,
            payload={
                "kind": "identity",
                "schema_version": 1,
                "canonical_identity_key": second_identity[
                    "canonical_identity_key"
                ],
                "canonical_name": second_identity["canonical_name"],
                "identity_type": second_identity["identity_type"],
                "alias_original": None,
                "alias_normalized": None,
                "projection_before": snapshot,
                "projection_after": second_snapshot,
            },
            actor_type="legacy",
            actor_identifier="legacy:test-bounded-prefetch",
            expected_case_version=2,
            previous_decision_id=first.id,
            locks_projection=True,
        )
        reversal = ReviewDecision(
            id=reversal_id,
            review_item_id=item.id,
            sequence=3,
            decision_type="reverted",
            decision_lifecycle="approved",
            scope="record",
            payload_schema="review.decision.v1",
            payload_version=1,
            payload={
                "kind": "decision_reversal",
                "schema_version": 1,
                "decision_id_to_revert": str(second.id),
                "restore_decision_id": str(first.id),
                "projection_before": second_snapshot,
                "projection_after": snapshot,
            },
            actor_type="legacy",
            actor_identifier="legacy:test-bounded-prefetch",
            expected_case_version=3,
            previous_decision_id=second.id,
            corrects_decision_id=second.id,
            locks_projection=True,
        )
        exact = FieldOverride(
            id=uuid4(),
            review_item_id=item.id,
            decision_id=reversal.id,
            stable_target_key=item.stable_target_key,
            target_table=item.target_table,
            target_pk=item.target_pk,
            field_path="scientific_status",
            value_schema="override.scalar.v1",
            value_version=1,
            projected_value=scalar,
            scope="record",
            locked=True,
            is_active=True,
        )
        irrelevant = tuple(
            FieldOverride(
                id=uuid4(),
                review_item_id=item.id,
                decision_id=reversal.id,
                stable_target_key=item.stable_target_key,
                target_table=item.target_table,
                target_pk=item.target_pk,
                field_path="scientific_status",
                value_schema="override.scalar.v1",
                value_version=1,
                projected_value=scalar,
                scope="document",
                document_key=f"dropbox_path:/irrelevant/{index}.pdf",
                locked=True,
                is_active=True,
            )
            for index in range(irrelevant_count)
        )
        with Session(self.engine, expire_on_commit=False) as db, db.begin():
            db.add(item)
            db.flush()
            db.add_all((first, second, reversal))
            db.flush()
            item.current_decision_id = reversal.id
            db.add_all((exact, *irrelevant))
        return item

    def _seed_fully_active_related_targets(
        self,
        related_count: int,
    ) -> tuple[UUID, dict[UUID, str]]:
        identity_key = "identity:fully-active-related"
        identity = CanonicalIdentity(
            id=UUID(int=1_050),
            canonical_identity_key=identity_key,
            identity_type="internal_person",
            display_name="Fully Active Related",
            status="active",
            origin="human",
        )
        anchor_target = PersonRole(
            id=1_100,
            period_id=21,
            role_type="researcher",
            person_type="internal",
            canonical_identity_key=identity_key,
            canonical_name="Fully Active Anchor",
            raw_name="Fully Active Anchor",
            raw_value="Fully Active Anchor",
            normalized_name="fully active anchor",
            normalized_value="fully active anchor",
            created_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
        )
        anchor = self._review_item(
            1_100,
            case_status=ReviewCaseStatus.RESOLVED,
            target_pk=anchor_target.id,
        )
        anchor.raw_value_sha256 = raw_value_sha256(anchor_target.raw_value)
        jobs: list[ImportJob] = []
        targets: list[PersonRole] = []
        items: list[ReviewItem] = []
        decisions: list[ReviewDecision] = []
        current_decisions: list[ReviewDecision] = []
        overrides: list[FieldOverride] = []
        traces: list[ImportedOcrTrace] = []
        for index in range(related_count):
            job_id = 1_200 + index
            target_id = 1_300 + index
            filename = f"active-related-{index}.pdf"
            source_path = f"/private/{filename}"
            jobs.append(ImportJob(
                id=job_id,
                source_type="PROGRESS_PDF",
                filename=filename,
                status="SUCCESS",
                source_identifier=f"id:active-related:{index}",
                source_rev="revision-1",
                document_key=f"dropbox_path:{source_path}",
                is_current=True,
                page_count=3,
                created_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
            ))
            raw_value = f"Fully Active Related {index}"
            target = PersonRole(
                id=target_id,
                period_id=21,
                import_job_id=job_id,
                role_type="researcher",
                person_type="internal",
                canonical_identity_key=identity_key,
                canonical_name=raw_value,
                raw_name=raw_value,
                raw_value=raw_value,
                normalized_name=raw_value.casefold(),
                normalized_value=raw_value.casefold(),
                source_page=2,
                source_section="Fully active evidence",
                metadata_json={"row_or_block_id": f"row-{target_id}"},
                created_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
            )
            item = self._review_item(
                1_300 + index,
                target_pk=target.id,
                automatic_priority=related_count - index,
            )
            item.document_key = jobs[-1].document_key
            item.source_revision = jobs[-1].source_rev
            item.source_page = target.source_page
            item.source_section = target.source_section
            item.row_or_block_id = target.metadata_json["row_or_block_id"]
            item.raw_value_sha256 = raw_value_sha256(target.raw_value)
            first_id = uuid4()
            second_id = uuid4()
            reversal_id = uuid4()
            scalar = {
                "kind": "string",
                "string_value": ScientificStatus.PENDING.value,
                "integer_value": None,
                "decimal_value": None,
                "boolean_value": None,
            }
            override = {
                "schema_version": 1,
                "field_path": "scientific_status",
                "projected_value": scalar,
                "scope": "record",
                "stable_target_key": item.stable_target_key,
                "target_table": item.target_table,
                "target_pk": item.target_pk,
                "document_key": None,
                "period_id": None,
                "relationship_key": None,
                "locked": True,
            }
            restored_identity = {
                "schema_version": 1,
                "canonical_identity_key": identity_key,
                "canonical_name": identity.display_name,
                "identity_type": "internal_person",
                "aliases": [],
            }
            initial_snapshot = {
                "schema_version": 1,
                "case_status": ReviewCaseStatus.PENDING.value,
                "scientific_status": ScientificStatus.PENDING.value,
                "current_decision_id": None,
                "identity": None,
                "overrides": [],
            }
            restored_snapshot = {
                "schema_version": 1,
                "case_status": ReviewCaseStatus.PENDING.value,
                "scientific_status": ScientificStatus.PENDING.value,
                "current_decision_id": str(first_id),
                "identity": restored_identity,
                "overrides": [override],
            }
            first = ReviewDecision(
                id=first_id,
                review_item_id=item.id,
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
                    "canonical_name": identity.display_name,
                    "identity_type": "internal_person",
                    "alias_original": None,
                    "alias_normalized": None,
                    "projection_before": initial_snapshot,
                    "projection_after": restored_snapshot,
                },
                actor_type="legacy",
                actor_identifier="legacy:test-active-related",
                expected_case_version=1,
                locks_projection=True,
            )
            superseded_identity = {
                **restored_identity,
                "canonical_identity_key": f"identity:superseded:{index}",
                "canonical_name": f"Superseded Active Related {index}",
            }
            superseded_snapshot = {
                **restored_snapshot,
                "current_decision_id": str(second_id),
                "identity": superseded_identity,
            }
            second = ReviewDecision(
                id=second_id,
                review_item_id=item.id,
                sequence=2,
                decision_type="corrected",
                decision_lifecycle="approved",
                scope="record",
                payload_schema="review.decision.v1",
                payload_version=1,
                payload={
                    "kind": "identity",
                    "schema_version": 1,
                    "canonical_identity_key": superseded_identity[
                        "canonical_identity_key"
                    ],
                    "canonical_name": superseded_identity["canonical_name"],
                    "identity_type": "internal_person",
                    "alias_original": None,
                    "alias_normalized": None,
                    "projection_before": restored_snapshot,
                    "projection_after": superseded_snapshot,
                },
                actor_type="legacy",
                actor_identifier="legacy:test-active-related",
                expected_case_version=2,
                previous_decision_id=first.id,
                locks_projection=True,
            )
            reversal = ReviewDecision(
                id=reversal_id,
                review_item_id=item.id,
                sequence=3,
                decision_type="reverted",
                decision_lifecycle="approved",
                scope="record",
                payload_schema="review.decision.v1",
                payload_version=1,
                payload={
                    "kind": "decision_reversal",
                    "schema_version": 1,
                    "decision_id_to_revert": str(second.id),
                    "restore_decision_id": str(first.id),
                    "projection_before": superseded_snapshot,
                    "projection_after": restored_snapshot,
                },
                actor_type="legacy",
                actor_identifier="legacy:test-active-related",
                expected_case_version=3,
                previous_decision_id=second.id,
                corrects_decision_id=second.id,
                locks_projection=True,
            )
            decisions.extend((first, second, reversal))
            current_decisions.append(reversal)
            overrides.append(FieldOverride(
                id=uuid4(),
                review_item_id=item.id,
                decision_id=reversal.id,
                stable_target_key=item.stable_target_key,
                target_table=item.target_table,
                target_pk=item.target_pk,
                field_path="scientific_status",
                value_schema="override.scalar.v1",
                value_version=1,
                projected_value=scalar,
                scope="record",
                locked=True,
                is_active=True,
            ))
            traces.append(ImportedOcrTrace(
                id=20_000 + index,
                import_job_id=job_id,
                source_filename=filename,
                source_path=source_path,
                extracted_text="Private extracted source text",
                parsed_payload={
                    "document": {
                        "name": filename,
                        "path_lower": source_path,
                        "rev": "revision-1",
                        "size": 2048,
                        "content_hash": "b" * 64,
                    }
                },
                created_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
            ))
            targets.append(target)
            items.append(item)
        with Session(self.engine, expire_on_commit=False) as db, db.begin():
            db.add_all((identity, anchor_target, anchor, *jobs, *targets, *items))
            db.flush()
            db.add_all((*decisions, *traces))
            db.flush()
            for item, decision in zip(items, current_decisions):
                item.current_decision_id = decision.id
            db.add_all(overrides)
        return anchor.id, {
            item.id: item.stable_target_key for item in items
        }

    @staticmethod
    def _table_hashes(engine) -> dict[str, str]:
        tables = (
            ReviewItem.__table__,
            ReviewDecision.__table__,
            FieldOverride.__table__,
            AuditEvent.__table__,
        )
        result: dict[str, str] = {}
        with engine.connect() as connection:
            for table in tables:
                rows = connection.execute(
                    select(table).order_by(table.c.id)
                ).mappings().all()
                encoded = json.dumps(
                    [dict(row) for row in rows],
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
                result[table.name] = hashlib.sha256(encoded).hexdigest()
        return result

    @staticmethod
    def _plan_nodes(plan: dict[str, object]) -> tuple[tuple[str, str | None], ...]:
        found: list[tuple[str, str | None]] = []

        def visit(node: dict[str, object]) -> None:
            found.append((str(node.get("Node Type")), node.get("Index Name")))
            for child in node.get("Plans", ()):
                visit(child)

        visit(plan)
        return tuple(found)

    def test_related_schemas_are_closed_and_enforce_item_bounds(self) -> None:
        entity = RelatedReviewEntity(
            public_type="person",
            public_id="person_roles:701",
            display_name="Related Person",
        )
        item = self._schema_item()
        response = RelatedReviewResponse(
            entity=entity,
            items=(item,),
            total_pending=1,
            truncated=False,
            correlation_id=uuid4(),
        )
        model_payloads = (
            (RelatedReviewEntity, entity.model_dump()),
            (RelatedReviewItem, item.model_dump()),
            (RelatedReviewResponse, response.model_dump()),
        )
        for model, payload in model_payloads:
            with self.subTest(model=model.__name__), self.assertRaises(ValidationError):
                model.model_validate({**payload, "private_internal": "forbidden"})

        with self.assertRaises(ValidationError):
            RelatedReviewResponse(
                entity=entity,
                items=tuple(self._schema_item() for _ in range(51)),
                total_pending=51,
                truncated=False,
                correlation_id=uuid4(),
            )

    def test_related_entity_sanitizes_long_display_names_before_length_validation(self) -> None:
        entity = RelatedReviewEntity(
            public_type="scientific_product",
            public_id="scientific_productions:239",
            display_name="Producto corregido " * 40,
        )

        self.assertLessEqual(len(entity.display_name), 220)
        self.assertTrue(entity.display_name.startswith("Producto corregido"))

    def test_related_response_rejects_duplicate_cases_and_unsafe_truncation_total(self) -> None:
        entity = RelatedReviewEntity(
            public_type="person",
            public_id="person_roles:701",
            display_name="Related Person",
        )
        duplicated = self._schema_item(case_id=UUID(int=50))
        with self.assertRaises(ValidationError):
            RelatedReviewResponse(
                entity=entity,
                items=(duplicated, duplicated),
                total_pending=2,
                truncated=False,
                correlation_id=uuid4(),
            )
        with self.assertRaises(ValidationError):
            RelatedReviewResponse(
                entity=entity,
                items=(self._schema_item(), self._schema_item()),
                total_pending=1,
                truncated=True,
                correlation_id=uuid4(),
            )

    def test_get_related_returns_only_pending_readable_cases_in_exact_priority_order(self) -> None:
        anchor, pending = self._seed_ordered_cases()
        correlation_id = uuid4()
        with Session(self.engine) as db:
            response = HumanReviewQueryService(
                db,
                correlation_id=correlation_id,
            ).get_related(anchor.id)

        self.assertEqual(tuple(item.case_id for item in response.items), tuple(row.id for row in pending))
        self.assertEqual(response.total_pending, 5)
        self.assertFalse(response.truncated)
        self.assertEqual(response.correlation_id, correlation_id)
        self.assertEqual(response.entity.public_type, "person")
        self.assertEqual(response.entity.public_id, "person_roles:701")

    def test_allowed_actions_are_projected_from_the_existing_decision_matrix(self) -> None:
        anchor, _ = self._seed_ordered_cases()
        with Session(self.engine) as db:
            response = HumanReviewQueryService(db).get_related(anchor.id)

        by_case = {item.case_type: item.allowed_actions for item in response.items}
        expected = {
            case_type: tuple(
                action
                for matrix_case, action in CASE_ACTION_DECISION_TYPES
                if matrix_case is case_type
            )
            for case_type in by_case
        }
        self.assertEqual(by_case, expected)
        self.assertEqual(by_case[ReviewCaseType.PERSON_IDENTITY], ("approve", "correct", "link"))
        self.assertEqual(by_case[ReviewCaseType.POSSIBLE_DUPLICATE], ("link",))

    def test_get_related_clamps_limits_and_serializes_no_private_locator_data(self) -> None:
        anchor, _ = self._seed_ordered_cases()
        with Session(self.engine) as db:
            low = HumanReviewQueryService(db).get_related(anchor.id, limit=0)
            high = HumanReviewQueryService(db).get_related(anchor.id, limit=500)

        self.assertEqual(len(low.items), 1)
        self.assertEqual(low.total_pending, 2)
        self.assertTrue(low.truncated)
        self.assertEqual(len(high.items), 5)
        self.assertEqual(high.total_pending, 5)
        self.assertFalse(high.truncated)
        serialized = json.dumps(high.model_dump(mode="json"), sort_keys=True)
        lowered = serialized.casefold()
        for forbidden in (
            "stable_target_key",
            "document_key",
            "target_table",
            "target_pk",
            "source_path",
            "bucket",
            "object_key",
            "dropbox_path",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, lowered)
        self.assertNotIn("\\", serialized)

    def test_get_related_is_read_only_and_does_not_autoflush_caller_state(self) -> None:
        anchor, _ = self._seed_ordered_cases()
        before = self._table_hashes(self.engine)
        with Session(self.engine) as db:
            unrelated = Faculty(id=999, name=None)
            db.add(unrelated)
            new_before = frozenset(db.new)
            dirty_before = frozenset(db.dirty)
            deleted_before = frozenset(db.deleted)
            response = HumanReviewQueryService(db).get_related(anchor.id)
            self.assertEqual(len(response.items), 5)
            self.assertEqual(frozenset(db.new), new_before)
            self.assertEqual(frozenset(db.dirty), dirty_before)
            self.assertEqual(frozenset(db.deleted), deleted_before)
        after = self._table_hashes(self.engine)
        self.assertEqual(after, before)

    def test_related_review_query_uses_existing_target_index_on_representative_data(self) -> None:
        anchor, _ = self._seed_ordered_cases()
        noise = tuple(
            self._review_item(10_000 + index, target_pk=20_000 + index)
            for index in range(3000)
        )
        with Session(self.engine) as db, db.begin():
            db.add_all(noise)
        with self.engine.begin() as connection:
            connection.execute(text("ANALYZE review_items"))

        captured: list[tuple[str, object]] = []

        def record_pending_query(
            _connection,
            _cursor,
            statement: str,
            parameters,
            _context,
            _executemany: bool,
        ) -> None:
            lowered = statement.lower()
            if (
                "from review_items" in lowered
                and "case_status" in lowered
                and "manual_priority" in lowered
                and "order by" in lowered
            ):
                captured.append((statement, parameters))

        event.listen(self.engine, "before_cursor_execute", record_pending_query)
        try:
            with Session(self.engine) as db:
                HumanReviewQueryService(db).get_related(anchor.id, limit=3)
        finally:
            event.remove(self.engine, "before_cursor_execute", record_pending_query)

        self.assertTrue(captured, "the service did not issue its pending related-case query")
        statement, parameters = captured[-1]
        with self.engine.connect() as connection:
            explained = connection.exec_driver_sql(
                f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {statement}",
                parameters,
            ).scalar_one()
        root = explained[0]["Plan"]
        nodes = self._plan_nodes(root)
        self.assertIn(
            "ix_review_items_target",
            {index_name for _, index_name in nodes},
            f"related query plan did not use ix_review_items_target: {nodes}",
        )

    def test_get_related_statement_count_does_not_grow_per_returned_item(self) -> None:
        anchor_id = self._seed_many_distinct_related_targets(50)

        def execute_and_count(limit: int) -> tuple[int, int]:
            statements: list[str] = []

            def record_statement(
                _connection,
                _cursor,
                statement: str,
                _parameters,
                _context,
                _executemany: bool,
            ) -> None:
                if statement.lstrip().casefold().startswith(("select", "with")):
                    statements.append(statement)

            event.listen(self.engine, "before_cursor_execute", record_statement)
            try:
                with Session(self.engine) as db:
                    response = HumanReviewQueryService(db).get_related(
                        anchor_id,
                        limit=limit,
                    )
            finally:
                event.remove(self.engine, "before_cursor_execute", record_statement)
            return len(response.items), len(statements)

        one_items, one_statements = execute_and_count(1)
        many_items, many_statements = execute_and_count(50)

        self.assertEqual(one_items, 1)
        self.assertEqual(many_items, 50)
        self.assertLessEqual(
            many_statements,
            one_statements + 1,
            f"related list statements grew per item: one={one_statements}, many={many_statements}",
        )

    def test_fully_active_related_workload_has_constant_statements_and_bounded_rows(self) -> None:
        anchor_id, stable_keys = self._seed_fully_active_related_targets(50)

        def execute_and_measure(limit: int) -> tuple[int, int, int]:
            statements: list[str] = []
            relevant_row_counts: list[int] = []

            def record_statement(
                _connection,
                cursor,
                statement: str,
                _parameters,
                _context,
                _executemany: bool,
            ) -> None:
                lowered = statement.casefold()
                if statement.lstrip().casefold().startswith(("select", "with")):
                    statements.append(statement)
                if any(
                    table in lowered
                    for table in (
                        "review_decisions",
                        "field_overrides",
                        "imported_ocr_traces",
                    )
                ):
                    relevant_row_counts.append(cursor.rowcount)

            event.listen(self.engine, "after_cursor_execute", record_statement)
            try:
                with Session(self.engine) as db:
                    response = HumanReviewQueryService(db).get_related(
                        anchor_id,
                        limit=limit,
                    )
            finally:
                event.remove(self.engine, "after_cursor_execute", record_statement)
            self.assertTrue(all(
                item.current_decision_id is not None
                and item.evidence_summary["available"]
                and item.canonical_value == "Fully Active Related"
                for item in response.items
            ))
            return (
                len(response.items),
                len(statements),
                max(relevant_row_counts, default=0),
            )

        one_items, one_statements, one_rows = execute_and_measure(1)
        many_items, many_statements, many_rows = execute_and_measure(50)

        self.assertEqual((one_items, many_items), (1, 50))
        self.assertEqual(many_statements, one_statements)
        self.assertLessEqual(one_rows, 32)
        self.assertLessEqual(
            many_rows,
            50,
            "the 50-item workload may return one bounded projection row per item, never an unbounded history",
        )
        with Session(self.engine) as db:
            source = EffectiveHumanProjectionSource(db)
            with source.read_scope():
                source.prefetch_stable_target_keys(set(stable_keys.values()))
                projections = tuple(
                    source.for_stable_target_key(stable_key)
                    for stable_key in stable_keys.values()
                )
        self.assertEqual(len(projections), 50)
        self.assertTrue(all(
            projection is not None
            and projection.canonical_name == "Fully Active Related"
            and projection.overrides["scientific_status"] == "pending"
            for projection in projections
        ))

    def test_get_related_materializes_at_most_two_ocr_signatures_per_job(self) -> None:
        anchor_id, pending_id = self._seed_large_related_evidence_set(200)
        trace_row_counts: list[int] = []
        trace_yield_per: list[int | None] = []
        trace_stream_results: list[bool | None] = []

        def record_trace_rows(
            _connection,
            cursor,
            statement: str,
            _parameters,
            context,
            _executemany: bool,
        ) -> None:
            if "imported_ocr_traces" in statement.casefold():
                trace_row_counts.append(cursor.rowcount)
                trace_yield_per.append(context.execution_options.get("yield_per"))
                trace_stream_results.append(
                    context.execution_options.get("stream_results")
                )

        event.listen(self.engine, "after_cursor_execute", record_trace_rows)
        try:
            with Session(self.engine) as db:
                response = HumanReviewQueryService(db).get_related(anchor_id)
                materialized_trace_count = sum(
                    isinstance(instance, ImportedOcrTrace)
                    for instance in db.identity_map.values()
                )
        finally:
            event.remove(self.engine, "after_cursor_execute", record_trace_rows)

        self.assertEqual(tuple(item.case_id for item in response.items), (pending_id,))
        summary = response.items[0].evidence_summary
        self.assertTrue(summary["available"])
        self.assertEqual(summary["count"], 1)
        self.assertEqual(summary["document_name"], "safe-list-evidence.pdf")
        self.assertEqual(summary["page"], 3)
        self.assertEqual(summary["section"], "List evidence")
        self.assertEqual(summary["locator"], "row-901")
        self.assertEqual(summary["fragment"], "Bounded list evidence")
        self.assertEqual(
            summary["stream_path"],
            f"human-review/cases/{pending_id}/evidence",
        )
        self.assertTrue(trace_row_counts)
        self.assertEqual(
            trace_yield_per,
            [1],
            f"related list did not use one bounded trace stream: {trace_yield_per}",
        )
        self.assertEqual(trace_stream_results, [True])
        self.assertEqual(materialized_trace_count, 0)
        self.assertLessEqual(
            max(trace_row_counts),
            2,
            f"related list materialized unbounded OCR rows: {trace_row_counts}",
        )

    def test_related_evidence_preserves_json_types_used_by_detail_validation(self) -> None:
        anchor_id, pending_id = self._seed_large_related_evidence_set(2)
        with Session(self.engine) as db, db.begin():
            trace = db.get(ImportedOcrTrace, 10_001)
            self.assertIsNotNone(trace)
            trace.parsed_payload = {
                "document": {
                    "name": "safe-list-evidence.pdf",
                    "path_lower": "/private/list-evidence.pdf",
                    "rev": "revision-1",
                    "size": "2048",
                    "content_hash": "a" * 64,
                }
            }

        with Session(self.engine) as db:
            service = HumanReviewQueryService(db)
            related = service.get_related(anchor_id)
            detail = service.get_case(pending_id)

        self.assertFalse(detail.evidence_summary["available"])
        self.assertEqual(
            related.items[0].evidence_summary,
            detail.evidence_summary,
        )

    def test_related_evidence_ignores_lower_priority_fallback_variants(self) -> None:
        anchor_id, pending_id = self._seed_large_related_evidence_set(4)
        with Session(self.engine) as db, db.begin():
            traces = tuple(db.scalars(
                select(ImportedOcrTrace).order_by(ImportedOcrTrace.id)
            ))
            for index, trace in enumerate(traces):
                trace.parsed_payload = {
                    "document": {
                        "name": "safe-list-evidence.pdf",
                        "filename": f"ignored-name-{index}.pdf",
                        "path_lower": "/private/list-evidence.pdf",
                        "path_display": f"/ignored/display/{index}.pdf",
                        "source_key": f"ignored-source-{index}",
                        "source_path": f"/ignored/source/{index}.pdf",
                        "rev": "revision-1",
                        "size": 2048,
                        "content_hash": "a" * 64,
                    }
                }

        with Session(self.engine) as db:
            service = HumanReviewQueryService(db)
            related = service.get_related(anchor_id)
            detail = service.get_case(pending_id)

        self.assertTrue(detail.evidence_summary["available"])
        self.assertEqual(
            related.items[0].evidence_summary,
            detail.evidence_summary,
        )

    def test_related_evidence_preserves_selected_name_length_validity(self) -> None:
        anchor_id, pending_id = self._seed_large_related_evidence_set(2)
        with Session(self.engine) as db, db.begin():
            trace = db.get(ImportedOcrTrace, 10_001)
            self.assertIsNotNone(trace)
            trace.parsed_payload = {
                "document": {
                    "name": f"{'x' * 321}/safe-list-evidence.pdf",
                    "path_lower": "/private/list-evidence.pdf",
                    "rev": "revision-1",
                    "size": 2048,
                    "content_hash": "a" * 64,
                }
            }

        with Session(self.engine) as db:
            service = HumanReviewQueryService(db)
            related = service.get_related(anchor_id)
            detail = service.get_case(pending_id)

        self.assertFalse(detail.evidence_summary["available"])
        self.assertEqual(
            related.items[0].evidence_summary,
            detail.evidence_summary,
        )

    def test_related_evidence_canonicalizes_dropbox_id_whitespace(self) -> None:
        anchor_id, pending_id = self._seed_large_related_evidence_set(3)
        with Session(self.engine) as db, db.begin():
            job = db.get(ImportJob, 900)
            self.assertIsNotNone(job)
            job.document_key = "dropbox:id"
            for item in db.scalars(select(ReviewItem)):
                item.document_key = job.document_key
            traces = tuple(db.scalars(
                select(ImportedOcrTrace).order_by(ImportedOcrTrace.id)
            ))
            for trace, dropbox_id in zip(traces, ("id", " id", "id ")):
                trace.parsed_payload = {
                    "document": {
                        "dropbox_id": dropbox_id,
                        "name": "safe-list-evidence.pdf",
                        "path_lower": "/private/list-evidence.pdf",
                        "rev": "revision-1",
                        "size": 2048,
                        "content_hash": "a" * 64,
                    }
                }

        with Session(self.engine) as db:
            service = HumanReviewQueryService(db)
            related = service.get_related(anchor_id)
            detail = service.get_case(pending_id)

        self.assertTrue(detail.evidence_summary["available"])
        self.assertEqual(
            related.items[0].evidence_summary,
            detail.evidence_summary,
        )

    def test_related_evidence_normalizes_equivalent_winning_path_branches(self) -> None:
        anchor_id, pending_id = self._seed_large_related_evidence_set(3)
        common = {
            "name": "safe-list-evidence.pdf",
            "rev": "revision-1",
            "size": 2048,
            "content_hash": "a" * 64,
        }
        documents = (
            {**common, "path_lower": "/private/list-evidence.pdf"},
            {**common, "path_display": "/private/list-evidence.pdf"},
            {
                **common,
                "source_key": "/private/list-evidence.pdf",
                "source_path": "/private/list-evidence.pdf",
            },
        )
        with Session(self.engine) as db, db.begin():
            traces = tuple(db.scalars(
                select(ImportedOcrTrace).order_by(ImportedOcrTrace.id)
            ))
            for trace, document in zip(traces, documents):
                trace.parsed_payload = {"document": document}

        with Session(self.engine) as db:
            service = HumanReviewQueryService(db)
            related = service.get_related(anchor_id)
            detail = service.get_case(pending_id)

        self.assertTrue(detail.evidence_summary["available"])
        self.assertEqual(
            related.items[0].evidence_summary,
            detail.evidence_summary,
        )

    def test_related_evidence_discards_empty_segments_in_canonical_source_keys(self) -> None:
        anchor_id, pending_id = self._seed_large_related_evidence_set(3)
        source_keys = (
            "/private/list-evidence.pdf",
            "//private//list-evidence.pdf/",
            "\\private\\\\list-evidence.pdf\\",
        )
        with Session(self.engine) as db, db.begin():
            traces = tuple(db.scalars(
                select(ImportedOcrTrace).order_by(ImportedOcrTrace.id)
            ))
            for trace, source_key in zip(traces, source_keys):
                trace.parsed_payload = {
                    "document": {
                        "name": "safe-list-evidence.pdf",
                        "source_key": source_key,
                        "source_path": "/private/list-evidence.pdf",
                        "rev": "revision-1",
                        "size": 2048,
                        "content_hash": "a" * 64,
                    }
                }

        with Session(self.engine) as db:
            service = HumanReviewQueryService(db)
            related = service.get_related(anchor_id)
            detail = service.get_case(pending_id)

        self.assertTrue(detail.evidence_summary["available"])
        self.assertEqual(
            related.items[0].evidence_summary,
            detail.evidence_summary,
        )

    def test_related_evidence_uses_python_strip_for_canonical_source_keys(self) -> None:
        anchor_id, pending_id = self._seed_large_related_evidence_set(3)
        source_keys = (
            "/private/file.pdf",
            "\t/private/file.pdf\n",
            "\u2003/private/file.pdf\u00a0",
        )
        with Session(self.engine) as db, db.begin():
            job = db.get(ImportJob, 900)
            self.assertIsNotNone(job)
            job.document_key = "dropbox_path:/private/file.pdf"
            for item in db.scalars(select(ReviewItem)):
                item.document_key = job.document_key
            traces = tuple(db.scalars(
                select(ImportedOcrTrace).order_by(ImportedOcrTrace.id)
            ))
            for trace, source_key in zip(traces, source_keys):
                trace.parsed_payload = {
                    "document": {
                        "name": "safe-list-evidence.pdf",
                        "source_key": source_key,
                        "source_path": "/private/list-evidence.pdf",
                        "rev": "revision-1",
                        "size": 2048,
                        "content_hash": "a" * 64,
                    }
                }

        with Session(self.engine) as db:
            service = HumanReviewQueryService(db)
            related = service.get_related(anchor_id)
            detail = service.get_case(pending_id)

        self.assertTrue(detail.evidence_summary["available"])
        self.assertEqual(
            related.items[0].evidence_summary,
            detail.evidence_summary,
        )

    def test_related_evidence_uses_python_casefold_for_canonical_source_keys(self) -> None:
        anchor_id, pending_id = self._seed_large_related_evidence_set(3)
        source_keys = (
            "/private/Straße.pdf",
            "/private/STRASSE.pdf",
            "/private/ſtrasse.pdf",
        )
        with Session(self.engine) as db, db.begin():
            job = db.get(ImportJob, 900)
            self.assertIsNotNone(job)
            job.document_key = "dropbox_path:/private/strasse.pdf"
            for item in db.scalars(select(ReviewItem)):
                item.document_key = job.document_key
            traces = tuple(db.scalars(
                select(ImportedOcrTrace).order_by(ImportedOcrTrace.id)
            ))
            for trace, source_key in zip(traces, source_keys):
                trace.parsed_payload = {
                    "document": {
                        "name": "safe-list-evidence.pdf",
                        "source_key": source_key,
                        "source_path": "/private/list-evidence.pdf",
                        "rev": "revision-1",
                        "size": 2048,
                        "content_hash": "a" * 64,
                    }
                }

        with Session(self.engine) as db:
            service = HumanReviewQueryService(db)
            related = service.get_related(anchor_id)
            detail = service.get_case(pending_id)

        self.assertTrue(detail.evidence_summary["available"])
        self.assertEqual(
            related.items[0].evidence_summary,
            detail.evidence_summary,
        )

    def test_stable_prefetch_bounds_override_rows_and_caches_evaluation(self) -> None:
        item = self._seed_projection_with_irrelevant_active_overrides(80)
        override_row_counts: list[int] = []
        evaluation_statements: list[str] = []
        phase = "prefetch"

        def record_rows(
            _connection,
            cursor,
            statement: str,
            _parameters,
            _context,
            _executemany: bool,
        ) -> None:
            if "field_overrides" in statement.casefold():
                override_row_counts.append(cursor.rowcount)
            if phase == "evaluation" and statement.lstrip().casefold().startswith(
                ("select", "with")
            ):
                evaluation_statements.append(statement)

        event.listen(self.engine, "after_cursor_execute", record_rows)
        try:
            with Session(self.engine) as db:
                source = EffectiveHumanProjectionSource(db)
                with source.read_scope():
                    source.prefetch_stable_target_keys({item.stable_target_key})
                    phase = "evaluation"
                    projection = source.for_stable_target_key(
                        item.stable_target_key
                    )
        finally:
            event.remove(self.engine, "after_cursor_execute", record_rows)

        self.assertIsNone(projection)
        self.assertTrue(override_row_counts)
        self.assertLessEqual(
            max(override_row_counts),
            2,
            f"stable prefetch materialized irrelevant overrides: {override_row_counts}",
        )
        self.assertEqual(
            evaluation_statements,
            [],
            "stable-key evaluation performed a database fallback after prefetch",
        )

    def test_stable_prefetch_restores_valid_reversal_from_exact_snapshot_rows(self) -> None:
        item = self._seed_projection_with_irrelevant_active_overrides(0)
        evaluation_statements: list[str] = []
        phase = "prefetch"

        def record_statement(
            _connection,
            _cursor,
            statement: str,
            _parameters,
            _context,
            _executemany: bool,
        ) -> None:
            if phase == "evaluation" and statement.lstrip().casefold().startswith(
                ("select", "with")
            ):
                evaluation_statements.append(statement)

        event.listen(self.engine, "before_cursor_execute", record_statement)
        try:
            with Session(self.engine) as db:
                source = EffectiveHumanProjectionSource(db)
                with source.read_scope():
                    source.prefetch_stable_target_keys({item.stable_target_key})
                    phase = "evaluation"
                    projection = source.for_stable_target_key(
                        item.stable_target_key
                    )
        finally:
            event.remove(self.engine, "before_cursor_execute", record_statement)

        self.assertIsNotNone(projection)
        self.assertEqual(
            projection.canonical_identity_key,
            "identity:bounded-prefetch",
        )
        self.assertEqual(projection.overrides["scientific_status"], "validated")
        self.assertEqual(evaluation_statements, [])


if __name__ == "__main__":
    unittest.main()
