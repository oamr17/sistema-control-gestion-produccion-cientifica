from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
import unicodedata

from app.api.dependencies import require_roles
from app.core.config import settings
from app.core.database import get_db
from app.models.entities import (
    ImportBatch,
    ImportJob,
    ExternalResearcher,
    ImportNormalizationAudit,
    ImportReviewItem,
    AcademicPeriod,
    Career,
    ImportedOcrTrace,
    ImportedProgressReport,
    ImportedProjectParticipant,
    ImportedResearchRecord,
    PersonRole,
    ProjectTeacher,
    ResearchEntity,
    ResearchProject,
    ScientificProduction,
    ScientificProductionAuthor,
    Teacher,
    User,
)
from app.models.enums import UserRole
from app.services.import_batching import status_counts
from app.services.kpi_service import KpiService


router = APIRouter()


def _admin_normalize_key(value: object) -> str:
    text = str(value or "").strip().upper()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.split())


def _looks_like_placeholder_teacher(value: object) -> bool:
    key = _admin_normalize_key(value)
    return any(
        marker in key
        for marker in (
            "AUTOR NO DETECTADO",
            "DOCENTE NO DETECTADO",
            "DIRECTOR NO DETECTADO",
            "RESPONSABLE NO DETECTADO",
        )
    )


def _looks_like_invalid_product_title(value: object) -> bool:
    key = _admin_normalize_key(value)
    if not key:
        return True
    if any(
        marker in key
        for marker in (
            "EVIDENCIA",
            "EVIDENCIAS",
            "ANEXO",
            "ADJUNTO",
            "OBSERVACION",
            "OBSERVACIONES",
            "DESCRIPCION",
            "RESUMEN",
            "AVANCE",
            "ACCIONES EJECUTADAS",
            "ACTIVIDADES REALIZADAS",
            "SE ADJUNTA",
            "LINK DE EVIDENCIA",
            "ENLACE DE EVIDENCIA",
            "PIRAMIDE CIENTIFICA",
            "INDEX PHP",
        )
    ):
        return True
    if any(marker in key for marker in ("HTTP", "WWW", ".COM", ".NET", ".ORG", "DOI", "ISSN", "ISBN")):
        return True
    if "/" in key:
        return True
    if key.split()[-1:] and key.split()[-1] in {"A", "AL", "CON", "DE", "DEL", "EL", "EN", "LA", "LAS", "LOS", "PARA", "POR", "Y"}:
        return True
    return False


def _is_imported_inactive_teacher(value: Teacher) -> bool:
    email = _admin_normalize_key(value.institutional_email)
    return bool(
        not value.is_active
        and email.startswith("IMPORTED-")
        and email.endswith("@LOCAL.IMPORT")
    )


class ResetImportedDataRequest(BaseModel):
    confirm: str


class CleanInvalidImportedRecordsRequest(BaseModel):
    confirm: str


@router.post("/reset-imported-data")
def reset_imported_data(
    payload: ResetImportedDataRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.FACULTY_ADMIN)),
) -> dict:
    if settings.app_env == "production":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="reset-imported-data solo esta disponible fuera de produccion.",
        )
    if not settings.demo_mode:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="reset-imported-data requiere DEMO_MODE=true.",
        )
    if payload.confirm != "RESET_IMPORTED_DATA":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail='Confirmacion requerida: {"confirm":"RESET_IMPORTED_DATA"}',
        )

    deleted: dict[str, int] = {}
    deleted["person_roles"] = db.query(PersonRole).delete(synchronize_session=False)
    deleted["project_teachers"] = db.query(ProjectTeacher).delete(synchronize_session=False)
    deleted["scientific_production_authors"] = db.query(ScientificProductionAuthor).delete(synchronize_session=False)
    deleted["scientific_productions"] = db.query(ScientificProduction).delete(synchronize_session=False)
    deleted["research_entities"] = db.query(ResearchEntity).delete(synchronize_session=False)
    deleted["research_projects"] = db.query(ResearchProject).delete(synchronize_session=False)
    deleted["external_researchers"] = db.query(ExternalResearcher).delete(synchronize_session=False)
    deleted["import_normalization_audits"] = db.query(ImportNormalizationAudit).delete(synchronize_session=False)
    deleted["import_review_items"] = db.query(ImportReviewItem).delete(synchronize_session=False)
    deleted["teachers"] = db.query(Teacher).delete(synchronize_session=False)
    deleted["imported_ocr_traces"] = db.query(ImportedOcrTrace).delete(synchronize_session=False)
    deleted["imported_progress_reports"] = db.query(ImportedProgressReport).delete(synchronize_session=False)
    deleted["imported_project_participants"] = db.query(ImportedProjectParticipant).delete(synchronize_session=False)
    deleted["imported_research_records"] = db.query(ImportedResearchRecord).delete(synchronize_session=False)
    deleted["import_jobs"] = db.query(ImportJob).delete(synchronize_session=False)
    deleted["import_batches"] = db.query(ImportBatch).delete(synchronize_session=False)
    db.commit()

    return {
        "ok": True,
        "deleted": {
            "import_batches": deleted["import_batches"],
            "import_jobs": deleted["import_jobs"],
            "teachers": deleted["teachers"],
            "scientific_productions": deleted["scientific_productions"],
            "research_entities": deleted["research_entities"],
            "research_projects": deleted["research_projects"],
            **deleted,
        },
    }


