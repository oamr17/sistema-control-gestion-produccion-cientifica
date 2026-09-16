from __future__ import annotations

import re
import unicodedata

from sqlalchemy.orm import Session, load_only

from app.models.entities import (
    AcademicPeriod,
    AnnualGoal,
    Career,
    ImportedOcrTrace,
    ImportedProgressReport,
    PersonRole,
    ResearchEntity,
    ScientificProduction,
)
from app.models.enums import GoalMetric
from app.schemas.kpis import CareerKpi, DashboardKpi, NamedCount, ProductionBreakdown
from app.services.validated_read_service import VALIDATED_STATUS, ValidatedReadService


def _normalize_key(value: object) -> str:
    text = str(value or "").strip().upper()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.split())


def _career_matches(value: object, career_name: str) -> bool:
    left = _normalize_key(value).replace("LICENCIATURA EN ", "").replace("LICENCIATURA ", "")
    right = _normalize_key(career_name).replace("LICENCIATURA EN ", "").replace("LICENCIATURA ", "")
    return bool(left and right and (left == right or left in right or right in left))


def _progress_dedupe_key(row: ImportedProgressReport, trace: ImportedOcrTrace | None = None) -> str:
    if trace:
        source = trace.source_path or trace.source_filename
        if source:
            return f"SOURCE:{_normalize_key(source)}"
    return "|".join(
        [
            "SIGNATURE",
            _normalize_key(row.year_label),
            str(row.cycle),
            _normalize_key(row.career_name),
            _normalize_key(row.teacher_name),
            str(row.articles),
            str(row.books),
            str(row.book_chapters),
            str(row.presentations),
            str(row.projects),
        ]
    )


def _dedupe_progress_rows(
    rows: list[ImportedProgressReport],
    traces_by_progress_id: dict[int, ImportedOcrTrace] | None = None,
) -> list[ImportedProgressReport]:
    selected: dict[str, ImportedProgressReport] = {}
    for row in rows:
        key = _progress_dedupe_key(row, (traces_by_progress_id or {}).get(row.id))
        current = selected.get(key)
        if current is None or row.id > current.id:
            selected[key] = row
    return list(selected.values())


