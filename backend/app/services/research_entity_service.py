from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.models.entities import Career, ResearchEntity, User
from app.models.enums import UserRole
from app.services.validated_read_service import VALIDATED_STATUS, ValidatedReadService

class ResearchEntityService:
    def __init__(self, db: Session, user: User):
        self.db = db
        self.user = user

    def list(
        self,
        period_id: int | None = None,
        career_id: int | None = None,
        status: str | None = None,
        validation_status: str | None = None,
    ) -> list[dict]:
        scoped_career_id = self.user.career_id if self.user.role == UserRole.CAREER_MANAGER else career_id
        if validation_status and validation_status != VALIDATED_STATUS:
            return []
        return ValidatedReadService(self.db).entity_views(period_id, scoped_career_id, status)
