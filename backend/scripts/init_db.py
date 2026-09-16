from pathlib import Path
from hashlib import sha256
from uuid import UUID

from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.database import migration_engine
from app.core.prototype_baseline import (
    BaselineRefused,
    establish_prototype_verified_schema_baseline,
)
from app.core.security import hash_password
from app.core.config import settings
from app.models import *  # noqa: F401,F403
from app.models.entities import (
    AcademicPeriod,
    AnnualGoal,
    Career,
    Faculty,
    ImportedOcrTrace,
    ImportJob,
    PersonRole,
    ProjectTeacher,
    ResearchProject,
    ScientificProduction,
    ScientificProductionAuthor,
    Teacher,
    User,
)
from app.models.human_review_core import ReviewItem
from app.services.human_review_demo_evidence import (
    DEMO_EVIDENCE_FILENAME,
    DEMO_EVIDENCE_PAGES,
    DEMO_EVIDENCE_SHA256,
    DEMO_EVIDENCE_SIZE,
    DEMO_EVIDENCE_SOURCE,
)
from app.models.enums import (
    GoalMetric,
    ProductionType,
    ProjectTeacherRole,
    ProjectType,
    Quartile,
    UserRole,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BootstrapSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=migration_engine,
)

_DEMO_CASE_A_ID = UUID("8e1a0000-0000-4000-8000-000000000001")
_DEMO_CASE_B_ID = UUID("8e1a0000-0000-4000-8000-000000000002")


def _seed_human_review_scope_demo(
    db,
    *,
    faculty: Faculty,
    adm_teacher: Teacher,
    cex_teacher: Teacher,
    adm_production: ScientificProduction,
    cex_production: ScientificProduction,
    period: AcademicPeriod,
) -> None:
    document_key = "demo:human-review-scope:v1"
    revision = "demo-synthetic-v1"
    job = db.query(ImportJob).filter(ImportJob.document_key == document_key).first()
    if job is None:
        job = ImportJob(
            source_type=DEMO_EVIDENCE_SOURCE,
            filename=DEMO_EVIDENCE_FILENAME,
            status="SUCCESS",
            imported_by="academic-prototype",
            summary="DEMO / SYNTHETIC Human Review evidence",
            source_identifier=DEMO_EVIDENCE_SOURCE,
            source_rev=revision,
            source_fingerprint=DEMO_EVIDENCE_SHA256,
            document_key=document_key,
            is_current=True,
            page_count=DEMO_EVIDENCE_PAGES,
            used_ocr=False,
            extraction_method="synthetic",
        )
        db.add(job)
        db.flush()

    trace = db.query(ImportedOcrTrace).filter(
        ImportedOcrTrace.import_job_id == job.id,
        ImportedOcrTrace.source_path == DEMO_EVIDENCE_SOURCE,
    ).first()
    document = {
        "source": DEMO_EVIDENCE_SOURCE,
        "name": DEMO_EVIDENCE_FILENAME,
        "size": DEMO_EVIDENCE_SIZE,
        "content_hash": DEMO_EVIDENCE_SHA256,
        "page_count": DEMO_EVIDENCE_PAGES,
    }
    if trace is None:
        trace = ImportedOcrTrace(
            import_job_id=job.id,
            source_filename=DEMO_EVIDENCE_FILENAME,
            source_path=DEMO_EVIDENCE_SOURCE,
            ocr_provider="synthetic",
            extracted_text="DEMO / SYNTHETIC Case A ADM; Case B same faculty.",
            parsed_payload={"document": document},
            confidence_score=1.0,
            review_status="DEMO_SYNTHETIC",
        )
        db.add(trace)
    else:
        trace.source_filename = DEMO_EVIDENCE_FILENAME
        trace.parsed_payload = {"document": document}

    role_specs = (
        (
            _DEMO_CASE_A_ID,
            f"b2b:v1:person_identity:{'a' * 64}",
            adm_teacher,
            adm_production,
            "Ana T.",
            "Ana Torres",
            1,
            "case_a_adm",
            "participant:case-a-adm",
        ),
        (
            _DEMO_CASE_B_ID,
            f"b2b:v1:person_identity:{'b' * 64}",
            cex_teacher,
            cex_production,
            "Carlos V.",
            "Carlos Vera",
            2,
            "case_b_same_faculty",
            "participant:case-b-cex",
        ),
    )
    for case_id, stable_key, teacher, production, raw_name, canonical_name, page, section, locator in role_specs:
        role = db.query(PersonRole).filter(PersonRole.person_key == stable_key).first()
        if role is None:
            role = PersonRole(
                period_id=period.id,
                import_job_id=job.id,
                teacher_id=teacher.id,
                scientific_production_id=production.id,
                role_type="author",
                person_type="docente_interno",
                person_key=stable_key,
                canonical_identity_key=f"demo:person:{teacher.id}",
                canonical_name=canonical_name,
                identity_source="demo",
                raw_name=raw_name,
                normalized_name=canonical_name.casefold(),
                source_file=DEMO_EVIDENCE_FILENAME,
                source_page=page,
                source_section=section,
                raw_value=raw_name,
                normalized_value=canonical_name,
                parser_version=revision,
                metadata_json={"row_or_block_id": locator},
                validation_status="pending_review",
            )
            db.add(role)
            db.flush()

        author = db.query(ScientificProductionAuthor).filter(
            ScientificProductionAuthor.production_id == production.id,
            ScientificProductionAuthor.teacher_id == teacher.id,
        ).first()
        if author is None:
            author = ScientificProductionAuthor(
                production_id=production.id,
                author_order=1,
                raw_author_name=raw_name,
                normalized_author_name=canonical_name,
                canonical_identity_key=f"demo:person:{teacher.id}",
                canonical_name=canonical_name,
                identity_source="demo",
                teacher_id=teacher.id,
                author_type="internal_teacher",
                source_file=DEMO_EVIDENCE_FILENAME,
                source_page=page,
                source_section=section,
                row_or_block_id=locator,
                validation_status="pending_review",
                import_job_id=job.id,
                parser_version=revision,
            )
            db.add(author)

        case = db.get(ReviewItem, case_id)
        if case is None:
            case = ReviewItem(
                id=case_id,
                case_type="person_identity",
                stable_target_key=stable_key,
                target_table="person_roles",
                target_pk=role.id,
                scope_faculty_id=faculty.id,
                scope_career_id=teacher.career_id,
                scope_resolution_reason=None,
                document_key=document_key,
                source_revision=revision,
                source_page=page,
                source_section=section,
                row_or_block_id=locator,
                field_path="canonical_name",
                raw_value_sha256=sha256(raw_name.encode("utf-8")).hexdigest(),
                period_id=period.id,
                relationship_key=f"teacher:{teacher.id}",
                case_status="pending",
                scientific_status="pending",
                automatic_priority=100 - page,
                possible_kpi_impact=True,
                version=1,
            )
            db.add(case)