class KpiService:
    def __init__(self, db: Session):
        self.db = db
        self.read_service = ValidatedReadService(db)
        self._period_rows_cache: dict[tuple[str, int], list[ImportedProgressReport]] = {}
        self._trace_lookup_cache: dict[tuple[int, ...], dict[int, ImportedOcrTrace]] = {}

    def dashboard(self, year_label: str, cycle: int, career_id: int | None = None) -> DashboardKpi:
        with self.db.no_autoflush, self.read_service.human_projection.read_scope():
            return self._dashboard(year_label, cycle, career_id)

    def _dashboard(self, year_label: str, cycle: int, career_id: int | None = None) -> DashboardKpi:
        period = (
            self.db.query(AcademicPeriod)
            .filter(AcademicPeriod.year_label == year_label, AcademicPeriod.cycle == cycle)
            .first()
        )
        if not period:
            return self._empty_dashboard(year_label, cycle)

        careers_query = self.db.query(Career)
        if career_id:
            careers_query = careers_query.filter(Career.id == career_id)
        careers = careers_query.order_by(Career.name).all()

        rows = self._period_progress_rows(year_label, cycle)
        roles = self.read_service.roles_for_period(period.id, validated_only=False)
        productions = self.read_service.productions_for_period(period.id, validated_only=False)
        entities = self.read_service.entities_for_period(period.id, validated_only=False)
        valid_roles = [role for role in roles if self._role_is_validated(role)]
        valid_productions = [
            production for production in productions if self.read_service.production_is_validated(production)
        ]
        valid_entities = [
            entity
            for entity in entities
            if self.read_service.effective_validation_status(entity)
            == VALIDATED_STATUS
        ]
        previous_period = self._previous_period(year_label, cycle)
        career_kpis = [
            self._career_kpi(
                career,
                valid_roles,
                valid_productions,
                valid_entities,
                previous_period,
                year_label,
            )
            for career in careers
        ]

        scoped_roles = self._scope_roles(valid_roles, career_id)
        scoped_productions = self._scope_productions(valid_productions, career_id)
        scoped_detected_productions = self._scope_productions(productions, career_id)
        scoped_entities = self._scope_entities(valid_entities, career_id)
        teacher_ids = {role.teacher_id for role in scoped_roles if role.person_type == "teacher" and role.teacher_id}
        external_ids = {
            role.external_researcher_id
            for role in scoped_roles
            if role.person_type == "external_researcher" and role.external_researcher_id
        }
        internal_fca_ids = {
            role.teacher_id
            for role in scoped_roles
            if role.teacher_id
            and role.teacher
            and "CIENCIAS ADMINISTRATIVAS" in _normalize_key(role.teacher.career.faculty.name)
        }
        internal_other_ids = teacher_ids - internal_fca_ids
        pending_roles = [
            role
            for role in roles
            if self.read_service.effective_validation_status(role)
            not in {VALIDATED_STATUS, "discarded", "discarded_invalid"}
        ]
        product_status = self._product_status_counts(scoped_productions)
        product_reconciliation = self._product_reconciliation_counts(productions, career_id)
        project_counts = self._project_counts(scoped_entities)
        entity_reconciliation = self._entity_reconciliation_counts(entities, career_id)
        reports_requires_review = sum(
            1
            for trace in self._trace_lookup(rows).values()
            if trace.review_status in {"PENDIENTE_REVISION", "REQUIERE_REVISION_TIPO_DOCUMENTO"}
        )
        canonical_metrics = self.read_service.canonical_participant_metrics(
            period_id=period.id,
            career_id=career_id,
        )

        total_teachers = len(teacher_ids)
        return DashboardKpi(
            year_label=year_label,
            cycle=cycle,
            reports_received=len(rows),
            reports_requires_review=reports_requires_review,
            total_teachers=total_teachers,
            internal_participants_count=total_teachers,
            internal_fca_count=len(internal_fca_ids),
            internal_other_faculty_count=len(internal_other_ids),
            external_participants_count=canonical_metrics["external_detected"],
            canonical_identities_count=canonical_metrics["canonical_identities"],
            participant_appearances_count=canonical_metrics["participations"],
            participant_roles_count=canonical_metrics["roles"],
            authorships_count=canonical_metrics["authorships"],
            external_researchers_detected=canonical_metrics["external_detected"],
            external_researchers_kpi_eligible=canonical_metrics["external_kpi_eligible"],
            external_researchers_pending_review=canonical_metrics["external_pending"],
            pending_participants_count=canonical_metrics["pending"],
            discarded_participants_count=len(
                self.read_service.discarded_participant_identity_keys_for_period(
                    period.id,
                    career_id,
                )
            ),
            teachers_in_research_percent=self._percent(total_teachers, total_teachers),
            scientific_output_total=len(scoped_productions),
            scientific_output_published=product_status["published"],
            scientific_output_in_review=product_status["in_review"],
            scientific_output_detected=product_reconciliation["detected"],
            scientific_output_kpi_eligible=product_reconciliation["counted"],
            scientific_output_pending_review=product_reconciliation["pending"],
            scientific_output_discarded=product_reconciliation["discarded"],
            projects_total=project_counts["total"],
            projects_current=project_counts["current"],
            projects_approved=project_counts["approved"],
            projects_average_progress_percent=project_counts["average_progress"],
            research_entities_detected=entity_reconciliation["detected"],
            research_entities_kpi_eligible=entity_reconciliation["eligible"],
            research_entities_pending_review=entity_reconciliation["pending"],
            research_entities_excluded=entity_reconciliation["excluded"],
            internal_teachers_by_career=[
                NamedCount(id=item.career_id, name=item.career_name, count=item.total_teachers)
                for item in career_kpis
            ],
            production_by_career=[
                NamedCount(id=item.career_id, name=item.career_name, count=item.scientific_output_total)
                for item in career_kpis
            ],
            external_researchers_by_university=self._external_researchers_by_university(scoped_roles),
            careers=career_kpis,
            alerts=self._alerts(career_kpis),
        )

    @staticmethod
    def _empty_dashboard(year_label: str, cycle: int) -> DashboardKpi:
        return DashboardKpi(
            year_label=year_label,
            cycle=cycle,
            reports_received=0,
            reports_requires_review=0,
            total_teachers=0,
            teachers_in_research_percent=0,
            scientific_output_total=0,
            scientific_output_published=0,
            scientific_output_in_review=0,
            projects_total=0,
            projects_current=0,
            projects_approved=0,
            projects_average_progress_percent=0,
            internal_teachers_by_career=[],
            production_by_career=[],
            external_researchers_by_university=[],
            careers=[],
            alerts=["No existe un periodo academico para el filtro seleccionado."],
        )

    def _career_kpi(
        self,
        career: Career,
        roles: list[PersonRole],
        productions: list[ScientificProduction],
        entities: list[ResearchEntity],
        previous_period: AcademicPeriod | None,
        year_label: str,
    ) -> CareerKpi:
        career_roles = [role for role in roles if role.teacher and role.teacher.career_id == career.id]
        career_job_ids = {role.import_job_id for role in career_roles if role.import_job_id}
        career_productions = [
            production
            for production in productions
            if (production.teacher and production.teacher.career_id == career.id)
            or production.import_job_id in career_job_ids
            or (production.research_entity and _career_matches(production.research_entity.career_name, career.name))
        ]
        career_entities = [
            entity
            for entity in entities
            if _career_matches(entity.career_name, career.name) or entity.import_job_id in career_job_ids
        ]
        teacher_count = len({role.teacher_id for role in career_roles if role.teacher_id})
        counts = self._production_breakdown(career_productions)
        output_total = sum(counts.values())
        previous_total = self._previous_career_output(previous_period, career)
        planned_output = self._planned_value(career.id, year_label, GoalMetric.SCIENTIFIC_OUTPUT)
        return CareerKpi(
            career_id=career.id,
            career_name=career.name,
            total_teachers=teacher_count,
            teachers_in_research=teacher_count,
            teachers_in_research_percent=self._percent(teacher_count, teacher_count),
            projects=len(career_entities),
            scientific_output_total=output_total,
            production=ProductionBreakdown(**counts),
            planned_output=planned_output,
            output_compliance_percent=self._percent(output_total, planned_output),
            output_variation_percent=self._variation(output_total, previous_total),
        )

    def _previous_career_output(self, period: AcademicPeriod | None, career: Career) -> int:
        if not period:
            return 0
        productions = [
            item
            for item in self.read_service.productions_for_period(period.id)
            if self.read_service.production_is_validated(item)
            and item.teacher
            and item.teacher.career_id == career.id
        ]
        return len(productions)

    def _period_progress_rows(self, year_label: str, cycle: int) -> list[ImportedProgressReport]:
        cache_key = (year_label, cycle)
        if cache_key in self._period_rows_cache:
            return self._period_rows_cache[cache_key]
        rows = self.read_service.progress_rows(year_label, cycle)
        deduped = _dedupe_progress_rows(rows, self._trace_lookup(rows))
        self._period_rows_cache[cache_key] = deduped
        return deduped

    def _trace_lookup(self, rows: list[ImportedProgressReport]) -> dict[int, ImportedOcrTrace]:
        row_ids = [row.id for row in rows]
        if not row_ids:
            return {}
        cache_key = tuple(sorted(row_ids))
        if cache_key in self._trace_lookup_cache:
            return self._trace_lookup_cache[cache_key]
        lookup = {
            trace.progress_report_id: trace
            for trace in self.db.query(ImportedOcrTrace)
            .options(
                load_only(
                    ImportedOcrTrace.id,
                    ImportedOcrTrace.progress_report_id,
                    ImportedOcrTrace.source_filename,
                    ImportedOcrTrace.source_path,
                    ImportedOcrTrace.review_status,
                )
            )
            .filter(ImportedOcrTrace.progress_report_id.in_(row_ids))
            .all()
            if trace.progress_report_id
        }
        self._trace_lookup_cache[cache_key] = lookup
        return lookup

    def _role_is_validated(self, role: PersonRole) -> bool:
        return (
            self.read_service.effective_validation_status(role)
            == VALIDATED_STATUS
            and self.read_service._effective_role_identity_is_validated(role)
        )

    @staticmethod
    def _scope_roles(roles: list[PersonRole], career_id: int | None) -> list[PersonRole]:
        if not career_id:
            return roles
        return [
            role
            for role in roles
            if (role.teacher and role.teacher.career_id == career_id)
            or (role.scientific_production and role.scientific_production.teacher and role.scientific_production.teacher.career_id == career_id)
        ]

    @staticmethod
    def _scope_productions(productions: list[ScientificProduction], career_id: int | None) -> list[ScientificProduction]:
        if not career_id:
            return productions
        return [item for item in productions if item.teacher and item.teacher.career_id == career_id]

    def _scope_entities(self, entities: list[ResearchEntity], career_id: int | None) -> list[ResearchEntity]:
        if not career_id:
            return entities
        career = self.db.get(Career, career_id)
        return [entity for entity in entities if career and _career_matches(entity.career_name, career.name)]

    @staticmethod
    def _production_breakdown(productions: list[ScientificProduction]) -> dict[str, int]:
        counts = {"articles": 0, "books": 0, "book_chapters": 0, "presentations": 0, "unclassified": 0}
        mapping = {
            "ARTICLE": "articles",
            "BOOK": "books",
            "BOOK_CHAPTER": "book_chapters",
            "PRESENTATION": "presentations",
        }
        for production in productions:
            counts[mapping.get(production.production_type.value, "unclassified")] += 1
        return counts

    @staticmethod
    def _product_status_counts(productions: list[ScientificProduction]) -> dict[str, int]:
        counts = {"published": 0, "in_review": 0}
        for production in productions:
            status = _normalize_key(production.status)
            if any(token in status for token in ("PUBLIC", "PUBLISHED")):
                counts["published"] += 1
            elif any(token in status for token in ("REVISION", "REVIEW", "ENVIADO", "SUBMITTED")):
                counts["in_review"] += 1
        return counts

    def _product_reconciliation_counts(
        self,
        productions: list[ScientificProduction],
        career_id: int | None,
    ) -> dict[str, int]:
        scoped = self._scope_productions(productions, career_id)
        counted = sum(1 for item in scoped if self.read_service.production_is_validated(item))
        discarded = sum(
            1
            for item in scoped
            if ValidatedReadService._discarded_status(
                self.read_service.effective_validation_status(item)
            )
        )
        pending = len(scoped) - counted - discarded
        return {"detected": len(scoped) - discarded, "counted": counted, "pending": pending, "discarded": discarded}

    @staticmethod
    def _project_counts(entities: list[ResearchEntity]) -> dict[str, int | float]:
        progress_values = [int(item.progress_percentage) for item in entities if item.progress_percentage is not None]
        current = sum(1 for item in entities if any(token in _normalize_key(item.status) for token in ("VIGENTE", "EJECUCION", "ACTIVO", "CURRENT")))
        approved = sum(1 for item in entities if any(token in _normalize_key(item.status) for token in ("APROBADO", "APPROVED")))
        average = round(sum(progress_values) / len(progress_values), 2) if progress_values else 0
        return {"total": len(entities), "current": current, "approved": approved, "average_progress": average}

    def _entity_reconciliation_counts(self, entities: list[ResearchEntity], career_id: int | None) -> dict[str, int]:
        scoped = self._scope_entities(entities, career_id)
        eligible = sum(
            1
            for item in scoped
            if self.read_service.effective_validation_status(item)
            == VALIDATED_STATUS
        )
        excluded = sum(
            1
            for item in scoped
            if ValidatedReadService._discarded_status(
                self.read_service.effective_validation_status(item)
            )
        )
        pending = len(scoped) - eligible - excluded
        return {"detected": len(scoped) - excluded, "eligible": eligible, "pending": pending, "excluded": excluded}

    def _external_researchers_by_university(self, roles: list[PersonRole]) -> list[NamedCount]:
        counts: dict[str, set[int]] = {}
        for role in roles:
            researcher = role.external_researcher
            if not researcher:
                continue
            institution = self.read_service.effective_external_institution(researcher)
            counts.setdefault(institution, set()).add(researcher.id)
        return [NamedCount(name=name, count=len(ids)) for name, ids in sorted(counts.items())]

    def _planned_value(self, career_id: int, year_label: str, metric: GoalMetric) -> int:
        return (
            self.db.query(AnnualGoal.planned_value)
            .filter(AnnualGoal.career_id == career_id, AnnualGoal.year_label == year_label, AnnualGoal.metric == metric)
            .scalar()
            or 0
        )

    def _previous_period(self, year_label: str, cycle: int) -> AcademicPeriod | None:
        if cycle == 2:
            return self.db.query(AcademicPeriod).filter(AcademicPeriod.year_label == year_label, AcademicPeriod.cycle == 1).first()
        return (
            self.db.query(AcademicPeriod)
            .filter(AcademicPeriod.year_label < year_label, AcademicPeriod.cycle == 2)
            .order_by(AcademicPeriod.year_label.desc())
            .first()
        )

    @staticmethod
    def _alerts(career_kpis: list[CareerKpi]) -> list[str]:
        alerts: list[str] = []
        for item in career_kpis:
            if item.planned_output and item.output_compliance_percent < 70:
                alerts.append(f"{item.career_name}: la produccion cientifica esta por debajo del 70% del POA.")
            if not item.projects:
                alerts.append(f"{item.career_name}: no tiene investigaciones validadas para el filtro.")
        return alerts

    @staticmethod
    def _percent(value: int, total: int) -> float:
        return round((value / total) * 100, 2) if total > 0 else 0

    @staticmethod
    def _variation(current: int, previous: int) -> float:
        if previous <= 0:
            return 100 if current > 0 else 0
        return round(((current - previous) / previous) * 100, 2)
