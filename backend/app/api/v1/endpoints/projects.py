from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.database import get_db
from app.models.entities import ResearchProject, User
from app.schemas.projects import ProjectCreate, ProjectRead
from app.services.project_service import ProjectService

router = APIRouter()


@router.get("", response_model=list[ProjectRead])
def list_projects(
    period_id: int | None = None,
    career_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    return ProjectService(db, user).list(period_id, career_id)


@router.post("", response_model=ProjectRead)
def create_project(
    payload: ProjectCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ResearchProject:
    return ProjectService(db, user).create(payload)
