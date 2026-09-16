from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.database import get_db
from app.models.entities import ResearchEntity, User
from app.schemas.research_entities import ResearchEntityRead
from app.services.research_entity_service import ResearchEntityService

router = APIRouter()


@router.get("", response_model=list[ResearchEntityRead])
def list_research_entities(
    period_id: int | None = None,
    career_id: int | None = None,
    status: str | None = None,
    validation_status: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    return ResearchEntityService(db, user).list(period_id, career_id, status, validation_status)
