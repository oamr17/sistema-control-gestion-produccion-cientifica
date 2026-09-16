from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.database import get_db
from app.models.entities import AcademicPeriod, Career, User
from app.schemas.common import CareerRead, PeriodRead

router = APIRouter()


@router.get("/careers", response_model=list[CareerRead])
def list_careers(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[Career]:
    query = db.query(Career)
    if user.career_id:
        query = query.filter(Career.id == user.career_id)
    return query.order_by(Career.name).all()


@router.get("/periods", response_model=list[PeriodRead])
def list_periods(
    db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> list[AcademicPeriod]:
    return db.query(AcademicPeriod).order_by(AcademicPeriod.year_label.desc(), AcademicPeriod.cycle.desc()).all()
