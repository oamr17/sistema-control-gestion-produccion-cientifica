from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import create_access_token, verify_password
from app.models.entities import User
from app.schemas.auth import TokenResponse


class AuthService:
    def __init__(self, db: Session):
        self.db = db

    def login(self, email: str, password: str) -> TokenResponse:
        user = self.db.query(User).filter(User.email == email, User.is_active.is_(True)).first()
        if not user or not verify_password(password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Credenciales institucionales no válidas",
            )

        token = create_access_token(user.email, user.role.value)
        return TokenResponse(
            access_token=token,
            role=user.role,
            career_id=user.career_id,
            full_name=user.full_name,
        )