def prototype_bootstrap() -> None:
    application_role = make_url(settings.database_url).username
    if not application_role:
        raise BaselineRefused("runtime role is missing from DATABASE_URL")
    establish_prototype_verified_schema_baseline(
        migration_engine,
        application_role,
        REPOSITORY_ROOT,
    )
    if settings.demo_mode:
        seed()


def seed() -> None:
    db = BootstrapSessionLocal()
    try:
        faculty = db.query(Faculty).first()
        if not faculty:
            faculty = Faculty(name="Facultad de Ciencias Administrativas")
            db.add(faculty)
            db.flush()
        else:
            faculty.name = "Facultad de Ciencias Administrativas"

        career_seed = [
            ("Administracion de Empresas", "ADM"),
            ("Comercio Exterior", "CEX"),
            ("Contabilidad y Auditoria", "CPA"),
            ("Finanzas", "FIN"),
            ("Gestion de la Informacion Gerencial", "GIG"),
            ("Mercadotecnia", "MKT"),
            ("Negocios Internacionales", "NIN"),
            ("Turismo", "TUR"),
        ]
        careers_by_code: dict[str, Career] = {}
        for name, code in career_seed:
            career = db.query(Career).filter(Career.code == code).first()
            if not career:
                career = Career(faculty_id=faculty.id, name=name, code=code)
                db.add(career)
                db.flush()
            else:
                career.name = name
                career.faculty_id = faculty.id
            careers_by_code[code] = career

        careers = [careers_by_code[code] for _, code in career_seed]

        periods = []
        for cycle in (1, 2):
            period = (
                db.query(AcademicPeriod)
                .filter(AcademicPeriod.year_label == "2025-2026", AcademicPeriod.cycle == cycle)
                .first()
            )
            if not period:
                period = AcademicPeriod(year_label="2025-2026", cycle=cycle)
                db.add(period)
                db.flush()
            periods.append(period)

        admin_user = db.query(User).filter(User.email == "admin@university.edu").first()
        if not admin_user:
            admin_user = User(
                email="admin@university.edu",
                full_name="Gestor de Investigación de Facultad",
                hashed_password=hash_password("Admin123*"),
                role=UserRole.FACULTY_ADMIN,
                faculty_id=faculty.id,
            )
            db.add(admin_user)
        else:
            admin_user.full_name = "Gestor de Investigación de Facultad"
            admin_user.role = UserRole.FACULTY_ADMIN
            admin_user.faculty_id = faculty.id
            admin_user.career_id = None

        career_manager = db.query(User).filter(User.email == "adm.manager@university.edu").first()
        if not career_manager:
            career_manager = User(
                email="adm.manager@university.edu",
                full_name="Gestor de Investigación de Administración de Empresas",
                hashed_password=hash_password("Manager123*"),
                role=UserRole.CAREER_MANAGER,
                career_id=careers[0].id,
            )
            db.add(career_manager)
        else:
            career_manager.full_name = "Gestor de Investigación de Administración de Empresas"
            career_manager.role = UserRole.CAREER_MANAGER
            career_manager.career_id = careers[0].id
            career_manager.faculty_id = None

        if not settings.seed_demo_data:
            for career in careers:
                goal = (
                    db.query(AnnualGoal)
                    .filter(
                        AnnualGoal.career_id == career.id,
                        AnnualGoal.year_label == "2025-2026",
                        AnnualGoal.metric == GoalMetric.SCIENTIFIC_OUTPUT,
                    )
                    .first()
                )
                if not goal:
                    db.add(
                        AnnualGoal(
                            career_id=career.id,
                            year_label="2025-2026",
                            metric=GoalMetric.SCIENTIFIC_OUTPUT,
                            planned_value=6,
                        )
                    )
                else:
                    goal.planned_value = 6
            db.commit()
            return

        teacher_seed = [
            ("ana.torres@university.edu", careers[0].id, "Ana Torres", 8),
            ("luis.mora@university.edu", careers[0].id, "Luis Mora", 4),
            ("diana.ruiz@university.edu", careers[0].id, "Diana Ruiz", 0),
            ("carlos.vera@university.edu", careers[1].id, "Carlos Vera", 6),
            ("martha.leon@university.edu", careers[1].id, "Martha Leon", 0),
            ("sofia.paz@university.edu", careers[2].id, "Sofia Paz", 10),
            ("pedro.andrade@university.edu", careers[3].id, "Pedro Andrade", 2),
        ]
        teachers_by_email: dict[str, Teacher] = {}
        for email, career_id, full_name, research_hours in teacher_seed:
            teacher = db.query(Teacher).filter(Teacher.institutional_email == email).first()
            if not teacher:
                teacher = Teacher(
                    career_id=career_id,
                    full_name=full_name,
                    institutional_email=email,
                    research_hours=research_hours,
                )
                db.add(teacher)
                db.flush()
            else:
                teacher.career_id = career_id
                teacher.full_name = full_name
                teacher.research_hours = research_hours
            teachers_by_email[email] = teacher

        production_seed = [
            {
                "teacher": "ana.torres@university.edu",
                "period_id": periods[1].id,
                "production_type": ProductionType.ARTICLE,
                "title": "Capacidades de innovacion en universidades publicas",
                "journal": "Revista Regional de Gestion",
                "quartile": Quartile.Q2,
                "link": "https://example.org/article-1",
                "evidence_url": "https://drive.google.com/example",
            },
            {
                "teacher": "luis.mora@university.edu",
                "period_id": periods[1].id,
                "production_type": ProductionType.BOOK_CHAPTER,
                "title": "Transformacion digital en pequenas y medianas empresas",
            },
            {
                "teacher": "carlos.vera@university.edu",
                "period_id": periods[1].id,
                "production_type": ProductionType.PRESENTATION,
                "title": "Conferencia de analitica para auditoria",
                "evidence_url": "https://drive.google.com/example-2",
            },
            {
                "teacher": "sofia.paz@university.edu",
                "period_id": periods[1].id,
                "production_type": ProductionType.BOOK,
                "title": "Comportamiento del consumidor y analisis de datos",
                "evidence_url": "https://drive.google.com/example-3",
            },
            {
                "teacher": "ana.torres@university.edu",
                "period_id": periods[0].id,
                "production_type": ProductionType.ARTICLE,
                "title": "Linea base del ciclo anterior",
                "journal": "Revision de Negocios",
                "quartile": Quartile.Q3,
            },
        ]

        if not db.query(ScientificProduction).first():
            for item in production_seed:
                db.add(
                    ScientificProduction(
                        teacher_id=teachers_by_email[item["teacher"]].id,
                        period_id=item["period_id"],
                        production_type=item["production_type"],
                        title=item["title"],
                        journal=item.get("journal"),
                        quartile=item.get("quartile"),
                        link=item.get("link"),
                        evidence_url=item.get("evidence_url"),
                    )
                )
        else:
            replacements = {
                "Innovation capabilities in public universities": "Capacidades de innovacion en universidades publicas",
                "Digital transformation in SMEs": "Transformacion digital en pequenas y medianas empresas",
                "Audit analytics conference": "Conferencia de analitica para auditoria",
                "Consumer behavior and data": "Comportamiento del consumidor y analisis de datos",
                "Previous cycle baseline": "Linea base del ciclo anterior",
                "Regional Management Journal": "Revista Regional de Gestion",
                "Business Review": "Revision de Negocios",
            }
            for production in db.query(ScientificProduction).all():
                if production.title in replacements:
                    production.title = replacements[production.title]
                if production.journal in replacements:
                    production.journal = replacements[production.journal]

        db.flush()

        project = db.query(ResearchProject).first()
        if not project:
            project = ResearchProject(
                period_id=periods[1].id,
                name="Observatorio de productividad e innovacion FCA",
                project_type=ProjectType.FCI,
                description="Proyecto inter-carreras para indicadores institucionales de investigacion.",
            )
            db.add(project)
            db.flush()
            db.add_all(
                [
                    ProjectTeacher(
                        project_id=project.id,
                        teacher_id=teachers_by_email["ana.torres@university.edu"].id,
                        role=ProjectTeacherRole.DIRECTOR,
                    ),
                    ProjectTeacher(
                        project_id=project.id,
                        teacher_id=teachers_by_email["carlos.vera@university.edu"].id,
                        role=ProjectTeacherRole.RESEARCHER,
                    ),
                    ProjectTeacher(
                        project_id=project.id,
                        teacher_id=teachers_by_email["sofia.paz@university.edu"].id,
                        role=ProjectTeacherRole.RESEARCHER,
                    ),
                ]
            )
        else:
            project.period_id = periods[1].id
            project.name = "Observatorio de productividad e innovacion FCA"
            project.description = "Proyecto inter-carreras para indicadores institucionales de investigacion."

        adm_production = db.query(ScientificProduction).filter(
            ScientificProduction.teacher_id
            == teachers_by_email["ana.torres@university.edu"].id,
            ScientificProduction.period_id == periods[1].id,
        ).order_by(ScientificProduction.id).first()
        cex_production = db.query(ScientificProduction).filter(
            ScientificProduction.teacher_id
            == teachers_by_email["carlos.vera@university.edu"].id,
            ScientificProduction.period_id == periods[1].id,
        ).order_by(ScientificProduction.id).first()
        if adm_production is None or cex_production is None:
            raise RuntimeError("demo scientific production prerequisites are missing")
        _seed_human_review_scope_demo(
            db,
            faculty=faculty,
            adm_teacher=teachers_by_email["ana.torres@university.edu"],
            cex_teacher=teachers_by_email["carlos.vera@university.edu"],
            adm_production=adm_production,
            cex_production=cex_production,
            period=periods[1],
        )

        for career in careers:
            goal = (
                db.query(AnnualGoal)
                .filter(
                    AnnualGoal.career_id == career.id,
                    AnnualGoal.year_label == "2025-2026",
                    AnnualGoal.metric == GoalMetric.SCIENTIFIC_OUTPUT,
                )
                .first()
            )
            if not goal:
                db.add(
                    AnnualGoal(
                        career_id=career.id,
                        year_label="2025-2026",
                        metric=GoalMetric.SCIENTIFIC_OUTPUT,
                        planned_value=6,
                    )
                )
            else:
                goal.planned_value = 6

        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    prototype_bootstrap()
