import inspect
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.entities import (
    AcademicPeriod,
    Career,
    Faculty,
    ImportJob,
    ImportNormalizationAudit,
    ImportedOcrTrace,
    ImportedProgressReport,
    PersonRole,
    ResearchEntity,
    ScientificProduction,
    ScientificProductionAuthor,
    Teacher,
)
from app.models.enums import GoalMetric, ProductionType
from app.services.import_service import ImportService
from app.services.kpi_service import KpiService
from app.services.validated_read_service import ValidatedReadService
from app.schemas.research_entities import ResearchEntityRead
from tests.support.sqlite import create_sqlite_compatible_schema


class ValidatedModelContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite+pysqlite:///:memory:")
        create_sqlite_compatible_schema(cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        self.db = Session(self.engine)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_teacher_and_person_role_have_validation_status(self):
        self.assertTrue(hasattr(Teacher, "validation_status"))
        self.assertTrue(hasattr(PersonRole, "validation_status"))

    def test_record_person_role_accepts_explicit_validation_status(self):
        parameters = inspect.signature(ImportService._record_person_role).parameters

        self.assertIn("validation_status", parameters)

    def test_record_person_role_derives_validated_and_pending_states(self):
        service = ImportService(self.db)
        context = {
            "import_batch_id": None,
            "import_job_id": 101,
            "source_file": "informe.pdf",
            "parser_version": "test",
        }
        service._record_person_role(
            context,
            role_type="integrante_interno",
            person_type="teacher",
            raw_name="Fernando Zambrano Farias",
            normalized_name="Fernando Jose Zambrano Farias",
            reason="Coincidencia confiable.",
            source_section="integrantes_internos",
            confidence_score=0.95,
            period_id=1,
            teacher_id=10,
        )
        service._record_person_role(
            context,
            role_type="responsable_informe",
            person_type="unresolved",
            raw_name="Fernando Zambrano",
            normalized_name="Fernando Zambrano",
            reason="Coincidencia insuficiente.",
            source_section="director_responsable",
            confidence_score=0.55,
            period_id=1,
        )
        self.db.flush()

        roles = self.db.query(PersonRole).order_by(PersonRole.id).all()
        self.assertEqual(roles[0].validation_status, "validated")
        self.assertEqual(roles[1].validation_status, "pending_review")

    def test_existing_person_role_is_promoted_after_validated_reprocessing(self):
        service = ImportService(self.db)
        context = {
            "import_batch_id": None,
            "import_job_id": 202,
            "source_file": "reprocesado.pdf",
            "parser_version": "test",
        }
        service._record_person_role(
            context,
            role_type="integrante_interno",
            person_type="unresolved",
            raw_name="Fernando Zambrano Farias",
            normalized_name="Fernando Jose Zambrano Farias",
            reason="Pendiente inicial.",
            source_section="integrantes_internos",
            confidence_score=0.55,
            period_id=1,
        )
        self.db.flush()
        service._record_person_role(
            context,
            role_type="integrante_interno",
            person_type="teacher",
            raw_name="Fernando Zambrano Farias",
            normalized_name="Fernando Jose Zambrano Farias",
            reason="Validado al reprocesar.",
            source_section="integrantes_internos",
            confidence_score=0.98,
            period_id=1,
            teacher_id=77,
        )
        self.db.flush()

        role = self.db.query(PersonRole).one()
        self.assertEqual(role.validation_status, "validated")
        self.assertEqual(role.teacher_id, 77)
        self.assertEqual(role.person_type, "teacher")

    def test_existing_research_entity_is_promoted_after_validated_reprocessing(self):
        period = AcademicPeriod(year_label="2040-2041", cycle=1)
        self.db.add(period)
        self.db.flush()
        entity = ResearchEntity(
            period_id=period.id,
            type="grupo_investigacion",
            code="GI-RE",
            normalized_code="GI-RE",
            name="Grupo reprocesado",
            normalized_name="GRUPO REPROCESADO",
            validation_status="pending_review",
        )
        self.db.add(entity)
        self.db.flush()

        ImportService(self.db)._persist_research_entities(
            [
                {
                    "type": "grupo_investigacion",
                    "code": "GI-RE",
                    "name": "Grupo reprocesado",
                    "director": "Fernando Jose Zambrano Farias",
                    "validation_status": "validated",
                }
            ],
            period.id,
            None,
            {"import_batch_id": None, "import_job_id": None, "source_file": "reprocesado.pdf", "parser_version": "test"},
        )
        self.db.flush()

        self.assertEqual(entity.validation_status, "validated")

    def test_merged_product_records_author_role_for_current_document_without_duplicating_author(self):
        period = AcademicPeriod(year_label="2042-2043", cycle=1)
        first_job = ImportJob(source_type="PROGRESS_PDF", filename="first.pdf", status="SUCCESS")
        second_job = ImportJob(source_type="PROGRESS_PDF", filename="zambrano.pdf", status="PROCESSING")
        self.db.add_all([period, first_job, second_job])
        self.db.flush()
        production = ScientificProduction(
            period_id=period.id,
            production_type=ProductionType.ARTICLE,
            title="Producto compartido",
            status="published",
            import_job_id=first_job.id,
            validation_status="pending_review",
        )
        self.db.add(production)
        self.db.flush()
        self.db.add(
            ScientificProductionAuthor(
                production_id=production.id,
                author_order=1,
                normalized_author_name="Autor Canonico Existente",
                author_type="unresolved",
                validation_status="pending_author_resolution",
                import_job_id=first_job.id,
            )
        )
        self.db.flush()

        ImportService(self.db)._persist_production_authors(
            production,
            [
                {
                    "author_order": 1,
                    "raw_author_name": "Jeniffer Marcillo Chasy",
                    "normalized_author_name": "Jeniffer Marcillo Chasy",
                    "author_type": "unresolved",
                    "confidence_score": 0.45,
                    "validation_status": "pending_author_resolution",
                    "reason": "Autor pendiente en evidencia reprocesada.",
                }
            ],
            {
                "import_batch_id": None,
                "import_job_id": second_job.id,
                "source_file": "zambrano.pdf",
                "parser_version": "test",
            },
            source_page=7,
            source_section="produccion_cientifica",
            source_field="authors",
            row_or_block_id="produccion_cientifica:4",
            only_if_missing=True,
        )
        self.db.flush()

        self.assertEqual(
            self.db.query(ScientificProductionAuthor).filter_by(production_id=production.id).count(),
            1,
        )
        role = self.db.query(PersonRole).filter_by(import_job_id=second_job.id).one()
        self.assertEqual(role.normalized_name, "Jeniffer Marcillo Chasy")
        self.assertEqual(role.validation_status, "pending_author_resolution")
        self.assertEqual(role.scientific_production_id, production.id)

    def test_progress_view_uses_only_validated_relational_rows(self):
        try:
            from app.services.validated_read_service import ValidatedReadService
        except ModuleNotFoundError:
            self.fail("ValidatedReadService is required")

        faculty = Faculty(name="CIENCIAS ADMINISTRATIVAS")
        career = Career(name="Licenciatura en Administracion de Empresas", code="ADM", faculty=faculty)
        period = AcademicPeriod(year_label="2025-2026", cycle=1)
        job = ImportJob(source_type="PROGRESS_PDF", filename="zambrano.pdf", status="SUCCESS", is_current=True)
        self.db.add_all([faculty, career, period, job])
        self.db.flush()
        progress = ImportedProgressReport(
            import_job_id=job.id,
            career_name=career.name,
            year_label=period.year_label,
            cycle=period.cycle,
            teacher_name="Fernando Jose Zambrano Farias",
        )
        teacher = Teacher(
            career_id=career.id,
            full_name="Fernando Jose Zambrano Farias",
            institutional_email="fernando@example.test",
            validation_status="validated",
        )
        pending_teacher = Teacher(
            career_id=career.id,
            full_name="Persona Pendiente Paredes Ruiz",
            institutional_email="pending@example.test",
            validation_status="pending_review",
        )
        self.db.add_all([progress, teacher, pending_teacher])
        self.db.flush()
        trace = ImportedOcrTrace(
            import_job_id=job.id,
            progress_report_id=progress.id,
            source_filename="zambrano.pdf",
            parsed_payload={
                "integrantes_internos": [{"name": "Persona Falsa Desde Json"}],
                "produccion_cientifica": [{"title": "Producto falso desde JSON"}],
                "research_entities": [{"name": "Entidad falsa desde JSON"}],
            },
            review_status="VALIDADO_AUTOMATICO",
        )
        valid_role = PersonRole(
            period_id=period.id,
            import_job_id=job.id,
            teacher_id=teacher.id,
            role_type="integrante_interno",
            person_type="teacher",
            person_key="FERNANDO JOSE ZAMBRANO FARIAS",
            normalized_name=teacher.full_name,
            confidence_score=0.98,
            validation_status="validated",
        )
        pending_role = PersonRole(
            period_id=period.id,
            import_job_id=job.id,
            teacher_id=pending_teacher.id,
            role_type="integrante_interno",
            person_type="teacher",
            person_key="PERSONA PENDIENTE PAREDES RUIZ",
            normalized_name=pending_teacher.full_name,
            confidence_score=0.60,
            validation_status="pending_review",
        )
        pending_author_role = PersonRole(
            period_id=period.id,
            import_job_id=job.id,
            role_type="autor_producto",
            person_type="unresolved",
            person_key="JENIFFER MARCILLO CHASY",
            normalized_name="Jeniffer Marcillo Chasy",
            confidence_score=0.45,
            validation_status="pending_author_resolution",
        )
        discarded_role = PersonRole(
            period_id=period.id,
            import_job_id=job.id,
            role_type="autor_producto",
            person_type="unresolved",
            person_key="EN LAS",
            normalized_name="en las",
            confidence_score=0.10,
            validation_status="discarded_invalid",
        )
        valid_entity = ResearchEntity(
            period_id=period.id,
            import_job_id=job.id,
            type="proyecto_fci",
            code="GI-001",
            normalized_code="GI001",
            name="Grupo validado",
            normalized_name="GRUPO VALIDADO",
            director_name=teacher.full_name,
            validation_status="validated",
        )
        pending_entity = ResearchEntity(
            period_id=period.id,
            import_job_id=job.id,
            type="semillero",
            code="SI-999",
            normalized_code="SI999",
            name="Entidad pendiente",
            normalized_name="ENTIDAD PENDIENTE",
            validation_status="pending_review",
        )
        self.db.add_all(
            [trace, valid_role, pending_role, pending_author_role, discarded_role, valid_entity, pending_entity]
        )
        self.db.flush()
        production = ScientificProduction(
            teacher_id=teacher.id,
            period_id=period.id,
            research_entity_id=valid_entity.id,
            production_type=ProductionType.ARTICLE,
            title="Producto relacional validado",
            status="published",
            import_job_id=job.id,
            validation_status="validated",
        )
        pending_production = ScientificProduction(
            teacher_id=pending_teacher.id,
            period_id=period.id,
            production_type=ProductionType.BOOK,
            title="Producto relacional pendiente",
            status="in_review",
            import_job_id=job.id,
            validation_status="pending_review",
        )
        self.db.add_all([production, pending_production])
        self.db.flush()
        self.db.add_all(
            [
                ScientificProductionAuthor(
                    production_id=production.id,
                    author_order=1,
                    normalized_author_name=teacher.full_name,
                    teacher_id=teacher.id,
                    author_type="internal",
                    validation_status="validated",
                ),
                ScientificProductionAuthor(
                    production_id=production.id,
                    author_order=2,
                    normalized_author_name="Autor pendiente",
                    author_type="unresolved",
                    validation_status="pending_author_resolution",
                ),
                ScientificProductionAuthor(
                    production_id=pending_production.id,
                    author_order=1,
                    normalized_author_name="Autor pendiente del producto",
                    author_type="unresolved",
                    validation_status="pending_author_resolution",
                ),
            ]
        )
        self.db.flush()

        view = ValidatedReadService(self.db).progress_view(progress, trace)

        self.assertEqual([item["name"] for item in view["group_members"]], [teacher.full_name])
        self.assertEqual([item["name"] for item in view["research_entities"]], ["Grupo validado"])
        self.assertEqual(
            [item["title"] for item in view["scientific_products"]],
            ["Producto relacional validado", "Producto relacional pendiente"],
        )
        self.assertEqual(
            view["scientific_products"][0]["authors"],
            [teacher.full_name, "Autor pendiente"],
        )
        self.assertTrue(view["scientific_products"][0]["kpi_eligible"])
        self.assertFalse(view["scientific_products"][1]["kpi_eligible"])
        participants = {item["canonical_name"]: item for item in view["normalized_participants"]}
        self.assertEqual(set(participants), {teacher.full_name, pending_teacher.full_name, "Jeniffer Marcillo Chasy"})
        self.assertEqual(participants[teacher.full_name]["validation_status"], "validated")
        self.assertEqual(participants[teacher.full_name]["review_bucket"], "valid_person")
        self.assertTrue(participants[teacher.full_name]["kpi_eligible"])
        self.assertEqual(participants[pending_teacher.full_name]["validation_status"], "pending_review")
        self.assertEqual(participants[pending_teacher.full_name]["review_bucket"], "pending_person")
        self.assertTrue(participants[pending_teacher.full_name]["show_in_participants"])
        self.assertFalse(participants[pending_teacher.full_name]["kpi_eligible"])
        self.assertEqual(participants["Jeniffer Marcillo Chasy"]["review_bucket"], "pending_author_classification")
        self.assertFalse(participants["Jeniffer Marcillo Chasy"]["kpi_eligible"])
        self.assertNotIn("en las", participants)
        self.assertEqual(view["participants_summary"]["pending_people_count"], 1)
        self.assertEqual(view["participants_summary"]["pending_author_classification_count"], 1)
        self.assertEqual(view["participants_summary"]["kpi_eligible"], 1)
        self.assertNotIn("Persona Falsa Desde Json", str(view))
        self.assertNotIn("Producto falso desde JSON", str(view))
        self.assertNotIn("Entidad falsa desde JSON", str(view))

        service = ValidatedReadService(self.db)
        self.assertEqual([row.id for row in service.participant_roles()], [valid_role.id])
        self.assertEqual([row.id for row in service.production_list(period_id=period.id)], [production.id])
        self.assertEqual([row.id for row in service.entity_list(period_id=period.id)], [valid_entity.id])
        teacher_views = service.teacher_views(career_id=career.id)
        self.assertEqual([row["id"] for row in teacher_views], [teacher.id])
        self.assertEqual([row["id"] for row in teacher_views[0]["productions"]], [production.id])
        goal_totals = service.goal_totals(period.id, [career.id])
        self.assertEqual(goal_totals[GoalMetric.TEACHERS_IN_RESEARCH], 1)
        self.assertEqual(goal_totals[GoalMetric.ARTICLES], 1)
        self.assertEqual(goal_totals[GoalMetric.SCIENTIFIC_OUTPUT], 1)
        self.assertEqual(service.goal_totals(period.id, None)[GoalMetric.PROJECTS], 1)

    def test_progress_products_follow_relational_audit_for_merged_document_evidence(self):
        period = AcademicPeriod(year_label="2044-2045", cycle=1)
        canonical_job = ImportJob(source_type="PROGRESS_PDF", filename="canonical.pdf", status="SUCCESS", is_current=False)
        evidence_job = ImportJob(source_type="PROGRESS_PDF", filename="zambrano.pdf", status="SUCCESS", is_current=True)
        self.db.add_all([period, canonical_job, evidence_job])
        self.db.flush()
        product = ScientificProduction(
            period_id=period.id,
            production_type=ProductionType.ARTICLE,
            title="Producto canónico compartido",
            status="published",
            import_job_id=canonical_job.id,
            source_section="produccion_cientifica",
            validation_status="pending_review",
        )
        self.db.add(product)
        self.db.flush()
        self.db.add_all(
            [
                ScientificProductionAuthor(
                    production_id=product.id,
                    author_order=1,
                    normalized_author_name="Jeniffer Marcillo Chasy",
                    author_type="unresolved",
                    validation_status="pending_author_resolution",
                ),
                ImportNormalizationAudit(
                    import_job_id=evidence_job.id,
                    normalized_record_id=product.id,
                    entity_type="scientific_production",
                    source_section="produccion_cientifica",
                    action="merged_duplicate",
                ),
            ]
        )
        self.db.flush()

        products = ValidatedReadService(self.db).productions_for_jobs(
            [evidence_job.id],
            validated_only=False,
        )

        self.assertEqual([item.id for item in products], [product.id])

    def test_reused_master_records_are_visible_only_through_current_evidence(self):
        faculty = Faculty(name="FACULTAD EVIDENCIA")
        career = Career(name="Carrera Evidencia", code="EVI", faculty=faculty)
        period = AcademicPeriod(year_label="2046-2047", cycle=1)
        historical_job = ImportJob(
            source_type="PROGRESS_PDF",
            filename="evidence.pdf",
            status="SUCCESS",
            document_key="dropbox:id:evidence",
            source_rev="old",
            is_current=False,
        )
        current_job = ImportJob(
            source_type="PROGRESS_PDF",
            filename="evidence.pdf",
            status="SUCCESS",
            document_key="dropbox:id:evidence",
            source_rev="current",
            is_current=True,
        )
        self.db.add_all([faculty, career, period, historical_job, current_job])
        self.db.flush()
        teacher = Teacher(
            career_id=career.id,
            import_job_id=historical_job.id,
            full_name="Docente Maestro Reutilizado",
            institutional_email="reused@example.test",
            validation_status="validated",
        )
        entity = ResearchEntity(
            period_id=period.id,
            import_job_id=historical_job.id,
            type="proyecto_fci",
            name="Entidad maestra reutilizada",
            validation_status="validated",
        )
        self.db.add_all([teacher, entity])
        self.db.flush()
        role = PersonRole(
            period_id=period.id,
            import_job_id=current_job.id,
            teacher_id=teacher.id,
            role_type="integrante_interno",
            person_type="teacher",
            person_key="DOCENTE MAESTRO REUTILIZADO",
            normalized_name=teacher.full_name,
            validation_status="validated",
        )
        audit = ImportNormalizationAudit(
            import_job_id=current_job.id,
            normalized_record_id=entity.id,
            entity_type="research_entity",
            action="merged_duplicate",
        )
        self.db.add_all([role, audit])
        self.db.flush()

        service = ValidatedReadService(self.db)

        self.assertEqual([row["id"] for row in service.teacher_views(career.id)], [teacher.id])
        self.assertEqual([row.id for row in service.entities_for_period(period.id)], [entity.id])
        self.assertEqual([row.id for row in service.entity_list(period_id=period.id)], [entity.id])

    def test_dashboard_counts_validated_relational_rows_not_payload(self):
        faculty = Faculty(name="FACULTAD TEST")
        career = Career(name="Licenciatura en Carrera Test", code="TST", faculty=faculty)
        period = AcademicPeriod(year_label="2030-2031", cycle=1)
        job = ImportJob(source_type="PROGRESS_PDF", filename="dashboard.pdf", status="SUCCESS", is_current=True)
        self.db.add_all([faculty, career, period, job])
        self.db.flush()
        progress = ImportedProgressReport(
            import_job_id=job.id,
            career_name=career.name,
            year_label=period.year_label,
            cycle=period.cycle,
            teacher_name="Ana Maria Paredes Ruiz",
        )
        teacher = Teacher(
            career_id=career.id,
            full_name="Ana Maria Paredes Ruiz",
            institutional_email="dashboard@example.test",
            validation_status="validated",
        )
        self.db.add_all([progress, teacher])
        self.db.flush()
        trace = ImportedOcrTrace(
            import_job_id=job.id,
            progress_report_id=progress.id,
            source_filename="dashboard.pdf",
            parsed_payload={
                "integrantes_internos": [
                    {"name": "Lucia Elena Paredes Rojas", "career": career.name},
                    {"name": "Maria Elena Suarez Mena", "career": career.name},
                ],
                "produccion_cientifica": [
                    {"title": "Producto falso numero uno para dashboard", "authors": ["Lucia Elena Paredes Rojas"], "status": "PUBLICADO", "validation_status": "validado"},
                    {"title": "Producto falso numero dos para dashboard", "authors": ["Maria Elena Suarez Mena"], "status": "PUBLICADO", "validation_status": "validado"},
                ],
                "research_entities": [
                    {"type": "grupo_investigacion", "code": "FAKE1", "name": "Entidad falsa uno", "validation_status": "validated", "kpi_eligible": True},
                    {"type": "grupo_investigacion", "code": "FAKE2", "name": "Entidad falsa dos", "validation_status": "validated", "kpi_eligible": True},
                ],
            },
            review_status="VALIDADO_AUTOMATICO",
        )
        role = PersonRole(
            period_id=period.id,
            import_job_id=job.id,
            teacher_id=teacher.id,
            role_type="integrante_interno",
            person_type="teacher",
            person_key="ANA MARIA PAREDES RUIZ",
            normalized_name=teacher.full_name,
            confidence_score=0.98,
            validation_status="validated",
        )
        entity = ResearchEntity(
            period_id=period.id,
            import_job_id=job.id,
            type="grupo_investigacion",
            code="GI-TST",
            normalized_code="GITST",
            name="Entidad relacional validada",
            normalized_name="ENTIDAD RELACIONAL VALIDADA",
            director_name=teacher.full_name,
            validation_status="validated",
        )
        self.db.add_all([trace, role, entity])
        self.db.flush()
        production = ScientificProduction(
            teacher_id=teacher.id,
            period_id=period.id,
            research_entity_id=entity.id,
            production_type=ProductionType.ARTICLE,
            title="Producto relacional del dashboard",
            status="published",
            import_job_id=job.id,
            validation_status="validated",
        )
        self.db.add(production)
        self.db.flush()
        self.db.add(
            ScientificProductionAuthor(
                production_id=production.id,
                author_order=1,
                normalized_author_name=teacher.full_name,
                teacher_id=teacher.id,
                author_type="internal",
                validation_status="validated",
            )
        )
        self.db.flush()

        dashboard = KpiService(self.db).dashboard(period.year_label, period.cycle)

        self.assertEqual(dashboard.total_teachers, 1)
        self.assertEqual(dashboard.scientific_output_total, 1)
        self.assertEqual(dashboard.research_entities_kpi_eligible, 1)

    def test_all_operational_readers_exclude_historical_job_rows(self):
        faculty = Faculty(name="FACULTAD VERSIONES")
        career = Career(name="Carrera Versiones", code="VER", faculty=faculty)
        period = AcademicPeriod(year_label="2048-2049", cycle=1)
        historical_job = ImportJob(
            source_type="PROGRESS_PDF",
            filename="version.pdf",
            status="SUCCESS",
            document_key="dropbox:id:versioned",
            source_rev="rev-old",
            is_current=False,
        )
        current_job = ImportJob(
            source_type="PROGRESS_PDF",
            filename="version.pdf",
            status="SUCCESS",
            document_key="dropbox:id:versioned",
            source_rev="rev-current",
            is_current=True,
        )
        self.db.add_all([faculty, career, period, historical_job, current_job])
        self.db.flush()

        historical_teacher = Teacher(
            career_id=career.id,
            import_job_id=historical_job.id,
            full_name="Persona Historica Oculta",
            institutional_email="historical@example.test",
            validation_status="validated",
        )
        current_teacher = Teacher(
            career_id=career.id,
            import_job_id=current_job.id,
            full_name="Persona Vigente Visible",
            institutional_email="current@example.test",
            validation_status="validated",
        )
        self.db.add_all([historical_teacher, current_teacher])
        self.db.flush()

        historical_role = PersonRole(
            period_id=period.id,
            import_job_id=historical_job.id,
            teacher_id=historical_teacher.id,
            role_type="integrante_interno",
            person_type="teacher",
            person_key="PERSONA HISTORICA OCULTA",
            normalized_name=historical_teacher.full_name,
            validation_status="validated",
        )
        current_role = PersonRole(
            period_id=period.id,
            import_job_id=current_job.id,
            teacher_id=current_teacher.id,
            role_type="integrante_interno",
            person_type="teacher",
            person_key="PERSONA VIGENTE VISIBLE",
            normalized_name=current_teacher.full_name,
            validation_status="validated",
        )
        historical_entity = ResearchEntity(
            period_id=period.id,
            import_job_id=historical_job.id,
            type="proyecto_fci",
            name="Proyecto historico oculto",
            validation_status="validated",
        )
        current_entity = ResearchEntity(
            period_id=period.id,
            import_job_id=current_job.id,
            type="proyecto_fci",
            name="Proyecto vigente visible",
            validation_status="validated",
        )
        historical_progress = ImportedProgressReport(
            import_job_id=historical_job.id,
            year_label=period.year_label,
            cycle=period.cycle,
            teacher_name=historical_teacher.full_name,
        )
        current_progress = ImportedProgressReport(
            import_job_id=current_job.id,
            year_label=period.year_label,
            cycle=period.cycle,
            teacher_name=current_teacher.full_name,
        )
        self.db.add_all(
            [historical_role, current_role, historical_entity, current_entity, historical_progress, current_progress]
        )
        self.db.flush()

        historical_product = ScientificProduction(
            teacher_id=historical_teacher.id,
            period_id=period.id,
            import_job_id=historical_job.id,
            production_type=ProductionType.ARTICLE,
            title="Producto historico oculto",
            validation_status="validated",
        )
        current_product = ScientificProduction(
            teacher_id=current_teacher.id,
            period_id=period.id,
            import_job_id=current_job.id,
            production_type=ProductionType.ARTICLE,
            title="Producto vigente visible",
            validation_status="validated",
        )
        self.db.add_all([historical_product, current_product])
        self.db.flush()
        self.db.add_all(
            [
                ScientificProductionAuthor(
                    production_id=historical_product.id,
                    import_job_id=historical_job.id,
                    author_order=1,
                    normalized_author_name=historical_teacher.full_name,
                    teacher_id=historical_teacher.id,
                    author_type="internal",
                    validation_status="validated",
                ),
                ScientificProductionAuthor(
                    production_id=current_product.id,
                    import_job_id=current_job.id,
                    author_order=1,
                    normalized_author_name=current_teacher.full_name,
                    teacher_id=current_teacher.id,
                    author_type="internal",
                    validation_status="validated",
                ),
            ]
        )
        self.db.flush()

        service = ValidatedReadService(self.db)

        self.assertEqual([row.id for row in service.roles_for_period(period.id)], [current_role.id])
        self.assertEqual([row.id for row in service.participant_roles()], [current_role.id])
        self.assertEqual([row.id for row in service.productions_for_period(period.id)], [current_product.id])
        self.assertEqual([row.id for row in service.production_list(period_id=period.id)], [current_product.id])
        self.assertEqual([row.id for row in service.entities_for_period(period.id)], [current_entity.id])
        self.assertEqual([row.id for row in service.entity_list(period_id=period.id)], [current_entity.id])
        self.assertEqual([row["id"] for row in service.teacher_views(career.id)], [current_teacher.id])
        self.assertEqual([row.id for row in service.progress_rows(period.year_label, period.cycle)], [current_progress.id])
        self.assertEqual(service.goal_totals(period.id, [career.id])[GoalMetric.SCIENTIFIC_OUTPUT], 1)
        dashboard = KpiService(self.db).dashboard(period.year_label, period.cycle)
        self.assertEqual(dashboard.total_teachers, 1)
        self.assertEqual(dashboard.scientific_output_total, 1)
        self.assertEqual(dashboard.projects_total, 1)

    def test_discarded_author_metrics_exclude_historical_versions(self):
        period = AcademicPeriod(year_label="2050-2051", cycle=1)
        historical_job = ImportJob(
            source_type="PROGRESS_PDF",
            filename="authors.pdf",
            status="SUCCESS",
            document_key="dropbox:id:authors",
            source_rev="old",
            is_current=False,
        )
        current_job = ImportJob(
            source_type="PROGRESS_PDF",
            filename="authors.pdf",
            status="SUCCESS",
            document_key="dropbox:id:authors",
            source_rev="current",
            is_current=True,
        )
        self.db.add_all([period, historical_job, current_job])
        self.db.flush()
        progress = ImportedProgressReport(
            import_job_id=current_job.id,
            year_label=period.year_label,
            cycle=period.cycle,
            teacher_name="Informe vigente",
        )
        self.db.add(progress)
        self.db.flush()
        trace = ImportedOcrTrace(
            import_job_id=current_job.id,
            progress_report_id=progress.id,
            source_filename="authors.pdf",
            review_status="PENDIENTE_REVISION",
        )
        historical_product = ScientificProduction(
            period_id=period.id,
            import_job_id=historical_job.id,
            production_type=ProductionType.ARTICLE,
            title="Historico",
            validation_status="pending_review",
        )
        current_product = ScientificProduction(
            period_id=period.id,
            import_job_id=current_job.id,
            production_type=ProductionType.ARTICLE,
            title="Vigente",
            validation_status="pending_review",
        )
        self.db.add_all([trace, historical_product, current_product])
        self.db.flush()
        self.db.add_all(
            [
                ScientificProductionAuthor(
                    production_id=historical_product.id,
                    import_job_id=historical_job.id,
                    author_order=1,
                    raw_author_name="Texto historico invalido",
                    author_type="unresolved",
                    validation_status="discarded_invalid",
                ),
                ScientificProductionAuthor(
                    production_id=current_product.id,
                    import_job_id=current_job.id,
                    author_order=1,
                    raw_author_name="Texto vigente invalido",
                    author_type="unresolved",
                    validation_status="discarded_invalid",
                ),
            ]
        )
        self.db.flush()

        service = ValidatedReadService(self.db)
        view = service.progress_view(progress, trace)

        self.assertEqual(service.discarded_author_count_for_period(period.id), 1)
        self.assertEqual(view["participants_summary"]["discarded_invalid"], 1)
        self.assertEqual(view["participants_summary"]["invalid_text_fragments_count"], 1)
        self.assertEqual(KpiService(self.db).dashboard(period.year_label, period.cycle).discarded_participants_count, 1)

    def test_research_entity_response_uses_period_cycle_when_entity_cycle_is_null(self):
        period = AcademicPeriod(year_label="2052-2053", cycle=1)
        entity = ResearchEntity(
            period=period,
            type="proyecto_fci",
            name="Proyecto con ciclo relacionado",
            cycle=None,
            validation_status="validated",
        )
        self.db.add(entity)
        self.db.flush()

        response = ResearchEntityRead.model_validate(entity)

        self.assertEqual(response.cycle, 1)
        self.assertEqual(response.year_label, period.year_label)

    def test_progress_serializer_delegates_to_validated_read_service(self):
        from app.api.v1.endpoints.imports import _serialize_progress_row

        source = inspect.getsource(_serialize_progress_row)

        self.assertIn("ValidatedReadService", source)
        self.assertNotIn("parsed_payload", source)

    def test_live_readers_delegate_to_validated_read_service(self):
        from app.api.v1.endpoints import goals, participants
        from app.services.production_service import ProductionService
        from app.services.research_entity_service import ResearchEntityService
        from app.services.teacher_service import TeacherService

        self.assertIn("ValidatedReadService", inspect.getsource(participants.list_participants))
        self.assertIn("ValidatedReadService", inspect.getsource(ProductionService.list))
        self.assertIn("ValidatedReadService", inspect.getsource(TeacherService.list))
        self.assertIn("ValidatedReadService", inspect.getsource(ResearchEntityService.list))
        self.assertIn("ValidatedReadService", inspect.getsource(goals.goal_summary))
        self.assertNotIn("parsed_payload", inspect.getsource(goals))


if __name__ == "__main__":
    unittest.main()
