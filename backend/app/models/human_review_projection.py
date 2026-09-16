from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    SmallInteger,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


JSON_DOCUMENT = JSON().with_variant(JSONB, "postgresql")


class CanonicalIdentity(Base):
    __tablename__ = "canonical_identities"
    __table_args__ = (
        CheckConstraint(
            "identity_type IN ('internal_person', 'external_person', 'unclassified_person')",
            name="ck_canonical_identities_identity_type",
        ),
        CheckConstraint(
            "status IN ('active', 'merged', 'superseded')",
            name="ck_canonical_identities_status",
        ),
        CheckConstraint("origin IN ('b1_locked', 'human')", name="ck_canonical_identities_origin"),
        UniqueConstraint("canonical_identity_key", name="uq_canonical_identities_key"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    canonical_identity_key: Mapped[str] = mapped_column(String(320), nullable=False)
    identity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    display_name: Mapped[str] = mapped_column(String(220), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active", server_default=text("'active'"))
    origin: Mapped[str] = mapped_column(String(30), nullable=False)
    created_by_decision_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("review_decisions.id", name="fk_canonical_identities_created_by_decision_id", ondelete="RESTRICT"),
        nullable=True,
    )
    superseded_by_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("canonical_identities.id", name="fk_canonical_identities_superseded_by_id", ondelete="RESTRICT"),
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


class PersonAlias(Base):
    __tablename__ = "person_aliases"
    __table_args__ = (
        CheckConstraint("alias_class = 'person_name'", name="ck_person_aliases_alias_class"),
        CheckConstraint("scope = 'global_identity'", name="ck_person_aliases_scope"),
        CheckConstraint("status IN ('active', 'superseded')", name="ck_person_aliases_status"),
        Index(
            "uq_person_aliases_active_normalized_class",
            "alias_normalized",
            "alias_class",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    alias_original: Mapped[str] = mapped_column(String(320), nullable=False)
    alias_normalized: Mapped[str] = mapped_column(String(320), nullable=False)
    alias_class: Mapped[str] = mapped_column(
        String(40), nullable=False, default="person_name", server_default=text("'person_name'")
    )
    canonical_identity_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("canonical_identities.id", name="fk_person_aliases_canonical_identity_id", ondelete="RESTRICT"),
        nullable=False,
    )
    decision_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("review_decisions.id", name="fk_person_aliases_decision_id", ondelete="RESTRICT"),
        nullable=False,
    )
    scope: Mapped[str] = mapped_column(
        String(30), nullable=False, default="global_identity", server_default=text("'global_identity'")
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active", server_default=text("'active'"))
    superseded_by_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("person_aliases.id", name="fk_person_aliases_superseded_by_id", ondelete="RESTRICT"),
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


class FieldOverride(Base):
    __tablename__ = "field_overrides"
    __table_args__ = (
        CheckConstraint(
            "target_table IN ('person_roles', 'scientific_production_authors', 'scientific_productions', "
            "'research_entities', 'external_researchers')",
            name="ck_field_overrides_target_table",
        ),
        CheckConstraint(
            "field_path IN ('canonical_identity_key', 'canonical_name', 'product_title', "
            "'author_identity_key', 'project_director_identity_key', "
            "'project_director_relationship_status', 'external_identity_key', "
            "'external_institution', 'scientific_status')",
            name="ck_field_overrides_field_path",
        ),
        CheckConstraint(
            "scope IN ('global_identity', 'record', 'document', 'relationship', 'period')",
            name="ck_field_overrides_scope",
        ),
        CheckConstraint(
            "(scope = 'record' AND document_key IS NULL AND period_id IS NULL AND relationship_key IS NULL) OR "
            "(scope = 'document' AND NULLIF(BTRIM(document_key), '') IS NOT NULL AND period_id IS NULL AND relationship_key IS NULL) OR "
            "(scope = 'period' AND document_key IS NULL AND period_id IS NOT NULL AND relationship_key IS NULL) OR "
            "(scope = 'relationship' AND document_key IS NULL AND period_id IS NULL AND NULLIF(BTRIM(relationship_key), '') IS NOT NULL) OR "
            "(scope = 'global_identity' AND document_key IS NULL AND period_id IS NULL AND relationship_key IS NULL "
            "AND field_path IN ('canonical_identity_key', 'canonical_name'))",
            name="ck_field_overrides_scope_context",
        ),
        CheckConstraint("jsonb_typeof(projected_value) = 'object'", name="ck_field_overrides_value_object"),
        CheckConstraint("value_version = 1", name="ck_field_overrides_value_version"),
        Index("ix_field_overrides_target", "stable_target_key", "field_path", "is_active"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    review_item_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("review_items.id", name="fk_field_overrides_review_item_id", ondelete="RESTRICT"),
        nullable=False,
    )
    decision_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("review_decisions.id", name="fk_field_overrides_decision_id", ondelete="RESTRICT"),
        nullable=False,
    )
    stable_target_key: Mapped[str] = mapped_column(String(128), nullable=False)
    target_table: Mapped[str] = mapped_column(String(80), nullable=False)
    target_pk: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    field_path: Mapped[str] = mapped_column(String(120), nullable=False)
    value_schema: Mapped[str] = mapped_column(String(80), nullable=False)
    value_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default=text("1"))
    projected_value: Mapped[dict[str, object]] = mapped_column(JSON_DOCUMENT, nullable=False)
    scope: Mapped[str] = mapped_column(String(30), nullable=False)
    document_key: Mapped[str | None] = mapped_column(String(900), nullable=True)
    period_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    relationship_key: Mapped[str | None] = mapped_column(String(320), nullable=True)
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now_utc, server_default=text("CURRENT_TIMESTAMP")
    )
    superseded_by_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("field_overrides.id", name="fk_field_overrides_superseded_by_id", ondelete="RESTRICT"),
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


Index(
    "uq_field_overrides_active_target_field_scope_context",
    FieldOverride.stable_target_key,
    FieldOverride.field_path,
    FieldOverride.scope,
    func.coalesce(FieldOverride.document_key, ""),
    func.coalesce(FieldOverride.period_id, -1),
    func.coalesce(FieldOverride.relationship_key, ""),
    unique=True,
    postgresql_where=text("is_active"),
)
