from __future__ import annotations

import importlib
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

from sqlalchemy import (
    BigInteger,
    Boolean,
    CHAR,
    CheckConstraint,
    Column,
    DateTime,
    Integer,
    JSON,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import class_mapper


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import Base
from app.models.human_review_enums import (
    AuditEventType,
    B2BCapability,
    CanonicalIdentityOrigin,
    CanonicalIdentityStatus,
    CanonicalIdentityType,
    DecisionLifecycle,
    DecisionScope,
    OverrideField,
    OverrideScope,
    PersonAliasClass,
    PersonAliasScope,
    PersonAliasStatus,
    ReviewCaseStatus,
    ReviewCaseType,
    ReviewActorType,
    ReviewDecisionType,
    ReviewTargetTable,
    ScientificStatus,
)


MODEL_NAMES = (
    "UserB2BCapability",
    "ReviewItem",
    "ReviewDecision",
    "CanonicalIdentity",
    "PersonAlias",
    "FieldOverride",
    "AuditEvent",
)

EXPECTED_COLUMNS = {
    "UserB2BCapability": (
        "id", "user_id", "capability", "is_active", "approval_reference",
        "approved_input_sha256", "assigned_by_identifier", "assigned_at",
        "revoked_at", "revocation_reason", "version",
    ),
    "ReviewItem": (
        "id", "case_type", "stable_target_key", "target_table", "target_pk",
        "scope_faculty_id", "scope_career_id", "scope_resolution_reason",
        "document_key", "source_revision", "source_page", "source_section",
        "row_or_block_id", "field_path", "raw_value_sha256", "period_id",
        "relationship_key", "case_status", "scientific_status",
        "automatic_priority", "manual_priority", "possible_kpi_impact",
        "current_decision_id", "version", "created_at", "updated_at",
    ),
    "ReviewDecision": (
        "id", "review_item_id", "sequence", "decision_type",
        "decision_lifecycle", "scope", "payload_schema", "payload_version",
        "payload", "reason", "actor_type", "actor_user_id", "actor_identifier",
        "actor_capability", "decided_at", "expected_case_version",
        "previous_decision_id", "corrects_decision_id", "locks_projection",
        "created_at",
    ),
    "CanonicalIdentity": (
        "id", "canonical_identity_key", "identity_type", "display_name", "status",
        "origin", "created_by_decision_id", "superseded_by_id", "version",
        "created_at", "updated_at",
    ),
    "PersonAlias": (
        "id", "alias_original", "alias_normalized", "alias_class",
        "canonical_identity_id", "decision_id", "scope", "status",
        "superseded_by_id", "version", "created_at", "updated_at",
    ),
    "FieldOverride": (
        "id", "review_item_id", "decision_id", "stable_target_key", "target_table",
        "target_pk", "field_path", "value_schema", "value_version",
        "projected_value", "scope", "document_key", "period_id",
        "relationship_key", "locked", "is_active", "valid_from",
        "superseded_by_id", "version", "created_at", "updated_at",
    ),
    "AuditEvent": (
        "id", "event_type", "aggregate_type", "aggregate_key", "review_item_id",
        "actor_user_id", "actor_identifier", "actor_capability", "occurred_at",
        "payload_schema", "payload_version", "payload", "correlation_id",
        "request_id", "previous_event_id", "corrects_event_id",
        "previous_event_hash", "event_hash", "created_at",
    ),
}

TABLE_NAMES = {
    "UserB2BCapability": "user_b2b_capabilities",
    "ReviewItem": "review_items",
    "ReviewDecision": "review_decisions",
    "CanonicalIdentity": "canonical_identities",
    "PersonAlias": "person_aliases",
    "FieldOverride": "field_overrides",
    "AuditEvent": "audit_events",
}


EXPECTED_TYPE_GROUPS = {
    "UserB2BCapability": {
        ("id",): ("uuid", True),
        ("user_id", "version"): ("integer",),
        ("capability",): ("varchar", 40),
        ("is_active",): ("boolean",),
        ("approval_reference",): ("varchar", 240),
        ("approved_input_sha256",): ("char", 64),
        ("assigned_by_identifier",): ("varchar", 180),
        ("assigned_at", "revoked_at"): ("datetime", True),
        ("revocation_reason",): ("text",),
    },
    "ReviewItem": {
        ("id", "current_decision_id"): ("uuid", True),
        ("case_type",): ("varchar", 60),
        ("stable_target_key",): ("varchar", 128),
        ("target_table",): ("varchar", 80),
        ("target_pk",): ("big_integer",),
        ("scope_faculty_id", "scope_career_id"): ("integer",),
        ("scope_resolution_reason",): ("varchar", 80),
        ("document_key",): ("varchar", 900),
        ("source_revision", "source_section", "field_path"): ("varchar", 120),
        ("source_page", "period_id", "version"): ("integer",),
        ("row_or_block_id",): ("text",),
        ("raw_value_sha256",): ("char", 64),
        ("relationship_key",): ("varchar", 320),
        ("case_status", "scientific_status"): ("varchar", 40),
        ("automatic_priority", "manual_priority"): ("small_integer",),
        ("possible_kpi_impact",): ("boolean",),
        ("created_at", "updated_at"): ("datetime", True),
    },
    "ReviewDecision": {
        ("id", "review_item_id", "previous_decision_id", "corrects_decision_id"): ("uuid", True),
        ("sequence", "actor_user_id", "expected_case_version"): ("integer",),
        ("decision_type", "actor_capability"): ("varchar", 40),
        ("decision_lifecycle", "scope", "actor_type"): ("varchar", 30),
        ("payload_schema",): ("varchar", 80),
        ("payload_version",): ("small_integer",),
        ("payload",): ("json_jsonb",),
        ("reason",): ("text",),
        ("actor_identifier",): ("varchar", 180),
        ("decided_at", "created_at"): ("datetime", True),
        ("locks_projection",): ("boolean",),
    },
    "CanonicalIdentity": {
        ("id", "created_by_decision_id", "superseded_by_id"): ("uuid", True),
        ("canonical_identity_key",): ("varchar", 320),
        ("identity_type",): ("varchar", 40),
        ("display_name",): ("varchar", 220),
        ("status", "origin"): ("varchar", 30),
        ("version",): ("integer",),
        ("created_at", "updated_at"): ("datetime", True),
    },
    "PersonAlias": {
        ("id", "canonical_identity_id", "decision_id", "superseded_by_id"): ("uuid", True),
        ("alias_original", "alias_normalized"): ("varchar", 320),
        ("alias_class",): ("varchar", 40),
        ("scope", "status"): ("varchar", 30),
        ("version",): ("integer",),
        ("created_at", "updated_at"): ("datetime", True),
    },
    "FieldOverride": {
        ("id", "review_item_id", "decision_id", "superseded_by_id"): ("uuid", True),
        ("stable_target_key",): ("varchar", 128),
        ("target_table", "value_schema"): ("varchar", 80),
        ("target_pk",): ("big_integer",),
        ("field_path",): ("varchar", 120),
        ("value_version",): ("small_integer",),
        ("projected_value",): ("json_jsonb",),
        ("scope",): ("varchar", 30),
        ("document_key",): ("varchar", 900),
        ("period_id", "version"): ("integer",),
        ("relationship_key",): ("varchar", 320),
        ("locked", "is_active"): ("boolean",),
        ("valid_from", "created_at", "updated_at"): ("datetime", True),
    },
    "AuditEvent": {
        ("id", "review_item_id", "correlation_id", "request_id", "previous_event_id", "corrects_event_id"): ("uuid", True),
        ("event_type", "aggregate_type"): ("varchar", 60),
        ("aggregate_key",): ("varchar", 320),
        ("actor_user_id",): ("integer",),
        ("actor_identifier",): ("varchar", 180),
        ("actor_capability",): ("varchar", 40),
        ("occurred_at", "created_at"): ("datetime", True),
        ("payload_schema",): ("varchar", 80),
        ("payload_version",): ("small_integer",),
        ("payload",): ("json_jsonb",),
        ("previous_event_hash", "event_hash"): ("char", 64),
    },
}


def _normalize_sql(value: object) -> str:
    return " ".join(str(value).split())


def _enum_in(column_name: str, enum_type: type) -> str:
    values = ", ".join(f"'{member.value}'" for member in enum_type)
    return f"{column_name} IN ({values})"


def _enum_members_in(column_name: str, members: tuple[object, ...]) -> str:
    values = ", ".join(f"'{member.value}'" for member in members)
    return f"{column_name} IN ({values})"


def _enum_default(member: object) -> tuple[object, ...]:
    return (member.value, f"'{member.value}'", None)


ACTIVE_REVIEW_CASE_STATUSES = (
    ReviewCaseStatus.PENDING,
    ReviewCaseStatus.IN_REVIEW,
    ReviewCaseStatus.AWAITING_GESTOR_APPROVAL,
    ReviewCaseStatus.REOPENED,
    ReviewCaseStatus.CONFLICTED,
)


def _expected_checks() -> dict[str, dict[str, str]]:
    capability_values = ", ".join(f"'{member.value}'" for member in B2BCapability)
    human_actor = ReviewActorType.HUMAN.value
    legacy_actor = ReviewActorType.LEGACY.value
    approved_lifecycle = DecisionLifecycle.APPROVED.value
    record_scope = OverrideScope.RECORD.value
    document_scope = OverrideScope.DOCUMENT.value
    period_scope = OverrideScope.PERIOD.value
    relationship_scope = OverrideScope.RELATIONSHIP.value
    global_identity_scope = OverrideScope.GLOBAL_IDENTITY.value
    compatible_global_fields = (
        OverrideField.CANONICAL_IDENTITY_KEY,
        OverrideField.CANONICAL_NAME,
    )
    compatible_global_values = ", ".join(
        f"'{member.value}'" for member in compatible_global_fields
    )
    audit_corrected = AuditEventType.AUDIT_CORRECTED.value
    return {
        "UserB2BCapability": {
            "ck_user_b2b_capabilities_capability": _enum_in("capability", B2BCapability),
            "ck_user_b2b_capabilities_hash": "approved_input_sha256 ~ '^[0-9a-f]{64}$'",
            "ck_user_b2b_capabilities_active_not_revoked": "NOT is_active OR revoked_at IS NULL",
            "ck_user_b2b_capabilities_revoked_complete":
                "is_active OR (revoked_at IS NOT NULL AND NULLIF(BTRIM(revocation_reason), '') IS NOT NULL)",
        },
        "ReviewItem": {
            "ck_review_items_case_type": _enum_in("case_type", ReviewCaseType),
            "ck_review_items_target_table": _enum_in("target_table", ReviewTargetTable),
            "ck_review_items_case_status": _enum_in("case_status", ReviewCaseStatus),
            "ck_review_items_scientific_status": _enum_in("scientific_status", ScientificStatus),
            "ck_review_items_raw_hash": "raw_value_sha256 ~ '^[0-9a-f]{64}$'",
            "ck_review_items_scope_hierarchy":
                "scope_career_id IS NULL OR scope_faculty_id IS NOT NULL",
            "ck_review_items_scope_resolution":
                "(scope_faculty_id IS NULL AND scope_career_id IS NULL AND "
                "scope_resolution_reason IS NOT NULL AND scope_resolution_reason IN "
                "('unresolved_no_persisted_scope', 'unresolved_cross_faculty', "
                "'unresolved_missing_target')) OR (scope_faculty_id IS NOT NULL AND "
                "scope_resolution_reason IS NULL)",
        },
        "ReviewDecision": {
            "ck_review_decisions_decision_type": _enum_in("decision_type", ReviewDecisionType),
            "ck_review_decisions_lifecycle": _enum_in("decision_lifecycle", DecisionLifecycle),
            "ck_review_decisions_scope": _enum_in("scope", DecisionScope),
            "ck_review_decisions_payload_object": "jsonb_typeof(payload) = 'object'",
            "ck_review_decisions_payload_version": "payload_version = 1",
            "ck_review_decisions_positive_sequence": "sequence > 0",
            "ck_review_decisions_actor_shape":
                f"(actor_type = '{human_actor}' AND actor_user_id IS NOT NULL AND actor_capability IN "
                f"({capability_values})) OR (actor_type = '{legacy_actor}' AND actor_user_id IS NULL AND "
                "actor_capability IS NULL AND NULLIF(BTRIM(actor_identifier), '') IS NOT NULL)",
            "ck_review_decisions_approved_locks_projection":
                f"decision_lifecycle <> '{approved_lifecycle}' OR locks_projection",
            "ck_review_decisions_previous_not_self":
                "previous_decision_id IS NULL OR previous_decision_id <> id",
            "ck_review_decisions_corrects_not_self":
                "corrects_decision_id IS NULL OR corrects_decision_id <> id",
        },
        "CanonicalIdentity": {
            "ck_canonical_identities_identity_type": _enum_in("identity_type", CanonicalIdentityType),
            "ck_canonical_identities_status": _enum_in("status", CanonicalIdentityStatus),
            "ck_canonical_identities_origin": _enum_in("origin", CanonicalIdentityOrigin),
        },
        "PersonAlias": {
            "ck_person_aliases_alias_class": f"alias_class = '{PersonAliasClass.PERSON_NAME.value}'",
            "ck_person_aliases_scope": f"scope = '{PersonAliasScope.GLOBAL_IDENTITY.value}'",
            "ck_person_aliases_status": _enum_in("status", PersonAliasStatus),
        },
        "FieldOverride": {
            "ck_field_overrides_target_table": _enum_in("target_table", ReviewTargetTable),
            "ck_field_overrides_field_path": _enum_in("field_path", OverrideField),
            "ck_field_overrides_scope": _enum_in("scope", OverrideScope),
            "ck_field_overrides_scope_context":
                f"(scope = '{record_scope}' AND document_key IS NULL AND period_id IS NULL AND relationship_key IS NULL) OR "
                f"(scope = '{document_scope}' AND NULLIF(BTRIM(document_key), '') IS NOT NULL AND period_id IS NULL AND relationship_key IS NULL) OR "
                f"(scope = '{period_scope}' AND document_key IS NULL AND period_id IS NOT NULL AND relationship_key IS NULL) OR "
                f"(scope = '{relationship_scope}' AND document_key IS NULL AND period_id IS NULL AND NULLIF(BTRIM(relationship_key), '') IS NOT NULL) OR "
                f"(scope = '{global_identity_scope}' AND document_key IS NULL AND period_id IS NULL AND relationship_key IS NULL "
                f"AND field_path IN ({compatible_global_values}))",
            "ck_field_overrides_value_object": "jsonb_typeof(projected_value) = 'object'",
            "ck_field_overrides_value_version": "value_version = 1",
        },
        "AuditEvent": {
            "ck_audit_events_event_type": _enum_in("event_type", AuditEventType),
            "ck_audit_events_actor_capability":
                f"actor_capability IS NULL OR actor_capability IN ({capability_values})",
            "ck_audit_events_payload_object": "jsonb_typeof(payload) = 'object'",
            "ck_audit_events_payload_version": "payload_version = 1",
            "ck_audit_events_hashes":
                "event_hash ~ '^[0-9a-f]{64}$' AND (previous_event_hash IS NULL OR "
                "previous_event_hash ~ '^[0-9a-f]{64}$')",
            "ck_audit_events_previous_not_self":
                "previous_event_id IS NULL OR previous_event_id <> id",
            "ck_audit_events_corrects_not_self":
                "corrects_event_id IS NULL OR corrects_event_id <> id",
            "ck_audit_events_correction_target":
                f"event_type <> '{audit_corrected}' OR corrects_event_id IS NOT NULL",
        },
    }


EXPECTED_INDEXES = {
    "UserB2BCapability": {
        "uq_user_b2b_capabilities_active_user": (True, ("user_id",), "is_active"),
        "ix_user_b2b_capabilities_capability_active": (False, ("capability", "is_active"), None),
    },
    "ReviewItem": {
        "uq_review_items_active_case_target": (
            True,
            ("case_type", "stable_target_key"),
            _enum_members_in("case_status", ACTIVE_REVIEW_CASE_STATUSES),
        ),
        "ix_review_items_queue": (False, ("case_type", "case_status", "automatic_priority", "created_at"), None),
        "ix_review_items_target": (False, ("target_table", "target_pk"), None),
        "ix_review_items_document": (False, ("document_key",), None),
        "ix_review_items_period": (False, ("period_id",), None),
        "ix_review_items_current_decision": (False, ("current_decision_id",), None),
        "ix_review_items_scope_faculty_queue": (
            False,
            ("scope_faculty_id", "case_status", "automatic_priority", "created_at", "id"),
            None,
        ),
        "ix_review_items_scope_career_queue": (
            False,
            ("scope_career_id", "case_status", "automatic_priority", "created_at", "id"),
            None,
        ),
    },
    "ReviewDecision": {
        "ix_review_decisions_case": (False, ("review_item_id", "decided_at"), None),
        "ix_review_decisions_lifecycle": (False, ("decision_lifecycle",), None),
        "ix_review_decisions_actor": (False, ("actor_user_id", "decided_at"), None),
        "ix_review_decisions_previous": (False, ("previous_decision_id",), None),
        "ix_review_decisions_corrects": (False, ("corrects_decision_id",), None),
    },
    "CanonicalIdentity": {},
    "PersonAlias": {
        "uq_person_aliases_active_normalized_class": (
            True,
            ("alias_normalized", "alias_class"),
            f"status = '{PersonAliasStatus.ACTIVE.value}'",
        ),
    },
    "FieldOverride": {
        "ix_field_overrides_target": (False, ("stable_target_key", "field_path", "is_active"), None),
        "uq_field_overrides_active_target_field_scope_context": (
            True,
            (
                "stable_target_key", "field_path", "scope", "coalesce(document_key, '')",
                "coalesce(period_id, -1)", "coalesce(relationship_key, '')",
            ),
            "is_active",
        ),
    },
    "AuditEvent": {
        "ix_audit_events_occurred_at": (False, ("occurred_at",), None),
        "ix_audit_events_review_item": (False, ("review_item_id",), None),
        "ix_audit_events_actor": (False, ("actor_user_id", "occurred_at"), None),
        "ix_audit_events_event_type": (False, ("event_type", "occurred_at"), None),
        "ix_audit_events_aggregate": (False, ("aggregate_type", "aggregate_key"), None),
        "ix_audit_events_correlation": (False, ("correlation_id",), None),
        "ix_audit_events_corrects": (False, ("corrects_event_id",), None),
    },
}


EXPECTED_FOREIGN_KEYS = {
    "UserB2BCapability": {
        "fk_user_b2b_capabilities_user_id_users": ("user_id", "users.id", "RESTRICT", None, None, False),
    },
    "ReviewItem": {
        "fk_review_items_current_decision_id_review_decisions":
            ("current_decision_id", "review_decisions.id", "RESTRICT", True, "DEFERRED", True),
        "fk_review_items_scope_faculty_id_faculties":
            ("scope_faculty_id", "faculties.id", "RESTRICT", None, None, False),
        "fk_review_items_scope_career_faculty_careers": (
            ("scope_career_id", "scope_faculty_id"),
            ("careers.id", "careers.faculty_id"),
            "RESTRICT",
            None,
            None,
            False,
        ),
    },
    "ReviewDecision": {
        "fk_review_decisions_review_item_id_review_items":
            ("review_item_id", "review_items.id", "RESTRICT", None, None, False),
        "fk_review_decisions_actor_user_id_users":
            ("actor_user_id", "users.id", "RESTRICT", None, None, False),
        "fk_review_decisions_previous_decision_id":
            ("previous_decision_id", "review_decisions.id", "RESTRICT", None, None, False),
        "fk_review_decisions_corrects_decision_id":
            ("corrects_decision_id", "review_decisions.id", "RESTRICT", None, None, False),
    },
    "CanonicalIdentity": {
        "fk_canonical_identities_created_by_decision_id":
            ("created_by_decision_id", "review_decisions.id", "RESTRICT", None, None, False),
        "fk_canonical_identities_superseded_by_id":
            ("superseded_by_id", "canonical_identities.id", "RESTRICT", None, None, False),
    },
    "PersonAlias": {
        "fk_person_aliases_canonical_identity_id":
            ("canonical_identity_id", "canonical_identities.id", "RESTRICT", None, None, False),
        "fk_person_aliases_decision_id":
            ("decision_id", "review_decisions.id", "RESTRICT", None, None, False),
        "fk_person_aliases_superseded_by_id":
            ("superseded_by_id", "person_aliases.id", "RESTRICT", None, None, False),
    },
    "FieldOverride": {
        "fk_field_overrides_review_item_id":
            ("review_item_id", "review_items.id", "RESTRICT", None, None, False),
        "fk_field_overrides_decision_id":
            ("decision_id", "review_decisions.id", "RESTRICT", None, None, False),
        "fk_field_overrides_superseded_by_id":
            ("superseded_by_id", "field_overrides.id", "RESTRICT", None, None, False),
    },
    "AuditEvent": {
        "fk_audit_events_review_item_id":
            ("review_item_id", "review_items.id", "RESTRICT", None, None, False),
        "fk_audit_events_actor_user_id":
            ("actor_user_id", "users.id", "RESTRICT", None, None, False),
        "fk_audit_events_previous_event_id":
            ("previous_event_id", "audit_events.id", "RESTRICT", None, None, False),
        "fk_audit_events_corrects_event_id":
            ("corrects_event_id", "audit_events.id", "RESTRICT", None, None, False),
    },
}


EXPECTED_UNIQUE_CONSTRAINTS = {
    "UserB2BCapability": {},
    "ReviewItem": {},
    "ReviewDecision": {"uq_review_decisions_item_sequence": ("review_item_id", "sequence")},
    "CanonicalIdentity": {"uq_canonical_identities_key": ("canonical_identity_key",)},
    "PersonAlias": {},
    "FieldOverride": {},
    "AuditEvent": {"uq_audit_events_event_hash": ("event_hash",)},
}


EXPECTED_DEFAULTS = {
    ("UserB2BCapability", "is_active"): (True, "true", None),
    ("UserB2BCapability", "assigned_at"): (("callable", "_now_utc"), "CURRENT_TIMESTAMP", None),
    ("UserB2BCapability", "version"): (1, "1", None),
    ("ReviewItem", "case_status"): _enum_default(ReviewCaseStatus.PENDING),
    ("ReviewItem", "scientific_status"): _enum_default(ScientificStatus.PENDING),
    ("ReviewItem", "automatic_priority"): (0, "0", None),
    ("ReviewItem", "possible_kpi_impact"): (False, "false", None),
    ("ReviewItem", "version"): (1, "1", None),
    ("ReviewItem", "created_at"): (("callable", "_now_utc"), "CURRENT_TIMESTAMP", None),
    ("ReviewItem", "updated_at"): (("callable", "_now_utc"), "CURRENT_TIMESTAMP", ("callable", "_now_utc")),
    ("ReviewDecision", "payload_version"): (1, "1", None),
    ("ReviewDecision", "locks_projection"): (False, "false", None),
    ("ReviewDecision", "decided_at"): (("callable", "_now_utc"), "CURRENT_TIMESTAMP", None),
    ("ReviewDecision", "created_at"): (("callable", "_now_utc"), "CURRENT_TIMESTAMP", None),
    ("CanonicalIdentity", "status"): _enum_default(CanonicalIdentityStatus.ACTIVE),
    ("CanonicalIdentity", "version"): (1, "1", None),
    ("CanonicalIdentity", "created_at"): (("callable", "_now_utc"), "CURRENT_TIMESTAMP", None),
    ("CanonicalIdentity", "updated_at"): (("callable", "_now_utc"), "CURRENT_TIMESTAMP", ("callable", "_now_utc")),
    ("PersonAlias", "alias_class"): _enum_default(PersonAliasClass.PERSON_NAME),
    ("PersonAlias", "scope"): _enum_default(PersonAliasScope.GLOBAL_IDENTITY),
    ("PersonAlias", "status"): _enum_default(PersonAliasStatus.ACTIVE),
    ("PersonAlias", "version"): (1, "1", None),
    ("PersonAlias", "created_at"): (("callable", "_now_utc"), "CURRENT_TIMESTAMP", None),
    ("PersonAlias", "updated_at"): (("callable", "_now_utc"), "CURRENT_TIMESTAMP", ("callable", "_now_utc")),
    ("FieldOverride", "value_version"): (1, "1", None),
    ("FieldOverride", "locked"): (True, "true", None),
    ("FieldOverride", "is_active"): (True, "true", None),
    ("FieldOverride", "valid_from"): (("callable", "_now_utc"), "CURRENT_TIMESTAMP", None),
    ("FieldOverride", "version"): (1, "1", None),
    ("FieldOverride", "created_at"): (("callable", "_now_utc"), "CURRENT_TIMESTAMP", None),
    ("FieldOverride", "updated_at"): (("callable", "_now_utc"), "CURRENT_TIMESTAMP", ("callable", "_now_utc")),
    ("AuditEvent", "payload_version"): (1, "1", None),
    ("AuditEvent", "occurred_at"): (("callable", "_now_utc"), "CURRENT_TIMESTAMP", None),
    ("AuditEvent", "created_at"): (("callable", "_now_utc"), "CURRENT_TIMESTAMP", None),
}


def _models() -> dict[str, type]:
    try:
        package = importlib.import_module("app.models")
        return {name: getattr(package, name) for name in MODEL_NAMES}
    except (ModuleNotFoundError, AttributeError) as error:
        raise AssertionError(
            "B2B.1 foundational ORM model modules/classes are not implemented"
        ) from error


def _table(model: type):
    return class_mapper(model).local_table


def _constraint_sql(table, name: str) -> str:
    constraint = next(item for item in table.constraints if item.name == name)
    return str(constraint.sqltext)


def _type_spec(column_type: object) -> tuple[object, ...]:
    if isinstance(column_type, Uuid):
        return ("uuid", column_type.as_uuid)
    if isinstance(column_type, SmallInteger):
        return ("small_integer",)
    if isinstance(column_type, BigInteger):
        return ("big_integer",)
    if isinstance(column_type, Integer):
        return ("integer",)
    if isinstance(column_type, Boolean):
        return ("boolean",)
    if isinstance(column_type, DateTime):
        return ("datetime", column_type.timezone)
    if isinstance(column_type, JSON):
        variant = column_type._variant_mapping.get("postgresql")
        return ("json_jsonb",) if isinstance(variant, JSONB) else ("json",)
    if isinstance(column_type, Text):
        return ("text",)
    if isinstance(column_type, CHAR):
        return ("char", column_type.length)
    if isinstance(column_type, String):
        return ("varchar", column_type.length)
    raise AssertionError(f"unclassified SQLAlchemy type: {column_type!r}")


def _expanded_type_specs(groups: dict[tuple[str, ...], tuple[object, ...]]) -> dict[str, tuple[object, ...]]:
    expanded: dict[str, tuple[object, ...]] = {}
    for column_names, type_spec in groups.items():
        for column_name in column_names:
            if column_name in expanded:
                raise AssertionError(f"duplicate expected type for {column_name}")
            expanded[column_name] = type_spec
    return expanded


def _index_expression(table_name: str, expression: object) -> str:
    if isinstance(expression, Column):
        return expression.name
    compiled = expression.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    )
    return _normalize_sql(compiled).replace(f"{table_name}.", "")


