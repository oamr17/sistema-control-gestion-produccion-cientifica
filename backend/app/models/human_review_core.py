from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CHAR,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


JSON_DOCUMENT = JSON().with_variant(JSONB, "postgresql")


class ReviewItem(Base):
    __tablename__ = "review_items"
    __table_args__ = (
        CheckConstraint(
            "case_type IN ('person_identity', 'author_identity', 'product', 'project_director_relation', "
            "'external_identity', 'possible_duplicate', 'invalid_text', 'new_evidence_conflict')",
            name="ck_review_items_case_type",
        ),
        CheckConstraint(
            "target_table IN ('person_roles', 'scientific_production_authors', 'scientific_productions', "
            "'research_entities', 'external_researchers')",
            name="ck_review_items_target_table",
        ),
        CheckConstraint(
            "case_status IN ('pending', 'in_review', 'awaiting_gestor_approval', 'resolved', "
            "'reopened', 'conflicted', 'superseded')",
            name="ck_review_items_case_status",
        ),
        CheckConstraint(
            "scientific_status IN ('pending', 'validated', 'rejected', 'discarded')",
            name="ck_review_items_scientific_status",
        ),
        CheckConstraint(
            "scope_career_id IS NULL OR scope_faculty_id IS NOT NULL",
            name="ck_review_items_scope_hierarchy",
        ),
        CheckConstraint(
            "(scope_faculty_id IS NULL AND scope_career_id IS NULL "
            "AND scope_resolution_reason IS NOT NULL "
            "AND scope_resolution_reason IN "
            "('unresolved_no_persisted_scope', 'unresolved_cross_faculty', "
            "'unresolved_missing_target')) "
            "OR (scope_faculty_id IS NOT NULL AND scope_resolution_reason IS NULL)",
            name="ck_review_items_scope_resolution",
        ),
        ForeignKeyConstraint(
            ("scope_career_id", "scope_faculty_id"),
            ("careers.id", "careers.faculty_id"),
            name="fk_review_items_scope_career_faculty_careers",
            ondelete="RESTRICT",
        ),
        CheckConstraint("raw_value_sha256 ~ '^[0-9a-f]{64}$'", name="ck_review_items_raw_hash"),
        Index(
            "uq_review_items_active_case_target",
            "case_type",
            "stable_target_key",
            unique=True,
            postgresql_where=text(
                "case_status IN ('pending', 'in_review', 'awaiting_gestor_approval', 'reopened', 'conflicted')"
            ),
        ),
        Index("ix_review_items_queue", "case_type", "case_status", "automatic_priority", "created_at"),
        Index("ix_review_items_target", "target_table", "target_pk"),
        Index("ix_review_items_document", "document_key"),
        Index("ix_review_items_period", "period_id"),
        Index("ix_review_items_current_decision", "current_decision_id"),
        Index(
            "ix_review_items_scope_faculty_queue",
            "scope_faculty_id",
            "case_status",
            "automatic_priority",
            "created_at",
            "id",
        ),
        Index(
            "ix_review_items_scope_career_queue",
            "scope_career_id",
            "case_status",
            "automatic_priority",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    case_type: Mapped[str] = mapped_column(String(60), nullable=False)
    stable_target_key: Mapped[str] = mapped_column(String(128), nullable=False)
    target_table: Mapped[str] = mapped_column(String(80), nullable=False)
    target_pk: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    scope_faculty_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "faculties.id",
            name="fk_review_items_scope_faculty_id_faculties",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    scope_career_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scope_resolution_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    document_key: Mapped[str] = mapped_column(String(900), nullable=False)
    source_revision: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_section: Mapped[str] = mapped_column(String(120), nullable=False)
    row_or_block_id: Mapped[str] = mapped_column(Text, nullable=False)
    field_path: Mapped[str] = mapped_column(String(120), nullable=False)
    raw_value_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    period_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    relationship_key: Mapped[str | None] = mapped_column(String(320), nullable=True)
    case_status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="pending", server_default=text("'pending'")
    )
    scientific_status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="pending", server_default=text("'pending'")
    )
    automatic_priority: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0")
    )
    manual_priority: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    possible_kpi_impact: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    current_decision_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "review_decisions.id",
            name="fk_review_items_current_decision_id_review_decisions",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        nullable=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc, onupdate=_now_utc,
        server_default=text("CURRENT_TIMESTAMP")
    )


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    __table_args__ = (
        CheckConstraint(
            "decision_type IN ('validated', 'corrected', 'linked', 'merged', 'maintained_separate', "
            "'separated', 'rejected', 'discarded', 'maintained', 'reverted')",
            name="ck_review_decisions_decision_type",
        ),
        CheckConstraint(
            "decision_lifecycle IN ('proposed', 'approved', 'declined', 'superseded')",
            name="ck_review_decisions_lifecycle",
        ),
        CheckConstraint(
            "scope IN ('global_identity', 'record', 'document', 'relationship', 'period')",
            name="ck_review_decisions_scope",
        ),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_review_decisions_payload_object"),
        CheckConstraint("payload_version = 1", name="ck_review_decisions_payload_version"),
        CheckConstraint("sequence > 0", name="ck_review_decisions_positive_sequence"),
        CheckConstraint(
            "(actor_type = 'human' AND actor_user_id IS NOT NULL AND actor_capability IN "
            "('RESEARCH_MANAGER', 'SYSTEM_ADMIN')) OR "
            "(actor_type = 'legacy' AND actor_user_id IS NULL AND actor_capability IS NULL "
            "AND NULLIF(BTRIM(actor_identifier), '') IS NOT NULL)",
            name="ck_review_decisions_actor_shape",
        ),
        CheckConstraint(
            "decision_lifecycle <> 'approved' OR locks_projection",
            name="ck_review_decisions_approved_locks_projection",
        ),
        CheckConstraint(
            "previous_decision_id IS NULL OR previous_decision_id <> id",
            name="ck_review_decisions_previous_not_self",
        ),
        CheckConstraint(
            "corrects_decision_id IS NULL OR corrects_decision_id <> id",
            name="ck_review_decisions_corrects_not_self",
        ),
        UniqueConstraint("review_item_id", "sequence", name="uq_review_decisions_item_sequence"),
        Index("ix_review_decisions_case", "review_item_id", "decided_at"),
        Index("ix_review_decisions_lifecycle", "decision_lifecycle"),
        Index("ix_review_decisions_actor", "actor_user_id", "decided_at"),
        Index("ix_review_decisions_previous", "previous_decision_id"),
        Index("ix_review_decisions_corrects", "corrects_decision_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    review_item_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("review_items.id", name="fk_review_decisions_review_item_id_review_items", ondelete="RESTRICT"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    decision_type: Mapped[str] = mapped_column(String(40), nullable=False)
    decision_lifecycle: Mapped[str] = mapped_column(String(30), nullable=False)
    scope: Mapped[str] = mapped_column(String(30), nullable=False)
    payload_schema: Mapped[str] = mapped_column(String(80), nullable=False)
    payload_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default=text("1"))
    payload: Mapped[dict[str, object]] = mapped_column(JSON_DOCUMENT, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_type: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", name="fk_review_decisions_actor_user_id_users", ondelete="RESTRICT"),
        nullable=True,
    )
    actor_identifier: Mapped[str] = mapped_column(String(180), nullable=False)
    actor_capability: Mapped[str | None] = mapped_column(String(40), nullable=True)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc, server_default=text("CURRENT_TIMESTAMP")
    )
    expected_case_version: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_decision_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("review_decisions.id", name="fk_review_decisions_previous_decision_id", ondelete="RESTRICT"),
        nullable=True,
    )
    corrects_decision_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("review_decisions.id", name="fk_review_decisions_corrects_decision_id", ondelete="RESTRICT"),
        nullable=True,
    )
    locks_projection: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc, server_default=text("CURRENT_TIMESTAMP")
    )
