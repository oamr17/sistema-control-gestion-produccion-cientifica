from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.database import get_db
from app.models.entities import User
from app.models.enums import UserRole
from app.schemas.kpis import DashboardKpi
from app.services.kpi_service import KpiService

router = APIRouter()


@router.get("/dashboard", response_model=DashboardKpi)
def dashboard(
    year_label: str = "2025-2026",
    cycle: int = 2,
    career_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DashboardKpi:
    scoped_career_id = user.career_id if user.role == UserRole.CAREER_MANAGER else career_id
    return KpiService(db).dashboard(year_label, cycle, scoped_career_id)
