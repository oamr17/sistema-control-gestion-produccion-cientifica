from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import datetime
from enum import Enum
from ipaddress import IPv4Address, ip_address
import re
from socket import inet_aton
from types import MappingProxyType
from typing import Annotated, Literal, Self, TypeAlias
import unicodedata
from urllib.parse import parse_qsl, unquote, urlsplit
from uuid import UUID

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    WithJsonSchema,
    field_serializer,
    field_validator,
    model_validator,
)

from app.models.human_review_enums import (
    AuditEventType,
    B2BAction,
    B2BCapability,
    DecisionScope,
    OverrideField,
    ReviewCaseStatus,
    ReviewCaseType,
    ReviewDecisionType,
    ReviewTargetTable,
    ScientificStatus,
)
from app.services.human_review_state import (
    HumanReviewDomainError,
    IncompatibleDecisionError,
)


DecisionAction: TypeAlias = Literal["approve", "correct", "link"]
LinkResolution: TypeAlias = Literal["linked", "maintained_separate", "separated"]
DuplicateResolution: TypeAlias = Literal["merged", "maintained_separate", "separated"]
HumanReviewErrorCode: TypeAlias = Literal[
    "HUMAN_REVIEW_VALIDATION",
    "AUTHENTICATION_REQUIRED",
    "B2B_CAPABILITY_REQUIRED",
    "REVIEW_CASE_NOT_FOUND",
    "REVIEW_CASE_VERSION_CONFLICT",
    "INCOMPATIBLE_DECISION",
    "INVALID_COMMAND_PAYLOAD",
    "HUMAN_REVIEW_INTERNAL_ERROR",
    "EVIDENCE_UNAVAILABLE",
]


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        """Copy through validation so public update paths cannot bypass safety."""

        values = self.model_dump(mode="python", round_trip=True)
        if update:
            values.update(dict(update))
        return type(self).model_validate(values)


_FACETS_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "statuses": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                status.value: {"type": "integer", "minimum": 0}
                for status in ReviewCaseStatus
            },
        },
        "case_types": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                case_type.value: {"type": "integer", "minimum": 0}
                for case_type in ReviewCaseType
            },
        },
    },
}
_PUBLIC_SCALAR_SCHEMA = {
    "anyOf": [
        {"type": "string"},
        {"type": "integer"},
        {"type": "number"},
        {"type": "boolean"},
        {"type": "null"},
    ]
}
_OVERRIDE_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        field: _PUBLIC_SCALAR_SCHEMA
        for field in (
            "decision_id", "field", "field_path", "scope", "scope_id", "value", "created_at",
        )
    },
}
_EVIDENCE_SUMMARY_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        field: _PUBLIC_SCALAR_SCHEMA
        for field in (
            "available", "count", "document_name", "page", "section", "locator", "fragment", "stream_path",
        )
    },
}
_KPI_EFFECT_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        field: _PUBLIC_SCALAR_SCHEMA for field in ("metric", "before", "after", "delta")
    },
}
_AUDIT_PAYLOAD_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "kind": _PUBLIC_SCALAR_SCHEMA,
        "schema_version": _PUBLIC_SCALAR_SCHEMA,
        "decision_id": _PUBLIC_SCALAR_SCHEMA,
        "decision_type": _PUBLIC_SCALAR_SCHEMA,
        "previous_case_status": _PUBLIC_SCALAR_SCHEMA,
        "resulting_case_status": _PUBLIC_SCALAR_SCHEMA,
        "review_item_id": _PUBLIC_SCALAR_SCHEMA,
        "kpi_effect": {
            "type": "array",
            "maxItems": 100,
            "items": _KPI_EFFECT_JSON_SCHEMA,
        },
    },
}
_AUDIT_ITEM_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "id": _PUBLIC_SCALAR_SCHEMA,
        "event_type": _PUBLIC_SCALAR_SCHEMA,
        "actor_id": _PUBLIC_SCALAR_SCHEMA,
        "review_item_id": _PUBLIC_SCALAR_SCHEMA,
        "decision_id": _PUBLIC_SCALAR_SCHEMA,
        "created_at": _PUBLIC_SCALAR_SCHEMA,
        "correlation_id": _PUBLIC_SCALAR_SCHEMA,
        "summary": _PUBLIC_SCALAR_SCHEMA,
        "payload": _AUDIT_PAYLOAD_JSON_SCHEMA,
    },
}
_ERROR_DETAILS_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        field: _PUBLIC_SCALAR_SCHEMA
        for field in (
            "field", "current_status", "target_status", "case_type", "action", "resolution", "scope",
            "expected_version", "actual_version",
        )
    },
}

ReviewFacetsMapping: TypeAlias = Annotated[
    Mapping[str, Mapping[str, int]], WithJsonSchema(_FACETS_JSON_SCHEMA)
]
ReviewOverrideMapping: TypeAlias = Annotated[
    Mapping[str, object], WithJsonSchema(_OVERRIDE_JSON_SCHEMA)
]
EvidenceSummaryMapping: TypeAlias = Annotated[
    Mapping[str, object], WithJsonSchema(_EVIDENCE_SUMMARY_JSON_SCHEMA)
]
AuditItemMapping: TypeAlias = Annotated[
    Mapping[str, object], WithJsonSchema(_AUDIT_ITEM_JSON_SCHEMA)
]
ErrorDetailsMapping: TypeAlias = Annotated[
    Mapping[str, object], WithJsonSchema(_ERROR_DETAILS_JSON_SCHEMA)
]


