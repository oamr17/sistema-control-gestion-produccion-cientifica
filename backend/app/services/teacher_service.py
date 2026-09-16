from fastapi import HTTPException, status
from sqlalchemy.orm import Session, selectinload

from app.models.entities import Career, ProjectTeacher, ScientificProduction, Teacher, User
from app.models.enums import UserRole
from app.schemas.teachers import TeacherCreate, TeacherUpdate
from app.services.validated_read_service import ValidatedReadService


class TeacherService:
    def __init__(self, db: Session, user: User):
        self.db = db
        self.user = user

    def _career_scope(self, career_id: int) -> None:
        if self.user.role == UserRole.CAREER_MANAGER and self.user.career_id != career_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tienes acceso a esa carrera")

    def list(self, career_id: int | None = None) -> list[dict]:
        scoped_career_id = self.user.career_id if self.user.role == UserRole.CAREER_MANAGER else career_id
        return ValidatedReadService(self.db).teacher_views(scoped_career_id)

    def create(self, data: TeacherCreate) -> Teacher:
        self._career_scope(data.career_id)
        teacher = Teacher(**data.model_dump())
        self.db.add(teacher)
        self.db.commit()
        self.db.refresh(teacher)
        return teacher

    def update(self, teacher_id: int, data: TeacherUpdate) -> Teacher:
        teacher = self.db.get(Teacher, teacher_id)
        if not teacher:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Docente no encontrado")
        self._career_scope(teacher.career_id)
        update_data = data.model_dump(exclude_unset=True)
        if "career_id" in update_data:
            self._career_scope(update_data["career_id"])
        for key, value in update_data.items():
            setattr(teacher, key, value)
        self.db.commit()
        self.db.refresh(teacher)
        return teacher
