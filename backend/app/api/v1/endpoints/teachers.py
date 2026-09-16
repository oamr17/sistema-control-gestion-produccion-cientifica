from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.database import get_db
from app.models.entities import Teacher, User
from app.schemas.teachers import TeacherCreate, TeacherRead, TeacherUpdate
from app.services.teacher_service import TeacherService

router = APIRouter()


@router.get("", response_model=list[TeacherRead])
def list_teachers(
    career_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    return TeacherService(db, user).list(career_id)


@router.post("", response_model=TeacherRead)
def create_teacher(
    payload: TeacherCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Teacher:
    return TeacherService(db, user).create(payload)


@router.patch("/{teacher_id}", response_model=TeacherRead)
def update_teacher(
    teacher_id: int,
    payload: TeacherUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Teacher:
    return TeacherService(db, user).update(teacher_id, payload)
