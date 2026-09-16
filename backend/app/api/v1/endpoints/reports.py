from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.database import get_db
from app.models.entities import User
from app.services.report_service import ReportService

router = APIRouter()


@router.get("/excel")
def export_excel(
    year_label: str = "2025-2026",
    cycle: int = 2,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> StreamingResponse:
    buffer = ReportService(db).excel(year_label, cycle)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=scientific-production.xlsx"},
    )


@router.get("/pdf")
def export_pdf(
    year_label: str = "2025-2026",
    cycle: int = 2,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> StreamingResponse:
    buffer = ReportService(db).pdf(year_label, cycle)
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=scientific-production.pdf"},
    )
