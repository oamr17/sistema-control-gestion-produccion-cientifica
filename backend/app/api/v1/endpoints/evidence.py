from fastapi import APIRouter, Depends, File, UploadFile

from app.api.dependencies import get_current_user
from app.models.entities import User
from app.services.evidence_service import EvidenceService

router = APIRouter()


@router.post("/upload")
def upload_evidence(
    file: UploadFile = File(...),
    _: User = Depends(get_current_user),
) -> dict[str, str]:
    return {"url": EvidenceService().upload_pdf(file)}
