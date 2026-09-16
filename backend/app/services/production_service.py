from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.entities import Career, ResearchEntity, ScientificProduction, Teacher, User
from app.models.enums import ProductionType, UserRole
from app.schemas.production import ProductionCreate
from app.services.validated_read_service import ValidatedReadService


class ProductionService:
    def __init__(self, db: Session, user: User):
        self.db = db
        self.user = user

    def list(
        self,
        period_id: int | None = None,
        career_id: int | None = None,
        visibility: str = "eligible",
    ) -> list[dict]:
        scoped_career_id = self.user.career_id if self.user.role == UserRole.CAREER_MANAGER else career_id
        return ValidatedReadService(self.db).production_views(
            period_id,
            scoped_career_id,
            visibility=visibility,
        )

    def create(self, data: ProductionCreate) -> ScientificProduction:
        if not data.teacher_id:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="El docente es obligatorio")
        teacher = self.db.get(Teacher, data.teacher_id)
        if not teacher:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Docente no encontrado")
        if self.user.role == UserRole.CAREER_MANAGER and teacher.career_id != self.user.career_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tienes acceso a esa carrera")
        if data.production_type == ProductionType.ARTICLE and not data.quartile:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="El cuartil es obligatorio para artículos científicos",
            )
        production = ScientificProduction(**data.model_dump())
        self.db.add(production)
        self.db.commit()
        self.db.refresh(production)
        return production