def _default_spec(default: object | None) -> object | None:
    if default is None:
        return None
    argument = default.arg
    if callable(argument):
        return ("callable", argument.__name__)
    return argument


class HumanReviewModelMetadataTests(unittest.TestCase):
    def test_expected_metadata_oracle_tracks_review_actor_enum_mutation(self) -> None:
        original_value = ReviewActorType.HUMAN.value
        original_oracle = _expected_checks()["ReviewDecision"]["ck_review_decisions_actor_shape"]
        with patch.object(ReviewActorType.HUMAN, "_value_", "human_contract_changed"):
            mutated_oracle = _expected_checks()["ReviewDecision"]["ck_review_decisions_actor_shape"]
        self.assertNotEqual(mutated_oracle, original_oracle)
        self.assertIn("human_contract_changed", mutated_oracle)
        self.assertEqual(ReviewActorType.HUMAN.value, original_value)

    def test_complete_check_inventory_and_normalized_sql(self) -> None:
        models = _models()
        for model_name, expected in _expected_checks().items():
            table = _table(models[model_name])
            actual = {
                constraint.name: _normalize_sql(constraint.sqltext)
                for constraint in table.constraints
                if isinstance(constraint, CheckConstraint)
            }
            with self.subTest(model=model_name):
                self.assertEqual(actual, expected)

        audit_capability = _expected_checks()["AuditEvent"]["ck_audit_events_actor_capability"]
        self.assertNotIn("CAREER_MANAGER", audit_capability)
        self.assertNotIn("research_manager", audit_capability)

    def test_exact_scalar_type_family_for_every_column(self) -> None:
        for model_name, model in _models().items():
            table = _table(model)
            expected = _expanded_type_specs(EXPECTED_TYPE_GROUPS[model_name])
            actual = {column.name: _type_spec(column.type) for column in table.columns}
            self.assertEqual(actual, expected, model_name)

    def test_complete_foreign_key_inventory_and_behavior(self) -> None:
        for model_name, model in _models().items():
            actual: dict[str, tuple[object, ...]] = {}
            for constraint in _table(model).foreign_key_constraints:
                elements = tuple(constraint.elements)
                parent: object = elements[0].parent.name
                target: object = elements[0].target_fullname
                if len(elements) > 1:
                    parent = tuple(element.parent.name for element in elements)
                    target = tuple(element.target_fullname for element in elements)
                actual[constraint.name] = (
                    parent,
                    target,
                    constraint.ondelete,
                    constraint.deferrable,
                    constraint.initially,
                    constraint.use_alter,
                )
            self.assertEqual(actual, EXPECTED_FOREIGN_KEYS[model_name], model_name)

    def test_complete_index_inventory_order_uniqueness_and_predicates(self) -> None:
        for model_name, model in _models().items():
            table = _table(model)
            actual: dict[str, tuple[object, ...]] = {}
            for index in table.indexes:
                expressions = tuple(
                    _index_expression(table.name, expression)
                    for expression in index.expressions
                )
                predicate = index.dialect_options["postgresql"]["where"]
                actual[index.name] = (
                    bool(index.unique),
                    expressions,
                    None if predicate is None else _normalize_sql(predicate),
                )
            self.assertEqual(actual, EXPECTED_INDEXES[model_name], model_name)

    def test_complete_named_unique_constraint_inventory(self) -> None:
        for model_name, model in _models().items():
            actual = {
                constraint.name: tuple(column.name for column in constraint.columns)
                for constraint in _table(model).constraints
                if isinstance(constraint, UniqueConstraint)
            }
            self.assertEqual(actual, EXPECTED_UNIQUE_CONSTRAINTS[model_name], model_name)

    def test_important_client_server_and_onupdate_defaults_are_exact(self) -> None:
        models = _models()
        for (model_name, column_name), expected in EXPECTED_DEFAULTS.items():
            column = _table(models[model_name]).c[column_name]
            actual = (
                _default_spec(column.default),
                None if column.server_default is None else _normalize_sql(column.server_default.arg),
                _default_spec(column.onupdate),
            )
            with self.subTest(model=model_name, column=column_name):
                self.assertEqual(actual, expected)

    def test_models_are_exported_and_register_exact_tables(self) -> None:
        models = _models()
        package = importlib.import_module("app.models")
        for name in MODEL_NAMES:
            self.assertIn(name, package.__all__)
            self.assertIs(getattr(package, name), models[name])
        self.assertTrue(set(TABLE_NAMES.values()).issubset(Base.metadata.tables))

    def test_exact_column_sets_and_uuid_primary_keys(self) -> None:
        for name, model in _models().items():
            with self.subTest(model=name):
                table = _table(model)
                self.assertEqual(tuple(table.columns.keys()), EXPECTED_COLUMNS[name])
                self.assertEqual(table.name, TABLE_NAMES[name])
                self.assertEqual(tuple(column.name for column in table.primary_key), ("id",))
                self.assertIsInstance(table.c.id.type, Uuid)
                self.assertTrue(table.c.id.type.as_uuid)
                self.assertFalse(table.c.id.nullable)
                self.assertIsNotNone(table.c.id.default)

    def test_exact_string_lengths_and_scalar_types(self) -> None:
        models = _models()
        expected_lengths = {
            "UserB2BCapability": {"capability": 40, "approval_reference": 240,
                "approved_input_sha256": 64, "assigned_by_identifier": 180},
            "ReviewItem": {"case_type": 60, "stable_target_key": 128,
                "target_table": 80, "scope_resolution_reason": 80,
                "document_key": 900, "source_revision": 120,
                "source_section": 120, "field_path": 120,
                "raw_value_sha256": 64, "relationship_key": 320,
                "case_status": 40, "scientific_status": 40},
            "ReviewDecision": {"decision_type": 40, "decision_lifecycle": 30,
                "scope": 30, "payload_schema": 80, "actor_type": 30,
                "actor_identifier": 180, "actor_capability": 40},
            "CanonicalIdentity": {"canonical_identity_key": 320, "identity_type": 40,
                "display_name": 220, "status": 30, "origin": 30},
            "PersonAlias": {"alias_original": 320, "alias_normalized": 320,
                "alias_class": 40, "scope": 30, "status": 30},
            "FieldOverride": {"stable_target_key": 128, "target_table": 80,
                "field_path": 120, "value_schema": 80, "scope": 30,
                "document_key": 900, "relationship_key": 320},
            "AuditEvent": {"event_type": 60, "aggregate_type": 60,
                "aggregate_key": 320, "actor_identifier": 180,
                "actor_capability": 40, "payload_schema": 80,
                "previous_event_hash": 64, "event_hash": 64},
        }
        for model_name, columns in expected_lengths.items():
            table = _table(models[model_name])
            for column_name, length in columns.items():
                with self.subTest(model=model_name, column=column_name):
                    self.assertIsInstance(table.c[column_name].type, (String, CHAR))
                    self.assertEqual(table.c[column_name].type.length, length)

        review_items = _table(models["ReviewItem"])
        self.assertIsInstance(review_items.c.row_or_block_id.type, Text)
        self.assertIsNone(review_items.c.row_or_block_id.type.length)
        self.assertIsInstance(review_items.c.target_pk.type, BigInteger)
        self.assertIsInstance(review_items.c.automatic_priority.type, SmallInteger)
        self.assertIsInstance(review_items.c.manual_priority.type, SmallInteger)
        self.assertIsInstance(review_items.c.possible_kpi_impact.type, Boolean)
        self.assertIsInstance(review_items.c.source_page.type, Integer)
        self.assertIsInstance(_table(models["ReviewDecision"]).c.reason.type, Text)
        self.assertIsInstance(_table(models["UserB2BCapability"]).c.revocation_reason.type, Text)

    def test_nullability_matches_foundational_contract(self) -> None:
        models = _models()
        nullable = {
            "UserB2BCapability": {"revoked_at", "revocation_reason"},
            "ReviewItem": {"target_pk", "source_revision", "source_page", "period_id",
                "scope_faculty_id", "scope_career_id", "scope_resolution_reason",
                "relationship_key", "manual_priority", "current_decision_id"},
            "ReviewDecision": {"reason", "actor_user_id", "actor_capability",
                "previous_decision_id", "corrects_decision_id"},
            "CanonicalIdentity": {"created_by_decision_id", "superseded_by_id"},
            "PersonAlias": {"superseded_by_id"},
            "FieldOverride": {"target_pk", "document_key", "period_id",
                "relationship_key", "superseded_by_id"},
            "AuditEvent": {"review_item_id", "actor_user_id", "actor_capability",
                "request_id", "previous_event_id", "corrects_event_id",
                "previous_event_hash"},
        }
        for model_name, model in models.items():
            table = _table(model)
            actual = {column.name for column in table.columns if column.nullable}
            self.assertEqual(actual, nullable[model_name], model_name)

    def test_all_timestamps_are_timezone_aware(self) -> None:
        for model_name, model in _models().items():
            for column in _table(model).columns:
                if isinstance(column.type, DateTime):
                    with self.subTest(model=model_name, column=column.name):
                        self.assertTrue(column.type.timezone)
                        if not column.nullable:
                            self.assertIsNotNone(column.default)
                            self.assertIsNotNone(column.server_default)

    def test_json_columns_are_portable_json_with_postgresql_jsonb_variant(self) -> None:
        models = _models()
        for model_name, column_name in (
            ("ReviewDecision", "payload"),
            ("FieldOverride", "projected_value"),
            ("AuditEvent", "payload"),
        ):
            column_type = _table(models[model_name]).c[column_name].type
            self.assertIsInstance(column_type, JSON)
            self.assertIsInstance(column_type._variant_mapping["postgresql"], JSONB)

    def test_mutable_models_have_version_one_defaults(self) -> None:
        models = _models()
        for model_name in (
            "UserB2BCapability", "ReviewItem", "CanonicalIdentity",
            "PersonAlias", "FieldOverride",
        ):
            column = _table(models[model_name]).c.version
            self.assertFalse(column.nullable)
            self.assertEqual(column.default.arg, 1)
            self.assertEqual(str(column.server_default.arg), "1")
        self.assertNotIn("version", _table(models["ReviewDecision"]).c)
        self.assertNotIn("version", _table(models["AuditEvent"]).c)

    def test_named_foreign_keys_are_restrictive_and_current_pointer_is_deferred(self) -> None:
        models = _models()
        expected = {
            ("UserB2BCapability", "user_id"): "users.id",
            ("ReviewItem", "current_decision_id"): "review_decisions.id",
            ("ReviewDecision", "review_item_id"): "review_items.id",
            ("ReviewDecision", "actor_user_id"): "users.id",
            ("ReviewDecision", "previous_decision_id"): "review_decisions.id",
            ("ReviewDecision", "corrects_decision_id"): "review_decisions.id",
            ("CanonicalIdentity", "created_by_decision_id"): "review_decisions.id",
            ("CanonicalIdentity", "superseded_by_id"): "canonical_identities.id",
            ("PersonAlias", "canonical_identity_id"): "canonical_identities.id",
            ("PersonAlias", "decision_id"): "review_decisions.id",
            ("PersonAlias", "superseded_by_id"): "person_aliases.id",
            ("FieldOverride", "review_item_id"): "review_items.id",
            ("FieldOverride", "decision_id"): "review_decisions.id",
            ("FieldOverride", "superseded_by_id"): "field_overrides.id",
            ("AuditEvent", "review_item_id"): "review_items.id",
            ("AuditEvent", "actor_user_id"): "users.id",
            ("AuditEvent", "previous_event_id"): "audit_events.id",
            ("AuditEvent", "corrects_event_id"): "audit_events.id",
        }
        for (model_name, column_name), target in expected.items():
            fk = next(iter(_table(models[model_name]).c[column_name].foreign_keys))
            self.assertEqual(fk.target_fullname, target)
            self.assertIsNotNone(fk.constraint.name)
            self.assertEqual(fk.ondelete, "RESTRICT")
        pointer_fk = next(iter(_table(models["ReviewItem"]).c.current_decision_id.foreign_keys))
        self.assertTrue(pointer_fk.deferrable)
        self.assertEqual(pointer_fk.initially, "DEFERRED")
        self.assertTrue(pointer_fk.use_alter)

    def test_enum_hash_payload_actor_and_state_checks_are_named(self) -> None:
        models = _models()
        required_checks = {
            "UserB2BCapability": {
                "ck_user_b2b_capabilities_capability",
                "ck_user_b2b_capabilities_hash",
                "ck_user_b2b_capabilities_active_not_revoked",
                "ck_user_b2b_capabilities_revoked_complete",
            },
            "ReviewItem": {
                "ck_review_items_case_type", "ck_review_items_target_table",
                "ck_review_items_case_status", "ck_review_items_scientific_status",
                "ck_review_items_raw_hash", "ck_review_items_scope_hierarchy",
                "ck_review_items_scope_resolution",
            },
            "ReviewDecision": {
                "ck_review_decisions_decision_type",
                "ck_review_decisions_lifecycle", "ck_review_decisions_scope",
                "ck_review_decisions_payload_object", "ck_review_decisions_payload_version",
                "ck_review_decisions_positive_sequence", "ck_review_decisions_actor_shape",
                "ck_review_decisions_approved_locks_projection",
            },
            "CanonicalIdentity": {
                "ck_canonical_identities_identity_type", "ck_canonical_identities_status",
                "ck_canonical_identities_origin",
            },
            "PersonAlias": {
                "ck_person_aliases_alias_class", "ck_person_aliases_scope",
                "ck_person_aliases_status",
            },
            "FieldOverride": {
                "ck_field_overrides_target_table", "ck_field_overrides_field_path",
                "ck_field_overrides_scope", "ck_field_overrides_scope_context",
                "ck_field_overrides_value_object", "ck_field_overrides_value_version",
            },
            "AuditEvent": {
                "ck_audit_events_event_type", "ck_audit_events_payload_object",
                "ck_audit_events_payload_version", "ck_audit_events_hashes",
                "ck_audit_events_previous_not_self", "ck_audit_events_corrects_not_self",
                "ck_audit_events_correction_target",
            },
        }
        for model_name, required in required_checks.items():
            actual = {constraint.name for constraint in _table(models[model_name]).constraints}
            self.assertTrue(required.issubset(actual), f"{model_name}: {required - actual}")

        field_sql = _constraint_sql(
            _table(models["FieldOverride"]), "ck_field_overrides_field_path"
        )
        for forbidden in ("raw_", "person_key", "parsed_payload", "import_job_id",
                          "production_id", "research_entity_id"):
            self.assertNotIn(f"'{forbidden}'", field_sql)
        self.assertEqual(field_sql.count("'"), 18)

    def test_unique_constraints_and_required_indexes_are_named(self) -> None:
        models = _models()
        expected_indexes = {
            "UserB2BCapability": {
                "uq_user_b2b_capabilities_active_user",
                "ix_user_b2b_capabilities_capability_active",
            },
            "ReviewItem": {
                "uq_review_items_active_case_target", "ix_review_items_queue",
                "ix_review_items_target", "ix_review_items_document",
                "ix_review_items_period", "ix_review_items_current_decision",
                "ix_review_items_scope_faculty_queue",
                "ix_review_items_scope_career_queue",
            },
            "ReviewDecision": {
                "ix_review_decisions_case", "ix_review_decisions_lifecycle",
                "ix_review_decisions_actor", "ix_review_decisions_previous",
                "ix_review_decisions_corrects",
            },
            "PersonAlias": {"uq_person_aliases_active_normalized_class"},
            "FieldOverride": {
                "uq_field_overrides_active_target_field_scope_context",
                "ix_field_overrides_target",
            },
            "AuditEvent": {
                "ix_audit_events_occurred_at", "ix_audit_events_review_item",
                "ix_audit_events_actor", "ix_audit_events_event_type",
                "ix_audit_events_aggregate", "ix_audit_events_correlation",
                "ix_audit_events_corrects",
            },
        }
        for model_name, required in expected_indexes.items():
            actual = {index.name for index in _table(models[model_name]).indexes}
            self.assertTrue(required.issubset(actual), f"{model_name}: {required - actual}")

        review_item_indexes = {index.name: index for index in _table(models["ReviewItem"]).indexes}
        active = review_item_indexes["uq_review_items_active_case_target"]
        self.assertTrue(active.unique)
        self.assertEqual(tuple(column.name for column in active.columns),
                         ("case_type", "stable_target_key"))
        predicate = str(active.dialect_options["postgresql"]["where"])
        for status in ACTIVE_REVIEW_CASE_STATUSES:
            self.assertIn(status.value, predicate)
        self.assertNotIn(ReviewCaseStatus.RESOLVED.value, predicate)
        self.assertNotIn(ReviewCaseStatus.SUPERSEDED.value, predicate)

        alias_index = next(index for index in _table(models["PersonAlias"]).indexes
                           if index.name == "uq_person_aliases_active_normalized_class")
        self.assertTrue(alias_index.unique)
        self.assertEqual(tuple(column.name for column in alias_index.columns),
                         ("alias_normalized", "alias_class"))
        self.assertEqual(str(alias_index.dialect_options["postgresql"]["where"]),
                         f"status = '{PersonAliasStatus.ACTIVE.value}'")

        override_index = next(index for index in _table(models["FieldOverride"]).indexes
                              if index.name == "uq_field_overrides_active_target_field_scope_context")
        self.assertTrue(override_index.unique)
        self.assertEqual(str(override_index.dialect_options["postgresql"]["where"]), "is_active")

    def test_table_unique_constraints_cover_sequences_keys_and_hashes(self) -> None:
        models = _models()
        expected = {
            "ReviewDecision": {"uq_review_decisions_item_sequence": ("review_item_id", "sequence")},
            "CanonicalIdentity": {"uq_canonical_identities_key": ("canonical_identity_key",)},
            "AuditEvent": {"uq_audit_events_event_hash": ("event_hash",)},
        }
        for model_name, constraints in expected.items():
            table = _table(models[model_name])
            by_name = {constraint.name: tuple(column.name for column in constraint.columns)
                       for constraint in table.constraints if hasattr(constraint, "columns")}
            for constraint_name, columns in constraints.items():
                self.assertEqual(by_name[constraint_name], columns)

    def test_identity_and_alias_structurally_exclude_forbidden_data(self) -> None:
        models = _models()
        canonical_columns = set(_table(models["CanonicalIdentity"]).columns.keys())
        for forbidden in (
            "cedula", "id_document", "document_id", "national_id", "email", "role",
            "product_id", "period_id", "scientific_status", "kpi",
        ):
            self.assertNotIn(forbidden, canonical_columns)
        alias_columns = set(_table(models["PersonAlias"]).columns.keys())
        for forbidden in (
            "role", "roles", "authorship", "authorships", "product_id", "period_id",
            "scientific_status", "kpi",
        ):
            self.assertNotIn(forbidden, alias_columns)

    def test_models_do_not_add_inverse_relationships_to_user(self) -> None:
        _models()
        package = importlib.import_module("app.models")
        user_relationships = set(class_mapper(package.User).relationships.keys())
        for forbidden in (
            "b2b_capabilities", "review_decisions", "audit_events",
            "canonical_identities", "person_aliases", "field_overrides",
        ):
            self.assertNotIn(forbidden, user_relationships)


if __name__ == "__main__":
    unittest.main()
