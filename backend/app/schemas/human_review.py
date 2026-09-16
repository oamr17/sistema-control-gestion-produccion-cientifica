from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator, model_validator

from app.models.human_review_enums import (
    CanonicalIdentityType,
    OverrideField,
    OverrideScope,
    ReviewCaseType,
    ReviewCaseStatus,
    ReviewTargetTable,
    ScientificStatus,
)


class _ClosedFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StableTargetV1(_ClosedFrozenModel):
    schema_version: Literal[1]
    case_type: ReviewCaseType
    target_table: ReviewTargetTable
    target_pk: int | None = None
    document_key: str
    source_revision: str | None = None
    source_page: int | None = None
    source_section: str
    row_or_block_id: str
    field_path: OverrideField | Literal["case"]
    raw_value_sha256: str
    period_id: int | None = None
    relationship_key: str | None = None

    @field_validator("document_key", "source_section", "row_or_block_id")
    @classmethod
    def require_nonblank_stable_locator(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("stable locators must contain a non-whitespace character")
        return value

    @field_validator("raw_value_sha256")
    @classmethod
    def require_lowercase_sha256(cls, value: str) -> str:
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("raw_value_sha256 must be 64 lowercase hexadecimal characters")
        return value


class ScalarOverrideValueV1(_ClosedFrozenModel):
    kind: Literal["string", "integer", "decimal", "boolean", "null"]
    string_value: str | None = None
    integer_value: StrictInt | None = None
    decimal_value: StrictStr | None = None
    boolean_value: StrictBool | None = None

    @field_validator("decimal_value")
    @classmethod
    def require_finite_decimal_string(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("decimal_value must not be empty")
        try:
            parsed = Decimal(value)
        except InvalidOperation as error:
            raise ValueError("decimal_value must represent a decimal") from error
        if not parsed.is_finite():
            raise ValueError("decimal_value must be finite")
        return value

    @model_validator(mode="after")
    def require_exact_value_for_kind(self) -> ScalarOverrideValueV1:
        values = {
            "string": self.string_value,
            "integer": self.integer_value,
            "decimal": self.decimal_value,
            "boolean": self.boolean_value,
        }
        populated = {name for name, value in values.items() if value is not None}
        expected = set() if self.kind == "null" else {self.kind}
        if populated != expected:
            raise ValueError(f"kind {self.kind!r} requires exactly its matching scalar value")
        return self


class ProjectionAliasSnapshotV1(_ClosedFrozenModel):
    schema_version: Literal[1]
    alias_original: str
    alias_normalized: str

    @field_validator("alias_original", "alias_normalized")
    @classmethod
    def require_nonblank_alias(cls, value: str, info) -> str:
        if not value.strip():
            raise ValueError(f"{info.field_name} cannot be blank")
        return value


class ProjectionOverrideSnapshotV1(_ClosedFrozenModel):
    schema_version: Literal[1]
    field_path: OverrideField
    projected_value: ScalarOverrideValueV1
    scope: OverrideScope
    stable_target_key: str = Field(
        max_length=128,
        pattern=r"^b2b:v1:[a-z_]+:[0-9a-f]{64}$",
    )
    target_table: ReviewTargetTable
    target_pk: StrictInt | None = None
    document_key: str | None = None
    period_id: StrictInt | None = None
    relationship_key: str | None = None
    locked: StrictBool

    @model_validator(mode="after")
    def require_compatible_scope_context(self) -> ProjectionOverrideSnapshotV1:
        contexts = {
            "document_key": self.document_key,
            "period_id": self.period_id,
            "relationship_key": self.relationship_key,
        }
        required_context = {
            OverrideScope.RECORD: None,
            OverrideScope.DOCUMENT: "document_key",
            OverrideScope.PERIOD: "period_id",
            OverrideScope.RELATIONSHIP: "relationship_key",
            OverrideScope.GLOBAL_IDENTITY: None,
        }[self.scope]
        for name, value in contexts.items():
            if name == required_context:
                if value is None or isinstance(value, str) and not value.strip():
                    raise ValueError(f"scope {self.scope.value!r} requires nonblank {name}")
            elif value is not None:
                raise ValueError(f"scope {self.scope.value!r} forbids {name}")
        if self.scope is OverrideScope.GLOBAL_IDENTITY and self.field_path not in {
            OverrideField.CANONICAL_IDENTITY_KEY,
            OverrideField.CANONICAL_NAME,
        }:
            raise ValueError("global_identity scope only supports canonical identity fields")
        return self


class IdentityProjectionSnapshotV1(_ClosedFrozenModel):
    schema_version: Literal[1]
    canonical_identity_key: str
    canonical_name: str
    identity_type: CanonicalIdentityType
    aliases: tuple[ProjectionAliasSnapshotV1, ...]

    @field_validator("canonical_identity_key", "canonical_name")
    @classmethod
    def require_nonblank_identity_value(cls, value: str, info) -> str:
        if not value.strip():
            raise ValueError(f"{info.field_name} cannot be blank")
        return value

    @field_validator("aliases")
    @classmethod
    def sort_unique_aliases(
        cls,
        aliases: tuple[ProjectionAliasSnapshotV1, ...],
    ) -> tuple[ProjectionAliasSnapshotV1, ...]:
        normalized = tuple(alias.alias_normalized for alias in aliases)
        if len(normalized) != len(set(normalized)):
            raise ValueError("projection alias normalized values must be unique")
        return tuple(sorted(aliases, key=lambda alias: (alias.alias_normalized, alias.alias_original)))


def _override_snapshot_key(
    item: ProjectionOverrideSnapshotV1,
) -> tuple[str, str, str, str, int, str]:
    return (
        item.stable_target_key,
        item.field_path.value,
        item.scope.value,
        item.document_key or "",
        item.period_id if item.period_id is not None else -1,
        item.relationship_key or "",
    )


class ReviewProjectionSnapshotV1(_ClosedFrozenModel):
    schema_version: Literal[1]
    case_status: ReviewCaseStatus
    scientific_status: ScientificStatus
    current_decision_id: UUID | None
    identity: IdentityProjectionSnapshotV1 | None
    overrides: tuple[ProjectionOverrideSnapshotV1, ...]

    @field_validator("overrides")
    @classmethod
    def sort_unique_overrides(
        cls,
        overrides: tuple[ProjectionOverrideSnapshotV1, ...],
    ) -> tuple[ProjectionOverrideSnapshotV1, ...]:
        keys = tuple(_override_snapshot_key(item) for item in overrides)
        if len(keys) != len(set(keys)):
            raise ValueError("projection override target, field, scope and context must be unique")
        return tuple(sorted(overrides, key=_override_snapshot_key))


class _ProjectionDecisionPayload(_ClosedFrozenModel):
    projection_before: ReviewProjectionSnapshotV1 | None = None
    projection_after: ReviewProjectionSnapshotV1 | None = None

    @model_validator(mode="after")
    def require_complete_projection_pair(self) -> _ProjectionDecisionPayload:
        if (self.projection_before is None) != (self.projection_after is None):
            raise ValueError(
                "projection_before and projection_after must be provided together"
            )
        return self


class IdentityDecisionPayloadV1(_ProjectionDecisionPayload):
    kind: Literal["identity"]
    schema_version: Literal[1]
    canonical_identity_key: str
    canonical_name: str
    identity_type: CanonicalIdentityType
    alias_original: str | None = None
    alias_normalized: str | None = None


class FieldOverridePayloadV1(_ProjectionDecisionPayload):
    kind: Literal["field_override"]
    schema_version: Literal[1]
    field_path: OverrideField
    value: ScalarOverrideValueV1
    scope: OverrideScope
    stable_target_key: str | None = None
    document_key: str | None = None
    period_id: int | None = None
    relationship_key: str | None = None

    @model_validator(mode="after")
    def require_compatible_scope_context(self) -> FieldOverridePayloadV1:
        contexts = {
            "stable_target_key": self.stable_target_key,
            "document_key": self.document_key,
            "period_id": self.period_id,
            "relationship_key": self.relationship_key,
        }
        required_context = {
            OverrideScope.RECORD: "stable_target_key",
            OverrideScope.DOCUMENT: "document_key",
            OverrideScope.PERIOD: "period_id",
            OverrideScope.RELATIONSHIP: "relationship_key",
            OverrideScope.GLOBAL_IDENTITY: None,
        }[self.scope]
        for name, value in contexts.items():
            if name == required_context:
                if value is None or isinstance(value, str) and not value.strip():
                    raise ValueError(f"scope {self.scope.value!r} requires nonblank {name}")
            elif value is not None:
                raise ValueError(f"scope {self.scope.value!r} forbids {name}")

        if self.scope is OverrideScope.RECORD:
            if len(self.stable_target_key) > 128 or re.fullmatch(
                r"b2b:v1:[a-z_]+:[0-9a-f]{64}", self.stable_target_key
            ) is None:
                raise ValueError("record stable_target_key is malformed or exceeds 128 characters")
        if self.scope is OverrideScope.GLOBAL_IDENTITY and self.field_path not in {
            OverrideField.CANONICAL_IDENTITY_KEY,
            OverrideField.CANONICAL_NAME,
        }:
            raise ValueError("global_identity scope only supports canonical identity fields")
        return self


class IdentityMergePayloadV1(_ProjectionDecisionPayload):
    kind: Literal["identity_merge"]
    schema_version: Literal[1]
    target_identity_key: str
    member_stable_target_keys: tuple[str, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def require_unique_members(self) -> IdentityMergePayloadV1:
        if len(set(self.member_stable_target_keys)) != len(self.member_stable_target_keys):
            raise ValueError("member_stable_target_keys must be unique")
        return self


class IdentitySeparationMemberV1(_ClosedFrozenModel):
    stable_target_key: str
    target_identity_key: str
    target_canonical_name: str


class IdentitySeparationPayloadV1(_ProjectionDecisionPayload):
    kind: Literal["identity_separation"]
    schema_version: Literal[1]
    source_identity_key: str
    assignments: tuple[IdentitySeparationMemberV1, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_targets(self) -> IdentitySeparationPayloadV1:
        stable_target_keys = tuple(assignment.stable_target_key for assignment in self.assignments)
        if len(set(stable_target_keys)) != len(stable_target_keys):
            raise ValueError("assignment stable_target_key values must be unique")
        return self


class MaintainSeparatePayloadV1(_ProjectionDecisionPayload):
    kind: Literal["maintain_separate"]
    schema_version: Literal[1]
    stable_target_keys: tuple[str, ...] = Field(min_length=2)
    identity_keys: tuple[str, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def require_parallel_unique_members(self) -> MaintainSeparatePayloadV1:
        if len(self.stable_target_keys) != len(self.identity_keys):
            raise ValueError("stable_target_keys and identity_keys must have the same length")
        if len(set(self.stable_target_keys)) != len(self.stable_target_keys):
            raise ValueError("stable_target_keys must be unique")
        if len(set(self.identity_keys)) != len(self.identity_keys):
            raise ValueError("identity_keys must be unique")
        return self


class DecisionReversalPayloadV1(_ProjectionDecisionPayload):
    projection_before: ReviewProjectionSnapshotV1
    projection_after: ReviewProjectionSnapshotV1
    kind: Literal["decision_reversal"]
    schema_version: Literal[1]
    decision_id_to_revert: UUID
    restore_decision_id: UUID | None = None


DecisionPayloadV1 = Annotated[
    IdentityDecisionPayloadV1
    | IdentityMergePayloadV1
    | IdentitySeparationPayloadV1
    | MaintainSeparatePayloadV1
    | FieldOverridePayloadV1
    | DecisionReversalPayloadV1,
    Field(discriminator="kind"),
]
