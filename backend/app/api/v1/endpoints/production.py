from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.database import get_db
from app.models.entities import ScientificProduction, User
from app.schemas.production import ProductionCreate, ProductionRead
from app.services.production_service import ProductionService

router = APIRouter()


@router.get("", response_model=list[ProductionRead])
def list_production(
    period_id: int | None = None,
    career_id: int | None = None,
    visibility: Literal["all", "eligible", "pending", "discarded"] = "eligible",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    return ProductionService(db, user).list(period_id, career_id, visibility)


@router.post("", response_model=ProductionRead)
def create_production(
    payload: ProductionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ScientificProduction:
    return ProductionService(db, user).create(payload)
