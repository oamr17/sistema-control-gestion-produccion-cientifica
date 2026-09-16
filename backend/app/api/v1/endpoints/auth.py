from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.auth import LoginRequest, TokenResponse
from app.services.auth_service import AuthService

router = APIRouter()


@router.post("/login", response_model=TokenResponse)
async def login(request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/x-www-form-urlencoded") or content_type.startswith("multipart/form-data"):
        form = await request.form()
        email = str(form.get("username") or "")
        password = str(form.get("password") or "")
    else:
        payload = LoginRequest.model_validate(await request.json())
        email = payload.email
        password = payload.password

    return AuthService(db).login(email, password)