@router.post("/clean-invalid-imported-records")
def clean_invalid_imported_records(
    payload: CleanInvalidImportedRecordsRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.FACULTY_ADMIN)),
) -> dict:
    if settings.app_env == "production":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="clean-invalid-imported-records solo esta disponible fuera de produccion.",
        )
    if not settings.demo_mode:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="clean-invalid-imported-records requiere DEMO_MODE=true.",
        )
    if payload.confirm != "CLEAN_INVALID_IMPORTED_RECORDS":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail='Confirmacion requerida: {"confirm":"CLEAN_INVALID_IMPORTED_RECORDS"}',
        )

    fake_teachers = [
        teacher
        for teacher in db.query(Teacher).all()
        if _looks_like_placeholder_teacher(teacher.full_name) or _is_imported_inactive_teacher(teacher)
    ]
    fake_teacher_ids = [teacher.id for teacher in fake_teachers]
    invalid_products = [
        product
        for product in db.query(ScientificProduction).all()
        if _looks_like_invalid_product_title(product.title) or product.teacher_id in fake_teacher_ids
    ]
    invalid_product_ids = [product.id for product in invalid_products]

    deleted = {
        "person_roles": 0,
        "scientific_production_authors": 0,
        "scientific_productions": 0,
        "project_teachers": 0,
        "teachers": 0,
    }
    if invalid_product_ids:
        deleted["person_roles"] += db.query(PersonRole).filter(
            PersonRole.scientific_production_id.in_(invalid_product_ids)
        ).delete(synchronize_session=False)
        deleted["scientific_production_authors"] += db.query(ScientificProductionAuthor).filter(
            ScientificProductionAuthor.production_id.in_(invalid_product_ids)
        ).delete(synchronize_session=False)
        deleted["scientific_productions"] = (
            db.query(ScientificProduction)
            .filter(ScientificProduction.id.in_(invalid_product_ids))
            .delete(synchronize_session=False)
        )
    if fake_teacher_ids:
        deleted["person_roles"] += db.query(PersonRole).filter(PersonRole.teacher_id.in_(fake_teacher_ids)).delete(
            synchronize_session=False
        )
        deleted["project_teachers"] = (
            db.query(ProjectTeacher)
            .filter(ProjectTeacher.teacher_id.in_(fake_teacher_ids))
            .delete(synchronize_session=False)
        )
        deleted["teachers"] = (
            db.query(Teacher)
            .filter(Teacher.id.in_(fake_teacher_ids))
            .delete(synchronize_session=False)
        )
    db.commit()
    return {
        "ok": True,
        "deleted": deleted,
        "removed_teacher_ids": fake_teacher_ids,
        "removed_product_ids": invalid_product_ids,
    }


