from __future__ import annotations

import unittest
from copy import deepcopy
from uuid import uuid4

from sqlalchemy import CheckConstraint, MetaData, create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.api.v1.endpoints.participants import list_participants
from app.api.v1.endpoints.production import list_production
from app.api.v1.endpoints.research_entities import list_research_entities
from app.models.entities import (
    AcademicPeriod,
    Career,
    ExternalResearcher,
    Faculty,
    ImportJob,
    ImportedProgressReport,
    PersonRole,
    ResearchEntity,
    ScientificProduction,
    ScientificProductionAuthor,
    Teacher,
    User,
)
from app.models.enums import ProductionType, UserRole
from app.models.human_review_core import ReviewDecision, ReviewItem
from app.models.human_review_projection import CanonicalIdentity, FieldOverride
from app.services.human_review_projection import (
    EffectiveHumanProjectionSource,
    load_identity_projection,
)
from app.services.kpi_service import KpiService
from app.services.validated_read_service import ValidatedReadService


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


def _override(
    *,
    stable_target_key: str,
    target_table: str,
    target_pk: int,
    field_path: str,
    value: str,
    scope: str = "record",
    document_key: str | None = None,
    period_id: int | None = None,
    relationship_key: str | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "field_path": field_path,
        "projected_value": _scalar(value),
        "scope": scope,
        "stable_target_key": stable_target_key,
        "target_table": target_table,
        "target_pk": target_pk,
        "document_key": document_key,
        "period_id": period_id,
        "relationship_key": relationship_key,
        "locked": True,
    }


