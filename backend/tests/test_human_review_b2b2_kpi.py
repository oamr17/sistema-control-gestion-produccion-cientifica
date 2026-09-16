from __future__ import annotations

import unittest
from copy import deepcopy
from uuid import uuid4

from sqlalchemy import CheckConstraint, MetaData, create_engine, event, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models.entities import (
    AcademicPeriod,
    Career,
    Faculty,
    ImportJob,
    PersonRole,
    ScientificProduction,
    ScientificProductionAuthor,
    Teacher,
)
from app.models.enums import ProductionType
from app.models.human_review_core import ReviewDecision, ReviewItem
from app.models.human_review_projection import FieldOverride
from app.services.human_review_commands import _capture_effective_kpi, _diff_effective_kpi
from app.services.kpi_service import KpiService


def _create_sqlite_schema_without_postgresql_checks(engine) -> None:
    metadata = MetaData()
    for table in Base.metadata.tables.values():
        table.to_metadata(metadata)
    for table in metadata.tables.values():
        for constraint in tuple(table.constraints):
            if isinstance(constraint, CheckConstraint):
                table.constraints.remove(constraint)
    metadata.create_all(engine)


def _stable(case_type: str, marker: str) -> str:
    return f"b2b:v1:{case_type}:{marker * 64}"


def _scalar(value: str) -> dict[str, object]:
    return {
        "kind": "string",
        "string_value": value,
        "integer_value": None,
        "decimal_value": None,
        "boolean_value": None,
    }


