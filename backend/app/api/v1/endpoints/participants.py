import re

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, joinedload

from app.api.dependencies import get_current_user
from app.core.database import get_db
from app.models.entities import Career, Teacher, User
from app.models.enums import UserRole
from app.services.validated_read_service import ValidatedReadService


router = APIRouter()


def _is_real_email(value: object) -> bool:
    email = str(value or "").strip()
    key = email.lower()
    if not email:
        return False
    if key.startswith("imported-") or key.endswith("@local.import"):
        return False
    if any(marker in key for marker in ("\\", "/", "dropbox", "source_key")):
        return False
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email))


def _role_label(value: str) -> str:
    labels = {
        "integrante_interno": "Docente interno",
        "participante_externo": "Participante externo",
        "director": "Director de proyecto",
        "autor_producto": "Autor de produccion cientifica",
        "responsable_informe": "Responsable del informe",
    }
    return labels.get(value, value.replace("_", " ").title())


def _person_type_label(value: str) -> str:
    labels = {
        "teacher": "Docente interno",
        "external_researcher": "Investigador externo",
        "investigador_externo": "Investigador externo",
        "estudiante": "Estudiante",
        "graduado": "Graduado",
        "unresolved": "Pendiente de validacion",
    }
    return labels.get(value, value.replace("_", " ").title())


@router.get("")
def list_participants(
    career_id: int | None = None,
    role: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    scoped_career_id = user.career_id if user.role == UserRole.CAREER_MANAGER else career_id
    rows = ValidatedReadService(db).canonical_participants(
        career_id=scoped_career_id,
        role_type=role,
    )
    result = []
    for index, row in enumerate(rows, start=1):
        person_type = row["person_type"]
        review_bucket = (
            "valid_person"
            if row["kpi_eligible"]
            else "pending_author_classification"
            if row["roles"] == ["autor_producto"]
            else "pending_person"
        )
        variants = [
            {
                **variant,
                "id": variant["source_id"],
                "role_type": variant["role_type"],
                "role_label": _role_label(variant["role_type"]),
                "person_type": variant["person_type"],
                "source_file": variant["document"],
                "confidence_score": variant["confidence"],
                "product_id": variant.get("production_id"),
            }
            for variant in row["variants"]
        ]
        result.append(
            {
                "id": index,
                "canonical_identity_key": row["canonical_identity_key"],
                "canonical_name": row["canonical_name"],
                "identity_source": row["identity_source"],
                "identity_confidence": row["identity_confidence"],
                "identity_reason": row["identity_reason"],
                "identity_locked": row["identity_locked"],
                "person_key": next((item.get("person_key") for item in variants if item.get("person_key")), None),
                "person": row["canonical_name"],
                "person_type": person_type,
                "type_label": _person_type_label(person_type),
                "roles": row["roles"],
                "role_labels": [_role_label(value) for value in row["roles"]],
                "affiliation": "; ".join(row["affiliations"]) or "No registrado",
                "email": next((email for email in row["emails"] if _is_real_email(email)), None),
                "status": "validado" if row["kpi_eligible"] else "pendiente_validacion",
                "validation_status": row["overall_status"],
                "overall_status": row["overall_status"],
                "review_bucket": review_bucket,
                "show_in_participants": True,
                "kpi_eligible": row["kpi_eligible"],
                "pending_reasons": row["pending_reasons"],
                "possible_match_notice": row["possible_match_notice"],
                "possible_matches": row["possible_matches"],
                "variants": row["variants"],
                "appearances": variants,
                "documents": row["documents"],
                "research_entities": row["research_entities"],
                "projects": [
                    {
                        "id": entity["id"],
                        "name": entity["name"],
                        "status": entity["status"],
                        "source_section": entity["source_section"],
                    }
                    for entity in row["research_entities"]
                ],
                "authorships": row["authorships"],
                "products": [
                    {
                        "id": authorship["production_id"],
                        "title": authorship["title"],
                        "status": authorship["status"],
                        "source_section": authorship["source_section"],
                    }
                    for authorship in row["authorships"]
                ],
                "evidence": row["evidence"],
                "validation_statuses": row["validation_statuses"],
                "participation_count": row["participation_count"],
                "role_count": row["role_count"],
                "role_type_count": row["role_type_count"],
                "authorship_count": row["authorship_count"],
                "evidence_count": row["evidence_count"],
            }
        )
    if status:
        result = [item for item in result if status in {item["status"], item["overall_status"]}]
    return sorted(result, key=lambda item: (item["person"], item["type_label"]))