class HumanReviewB2B2ReaderTests(unittest.TestCase):
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
            full_name="Nombre Docente Fuente",
            institutional_email=f"{uuid4().hex}@example.test",
            validation_status="validated",
        )
        self.db.add_all([faculty, self.career, self.period, self.job, self.teacher])
        self.db.flush()

    def tearDown(self) -> None:
        self.db.rollback()
        self.db.close()

    def _activate_projection(
        self,
        *,
        case_type: str,
        target_table: str,
        target_pk: int,
        marker: str,
        decision_type: str,
        overrides: list[dict[str, object]],
        identity: dict[str, object] | None = None,
        scientific_status: str = "validated",
        relationship_key: str | None = None,
        payload_schema: str = "review.decision.v1",
        payload_version: int = 1,
        lifecycle: str = "approved",
        locks_projection: bool = True,
        payload_kind: str = "auto",
    ) -> tuple[ReviewItem, ReviewDecision, list[FieldOverride]]:
        stable_target_key = _stable(case_type, marker)
        decision_id = uuid4()
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
            "overrides": overrides,
        }
        primary = overrides[0] if overrides else None
        if payload_kind == "identity_merge":
            payload = {
                "kind": "identity_merge",
                "schema_version": 1,
                "target_identity_key": identity["canonical_identity_key"],
                "member_stable_target_keys": [
                    stable_target_key,
                    _stable(case_type, "f"),
                ],
                "projection_before": before,
                "projection_after": after,
            }
        elif payload_kind == "maintain_separate":
            payload = {
                "kind": "maintain_separate",
                "schema_version": 1,
                "stable_target_keys": [
                    stable_target_key,
                    _stable(case_type, "e"),
                ],
                "identity_keys": [
                    identity["canonical_identity_key"],
                    f"{identity['canonical_identity_key']}:counterpart",
                ],
                "projection_before": before,
                "projection_after": after,
            }
        elif payload_kind == "identity_separation":
            payload = {
                "kind": "identity_separation",
                "schema_version": 1,
                "source_identity_key": f"{identity['canonical_identity_key']}:source",
                "assignments": [
                    {
                        "stable_target_key": stable_target_key,
                        "target_identity_key": identity["canonical_identity_key"],
                        "target_canonical_name": identity["canonical_name"],
                    }
                ],
                "projection_before": before,
                "projection_after": after,
            }
        elif identity is not None:
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
        else:
            assert primary is not None
            payload = {
                "kind": "field_override",
                "schema_version": 1,
                "field_path": primary["field_path"],
                "value": primary["projected_value"],
                "scope": primary["scope"],
                "stable_target_key": (
                    stable_target_key if primary["scope"] == "record" else None
                ),
                "document_key": primary["document_key"],
                "period_id": primary["period_id"],
                "relationship_key": primary["relationship_key"],
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
            field_path=str(primary["field_path"] if primary else "canonical_name"),
            raw_value_sha256=marker * 64,
            period_id=self.period.id,
            relationship_key=relationship_key,
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
            decision_lifecycle=lifecycle,
            scope=str(primary["scope"] if primary else "global_identity"),
            payload_schema=payload_schema,
            payload_version=payload_version,
            payload=payload,
            reason="Decisión humana de prueba",
            actor_type="human",
            actor_user_id=1,
            actor_identifier="gestor@example.test",
            actor_capability="RESEARCH_MANAGER",
            expected_case_version=1,
            locks_projection=locks_projection,
        )
        self.db.add(decision)
        self.db.flush()
        item.current_decision_id = decision.id
        materialized: list[FieldOverride] = []
        for snapshot in overrides:
            materialized.append(
                FieldOverride(
                    review_item_id=item.id,
                    decision_id=decision.id,
                    stable_target_key=str(snapshot["stable_target_key"]),
                    target_table=str(snapshot["target_table"]),
                    target_pk=int(snapshot["target_pk"]),
                    field_path=str(snapshot["field_path"]),
                    value_schema="override.scalar.v1",
                    value_version=1,
                    projected_value=deepcopy(snapshot["projected_value"]),
                    scope=str(snapshot["scope"]),
                    document_key=snapshot["document_key"],
                    period_id=snapshot["period_id"],
                    relationship_key=snapshot["relationship_key"],
                    locked=True,
                    is_active=True,
                )
            )
        self.db.add_all(materialized)
        self.db.flush()
        return item, decision, materialized

    def _build_cross_module_fixture(
        self,
        *,
        marker: str,
        source_identity_key: str,
        teacher: Teacher | None = None,
    ) -> dict[str, object]:
        person = teacher or self.teacher
        entity = ResearchEntity(
            period_id=self.period.id,
            import_job_id=self.job.id,
            type="proyecto_fci",
            name=f"Proyecto transversal {marker}",
            director_name=f"Nombre fuente {marker}",
            normalized_director_name=f"Nombre fuente {marker}",
            status="VIGENTE",
            validation_status="validated",
        )
        production = ScientificProduction(
            teacher_id=person.id,
            period_id=self.period.id,
            production_type=ProductionType.ARTICLE,
            title=f"Producto transversal {marker}",
            status="published",
            import_job_id=self.job.id,
            validation_status="validated",
        )
        self.db.add_all([entity, production])
        self.db.flush()
        reviewed_role = PersonRole(
            period_id=self.period.id,
            import_job_id=self.job.id,
            teacher_id=person.id,
            role_type="investigador",
            person_type="teacher",
            canonical_identity_key=source_identity_key,
            canonical_name=f"Nombre fuente {marker}",
            normalized_name=f"Nombre fuente {marker}",
            validation_status="validated",
        )
        project_role = PersonRole(
            period_id=self.period.id,
            import_job_id=self.job.id,
            teacher_id=person.id,
            research_entity_id=entity.id,
            role_type="director",
            person_type="teacher",
            canonical_identity_key=source_identity_key,
            canonical_name=f"Nombre fuente {marker}",
            normalized_name=f"Nombre fuente {marker}",
            validation_status="validated",
        )
        author = ScientificProductionAuthor(
            production_id=production.id,
            teacher_id=person.id,
            author_order=1,
            normalized_author_name=f"Nombre fuente {marker}",
            canonical_identity_key=source_identity_key,
            canonical_name=f"Nombre fuente {marker}",
            author_type="internal",
            validation_status="validated",
        )
        self.db.add_all([reviewed_role, project_role, author])
        self.db.flush()
        return {
            "entity": entity,
            "production": production,
            "reviewed_role": reviewed_role,
            "project_role": project_role,
            "author": author,
        }

    def test_approved_locked_identity_wins_without_mutating_source_rows(self) -> None:
        role = PersonRole(
            period_id=self.period.id,
            import_job_id=self.job.id,
            teacher_id=self.teacher.id,
            role_type="integrante_interno",
            person_type="teacher",
            person_key="PARSER-PERSON",
            canonical_identity_key="pending:parser-person",
            canonical_name="Nombre Parser Contrario",
            normalized_name="Nombre Parser Contrario",
            validation_status="pending_review",
        )
        self.db.add(role)
        self.db.flush()
        stable = _stable("person_identity", "a")
        identity = {
            "schema_version": 1,
            "canonical_identity_key": "human:person:approved",
            "canonical_name": "Nombre Humano Definitivo",
            "identity_type": "internal_person",
            "aliases": [],
        }
        overrides = [
            _override(
                stable_target_key=stable,
                target_table="person_roles",
                target_pk=role.id,
                field_path="canonical_identity_key",
                value="human:person:approved",
                scope="global_identity",
            ),
            _override(
                stable_target_key=stable,
                target_table="person_roles",
                target_pk=role.id,
                field_path="canonical_name",
                value="Nombre Humano Definitivo",
                scope="global_identity",
            ),
            _override(
                stable_target_key=stable,
                target_table="person_roles",
                target_pk=role.id,
                field_path="scientific_status",
                value="validated",
            ),
        ]
        self._activate_projection(
            case_type="person_identity",
            target_table="person_roles",
            target_pk=role.id,
            marker="a",
            decision_type="corrected",
            overrides=overrides,
            identity=identity,
        )
        self.db.expire_all()
        source_before = self.db.get(PersonRole, role.id)
        source_values = (
            source_before.canonical_identity_key,
            source_before.canonical_name,
            source_before.validation_status,
        )
        service = ValidatedReadService(self.db)

        first = service.canonical_participants(period_id=self.period.id)
        second = service.canonical_participants(period_id=self.period.id)

        self.assertEqual(len(first), 1)
        self.assertEqual(first, second)
        self.assertEqual(first[0]["canonical_identity_key"], "human:person:approved")
        self.assertEqual(first[0]["canonical_name"], "Nombre Humano Definitivo")
        self.assertEqual(first[0]["overall_status"], "validated")
        self.assertTrue(first[0]["kpi_eligible"])
        self.assertEqual(
            source_values,
            (
                source_before.canonical_identity_key,
                source_before.canonical_name,
                source_before.validation_status,
            ),
        )
        self.assertEqual(list(self.db.new), [])
        self.assertEqual(list(self.db.dirty), [])
        self.assertEqual(list(self.db.deleted), [])

    def test_global_identity_correction_collapses_source_appearances_in_fresh_session(self) -> None:
        source_identity_key = "source:person:shared"
        first_appearance = PersonRole(
            period_id=self.period.id,
            import_job_id=self.job.id,
            teacher_id=self.teacher.id,
            role_type="director",
            person_type="teacher",
            canonical_identity_key=source_identity_key,
            canonical_name="Nombre fuente",
            normalized_name="Nombre fuente",
            validation_status="pending_review",
            source_section="proyectos",
        )
        second_appearance = PersonRole(
            period_id=self.period.id,
            import_job_id=self.job.id,
            teacher_id=self.teacher.id,
            role_type="integrante_interno",
            person_type="teacher",
            canonical_identity_key=source_identity_key,
            canonical_name="Nombre fuente",
            normalized_name="Nombre fuente",
            validation_status="validated",
            source_section="participantes",
        )
        self.db.add_all([first_appearance, second_appearance])
        self.db.flush()

        stable = _stable("person_identity", "9")
        effective_identity_key = "human:person:corrected"
        review_item, review_decision, _ = self._activate_projection(
            case_type="person_identity",
            target_table="person_roles",
            target_pk=first_appearance.id,
            marker="9",
            decision_type="corrected",
            overrides=[
                _override(
                    stable_target_key=stable,
                    target_table="person_roles",
                    target_pk=first_appearance.id,
                    field_path="canonical_identity_key",
                    value=effective_identity_key,
                    scope="global_identity",
                ),
                _override(
                    stable_target_key=stable,
                    target_table="person_roles",
                    target_pk=first_appearance.id,
                    field_path="canonical_name",
                    value="Nombre humano corregido",
                    scope="global_identity",
                ),
                _override(
                    stable_target_key=stable,
                    target_table="person_roles",
                    target_pk=first_appearance.id,
                    field_path="scientific_status",
                    value="validated",
                ),
            ],
            identity={
                "schema_version": 1,
                "canonical_identity_key": effective_identity_key,
                "canonical_name": "Nombre humano corregido",
                "identity_type": "internal_person",
                "aliases": [],
            },
        )
        self.db.flush()

        with Session(
            bind=self.db.connection(),
            join_transaction_mode="create_savepoint",
        ) as fresh_db:
            stable_projection = load_identity_projection(fresh_db, stable)
            projection_source = EffectiveHumanProjectionSource(fresh_db)
            first_projection = projection_source.for_record(
                "person_roles", first_appearance.id
            )
            second_projection = projection_source.for_record(
                "person_roles", second_appearance.id
            )
            persisted_decision = fresh_db.get(ReviewDecision, review_decision.id)
            persisted_overrides = (
                fresh_db.query(FieldOverride)
                .filter(FieldOverride.review_item_id == review_item.id)
                .order_by(FieldOverride.field_path.asc())
                .all()
            )
            participants = ValidatedReadService(fresh_db).canonical_participants(
                period_id=self.period.id
            )
            source_rows = (
                fresh_db.query(PersonRole)
                .filter(PersonRole.id.in_([first_appearance.id, second_appearance.id]))
                .all()
            )

        self.assertEqual(persisted_decision.decision_type, "corrected")
        self.assertEqual(persisted_decision.scope, "global_identity")
        self.assertEqual(
            {row.field_path for row in persisted_overrides},
            {"canonical_identity_key", "canonical_name", "scientific_status"},
        )
        self.assertEqual(
            stable_projection.canonical_identity_key, effective_identity_key
        )
        self.assertEqual(
            first_projection.canonical_identity_key, effective_identity_key
        )
        self.assertIsNone(second_projection)
        self.assertEqual(len(participants), 1)
        self.assertEqual(
            participants[0]["canonical_identity_key"], effective_identity_key
        )
        self.assertEqual(
            participants[0]["canonical_name"], "Nombre humano corregido"
        )
        self.assertEqual(
            {variant["source_id"] for variant in participants[0]["variants"]},
            {first_appearance.id, second_appearance.id},
        )
        self.assertNotIn(
            source_identity_key,
            {row["canonical_identity_key"] for row in participants},
        )
        self.assertEqual(len(source_rows), 2)
        self.assertEqual(
            {row.canonical_identity_key for row in source_rows},
            {source_identity_key},
        )

    def test_source_appearances_without_decision_remain_one_original_identity(self) -> None:
        source_identity_key = "source:person:without-decision"
        roles = [
            PersonRole(
                period_id=self.period.id,
                import_job_id=self.job.id,
                teacher_id=self.teacher.id,
                role_type=role_type,
                person_type="teacher",
                canonical_identity_key=source_identity_key,
                canonical_name="Identidad fuente sin decisión",
                normalized_name="Identidad fuente sin decisión",
                validation_status="validated",
            )
            for role_type in ("director", "integrante_interno")
        ]
        self.db.add_all(roles)
        self.db.flush()

        with Session(
            bind=self.db.connection(),
            join_transaction_mode="create_savepoint",
        ) as fresh_db:
            participants = ValidatedReadService(fresh_db).canonical_participants(
                period_id=self.period.id
            )

        self.assertEqual(len(participants), 1)
        self.assertEqual(
            participants[0]["canonical_identity_key"], source_identity_key
        )
        self.assertEqual(
            {variant["source_id"] for variant in participants[0]["variants"]},
            {role.id for role in roles},
        )

    def test_global_identity_link_collapses_source_appearances(self) -> None:
        source_identity_key = "source:person:linked"
        linked_identity_key = "human:person:existing"
        self.db.add(
            CanonicalIdentity(
                canonical_identity_key=linked_identity_key,
                identity_type="internal_person",
                display_name="Identidad humana existente",
                status="active",
                origin="b1_locked",
            )
        )
        roles = [
            PersonRole(
                period_id=self.period.id,
                import_job_id=self.job.id,
                teacher_id=self.teacher.id,
                role_type=role_type,
                person_type="teacher",
                canonical_identity_key=source_identity_key,
                canonical_name="Identidad fuente enlazada",
                normalized_name="Identidad fuente enlazada",
                validation_status=(
                    "pending_review" if role_type == "director" else "validated"
                ),
            )
            for role_type in ("director", "integrante_interno")
        ]
        self.db.add_all(roles)
        self.db.flush()
        stable = _stable("person_identity", "8")
        self._activate_projection(
            case_type="person_identity",
            target_table="person_roles",
            target_pk=roles[0].id,
            marker="8",
            decision_type="linked",
            overrides=[
                _override(
                    stable_target_key=stable,
                    target_table="person_roles",
                    target_pk=roles[0].id,
                    field_path="canonical_identity_key",
                    value=linked_identity_key,
                    scope="global_identity",
                ),
                _override(
                    stable_target_key=stable,
                    target_table="person_roles",
                    target_pk=roles[0].id,
                    field_path="canonical_name",
                    value="Identidad humana existente",
                    scope="global_identity",
                ),
                _override(
                    stable_target_key=stable,
                    target_table="person_roles",
                    target_pk=roles[0].id,
                    field_path="scientific_status",
                    value="validated",
                ),
            ],
            identity={
                "schema_version": 1,
                "canonical_identity_key": linked_identity_key,
                "canonical_name": "Identidad humana existente",
                "identity_type": "internal_person",
                "aliases": [],
            },
        )

        with Session(
            bind=self.db.connection(),
            join_transaction_mode="create_savepoint",
        ) as fresh_db:
            participants = ValidatedReadService(fresh_db).canonical_participants(
                period_id=self.period.id
            )

        self.assertEqual(len(participants), 1)
        self.assertEqual(
            participants[0]["canonical_identity_key"], linked_identity_key
        )
        self.assertEqual(
            {variant["source_id"] for variant in participants[0]["variants"]},
            {role.id for role in roles},
        )

    def test_maintain_separate_keeps_two_persisted_identities(self) -> None:
        roles = [
            PersonRole(
                period_id=self.period.id,
                import_job_id=self.job.id,
                teacher_id=self.teacher.id,
                role_type=role_type,
                person_type="teacher",
                canonical_identity_key=identity_key,
                canonical_name=canonical_name,
                normalized_name=canonical_name,
                validation_status="validated",
            )
            for role_type, identity_key, canonical_name in (
                ("director", "human:separate:first", "Primera identidad"),
                ("integrante_interno", "human:separate:second", "Segunda identidad"),
            )
        ]
        self.db.add_all(roles)
        self.db.flush()
        stable = _stable("possible_duplicate", "5")
        self._activate_projection(
            case_type="possible_duplicate",
            target_table="person_roles",
            target_pk=roles[0].id,
            marker="5",
            decision_type="maintained_separate",
            overrides=[
                _override(
                    stable_target_key=stable,
                    target_table="person_roles",
                    target_pk=roles[0].id,
                    field_path="scientific_status",
                    value="validated",
                )
            ],
            identity={
                "schema_version": 1,
                "canonical_identity_key": "human:separate:first",
                "canonical_name": "Primera identidad",
                "identity_type": "internal_person",
                "aliases": [],
            },
            payload_kind="maintain_separate",
        )

        with Session(
            bind=self.db.connection(),
            join_transaction_mode="create_savepoint",
        ) as fresh_db:
            participants = ValidatedReadService(fresh_db).canonical_participants(
                period_id=self.period.id
            )

        self.assertEqual(len(participants), 2)
        self.assertEqual(
            {row["canonical_identity_key"] for row in participants},
            {"human:separate:first", "human:separate:second"},
        )

    def test_cross_module_corrected_identity_keeps_one_person_one_product_one_project_and_one_kpi_identity(self) -> None:
        source_identity_key = "source:cross-module:person"
        effective_identity_key = "human:cross-module:person"
        user = User(
            email=f"{uuid4().hex}@example.test",
            full_name="Gestora transversal",
            hashed_password="test-only",
            role=UserRole.FACULTY_ADMIN,
            is_active=True,
        )
        entity = ResearchEntity(
            period_id=self.period.id,
            import_job_id=self.job.id,
            type="proyecto_fci",
            name="Proyecto transversal",
            director_name="Nombre fuente",
            normalized_director_name="Nombre fuente",
            status="VIGENTE",
            validation_status="validated",
        )
        production = ScientificProduction(
            teacher_id=self.teacher.id,
            period_id=self.period.id,
            production_type=ProductionType.ARTICLE,
            title="Producto transversal",
            status="published",
            import_job_id=self.job.id,
            validation_status="validated",
        )
        self.db.add_all([user, entity, production])
        self.db.flush()
        reviewed_appearance = PersonRole(
            period_id=self.period.id,
            import_job_id=self.job.id,
            teacher_id=self.teacher.id,
            role_type="investigador",
            person_type="teacher",
            canonical_identity_key=source_identity_key,
            canonical_name="Nombre fuente",
            normalized_name="Nombre fuente",
            validation_status="validated",
        )
        project_appearance = PersonRole(
            period_id=self.period.id,
            import_job_id=self.job.id,
            teacher_id=self.teacher.id,
            research_entity_id=entity.id,
            role_type="director",
            person_type="teacher",
            canonical_identity_key=source_identity_key,
            canonical_name="Nombre fuente",
            normalized_name="Nombre fuente",
            validation_status="validated",
        )
        author = ScientificProductionAuthor(
            production_id=production.id,
            teacher_id=self.teacher.id,
            author_order=1,
            normalized_author_name="Nombre fuente",
            canonical_identity_key=source_identity_key,
            canonical_name="Nombre fuente",
            author_type="internal",
            validation_status="validated",
        )
        self.db.add_all([reviewed_appearance, project_appearance, author])
        self.db.flush()
        stable = _stable("person_identity", "4")
        self._activate_projection(
            case_type="person_identity",
            target_table="person_roles",
            target_pk=reviewed_appearance.id,
            marker="4",
            decision_type="corrected",
            overrides=[
                _override(
                    stable_target_key=stable,
                    target_table="person_roles",
                    target_pk=reviewed_appearance.id,
                    field_path="canonical_identity_key",
                    value=effective_identity_key,
                    scope="global_identity",
                ),
                _override(
                    stable_target_key=stable,
                    target_table="person_roles",
                    target_pk=reviewed_appearance.id,
                    field_path="canonical_name",
                    value="Nombre humano transversal",
                    scope="global_identity",
                ),
                _override(
                    stable_target_key=stable,
                    target_table="person_roles",
                    target_pk=reviewed_appearance.id,
                    field_path="scientific_status",
                    value="validated",
                ),
            ],
            identity={
                "schema_version": 1,
                "canonical_identity_key": effective_identity_key,
                "canonical_name": "Nombre humano transversal",
                "identity_type": "internal_person",
                "aliases": [],
            },
        )
        relation_stable = _stable("project_director_relation", "4")
        relationship_key = f"entity:{entity.id}:director"
        self._activate_projection(
            case_type="project_director_relation",
            target_table="research_entities",
            target_pk=entity.id,
            marker="4",
            decision_type="linked",
            relationship_key=relationship_key,
            overrides=[
                _override(
                    stable_target_key=relation_stable,
                    target_table="research_entities",
                    target_pk=entity.id,
                    field_path="project_director_relationship_status",
                    value="linked",
                    scope="relationship",
                    relationship_key=relationship_key,
                ),
                _override(
                    stable_target_key=relation_stable,
                    target_table="research_entities",
                    target_pk=entity.id,
                    field_path="scientific_status",
                    value="validated",
                ),
            ],
        )
        source_ids = {
            "reviewed": reviewed_appearance.id,
            "project": project_appearance.id,
            "author": author.id,
            "production": production.id,
            "entity": entity.id,
            "user": user.id,
        }

        with Session(
            bind=self.db.connection(),
            join_transaction_mode="create_savepoint",
        ) as fresh_db:
            read_service = ValidatedReadService(fresh_db)
            participants = read_service.canonical_participants(
                period_id=self.period.id
            )
            production_view = read_service.production_views(
                self.period.id, visibility="all"
            )
            project_view = read_service.entity_views(self.period.id)
            dashboard = KpiService(fresh_db).dashboard(
                self.period.year_label, self.period.cycle
            )
            fresh_user = fresh_db.get(User, source_ids["user"])
            public_participants = list_participants(
                career_id=None,
                role=None,
                status=None,
                db=fresh_db,
                user=fresh_user,
            )
            public_productions = list_production(
                period_id=self.period.id,
                career_id=None,
                visibility="all",
                db=fresh_db,
                user=fresh_user,
            )
            public_projects = list_research_entities(
                period_id=self.period.id,
                career_id=None,
                status=None,
                validation_status=None,
                db=fresh_db,
                user=fresh_user,
            )
            persisted_roles = (
                fresh_db.query(PersonRole)
                .filter(
                    PersonRole.id.in_(
                        [source_ids["reviewed"], source_ids["project"]]
                    )
                )
                .all()
            )
            persisted_author = fresh_db.get(
                ScientificProductionAuthor, source_ids["author"]
            )
            persisted_production = fresh_db.get(
                ScientificProduction, source_ids["production"]
            )
            persisted_entity = fresh_db.get(ResearchEntity, source_ids["entity"])
            fresh_session_dirty = list(fresh_db.dirty)

        with self.subTest(module="Participants"):
            self.assertEqual(len(participants), 1)
            self.assertEqual(
                participants[0]["canonical_identity_key"], effective_identity_key
            )
        with self.subTest(module="Scientific Production"):
            self.assertEqual(len(production_view), 1)
            self.assertEqual(len(production_view[0]["authors"]), 1)
            self.assertEqual(
                production_view[0]["authors"][0]["canonical_identity_key"],
                effective_identity_key,
            )
        with self.subTest(module="Projects"):
            self.assertEqual(len(project_view), 1)
            self.assertEqual(
                project_view[0]["director_canonical_identity_key"],
                effective_identity_key,
            )
        with self.subTest(module="KPI"):
            self.assertEqual(dashboard.canonical_identities_count, 1)
            self.assertEqual(dashboard.scientific_output_total, 1)
            self.assertEqual(dashboard.projects_total, 1)
            self.assertEqual(dashboard.authorships_count, 1)
        with self.subTest(module="Public endpoints"):
            self.assertEqual(
                [row["canonical_identity_key"] for row in public_participants],
                [effective_identity_key],
            )
            self.assertEqual(
                public_productions[0]["authors"][0]["canonical_identity_key"],
                effective_identity_key,
            )
            self.assertEqual(
                public_projects[0]["director_canonical_identity_key"],
                effective_identity_key,
            )
        self.assertEqual(
            {row.canonical_identity_key for row in persisted_roles},
            {source_identity_key},
        )
        self.assertEqual(
            persisted_author.canonical_identity_key, source_identity_key
        )
        self.assertEqual(
            (
                persisted_production.title,
                persisted_production.status,
                persisted_production.validation_status,
            ),
            ("Producto transversal", "published", "validated"),
        )
        self.assertEqual(
            (
                persisted_entity.name,
                persisted_entity.director_name,
                persisted_entity.status,
                persisted_entity.validation_status,
            ),
            (
                "Proyecto transversal",
                "Nombre fuente",
                "VIGENTE",
                "validated",
            ),
        )
        self.assertEqual(fresh_session_dirty, [])

    def test_cross_module_link_to_existing_identity_keeps_one_logical_person(self) -> None:
        source_identity_key = "source:cross-module:linked"
        linked_identity_key = "human:cross-module:existing"
        self.db.add(
            CanonicalIdentity(
                canonical_identity_key=linked_identity_key,
                identity_type="internal_person",
                display_name="Identidad humana existente transversal",
                status="active",
                origin="b1_locked",
            )
        )
        fixture = self._build_cross_module_fixture(
            marker="link",
            source_identity_key=source_identity_key,
        )
        reviewed_role = fixture["reviewed_role"]
        project_role = fixture["project_role"]
        author = fixture["author"]
        assert isinstance(reviewed_role, PersonRole)
        assert isinstance(project_role, PersonRole)
        assert isinstance(author, ScientificProductionAuthor)
        stable = _stable("person_identity", "3")
        self._activate_projection(
            case_type="person_identity",
            target_table="person_roles",
            target_pk=reviewed_role.id,
            marker="3",
            decision_type="linked",
            overrides=[
                _override(
                    stable_target_key=stable,
                    target_table="person_roles",
                    target_pk=reviewed_role.id,
                    field_path="canonical_identity_key",
                    value=linked_identity_key,
                    scope="global_identity",
                ),
                _override(
                    stable_target_key=stable,
                    target_table="person_roles",
                    target_pk=reviewed_role.id,
                    field_path="canonical_name",
                    value="Identidad humana existente transversal",
                    scope="global_identity",
                ),
                _override(
                    stable_target_key=stable,
                    target_table="person_roles",
                    target_pk=reviewed_role.id,
                    field_path="scientific_status",
                    value="validated",
                ),
            ],
            identity={
                "schema_version": 1,
                "canonical_identity_key": linked_identity_key,
                "canonical_name": "Identidad humana existente transversal",
                "identity_type": "internal_person",
                "aliases": [],
            },
        )

        with Session(
            bind=self.db.connection(),
            join_transaction_mode="create_savepoint",
        ) as fresh_db:
            reader = ValidatedReadService(fresh_db)
            participants = reader.canonical_participants(period_id=self.period.id)
            productions = reader.production_views(self.period.id, visibility="all")
            projects = reader.entity_views(self.period.id)
            dashboard = KpiService(fresh_db).dashboard(
                self.period.year_label, self.period.cycle
            )
            persisted_keys = {
                fresh_db.get(PersonRole, reviewed_role.id).canonical_identity_key,
                fresh_db.get(PersonRole, project_role.id).canonical_identity_key,
                fresh_db.get(
                    ScientificProductionAuthor, author.id
                ).canonical_identity_key,
            }

        self.assertEqual(
            [row["canonical_identity_key"] for row in participants],
            [linked_identity_key],
        )
        self.assertEqual(
            {row["authors"][0]["canonical_identity_key"] for row in productions},
            {linked_identity_key},
        )
        self.assertEqual(
            {row["director_canonical_identity_key"] for row in projects},
            {linked_identity_key},
        )
        self.assertEqual(dashboard.canonical_identities_count, 1)
        self.assertEqual(dashboard.authorships_count, 1)
        self.assertEqual(persisted_keys, {source_identity_key})

    def test_cross_module_maintain_separate_preserves_two_people_and_relationships(self) -> None:
        first_key = "human:cross-module:separate:first"
        second_key = "human:cross-module:separate:second"
        first = self._build_cross_module_fixture(
            marker="separate-first",
            source_identity_key=first_key,
        )
        second_teacher = Teacher(
            career=self.career,
            full_name="Segunda persona legítima",
            institutional_email=f"{uuid4().hex}@example.test",
            validation_status="validated",
        )
        self.db.add(second_teacher)
        self.db.flush()
        self._build_cross_module_fixture(
            marker="separate-second",
            source_identity_key=second_key,
            teacher=second_teacher,
        )
        first_role = first["reviewed_role"]
        assert isinstance(first_role, PersonRole)
        stable = _stable("possible_duplicate", "2")
        self._activate_projection(
            case_type="possible_duplicate",
            target_table="person_roles",
            target_pk=first_role.id,
            marker="2",
            decision_type="maintained_separate",
            overrides=[
                _override(
                    stable_target_key=stable,
                    target_table="person_roles",
                    target_pk=first_role.id,
                    field_path="scientific_status",
                    value="validated",
                )
            ],
            identity={
                "schema_version": 1,
                "canonical_identity_key": first_key,
                "canonical_name": "Primera persona legítima",
                "identity_type": "internal_person",
                "aliases": [],
            },
            payload_kind="maintain_separate",
        )

        with Session(
            bind=self.db.connection(),
            join_transaction_mode="create_savepoint",
        ) as fresh_db:
            reader = ValidatedReadService(fresh_db)
            participants = reader.canonical_participants(period_id=self.period.id)
            productions = reader.production_views(self.period.id, visibility="all")
            projects = reader.entity_views(self.period.id)
            dashboard = KpiService(fresh_db).dashboard(
                self.period.year_label, self.period.cycle
            )

        self.assertEqual(
            {row["canonical_identity_key"] for row in participants},
            {first_key, second_key},
        )
        self.assertEqual(
            {
                author_row["canonical_identity_key"]
                for production_row in productions
                for author_row in production_row["authors"]
            },
            {first_key, second_key},
        )
        self.assertEqual(
            {row["director_canonical_identity_key"] for row in projects},
            {first_key, second_key},
        )
        self.assertEqual(dashboard.canonical_identities_count, 2)
        self.assertEqual(dashboard.scientific_output_total, 2)
        self.assertEqual(dashboard.projects_total, 2)
        self.assertEqual(dashboard.authorships_count, 2)

    def test_cross_module_no_decision_preserves_multiple_products_projects_and_legitimate_people(self) -> None:
        main_key = "source:cross-module:multi"
        other_key = "source:cross-module:legitimate-other"
        first = self._build_cross_module_fixture(
            marker="multi-first",
            source_identity_key=main_key,
        )
        second = self._build_cross_module_fixture(
            marker="multi-second",
            source_identity_key=main_key,
        )
        other_teacher = Teacher(
            career=self.career,
            full_name="Coautora legítima",
            institutional_email=f"{uuid4().hex}@example.test",
            validation_status="validated",
        )
        self.db.add(other_teacher)
        self.db.flush()
        first_production = first["production"]
        first_entity = first["entity"]
        assert isinstance(first_production, ScientificProduction)
        assert isinstance(first_entity, ResearchEntity)
        other_author = ScientificProductionAuthor(
            production_id=first_production.id,
            teacher_id=other_teacher.id,
            author_order=2,
            normalized_author_name="Coautora legítima",
            canonical_identity_key=other_key,
            canonical_name="Coautora legítima",
            author_type="internal",
            validation_status="validated",
        )
        other_project_role = PersonRole(
            period_id=self.period.id,
            import_job_id=self.job.id,
            teacher_id=other_teacher.id,
            research_entity_id=first_entity.id,
            role_type="integrante_interno",
            person_type="teacher",
            canonical_identity_key=other_key,
            canonical_name="Coautora legítima",
            normalized_name="Coautora legítima",
            validation_status="validated",
        )
        self.db.add_all([other_author, other_project_role])
        self.db.flush()

        with Session(
            bind=self.db.connection(),
            join_transaction_mode="create_savepoint",
        ) as fresh_db:
            reader = ValidatedReadService(fresh_db)
            participants = reader.canonical_participants(period_id=self.period.id)
            productions = reader.production_views(self.period.id, visibility="all")
            projects = reader.entity_views(self.period.id)
            dashboard = KpiService(fresh_db).dashboard(
                self.period.year_label, self.period.cycle
            )

        participants_by_key = {
            row["canonical_identity_key"]: row for row in participants
        }
        authors_by_product = {
            row["id"]: {
                author_row["canonical_identity_key"]
                for author_row in row["authors"]
            }
            for row in productions
        }
        self.assertEqual(set(participants_by_key), {main_key, other_key})
        self.assertEqual(participants_by_key[main_key]["authorship_count"], 2)
        self.assertEqual(len(participants_by_key[main_key]["research_entities"]), 2)
        self.assertEqual(participants_by_key[other_key]["authorship_count"], 1)
        self.assertEqual(len(participants_by_key[other_key]["research_entities"]), 1)
        self.assertEqual(len(productions), 2)
        self.assertIn({main_key, other_key}, authors_by_product.values())
        self.assertIn({main_key}, authors_by_product.values())
        self.assertEqual(len(projects), 2)
        self.assertEqual(
            {row["director_canonical_identity_key"] for row in projects},
            {main_key},
        )
        self.assertEqual(dashboard.canonical_identities_count, 2)
        self.assertEqual(dashboard.scientific_output_total, 2)
        self.assertEqual(dashboard.projects_total, 2)
        self.assertEqual(dashboard.authorships_count, 3)

    def test_product_projection_and_reversal_switch_immediately_between_human_and_source(self) -> None:
        production = ScientificProduction(
            teacher_id=self.teacher.id,
            period_id=self.period.id,
            production_type=ProductionType.ARTICLE,
            title="Título Parser",
            normalized_title="Título Parser Reconstruido",
            status="published",
            import_job_id=self.job.id,
            validation_status="pending_review",
        )
        self.db.add(production)
        self.db.flush()
        self.db.add(
            ScientificProductionAuthor(
                production_id=production.id,
                author_order=1,
                normalized_author_name="Autora Válida",
                canonical_identity_key="human:author:valid",
                author_type="internal",
                validation_status="validated",
            )
        )
        self.db.flush()
        stable = _stable("product", "b")
        overrides = [
            _override(
                stable_target_key=stable,
                target_table="scientific_productions",
                target_pk=production.id,
                field_path="product_title",
                value="Título Humano",
            ),
            _override(
                stable_target_key=stable,
                target_table="scientific_productions",
                target_pk=production.id,
                field_path="scientific_status",
                value="validated",
            ),
        ]
        item, applied_decision, materialized = self._activate_projection(
            case_type="product",
            target_table="scientific_productions",
            target_pk=production.id,
            marker="b",
            decision_type="corrected",
            overrides=overrides,
        )
        service = ValidatedReadService(self.db)

        applied = service.production_views(self.period.id, visibility="all")

        self.assertEqual(applied[0]["title"], "Título Humano")
        self.assertEqual(applied[0]["canonical_title"], "Título Humano")
        self.assertEqual(applied[0]["validation_status"], "validated")
        self.assertEqual(applied[0]["visibility"], "eligible")
        self.assertTrue(applied[0]["kpi_eligible"])
        self.assertEqual(production.normalized_title, "Título Parser Reconstruido")
        self.assertEqual(production.validation_status, "pending_review")

        applied_payload = deepcopy(applied_decision.payload)
        reversal_id = uuid4()
        reversal = ReviewDecision(
            id=reversal_id,
            review_item_id=item.id,
            sequence=2,
            decision_type="reverted",
            decision_lifecycle="approved",
            scope="record",
            payload_schema="review.decision.v1",
            payload_version=1,
            payload={
                "kind": "decision_reversal",
                "schema_version": 1,
                "decision_id_to_revert": str(applied_decision.id),
                "restore_decision_id": None,
                "projection_before": applied_payload["projection_after"],
                "projection_after": applied_payload["projection_before"],
            },
            reason="Reversión funcional",
            actor_type="human",
            actor_user_id=1,
            actor_identifier="gestor@example.test",
            actor_capability="RESEARCH_MANAGER",
            expected_case_version=2,
            previous_decision_id=applied_decision.id,
            corrects_decision_id=applied_decision.id,
            locks_projection=True,
        )
        self.db.add(reversal)
        self.db.flush()
        item.current_decision_id = reversal.id
        item.case_status = "pending"
        item.scientific_status = "pending"
        for row in materialized:
            row.is_active = False
        self.db.flush()

        reverted = service.production_views(self.period.id, visibility="all")

        self.assertEqual(reverted[0]["title"], "Título Parser Reconstruido")
        self.assertEqual(reverted[0]["validation_status"], "pending_review")
        self.assertEqual(reverted[0]["visibility"], "pending")
        self.assertFalse(reverted[0]["kpi_eligible"])

    def test_project_director_relationship_uses_effective_human_projection(self) -> None:
        entity = ResearchEntity(
            period_id=self.period.id,
            import_job_id=self.job.id,
            type="proyecto_fci",
            name="Proyecto Fuente",
            director_name="Director Parser",
            normalized_director_name="Director Parser",
            validation_status="pending_review",
        )
        self.db.add(entity)
        self.db.flush()
        stable = _stable("project_director_relation", "c")
        relationship_key = f"entity:{entity.id}:director"
        overrides = [
            _override(
                stable_target_key=stable,
                target_table="research_entities",
                target_pk=entity.id,
                field_path="project_director_identity_key",
                value="human:director:approved",
                scope="relationship",
                relationship_key=relationship_key,
            ),
            _override(
                stable_target_key=stable,
                target_table="research_entities",
                target_pk=entity.id,
                field_path="project_director_relationship_status",
                value="validated",
                scope="relationship",
                relationship_key=relationship_key,
            ),
            _override(
                stable_target_key=stable,
                target_table="research_entities",
                target_pk=entity.id,
                field_path="scientific_status",
                value="validated",
            ),
        ]
        self._activate_projection(
            case_type="project_director_relation",
            target_table="research_entities",
            target_pk=entity.id,
            marker="c",
            decision_type="linked",
            overrides=overrides,
            relationship_key=relationship_key,
        )

        state = ValidatedReadService(self.db).project_director_state(entity)

        self.assertEqual(state["project_validation_status"], "validated")
        self.assertEqual(state["director_canonical_identity_key"], "human:director:approved")
        self.assertEqual(state["director_relationship_validation_status"], "validated")
        self.assertEqual(entity.validation_status, "pending_review")

    def test_possible_duplicate_resolutions_publish_their_typed_identity_snapshot(self) -> None:
        cases = (
            ("merged", "identity_merge", "4"),
            ("maintained_separate", "maintain_separate", "5"),
            ("separated", "identity_separation", "6"),
        )
        source = EffectiveHumanProjectionSource(self.db)
        for decision_type, payload_kind, marker in cases:
            with self.subTest(decision_type=decision_type):
                role = PersonRole(
                    period_id=self.period.id,
                    import_job_id=self.job.id,
                    teacher_id=self.teacher.id,
                    role_type=f"rol_{decision_type}",
                    person_type="teacher",
                    canonical_identity_key=f"pending:{decision_type}",
                    canonical_name=f"Parser {decision_type}",
                    normalized_name=f"Parser {decision_type}",
                    validation_status="pending_review",
                )
                self.db.add(role)
                self.db.flush()
                stable = _stable("possible_duplicate", marker)
                identity = {
                    "schema_version": 1,
                    "canonical_identity_key": f"human:{decision_type}",
                    "canonical_name": f"Humano {decision_type}",
                    "identity_type": "internal_person",
                    "aliases": [],
                }
                overrides = [
                    _override(
                        stable_target_key=stable,
                        target_table="person_roles",
                        target_pk=role.id,
                        field_path="scientific_status",
                        value="validated",
                    )
                ]
                self._activate_projection(
                    case_type="possible_duplicate",
                    target_table="person_roles",
                    target_pk=role.id,
                    marker=marker,
                    decision_type=decision_type,
                    overrides=overrides,
                    identity=identity,
                    payload_kind=payload_kind,
                )

                projection = source.for_record("person_roles", role.id)

                self.assertIsNotNone(projection)
                self.assertEqual(
                    projection.canonical_identity_key,
                    f"human:{decision_type}",
                )
                self.assertEqual(projection.scientific_status, "validated")

    def test_author_external_projections_apply_only_for_compatible_case_types(self) -> None:
        production = ScientificProduction(
            teacher_id=self.teacher.id,
            period_id=self.period.id,
            production_type=ProductionType.ARTICLE,
            title="Producto con autor",
            import_job_id=self.job.id,
            validation_status="validated",
        )
        self.db.add(production)
        self.db.flush()
        author = ScientificProductionAuthor(
            production_id=production.id,
            author_order=1,
            normalized_author_name="Autor Parser",
            canonical_identity_key="pending:author-parser",
            author_type="internal",
            validation_status="pending_review",
        )
        external = ExternalResearcher(
            period_id=self.period.id,
            import_job_id=self.job.id,
            full_name="Externo Parser",
            normalized_name="EXTERNO PARSER",
            institution="Institución Parser",
            normalized_institution="INSTITUCION PARSER",
            requires_review=True,
        )
        self.db.add_all([author, external])
        self.db.flush()
        external_role = PersonRole(
            period_id=self.period.id,
            import_job_id=self.job.id,
            external_researcher_id=external.id,
            role_type="integrante_externo",
            person_type="external_researcher",
            canonical_identity_key="pending:external-parser",
            canonical_name="Externo Parser",
            normalized_name="Externo Parser",
            validation_status="pending_review",
        )
        invalid_role = PersonRole(
            period_id=self.period.id,
            import_job_id=self.job.id,
            role_type="autor_producto",
            person_type="unresolved",
            canonical_identity_key="pending:invalid-fragment",
            normalized_name="Fragmento inválido",
            validation_status="pending_review",
        )
        conflict_product = ScientificProduction(
            teacher_id=self.teacher.id,
            period_id=self.period.id,
            production_type=ProductionType.BOOK,
            title="Nueva evidencia",
            import_job_id=self.job.id,
            validation_status="pending_review",
        )
        self.db.add_all([external_role, invalid_role, conflict_product])
        self.db.flush()

        author_stable = _stable("author_identity", "7")
        self._activate_projection(
            case_type="author_identity",
            target_table="scientific_production_authors",
            target_pk=author.id,
            marker="7",
            decision_type="linked",
            overrides=[
                _override(
                    stable_target_key=author_stable,
                    target_table="scientific_production_authors",
                    target_pk=author.id,
                    field_path="author_identity_key",
                    value="human:author:linked",
                ),
                _override(
                    stable_target_key=author_stable,
                    target_table="scientific_production_authors",
                    target_pk=author.id,
                    field_path="canonical_name",
                    value="Autor Humano",
                ),
                _override(
                    stable_target_key=author_stable,
                    target_table="scientific_production_authors",
                    target_pk=author.id,
                    field_path="scientific_status",
                    value="validated",
                ),
            ],
            identity={
                "schema_version": 1,
                "canonical_identity_key": "human:author:linked",
                "canonical_name": "Autor Humano",
                "identity_type": "internal_person",
                "aliases": [],
            },
        )
        external_stable = _stable("external_identity", "8")
        self._activate_projection(
            case_type="external_identity",
            target_table="external_researchers",
            target_pk=external.id,
            marker="8",
            decision_type="linked",
            overrides=[
                _override(
                    stable_target_key=external_stable,
                    target_table="external_researchers",
                    target_pk=external.id,
                    field_path="external_identity_key",
                    value="human:external:linked",
                ),
                _override(
                    stable_target_key=external_stable,
                    target_table="external_researchers",
                    target_pk=external.id,
                    field_path="external_institution",
                    value="Institución Humana",
                ),
                _override(
                    stable_target_key=external_stable,
                    target_table="external_researchers",
                    target_pk=external.id,
                    field_path="scientific_status",
                    value="validated",
                ),
            ],
            identity={
                "schema_version": 1,
                "canonical_identity_key": "human:external:linked",
                "canonical_name": "Externo Humano",
                "identity_type": "external_person",
                "aliases": [],
            },
        )
        invalid_stable = _stable("invalid_text", "9")
        self._activate_projection(
            case_type="invalid_text",
            target_table="person_roles",
            target_pk=invalid_role.id,
            marker="9",
            decision_type="discarded",
            scientific_status="discarded",
            overrides=[
                _override(
                    stable_target_key=invalid_stable,
                    target_table="person_roles",
                    target_pk=invalid_role.id,
                    field_path="scientific_status",
                    value="discarded",
                )
            ],
        )
        conflict_stable = _stable("new_evidence_conflict", "0")
        self._activate_projection(
            case_type="new_evidence_conflict",
            target_table="scientific_productions",
            target_pk=conflict_product.id,
            marker="0",
            decision_type="maintained",
            overrides=[
                _override(
                    stable_target_key=conflict_stable,
                    target_table="scientific_productions",
                    target_pk=conflict_product.id,
                    field_path="scientific_status",
                    value="validated",
                )
            ],
        )
        service = ValidatedReadService(self.db)

        product_view = service.production_views(self.period.id, visibility="all")
        participants = service.canonical_participants(period_id=self.period.id)

        projected_author = next(
            row["authors"][0]
            for row in product_view
            if row["id"] == production.id
        )
        self.assertEqual(projected_author["canonical_identity_key"], "human:author:linked")
        self.assertEqual(projected_author["canonical_name"], "Autor Humano")
        external_participant = next(
            row for row in participants if row["canonical_identity_key"] == "human:external:linked"
        )
        self.assertEqual(external_participant["canonical_name"], "Externo Humano")
        self.assertEqual(external_participant["affiliations"], ["Institución Humana"])
        self.assertIn("pending:invalid-fragment", {
            row["canonical_identity_key"] for row in participants
        })
        conflict_view = next(row for row in product_view if row["id"] == conflict_product.id)
        self.assertFalse(conflict_view["kpi_eligible"])

    def test_corrupt_incompatible_and_ambiguous_projections_all_fail_closed(self) -> None:
        corruptions = (
            ("decision lifecycle", "decision_lifecycle", "proposed"),
            ("decision lock", "locks_projection", False),
            ("payload schema", "payload_schema", "unknown.v9"),
            ("payload version", "payload_version", 9),
            ("override active", "is_active", False),
            ("override lock", "locked", False),
            ("override schema", "value_schema", "unknown.v9"),
            ("override version", "value_version", 9),
        )
        for index, (label, attribute, value) in enumerate(corruptions):
            with self.subTest(label=label):
                marker = format(index + 1, "x")
                role = PersonRole(
                    period_id=self.period.id,
                    import_job_id=self.job.id,
                    teacher_id=self.teacher.id,
                    role_type=f"corrupt_{index}",
                    person_type="teacher",
                    canonical_identity_key=f"pending:corrupt-{index}",
                    canonical_name=f"Fallback {index}",
                    normalized_name=f"Fallback {index}",
                    validation_status="pending_review",
                )
                self.db.add(role)
                self.db.flush()
                stable = _stable("person_identity", marker)
                item, decision, rows = self._activate_projection(
                    case_type="person_identity",
                    target_table="person_roles",
                    target_pk=role.id,
                    marker=marker,
                    decision_type="corrected",
                    overrides=[
                        _override(
                            stable_target_key=stable,
                            target_table="person_roles",
                            target_pk=role.id,
                            field_path="canonical_name",
                            value=f"Must Not Leak {index}",
                            scope="global_identity",
                        )
                    ],
                    identity={
                        "schema_version": 1,
                        "canonical_identity_key": f"human:corrupt-{index}",
                        "canonical_name": f"Must Not Leak {index}",
                        "identity_type": "internal_person",
                        "aliases": [],
                    },
                )
                target = decision if hasattr(decision, attribute) else rows[0]
                setattr(target, attribute, value)
                self.db.flush()

                projection = EffectiveHumanProjectionSource(self.db).for_record(
                    "person_roles", role.id
                )

                self.assertIsNone(projection)
                self.assertEqual(role.canonical_name, f"Fallback {index}")

        ambiguous_role = PersonRole(
            period_id=self.period.id,
            import_job_id=self.job.id,
            teacher_id=self.teacher.id,
            role_type="ambiguous",
            person_type="teacher",
            canonical_identity_key="pending:ambiguous",
            canonical_name="Fallback ambiguo",
            normalized_name="Fallback ambiguo",
            validation_status="pending_review",
        )
        self.db.add(ambiguous_role)
        self.db.flush()
        for marker in ("a", "b"):
            stable = _stable("person_identity", marker)
            self._activate_projection(
                case_type="person_identity",
                target_table="person_roles",
                target_pk=ambiguous_role.id,
                marker=marker,
                decision_type="corrected",
                overrides=[
                    _override(
                        stable_target_key=stable,
                        target_table="person_roles",
                        target_pk=role.id,
                        field_path="canonical_identity_key",
                        value=f"human:crossed-{marker}",
                        scope="global_identity",
                    ),
                    _override(
                        stable_target_key=stable,
                        target_table="person_roles",
                        target_pk=ambiguous_role.id,
                        field_path="canonical_name",
                        value=f"Ambiguous {marker}",
                        scope="global_identity",
                    )
                ],
                identity={
                    "schema_version": 1,
                    "canonical_identity_key": f"human:ambiguous-{marker}",
                    "canonical_name": f"Ambiguous {marker}",
                    "identity_type": "internal_person",
                    "aliases": [],
                },
            )

        self.assertIsNone(
            EffectiveHumanProjectionSource(self.db).for_record(
                "person_roles", ambiguous_role.id
            )
        )

    def test_inactive_projection_fails_closed_to_parser_data(self) -> None:
        role = PersonRole(
            period_id=self.period.id,
            import_job_id=self.job.id,
            teacher_id=self.teacher.id,
            role_type="integrante_interno",
            person_type="teacher",
            canonical_identity_key="pending:safe-parser",
            canonical_name="Fallback Seguro",
            normalized_name="Fallback Seguro",
            validation_status="pending_review",
        )
        self.db.add(role)
        self.db.flush()
        stable = _stable("person_identity", "d")
        identity = {
            "schema_version": 1,
            "canonical_identity_key": "human:must-not-leak",
            "canonical_name": "Humano Inválido",
            "identity_type": "internal_person",
            "aliases": [],
        }
        overrides = [
            _override(
                stable_target_key=stable,
                target_table="person_roles",
                target_pk=role.id,
                field_path="canonical_identity_key",
                value="human:must-not-leak",
                scope="global_identity",
            ),
            _override(
                stable_target_key=stable,
                target_table="person_roles",
                target_pk=role.id,
                field_path="canonical_name",
                value="Humano Inválido",
                scope="global_identity",
            ),
        ]
        _, _, materialized = self._activate_projection(
            case_type="person_identity",
            target_table="person_roles",
            target_pk=role.id,
            marker="d",
            decision_type="corrected",
            overrides=overrides,
            identity=identity,
        )
        materialized[0].is_active = False
        self.db.flush()

        result = ValidatedReadService(self.db).canonical_participants(
            period_id=self.period.id
        )

        self.assertEqual(result[0]["canonical_identity_key"], "pending:safe-parser")
        self.assertEqual(result[0]["canonical_name"], "Fallback Seguro")
        self.assertEqual(result[0]["overall_status"], "pending_review")

    def test_functional_reversal_restores_a_previous_human_snapshot(self) -> None:
        entity = ResearchEntity(
            period_id=self.period.id,
            import_job_id=self.job.id,
            type="proyecto_fci",
            name="Proyecto de reversión",
            director_name="Nombre Parser",
            normalized_director_name="Nombre Parser",
            status="VIGENTE",
            validation_status="validated",
        )
        production = ScientificProduction(
            teacher_id=self.teacher.id,
            period_id=self.period.id,
            production_type=ProductionType.ARTICLE,
            title="Producto de reversión",
            status="published",
            import_job_id=self.job.id,
            validation_status="validated",
        )
        self.db.add_all([entity, production])
        self.db.flush()
        role = PersonRole(
            period_id=self.period.id,
            import_job_id=self.job.id,
            teacher_id=self.teacher.id,
            role_type="reversal_snapshot",
            person_type="teacher",
            canonical_identity_key="pending:parser-reversal",
            canonical_name="Nombre Parser",
            normalized_name="Nombre Parser",
            validation_status="pending_review",
        )
        second_appearance = PersonRole(
            period_id=self.period.id,
            import_job_id=self.job.id,
            teacher_id=self.teacher.id,
            research_entity_id=entity.id,
            role_type="director",
            person_type="teacher",
            canonical_identity_key="pending:parser-reversal",
            canonical_name="Nombre Parser",
            normalized_name="Nombre Parser",
            validation_status="validated",
        )
        author = ScientificProductionAuthor(
            production_id=production.id,
            teacher_id=self.teacher.id,
            author_order=1,
            normalized_author_name="Nombre Parser",
            canonical_identity_key="pending:parser-reversal",
            canonical_name="Nombre Parser",
            author_type="internal",
            validation_status="validated",
        )
        self.db.add_all([role, second_appearance, author])
        self.db.flush()
        with Session(
            bind=self.db.connection(),
            join_transaction_mode="create_savepoint",
        ) as fresh_db:
            dashboard_before = KpiService(fresh_db).dashboard(
                self.period.year_label, self.period.cycle
            )
            kpi_before = (
                dashboard_before.canonical_identities_count,
                dashboard_before.scientific_output_total,
                dashboard_before.projects_total,
                dashboard_before.authorships_count,
            )
        stable = _stable("person_identity", "d")
        first_identity = {
            "schema_version": 1,
            "canonical_identity_key": "human:identity:first",
            "canonical_name": "Primera DecisiÃ³n Humana",
            "identity_type": "internal_person",
            "aliases": [],
        }
        first_overrides = [
            _override(
                stable_target_key=stable,
                target_table="person_roles",
                target_pk=role.id,
                field_path="canonical_identity_key",
                value="human:identity:first",
                scope="global_identity",
            ),
            _override(
                stable_target_key=stable,
                target_table="person_roles",
                target_pk=role.id,
                field_path="canonical_name",
                value="Primera DecisiÃ³n Humana",
                scope="global_identity",
            ),
            _override(
                stable_target_key=stable,
                target_table="person_roles",
                target_pk=role.id,
                field_path="scientific_status",
                value="validated",
            ),
        ]
        item, first, first_rows = self._activate_projection(
            case_type="person_identity",
            target_table="person_roles",
            target_pk=role.id,
            marker="d",
            decision_type="corrected",
            overrides=first_overrides,
            identity=first_identity,
        )
        with Session(
            bind=self.db.connection(),
            join_transaction_mode="create_savepoint",
        ) as fresh_db:
            correction_reader = ValidatedReadService(fresh_db)
            correction_productions = correction_reader.production_views(
                self.period.id, visibility="all"
            )
            correction_projects = correction_reader.entity_views(self.period.id)
            dashboard_after_correction = KpiService(fresh_db).dashboard(
                self.period.year_label, self.period.cycle
            )
            kpi_after_correction = (
                dashboard_after_correction.canonical_identities_count,
                dashboard_after_correction.scientific_output_total,
                dashboard_after_correction.projects_total,
                dashboard_after_correction.authorships_count,
            )
        self.assertEqual(
            correction_productions[0]["authors"][0]["canonical_identity_key"],
            "human:identity:first",
        )
        self.assertEqual(
            correction_projects[0]["director_canonical_identity_key"],
            "human:identity:first",
        )
        second_id = uuid4()
        second_identity = {
            "schema_version": 1,
            "canonical_identity_key": "human:identity:second",
            "canonical_name": "Segunda DecisiÃ³n Humana",
            "identity_type": "internal_person",
            "aliases": [],
        }
        second_overrides = [
            _override(
                stable_target_key=stable,
                target_table="person_roles",
                target_pk=role.id,
                field_path="canonical_identity_key",
                value="human:identity:second",
                scope="global_identity",
            ),
            _override(
                stable_target_key=stable,
                target_table="person_roles",
                target_pk=role.id,
                field_path="canonical_name",
                value="Segunda DecisiÃ³n Humana",
                scope="global_identity",
            ),
            _override(
                stable_target_key=stable,
                target_table="person_roles",
                target_pk=role.id,
                field_path="scientific_status",
                value="validated",
            ),
        ]
        second_snapshot = {
            "schema_version": 1,
            "case_status": "resolved",
            "scientific_status": "validated",
            "current_decision_id": str(second_id),
            "identity": second_identity,
            "overrides": second_overrides,
        }
        second = ReviewDecision(
            id=second_id,
            review_item_id=item.id,
            sequence=2,
            decision_type="corrected",
            decision_lifecycle="approved",
            scope="global_identity",
            payload_schema="review.decision.v1",
            payload_version=1,
            payload={
                "kind": "identity",
                "schema_version": 1,
                "canonical_identity_key": second_identity["canonical_identity_key"],
                "canonical_name": second_identity["canonical_name"],
                "identity_type": second_identity["identity_type"],
                "alias_original": None,
                "alias_normalized": None,
                "projection_before": deepcopy(first.payload["projection_after"]),
                "projection_after": second_snapshot,
            },
            reason="CorrecciÃ³n posterior",
            actor_type="human",
            actor_user_id=1,
            actor_identifier="gestor@example.test",
            actor_capability="RESEARCH_MANAGER",
            expected_case_version=2,
            previous_decision_id=first.id,
            locks_projection=True,
        )
        self.db.add(second)
        second_values = {
            str(snapshot["field_path"]): deepcopy(snapshot["projected_value"])
            for snapshot in second_overrides
        }
        for row in first_rows:
            row.decision_id = second.id
            row.projected_value = second_values[row.field_path]
        item.current_decision_id = second.id
        self.db.flush()

        reversal_id = uuid4()
        reversal = ReviewDecision(
            id=reversal_id,
            review_item_id=item.id,
            sequence=3,
            decision_type="reverted",
            decision_lifecycle="approved",
            scope="global_identity",
            payload_schema="review.decision.v1",
            payload_version=1,
            payload={
                "kind": "decision_reversal",
                "schema_version": 1,
                "decision_id_to_revert": str(second.id),
                "restore_decision_id": str(first.id),
                "projection_before": second_snapshot,
                "projection_after": deepcopy(first.payload["projection_after"]),
            },
            reason="ReversiÃ³n a la decisiÃ³n humana previa",
            actor_type="human",
            actor_user_id=1,
            actor_identifier="gestor@example.test",
            actor_capability="RESEARCH_MANAGER",
            expected_case_version=3,
            previous_decision_id=second.id,
            corrects_decision_id=second.id,
            locks_projection=True,
        )
        self.db.add(reversal)
        first_values = {
            str(snapshot["field_path"]): deepcopy(snapshot["projected_value"])
            for snapshot in first_overrides
        }
        for row in first_rows:
            row.decision_id = reversal.id
            row.projected_value = first_values[row.field_path]
        item.current_decision_id = reversal.id
        self.db.flush()
        self.db.expire_all()

        with Session(
            bind=self.db.connection(),
            join_transaction_mode="create_savepoint",
        ) as fresh_db:
            projection = EffectiveHumanProjectionSource(fresh_db).for_record(
                "person_roles", role.id
            )
            participants = ValidatedReadService(fresh_db).canonical_participants(
                period_id=self.period.id
            )
            reverted_reader = ValidatedReadService(fresh_db)
            reverted_productions = reverted_reader.production_views(
                self.period.id, visibility="all"
            )
            reverted_projects = reverted_reader.entity_views(self.period.id)
            dashboard_after_reversal = KpiService(fresh_db).dashboard(
                self.period.year_label, self.period.cycle
            )
            kpi_after_reversal = (
                dashboard_after_reversal.canonical_identities_count,
                dashboard_after_reversal.scientific_output_total,
                dashboard_after_reversal.projects_total,
                dashboard_after_reversal.authorships_count,
            )
            source_rows = (
                fresh_db.query(PersonRole)
                .filter(PersonRole.id.in_([role.id, second_appearance.id]))
                .all()
            )
            source_author = fresh_db.get(ScientificProductionAuthor, author.id)

        self.assertIsNotNone(projection)
        self.assertEqual(projection.decision_id, reversal.id)
        self.assertEqual(projection.canonical_identity_key, "human:identity:first")
        self.assertEqual(projection.canonical_name, "Primera DecisiÃ³n Humana")
        self.assertEqual(len(participants), 1)
        self.assertEqual(participants[0]["canonical_identity_key"], "human:identity:first")
        self.assertEqual(participants[0]["canonical_name"], "Primera DecisiÃ³n Humana")
        self.assertEqual(
            {variant["source_id"] for variant in participants[0]["variants"]},
            {role.id, second_appearance.id, author.id},
        )
        self.assertEqual(
            reverted_productions[0]["authors"][0]["canonical_identity_key"],
            "human:identity:first",
        )
        self.assertEqual(
            reverted_projects[0]["director_canonical_identity_key"],
            "human:identity:first",
        )
        self.assertEqual(kpi_before, (1, 1, 1, 1))
        self.assertEqual(kpi_after_correction, kpi_before)
        self.assertEqual(kpi_after_reversal, kpi_before)
        self.assertEqual(len(source_rows), 2)
        self.assertEqual(
            {row.canonical_identity_key for row in source_rows},
            {"pending:parser-reversal"},
        )
        self.assertEqual(
            source_author.canonical_identity_key, "pending:parser-reversal"
        )
        self.assertEqual(self.db.get(PersonRole, role.id).canonical_name, "Nombre Parser")
        self.assertEqual(list(self.db.dirty), [])

        valid_reversal_payload = deepcopy(reversal.payload)
        self_referential_payload = deepcopy(valid_reversal_payload)
        self_referential_payload["restore_decision_id"] = str(reversal.id)
        self_referential_payload["projection_after"][
            "current_decision_id"
        ] = str(reversal.id)
        reversal.payload = self_referential_payload
        self.db.flush()
        self.assertIsNone(
            EffectiveHumanProjectionSource(self.db).for_record(
                "person_roles", role.id
            )
        )

        reversal.payload = valid_reversal_payload
        valid_first_payload = deepcopy(first.payload)
        impossible_snapshot = deepcopy(
            valid_first_payload["projection_after"]
        )
        first.decision_type = "reverted"
        first.payload = {
            "kind": "decision_reversal",
            "schema_version": 1,
            "decision_id_to_revert": str(second.id),
            "restore_decision_id": str(second.id),
            "projection_before": deepcopy(impossible_snapshot),
            "projection_after": deepcopy(impossible_snapshot),
        }
        second.payload = {
            "kind": "identity",
            "schema_version": 1,
            "canonical_identity_key": first_identity[
                "canonical_identity_key"
            ],
            "canonical_name": first_identity["canonical_name"],
            "identity_type": first_identity["identity_type"],
            "alias_original": None,
            "alias_normalized": None,
            "projection_before": deepcopy(impossible_snapshot),
            "projection_after": deepcopy(impossible_snapshot),
        }
        self.db.flush()
        self.assertIsNone(
            EffectiveHumanProjectionSource(self.db).for_record(
                "person_roles", role.id
            )
        )

        first.payload = valid_first_payload
        first.decision_type = "discarded"
        self.db.flush()
        self.assertIsNone(
            EffectiveHumanProjectionSource(self.db).for_record(
                "person_roles", role.id
            )
        )

    def test_possible_duplicate_cannot_project_an_external_researcher(self) -> None:
        external = ExternalResearcher(
            period_id=self.period.id,
            import_job_id=self.job.id,
            full_name="Externa fuera de matriz",
            normalized_name="EXTERNA FUERA DE MATRIZ",
            institution="InstituciÃ³n fuente",
            normalized_institution="INSTITUCION FUENTE",
            requires_review=True,
        )
        self.db.add(external)
        self.db.flush()
        stable = _stable("possible_duplicate", "9")
        self._activate_projection(
            case_type="possible_duplicate",
            target_table="external_researchers",
            target_pk=external.id,
            marker="9",
            decision_type="discarded",
            scientific_status="discarded",
            overrides=[
                _override(
                    stable_target_key=stable,
                    target_table="external_researchers",
                    target_pk=external.id,
                    field_path="scientific_status",
                    value="discarded",
                )
            ],
        )

        self.assertIsNone(
            EffectiveHumanProjectionSource(self.db).for_record(
                "external_researchers", external.id
            )
        )

    def test_crossed_current_pointer_or_materialization_fails_closed(self) -> None:
        roles = []
        projections = []
        for marker in ("a", "b"):
            role = PersonRole(
                period_id=self.period.id,
                import_job_id=self.job.id,
                teacher_id=self.teacher.id,
                role_type=f"crossed_{marker}",
                person_type="teacher",
                canonical_identity_key=f"pending:crossed-{marker}",
                canonical_name=f"Parser {marker}",
                normalized_name=f"Parser {marker}",
                validation_status="pending_review",
            )
            self.db.add(role)
            self.db.flush()
            stable = _stable("person_identity", marker)
            projections.append(self._activate_projection(
                case_type="person_identity",
                target_table="person_roles",
                target_pk=role.id,
                marker=marker,
                decision_type="corrected",
                overrides=[
                    _override(
                        stable_target_key=stable,
                        target_table="person_roles",
                        target_pk=role.id,
                        field_path="canonical_name",
                        value=f"Humano {marker}",
                        scope="global_identity",
                    ),
                    _override(
                        stable_target_key=stable,
                        target_table="person_roles",
                        target_pk=role.id,
                        field_path="scientific_status",
                        value="validated",
                    ),
                ],
                identity={
                    "schema_version": 1,
                    "canonical_identity_key": f"human:crossed-{marker}",
                    "canonical_name": f"Humano {marker}",
                    "identity_type": "internal_person",
                    "aliases": [],
                },
            ))
            roles.append(role)
        first_item, first_decision, first_rows = projections[0]
        _, second_decision, _ = projections[1]

        self.assertIsNotNone(
            EffectiveHumanProjectionSource(self.db).for_record("person_roles", roles[0].id)
        )

        first_item.current_decision_id = second_decision.id
        self.db.flush()
        self.assertIsNone(
            EffectiveHumanProjectionSource(self.db).for_record("person_roles", roles[0].id)
        )

        first_item.current_decision_id = first_decision.id
        first_rows[0].target_pk = roles[1].id
        self.db.flush()
        self.assertIsNone(
            EffectiveHumanProjectionSource(self.db).for_record("person_roles", roles[0].id)
        )
        self.assertEqual(roles[0].canonical_name, "Parser a")

    def test_relation_link_without_identity_key_resolves_relationship(self) -> None:
        entity = ResearchEntity(
            period_id=self.period.id,
            import_job_id=self.job.id,
            type="proyecto_fci",
            name="Proyecto sin director ligado",
            director_name="Director parser",
            normalized_director_name="Director parser",
            validation_status="pending_review",
        )
        self.db.add(entity)
        self.db.flush()
        stable = _stable("project_director_relation", "a")
        self._activate_projection(
            case_type="project_director_relation",
            target_table="research_entities",
            target_pk=entity.id,
            marker="a",
            decision_type="linked",
            relationship_key=f"entity:{entity.id}:director",
            overrides=[
                _override(
                    stable_target_key=stable,
                    target_table="research_entities",
                    target_pk=entity.id,
                    field_path="project_director_relationship_status",
                    value="linked",
                    scope="relationship",
                    relationship_key=f"entity:{entity.id}:director",
                ),
                _override(
                    stable_target_key=stable,
                    target_table="research_entities",
                    target_pk=entity.id,
                    field_path="scientific_status",
                    value="validated",
                ),
            ],
        )

        state = ValidatedReadService(self.db).project_director_state(entity)

        self.assertEqual(state["project_validation_status"], "validated")
        self.assertEqual(state["director_relationship_validation_status"], "validated")
        self.assertNotEqual(state["director_relationship_validation_status"], "pending_review")
        self.assertIsNone(state["director_canonical_identity_key"])

    def test_external_identity_projection_reaches_external_production_author(self) -> None:
        external = ExternalResearcher(
            period_id=self.period.id,
            import_job_id=self.job.id,
            full_name="Autora externa parser",
            normalized_name="AUTORA EXTERNA PARSER",
            institution="InstituciÃ³n parser",
            normalized_institution="INSTITUCION PARSER",
            requires_review=True,
        )
        product = ScientificProduction(
            teacher_id=self.teacher.id,
            period_id=self.period.id,
            production_type=ProductionType.ARTICLE,
            title="Producto externo",
            status="published",
            import_job_id=self.job.id,
            validation_status="validated",
        )
        self.db.add_all([external, product])
        self.db.flush()
        author = ScientificProductionAuthor(
            production_id=product.id,
            external_researcher_id=external.id,
            author_order=1,
            normalized_author_name="Autora externa parser",
            canonical_identity_key="pending:external-author",
            author_type="external",
            validation_status="pending_review",
        )
        self.db.add(author)
        self.db.flush()
        stable = _stable("external_identity", "b")
        self._activate_projection(
            case_type="external_identity",
            target_table="external_researchers",
            target_pk=external.id,
            marker="b",
            decision_type="linked",
            overrides=[
                _override(stable_target_key=stable, target_table="external_researchers", target_pk=external.id, field_path="external_identity_key", value="human:external:author"),
                _override(stable_target_key=stable, target_table="external_researchers", target_pk=external.id, field_path="external_institution", value="InstituciÃ³n humana"),
                _override(stable_target_key=stable, target_table="external_researchers", target_pk=external.id, field_path="scientific_status", value="validated"),
            ],
            identity={
                "schema_version": 1,
                "canonical_identity_key": "human:external:author",
                "canonical_name": "Autora externa humana",
                "identity_type": "external_person",
                "aliases": [],
            },
        )

        view = ValidatedReadService(self.db).production_views(self.period.id, visibility="all")[0]

        self.assertEqual(view["authors"][0]["canonical_identity_key"], "human:external:author")
        self.assertEqual(view["authors"][0]["canonical_name"], "Autora externa humana")
        self.assertEqual(view["authors"][0]["validation_status"], "validated")
        self.assertTrue(view["kpi_eligible"])

    def test_invalid_stable_prefix_semantic_status_and_payload_fail_closed(self) -> None:
        role = PersonRole(
            period_id=self.period.id,
            import_job_id=self.job.id,
            teacher_id=self.teacher.id,
            role_type="fail_closed_semantics",
            person_type="teacher",
            canonical_identity_key="pending:semantic",
            canonical_name="Parser semantic",
            normalized_name="Parser semantic",
            validation_status="pending_review",
        )
        self.db.add(role)
        self.db.flush()
        stable = _stable("person_identity", "c")
        item, decision, rows = self._activate_projection(
            case_type="person_identity",
            target_table="person_roles",
            target_pk=role.id,
            marker="c",
            decision_type="corrected",
            overrides=[
                _override(stable_target_key=stable, target_table="person_roles", target_pk=role.id, field_path="canonical_identity_key", value="human:semantic", scope="global_identity"),
                _override(stable_target_key=stable, target_table="person_roles", target_pk=role.id, field_path="canonical_name", value="Humano semantic", scope="global_identity"),
                _override(stable_target_key=stable, target_table="person_roles", target_pk=role.id, field_path="scientific_status", value="validated"),
            ],
            identity={"schema_version": 1, "canonical_identity_key": "human:semantic", "canonical_name": "Humano semantic", "identity_type": "internal_person", "aliases": []},
        )
        source = EffectiveHumanProjectionSource(self.db)
        self.assertIsNotNone(source.for_record("person_roles", role.id))

        item.stable_target_key = _stable("author_identity", "c")
        self.db.flush()
        self.assertIsNone(source.for_record("person_roles", role.id))
        item.stable_target_key = stable
        next(row for row in rows if row.field_path == "scientific_status").projected_value = _scalar("discarded")
        self.db.flush()
        self.assertIsNone(source.for_record("person_roles", role.id))
        next(row for row in rows if row.field_path == "scientific_status").projected_value = _scalar("validated")
        decision.decision_type = "discarded"
        self.db.flush()
        self.assertIsNone(source.for_record("person_roles", role.id))
        self.assertEqual(role.canonical_name, "Parser semantic")

    def test_reading_a_projection_does_not_autoflush_a_dirty_scientific_source(self) -> None:
        product = ScientificProduction(
            teacher_id=self.teacher.id,
            period_id=self.period.id,
            production_type=ProductionType.ARTICLE,
            title="TÃ­tulo parser pendiente",
            normalized_title="TÃ­tulo parser pendiente",
            status="published",
            import_job_id=self.job.id,
            validation_status="pending_review",
        )
        self.db.add(product)
        self.db.flush()
        stable = _stable("product", "d")
        self._activate_projection(
            case_type="product",
            target_table="scientific_productions",
            target_pk=product.id,
            marker="d",
            decision_type="corrected",
            overrides=[
                _override(stable_target_key=stable, target_table="scientific_productions", target_pk=product.id, field_path="product_title", value="TÃ­tulo humano"),
                _override(stable_target_key=stable, target_table="scientific_productions", target_pk=product.id, field_path="scientific_status", value="validated"),
            ],
        )
        product.title = "Cambio sucio que no debe persistirse"

        service = ValidatedReadService(self.db)
        title = service.effective_product_title(product)

        self.assertEqual(title, "TÃ­tulo humano")
        self.assertIn(product, self.db.dirty)
        service.productions_for_period(self.period.id, validated_only=False)
        self.assertIn(product, self.db.dirty)
        self.db.expire(product, ["authors"])
        service.production_is_validated(product)
        self.assertIn(product, self.db.dirty)

    def test_teacher_progress_and_entity_views_use_effective_values_without_duplicate_participants(self) -> None:
        entity = ResearchEntity(
            period_id=self.period.id,
            import_job_id=self.job.id,
            type="proyecto_fci",
            name="Proyecto efectivo",
            director_name="Director parser",
            normalized_director_name="Director parser",
            validation_status="pending_review",
        )
        product = ScientificProduction(
            teacher_id=self.teacher.id,
            period_id=self.period.id,
            production_type=ProductionType.ARTICLE,
            title="Producto parser",
            normalized_title="Producto parser",
            status="published",
            import_job_id=self.job.id,
            validation_status="pending_review",
        )
        self.db.add_all([entity, product])
        self.db.flush()
        author = ScientificProductionAuthor(
            production_id=product.id,
            author_order=1,
            normalized_author_name="Autora validada",
            canonical_identity_key="human:author:effective",
            author_type="internal",
            validation_status="validated",
        )
        director = PersonRole(
            period_id=self.period.id,
            import_job_id=self.job.id,
            teacher_id=self.teacher.id,
            research_entity_id=entity.id,
            role_type="director",
            person_type="teacher",
            canonical_identity_key="pending:director-effective",
            canonical_name="Director parser",
            normalized_name="Director parser",
            validation_status="pending_review",
        )
        duplicate = PersonRole(
            period_id=self.period.id,
            import_job_id=self.job.id,
            teacher_id=self.teacher.id,
            research_entity_id=entity.id,
            role_type="integrante_interno",
            person_type="teacher",
            canonical_identity_key="human:director:effective",
            canonical_name="Directora humana",
            normalized_name="Directora humana",
            validation_status="validated",
        )
        report = ImportedProgressReport(
            import_job_id=self.job.id,
            career_name=self.career.name,
            year_label=self.period.year_label,
            cycle=self.period.cycle,
            teacher_name=self.teacher.full_name,
        )
        self.db.add_all([author, director, duplicate, report])
        self.db.flush()

        identity_stable = _stable("person_identity", "e")
        self._activate_projection(
            case_type="person_identity",
            target_table="person_roles",
            target_pk=director.id,
            marker="e",
            decision_type="corrected",
            overrides=[
                _override(stable_target_key=identity_stable, target_table="person_roles", target_pk=director.id, field_path="canonical_identity_key", value="human:director:effective", scope="global_identity"),
                _override(stable_target_key=identity_stable, target_table="person_roles", target_pk=director.id, field_path="canonical_name", value="Directora humana", scope="global_identity"),
                _override(stable_target_key=identity_stable, target_table="person_roles", target_pk=director.id, field_path="scientific_status", value="validated"),
            ],
            identity={"schema_version": 1, "canonical_identity_key": "human:director:effective", "canonical_name": "Directora humana", "identity_type": "internal_person", "aliases": []},
        )
        product_stable = _stable("product", "f")
        self._activate_projection(
            case_type="product",
            target_table="scientific_productions",
            target_pk=product.id,
            marker="f",
            decision_type="corrected",
            overrides=[
                _override(stable_target_key=product_stable, target_table="scientific_productions", target_pk=product.id, field_path="product_title", value="Producto humano"),
                _override(stable_target_key=product_stable, target_table="scientific_productions", target_pk=product.id, field_path="scientific_status", value="validated"),
            ],
        )
        relation_stable = _stable("project_director_relation", "a")
        self._activate_projection(
            case_type="project_director_relation",
            target_table="research_entities",
            target_pk=entity.id,
            marker="a",
            decision_type="linked",
            relationship_key=f"entity:{entity.id}:director",
            overrides=[
                _override(stable_target_key=relation_stable, target_table="research_entities", target_pk=entity.id, field_path="project_director_relationship_status", value="linked", scope="relationship", relationship_key=f"entity:{entity.id}:director"),
                _override(stable_target_key=relation_stable, target_table="research_entities", target_pk=entity.id, field_path="scientific_status", value="validated"),
            ],
        )

        service = ValidatedReadService(self.db)
        teacher = service.teacher_views(self.career.id)[0]
        progress = service.progress_view(report, None)
        entity_view = service.entity_views(self.period.id)[0]

        self.assertEqual(teacher["full_name"], "Directora humana")
        self.assertEqual(teacher["productions"][0]["title"], "Producto humano")
        self.assertEqual(progress["scientific_products"][0]["title"], "Producto humano")
        self.assertEqual(progress["research_entities"][0]["director"], "Directora humana")
        self.assertEqual(entity_view["director_name"], "Directora humana")
        self.assertEqual(len(progress["normalized_participants"]), 1)
        self.assertEqual(progress["normalized_participants"][0]["canonical_name"], "Directora humana")


if __name__ == "__main__":
    unittest.main()