class HumanReviewB2B2KpiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = create_engine("sqlite+pysqlite:///:memory:")
        _create_sqlite_schema_without_postgresql_checks(cls.engine)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()

    def setUp(self) -> None:
        self.db = Session(self.engine)
        faculty = Faculty(name=f"FACULTAD {uuid4().hex}")
        self.career = Career(
            name=f"CARRERA {uuid4().hex}",
            code=uuid4().hex[:12],
            faculty=faculty,
        )
        self.period = AcademicPeriod(year_label=f"20{uuid4().hex[:2]}-20{uuid4().hex[:2]}", cycle=1)
        self.job = ImportJob(
            source_type="PROGRESS_PDF",
            filename=f"{uuid4().hex}.pdf",
            status="SUCCESS",
            is_current=True,
        )
        self.teacher = Teacher(
            career=self.career,
            full_name="Docente KPI",
            institutional_email=f"{uuid4().hex}@example.test",
            validation_status="validated",
        )
        self.db.add_all([faculty, self.career, self.period, self.job, self.teacher])
        self.db.flush()

    def tearDown(self) -> None:
        self.db.rollback()
        self.db.close()

    def _project_status(
        self,
        *,
        case_type: str,
        target_table: str,
        target_pk: int,
        marker: str,
        scientific_status: str,
        decision_type: str,
        identity: dict[str, object] | None = None,
    ) -> tuple[ReviewItem, FieldOverride]:
        stable_target_key = _stable(case_type, marker)
        decision_id = uuid4()
        override = {
            "schema_version": 1,
            "field_path": "scientific_status",
            "projected_value": _scalar(scientific_status),
            "scope": "record",
            "stable_target_key": stable_target_key,
            "target_table": target_table,
            "target_pk": target_pk,
            "document_key": None,
            "period_id": None,
            "relationship_key": None,
            "locked": True,
        }
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
            "scientific_status": scientific_status,
            "current_decision_id": str(decision_id),
            "identity": identity,
            "overrides": [override],
        }
        if identity is None:
            payload = {
                "kind": "field_override",
                "schema_version": 1,
                "field_path": "scientific_status",
                "value": _scalar(scientific_status),
                "scope": "record",
                "stable_target_key": stable_target_key,
                "document_key": None,
                "period_id": None,
                "relationship_key": None,
                "projection_before": before,
                "projection_after": after,
            }
        else:
            payload = {
                "kind": "identity",
                "schema_version": 1,
                "canonical_identity_key": identity["canonical_identity_key"],
                "canonical_name": identity["canonical_name"],
                "identity_type": identity["identity_type"],
                "alias_original": None,
                "alias_normalized": None,
                "projection_before": before,
                "projection_after": after,
            }
        item = ReviewItem(
            case_type=case_type,
            stable_target_key=stable_target_key,
            target_table=target_table,
            target_pk=target_pk,
            scope_faculty_id=self.career.faculty_id,
            scope_career_id=self.career.id,
            document_key=self.job.filename,
            source_section="test",
            row_or_block_id=f"row:{target_pk}",
            field_path="scientific_status",
            raw_value_sha256=marker * 64,
            period_id=self.period.id,
            case_status="resolved",
            scientific_status=scientific_status,
            possible_kpi_impact=True,
            version=2,
        )
        self.db.add(item)
        self.db.flush()
        decision = ReviewDecision(
            id=decision_id,
            review_item_id=item.id,
            sequence=1,
            decision_type=decision_type,
            decision_lifecycle="approved",
            scope="record",
            payload_schema="review.decision.v1",
            payload_version=1,
            payload=payload,
            reason="Decisión KPI",
            actor_type="human",
            actor_user_id=1,
            actor_identifier="gestor@example.test",
            actor_capability="RESEARCH_MANAGER",
            expected_case_version=1,
            locks_projection=True,
        )
        self.db.add(decision)
        self.db.flush()
        item.current_decision_id = decision.id
        materialized = FieldOverride(
            review_item_id=item.id,
            decision_id=decision.id,
            stable_target_key=stable_target_key,
            target_table=target_table,
            target_pk=target_pk,
            field_path="scientific_status",
            value_schema="override.scalar.v1",
            value_version=1,
            projected_value=deepcopy(override["projected_value"]),
            scope="record",
            locked=True,
            is_active=True,
        )
        self.db.add(materialized)
        self.db.flush()
        return item, materialized

    def _product(self, title: str, status: str) -> ScientificProduction:
        production = ScientificProduction(
            teacher_id=self.teacher.id,
            period_id=self.period.id,
            production_type=ProductionType.ARTICLE,
            title=title,
            status="published",
            import_job_id=self.job.id,
            validation_status=status,
        )
        self.db.add(production)
        self.db.flush()
        self.db.add(
            ScientificProductionAuthor(
                production_id=production.id,
                author_order=1,
                normalized_author_name="Autora Validada",
                canonical_identity_key="human:author:kpi",
                author_type="internal",
                validation_status="validated",
            )
        )
        self.db.flush()
        return production

    def test_dashboard_counts_applied_and_discarded_projections_once_and_reverts_to_fallback(self) -> None:
        applied = self._product("Producto aplicado", "pending_review")
        discarded = self._product("Producto descartado", "validated")
        applied_item, applied_override = self._project_status(
            case_type="product",
            target_table="scientific_productions",
            target_pk=applied.id,
            marker="e",
            scientific_status="validated",
            decision_type="validated",
        )
        self._project_status(
            case_type="product",
            target_table="scientific_productions",
            target_pk=discarded.id,
            marker="f",
            scientific_status="discarded",
            decision_type="discarded",
        )
        service = KpiService(self.db)

        first = service.dashboard(self.period.year_label, self.period.cycle, self.career.id)
        second = KpiService(self.db).dashboard(
            self.period.year_label,
            self.period.cycle,
            self.career.id,
        )

        self.assertEqual(first, second)
        self.assertEqual(first.scientific_output_total, 1)
        self.assertEqual(first.scientific_output_detected, 1)
        self.assertEqual(first.scientific_output_kpi_eligible, 1)
        self.assertEqual(first.scientific_output_pending_review, 0)
        self.assertEqual(first.scientific_output_discarded, 1)
        self.assertEqual(first.careers[0].scientific_output_total, 1)

        applied_item.current_decision_id = None
        applied_item.case_status = "pending"
        applied_item.scientific_status = "pending"
        applied_override.is_active = False
        self.db.flush()

        reverted = KpiService(self.db).dashboard(
            self.period.year_label,
            self.period.cycle,
            self.career.id,
        )

        self.assertEqual(reverted.scientific_output_total, 0)
        self.assertEqual(reverted.scientific_output_kpi_eligible, 0)
        self.assertEqual(reverted.scientific_output_pending_review, 1)
        self.assertEqual(applied.validation_status, "pending_review")
        self.assertEqual(discarded.validation_status, "validated")
        self.assertEqual(list(self.db.dirty), [])

    def test_dashboard_uses_human_identity_status_for_participant_metrics(self) -> None:
        role = PersonRole(
            period_id=self.period.id,
            import_job_id=self.job.id,
            teacher_id=self.teacher.id,
            role_type="integrante_interno",
            person_type="teacher",
            canonical_identity_key="pending:kpi-parser",
            canonical_name="Nombre Parser KPI",
            normalized_name="Nombre Parser KPI",
            validation_status="pending_review",
        )
        self.db.add(role)
        self.db.flush()
        identity = {
            "schema_version": 1,
            "canonical_identity_key": "human:kpi:identity",
            "canonical_name": "Nombre Humano KPI",
            "identity_type": "internal_person",
            "aliases": [],
        }
        self._project_status(
            case_type="person_identity",
            target_table="person_roles",
            target_pk=role.id,
            marker="1",
            scientific_status="validated",
            decision_type="linked",
            identity=identity,
        )

        dashboard = KpiService(self.db).dashboard(
            self.period.year_label,
            self.period.cycle,
            self.career.id,
        )

        self.assertEqual(dashboard.total_teachers, 1)
        self.assertEqual(dashboard.canonical_identities_count, 1)
        self.assertEqual(dashboard.pending_participants_count, 0)
        self.assertEqual(role.validation_status, "pending_review")

    def test_kpi_diff_emits_only_changed_top_level_metrics(self) -> None:
        before = {
            "scientific_output_total": 0,
            "scientific_output_kpi_eligible": 0,
            "pending_participants_count": 1,
        }
        after = {
            "scientific_output_total": 1,
            "scientific_output_kpi_eligible": 1,
            "pending_participants_count": 1,
        }

        changed = _diff_effective_kpi(before, after)
        unavailable = _diff_effective_kpi(None, after)

        self.assertEqual(
            changed.model_dump(mode="json"),
            {
                "affected": [
                    {"metric": "scientific_output_kpi_eligible", "before": 0, "after": 1, "delta": 1},
                    {"metric": "scientific_output_total", "before": 0, "after": 1, "delta": 1},
                ]
            },
        )
        self.assertEqual(unavailable.model_dump(mode="json"), {"affected": []})

    def test_real_kpi_capture_omits_validated_product_without_an_author(self) -> None:
        product = ScientificProduction(
            teacher_id=self.teacher.id,
            period_id=self.period.id,
            production_type=ProductionType.ARTICLE,
            title="Producto sin autor",
            status="published",
            import_job_id=self.job.id,
            validation_status="pending_review",
        )
        self.db.add(product)
        self.db.flush()
        item = ReviewItem(
            possible_kpi_impact=True,
            period_id=self.period.id,
            scope_faculty_id=self.career.faculty_id,
            scope_career_id=self.career.id,
        )
        before = _capture_effective_kpi(self.db, item)
        self._project_status(
            case_type="product",
            target_table="scientific_productions",
            target_pk=product.id,
            marker="c",
            scientific_status="validated",
            decision_type="validated",
        )
        after = _capture_effective_kpi(self.db, item)

        self.assertIsNotNone(before)
        self.assertIsNotNone(after)
        self.assertEqual(_diff_effective_kpi(before, after).model_dump(mode="json"), {"affected": []})

    def test_dashboard_counts_a_human_validated_product_once_when_author_is_also_projected(self) -> None:
        production = self._product("Producto contado una vez", "pending_review")
        author = production.authors[0]
        self._project_status(
            case_type="product",
            target_table="scientific_productions",
            target_pk=production.id,
            marker="a",
            scientific_status="validated",
            decision_type="validated",
        )
        self._project_status(
            case_type="author_identity",
            target_table="scientific_production_authors",
            target_pk=author.id,
            marker="b",
            scientific_status="validated",
            decision_type="validated",
        )

        first = KpiService(self.db).dashboard(
            self.period.year_label,
            self.period.cycle,
            self.career.id,
        )
        second = KpiService(self.db).dashboard(
            self.period.year_label,
            self.period.cycle,
            self.career.id,
        )

        self.assertEqual(first, second)
        self.assertEqual(first.scientific_output_total, 1)
        self.assertEqual(first.scientific_output_detected, 1)
        self.assertEqual(first.scientific_output_kpi_eligible, 1)
        self.assertEqual(first.scientific_output_pending_review, 0)
        self.assertEqual(first.scientific_output_discarded, 0)

    def test_dashboard_never_autoflushes_pending_scientific_writes(self) -> None:
        production = self._product("TÃ­tulo persistido", "validated")
        production.title = "CAMBIO SUCIO QUE EL READER NO DEBE PERSISTIR"
        self.assertIn(production, self.db.dirty)

        KpiService(self.db).dashboard(
            self.period.year_label,
            self.period.cycle,
            self.career.id,
        )

        self.assertIn(production, self.db.dirty)
        with self.db.no_autoflush:
            persisted_title = self.db.scalar(
                select(ScientificProduction.title).where(
                    ScientificProduction.id == production.id
                )
            )
        self.assertEqual(persisted_title, "TÃ­tulo persistido")

    def test_dashboard_memoizes_human_projection_lookups_per_target(self) -> None:
        for index in range(12):
            self._product(
                f"Producto con lookup agrupado {index}",
                "validated",
            )
        review_item_selects = 0

        def count_review_item_selects(
            _connection,
            _cursor,
            statement,
            _parameters,
            _context,
            _executemany,
        ) -> None:
            nonlocal review_item_selects
            normalized = " ".join(statement.lower().split())
            if normalized.startswith("select") and " from review_items " in normalized:
                review_item_selects += 1

        event.listen(self.engine, "before_cursor_execute", count_review_item_selects)
        try:
            KpiService(self.db).dashboard(
                self.period.year_label,
                self.period.cycle,
                self.career.id,
            )
        finally:
            event.remove(
                self.engine,
                "before_cursor_execute",
                count_review_item_selects,
            )

        # All 12 product and 12 author heads are loaded together; target
        # cardinality must not increase review_items round trips.
        self.assertLessEqual(review_item_selects, 1)

    def test_dashboard_deduplicates_discarded_participants_by_effective_identity(self) -> None:
        roles = [
            PersonRole(
                period_id=self.period.id,
                import_job_id=self.job.id,
                teacher_id=self.teacher.id,
                role_type=role_type,
                person_type="teacher",
                canonical_identity_key="human:same-discarded-person",
                canonical_name="Misma persona descartada",
                normalized_name="Misma persona descartada",
                validation_status="discarded",
            )
            for role_type in ("director", "integrante_interno")
        ]
        self.db.add_all(roles)
        self.db.flush()

        dashboard = KpiService(self.db).dashboard(
            self.period.year_label,
            self.period.cycle,
            self.career.id,
        )

        self.assertEqual(dashboard.discarded_participants_count, 1)


if __name__ == "__main__":
    unittest.main()
