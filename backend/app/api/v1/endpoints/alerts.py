from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import require_roles
from app.core.database import get_db
from app.models.entities import User
from app.models.enums import UserRole
from app.services.alert_service import AlertService

router = APIRouter()


@router.post("/send")
async def send_alerts(
    year_label: str = "2025-2026",
    cycle: int = 2,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.FACULTY_ADMIN)),
) -> dict[str, int | str]:
    return await AlertService(db).send_period_alerts(year_label, cycle)
