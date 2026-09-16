from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CHAR, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class UserB2BCapability(Base):
    __tablename__ = "user_b2b_capabilities"
    __table_args__ = (
        CheckConstraint(
            "capability IN ('RESEARCH_MANAGER', 'SYSTEM_ADMIN')",
            name="ck_user_b2b_capabilities_capability",
        ),
        CheckConstraint(
            "approved_input_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_user_b2b_capabilities_hash",
        ),
        CheckConstraint(
            "NOT is_active OR revoked_at IS NULL",
            name="ck_user_b2b_capabilities_active_not_revoked",
        ),
        CheckConstraint(
            "is_active OR (revoked_at IS NOT NULL AND NULLIF(BTRIM(revocation_reason), '') IS NOT NULL)",
            name="ck_user_b2b_capabilities_revoked_complete",
        ),
        Index(
            "uq_user_b2b_capabilities_active_user",
            "user_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
        Index(
            "ix_user_b2b_capabilities_capability_active",
            "capability",
            "is_active",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", name="fk_user_b2b_capabilities_user_id_users", ondelete="RESTRICT"),
        nullable=False,
    )
    capability: Mapped[str] = mapped_column(String(40), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    approval_reference: Mapped[str] = mapped_column(String(240), nullable=False)
    approved_input_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    assigned_by_identifier: Mapped[str] = mapped_column(String(180), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc, server_default=text("CURRENT_TIMESTAMP")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revocation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