@router.get("/data-consistency-report")
def data_consistency_report(
    year: str = "2025-2026",
    cycle: int = 1,
    career_id: int | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.FACULTY_ADMIN)),
) -> dict:
    period = (
        db.query(AcademicPeriod)
        .filter(AcademicPeriod.year_label == year, AcademicPeriod.cycle == cycle)
        .first()
    )
    if not period:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Periodo no encontrado.")

    dashboard = KpiService(db).dashboard(year, cycle, career_id)
    teachers_query = db.query(Teacher)
    if career_id:
        teachers_query = teachers_query.filter(Teacher.career_id == career_id)
    active_teachers = teachers_query.filter(Teacher.is_active.is_(True)).count()
    inactive_teachers = teachers_query.filter(Teacher.is_active.is_(False)).count()

    productions_query = (
        db.query(ScientificProduction)
        .outerjoin(Teacher)
        .outerjoin(ResearchEntity)
        .filter(ScientificProduction.period_id == period.id)
    )
    projects_query = db.query(ResearchProject).filter(ResearchProject.period_id == period.id)
    research_entities_query = db.query(ResearchEntity).filter(
        ResearchEntity.period_id == period.id,
        ResearchEntity.type.in_(("proyecto_fci", "grupo_investigacion", "semillero", "informe_seguimiento")),
    )
    if career_id:
        career = db.get(Career, career_id)
        career_name = career.name if career else None
        career_short_name = career_name.replace("Licenciatura en ", "").replace("Licenciatura ", "") if career_name else None
        productions_query = productions_query.filter(
            or_(
                Teacher.career_id == career_id,
                ResearchEntity.career_name.ilike(f"%{career_name}%") if career_name else False,
                ResearchEntity.career_name.ilike(f"%{career_short_name}%") if career_short_name else False,
            )
        )
        projects_query = projects_query.join(ProjectTeacher).join(Teacher).filter(Teacher.career_id == career_id).distinct()
        if career_name:
            research_entities_query = research_entities_query.filter(
                or_(
                    ResearchEntity.career_name.ilike(f"%{career_name}%"),
                    ResearchEntity.career_name.ilike(f"%{career_short_name}%") if career_short_name else False,
                )
            )

    productions = productions_query.all()
    projects = projects_query.all()
    research_entities = research_entities_query.all()
    project_entities = [*projects, *research_entities]
    teachers = teachers_query.order_by(Teacher.full_name).all()
    fake_teachers = [
        {"id": teacher.id, "full_name": teacher.full_name, "is_active": teacher.is_active}
        for teacher in teachers
        if _looks_like_placeholder_teacher(teacher.full_name)
    ]
    suspicious_products = [
        {"id": item.id, "title": item.title, "teacher_id": item.teacher_id}
        for item in productions
        if _looks_like_invalid_product_title(item.title)
    ]
    latest_batch = db.query(ImportBatch).filter(ImportBatch.source_type == "PROGRESS_PDF").order_by(ImportBatch.id.desc()).first()
    import_jobs = (
        db.query(ImportJob).filter(ImportJob.batch_id == latest_batch.id).all()
        if latest_batch
        else []
    )
    import_job_ids = [job.id for job in import_jobs]
    review_trace_rows = (
        db.query(ImportedOcrTrace.import_job_id)
        .filter(
            ImportedOcrTrace.import_job_id.in_(import_job_ids),
            ImportedOcrTrace.review_status.in_({"PENDIENTE_REVISION", "REQUIERE_REVISION_TIPO_DOCUMENTO"}),
        )
        .all()
        if import_job_ids
        else []
    )
    import_counts = status_counts(import_jobs, {row[0] for row in review_trace_rows})

    real = {
        "dashboard": {
            "informes_recibidos": dashboard.reports_received,
            "informes_revision": dashboard.reports_requires_review,
            "docentes_activos": dashboard.total_teachers,
            "productos_cientificos": dashboard.scientific_output_total,
            "proyectos_activos": dashboard.projects_total,
            "avance_promedio": dashboard.projects_average_progress_percent,
        },
        "teachers": {
            "total_docentes_endpoint": len(teachers),
            "total_docentes_mostrados": len(teachers),
            "docentes_activos": active_teachers,
            "docentes_desvinculados": inactive_teachers,
            "primera_pagina_frontend": min(len(teachers), 10),
            "page_size_frontend": 10,
            "registros_sospechosos": fake_teachers,
            "docentes_por_carrera": [
                {"career": name, "count": count}
                for name, count in db.query(Career.name, func.count(Teacher.id))
                .outerjoin(Teacher, (Teacher.career_id == Career.id) & (Teacher.is_active.is_(True)))
                .group_by(Career.name)
                .order_by(Career.name)
                .all()
            ],
        },
        "production": {
            "total_productos": len(productions),
            "primera_pagina_frontend": min(len(productions), 10),
            "page_size_frontend": 10,
            "publicados": sum(1 for item in productions if item.status in {"published", "publicado", "publicada"}),
            "enviados_revision": sum(1 for item in productions if item.status in {"en_revision", "enviado_revision"}),
            "registros_sospechosos": suspicious_products,
        },
        "projects": {
            "total_proyectos": len(project_entities),
            "research_projects": len(projects),
            "research_entities": len(research_entities),
            "primera_pagina_frontend": min(len(project_entities), 10),
            "page_size_frontend": 10,
            "vigentes": sum(1 for item in project_entities if str(item.status).lower() == "vigente"),
            "aprobados": sum(1 for item in project_entities if str(item.status).lower() == "aprobado"),
            "avance_promedio": round(sum(float(item.progress_percentage or 0) for item in project_entities) / len(project_entities), 2) if project_entities else 0,
        },
        "endpoint_sources": {
            "teachers": "/api/v1/teachers",
            "production": "/api/v1/production",
            "projects": "/api/v1/projects + /api/v1/research-entities",
            "dashboard": "/api/v1/dashboard/summary",
            "imports": "/api/v1/imports/latest/status",
            "frontend_pagination": "DataTable pagina en cliente; el endpoint devuelve todos los registros.",
        },
        "imports": {
            "batch_id": latest_batch.id if latest_batch else None,
            "total_archivos": len(import_jobs),
            **import_counts,
        },
        "external_researchers": {
            "total": db.query(ExternalResearcher).filter(ExternalResearcher.period_id == period.id).count(),
            "por_universidad": [
                {"university": name, "count": count}
                for name, count in db.query(ExternalResearcher.institution, func.count(ExternalResearcher.id))
                .filter(ExternalResearcher.period_id == period.id)
                .group_by(ExternalResearcher.institution)
                .order_by(ExternalResearcher.institution)
                .all()
            ],
        },
    }
    inconsistencies: list[dict] = []

    def compare(metric: str, dashboard_value: int | float, real_value: int | float, cause: str) -> None:
        if dashboard_value != real_value:
            inconsistencies.append(
                {
                    "type": "count_mismatch",
                    "metric": metric,
                    "dashboard_value": dashboard_value,
                    "real_value": real_value,
                    "difference": real_value - dashboard_value,
                    "possible_cause": cause,
                }
            )

    compare("total_teachers", dashboard.total_teachers, active_teachers, "Dashboard y docentes deben leer teachers activos.")
    compare("scientific_output_total", dashboard.scientific_output_total, len(productions), "Dashboard y produccion deben leer scientific_productions.")
    compare("projects_total", dashboard.projects_total, len(project_entities), "Dashboard y proyectos deben leer research_projects/research_entities.")
    compare("imports_total", dashboard.reports_received, db.query(ImportedProgressReport).filter(ImportedProgressReport.year_label == year, ImportedProgressReport.cycle == cycle).count(), "Informes recibidos deben contar reportes reales, no deduplicados.")

    if inactive_teachers:
        inconsistencies.append(
            {
                "type": "scope_visibility_warning",
                "metric": "teachers_active_vs_endpoint_total",
                "endpoint_value": len(teachers),
                "active_value": active_teachers,
                "inactive_value": inactive_teachers,
                "possible_cause": "Dashboard cuenta docentes activos, pero /teachers devuelve activos e inactivos.",
            }
        )
    if fake_teachers:
        inconsistencies.append(
            {
                "type": "invalid_persisted_record",
                "metric": "teachers",
                "count": len(fake_teachers),
                "records": fake_teachers[:20],
                "possible_cause": "Se persistieron placeholders como docentes reales.",
            }
        )
    if suspicious_products:
        inconsistencies.append(
            {
                "type": "invalid_persisted_record",
                "metric": "scientific_productions",
                "count": len(suspicious_products),
                "records": suspicious_products[:20],
                "possible_cause": "Se persistieron evidencias, anexos u observaciones como productos cientificos.",
            }
        )

    return {
        "year": year,
        "cycle": cycle,
        "career_id": career_id,
        "values": real,
        "inconsistencies": inconsistencies,
        "source_of_truth": {
            "teachers": "teachers",
            "production": "scientific_productions",
            "projects": "research_projects/research_entities",
            "external_researchers": "external_researchers",
            "imports": "import_batches/import_jobs/imported_progress_reports",
        },
    }
