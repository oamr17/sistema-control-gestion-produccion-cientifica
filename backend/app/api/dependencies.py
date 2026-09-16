from uuid import UUID, uuid4

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_access_token
from app.models.entities import User
from app.models.enums import UserRole
from app.models.human_review_enums import B2BAction
from app.schemas.human_review_api import HumanReviewErrorResponse
from app.services.human_review_authorization import (
    B2BAccessDenied,
    authorize_b2b_action,
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token invalido")

    user = db.query(User).filter(User.email == payload["sub"], User.is_active.is_(True)).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario inactivo")
    return user


def require_roles(*roles: UserRole):
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Rol insuficiente")
        return user

    return dependency


def require_b2b_action(action: B2BAction):
    requested_action = B2BAction(action)

    def dependency(
        request: Request,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        try:
            authorize_b2b_action(db, user, requested_action)
        except B2BAccessDenied:
            candidate = getattr(request.state, "correlation_id", None)
            try:
                correlation_id = candidate if isinstance(candidate, UUID) else UUID(str(candidate))
            except (TypeError, ValueError):
                correlation_id = uuid4()
            envelope = HumanReviewErrorResponse(
                code="B2B_CAPABILITY_REQUIRED",
                message="B2B capability is required for this action",
                correlation_id=correlation_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=envelope.model_dump(mode="json"),
            ) from None
        return user

    return dependency
