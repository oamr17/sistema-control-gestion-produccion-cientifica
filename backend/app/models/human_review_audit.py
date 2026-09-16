from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import CHAR, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, SmallInteger, String, UniqueConstraint, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


JSON_DOCUMENT = JSON().with_variant(JSONB, "postgresql")


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('case_backfilled', 'locked_decision_imported', 'identity_created', "
            "'alias_created', 'override_created', 'capability_assigned', 'capability_revoked', "
            "'audit_corrected', 'functional_reversion', 'scientific_decision_applied')",
            name="ck_audit_events_event_type",
        ),
        CheckConstraint(
            "actor_capability IS NULL OR actor_capability IN ('RESEARCH_MANAGER', 'SYSTEM_ADMIN')",
            name="ck_audit_events_actor_capability",
        ),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_audit_events_payload_object"),
        CheckConstraint("payload_version = 1", name="ck_audit_events_payload_version"),
        CheckConstraint(
            "event_hash ~ '^[0-9a-f]{64}$' AND "
            "(previous_event_hash IS NULL OR previous_event_hash ~ '^[0-9a-f]{64}$')",
            name="ck_audit_events_hashes",
        ),
        CheckConstraint("previous_event_id IS NULL OR previous_event_id <> id", name="ck_audit_events_previous_not_self"),
        CheckConstraint("corrects_event_id IS NULL OR corrects_event_id <> id", name="ck_audit_events_corrects_not_self"),
        CheckConstraint("event_type <> 'audit_corrected' OR corrects_event_id IS NOT NULL", name="ck_audit_events_correction_target"),
        UniqueConstraint("event_hash", name="uq_audit_events_event_hash"),
        Index("ix_audit_events_occurred_at", "occurred_at"),
        Index("ix_audit_events_review_item", "review_item_id"),
        Index("ix_audit_events_actor", "actor_user_id", "occurred_at"),
        Index("ix_audit_events_event_type", "event_type", "occurred_at"),
        Index("ix_audit_events_aggregate", "aggregate_type", "aggregate_key"),
        Index("ix_audit_events_correlation", "correlation_id"),
        Index("ix_audit_events_corrects", "corrects_event_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(60), nullable=False)
    aggregate_key: Mapped[str] = mapped_column(String(320), nullable=False)
    review_item_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("review_items.id", name="fk_audit_events_review_item_id", ondelete="RESTRICT"),
        nullable=True,
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", name="fk_audit_events_actor_user_id", ondelete="RESTRICT"),
        nullable=True,
    )
    actor_identifier: Mapped[str] = mapped_column(String(180), nullable=False)
    actor_capability: Mapped[str | None] = mapped_column(String(40), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc, server_default=text("CURRENT_TIMESTAMP")
    )
    payload_schema: Mapped[str] = mapped_column(String(80), nullable=False)
    payload_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default=text("1"))
    payload: Mapped[dict[str, object]] = mapped_column(JSON_DOCUMENT, nullable=False)
    correlation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    request_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    previous_event_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("audit_events.id", name="fk_audit_events_previous_event_id", ondelete="RESTRICT"),
        nullable=True,
    )
    corrects_event_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("audit_events.id", name="fk_audit_events_corrects_event_id", ondelete="RESTRICT"),
        nullable=True,
    )
    previous_event_hash: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    event_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc, server_default=text("CURRENT_TIMESTAMP")
    )