def _nonblank(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must contain a non-whitespace character")
    return value


class ReviewQueueQuery(_ClosedModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)
    statuses: tuple[ReviewCaseStatus, ...] = ()
    case_types: tuple[ReviewCaseType, ...] = ()
    period_id: int | None = Field(default=None, gt=0)
    document_id: int | None = Field(default=None, gt=0)
    source_revision: str | None = Field(default=None, min_length=1, max_length=120)
    created_from: datetime | None = None
    created_to: datetime | None = None
    q: str | None = Field(default=None, min_length=3, max_length=128)
    sort: Literal["priority_oldest"] = "priority_oldest"

    @field_validator("source_revision", "q")
    @classmethod
    def reject_blank_optional_filters(cls, value: str | None, info) -> str | None:
        return None if value is None else _nonblank(value, info.field_name)

    @field_validator("created_from", "created_to")
    @classmethod
    def require_aware_creation_bounds(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("creation bounds must be timezone-aware")
        return value


class ReviewQueueItem(_ClosedModel):
    id: UUID
    case_type: ReviewCaseType
    case_status: ReviewCaseStatus
    scientific_status: ScientificStatus
    document_id: int | None = Field(default=None, gt=0)
    document_name: str | None = Field(default=None, max_length=320)
    source_revision: str | None = Field(max_length=120)
    source_page: int | None = Field(gt=0)
    source_section: str = Field(min_length=1, max_length=240)
    detected_value: str | None = Field(default=None, max_length=4000)
    normalized_value: str | None = Field(default=None, max_length=4000)
    canonical_value: str | None = Field(default=None, max_length=4000)
    automatic_priority: int
    manual_priority: int | None
    possible_kpi_impact: bool
    version: int = Field(ge=1)
    created_at: datetime

    @field_validator("source_revision", "source_section")
    @classmethod
    def sanitize_public_queue_text(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        _nonblank(value, info.field_name)
        max_length = 120 if info.field_name == "source_revision" else 240
        return _sanitize_public_text(value, max_length=max_length)

    @field_serializer("source_revision", "source_section")
    def serialize_public_queue_text(self, value, info):
        if value is None:
            return None
        max_length = 120 if info.field_name == "source_revision" else 240
        return _sanitize_public_text(value, max_length=max_length)

    @field_validator("document_name")
    @classmethod
    def sanitize_public_queue_document_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        _nonblank(value, "document_name")
        return _sanitize_document_name(value)

    @field_serializer("document_name")
    def serialize_public_queue_document_name(self, value):
        return None if value is None else _sanitize_document_name(value)

    @field_validator("detected_value", "normalized_value", "canonical_value")
    @classmethod
    def sanitize_public_queue_values(cls, value: str | None) -> str | None:
        return None if value is None else _sanitize_narrative_text(value, max_length=4000)

    @field_serializer("detected_value", "normalized_value", "canonical_value")
    def serialize_public_queue_values(self, value):
        return None if value is None else _sanitize_narrative_text(value, max_length=4000)


class ReviewQueueResponse(_ClosedModel):
    items: tuple[ReviewQueueItem, ...] = Field(max_length=100)
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    facets: ReviewFacetsMapping
    correlation_id: UUID

    @field_serializer("items")
    def serialize_bounded_items(self, value):
        if not isinstance(value, (tuple, list)) or len(value) > 100:
            return ()
        sanitized: list[ReviewQueueItem] = []
        for item in value:
            if isinstance(item, ReviewQueueItem):
                sanitized.append(item)
                continue
            if isinstance(item, Mapping):
                try:
                    sanitized.append(ReviewQueueItem.model_validate(item))
                except (TypeError, ValueError):
                    continue
        return tuple(sanitized)

    @field_validator("facets", mode="after")
    @classmethod
    def secure_facets(cls, value):
        return _sanitize_facets(value)

    @field_serializer("facets")
    def serialize_secure_facets(self, value):
        sanitized = (
            value
            if isinstance(value, _ImmutablePublicMapping)
            else _sanitize_facets(value)
            if isinstance(value, Mapping)
            else _ImmutablePublicMapping({})
        )
        return _plain_public_value(sanitized)


class CounterpartReference(_ClosedModel):
    """Public, typed reference to an existing scientific source row."""

    target_type: ReviewTargetTable
    target_id: StrictInt = Field(gt=0)


class CounterpartOption(_ClosedModel):
    """A case-scoped counterpart choice; never contains an internal key."""

    counterpart_ref: CounterpartReference
    display_name: str = Field(min_length=1, max_length=500)
    source_label: str | None = Field(default=None, max_length=500)
    document_name: str | None = Field(default=None, max_length=320)

    @field_validator("display_name", "source_label")
    @classmethod
    def sanitize_counterpart_text(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        _nonblank(value, info.field_name)
        return _sanitize_public_text(value, max_length=500)

    @field_validator("document_name")
    @classmethod
    def sanitize_counterpart_document(cls, value: str | None) -> str | None:
        if value is None:
            return None
        _nonblank(value, "document_name")
        return _sanitize_document_name(value)


class ReviewCaseDetail(ReviewQueueItem):
    target_table: ReviewTargetTable
    target_pk: int | None = Field(gt=0)
    field_path: OverrideField | Literal["case"]
    detected_value: str | None = Field(max_length=4000)
    normalized_value: str | None = Field(max_length=4000)
    canonical_value: str | None = Field(max_length=4000)
    current_decision_id: UUID | None
    overrides: tuple[ReviewOverrideMapping, ...] = Field(max_length=100)
    effective_memberships: tuple[str, ...] = Field(
        max_length=100,
        validation_alias=AliasChoices("effective_memberships", "memberships"),
        description=(
            "Memberships currently derivable from persisted data and the closed "
            "case/current-decision matrix; historical memberships are not reconstructed."
        ),
    )
    counterpart_options: tuple[CounterpartOption, ...] = Field(
        default=(),
        max_length=100,
        description=(
            "Case-scoped public counterpart choices. Empty for cases without "
            "a currently valid duplicate candidate."
        ),
    )
    evidence_summary: EvidenceSummaryMapping

    @field_validator("effective_memberships", mode="after")
    @classmethod
    def sanitize_public_memberships(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_sanitize_public_text(item, max_length=500) for item in value)

    @field_serializer("effective_memberships")
    def serialize_public_memberships(self, value):
        if not isinstance(value, (tuple, list)) or len(value) > 100:
            return ()
        return tuple(
            _sanitize_public_text(item, max_length=500)
            for item in value
        )

    @field_validator("overrides", "evidence_summary", mode="after")
    @classmethod
    def secure_nested_public_data(cls, value, info):
        if info.field_name == "overrides":
            return tuple(_sanitize_override(item) for item in value)
        return _sanitize_evidence_summary(value)

    @field_serializer("overrides")
    def serialize_secure_overrides(self, value):
        if (
            isinstance(value, (tuple, list))
            and len(value) <= 100
            and all(isinstance(item, _ImmutablePublicMapping) for item in value)
        ):
            return _plain_public_value(value)
        sanitized = tuple(
            _sanitize_override(item)
            for item in value
            if isinstance(item, Mapping)
        ) if isinstance(value, (tuple, list)) and len(value) <= 100 else ()
        return _plain_public_value(sanitized)

    @field_serializer("evidence_summary")
    def serialize_secure_evidence_summary(self, value):
        sanitized = (
            value
            if isinstance(value, _ImmutablePublicMapping)
            else _sanitize_evidence_summary(value)
            if isinstance(value, Mapping)
            else _ImmutablePublicMapping({})
        )
        return _plain_public_value(sanitized)


class RelatedReviewEntity(_ClosedModel):
    public_type: Literal[
        "person",
        "scientific_product",
        "research_entity",
        "external_researcher",
    ]
    public_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=220)

    @field_validator("public_id", "display_name", mode="before")
    @classmethod
    def sanitize_public_entity_text(cls, value: object, info) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{info.field_name} must be a string")
        _nonblank(value, info.field_name)
        max_length = 128 if info.field_name == "public_id" else 220
        if info.field_name == "display_name" and len(value) > max_length:
            return value[:max_length] if _public_text_is_safe(value) else _REDACTED_PUBLIC_VALUE
        return _sanitize_public_text(value, max_length=max_length)

    @field_serializer("public_id", "display_name")
    def serialize_public_entity_text(self, value: str, info) -> str:
        max_length = 128 if info.field_name == "public_id" else 220
        return _sanitize_public_text(value, max_length=max_length)


class RelatedReviewItem(_ClosedModel):
    case_id: UUID
    case_type: ReviewCaseType
    case_status: ReviewCaseStatus
    scientific_status: ScientificStatus
    version: int = Field(ge=1)
    current_decision_id: UUID | None
    detected_value: str | None
    normalized_value: str | None
    canonical_value: str | None
    possible_kpi_impact: bool
    allowed_actions: tuple[DecisionAction, ...]
    evidence_summary: EvidenceSummaryMapping

    @field_validator("detected_value", "normalized_value", "canonical_value")
    @classmethod
    def sanitize_public_related_values(cls, value: str | None) -> str | None:
        return None if value is None else _sanitize_narrative_text(value, max_length=4000)

    @field_serializer("detected_value", "normalized_value", "canonical_value")
    def serialize_public_related_values(self, value: str | None) -> str | None:
        return None if value is None else _sanitize_narrative_text(value, max_length=4000)

    @field_validator("evidence_summary", mode="after")
    @classmethod
    def secure_related_evidence_summary(cls, value):
        return _sanitize_evidence_summary(value)

    @field_serializer("evidence_summary")
    def serialize_secure_related_evidence_summary(self, value):
        sanitized = (
            value
            if isinstance(value, _ImmutablePublicMapping)
            else _sanitize_evidence_summary(value)
            if isinstance(value, Mapping)
            else _ImmutablePublicMapping({})
        )
        return _plain_public_value(sanitized)


class RelatedReviewResponse(_ClosedModel):
    entity: RelatedReviewEntity
    items: tuple[RelatedReviewItem, ...] = Field(max_length=50)
    total_pending: int = Field(ge=0)
    truncated: bool
    correlation_id: UUID

    @model_validator(mode="after")
    def require_unique_cases_and_safe_total(self) -> Self:
        case_ids = tuple(item.case_id for item in self.items)
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("related review items must have unique case_id values")
        if self.truncated and self.total_pending < len(self.items):
            raise ValueError("truncated related review total cannot be smaller than items")
        return self

    @field_serializer("items")
    def serialize_bounded_related_items(self, value):
        if not isinstance(value, (tuple, list)) or len(value) > 50:
            return ()
        sanitized: list[RelatedReviewItem] = []
        seen: set[UUID] = set()
        for item in value:
            candidate = item
            if isinstance(item, Mapping):
                try:
                    candidate = RelatedReviewItem.model_validate(item)
                except (TypeError, ValueError):
                    continue
            if not isinstance(candidate, RelatedReviewItem) or candidate.case_id in seen:
                continue
            seen.add(candidate.case_id)
            sanitized.append(candidate)
        return tuple(sanitized)


class EffectiveCapabilitiesResponse(_ClosedModel):
    capability: B2BCapability | None
    actions: tuple[B2BAction, ...]


class EffectiveDataRevisionResponse(_ClosedModel):
    revision: int = Field(ge=0)


class _DecisionPayload(_ClosedModel):
    pass


class PersonDecisionPayload(_DecisionPayload):
    case_type: Literal["person_identity", "author_identity"]
    canonical_identity_key: str | None = Field(default=None, max_length=320)
    canonical_name: str = Field(min_length=1, max_length=500)
    aliases: tuple[str, ...] = Field(default=(), max_length=100)
    scientific_status: ScientificStatus
    resolution: LinkResolution | None = None

    @field_validator("canonical_identity_key")
    @classmethod
    def reject_blank_identity_key(cls, value: str | None, info) -> str | None:
        return None if value is None else _nonblank(value, info.field_name)

    @field_validator("canonical_name")
    @classmethod
    def reject_blank_identity_name(cls, value: str, info) -> str:
        return _nonblank(value, info.field_name)

    @field_validator("aliases")
    @classmethod
    def reject_blank_or_duplicate_aliases(cls, aliases: tuple[str, ...]) -> tuple[str, ...]:
        if any(not alias.strip() for alias in aliases):
            raise ValueError("aliases cannot contain blank values")
        if len(aliases) != len(set(aliases)):
            raise ValueError("aliases must be unique")
        if any(len(alias) > 500 for alias in aliases):
            raise ValueError("aliases cannot exceed 500 characters")
        return aliases


class ProductDecisionPayload(_DecisionPayload):
    case_type: Literal["product"]
    product_title: str | None = Field(default=None, max_length=1000)
    scientific_status: ScientificStatus

    @field_validator("product_title")
    @classmethod
    def reject_blank_product_title(cls, value: str | None) -> str | None:
        return None if value is None else _nonblank(value, "product_title")


class RelationDecisionPayload(_DecisionPayload):
    case_type: Literal["project_director_relation"]
    project_director_identity_key: str | None = Field(default=None, max_length=320)
    relationship_status: LinkResolution
    scientific_status: ScientificStatus

    @field_validator("project_director_identity_key")
    @classmethod
    def reject_blank_director_key(cls, value: str | None) -> str | None:
        return None if value is None else _nonblank(value, "project_director_identity_key")


class ExternalDecisionPayload(_DecisionPayload):
    case_type: Literal["external_identity"]
    external_identity_key: str | None = Field(default=None, max_length=320)
    external_institution: str | None = Field(default=None, max_length=500)
    scientific_status: ScientificStatus
    resolution: LinkResolution | None = None

    @field_validator("external_identity_key", "external_institution")
    @classmethod
    def reject_blank_external_text(cls, value: str | None, info) -> str | None:
        return None if value is None else _nonblank(value, info.field_name)


class DuplicateDecisionPayload(_DecisionPayload):
    case_type: Literal["possible_duplicate"]
    counterpart_ref: CounterpartReference
    resolution: DuplicateResolution
    scientific_status: ScientificStatus


DecisionPayloadV1 = Annotated[
    PersonDecisionPayload
    | ProductDecisionPayload
    | RelationDecisionPayload
    | ExternalDecisionPayload
    | DuplicateDecisionPayload,
    Field(discriminator="case_type"),
]


class ApplyDecisionRequest(_ClosedModel):
    expected_version: StrictInt = Field(ge=1)
    expected_current_decision_id: UUID | None
    action: DecisionAction
    scope: DecisionScope
    payload: DecisionPayloadV1
    reason: str | None = Field(default=None, max_length=4000)
    correlation_id: UUID


class DiscardRequest(_ClosedModel):
    expected_version: StrictInt = Field(ge=1)
    expected_current_decision_id: UUID | None
    reason: str = Field(min_length=1, max_length=4000)
    correlation_id: UUID

    @field_validator("reason")
    @classmethod
    def reject_blank_reason(cls, value: str) -> str:
        return _nonblank(value, "reason")


class RevertRequest(_ClosedModel):
    expected_version: StrictInt = Field(ge=1)
    expected_current_decision_id: UUID
    decision_id_to_revert: UUID
    reason: str = Field(min_length=1, max_length=4000)
    correlation_id: UUID

    @field_validator("reason")
    @classmethod
    def reject_blank_reason(cls, value: str) -> str:
        return _nonblank(value, "reason")


class KpiEffectItem(_ClosedModel):
    metric: str = Field(min_length=1, max_length=160)
    before: StrictInt
    after: StrictInt
    delta: StrictInt

    @field_validator("metric")
    @classmethod
    def sanitize_metric(cls, value: str) -> str:
        _nonblank(value, "metric")
        return _sanitize_public_text(value, max_length=160)

    @field_serializer("metric")
    def serialize_metric(self, value: str) -> str:
        return _sanitize_public_text(value, max_length=160)


class KpiEffect(_ClosedModel):
    affected: tuple[KpiEffectItem, ...] = Field(max_length=100)

    @field_serializer("affected")
    def serialize_bounded_effect(self, value):
        if not isinstance(value, (tuple, list)) or len(value) > 100:
            return ()
        sanitized: list[KpiEffectItem] = []
        for item in value:
            if isinstance(item, KpiEffectItem):
                sanitized.append(item)
                continue
            if isinstance(item, Mapping):
                try:
                    sanitized.append(KpiEffectItem.model_validate(item))
                except (TypeError, ValueError):
                    continue
        return tuple(sanitized)


class ApplyDecisionResponse(_ClosedModel):
    case: ReviewCaseDetail
    decision_id: UUID
    kpi_effect: KpiEffect
    correlation_id: UUID


class EvidenceResponse(_ClosedModel):
    document_name: str | None = Field(max_length=320)
    page: int | None = Field(gt=0)
    section: str | None = Field(max_length=240)
    locator: str | None = Field(max_length=500)
    fragment: str | None = Field(max_length=4000)
    stream_path: str | None = Field(max_length=500)
    correlation_id: UUID

    @field_validator("document_name")
    @classmethod
    def sanitize_public_document_name(cls, value: str | None) -> str | None:
        return None if value is None else _sanitize_document_name(value)

    @field_validator("section", "locator", "fragment")
    @classmethod
    def sanitize_public_evidence_text(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        max_length = {"section": 240, "locator": 500, "fragment": 4000}[info.field_name]
        return _sanitize_narrative_text(value, max_length=max_length)

    @field_serializer("document_name")
    def serialize_public_document_name(self, value: str | None) -> str | None:
        return None if value is None else _sanitize_document_name(value)

    @field_serializer("section", "locator", "fragment")
    def serialize_public_evidence_text(self, value: str | None, info) -> str | None:
        if value is None:
            return None
        max_length = {"section": 240, "locator": 500, "fragment": 4000}[info.field_name]
        return _sanitize_narrative_text(value, max_length=max_length)

    @field_validator("stream_path")
    @classmethod
    def require_application_relative_stream_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if (
            value.startswith(("/", "\\"))
            or "\\" in value
            or ":" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*", value) is None
        ):
            raise ValueError("stream_path must be an application-relative path")
        return value

    @field_serializer("stream_path")
    def serialize_application_relative_stream_path(self, value: str | None) -> str | None:
        if value is None or not isinstance(value, str):
            return None
        try:
            return self.require_application_relative_stream_path(value)
        except (TypeError, ValueError):
            return None


class AuditTimelineResponse(_ClosedModel):
    items: tuple[AuditItemMapping, ...] = Field(max_length=100)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    correlation_id: UUID

    @field_validator("items", mode="after")
    @classmethod
    def secure_timeline_items(cls, value):
        return tuple(_sanitize_audit_item(item) for item in value)

    @field_serializer("items")
    def serialize_secure_timeline_items(self, value):
        if (
            isinstance(value, (tuple, list))
            and len(value) <= 100
            and all(isinstance(item, _ImmutablePublicMapping) for item in value)
        ):
            return _plain_public_value(value)
        sanitized = tuple(
            _sanitize_audit_item(item)
            for item in value
            if isinstance(item, Mapping)
        ) if isinstance(value, (tuple, list)) and len(value) <= 100 else ()
        return _plain_public_value(sanitized)


_REDACTED_PUBLIC_VALUE = "[redacted]"
_MAX_PERCENT_DECODE_ROUNDS = 8
_PERCENT_ESCAPE = re.compile(r"%[0-9a-f]{2}", re.IGNORECASE)
_PATH_SEPARATOR_TRANSLATION = str.maketrans({
    "\u2044": "/",
    "\u2215": "/",
    "\u29F8": "/",
    "\uFF0F": "/",
    "\uFE68": "\\",
    "\u2216": "\\",
    "\u29F5": "\\",
    "\uFF3C": "\\",
})
_HIERARCHICAL_URI = re.compile(r"\b[a-z][a-z0-9+.-]{1,31}://\S+", re.IGNORECASE)
_SENSITIVE_OPAQUE_URI = re.compile(
    r"\b(?:file|sqlite|jdbc|data|postgres(?:ql)?|mysql|mariadb|mssql|mongodb(?:\+srv)?|"
    r"redis|rediss|amqp|amqps|s3|gs|minio|ftp|sftp|ssh|urn):\S+",
    re.IGNORECASE,
)
_WINDOWS_DRIVE_PATH = re.compile(
    r"(?<![a-z0-9])[a-z]:(?:(?:[/\\])|(?=[^\s])|"
    r"(?:[^\s/\\]{1,240}(?:[/\\]|\.[a-z0-9]{1,16})(?:\b|$)))",
    re.IGNORECASE,
)
_TRAVERSAL_PATH = re.compile(r"(?:^|[/\\])\.\.(?:[/\\]|$)")
_UNC_PATH = re.compile(r"(?:^|[\s=<(\[{])(?:\\\\|//)[^\s/\\]+[/\\][^\s]+")
_RELATIVE_FILE_PATH = re.compile(
    r"(?:^|[\s=<(\[{])(?:[a-z0-9._-]+/)+[a-z0-9._-]+\.[a-z0-9]{1,16}"
    r"(?:$|[\s>)\]}.,;])",
    re.IGNORECASE,
)
_PATH_ASSIGNMENT = re.compile(
    r"\b(?:path|source[_ -]*path|file|filename|directory|dir|dsn)\s*[=:]\s*"
    r"(?:[a-z]:|[/\\]|\.\.[/\\]|[a-z][a-z0-9+.-]{1,31}:)",
    re.IGNORECASE,
)
_STORAGE_LOCATION_ASSIGNMENT = re.compile(
    r"\b(?:storage|object|bucket|blob|artifact|collection)"
    r"[_ -]*(?:locator|key|path|location)\s*[=:]\s*"
    r"[^\s/\\]{1,240}[/\\][^\s]+",
    re.IGNORECASE,
)
_SECRET_FAMILY = re.compile(
    r"(?:"
    r"\bgh[pousr]_[a-z0-9_]{16,}\b|"
    r"\bgithub_pat_[a-z0-9_]{16,}\b|"
    r"\bglpat-[a-z0-9_-]{16,}\b|"
    r"\bAIza[a-z0-9_-]{20,}\b|"
    r"\bSG\.[a-z0-9_-]{12,}\.[a-z0-9_-]{12,}\b|"
    r"\bsk-(?:(?:proj|svcacct|admin|ant-api\d*)-)?[a-z0-9_-]{16,}\b|"
    r"\bsk_(?:live|test)_[a-z0-9_-]{12,}\b|"
    r"\bxox[baprs]-[a-z0-9_-]{12,}\b|"
    r"\b(?:akia|asia)[a-z0-9]{16}\b|"
    r"\beyj[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\b|"
    r"\b(?:bearer|password|passwd|passphrase|secret|credential|api[ _-]*key|"
    r"access[ _-]*token)\b\s*(?:[=:]|\s)\s*\S{6,}|"
    r"\b[^\s:@]{2,64}:[^\s@]{4,}@[a-z0-9][a-z0-9.-]*\b"
    r")",
    re.IGNORECASE,
)
_ASSIGNED_VALUE = re.compile(
    r"(?<![a-z0-9])(?P<key>"
    r"[a-z][a-z0-9]*(?:[_-][a-z0-9]+)*|"
    r"(?:refresh|client|access|api|oauth|session|id|authorization|private|bearer)"
    r"\s+(?:token|secret|key|id)"
    r")"
    r"\s*(?:=|:)\s*(?P<value>[^\s,;]{1,})",
    re.IGNORECASE,
)
_PRIVATE_KEY_HEADER = re.compile(
    r"-{5}\s*BEGIN(?:\s+[a-z0-9]+)*\s+PRIVATE\s+KEY\s*-{5}",
    re.IGNORECASE,
)
_PASSWORD_HASH_PATTERNS = (
    # Modular crypt and passlib-style families. The identifier is parsed as a
    # family instead of enumerating individual algorithm literals.
    re.compile(
        r"(?<!\S)\$[a-z0-9][a-z0-9_-]{0,31}\$"
        r"(?:[^\s$]{0,128}\$){0,5}[^\s]{12,}(?=$|\s|[,;)])",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b[a-z0-9_-]*(?:md\d+|s?sha\d*|pbkdf2(?:[-_]sha\d+)?|bcrypt|"
        r"scrypt|argon2(?:id|i|d)?|scram[-_]sha[-_]?\d+|ntlm|lm)"
        r"\${1,2}(?:[^\s$]{0,128}\$){0,5}[^\s]{12,}(?=$|\s|[,;)])",
        re.IGNORECASE,
    ),
    # LDAP `{SCHEME}base64` password hashes.
    re.compile(
        r"\{(?:s?sha\d*|md\d+|pbkdf2(?:[-_]sha\d+)?|bcrypt|scrypt|argon2"
        r"(?:id|i|d)?)\}[a-z0-9+/=]{12,}",
        re.IGNORECASE,
    ),
    re.compile(r"\bmd5[0-9a-f]{32}\b", re.IGNORECASE),
    re.compile(
        r"(?:\bmysql[ _-]*native[ _-]*password\s*=\s*)?\*[0-9a-f]{40}\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bpassword[ _-]*hash(?:[ _-]*suffix)?\b", re.IGNORECASE),
    re.compile(
        r"\b(?:ntlm|lm|md\d+|s?sha\d*|pbkdf2(?:[-_]sha\d+)?|bcrypt|"
        r"scrypt|argon2(?:id|i|d)?)\s*[:=]\s*"
        r"(?:[0-9a-f]{16,128}|[a-z0-9+/]{16,}={0,2})"
        r"(?=$|\s|[,;)])",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:bcrypt|argon2(?:id|i|d)?|scrypt|pbkdf2(?:[-_]sha\d+)?|"
        r"s?sha\d*|md\d+|ntlm|lm|scram[-_]sha[-_]?\d+)\s*[:=/]\s*"
        r"\$[a-z0-9][a-z0-9_-]{0,31}\$"
        r"(?:[^\s$]{0,128}\$){0,6}[^\s]{12,}(?=$|\s|[,;)])",
        re.IGNORECASE,
    ),
)
_CONSTRAINT_DIAGNOSTIC = re.compile(
    r"(?:"
    r"\bviolates\s+(?:unique|foreign\s+key|check|not[- ]null|exclusion|primary\s+key)"
    r"\s+constraint\b|"
    r"\b(?:unique|foreign\s+key|check|not[- ]null|exclusion|primary\s+key)"
    r"\s+constraint\s+[a-z0-9_]+\b|"
    r"\b(?:ck|fk|pk|uq|excl|nn)_[a-z0-9_]+\b|"
    r"\b[a-z][a-z0-9_]*(?:_fkey|_pkey|_key|_check|_constraint|_excl|_nn)\b|"
    r"\b[a-z][a-z0-9]*Constraint\b"
    r")",
    re.IGNORECASE,
)
_INTERNAL_INFRASTRUCTURE = re.compile(
    r"(?:"
    r"\b(?:backend|app)\.(?:app\.)?(?:services?|repositories?|core|models?|schemas?)"
    r"(?:\.[a-z0-9_]+)+\b|"
    r"\b(?:internal|private)[._-](?:db|database|postgres(?:ql)?|redis|minio|bucket|"
    r"storage|cache|service|host|server)(?:[._-][a-z0-9]+)*\b|"
    r"\b(?:minio|s3|redis|postgres(?:ql)?|mysql|mongodb)[._-]"
    r"(?:private|internal|bucket|storage|cache|db)(?:[._-][a-z0-9]+)*\b"
    r")",
    re.IGNORECASE,
)
_SQL_COMMAND_POSITION = re.compile(
    r"(?:^|[;:]|"
    r"\b(?:database|audit|sql|query|statement)\s+"
    r"(?:(?:said|reported|returned|error)\s*)?:?)"
    r"\s*(?P<statement>"
    r"(?:select|insert|update|delete|create|alter|drop|truncate|grant|revoke|"
    r"vacuum|explain|analyze|reindex|lock|call|merge|copy|set|do|with|"
    r"prepare|execute|begin|commit|rollback|show|values|reset|discard|"
    r"checkpoint|listen|notify|unlisten|comment|refresh|savepoint|abort|end|"
    r"close|cluster|deallocate|declare|fetch|import|load|move|reassign|"
    r"release|security|start)\b.*)",
    re.IGNORECASE | re.DOTALL,
)
_SQL_COMMAND_TOKEN = re.compile(
    r"\b(?:select|insert|update|delete|create|alter|drop|truncate|grant|revoke|"
    r"vacuum|explain|analyze|reindex|lock|call|merge|copy|set|do|with|"
    r"prepare|execute|begin|commit|rollback|show|values|reset|discard|"
    r"checkpoint|listen|notify|unlisten|comment|refresh|savepoint|abort|end|"
    r"close|cluster|deallocate|declare|fetch|import|load|move|reassign|"
    r"release|security|start)\b",
    re.IGNORECASE,
)
_SQL_RELATION = r'(?:[a-z_][a-z0-9_$]*|"(?:[^"]|"")+")(?:(?:\.)(?:[a-z_][a-z0-9_$]*|"(?:[^"]|"")+"))*'
_SQL_COMMAND_GRAMMARS = (
    re.compile(r"^select\s+\S", re.IGNORECASE | re.DOTALL),
    re.compile(
        rf"^insert\s+into\s+{_SQL_RELATION}"
        r"(?:\s*\([^)]*\))?\s+(?:values\b|select\b|default\s+values\b|\()",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        rf"^update\s+{_SQL_RELATION}(?:\s+(?:as\s+)?[a-z_][a-z0-9_$]*)?"
        r"\s+set\s+[a-z_][a-z0-9_$]*(?:\.[a-z_][a-z0-9_$]*)?\s*=",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        rf"^delete\s+from\s+{_SQL_RELATION}(?:\s+(?:as\s+)?[a-z_][a-z0-9_$]*)?"
        r"\s*(?:$|;|where\b|using\b|returning\b)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        rf"^create\s+(?:or\s+replace\s+)?(?:temp(?:orary)?\s+)?"
        rf"(?:table\s+{_SQL_RELATION}\s*(?:\(|as\b|of\b)|"
        rf"(?:function|procedure)\s+{_SQL_RELATION}\s*\(|"
        r"(?:database|schema|index|constraint|role|user|extension|view|trigger|type)\b)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"^(?:alter|drop)\s+(?:table|database|schema|index|constraint|role|"
        r"user|extension|view|function|procedure|trigger|type)\b",
        re.IGNORECASE,
    ),
    re.compile(rf"^truncate(?:\s+table)?\s+{_SQL_RELATION}\b", re.IGNORECASE),
    re.compile(
        r"^grant\s+(?:all(?:\s+privileges)?|select|insert|update|delete|"
        r"truncate|references|trigger|usage|execute|connect|create|temporary)"
        r"(?:\s*,\s*(?:select|insert|update|delete|truncate|references|trigger|"
        r"usage|execute|connect|create|temporary))*\s+on\b.+\bto\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"^revoke\s+(?:all(?:\s+privileges)?|select|insert|update|delete|"
        r"truncate|references|trigger|usage|execute|connect|create|temporary)"
        r"(?:\s*,\s*(?:select|insert|update|delete|truncate|references|trigger|"
        r"usage|execute|connect|create|temporary))*\s+on\b.+\bfrom\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        rf"^vacuum(?:\s*\([^)]*\)|(?:\s+(?:full|freeze|verbose|analyze))*)"
        rf"(?:\s+{_SQL_RELATION}(?:\s*\([^)]*\))?)?\s*;?\s*$",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"^explain(?:\s*\([^)]*\)|(?:\s+(?:analyze|verbose))*)\s+"
        r"(?:select|insert|update|delete|merge|execute)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        rf"^analyze(?:\s*\([^)]*\)|\s+verbose)?(?:\s+{_SQL_RELATION}"
        r"(?:\s*\([^)]*\))?)?\s*;?\s*$",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        rf"^reindex(?:\s+(?:table|index|system|database|schema))?\s+{_SQL_RELATION}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^lock(?:\s+table)?\s+{_SQL_RELATION}(?:\s*,\s*{_SQL_RELATION})*"
        r"\s+in\s+(?:access\s+share|row\s+share|row\s+exclusive|share\s+update\s+exclusive|"
        r"share|share\s+row\s+exclusive|exclusive|access\s+exclusive)\s+mode\b",
        re.IGNORECASE,
    ),
    re.compile(rf"^call\s+{_SQL_RELATION}\s*\(", re.IGNORECASE),
    re.compile(rf"^merge\s+into\s+{_SQL_RELATION}\b", re.IGNORECASE),
    re.compile(rf"^copy\s+{_SQL_RELATION}\s+(?:from|to)\b", re.IGNORECASE),
    re.compile(
        r"^set\s+(?:local\s+|session\s+)?[a-z_][a-z0-9_.]*\s+(?:to\b|=)",
        re.IGNORECASE,
    ),
    re.compile(r"^do\s+(?:\$\w*\$|')", re.IGNORECASE | re.DOTALL),
    re.compile(
        rf"^with(?:\s+recursive)?\s+{_SQL_RELATION}"
        r"(?:\s*\([^)]*\))?\s+as\s*\(.{1,3000}\)\s*"
        r"(?:select|insert|update|delete|merge)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        rf"^prepare\s+{_SQL_RELATION}(?:\s*\([^)]*\))?\s+as\s+"
        r"(?:select|insert|update|delete|merge)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        rf"^execute\s+{_SQL_RELATION}(?:\s*\([^)]*\))?\s*;?\s*$",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"^(?:begin|commit)(?:\s+(?:work|transaction))?\s*;?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^rollback(?:\s+(?:work|transaction))?"
        r"(?:\s+to(?:\s+savepoint)?\s+[a-z_][a-z0-9_$]*)?\s*;?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^show\s+(?:all|[a-z_][a-z0-9_.]*)\s*;?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^values\s*\([^)]*\)(?:\s*,\s*\([^)]*\))*\s*;?\s*$",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"^reset\s+(?:all|[a-z_][a-z0-9_.]*)\s*;?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^discard\s+(?:all|plans|sequences|temp|temporary)\s*;?\s*$",
        re.IGNORECASE,
    ),
    re.compile(r"^checkpoint\s*;?\s*$", re.IGNORECASE),
    re.compile(rf"^listen\s+{_SQL_RELATION}\s*;?\s*$", re.IGNORECASE),
    re.compile(
        rf"^notify\s+{_SQL_RELATION}(?:\s*,\s*'[^']*')?\s*;?\s*$",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        rf"^unlisten\s+(?:\*|{_SQL_RELATION})\s*;?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^comment\s+on\s+(?:table|column|database|schema|index|view|"
        r"materialized\s+view|function|procedure|trigger|type|role|extension)"
        r"\s+.{1,1000}?\s+is\s+(?:null|'[^']*'|[a-z_][a-z0-9_$]*)\s*;?\s*$",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        rf"^refresh\s+materialized\s+view(?:\s+concurrently)?\s+{_SQL_RELATION}"
        r"(?:\s+(?:with|without)\s+data)?\s*;?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^savepoint\s+{_SQL_RELATION}\s*;?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:abort|end)(?:\s+(?:work|transaction))?"
        r"(?:\s+and\s+(?:no\s+)?chain)?\s*;?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^close\s+(?:all|{_SQL_RELATION})\s*;?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^cluster(?:\s+verbose)?(?:\s+{_SQL_RELATION}"
        rf"(?:\s+using\s+{_SQL_RELATION})?)?\s*;?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^deallocate(?:\s+prepare)?\s+(?:all|{_SQL_RELATION})\s*;?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^declare\s+{_SQL_RELATION}\s+(?:(?:binary|asensitive|insensitive|"
        r"no\s+scroll|scroll)\s+)*cursor(?:\s+with\s+hold)?\s+for\s+"
        r"(?:select|with|values)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        rf"^fetch\s+(?:(?:next|prior|first|last|absolute\s+\d+|relative\s+\d+|"
        r"forward(?:\s+(?:\d+|all))?|backward(?:\s+(?:\d+|all))?|all)\s+)?"
        rf"(?:from|in)\s+{_SQL_RELATION}\s*;?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^import\s+foreign\s+schema\s+{_SQL_RELATION}(?:\s+(?:limit|except)"
        rf"\s+to\s*\([^)]*\))?\s+from\s+server\s+{_SQL_RELATION}\s+into\s+"
        rf"{_SQL_RELATION}(?:\s+options\s*\([^)]*\))?\s*;?\s*$",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(r"^load\s+'[^']+'\s*;?\s*$", re.IGNORECASE),
    re.compile(
        rf"^move\s+(?:(?:next|prior|first|last|absolute\s+\d+|relative\s+\d+|"
        r"forward(?:\s+(?:\d+|all))?|backward(?:\s+(?:\d+|all))?|all)\s+)?"
        rf"(?:from|in)\s+{_SQL_RELATION}\s*;?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^reassign\s+owned\s+by\s+{_SQL_RELATION}"
        rf"(?:\s*,\s*{_SQL_RELATION})*\s+to\s+{_SQL_RELATION}\s*;?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"^release(?:\s+savepoint)?\s+{_SQL_RELATION}\s*;?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^security\s+label(?:\s+for\s+[a-z_][a-z0-9_$]*)?\s+on\s+"
        r"(?:table|column|database|schema|view|materialized\s+view|function|"
        r"procedure|role|type)\s+.{1,1000}?\s+is\s+(?:null|'[^']*')\s*;?\s*$",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"^start\s+transaction(?:\s+isolation\s+level\s+"
        r"(?:serializable|repeatable\s+read|read\s+committed|read\s+uncommitted))?"
        r"(?:\s*,?\s*(?:read\s+(?:write|only)|deferrable|not\s+deferrable))*"
        r"\s*;?\s*$",
        re.IGNORECASE,
    ),
)

_ERROR_INTEGER_DETAIL_KEYS = frozenset({"expected_version", "actual_version"})
_ERROR_FIELD_DETAIL_VALUES = frozenset({
    "expected_version", "expected_current_decision_id", "decision_id_to_revert",
    "action", "scope", "payload", "reason", "correlation_id", "case_type",
    "resolution", "relationship_status", "canonical_identity_key", "canonical_name",
    "aliases", "scientific_status", "product_title", "project_director_identity_key",
    "external_identity_key", "external_institution", "counterpart_ref",
    "case", *tuple(field.value for field in OverrideField),
})
_ERROR_DETAIL_VALUES: Mapping[str, frozenset[str]] = MappingProxyType({
    "field": _ERROR_FIELD_DETAIL_VALUES,
    "current_status": frozenset(status.value for status in ReviewCaseStatus),
    "target_status": frozenset(status.value for status in ReviewCaseStatus),
    "case_type": frozenset(case_type.value for case_type in ReviewCaseType),
    "action": frozenset(("approve", "correct", "link")),
    "resolution": frozenset(("linked", "merged", "maintained_separate", "separated")),
    "scope": frozenset(scope.value for scope in DecisionScope),
})
_ERROR_SAFE_MESSAGES: Mapping[HumanReviewErrorCode, str] = MappingProxyType({
    "HUMAN_REVIEW_VALIDATION": "The human review request is invalid",
    "AUTHENTICATION_REQUIRED": "Authentication is required",
    "B2B_CAPABILITY_REQUIRED": "B2B capability is required",
    "REVIEW_CASE_NOT_FOUND": "Review case was not found",
    "REVIEW_CASE_VERSION_CONFLICT": "Review case version conflict",
    "INCOMPATIBLE_DECISION": "Decision is incompatible",
    "INVALID_COMMAND_PAYLOAD": "Command payload is invalid",
    "HUMAN_REVIEW_INTERNAL_ERROR": "Request could not be completed",
    "EVIDENCE_UNAVAILABLE": "Evidence is unavailable",
})

_OVERRIDE_FIELDS = frozenset({
    "decision_id", "field", "field_path", "scope", "scope_id", "value", "created_at",
})
_EVIDENCE_SUMMARY_FIELDS = frozenset({
    "available", "count", "document_name", "page", "section", "locator", "fragment", "stream_path",
})
_AUDIT_ITEM_FIELDS = frozenset({
    "id", "event_type", "actor_id", "review_item_id", "decision_id", "created_at",
    "correlation_id", "summary", "payload",
})
_AUDIT_PAYLOAD_FIELDS = frozenset({
    "kind", "schema_version", "decision_id", "decision_type", "previous_case_status",
    "resulting_case_status", "review_item_id", "kpi_effect",
})
_KPI_EFFECT_FIELDS = frozenset({"metric", "before", "after", "delta"})


class _ImmutablePublicMapping(Mapping[str, object]):
    """A tuple-backed mapping with no dict mutation surface."""

    __slots__ = ("_items",)

    def __init__(self, values: Mapping[str, object]) -> None:
        object.__setattr__(self, "_items", tuple(values.items()))

    def __getitem__(self, key: str) -> object:
        for candidate, value in self._items:
            if candidate == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _value in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __setattr__(self, _name: str, _value: object) -> None:
        raise TypeError("nested response mappings are immutable")

    def __repr__(self) -> str:
        return repr(dict(self._items))


def _normalized_key(value: object) -> str:
    camel_split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(value).strip())
    return re.sub(r"[^a-z0-9]+", "_", camel_split.lower()).strip("_")[:120]


def _security_default_ignorable(character: str) -> bool:
    codepoint = ord(character)
    return (
        unicodedata.category(character) == "Cf"
        or codepoint == 0x034F
        or 0x115F <= codepoint <= 0x1160
        or 0x17B4 <= codepoint <= 0x17B5
        or 0x180B <= codepoint <= 0x180F
        or codepoint == 0x3164
        or 0xFE00 <= codepoint <= 0xFE0F
        or codepoint == 0xFFA0
        or 0x1BCA0 <= codepoint <= 0x1BCA3
        or 0x1D173 <= codepoint <= 0x1D17A
        or 0xE0000 <= codepoint <= 0xE0FFF
    )


def _normalize_security_unicode(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    without_format_controls = "".join(
        character
        for character in normalized
        if not _security_default_ignorable(character)
    )
    return without_format_controls.translate(_PATH_SEPARATOR_TRANSLATION)


def _canonical_security_text(value: str) -> str:
    """Build a bounded fixed-point inspection copy without changing output."""

    canonical = value
    for _round in range(_MAX_PERCENT_DECODE_ROUNDS):
        normalized = _normalize_security_unicode(canonical)
        decoded = _normalize_security_unicode(unquote(normalized))
        if decoded == canonical:
            canonical = decoded
            break
        canonical = decoded
    return canonical


def _looks_like_sql_command(value: str) -> bool:
    for positioned in _SQL_COMMAND_POSITION.finditer(value):
        statement = positioned.group("statement").strip()
        if any(pattern.match(statement) is not None for pattern in _SQL_COMMAND_GRAMMARS):
            return True
    for token_match in _SQL_COMMAND_TOKEN.finditer(value):
        token = token_match.group(0)
        visibly_command_cased = token.isupper() or (
            not token.islower() and not token.istitle()
        )
        if not visibly_command_cased:
            continue
        statement = value[token_match.start():].strip()
        if any(pattern.match(statement) is not None for pattern in _SQL_COMMAND_GRAMMARS):
            return True
    return False


_ALLOWED_SLASH_PATTERNS = (
    re.compile(
        r"\b(?:doi\s*:\s*)?10\.\d{4,9}/[-._;()/:a-z0-9]+\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:19|20)\d{2}/(?:19|20)\d{2}\b"),
    re.compile(
        r"\b(?:19|20)\d{2}/(?:0?[1-9]|1[0-2])/"
        r"(?:0?[1-9]|[12]\d|3[01])\b"
    ),
    re.compile(r"\b\d+/\d+\b"),
    re.compile(r"\b(?:yes/no|no/yes)\b", re.IGNORECASE),
    re.compile(r"\bMethods/Results\b", re.IGNORECASE),
    re.compile(
        r"\b[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ'’-]{1,40}/"
        r"[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ'’-]{1,40}\s*\((?:19|20)\d{2}\)"
    ),
)
_LEXICAL_SLASH_PAIR = re.compile(
    r"\b[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’-]{0,30}/"
    r"[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’-]{0,30}\b"
)
_PATH_INDICATOR = re.compile(
    r"(?:^|[\s=:|?'\"(<\[{])(?:[/\\])?"
    r"(?:srv|var|opt|etc|tmp|home|users?|mnt|proc|sys|dev|root|private|"
    r"secret|secrets|config|lib|bin|app|apps)(?:[/\\])",
    re.IGNORECASE,
)
_NARRATIVE_PATH_INDICATOR = re.compile(
    r"(?:^|[\s=:|?'\"(<\[{])"
    r"(?:docs?|documents?|reports?|uploads?|files?)(?:[/\\])",
    re.IGNORECASE,
)
_STANDALONE_LEXICAL_SLASH_PAIRS = frozenset({
    "and/or", "pre/post", "input/output", "methods/results", "yes/no", "no/yes",
})
_DANGEROUS_LEXICAL_PATH_TERMS = frozenset({
    "srv", "var", "opt", "etc", "tmp", "home", "user", "users", "mnt",
    "proc", "sys", "dev", "root", "private", "secret", "secrets", "config",
    "lib", "bin", "app", "apps", "doc", "docs", "document", "documents",
    "report", "reports", "upload", "uploads", "file", "files", "internal",
    "bucket", "buckets", "object", "objects", "key", "keys", "raw",
})


def _slashes_are_allowed_public_notation(value: str) -> bool:
    """Allow bounded public notation and lexical X/Y prose, never paths."""

    if "\\" in value:
        return False
    if (
        _PATH_INDICATOR.search(value) is not None
        or _NARRATIVE_PATH_INDICATOR.search(value) is not None
    ):
        return False
    remainder = value
    for pattern in _ALLOWED_SLASH_PATTERNS:
        remainder = pattern.sub("[public-slash]", remainder)

    def replace_lexical_pair(match: re.Match[str]) -> str:
        pair = match.group(0).lower()
        pair_terms = frozenset(
            component
            for side in pair.split("/", maxsplit=1)
            for component in re.split(r"[-_]+", side)
            if component
        )
        if pair_terms & _DANGEROUS_LEXICAL_PATH_TERMS:
            return match.group(0)
        outside = remainder[:match.start()] + remainder[match.end():]
        if (
            pair in _STANDALONE_LEXICAL_SLASH_PAIRS
            or any(character.isspace() for character in outside)
        ):
            return "[public-lexical-pair]"
        return match.group(0)

    remainder = _LEXICAL_SLASH_PAIR.sub(replace_lexical_pair, remainder)
    return "/" not in remainder


def _looks_like_structured_path(value: str) -> bool:
    stripped = value.strip()
    return (
        _WINDOWS_DRIVE_PATH.search(value) is not None
        or _TRAVERSAL_PATH.search(value) is not None
        or _UNC_PATH.search(value) is not None
        or _RELATIVE_FILE_PATH.search(value) is not None
        or _PATH_ASSIGNMENT.search(value) is not None
        or _STORAGE_LOCATION_ASSIGNMENT.search(value) is not None
        or stripped.startswith(("/", "\\", "./", ".\\", "~/", "~\\"))
        or not _slashes_are_allowed_public_notation(value)
    )


_PRIVATE_HOST_TERMS = (
    "internal", "private", "local", "localhost", "lan", "home", "corp",
)
_PRIVATE_HOST_LABEL = re.compile(
    r"^(?:internal|private|local|localhost|lan|home|corp)\d*$",
    re.IGNORECASE,
)
_COMPACT_PRIVATE_HOST_LABEL = re.compile(
    r"^(?:(?:minio|s3|redis|postgres(?:ql)?|mysql|mariadb|mssql|mongodb|"
    r"database|db|storage|objectstore)(?:internal|private|local|corp)|"
    r"(?:internal|private|local|corp)(?:minio|s3|redis|postgres(?:ql)?|mysql|"
    r"database|db|storage|objectstore))\d*$",
    re.IGNORECASE,
)
_INFRASTRUCTURE_HOST_TOKEN = re.compile(
    r"^(?:minio|s3|postgres(?:ql)?|redis|mysql|mariadb|mssql|mongodb|"
    r"database|db|objectstore|object-storage)\d*$",
    re.IGNORECASE,
)
_PUBLIC_STORAGE_SUFFIXES = (
    "storage.googleapis.com",
    "s3.amazonaws.com",
)
_PRIVATE_IP_ALIAS_SUFFIXES = ("sslip.io", "nip.io")
_NON_PUBLIC_DNS_SUFFIXES = (
    "test", "invalid", "example", "localhost", "local", "internal", "private",
    "localdomain", "arpa", "lan", "home", "corp", "svc", "consul", "onion",
)
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_NUMERIC_SHAPED_HOST = re.compile(r"^[0-9a-fx.]+$", re.IGNORECASE)
_PUBLIC_URL_TRAILING_PUNCTUATION = ".,;:!?)]}"


def _semantic_key_is_sensitive(value: object) -> bool:
    key = _normalized_key(value)
    if not key:
        return False
    terms = tuple(term for term in key.split("_") if term)
    compact = "".join(terms)
    if any(
        term in {
            "token", "secret", "password", "passwd", "passphrase", "credential",
            "authorization", "oauth", "session", "cookie", "signature", "sig",
        }
        for term in terms
    ):
        return True
    if (
        "token" in compact
        or "secret" in compact
        or "password" in compact
        or "signature" in compact
        or "authorization" in compact
        or "credential" in compact
        or compact in {"sig", "pwd", "jwt"}
        or compact.startswith(("xamzcredential", "xamzsecuritytoken"))
    ):
        return True
    if compact.endswith("key") and compact.removesuffix("key") in {
        "api", "access", "private", "client", "signing", "encryption", "auth",
        "authn", "authz", "authentication", "authorization", "oauth", "jwt",
        "session",
    }:
        return True
    for suffix in ("key", "code", "id"):
        if compact.endswith(suffix) and compact.removesuffix(suffix) in {
            "auth", "authn", "authz", "authentication", "authorization",
            "oauth", "jwt", "session", "access", "client", "signing",
        }:
            return True
    return "key" in terms and any(
        term in {"api", "access", "private", "client", "signing", "encryption"}
        for term in terms
    )


def _contains_sensitive_assignment(value: str) -> bool:
    return any(
        _semantic_key_is_sensitive(match.group("key"))
        for match in _ASSIGNED_VALUE.finditer(value)
    )


def _contains_password_hash(value: str) -> bool:
    return any(pattern.search(value) is not None for pattern in _PASSWORD_HASH_PATTERNS)


def _contains_secret_material(value: str) -> bool:
    return any((
        _SECRET_FAMILY.search(value) is not None,
        _contains_sensitive_assignment(value),
        _PRIVATE_KEY_HEADER.search(value) is not None,
        _contains_password_hash(value),
    ))


def _numeric_host_address(host: str):
    try:
        return ip_address(host)
    except ValueError:
        pass
    if re.fullmatch(
        r"(?:0x[0-9a-f]+|0[0-7]+|[0-9]+)"
        r"(?:\.(?:0x[0-9a-f]+|0[0-7]+|[0-9]+)){0,3}",
        host,
        re.IGNORECASE,
    ) is None:
        return None
    try:
        return IPv4Address(inet_aton(host))
    except (OSError, ValueError):
        return None


def _private_ip_alias_is_safe(host: str) -> bool:
    for suffix in _PRIVATE_IP_ALIAS_SUFFIXES:
        suffix_with_dot = "." + suffix
        if not host.endswith(suffix_with_dot):
            continue
        alias = host.removesuffix(suffix_with_dot)
        alias_labels = alias.split(".")
        found_address = False
        for start in range(len(alias_labels)):
            candidate = ".".join(alias_labels[start:]).replace("-", ".")
            numeric = _numeric_host_address(candidate)
            if numeric is None:
                continue
            found_address = True
            if not numeric.is_global:
                return False
        return found_address
    return True


def _hostname_is_public(host: str) -> bool:
    numeric = _numeric_host_address(host)
    if numeric is not None:
        return numeric.is_global
    if _NUMERIC_SHAPED_HOST.fullmatch(host) is not None:
        return False
    if not _private_ip_alias_is_safe(host):
        return False
    labels = tuple(label for label in host.split(".") if label)
    if (
        len(labels) < 2
        or any(_DNS_LABEL.fullmatch(label) is None for label in labels)
        or any(
            host == suffix or host.endswith("." + suffix)
            for suffix in _NON_PUBLIC_DNS_SUFFIXES
        )
    ):
        return False
    public_storage_suffix = next((
        suffix
        for suffix in _PUBLIC_STORAGE_SUFFIXES
        if host == suffix or host.endswith("." + suffix)
    ), None)
    labels_to_classify = labels
    if public_storage_suffix is not None:
        provider_label_count = len(public_storage_suffix.split("."))
        labels_to_classify = labels[:-provider_label_count]
    for label in labels_to_classify:
        components = tuple(part for part in re.split(r"[-_]+", label) if part)
        if any(_PRIVATE_HOST_LABEL.fullmatch(component) for component in components):
            return False
        if any(_COMPACT_PRIVATE_HOST_LABEL.fullmatch(component) for component in components):
            return False
    if public_storage_suffix is not None and any(
        _INFRASTRUCTURE_HOST_TOKEN.fullmatch(label)
        for label in labels_to_classify
    ):
        return False
    return public_storage_suffix is not None or bool(labels_to_classify)


def _component_contains_private_material(value: str) -> bool:
    canonical = _canonical_security_text(value)
    if _PERCENT_ESCAPE.search(canonical) is not None:
        return True
    if any(ord(character) < 32 or ord(character) == 127 for character in canonical):
        return True
    sql_segment_detected = any(
        _looks_like_sql_command(segment)
        for segment in re.split(r"[/\\?&#]+", canonical)
        if segment.strip()
    )
    return any((
        _WINDOWS_DRIVE_PATH.search(canonical) is not None,
        _TRAVERSAL_PATH.search(canonical) is not None,
        _UNC_PATH.search(canonical) is not None,
        _PATH_INDICATOR.search(canonical) is not None,
        "\\" in canonical,
        _HIERARCHICAL_URI.search(canonical) is not None,
        _SENSITIVE_OPAQUE_URI.search(canonical) is not None,
        _contains_secret_material(canonical),
        _CONSTRAINT_DIAGNOSTIC.search(canonical) is not None,
        _INTERNAL_INFRASTRUCTURE.search(canonical) is not None,
        sql_segment_detected,
        re.search(
            r"\b(?:sqlstate|traceback|stack[ _-]*trace)\b",
            canonical,
            re.IGNORECASE,
        ) is not None,
    ))


def _url_components_are_public(parsed) -> bool:
    canonical_path = _canonical_security_text(parsed.path)
    path_segments = tuple(
        segment for segment in canonical_path.split("/") if segment
    )
    if any(
        _semantic_key_is_sensitive(segment)
        and index + 1 < len(path_segments)
        for index, segment in enumerate(path_segments)
    ):
        return False
    if any(
        _component_contains_private_material(component)
        for component in (parsed.netloc, parsed.path, parsed.fragment)
    ):
        return False
    try:
        query_pairs = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=False,
            max_num_fields=100,
        )
    except ValueError:
        return False
    for key, item in query_pairs:
        if (
            _semantic_key_is_sensitive(key)
            or _component_contains_private_material(key)
            or _component_contains_private_material(item)
        ):
            return False
    return not _component_contains_private_material(parsed.query)


def _public_http_url_is_safe(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.netloc
    ):
        return False
    try:
        host = hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        return False
    if not host or any(character.isspace() for character in host):
        return False
    return (
        (port is None or 0 < port < 65536)
        and _hostname_is_public(host)
        and _url_components_are_public(parsed)
    )


def _mask_allowed_public_urls(value: str) -> str | None:
    """Validate URLs on the inspection copy and mask them before path checks."""

    parts: list[str] = []
    cursor = 0
    for match in _HIERARCHICAL_URI.finditer(value):
        matched = match.group(0)
        public_url = matched.rstrip(_PUBLIC_URL_TRAILING_PUNCTUATION)
        trailing = matched[len(public_url):]
        if not public_url or not _public_http_url_is_safe(public_url):
            return None
        parts.extend((value[cursor:match.start()], "[public-url]", trailing))
        cursor = match.end()
    parts.append(value[cursor:])
    return "".join(parts)


def _public_text_is_safe(
    value: str,
    *,
    document_name: bool = False,
    allow_public_urls: bool = False,
) -> bool:
    if not value.strip():
        return False
    if any(ord(character) < 32 and character not in "\r\n\t" for character in value):
        return False

    unicode_canonical = _normalize_security_unicode(value)
    canonical = _canonical_security_text(unicode_canonical)
    if document_name and (
        canonical in {".", ".."}
        or "/" in canonical
        or "\\" in canonical
    ):
        return False
    inspection = canonical
    if allow_public_urls:
        # Preserve URL delimiters while the URL parser decodes and inspects
        # every component. Canonicalize the remaining prose only after masking.
        masked = _mask_allowed_public_urls(unicode_canonical)
        if masked is None:
            return False
        inspection = _canonical_security_text(masked)
    if _PERCENT_ESCAPE.search(inspection) is not None:
        return False
    return not any((
        _looks_like_structured_path(inspection),
        _HIERARCHICAL_URI.search(inspection) is not None,
        _SENSITIVE_OPAQUE_URI.search(inspection) is not None,
        _SECRET_FAMILY.search(inspection) is not None,
        _contains_sensitive_assignment(inspection),
        _PRIVATE_KEY_HEADER.search(inspection) is not None,
        _contains_password_hash(inspection),
        _CONSTRAINT_DIAGNOSTIC.search(inspection) is not None,
        _INTERNAL_INFRASTRUCTURE.search(inspection) is not None,
        _looks_like_sql_command(inspection),
        re.search(r"\b(?:sqlstate|traceback|stack[ _-]*trace)\b", inspection, re.IGNORECASE)
        is not None,
    ))


def _sanitize_public_text(value: object, *, max_length: int) -> str:
    if not isinstance(value, str) or len(value) > max_length:
        return _REDACTED_PUBLIC_VALUE
    return value if _public_text_is_safe(value) else _REDACTED_PUBLIC_VALUE


def _sanitize_narrative_text(value: object, *, max_length: int) -> str:
    if not isinstance(value, str) or len(value) > max_length:
        return _REDACTED_PUBLIC_VALUE
    return (
        value
        if _public_text_is_safe(value, allow_public_urls=True)
        else _REDACTED_PUBLIC_VALUE
    )


def _sanitize_document_name(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 320
        or not _public_text_is_safe(value, document_name=True)
    ):
        return _REDACTED_PUBLIC_VALUE
    return value


def _safe_public_scalar(
    value: object,
    *,
    max_length: int,
    allow_public_urls: bool = False,
) -> object:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return _safe_public_scalar(
            value.value,
            max_length=max_length,
            allow_public_urls=allow_public_urls,
        )
    if isinstance(value, str):
        sanitizer = _sanitize_narrative_text if allow_public_urls else _sanitize_public_text
        return sanitizer(value, max_length=max_length)
    return _REDACTED_PUBLIC_VALUE


def _safe_public_value(
    value: object,
    *,
    max_length: int,
    allow_public_urls: bool = False,
) -> object:
    if isinstance(value, (tuple, list)):
        if len(value) > 100:
            return ()
        return tuple(
            _safe_public_scalar(
                item,
                max_length=max_length,
                allow_public_urls=allow_public_urls,
            )
            for item in value
        )
    return _safe_public_scalar(
        value,
        max_length=max_length,
        allow_public_urls=allow_public_urls,
    )


def _safe_enum_value(value: object, allowed: frozenset[str]) -> str | None:
    if isinstance(value, Enum):
        value = value.value
    return value if isinstance(value, str) and value in allowed else None


def _safe_uuid_value(value: object) -> str | None:
    if isinstance(value, UUID):
        return str(value)
    if not isinstance(value, str):
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None


def _safe_nonnegative_integer(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _safe_integer_value(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _safe_positive_integer(value: object) -> int | None:
    result = _safe_nonnegative_integer(value)
    return result if result is not None and result > 0 else None


def _safe_datetime_value(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if not isinstance(value, str) or len(value) > 80:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def _sanitize_override(value: Mapping[str, object]) -> _ImmutablePublicMapping:
    allowed_override_fields = frozenset(field.value for field in OverrideField)
    allowed_scopes = frozenset(scope.value for scope in DecisionScope)
    sanitized: dict[str, object] = {}
    for raw_key, raw_value in value.items():
        key = _normalized_key(raw_key)
        if key not in _OVERRIDE_FIELDS:
            continue
        if key == "decision_id":
            if (safe := _safe_uuid_value(raw_value)) is not None:
                sanitized[key] = safe
        elif key in {"field", "field_path"}:
            if (safe := _safe_enum_value(raw_value, allowed_override_fields)) is not None:
                sanitized[key] = safe
        elif key == "scope":
            if (safe := _safe_enum_value(raw_value, allowed_scopes)) is not None:
                sanitized[key] = safe
        elif key == "scope_id":
            sanitized[key] = _safe_public_value(raw_value, max_length=900)
        elif key == "value":
            sanitized[key] = _safe_public_value(
                raw_value,
                max_length=4000,
                allow_public_urls=True,
            )
        elif key == "created_at":
            if (safe := _safe_datetime_value(raw_value)) is not None:
                sanitized[key] = safe
    return _ImmutablePublicMapping(sanitized)


def _sanitize_evidence_summary(value: Mapping[str, object]) -> _ImmutablePublicMapping:
    sanitized: dict[str, object] = {}
    for raw_key, raw_value in value.items():
        key = _normalized_key(raw_key)
        if key not in _EVIDENCE_SUMMARY_FIELDS:
            continue
        if key == "available" and isinstance(raw_value, bool):
            sanitized[key] = raw_value
        elif key == "count":
            if (safe := _safe_nonnegative_integer(raw_value)) is not None:
                sanitized[key] = safe
        elif key == "page":
            if (safe := _safe_positive_integer(raw_value)) is not None:
                sanitized[key] = safe
        elif key == "document_name" and isinstance(raw_value, str):
            sanitized[key] = _sanitize_document_name(raw_value)
        elif key in {"section", "locator", "fragment"} and isinstance(raw_value, str):
            max_length = {"section": 240, "locator": 500, "fragment": 4000}[key]
            sanitized[key] = _sanitize_narrative_text(raw_value, max_length=max_length)
        elif key == "stream_path" and isinstance(raw_value, str):
            try:
                sanitized[key] = EvidenceResponse.require_application_relative_stream_path(raw_value)
            except ValueError:
                continue
    return _ImmutablePublicMapping(sanitized)


def _sanitize_facets(value: Mapping[str, Mapping[str, int]]) -> _ImmutablePublicMapping:
    expected_values = {
        "statuses": frozenset(status.value for status in ReviewCaseStatus),
        "case_types": frozenset(case_type.value for case_type in ReviewCaseType),
    }
    sanitized: dict[str, object] = {}
    for raw_key, counts in value.items():
        key = _normalized_key(raw_key)
        allowed_values = expected_values.get(key)
        if allowed_values is None or not isinstance(counts, Mapping):
            continue
        safe_counts = {
            normalized: count
            for raw_name, count in counts.items()
            if (normalized := _normalized_key(raw_name)) in allowed_values
            and isinstance(count, int)
            and not isinstance(count, bool)
            and count >= 0
        }
        sanitized[key] = _ImmutablePublicMapping(safe_counts)
    return _ImmutablePublicMapping(sanitized)


def _sanitize_audit_payload(value: Mapping[str, object]) -> _ImmutablePublicMapping:
    event_types = frozenset(event.value for event in AuditEventType)
    decision_types = frozenset(decision.value for decision in ReviewDecisionType)
    case_statuses = frozenset(status.value for status in ReviewCaseStatus)
    sanitized: dict[str, object] = {}
    for raw_key, raw_value in value.items():
        key = _normalized_key(raw_key)
        if key not in _AUDIT_PAYLOAD_FIELDS:
            continue
        if key == "kind":
            if (safe := _safe_enum_value(raw_value, event_types)) is not None:
                sanitized[key] = safe
        elif key == "schema_version":
            if (safe := _safe_positive_integer(raw_value)) is not None:
                sanitized[key] = safe
        elif key in {"decision_id", "review_item_id"}:
            if (safe := _safe_uuid_value(raw_value)) is not None:
                sanitized[key] = safe
        elif key == "decision_type":
            if (safe := _safe_enum_value(raw_value, decision_types)) is not None:
                sanitized[key] = safe
        elif key in {"previous_case_status", "resulting_case_status"}:
            if (safe := _safe_enum_value(raw_value, case_statuses)) is not None:
                sanitized[key] = safe
        elif key == "kpi_effect" and isinstance(raw_value, (tuple, list)):
            sanitized[key] = (
                tuple(
                    _sanitize_audit_kpi_effect(item)
                    for item in raw_value
                    if isinstance(item, Mapping)
                )
                if len(raw_value) <= 100
                else ()
            )
    return _ImmutablePublicMapping(sanitized)


def _sanitize_audit_kpi_effect(value: Mapping[str, object]) -> _ImmutablePublicMapping:
    sanitized: dict[str, object] = {}
    for raw_key, raw_value in value.items():
        key = _normalized_key(raw_key)
        if key not in _KPI_EFFECT_FIELDS:
            continue
        if key == "metric" and isinstance(raw_value, str):
            sanitized[key] = _sanitize_public_text(raw_value, max_length=160)
        elif key in {"before", "after", "delta"}:
            if (safe := _safe_integer_value(raw_value)) is not None:
                sanitized[key] = safe
    return _ImmutablePublicMapping(sanitized)


def _sanitize_audit_item(value: Mapping[str, object]) -> _ImmutablePublicMapping:
    event_types = frozenset(event.value for event in AuditEventType)
    sanitized: dict[str, object] = {}
    for raw_key, raw_value in value.items():
        key = _normalized_key(raw_key)
        if key not in _AUDIT_ITEM_FIELDS:
            continue
        if key == "event_type":
            if (safe := _safe_enum_value(raw_value, event_types)) is not None:
                sanitized[key] = safe
        elif key in {"review_item_id", "decision_id", "correlation_id"}:
            if (safe := _safe_uuid_value(raw_value)) is not None:
                sanitized[key] = safe
        elif key in {"id", "actor_id"}:
            if (safe_int := _safe_nonnegative_integer(raw_value)) is not None:
                sanitized[key] = safe_int
            elif (safe_uuid := _safe_uuid_value(raw_value)) is not None:
                sanitized[key] = safe_uuid
        elif key == "created_at":
            if (safe := _safe_datetime_value(raw_value)) is not None:
                sanitized[key] = safe
        elif key == "summary" and isinstance(raw_value, str):
            sanitized[key] = _sanitize_narrative_text(raw_value, max_length=4000)
        elif key == "payload" and isinstance(raw_value, Mapping):
            sanitized[key] = _sanitize_audit_payload(raw_value)
    return _ImmutablePublicMapping(sanitized)


def _plain_public_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain_public_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return tuple(_plain_public_value(item) for item in value)
    return value


def sanitize_error_details(details: Mapping[str, object] | None) -> Mapping[str, object] | None:
    if details is None:
        return None
    sanitized: dict[str, object] = {}
    for key, value in details.items():
        normalized = _normalized_key(key)
        allowed_values = _ERROR_DETAIL_VALUES.get(normalized)
        if allowed_values is not None:
            if isinstance(value, Enum):
                value = value.value
            if isinstance(value, str) and value in allowed_values:
                sanitized[normalized] = value
        elif (
            normalized in _ERROR_INTEGER_DETAIL_KEYS
            and isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
        ):
            sanitized[normalized] = value
    return _ImmutablePublicMapping(sanitized)


class HumanReviewErrorResponse(_ClosedModel):
    code: HumanReviewErrorCode
    message: str = Field(min_length=1, max_length=500)
    correlation_id: UUID
    details: ErrorDetailsMapping | None = None

    @field_validator("message")
    @classmethod
    def sanitize_message(cls, value: str, info) -> str:
        _nonblank(value, "message")
        return _ERROR_SAFE_MESSAGES[info.data["code"]]

    @field_serializer("message")
    def serialize_safe_message(self, value: str) -> str:
        return _ERROR_SAFE_MESSAGES[self.code]

    @field_validator("details", mode="after")
    @classmethod
    def sanitize_details(cls, value):
        return sanitize_error_details(value)

    @field_serializer("details")
    def serialize_sanitized_details(self, value):
        sanitized = sanitize_error_details(value) if isinstance(value, Mapping) else None
        return _plain_public_value(sanitized)


class HumanReviewApiError(HumanReviewDomainError):
    code: HumanReviewErrorCode = "HUMAN_REVIEW_VALIDATION"


class HumanReviewValidationError(HumanReviewApiError):
    code: HumanReviewErrorCode = "HUMAN_REVIEW_VALIDATION"


CASE_ACTION_DECISION_TYPES = MappingProxyType({
    (case_type, action): decision_types
    for case_type, action, decision_types in (
        (ReviewCaseType.PERSON_IDENTITY, "approve", frozenset({ReviewDecisionType.VALIDATED})),
        (ReviewCaseType.PERSON_IDENTITY, "correct", frozenset({ReviewDecisionType.CORRECTED})),
        (ReviewCaseType.PERSON_IDENTITY, "link", frozenset({ReviewDecisionType.LINKED, ReviewDecisionType.MAINTAINED_SEPARATE, ReviewDecisionType.SEPARATED})),
        (ReviewCaseType.AUTHOR_IDENTITY, "approve", frozenset({ReviewDecisionType.VALIDATED})),
        (ReviewCaseType.AUTHOR_IDENTITY, "correct", frozenset({ReviewDecisionType.CORRECTED})),
        (ReviewCaseType.AUTHOR_IDENTITY, "link", frozenset({ReviewDecisionType.LINKED, ReviewDecisionType.MAINTAINED_SEPARATE, ReviewDecisionType.SEPARATED})),
        (ReviewCaseType.PRODUCT, "approve", frozenset({ReviewDecisionType.VALIDATED})),
        (ReviewCaseType.PRODUCT, "correct", frozenset({ReviewDecisionType.CORRECTED})),
        (ReviewCaseType.PROJECT_DIRECTOR_RELATION, "approve", frozenset({ReviewDecisionType.VALIDATED})),
        (ReviewCaseType.PROJECT_DIRECTOR_RELATION, "correct", frozenset({ReviewDecisionType.CORRECTED})),
        (ReviewCaseType.PROJECT_DIRECTOR_RELATION, "link", frozenset({ReviewDecisionType.LINKED, ReviewDecisionType.MAINTAINED_SEPARATE, ReviewDecisionType.SEPARATED})),
        (ReviewCaseType.EXTERNAL_IDENTITY, "approve", frozenset({ReviewDecisionType.VALIDATED})),
        (ReviewCaseType.EXTERNAL_IDENTITY, "correct", frozenset({ReviewDecisionType.CORRECTED})),
        (ReviewCaseType.EXTERNAL_IDENTITY, "link", frozenset({ReviewDecisionType.LINKED, ReviewDecisionType.MAINTAINED_SEPARATE, ReviewDecisionType.SEPARATED})),
        (ReviewCaseType.POSSIBLE_DUPLICATE, "link", frozenset({ReviewDecisionType.MERGED, ReviewDecisionType.MAINTAINED_SEPARATE, ReviewDecisionType.SEPARATED})),
    )
})


def derive_decision_type(
    case_type: ReviewCaseType,
    action: DecisionAction,
    payload: DecisionPayloadV1,
) -> ReviewDecisionType:
    try:
        resolved_case_type = ReviewCaseType(case_type)
    except (TypeError, ValueError) as exc:
        raise IncompatibleDecisionError("Case type does not support scientific decisions") from exc
    if payload.case_type != resolved_case_type.value:
        raise IncompatibleDecisionError("Payload case type does not match the review case")
    allowed = CASE_ACTION_DECISION_TYPES.get((resolved_case_type, action))
    if allowed is None:
        raise IncompatibleDecisionError("Action is incompatible with the review case type")
    if action != "link":
        if isinstance(payload, (PersonDecisionPayload, ExternalDecisionPayload)) and payload.resolution is not None:
            raise IncompatibleDecisionError("Resolution is incompatible with the requested action")
        return next(iter(allowed))
    if isinstance(payload, (PersonDecisionPayload, ExternalDecisionPayload, DuplicateDecisionPayload)):
        resolution = payload.resolution
    elif isinstance(payload, RelationDecisionPayload):
        resolution = payload.relationship_status
    else:
        resolution = None
    if resolution is None:
        raise IncompatibleDecisionError("Link action requires a compatible resolution")
    decision_type = ReviewDecisionType(resolution)
    if decision_type not in allowed:
        raise IncompatibleDecisionError("Resolution is incompatible with the review case type")
    return decision_type


_UNSET = object()


def validate_apply_decision(
    request: ApplyDecisionRequest,
    *,
    case_type: ReviewCaseType | None = None,
    normalized_value: object = _UNSET,
    final_value: object = _UNSET,
) -> ReviewDecisionType:
    try:
        decision_type = derive_decision_type(
            case_type or ReviewCaseType(request.payload.case_type),
            request.action,
            request.payload,
        )
    except IncompatibleDecisionError as error:
        raise IncompatibleDecisionError(
            str(error),
            correlation_id=request.correlation_id,
            details=error.details,
        ) from error
    comparison_proven = normalized_value is not _UNSET and final_value is not _UNSET
    value_changed = (
        comparison_proven and normalized_value != final_value
    )
    reason_missing = request.reason is None or not request.reason.strip()
    reason_required = (
        request.action == "correct"
        or decision_type in {
            ReviewDecisionType.MERGED,
            ReviewDecisionType.MAINTAINED_SEPARATE,
            ReviewDecisionType.SEPARATED,
        }
        or value_changed
        or (request.action != "approve")
        or (reason_missing and not comparison_proven)
    )
    if reason_required and reason_missing:
        raise HumanReviewValidationError(
            "A nonblank reason is required for this decision",
            correlation_id=request.correlation_id,
            details={"field": "reason"},
        )
    return decision_type
