from __future__ import annotations

from functools import wraps
from typing import Any

import unicodedata

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.entities import (
    Career,
    ExternalResearcher,
    ImportNormalizationAudit,
    ImportJob,
    ImportedOcrTrace,
    ImportedProgressReport,
    PersonRole,
    ProjectTeacher,
    ResearchEntity,
    ScientificProduction,
    ScientificProductionAuthor,
    Teacher,
)
from app.models.enums import GoalMetric, ProductionType
from app.models.human_review_core import ReviewDecision
from app.services.human_review_projection import (
    EffectiveHumanProjection,
    EffectiveHumanProjectionSource,
)


VALIDATED_STATUS = "validated"
GLOBAL_IDENTITY_REDIRECT_DECISIONS = frozenset(
    {"validated", "corrected", "linked", "merged", "reverted"}
)


def _read_only_reader(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self.db.no_autoflush, self.human_projection.read_scope():
            return method(self, *args, **kwargs)

    return wrapped


class ValidatedReadService:
    def __init__(self, db: Session):
        self.db = db
        self.human_projection = EffectiveHumanProjectionSource(db)

    def _prefetch_human_projections(self, rows: list[object]) -> None:
        records: set[tuple[str, int]] = set()

        def add(table: str, row: object | None) -> None:
            target_pk = getattr(row, "id", None)
            if isinstance(target_pk, int):
                records.add((table, target_pk))

        for row in rows:
            if isinstance(row, PersonRole):
                add("person_roles", row)
                add("external_researchers", row.external_researcher)
            elif isinstance(row, ScientificProductionAuthor):
                add("scientific_production_authors", row)
                add("external_researchers", row.external_researcher)
            elif isinstance(row, ScientificProduction):
                add("scientific_productions", row)
                for author in row.authors:
                    add("scientific_production_authors", author)
                    add("external_researchers", author.external_researcher)
            elif isinstance(row, ResearchEntity):
                add("research_entities", row)
            elif isinstance(row, ExternalResearcher):
                add("external_researchers", row)
        self.human_projection.prefetch_records(records)

    def _projection_for(
        self,
        row: PersonRole
        | ScientificProductionAuthor
        | ScientificProduction
        | ResearchEntity
        | ExternalResearcher,
    ) -> EffectiveHumanProjection | None:
        table = {
            PersonRole: "person_roles",
            ScientificProductionAuthor: "scientific_production_authors",
            ScientificProduction: "scientific_productions",
            ResearchEntity: "research_entities",
            ExternalResearcher: "external_researchers",
        }.get(type(row))
        if table is None:
            return None
        return self.human_projection.for_record(table, getattr(row, "id", None))

    def _identity_projection_for(
        self,
        row: PersonRole | ScientificProductionAuthor,
    ) -> EffectiveHumanProjection | None:
        projection = self._projection_for(row)
        if projection is not None and projection.canonical_identity_key is not None:
            return projection
        if (
            isinstance(row, (PersonRole, ScientificProductionAuthor))
            and row.external_researcher is not None
        ):
            external = self._projection_for(row.external_researcher)
            if external is not None and external.canonical_identity_key is not None:
                return external
        return None

    @_read_only_reader
    def effective_validation_status(
        self,
        row: PersonRole | ScientificProductionAuthor | ScientificProduction | ResearchEntity,
    ) -> str:
        projection = self._projection_for(row)
        if (
            projection is None
            and isinstance(row, (PersonRole, ScientificProductionAuthor))
            and row.external_researcher is not None
        ):
            projection = self._projection_for(row.external_researcher)
        if projection is None or projection.scientific_status == "pending":
            return str(row.validation_status)
        return projection.scientific_status

    @_read_only_reader
    def effective_canonical_key(
        self,
        row: PersonRole | ScientificProductionAuthor,
    ) -> str:
        projection = self._identity_projection_for(row)
        if projection is not None and projection.canonical_identity_key is not None:
            return projection.canonical_identity_key
        return self._canonical_key(row)

    @_read_only_reader
    def effective_canonical_name(
        self,
        row: PersonRole | ScientificProductionAuthor,
    ) -> str:
        projection = self._identity_projection_for(row)
        if projection is not None and projection.canonical_name is not None:
            return projection.canonical_name
        return self._canonical_row_name(row)

    @_read_only_reader
    def effective_external_institution(self, researcher: ExternalResearcher) -> str:
        projection = self._projection_for(researcher)
        if projection is None:
            return researcher.institution
        value = projection.value("external_institution")
        return value if isinstance(value, str) and value.strip() else researcher.institution

    @_read_only_reader
    def effective_product_title(self, production: ScientificProduction) -> str:
        projection = self._projection_for(production)
        if projection is not None:
            value = projection.value("product_title")
            if isinstance(value, str) and value.strip():
                return value
        return production.normalized_title or production.title

    @_read_only_reader
    def effective_teacher_names(
        self,
        teachers: list[Teacher],
    ) -> dict[int, str]:
        teachers_by_id = {teacher.id: teacher for teacher in teachers}
        if not teachers_by_id:
            return {}
        roles = self.db.query(PersonRole).filter(
            PersonRole.teacher_id.in_(tuple(teachers_by_id))
        ).all()
        self._prefetch_human_projections(roles)
        candidates: dict[int, set[tuple[str, str]]] = {}
        for role in roles:
            projection = self._identity_projection_for(role)
            if (
                role.teacher_id is None
                or projection is None
                or projection.scientific_status != VALIDATED_STATUS
                or not projection.canonical_identity_key
                or not projection.canonical_name
            ):
                continue
            candidates.setdefault(role.teacher_id, set()).add((
                projection.canonical_identity_key,
                projection.canonical_name,
            ))
        return {
            teacher_id: (
                next(iter(values))[1]
                if len(values) == 1
                else teacher.full_name
            )
            for teacher_id, teacher in teachers_by_id.items()
            for values in (candidates.get(teacher_id, set()),)
        }

    @staticmethod
    def _current_job_clause(import_job_id):
        return or_(
            import_job_id.is_(None),
            and_(ImportJob.is_current.is_(True), ImportJob.status == "SUCCESS"),
        )

    @staticmethod
    def _job_is_current(job: ImportJob | None) -> bool:
        return job is None or (job.is_current and job.status == "SUCCESS")

    def _current_job_ids(self, job_ids: list[int]) -> list[int]:
        if not job_ids:
            return []
        return [
            row[0]
            for row in self.db.query(ImportJob.id)
            .filter(
                ImportJob.id.in_(job_ids),
                ImportJob.is_current.is_(True),
                ImportJob.status == "SUCCESS",
            )
            .all()
        ]

    def _current_audited_ids(self, entity_type: str) -> list[int]:
        return [
            row[0]
            for row in (
                self.db.query(ImportNormalizationAudit.normalized_record_id)
                .join(ImportJob, ImportNormalizationAudit.import_job_id == ImportJob.id)
                .filter(
                    ImportJob.is_current.is_(True),
                    ImportJob.status == "SUCCESS",
                    ImportNormalizationAudit.entity_type == entity_type,
                    ImportNormalizationAudit.normalized_record_id.is_not(None),
                )
                .distinct()
                .all()
            )
        ]

    def _current_production_evidence_jobs(
        self,
        productions: list[ScientificProduction],
    ) -> dict[int, ImportJob]:
        production_ids = [row.id for row in productions]
        if not production_ids:
            return {}
        rows = (
            self.db.query(ImportNormalizationAudit.normalized_record_id, ImportJob)
            .join(ImportJob, ImportNormalizationAudit.import_job_id == ImportJob.id)
            .filter(
                ImportNormalizationAudit.normalized_record_id.in_(production_ids),
                ImportNormalizationAudit.entity_type == "scientific_production",
                ImportNormalizationAudit.source_section == "produccion_cientifica",
                ImportJob.is_current.is_(True),
                ImportJob.status == "SUCCESS",
            )
            .order_by(ImportNormalizationAudit.normalized_record_id.asc(), ImportJob.id.asc())
            .all()
        )
        result: dict[int, ImportJob] = {}
        for production_id, job in rows:
            result.setdefault(production_id, job)
        for production in productions:
            if self._job_is_current(production.import_job):
                if production.import_job:
                    result[production.id] = production.import_job
        return result

    @_read_only_reader
    def roles_for_jobs(self, job_ids: list[int], *, validated_only: bool = True) -> list[PersonRole]:
        current_job_ids = self._current_job_ids(job_ids)
        if not current_job_ids:
            return []
        query = (
            self.db.query(PersonRole)
            .options(
                joinedload(PersonRole.teacher).joinedload(Teacher.career).joinedload(Career.faculty),
                joinedload(PersonRole.external_researcher),
            )
            .filter(PersonRole.import_job_id.in_(current_job_ids))
        )
        rows = query.order_by(PersonRole.id.asc()).all()
        self._prefetch_human_projections(rows)
        if validated_only:
            rows = [
                row
                for row in rows
                if self.effective_validation_status(row) == VALIDATED_STATUS
            ]
        return rows

    @_read_only_reader
    def roles_for_period(self, period_id: int, *, validated_only: bool = True) -> list[PersonRole]:
        query = (
            self.db.query(PersonRole)
            .outerjoin(ImportJob, PersonRole.import_job_id == ImportJob.id)
            .options(
                joinedload(PersonRole.teacher).joinedload(Teacher.career).joinedload(Career.faculty),
                joinedload(PersonRole.external_researcher),
            )
            .filter(
                PersonRole.period_id == period_id,
                self._current_job_clause(PersonRole.import_job_id),
            )
        )
        rows = query.order_by(PersonRole.id.asc()).all()
        self._prefetch_human_projections(rows)
        if validated_only:
            rows = [
                row
                for row in rows
                if self.effective_validation_status(row) == VALIDATED_STATUS
            ]
        return rows

    @_read_only_reader
    def participant_roles(self, role_type: str | None = None) -> list[PersonRole]:
        query = (
            self.db.query(PersonRole)
            .outerjoin(ImportJob, PersonRole.import_job_id == ImportJob.id)
            .options(
                joinedload(PersonRole.teacher).joinedload(Teacher.career).joinedload(Career.faculty),
                joinedload(PersonRole.external_researcher),
                joinedload(PersonRole.scientific_production).selectinload(ScientificProduction.authors),
                joinedload(PersonRole.research_project),
                joinedload(PersonRole.import_job),
            )
            .filter(self._current_job_clause(PersonRole.import_job_id))
        )
        if role_type:
            query = query.filter(PersonRole.role_type == role_type)
        rows = query.order_by(PersonRole.normalized_name.asc(), PersonRole.created_at.asc()).all()
        self._prefetch_human_projections(rows)
        return [
            row
            for row in rows
            if self.effective_validation_status(row) == VALIDATED_STATUS
            and self._effective_role_identity_is_validated(row)
            and (not row.scientific_production or self.production_is_validated(row.scientific_production))
        ]

    def _canonical_role_rows(self, period_id: int | None = None) -> list[PersonRole]:
        query = (
            self.db.query(PersonRole)
            .outerjoin(ImportJob, PersonRole.import_job_id == ImportJob.id)
            .options(
                joinedload(PersonRole.teacher).joinedload(Teacher.career).joinedload(Career.faculty),
                joinedload(PersonRole.external_researcher),
                joinedload(PersonRole.research_entity),
                joinedload(PersonRole.research_project),
                joinedload(PersonRole.import_job),
            )
            .filter(self._current_job_clause(PersonRole.import_job_id))
        )
        if period_id is not None:
            query = query.filter(PersonRole.period_id == period_id)
        rows = query.order_by(PersonRole.id.asc()).all()
        self._prefetch_human_projections(rows)
        return rows

    @staticmethod
    def _canonical_key(row: PersonRole | ScientificProductionAuthor) -> str:
        if row.canonical_identity_key:
            return row.canonical_identity_key
        if isinstance(row, PersonRole) and row.person_key:
            return f"legacy:{row.person_key}"
        return f"legacy-author:{row.id}"

    @staticmethod
    def _canonical_row_name(row: PersonRole | ScientificProductionAuthor) -> str:
        if row.canonical_name:
            return row.canonical_name
        if isinstance(row, PersonRole):
            return row.normalized_name or row.raw_name or "Persona no identificada"
        return row.normalized_author_name or row.raw_author_name or "Autor no identificado"

    @staticmethod
    def _discarded_status(value: str | None) -> bool:
        key = str(value or "").casefold()
        return key.startswith("discard") or key in {"invalid", "invalid_text_fragment", "descartado"}

    @staticmethod
    def _identity_preference(row: PersonRole | ScientificProductionAuthor) -> tuple[int, float, int]:
        return (
            1 if row.identity_locked else 0,
            float(row.identity_confidence or 0),
            -row.id,
        )

    @classmethod
    def _identity_is_kpi_eligible(cls, key: str, statuses: set[str]) -> bool:
        return not key.startswith("pending:") and VALIDATED_STATUS in statuses

    def _global_identity_redirects(
        self,
        rows: list[PersonRole | ScientificProductionAuthor],
    ) -> dict[str, EffectiveHumanProjection]:
        candidates: dict[str, dict[str, EffectiveHumanProjection]] = {}
        for row in rows:
            projection = self._identity_projection_for(row)
            if projection is None or projection.canonical_identity_key is None:
                continue
            decision = self.db.get(ReviewDecision, projection.decision_id)
            if (
                decision is None
                or decision.scope != "global_identity"
                or decision.decision_type not in GLOBAL_IDENTITY_REDIRECT_DECISIONS
            ):
                continue
            source_key = self._canonical_key(row)
            if source_key == projection.canonical_identity_key:
                continue
            candidates.setdefault(source_key, {})[
                projection.canonical_identity_key
            ] = projection
        return {
            source_key: next(iter(projections.values()))
            for source_key, projections in candidates.items()
            if len(projections) == 1
        }

    @staticmethod
    def _participant_identities_by_source(
        participants: list[dict[str, Any]],
    ) -> dict[tuple[str, int], dict[str, Any]]:
        identities: dict[tuple[str, int], dict[str, Any]] = {}
        for participant in participants:
            for variant in participant.get("variants", []):
                source_table = variant.get("source_table")
                source_id = variant.get("source_id")
                if isinstance(source_table, str) and isinstance(source_id, int):
                    identities[(source_table, source_id)] = participant
        return identities

    @_read_only_reader
    def canonical_participants(
        self,
        *,
        period_id: int | None = None,
        career_id: int | None = None,
        role_type: str | None = None,
    ) -> list[dict[str, Any]]:
        roles = self._canonical_role_rows(period_id)
        productions = self._operational_production_rows(period_id=period_id, career_id=career_id)
        effective_production_jobs = self._current_production_evidence_jobs(productions)
        authors = [author for production in productions for author in production.authors]
        career = self.db.get(Career, career_id) if career_id else None
        global_identity_redirects = self._global_identity_redirects([*roles, *authors])

        grouped: dict[str, dict[str, Any]] = {}

        def ensure_group(row: PersonRole | ScientificProductionAuthor, person_type: str) -> dict[str, Any]:
            direct_projection = self._identity_projection_for(row)
            human_projection = direct_projection or global_identity_redirects.get(
                self._canonical_key(row)
            )
            key = (
                human_projection.canonical_identity_key
                if human_projection is not None
                and human_projection.canonical_identity_key is not None
                else self._canonical_key(row)
            )
            canonical_name = (
                human_projection.canonical_name
                if human_projection is not None
                and human_projection.canonical_name is not None
                else self._canonical_row_name(row)
            )
            validation_status = self.effective_validation_status(row)
            item = grouped.get(key)
            if item is None:
                item = {
                    "canonical_identity_key": key,
                    "canonical_name": canonical_name,
                    "identity_source": (
                        "human_review"
                        if human_projection is not None
                        else row.identity_source or "legacy_fallback"
                    ),
                    "identity_confidence": row.identity_confidence,
                    "identity_reason": row.identity_reason,
                    "identity_locked": bool(human_projection is not None or row.identity_locked),
                    "person_types": set(),
                    "roles": set(),
                    "documents": {},
                    "research_entities": {},
                    "authorships": {},
                    "variants": [],
                    "evidence": [],
                    "validation_statuses": set(),
                    "pending_reasons": set(),
                    "affiliations": set(),
                    "emails": set(),
                    "_preferred_row": row,
                    "_role_row_ids": set(),
                }
                grouped[key] = item
            preferred = item["_preferred_row"]
            if (
                human_projection is not None
                or self._identity_preference(row) > self._identity_preference(preferred)
            ):
                item.update(
                    canonical_name=canonical_name,
                    identity_source=(
                        "human_review"
                        if human_projection is not None
                        else row.identity_source or "legacy_fallback"
                    ),
                    identity_confidence=row.identity_confidence,
                    identity_reason=row.identity_reason,
                    identity_locked=bool(human_projection is not None or row.identity_locked),
                    _preferred_row=row,
                )
            item["person_types"].add(person_type)
            item["validation_statuses"].add(validation_status)
            if validation_status != VALIDATED_STATUS:
                item["pending_reasons"].add(
                    row.identity_reason or row.reason or validation_status
                )
            return item

        for role in roles:
            role_status = self.effective_validation_status(role)
            if self._discarded_status(role_status):
                continue
            if career:
                affiliation = self._role_affiliation_value(role)
                if role.teacher and role.teacher.career_id != career.id:
                    continue
                if not role.teacher and not self._career_matches(affiliation, career.name):
                    continue
            item = ensure_group(role, self._participant_person_type(role))
            item["roles"].add(role.role_type)
            item["_role_row_ids"].add(role.id)
            affiliation = self._role_affiliation_value(role)
            if affiliation:
                item["affiliations"].add(affiliation)
            if role.teacher and role.teacher.institutional_email:
                item["emails"].add(role.teacher.institutional_email)
            document_key = role.import_job_id or role.source_file or f"role:{role.id}"
            item["documents"][document_key] = {
                "import_job_id": role.import_job_id,
                "filename": role.import_job.filename if role.import_job else role.source_file,
            }
            if role.research_entity:
                item["research_entities"][role.research_entity.id] = {
                    "id": role.research_entity.id,
                    "name": role.research_entity.name,
                    "type": role.research_entity.type,
                    "status": role.research_entity.status,
                    "source_section": role.research_entity.source_section,
                    "validation_status": self.effective_validation_status(
                        role.research_entity
                    ),
                }
            variant = {
                "source_table": "person_roles",
                "source_id": role.id,
                "role_type": role.role_type,
                "person_type": role.person_type,
                "raw_name": role.raw_name,
                "normalized_name": role.normalized_name,
                "person_key": role.person_key,
                "import_job_id": role.import_job_id,
                "batch_id": role.import_batch_id,
                "document": role.import_job.filename if role.import_job else role.source_file,
                "source_page": role.source_page,
                "source_section": role.source_section,
                "raw_value": role.raw_value,
                "normalized_value": role.normalized_value,
                "validation_status": role_status,
                "confidence": role.confidence_score,
                "reason": role.reason,
                "project_id": role.research_project_id,
                "details": role.metadata_json or {},
            }
            item["variants"].append(variant)
            item["evidence"].append(variant.copy())

        for author in authors:
            author_status = self.effective_validation_status(author)
            if self._discarded_status(author_status):
                continue
            production = author.production
            effective_job = effective_production_jobs.get(production.id)
            item = ensure_group(author, author.person_type)
            item["roles"].add("autor_producto")
            if author.teacher:
                item["affiliations"].add(author.teacher.career.name)
            elif author.external_researcher and author.external_researcher.institution:
                item["affiliations"].add(
                    self.effective_external_institution(
                        author.external_researcher
                    )
                )
            document_key = effective_job.id if effective_job else production.source_file or f"production:{production.id}"
            item["documents"][document_key] = {
                "import_job_id": effective_job.id if effective_job else None,
                "filename": effective_job.filename if effective_job else production.source_file,
            }
            item["authorships"][production.id] = {
                "production_id": production.id,
                "title": self.effective_product_title(production),
                "raw_title": production.raw_title or production.raw_value or production.title,
                "validation_status": self.effective_validation_status(production),
                "status": production.status,
                "kpi_eligible": self.production_is_validated(production),
                "source_file": production.source_file,
                "source_page": production.source_page,
                "source_section": production.source_section,
            }
            if production.research_entity:
                entity = production.research_entity
                item["research_entities"][entity.id] = {
                    "id": entity.id,
                    "name": entity.name,
                    "type": entity.type,
                    "status": entity.status,
                    "source_section": entity.source_section,
                    "validation_status": entity.validation_status,
                }
            variant = {
                "source_table": "scientific_production_authors",
                "source_id": author.id,
                "role_type": "autor_producto",
                "person_type": author.person_type,
                "raw_name": author.raw_author_name,
                "normalized_name": author.normalized_author_name,
                "person_key": None,
                "import_job_id": author.import_job_id,
                "batch_id": author.import_batch_id,
                "document": author.source_file or production.source_file,
                "source_page": author.source_page or production.source_page,
                "source_section": author.source_section or production.source_section,
                "raw_value": None,
                "normalized_value": None,
                "validation_status": author_status,
                "confidence": author.confidence_score,
                "reason": author.reason,
                "production_id": production.id,
                "project_id": None,
                "details": author.metadata_json or {},
            }
            item["variants"].append(variant)
            item["evidence"].append(variant.copy())

        result: list[dict[str, Any]] = []
        for key, item in grouped.items():
            if role_type and role_type not in item["roles"]:
                continue
            statuses = item["validation_statuses"]
            kpi_eligible = self._identity_is_kpi_eligible(key, statuses)
            person_types = sorted(item["person_types"])
            item.update(
                person_type=self._preferred_person_type(person_types),
                roles=sorted(item["roles"]),
                documents=list(item["documents"].values()),
                research_entities=list(item["research_entities"].values()),
                authorships=list(item["authorships"].values()),
                validation_statuses=sorted(statuses),
                pending_reasons=sorted(item["pending_reasons"]),
                affiliations=sorted(item["affiliations"]),
                emails=sorted(item["emails"]),
                overall_status="validated" if kpi_eligible else "pending_review",
                kpi_eligible=kpi_eligible,
                participation_count=len(item["documents"]),
                role_count=len(item["_role_row_ids"]),
                role_type_count=len(item["roles"]),
                authorship_count=len(item["authorships"]),
                evidence_count=len(item["variants"]),
            )
            item.pop("_preferred_row", None)
            item.pop("_role_row_ids", None)
            item["possible_match_notice"] = None
            item["possible_matches"] = []
            result.append(item)

        exact_pending_names: dict[str, list[dict[str, Any]]] = {}
        for item in result:
            if not item["canonical_identity_key"].startswith("pending:"):
                continue
            name_key = self._identity_name_key(item["canonical_name"])
            if name_key:
                exact_pending_names.setdefault(name_key, []).append(item)
        for matches in exact_pending_names.values():
            distinct_keys = {item["canonical_identity_key"] for item in matches}
            if len(distinct_keys) < 2:
                continue
            for item in matches:
                item["possible_match_notice"] = "Posible coincidencia con otro registro"
                item["possible_matches"] = [
                    {
                        "canonical_identity_key": other["canonical_identity_key"],
                        "canonical_name": other["canonical_name"],
                        "variants": sorted(
                            {
                                str(variant.get("raw_name") or variant.get("normalized_name") or "").strip()
                                for variant in other["variants"]
                                if variant.get("raw_name") or variant.get("normalized_name")
                            }
                        ),
                        "document": next(
                            (
                                document.get("filename")
                                for document in other["documents"]
                                if document.get("filename")
                            ),
                            None,
                        ),
                        "documents": other["documents"],
                        "roles": other["roles"],
                        "evidence_type": (
                            "Autoria"
                            if other["roles"] == ["autor_producto"]
                            else "Rol y autoria"
                            if "autor_producto" in other["roles"]
                            else "Rol"
                        ),
                        "reason_not_merged": (
                            "El nombre completo coincide, pero las claves pending pertenecen a evidencia documental "
                            "separada y no existe corroboracion suficiente para fusionarlas automaticamente."
                        ),
                        "pending_reasons": other["pending_reasons"],
                    }
                    for other in matches
                    if other["canonical_identity_key"] != item["canonical_identity_key"]
                ]
        return sorted(result, key=lambda item: (str(item["canonical_name"]), item["canonical_identity_key"]))

    @staticmethod
    def _preferred_person_type(values: list[str]) -> str:
        priority = (
            "docente_interno",
            "investigador_externo",
            "estudiante",
            "graduado",
            "pendiente_clasificacion",
        )
        return next((value for value in priority if value in values), values[0] if values else "pendiente_clasificacion")

    @staticmethod
    def _identity_name_key(value: str | None) -> str:
        text = unicodedata.normalize("NFKD", str(value or "").upper())
        text = "".join(char for char in text if not unicodedata.combining(char))
        return " ".join("".join(char if char.isalnum() else " " for char in text).split())

    def _role_affiliation_value(self, role: PersonRole) -> str | None:
        details = role.metadata_json or {}
        if role.teacher:
            return role.teacher.career.name
        if role.external_researcher:
            return self.effective_external_institution(role.external_researcher)
        return (
            details.get("normalized_career")
            or details.get("raw_career")
            or details.get("normalized_institution")
            or details.get("raw_institution")
        )

    @_read_only_reader
    def productions_for_jobs(self, job_ids: list[int], *, validated_only: bool = True) -> list[ScientificProduction]:
        current_job_ids = self._current_job_ids(job_ids)
        if not current_job_ids:
            return []
        audited_ids = [
            row[0]
            for row in (
                self.db.query(ImportNormalizationAudit.normalized_record_id)
                .filter(
                    ImportNormalizationAudit.import_job_id.in_(current_job_ids),
                    ImportNormalizationAudit.entity_type == "scientific_production",
                    ImportNormalizationAudit.source_section == "produccion_cientifica",
                    ImportNormalizationAudit.normalized_record_id.is_not(None),
                )
                .distinct()
                .all()
            )
        ]
        query = (
            self.db.query(ScientificProduction)
            .outerjoin(ImportJob, ScientificProduction.import_job_id == ImportJob.id)
            .options(
                selectinload(ScientificProduction.authors),
                joinedload(ScientificProduction.teacher).joinedload(Teacher.career).joinedload(Career.faculty),
                joinedload(ScientificProduction.research_entity),
            )
            .filter(
                or_(
                    ScientificProduction.import_job_id.in_(job_ids),
                    ScientificProduction.id.in_(audited_ids) if audited_ids else False,
                ),
                or_(
                    self._current_job_clause(ScientificProduction.import_job_id),
                    ScientificProduction.id.in_(audited_ids) if audited_ids else False,
                ),
            )
        )
        rows = query.order_by(ScientificProduction.id.asc()).all()
        self._prefetch_human_projections(rows)
        if validated_only:
            rows = [row for row in rows if self.production_is_validated(row)]
        return rows

    @_read_only_reader
    def productions_for_period(self, period_id: int, *, validated_only: bool = True) -> list[ScientificProduction]:
        audited_ids = self._current_audited_ids("scientific_production")
        query = (
            self.db.query(ScientificProduction)
            .outerjoin(ImportJob, ScientificProduction.import_job_id == ImportJob.id)
            .options(
                selectinload(ScientificProduction.authors),
                joinedload(ScientificProduction.teacher).joinedload(Teacher.career).joinedload(Career.faculty),
                joinedload(ScientificProduction.research_entity),
            )
            .filter(
                ScientificProduction.period_id == period_id,
                or_(
                    self._current_job_clause(ScientificProduction.import_job_id),
                    ScientificProduction.id.in_(audited_ids) if audited_ids else False,
                ),
            )
        )
        rows = query.order_by(ScientificProduction.id.asc()).all()
        self._prefetch_human_projections(rows)
        if validated_only:
            rows = [row for row in rows if self.production_is_validated(row)]
        return rows

    def _operational_production_rows(
        self,
        period_id: int | None = None,
        career_id: int | None = None,
    ) -> list[ScientificProduction]:
        audited_ids = self._current_audited_ids("scientific_production")
        query = (
            self.db.query(ScientificProduction)
            .outerjoin(ImportJob, ScientificProduction.import_job_id == ImportJob.id)
            .outerjoin(Teacher, ScientificProduction.teacher_id == Teacher.id)
            .outerjoin(ResearchEntity, ScientificProduction.research_entity_id == ResearchEntity.id)
            .options(
                selectinload(ScientificProduction.authors).joinedload(ScientificProductionAuthor.teacher),
                selectinload(ScientificProduction.authors).joinedload(ScientificProductionAuthor.external_researcher),
                selectinload(ScientificProduction.authors).joinedload(ScientificProductionAuthor.import_job),
                joinedload(ScientificProduction.teacher).joinedload(Teacher.career).joinedload(Career.faculty),
                joinedload(ScientificProduction.research_entity),
                joinedload(ScientificProduction.period),
                joinedload(ScientificProduction.import_job),
            )
            .filter(
                or_(
                    self._current_job_clause(ScientificProduction.import_job_id),
                    ScientificProduction.id.in_(audited_ids) if audited_ids else False,
                ),
            )
        )
        if period_id:
            query = query.filter(ScientificProduction.period_id == period_id)
        if career_id:
            career = self.db.get(Career, career_id)
            career_name = career.name if career else None
            short_name = self._career_key(career_name) if career_name else None
            query = query.filter(
                or_(
                    Teacher.career_id == career_id,
                    ResearchEntity.career_name.ilike(f"%{career_name}%") if career_name else False,
                    ResearchEntity.career_name.ilike(f"%{short_name.title()}%") if short_name else False,
                )
            )
        rows = query.order_by(
            ScientificProduction.created_at.desc(),
            ScientificProduction.id.desc(),
        ).all()
        self._prefetch_human_projections(rows)
        return rows

    @_read_only_reader
    def production_list(
        self,
        period_id: int | None = None,
        career_id: int | None = None,
    ) -> list[ScientificProduction]:
        return [
            row
            for row in self._operational_production_rows(period_id, career_id)
            if self.production_is_validated(row)
        ]

    @_read_only_reader
    def production_visibility(self, production: ScientificProduction) -> str:
        if self._discarded_status(self.effective_validation_status(production)):
            return "discarded"
        if self.production_is_validated(production):
            return "eligible"
        return "pending"

    @_read_only_reader
    def production_views(
        self,
        period_id: int | None = None,
        career_id: int | None = None,
        *,
        visibility: str = "eligible",
    ) -> list[dict[str, Any]]:
        if visibility not in {"all", "eligible", "pending", "discarded"}:
            raise ValueError(f"Unsupported production visibility: {visibility}")
        productions = self._operational_production_rows(period_id, career_id)
        participants = self.canonical_participants(period_id=period_id, career_id=career_id)
        identities_by_source = self._participant_identities_by_source(participants)
        variants_by_key = {
            item["canonical_identity_key"]: sorted(
                {
                    str(variant.get("raw_name") or variant.get("normalized_name") or "").strip()
                    for variant in item["variants"]
                    if variant.get("raw_name") or variant.get("normalized_name")
                }
            )
            for item in participants
        }
        effective_teacher_names = self.effective_teacher_names([
            production.teacher
            for production in productions
            if production.teacher is not None
        ])
        result: list[dict[str, Any]] = []
        for production in productions:
            bucket = self.production_visibility(production)
            if visibility != "all" and visibility != bucket:
                continue
            authors = []
            for author in sorted(production.authors, key=lambda row: (row.author_order, row.id)):
                participant_identity = identities_by_source.get(
                    ("scientific_production_authors", author.id)
                )
                key = (
                    participant_identity["canonical_identity_key"]
                    if participant_identity is not None
                    else self.effective_canonical_key(author)
                )
                author_name = (
                    participant_identity["canonical_name"]
                    if participant_identity is not None
                    else self.effective_canonical_name(author)
                )
                authors.append(
                    {
                        "id": author.id,
                        "production_id": production.id,
                        "canonical_identity_key": key,
                        "canonical_name": author_name,
                        "variants": variants_by_key.get(
                            key,
                            [author.raw_author_name or author.normalized_author_name or author_name],
                        ),
                        "raw_author_name": author.raw_author_name,
                        "normalized_author_name": author.normalized_author_name,
                        "person_type": author.person_type,
                        "production_role": author.production_role,
                        "validation_status": self.effective_validation_status(author),
                        "identity_source": (
                            participant_identity["identity_source"]
                            if participant_identity is not None
                            else author.identity_source
                        ),
                        "identity_confidence": author.identity_confidence,
                        "identity_reason": author.identity_reason,
                        "confidence": author.confidence_score,
                        "reason": author.reason,
                    }
                )
            evidence_available = bool(
                production.evidence_url or production.source_file or production.link or production.raw_value
            )
            result.append(
                {
                    "id": production.id,
                    "teacher_id": production.teacher_id,
                    "period_id": production.period_id,
                    "research_entity_id": production.research_entity_id,
                    "production_type": production.production_type,
                    "title": self.effective_product_title(production),
                    "canonical_title": self.effective_product_title(production),
                    "journal": production.journal,
                    "quartile": production.quartile,
                    "link": production.link,
                    "evidence_url": production.evidence_url,
                    "status": production.status,
                    "validation_status": self.effective_validation_status(production),
                    "review_reason": production.review_reason,
                    "raw_title": production.raw_title or production.raw_value or production.title,
                    "normalized_title": production.normalized_title,
                    "raw_authors": production.raw_authors,
                    "normalized_authors": production.normalized_authors,
                    "source_file": production.source_file,
                    "document": production.source_file,
                    "source_section": production.source_section,
                    "source_page": production.source_page,
                    "normalization_reason": production.normalization_reason,
                    "confidence": production.confidence_score,
                    "reason": production.review_reason or production.normalization_reason,
                    "evidence_status": "available" if evidence_available else "missing",
                    "kpi_eligible": bucket == "eligible",
                    "visibility": bucket,
                    "teacher_name": effective_teacher_names.get(
                        production.teacher_id,
                        production.teacher_name,
                    ),
                    "career_name": production.career_name,
                    "faculty_name": production.faculty_name,
                    "year_label": production.year_label,
                    "cycle": production.cycle,
                    "authors": authors,
                }
            )
        return result

    @_read_only_reader
    def entities_for_jobs(self, job_ids: list[int], *, validated_only: bool = True) -> list[ResearchEntity]:
        current_job_ids = self._current_job_ids(job_ids)
        if not current_job_ids:
            return []
        audited_ids = [
            row[0]
            for row in self.db.query(ImportNormalizationAudit.normalized_record_id)
            .filter(
                ImportNormalizationAudit.import_job_id.in_(current_job_ids),
                ImportNormalizationAudit.entity_type == "research_entity",
                ImportNormalizationAudit.normalized_record_id.is_not(None),
            )
            .distinct()
            .all()
        ]
        query = self.db.query(ResearchEntity).filter(
            or_(
                ResearchEntity.import_job_id.in_(current_job_ids),
                ResearchEntity.id.in_(audited_ids) if audited_ids else False,
            )
        )
        rows = query.order_by(ResearchEntity.id.asc()).all()
        self._prefetch_human_projections(rows)
        if validated_only:
            rows = [
                row
                for row in rows
                if self.effective_validation_status(row) == VALIDATED_STATUS
            ]
        return rows

    @_read_only_reader
    def entities_for_period(self, period_id: int, *, validated_only: bool = True) -> list[ResearchEntity]:
        audited_ids = self._current_audited_ids("research_entity")
        query = (
            self.db.query(ResearchEntity)
            .outerjoin(ImportJob, ResearchEntity.import_job_id == ImportJob.id)
            .filter(
                ResearchEntity.period_id == period_id,
                or_(
                    self._current_job_clause(ResearchEntity.import_job_id),
                    ResearchEntity.id.in_(audited_ids) if audited_ids else False,
                ),
            )
        )
        rows = query.order_by(ResearchEntity.id.asc()).all()
        self._prefetch_human_projections(rows)
        if validated_only:
            rows = [
                row
                for row in rows
                if self.effective_validation_status(row) == VALIDATED_STATUS
            ]
        return rows

    @_read_only_reader
    def entity_list(
        self,
        period_id: int | None = None,
        career_id: int | None = None,
        status: str | None = None,
    ) -> list[ResearchEntity]:
        audited_ids = self._current_audited_ids("research_entity")
        query = (
            self.db.query(ResearchEntity)
            .outerjoin(ImportJob, ResearchEntity.import_job_id == ImportJob.id)
            .options(joinedload(ResearchEntity.period))
            .filter(
                or_(
                    self._current_job_clause(ResearchEntity.import_job_id),
                    ResearchEntity.id.in_(audited_ids) if audited_ids else False,
                ),
            )
        )
        if period_id:
            query = query.filter(ResearchEntity.period_id == period_id)
        if status:
            query = query.filter(ResearchEntity.status == status)
        rows = [
            row
            for row in query.order_by(
                ResearchEntity.created_at.desc(), ResearchEntity.id.desc()
            ).all()
            if self.effective_validation_status(row) == VALIDATED_STATUS
        ]
        if not career_id:
            return rows
        career = self.db.get(Career, career_id)
        return [row for row in rows if career and self._career_matches(row.career_name, career.name)]

    @_read_only_reader
    def project_director_state(
        self,
        entity: ResearchEntity,
        participants_by_key: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        director_roles = [
            role
            for role in self._canonical_role_rows(entity.period_id)
            if role.research_entity_id == entity.id
            and role.role_type in {
                "director",
                "director_proyecto",
                "coordinador",
                "tutor_semillero",
                "responsable_informe",
            }
        ]
        preferred = max(director_roles, key=self._identity_preference, default=None)
        projection = self._projection_for(entity)
        project_status = self.effective_validation_status(entity)
        projected_director_key = (
            projection.value("project_director_identity_key")
            if projection is not None
            else None
        )
        projected_relationship_status = (
            projection.value("project_director_relationship_status")
            if projection is not None
            else None
        )
        project_validated = project_status == VALIDATED_STATUS
        if participants_by_key is None:
            participants_by_key = {
                item["canonical_identity_key"]: item
                for item in self.canonical_participants(period_id=entity.period_id)
            }
        identities_by_source = self._participant_identities_by_source(
            list(participants_by_key.values())
        )
        preferred_identity = (
            identities_by_source.get(("person_roles", preferred.id))
            if preferred is not None
            else None
        )
        projected_relationship_resolved = (
            isinstance(projected_relationship_status, str)
            and projected_relationship_status in {
                "linked",
                "maintained_separate",
                "separated",
                "validated",
            }
        )
        if projected_relationship_resolved:
            relationship_status = "validated"
            director_key = (
                projected_director_key
                if isinstance(projected_director_key, str)
                and projected_director_key.strip()
                else (
                    preferred_identity["canonical_identity_key"]
                    if preferred_identity is not None
                    else self.effective_canonical_key(preferred)
                    if preferred is not None
                    else None
                )
            )
            identity = (
                participants_by_key.get(director_key)
                if director_key is not None
                else None
            )
            identity_status = (
                "validated"
                if isinstance(projected_director_key, str)
                and projected_director_key.strip()
                else (
                    identity["overall_status"]
                    if identity is not None
                    else "not_resolved"
                )
            )
            director_name = (
                identity["canonical_name"]
                if identity
                else (
                    self.effective_canonical_name(preferred)
                    if preferred is not None
                    else entity.normalized_director_name or entity.director_name
                )
            )
            identity_reason = identity.get("identity_reason") if identity else None
            relationship_reason = None
        elif preferred is None:
            relationship_status = "not_detected"
            identity_status = "not_resolved"
            director_key = None
            director_name = entity.normalized_director_name or entity.director_name
            identity_reason = "No existe una identidad canonica relacionada con el director detectado."
            relationship_reason = "No existe una relacion persona-entidad para el director detectado."
        else:
            director_key = (
                preferred_identity["canonical_identity_key"]
                if preferred_identity is not None
                else self.effective_canonical_key(preferred)
            )
            director_name = (
                preferred_identity["canonical_name"]
                if preferred_identity is not None
                else self.effective_canonical_name(preferred)
            )
            related_roles = [
                role
                for role in director_roles
                if (
                    identities_by_source.get(("person_roles", role.id), {}).get(
                        "canonical_identity_key"
                    )
                    or self.effective_canonical_key(role)
                )
                == director_key
            ]
            relationship_status = (
                "validated"
                if any(
                    self.effective_validation_status(role) == VALIDATED_STATUS
                    for role in related_roles
                )
                else "pending_review"
            )
            identity = participants_by_key.get(director_key)
            identity_status = identity["overall_status"] if identity else "pending_review"
            identity_reason = (
                identity.get("identity_reason")
                if identity
                else preferred.identity_reason
            )
            relationship_reason = (
                None
                if relationship_status == "validated"
                else preferred.reason or preferred.identity_reason or preferred.validation_status
            )

        project_label = "Proyecto validado" if project_validated else "Proyecto pendiente"
        identity_label = {
            "validated": "identidad validada",
            "pending_review": "identidad pendiente",
            "not_resolved": "identidad no resuelta",
        }.get(identity_status, "identidad pendiente")
        relationship_label = {
            "validated": "relación como director validada",
            "pending_review": "relación como director pendiente",
            "not_detected": "relación como director no detectada",
        }.get(relationship_status, "relación como director pendiente")
        director_display_status = f"{identity_label.capitalize()} / {relationship_label}"
        display_status = f"{project_label} / {identity_label} / {relationship_label}"
        return {
            "project_validation_status": project_status,
            "director_validation_status": relationship_status,
            "director_identity_validation_status": identity_status,
            "director_relationship_validation_status": relationship_status,
            "director_canonical_identity_key": director_key,
            "director_canonical_name": director_name,
            "director_identity_reason": identity_reason,
            "director_relationship_reason": relationship_reason,
            "director_pending_reason": relationship_reason,
            "director_display_status": director_display_status,
            "display_status": display_status,
        }

    @_read_only_reader
    def entity_views(
        self,
        period_id: int | None = None,
        career_id: int | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        result = []
        entities = self.entity_list(period_id, career_id, status)
        participants_by_key = {
            item["canonical_identity_key"]: item
            for item in self.canonical_participants(period_id=period_id)
        }
        for entity in entities:
            director_state = self.project_director_state(
                entity, participants_by_key
            )
            effective_director_name = (
                director_state.get("director_canonical_name")
                or entity.normalized_director_name
                or entity.director_name
            )
            row = {
                "id": entity.id,
                "period_id": entity.period_id,
                "type": entity.type,
                "code": entity.code,
                "normalized_code": entity.normalized_code,
                "name": entity.name,
                "normalized_name": entity.normalized_name,
                "director_name": effective_director_name,
                "normalized_director_name": effective_director_name,
                "year": entity.year,
                "cycle": entity.cycle if entity.cycle is not None else (entity.period.cycle if entity.period else None),
                "academic_unit": entity.academic_unit,
                "career_name": entity.career_name,
                "progress_percentage": entity.progress_percentage,
                "status": entity.status,
                "validation_status": self.effective_validation_status(entity),
                "source_file": entity.source_file,
                "source_page": entity.source_page,
                "source_section": entity.source_section,
                "confidence_score": entity.confidence_score,
                "reason": entity.reason,
                "year_label": entity.period.year_label if entity.period else None,
            }
            row.update(director_state)
            result.append(row)
        return result

    @_read_only_reader
    def canonical_participant_metrics(
        self,
        *,
        period_id: int | None = None,
        career_id: int | None = None,
    ) -> dict[str, int]:
        participants = self.canonical_participants(period_id=period_id, career_id=career_id)
        externals = [item for item in participants if item["person_type"] == "investigador_externo"]
        return {
            "canonical_identities": len(participants),
            "participations": sum(item["participation_count"] for item in participants),
            "roles": sum(item["role_count"] for item in participants),
            "authorships": sum(item["authorship_count"] for item in participants),
            "pending": sum(1 for item in participants if item["overall_status"] == "pending_review"),
            "external_detected": len(externals),
            "external_kpi_eligible": sum(1 for item in externals if item["kpi_eligible"]),
            "external_pending": sum(1 for item in externals if not item["kpi_eligible"]),
        }

    @_read_only_reader
    def teacher_views(self, career_id: int | None = None) -> list[dict[str, Any]]:
        current_audited_production_ids = set(self._current_audited_ids("scientific_production"))
        current_teacher_ids = [
            row[0]
            for row in self.db.query(PersonRole.teacher_id)
            .join(ImportJob, PersonRole.import_job_id == ImportJob.id)
            .filter(
                ImportJob.is_current.is_(True),
                ImportJob.status == "SUCCESS",
                PersonRole.teacher_id.is_not(None),
            )
            .distinct()
            .all()
        ]
        query = (
            self.db.query(Teacher)
            .outerjoin(ImportJob, Teacher.import_job_id == ImportJob.id)
            .options(
                selectinload(Teacher.career).selectinload(Career.faculty),
                selectinload(Teacher.projects).selectinload(ProjectTeacher.project),
                selectinload(Teacher.productions).selectinload(ScientificProduction.period),
                selectinload(Teacher.productions).selectinload(ScientificProduction.authors),
            )
            .filter(
                Teacher.validation_status == VALIDATED_STATUS,
                or_(
                    self._current_job_clause(Teacher.import_job_id),
                    Teacher.id.in_(current_teacher_ids) if current_teacher_ids else False,
                ),
            )
        )
        if career_id:
            query = query.filter(Teacher.career_id == career_id)
        teachers = query.order_by(Teacher.full_name).all()
        projected_names_by_teacher: dict[int, set[str]] = {}
        teacher_ids = {teacher.id for teacher in teachers}
        for role in self._canonical_role_rows():
            if role.teacher_id not in teacher_ids:
                continue
            projection = self._identity_projection_for(role)
            if projection is not None and projection.canonical_name:
                projected_names_by_teacher.setdefault(
                    role.teacher_id, set()
                ).add(projection.canonical_name)
        return [
            {
                "id": teacher.id,
                "career_id": teacher.career_id,
                "full_name": (
                    next(iter(projected_names_by_teacher[teacher.id]))
                    if len(projected_names_by_teacher.get(teacher.id, ())) == 1
                    else teacher.full_name
                ),
                "institutional_email": teacher.institutional_email,
                "research_hours": teacher.research_hours,
                "is_active": teacher.is_active,
                "career_name": teacher.career.name,
                "faculty_name": teacher.career.faculty.name,
                "projects": [
                    {
                        "id": association.id,
                        "role": association.role.value,
                        "project_name": association.project.name,
                        "project_type": association.project.project_type.value,
                    }
                    for association in teacher.projects
                ],
                "productions": [
                    {
                        "id": production.id,
                        "production_type": production.production_type.value,
                        "title": self.effective_product_title(production),
                        "year_label": production.period.year_label,
                        "cycle": production.period.cycle,
                    }
                    for production in teacher.productions
                    if self.production_is_validated(production)
                    and (
                        self._job_is_current(production.import_job)
                        or production.id in current_audited_production_ids
                    )
                ],
            }
            for teacher in teachers
        ]

    @_read_only_reader
    def goal_totals(self, period_id: int, career_ids: list[int] | None) -> dict[GoalMetric, int]:
        selected_ids = set(career_ids) if career_ids is not None else None
        careers = (
            self.db.query(Career).filter(Career.id.in_(selected_ids)).all()
            if selected_ids is not None
            else []
        )
        career_names = [career.name for career in careers]
        roles = [
            role
            for role in self.roles_for_period(period_id)
            if self._effective_role_identity_is_validated(role)
            and role.teacher
            and (selected_ids is None or role.teacher.career_id in selected_ids)
        ]
        productions = [
            production
            for production in self.productions_for_period(period_id)
            if self.production_is_validated(production)
            and (
                selected_ids is None
                or self._production_matches_careers(production, selected_ids, career_names)
            )
        ]
        entities = [
            entity
            for entity in self.entities_for_period(period_id)
            if selected_ids is None
            or any(self._career_matches(entity.career_name, name) for name in career_names)
        ]
        totals = {metric: 0 for metric in GoalMetric}
        totals[GoalMetric.TEACHERS_IN_RESEARCH] = len({role.teacher_id for role in roles if role.teacher_id})
        type_metrics = {
            ProductionType.ARTICLE: GoalMetric.ARTICLES,
            ProductionType.BOOK: GoalMetric.BOOKS,
            ProductionType.BOOK_CHAPTER: GoalMetric.BOOK_CHAPTERS,
            ProductionType.PRESENTATION: GoalMetric.PRESENTATIONS,
        }
        for production in productions:
            totals[type_metrics[production.production_type]] += 1
        totals[GoalMetric.SCIENTIFIC_OUTPUT] = len(productions)
        totals[GoalMetric.PROJECTS] = sum(1 for entity in entities if entity.type == "proyecto_fci")
        return totals

    @_read_only_reader
    def progress_rows(self, year_label: str | None = None, cycle: int | None = None) -> list[ImportedProgressReport]:
        query = (
            self.db.query(ImportedProgressReport)
            .join(ImportJob, ImportedProgressReport.import_job_id == ImportJob.id)
            .filter(ImportJob.is_current.is_(True), ImportJob.status == "SUCCESS")
        )
        if year_label:
            query = query.filter(ImportedProgressReport.year_label == year_label)
        if cycle:
            query = query.filter(ImportedProgressReport.cycle == cycle)
        return query.order_by(ImportedProgressReport.id.desc()).all()

    @_read_only_reader
    def discarded_participant_identity_keys_for_period(
        self,
        period_id: int,
        career_id: int | None = None,
    ) -> set[str]:
        roles = self._canonical_role_rows(period_id)
        productions = self._operational_production_rows(
            period_id=period_id,
            career_id=career_id,
        )
        career = self.db.get(Career, career_id) if career_id else None
        keys: set[str] = set()
        for role in roles:
            if career is not None:
                affiliation = self._role_affiliation_value(role)
                if role.teacher and role.teacher.career_id != career.id:
                    continue
                if (
                    not role.teacher
                    and not self._career_matches(affiliation, career.name)
                ):
                    continue
            if self._discarded_status(
                self.effective_validation_status(role)
            ):
                keys.add(self.effective_canonical_key(role))
        for production in productions:
            for author in production.authors:
                if self._discarded_status(
                    self.effective_validation_status(author)
                ):
                    keys.add(self.effective_canonical_key(author))
        return keys

    @_read_only_reader
    def discarded_author_count_for_jobs(self, job_ids: list[int]) -> int:
        current_job_ids = self._current_job_ids(job_ids)
        if not current_job_ids:
            return 0
        rows = (
            self.db.query(ScientificProductionAuthor)
            .outerjoin(
                ScientificProduction,
                ScientificProduction.id == ScientificProductionAuthor.production_id,
            )
            .filter(
                or_(
                    ScientificProductionAuthor.import_job_id.in_(current_job_ids),
                    and_(
                        ScientificProductionAuthor.import_job_id.is_(None),
                        ScientificProduction.import_job_id.in_(current_job_ids),
                    ),
                ),
            )
            .all()
        )
        return sum(
            1
            for row in rows
            if self._discarded_status(self.effective_validation_status(row))
        )

    @_read_only_reader
    def discarded_author_count_for_period(self, period_id: int) -> int:
        current_job_ids = [
            row[0]
            for row in self.db.query(ImportJob.id)
            .filter(ImportJob.is_current.is_(True), ImportJob.status == "SUCCESS")
            .all()
        ]
        if not current_job_ids:
            return 0
        rows = (
            self.db.query(ScientificProductionAuthor)
            .join(ScientificProduction, ScientificProduction.id == ScientificProductionAuthor.production_id)
            .filter(
                ScientificProduction.period_id == period_id,
                or_(
                    ScientificProductionAuthor.import_job_id.in_(current_job_ids),
                    and_(
                        ScientificProductionAuthor.import_job_id.is_(None),
                        ScientificProduction.import_job_id.in_(current_job_ids),
                    ),
                ),
            )
            .all()
        )
        return sum(
            1
            for row in rows
            if self._discarded_status(self.effective_validation_status(row))
        )

    @_read_only_reader
    def production_is_validated(self, production: ScientificProduction) -> bool:
        return self.effective_validation_status(production) == VALIDATED_STATUS and any(
            self.effective_validation_status(author) == VALIDATED_STATUS
            for author in production.authors
        )

    @staticmethod
    def _career_key(value: str | None) -> str:
        text = unicodedata.normalize("NFKD", str(value or "").upper())
        text = "".join(char for char in text if not unicodedata.combining(char))
        return " ".join(text.replace("LICENCIATURA EN ", "").replace("LICENCIATURA ", "").split())

    @classmethod
    def _career_matches(cls, value: str | None, expected: str | None) -> bool:
        left = cls._career_key(value)
        right = cls._career_key(expected)
        return bool(left and right and (left == right or left in right or right in left))

    @classmethod
    def _production_matches_careers(
        cls,
        production: ScientificProduction,
        career_ids: set[int],
        career_names: list[str],
    ) -> bool:
        if production.teacher and production.teacher.career_id in career_ids:
            return True
        entity_career = production.research_entity.career_name if production.research_entity else None
        return any(cls._career_matches(entity_career, name) for name in career_names)

    @_read_only_reader
    def progress_view(
        self,
        row: ImportedProgressReport,
        trace: ImportedOcrTrace | None,
    ) -> dict[str, Any]:
        job_ids = [row.import_job_id]
        participant_roles = self.roles_for_jobs(job_ids, validated_only=False)
        roles = [
            role
            for role in participant_roles
            if self.effective_validation_status(role) == VALIDATED_STATUS
            and self._effective_role_identity_is_validated(role)
        ]
        productions = self.productions_for_jobs(job_ids, validated_only=False)
        entities = self.entities_for_jobs(job_ids)

        group_members = self._group_members(roles)
        external_researchers = self._external_researchers(roles)
        scientific_products = self._scientific_products(productions)
        research_entities = [self._research_entity(entity) for entity in entities]
        group_projects = [
            entity
            for entity in research_entities
            if entity.get("type") == "proyecto_fci"
        ]
        project_directors = sorted(
            {
                self.effective_canonical_name(role)
                for role in roles
                if role.role_type in {"director", "director_proyecto", "coordinador", "tutor_semillero", "responsable_informe"}
                and self.effective_canonical_name(role)
            }
        )
        associated_teachers = sorted(
            {
                *[str(item["name"]) for item in group_members if item.get("name")],
                *[str(item["name"]) for item in external_researchers if item.get("name")],
                *project_directors,
            }
        )
        participants = self._normalized_participants(participant_roles)
        participant_summary = self._participant_summary(
            participants,
            discarded_invalid=self.discarded_author_count_for_jobs(job_ids),
        )
        product_counts = self._production_counts(scientific_products)
        research_topic = "; ".join(
            str(entity.get("name")) for entity in research_entities[:3] if entity.get("name")
        ) or None

        return {
            "id": row.id,
            "import_job_id": row.import_job_id,
            "career_name": row.career_name,
            "year_label": row.year_label,
            "cycle": row.cycle,
            "teacher_identifier": row.teacher_identifier,
            "teacher_name": row.teacher_name,
            "articles": product_counts["articles"],
            "books": product_counts["books"],
            "book_chapters": product_counts["book_chapters"],
            "presentations": product_counts["presentations"],
            "unclassified_products": product_counts["unclassified"],
            "projects": len(group_projects),
            "notes": row.notes,
            "research_topic": research_topic,
            "source_filename": trace.source_filename if trace else None,
            "source_path": trace.source_path if trace else None,
            "has_source_file": bool(trace and trace.source_path),
            "group_projects": group_projects,
            "research_entities": research_entities,
            "group_members": group_members,
            "external_researchers": external_researchers,
            "scientific_products": scientific_products,
            "project_directors": project_directors,
            "associated_teachers": associated_teachers,
            "normalized_participants": participants,
            "participants_summary": participant_summary,
            "person_aliases": [],
            "possible_merge_review": [],
        }

    @staticmethod
    def _role_identity_is_validated(role: PersonRole) -> bool:
        if role.person_type == "teacher":
            return bool(role.teacher and role.teacher.validation_status == VALIDATED_STATUS)
        if role.person_type == "external_researcher":
            return bool(role.external_researcher and not role.external_researcher.requires_review)
        return False

    def _effective_role_identity_is_validated(self, role: PersonRole) -> bool:
        projection = self._identity_projection_for(role)
        if projection is not None and projection.canonical_identity_key is not None:
            return True
        return self._role_identity_is_validated(role)

    def _group_members(self, roles: list[PersonRole]) -> list[dict[str, Any]]:
        selected: dict[int, dict[str, Any]] = {}
        for role in roles:
            teacher = role.teacher
            if not teacher or role.role_type == "autor_producto":
                continue
            selected[teacher.id] = {
                "name": self.effective_canonical_name(role),
                "faculty": teacher.career.faculty.name,
                "career": teacher.career.name,
                "source_section": role.source_section,
                "source_page": role.source_page,
                "validation_status": self.effective_validation_status(role),
            }
        return list(selected.values())

    def _external_researchers(self, roles: list[PersonRole]) -> list[dict[str, Any]]:
        selected: dict[int, dict[str, Any]] = {}
        for role in roles:
            researcher: ExternalResearcher | None = role.external_researcher
            if not researcher or role.role_type == "autor_producto":
                continue
            selected[researcher.id] = {
                "name": self.effective_canonical_name(role),
                "institution": self.effective_external_institution(researcher),
                "is_external": "true",
                "source_section": role.source_section,
                "source_page": role.source_page,
                "validation_status": self.effective_validation_status(role),
            }
        return list(selected.values())

    def _scientific_products(self, productions: list[ScientificProduction]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for production in productions:
            authors = sorted(
                (
                    author
                    for author in production.authors
                    if not self._discarded_status(
                        self.effective_validation_status(author)
                    )
                ),
                key=lambda author: author.author_order,
            )
            if not authors:
                continue
            items.append(
                {
                    "type": production.production_type.value,
                    "title": self.effective_product_title(production),
                    "authors": [
                        self.effective_canonical_name(author) for author in authors
                    ],
                    "status": production.status,
                    "impact": production.normalized_impact or production.raw_impact,
                    "link": production.link,
                    "source_section": production.source_section,
                    "source_page": production.source_page,
                    "validation_status": self.effective_validation_status(production),
                    "kpi_eligible": self.production_is_validated(production),
                }
            )
        return items

    def _research_entity(self, entity: ResearchEntity) -> dict[str, Any]:
        validation_status = self.effective_validation_status(entity)
        director_state = self.project_director_state(entity)
        return {
            "id": entity.id,
            "type": entity.type,
            "code": entity.code,
            "name": entity.name,
            "director": (
                director_state.get("director_canonical_name")
                or entity.director_name
            ),
            "status": entity.status,
            "progress_percentage": entity.progress_percentage,
            "career": entity.career_name,
            "academic_unit": entity.academic_unit,
            "source_section": entity.source_section,
            "source_page": entity.source_page,
            "validation_status": validation_status,
            "kpi_eligible": validation_status == VALIDATED_STATUS,
            "dashboard_counted": validation_status == VALIDATED_STATUS,
        }

    def _normalized_participants(self, roles: list[PersonRole]) -> list[dict[str, Any]]:
        participants: dict[str, dict[str, Any]] = {}
        for role in roles:
            state = self._participant_state(role)
            if not state["show_in_participants"]:
                continue
            name = self.effective_canonical_name(role)
            if not name and role.external_researcher:
                name = role.external_researcher.full_name
            key = self.effective_canonical_key(role)
            if key.startswith("legacy:") and role.person_key:
                key = str(role.person_key)
            if not key:
                key = str(name or "")
            if not key:
                continue
            person_type = self._participant_person_type(role)
            participant = participants.setdefault(
                key,
                {
                    "person_key": key,
                    "canonical_name": name,
                    "person_type": person_type,
                    "institutional_roles": [],
                    "production_roles": [],
                    "participations": [],
                    "validation_status": state["validation_status"],
                    "review_bucket": state["review_bucket"],
                    "show_in_participants": state["show_in_participants"],
                    "kpi_eligible": state["kpi_eligible"],
                    "participant_scope": "internal_fca" if person_type == "docente_interno" else "external",
                    "_state_rank": state["rank"],
                },
            )
            if state["rank"] > participant["_state_rank"]:
                participant.update(
                    validation_status=state["validation_status"],
                    review_bucket=state["review_bucket"],
                    show_in_participants=state["show_in_participants"],
                    kpi_eligible=state["kpi_eligible"],
                    person_type=person_type,
                    participant_scope="internal_fca" if person_type == "docente_interno" else "external",
                    _state_rank=state["rank"],
                )
            target = participant["production_roles"] if role.role_type == "autor_producto" else participant["institutional_roles"]
            if role.role_type not in target:
                target.append(role.role_type)
            if role.teacher:
                participation = {"faculty": role.teacher.career.faculty.name, "career": role.teacher.career.name}
                if participation not in participant["participations"]:
                    participant["participations"].append(participation)
            elif role.external_researcher:
                participation = {
                    "institution": self.effective_external_institution(
                        role.external_researcher
                    )
                }
                if participation not in participant["participations"]:
                    participant["participations"].append(participation)
        for participant in participants.values():
            participant.pop("_state_rank", None)
        return sorted(participants.values(), key=lambda item: str(item.get("canonical_name") or ""))

    def _participant_state(self, role: PersonRole) -> dict[str, Any]:
        validation_status = self.effective_validation_status(role)
        status_key = validation_status.casefold()
        discarded = status_key.startswith("discard") or status_key in {
            "invalid",
            "invalid_text_fragment",
            "descartado",
        }
        identity_validated = self._effective_role_identity_is_validated(role)
        if discarded:
            return {
                "validation_status": validation_status,
                "review_bucket": "invalid_text_fragment",
                "show_in_participants": False,
                "kpi_eligible": False,
                "rank": 0,
            }
        if validation_status == VALIDATED_STATUS and identity_validated:
            return {
                "validation_status": validation_status,
                "review_bucket": "valid_person",
                "show_in_participants": True,
                "kpi_eligible": True,
                "rank": 3,
            }
        if "merge" in status_key:
            review_bucket = "pending_merge"
        elif role.role_type == "autor_producto" or "author" in status_key or "autor" in status_key:
            review_bucket = "pending_author_classification"
        else:
            review_bucket = "pending_person"
        return {
            "validation_status": validation_status,
            "review_bucket": review_bucket,
            "show_in_participants": True,
            "kpi_eligible": False,
            "rank": 2 if review_bucket == "pending_merge" else 1,
        }

    @staticmethod
    def _participant_person_type(role: PersonRole) -> str:
        if role.person_type == "teacher":
            return "docente_interno"
        if role.person_type in {"external_researcher", "investigador_externo"}:
            return "investigador_externo"
        if role.person_type in {"student", "estudiante"}:
            return "estudiante"
        if role.person_type in {"graduate", "graduado"}:
            return "graduado"
        return "pendiente_clasificacion"

    @staticmethod
    def _participant_summary(
        participants: list[dict[str, Any]],
        *,
        discarded_invalid: int = 0,
    ) -> dict[str, int]:
        internal = sum(1 for item in participants if item["person_type"] == "docente_interno")
        external = sum(1 for item in participants if item["person_type"] == "investigador_externo")
        return {
            "total": len(participants),
            "show_in_participants_count": sum(1 for item in participants if item["show_in_participants"]),
            "docente_interno": internal,
            "investigador_externo": external,
            "kpi_eligible": sum(1 for item in participants if item["kpi_eligible"]),
            "pending_people_count": sum(1 for item in participants if item["review_bucket"] == "pending_person"),
            "pending_author_classification_count": sum(
                1 for item in participants if item["review_bucket"] == "pending_author_classification"
            ),
            "pending_merge_count": sum(1 for item in participants if item["review_bucket"] == "pending_merge"),
            "discarded_invalid": discarded_invalid,
            "invalid_text_fragments_count": discarded_invalid,
        }

    @staticmethod
    def _production_counts(products: list[dict[str, Any]]) -> dict[str, int]:
        counts = {"articles": 0, "books": 0, "book_chapters": 0, "presentations": 0, "unclassified": 0}
        mapping = {
            "ARTICLE": "articles",
            "BOOK": "books",
            "BOOK_CHAPTER": "book_chapters",
            "PRESENTATION": "presentations",
        }
        for product in products:
            if not product.get("kpi_eligible"):
                continue
            counts[mapping.get(str(product.get("type") or ""), "unclassified")] += 1
        return counts
