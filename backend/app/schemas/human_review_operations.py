from __future__ import annotations

from datetime import datetime
import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.models.human_review_enums import (
    AuditEventType,
    BackfillSourceMembership,
    B2BCapability,
    CanonicalIdentityType,
    OverrideField,
    ReviewCaseStatus,
    ReviewCaseType,
    ReviewDecisionType,
    ReviewTargetTable,
    ScientificStatus,
)
from app.schemas.human_review import StableTargetV1


class TableDigestV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str
    row_count: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ParticipantMetricsV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_identities: int = Field(ge=0)
    participations: int = Field(ge=0)
    roles: int = Field(ge=0)
    authorships: int = Field(ge=0)
    pending: int = Field(ge=0)
    external_detected: int = Field(ge=0)
    external_kpi_eligible: int = Field(ge=0)
    external_pending: int = Field(ge=0)


class B2B1InvariantSnapshotV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    captured_at: datetime
    migration_versions: tuple[str, ...]
    digests: tuple[TableDigestV1, ...]
    eligible_products: int = Field(ge=0)
    participant_metrics: ParticipantMetricsV1

    @model_validator(mode="after")
    def digest_labels_are_unique(self) -> B2B1InvariantSnapshotV1:
        labels = tuple(digest.label for digest in self.digests)
        if len(labels) != len(set(labels)):
            raise ValueError("duplicate digest labels are invalid")
        return self


class _ClosedAuditModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ApprovedAccountAssignmentV1(_ClosedAuditModel):
    user_id: int = Field(gt=0)
    email: EmailStr
    capability: B2BCapability


class ApprovedAccountAssignmentsV1(_ClosedAuditModel):
    schema_version: Literal[1]
    approval_reference: str = Field(min_length=1, max_length=240)
    assignments: tuple[ApprovedAccountAssignmentV1, ...] = Field(min_length=1)

    @field_validator("approval_reference")
    @classmethod
    def reject_blank_approval_reference(cls, value: str) -> str:
        return _require_nonblank(value, "approval_reference")

    @model_validator(mode="after")
    def reject_duplicate_or_contradictory_assignments(
        self,
    ) -> ApprovedAccountAssignmentsV1:
        user_ids = tuple(assignment.user_id for assignment in self.assignments)
        emails = tuple(str(assignment.email) for assignment in self.assignments)
        if len(user_ids) != len(set(user_ids)):
            raise ValueError("duplicate or contradictory user_id assignments are invalid")
        if len(emails) != len(set(emails)):
            raise ValueError("duplicate or contradictory email assignments are invalid")
        return self


class CapabilityAssignmentPlanEntryV1(_ClosedAuditModel):
    user_id: int = Field(gt=0)
    email: EmailStr
    capability: B2BCapability
    action: Literal["insert", "unchanged", "conflict"]
    reason: str = Field(min_length=1, max_length=240)

    @field_validator("reason")
    @classmethod
    def reject_blank_reason(cls, value: str) -> str:
        return _require_nonblank(value, "reason")


class CapabilityAssignmentPlanV1(_ClosedAuditModel):
    schema_version: Literal[1]
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    entries: tuple[CapabilityAssignmentPlanEntryV1, ...]
    insert_count: int = Field(ge=0)
    unchanged_count: int = Field(ge=0)
    conflict_count: int = Field(ge=0)

    @model_validator(mode="after")
    def counts_match_entries(self) -> CapabilityAssignmentPlanV1:
        actual = {
            action: sum(entry.action == action for entry in self.entries)
            for action in ("insert", "unchanged", "conflict")
        }
        expected = {
            "insert": self.insert_count,
            "unchanged": self.unchanged_count,
            "conflict": self.conflict_count,
        }
        if actual != expected:
            raise ValueError("capability assignment plan counts do not match entries")
        return self


class CapabilityAssignmentResultV1(_ClosedAuditModel):
    schema_version: Literal[1]
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    inserted: int = Field(ge=0)
    unchanged: int = Field(ge=0)
    audit_events_created: int = Field(ge=0)

    @model_validator(mode="after")
    def audit_count_matches_insertions(self) -> CapabilityAssignmentResultV1:
        if self.audit_events_created != self.inserted:
            raise ValueError("each inserted capability requires exactly one audit event")
        return self


class CaseBackfilledAuditPayloadV1(_ClosedAuditModel):
    kind: Literal["case_backfilled"]
    schema_version: Literal[1]
    review_item_id: UUID
    case_type: ReviewCaseType
    stable_target_key: str = Field(
        max_length=128,
        pattern=r"^b2b:v1:[a-z_]+:[0-9a-f]{64}$",
    )


class LockedDecisionImportedAuditPayloadV1(_ClosedAuditModel):
    kind: Literal["locked_decision_imported"]
    schema_version: Literal[1]
    decision_id: UUID
    source_table: ReviewTargetTable
    source_id: int
    legacy_actor_identifier: str = Field(min_length=1, max_length=180)
    legacy_decided_at: datetime


class IdentityCreatedAuditPayloadV1(_ClosedAuditModel):
    kind: Literal["identity_created"]
    schema_version: Literal[1]
    canonical_identity_id: UUID
    canonical_identity_key: str = Field(min_length=1)
    origin: Literal["b1_locked", "human"]


class AliasCreatedAuditPayloadV1(_ClosedAuditModel):
    kind: Literal["alias_created"]
    schema_version: Literal[1]
    person_alias_id: UUID
    canonical_identity_id: UUID
    alias_normalized: str = Field(min_length=1)


class OverrideCreatedAuditPayloadV1(_ClosedAuditModel):
    kind: Literal["override_created"]
    schema_version: Literal[1]
    field_override_id: UUID
    field_path: OverrideField
    stable_target_key: str = Field(
        max_length=128,
        pattern=r"^b2b:v1:[a-z_]+:[0-9a-f]{64}$",
    )


class CapabilityChangedAuditPayloadV1(_ClosedAuditModel):
    kind: Literal["capability_changed"]
    schema_version: Literal[1]
    assignment_id: UUID
    user_id: int
    capability: B2BCapability
    action: Literal["assigned", "revoked"]
    approved_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AuditCorrectionPayloadV1(_ClosedAuditModel):
    kind: Literal["audit_correction"]
    schema_version: Literal[1]
    corrects_event_id: UUID
    reason: str = Field(min_length=1)
    replacement_payload_schema: str = Field(min_length=1, max_length=80)
    replacement_payload_version: Literal[1]
    replacement_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class FunctionalReversalAuditPayloadV1(_ClosedAuditModel):
    kind: Literal["functional_reversal"]
    schema_version: Literal[1]
    new_decision_id: UUID
    reverted_decision_id: UUID
    restored_decision_id: UUID | None
    review_item_id: UUID


class ScientificDecisionKpiEffectItemV1(_ClosedAuditModel):
    metric: str = Field(min_length=1, max_length=120)
    before: int
    after: int
    delta: int


class ScientificDecisionAppliedAuditPayloadV1(_ClosedAuditModel):
    kind: Literal["scientific_decision_applied"]
    schema_version: Literal[1]
    decision_id: UUID
    decision_type: ReviewDecisionType
    previous_case_status: ReviewCaseStatus
    resulting_case_status: ReviewCaseStatus
    kpi_effect: tuple[ScientificDecisionKpiEffectItemV1, ...]
    review_item_id: UUID


AuditPayloadV1 = Annotated[
    CaseBackfilledAuditPayloadV1
    | LockedDecisionImportedAuditPayloadV1
    | IdentityCreatedAuditPayloadV1
    | AliasCreatedAuditPayloadV1
    | OverrideCreatedAuditPayloadV1
    | CapabilityChangedAuditPayloadV1
    | AuditCorrectionPayloadV1
    | FunctionalReversalAuditPayloadV1
    | ScientificDecisionAppliedAuditPayloadV1,
    Field(discriminator="kind"),
]


_PAYLOAD_KIND_BY_EVENT_TYPE = {
    AuditEventType.CASE_BACKFILLED: "case_backfilled",
    AuditEventType.LOCKED_DECISION_IMPORTED: "locked_decision_imported",
    AuditEventType.IDENTITY_CREATED: "identity_created",
    AuditEventType.ALIAS_CREATED: "alias_created",
    AuditEventType.OVERRIDE_CREATED: "override_created",
    AuditEventType.CAPABILITY_ASSIGNED: "capability_changed",
    AuditEventType.CAPABILITY_REVOKED: "capability_changed",
    AuditEventType.AUDIT_CORRECTED: "audit_correction",
    AuditEventType.FUNCTIONAL_REVERSION: "functional_reversal",
    AuditEventType.SCIENTIFIC_DECISION_APPLIED: "scientific_decision_applied",
}


def _require_nonblank(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} cannot be blank")
    return value


def _require_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


_MEMBERSHIP_RANK = {
    membership: rank for rank, membership in enumerate(BackfillSourceMembership)
}
_CASE_TYPE_RANK = {case_type: rank for rank, case_type in enumerate(ReviewCaseType)}
_TARGET_TABLE_RANK = {
    target_table: rank for rank, target_table in enumerate(ReviewTargetTable)
}
_BACKFILL_SECRET_PATTERN = re.compile(
    r"(?i)(?:password|passwd|database_url|postgresql://|postgres://|bearer\s+|"
    r"(?:access|auth|api)[_-]?token|client[_-]?secret|private[_-]?key)"
)


def _candidate_stable_target_key(candidate: BackfillCandidateV1) -> str:
    from app.services.human_review_targets import build_stable_target_key

    return build_stable_target_key(candidate.stable_target)


class SourcePopulationCountV1(_ClosedAuditModel):
    population: BackfillSourceMembership
    row_count: int = Field(ge=0)
    stable_target_count: int = Field(ge=0)

    @model_validator(mode="after")
    def stable_targets_do_not_exceed_rows(self) -> SourcePopulationCountV1:
        if self.stable_target_count > self.row_count:
            raise ValueError("stable_target_count cannot exceed row_count")
        return self


class SourcePopulationOverlapV1(_ClosedAuditModel):
    populations: tuple[BackfillSourceMembership, ...] = Field(min_length=2)
    stable_target_count: int = Field(ge=0)

    @field_validator("populations")
    @classmethod
    def sort_unique_populations(
        cls,
        populations: tuple[BackfillSourceMembership, ...],
    ) -> tuple[BackfillSourceMembership, ...]:
        if len(populations) != len(set(populations)):
            raise ValueError("overlap populations must be unique")
        return tuple(sorted(populations, key=_MEMBERSHIP_RANK.__getitem__))


class BackfillCandidateV1(_ClosedAuditModel):
    case_type: ReviewCaseType
    stable_target: StableTargetV1
    source_table: ReviewTargetTable
    source_id: int = Field(gt=0)
    memberships: tuple[BackfillSourceMembership, ...] = Field(min_length=1)
    case_status: ReviewCaseStatus
    scientific_status: ScientificStatus
    possible_kpi_impact: bool

    @field_validator("memberships")
    @classmethod
    def sort_unique_memberships(
        cls,
        memberships: tuple[BackfillSourceMembership, ...],
    ) -> tuple[BackfillSourceMembership, ...]:
        if len(memberships) != len(set(memberships)):
            raise ValueError("candidate memberships must be unique")
        return tuple(sorted(memberships, key=_MEMBERSHIP_RANK.__getitem__))

    @model_validator(mode="after")
    def enforce_closed_population_matrix(self) -> BackfillCandidateV1:
        if self.stable_target.target_table is not self.source_table:
            raise ValueError("candidate and stable target source_table must match")

        expected_target_case = self.case_type
        if self.case_type is ReviewCaseType.POSSIBLE_DUPLICATE:
            expected_target_case = (
                ReviewCaseType.PERSON_IDENTITY
                if self.source_table is ReviewTargetTable.PERSON_ROLES
                else ReviewCaseType.AUTHOR_IDENTITY
            )
        if self.stable_target.case_type is not expected_target_case:
            raise ValueError("candidate stable target case_type is incompatible")

        membership_set = frozenset(self.memberships)
        identity_matrix = {
            (ReviewCaseType.PERSON_IDENTITY, ReviewTargetTable.PERSON_ROLES),
            (
                ReviewCaseType.AUTHOR_IDENTITY,
                ReviewTargetTable.SCIENTIFIC_PRODUCTION_AUTHORS,
            ),
        }
        identity_memberships = {
            BackfillSourceMembership.CANONICAL_PENDING,
            BackfillSourceMembership.IDENTITY_LOCKED,
        }
        fixed_matrix = {
            (
                ReviewCaseType.PRODUCT,
                ReviewTargetTable.SCIENTIFIC_PRODUCTIONS,
            ): frozenset({BackfillSourceMembership.PRODUCT_PENDING}),
            (
                ReviewCaseType.PROJECT_DIRECTOR_RELATION,
                ReviewTargetTable.RESEARCH_ENTITIES,
            ): frozenset({BackfillSourceMembership.DIRECTOR_RELATION_PENDING}),
            (
                ReviewCaseType.EXTERNAL_IDENTITY,
                ReviewTargetTable.EXTERNAL_RESEARCHERS,
            ): frozenset({BackfillSourceMembership.EXTERNAL_PENDING}),
            (
                ReviewCaseType.POSSIBLE_DUPLICATE,
                ReviewTargetTable.PERSON_ROLES,
            ): frozenset({BackfillSourceMembership.POSSIBLE_MATCH}),
            (
                ReviewCaseType.POSSIBLE_DUPLICATE,
                ReviewTargetTable.SCIENTIFIC_PRODUCTION_AUTHORS,
            ): frozenset({BackfillSourceMembership.POSSIBLE_MATCH}),
        }
        matrix_key = (self.case_type, self.source_table)
        if matrix_key in identity_matrix:
            if not membership_set.issubset(identity_memberships):
                raise ValueError("identity candidate contains an incompatible population")
        elif fixed_matrix.get(matrix_key) != membership_set:
            raise ValueError("candidate is not included in the closed backfill matrix")

        locked = BackfillSourceMembership.IDENTITY_LOCKED in membership_set
        expected_statuses = (
            (ReviewCaseStatus.RESOLVED, ScientificStatus.VALIDATED)
            if locked
            else (ReviewCaseStatus.PENDING, ScientificStatus.PENDING)
        )
        if (self.case_status, self.scientific_status) != expected_statuses:
            raise ValueError("candidate statuses do not match its source memberships")
        if self.case_type is ReviewCaseType.POSSIBLE_DUPLICATE and self.possible_kpi_impact:
            raise ValueError("possible_duplicate cannot declare possible KPI impact")
        return self


class LockedDecisionImportV1(_ClosedAuditModel):
    candidate: BackfillCandidateV1
    canonical_identity_key: str
    canonical_name: str
    identity_type: CanonicalIdentityType
    legacy_actor_identifier: str
    legacy_decided_at: datetime
    alias_original: str | None

    @field_validator("canonical_identity_key", "canonical_name", "legacy_actor_identifier")
    @classmethod
    def reject_blank_locked_context(cls, value: str, info) -> str:
        return _require_nonblank(value, info.field_name)

    @field_validator("legacy_decided_at")
    @classmethod
    def require_aware_legacy_timestamp(cls, value: datetime) -> datetime:
        return _require_aware(value, "legacy_decided_at")

    @field_validator("alias_original", mode="before")
    @classmethod
    def normalize_blank_alias(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def require_locked_identity_candidate(self) -> LockedDecisionImportV1:
        if BackfillSourceMembership.IDENTITY_LOCKED not in self.candidate.memberships:
            raise ValueError("locked decision requires identity_locked membership")
        if self.candidate.case_type not in {
            ReviewCaseType.PERSON_IDENTITY,
            ReviewCaseType.AUTHOR_IDENTITY,
        }:
            raise ValueError("locked decision requires an identity case")
        if (
            self.candidate.case_status is not ReviewCaseStatus.RESOLVED
            or self.candidate.scientific_status is not ScientificStatus.VALIDATED
        ):
            raise ValueError("locked decision candidate must be resolved and validated")
        return self


class BackfillBlockerV1(_ClosedAuditModel):
    source_table: ReviewTargetTable
    source_id: int = Field(gt=0)
    code: Literal[
        "missing_stable_locator",
        "incomplete_locked_decision",
        "ambiguous_alias",
        "invariant_mismatch",
        "ambiguous_project_evidence",
        "contradictory_project_evidence",
    ]
    message: str

    @field_validator("message")
    @classmethod
    def reject_blank_or_secret_message(cls, value: str) -> str:
        _require_nonblank(value, "message")
        if _BACKFILL_SECRET_PATTERN.search(value):
            raise ValueError("backfill blocker message cannot contain secrets")
        return value


class BackfillDeferredRecordV1(_ClosedAuditModel):
    source_table: Literal[ReviewTargetTable.PERSON_ROLES]
    source_pk: int = Field(gt=0)
    source_population: Literal[BackfillSourceMembership.CANONICAL_PENDING]
    case_type: Literal[ReviewCaseType.PERSON_IDENTITY]
    period_id: int = Field(gt=0)
    document_key: str = Field(min_length=1, max_length=320)
    identity_reference: str = Field(min_length=1, max_length=320)
    reason_code: Literal["missing_row_locator"]
    disposition: Literal["expected_pending_review"]
    materializable: Literal[False]
    affects_kpi: Literal[False]
    evidence_summary: str = Field(min_length=1, max_length=500)

    @field_validator("document_key", "identity_reference", "evidence_summary")
    @classmethod
    def reject_blank_or_secret_deferred_context(cls, value: str, info) -> str:
        _require_nonblank(value, info.field_name)
        if _BACKFILL_SECRET_PATTERN.search(value):
            raise ValueError(f"{info.field_name} cannot contain secrets")
        return value

    @model_validator(mode="after")
    def technical_pk_is_not_a_functional_key(self) -> BackfillDeferredRecordV1:
        technical_pk = str(self.source_pk)
        if self.document_key.strip() == technical_pk:
            raise ValueError("source_pk cannot be used as document_key")
        if self.identity_reference.strip() == technical_pk:
            raise ValueError("source_pk cannot be used as identity_reference")
        return self


class BackfillExcludedRecordV1(_ClosedAuditModel):
    source_table: Literal[ReviewTargetTable.RESEARCH_ENTITIES]
    research_entity_id: int = Field(gt=0)
    source_population: Literal[BackfillSourceMembership.DIRECTOR_RELATION_PENDING]
    case_type: Literal[ReviewCaseType.PROJECT_DIRECTOR_RELATION]
    period_id: int = Field(gt=0)
    entity_type: Literal["grupo_investigacion", "semillero"]
    reason_code: Literal["entity_type_incompatible"]
    disposition: Literal["out_of_scope_record"]
    materializable: Literal[False]
    affects_kpi: Literal[False]
    evidence_summary: str = Field(min_length=1, max_length=500)

    @field_validator("evidence_summary")
    @classmethod
    def reject_blank_or_secret_exclusion_evidence(cls, value: str) -> str:
        _require_nonblank(value, "evidence_summary")
        if _BACKFILL_SECRET_PATTERN.search(value):
            raise ValueError("evidence_summary cannot contain secrets")
        return value


class B2B1BackfillPlanV1(_ClosedAuditModel):
    schema_version: Literal[1]
    captured_at: datetime
    invariant_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_counts: tuple[SourcePopulationCountV1, ...]
    overlaps: tuple[SourcePopulationOverlapV1, ...]
    union_stable_target_count: int = Field(ge=0)
    candidates: tuple[BackfillCandidateV1, ...]
    locked_decisions: tuple[LockedDecisionImportV1, ...]
    blockers: tuple[BackfillBlockerV1, ...]
    deferred_records: tuple[BackfillDeferredRecordV1, ...] = ()
    excluded_records: tuple[BackfillExcludedRecordV1, ...] = ()
    hard_blockers: tuple[BackfillBlockerV1, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def preserve_legacy_blockers_as_hard_blockers(cls, value: object) -> object:
        if isinstance(value, dict) and "hard_blockers" not in value:
            return {**value, "hard_blockers": value.get("blockers", ())}
        return value

    @field_validator("captured_at")
    @classmethod
    def require_aware_capture_timestamp(cls, value: datetime) -> datetime:
        return _require_aware(value, "captured_at")

    @field_validator("source_counts")
    @classmethod
    def sort_source_counts(
        cls,
        counts: tuple[SourcePopulationCountV1, ...],
    ) -> tuple[SourcePopulationCountV1, ...]:
        return tuple(sorted(counts, key=lambda item: _MEMBERSHIP_RANK[item.population]))

    @field_validator("overlaps")
    @classmethod
    def sort_overlaps(
        cls,
        overlaps: tuple[SourcePopulationOverlapV1, ...],
    ) -> tuple[SourcePopulationOverlapV1, ...]:
        return tuple(
            sorted(
                overlaps,
                key=lambda item: tuple(_MEMBERSHIP_RANK[value] for value in item.populations),
            )
        )

    @field_validator("candidates")
    @classmethod
    def sort_candidates(
        cls,
        candidates: tuple[BackfillCandidateV1, ...],
    ) -> tuple[BackfillCandidateV1, ...]:
        return tuple(
            sorted(
                candidates,
                key=lambda item: (
                    _CASE_TYPE_RANK[item.case_type],
                    _candidate_stable_target_key(item),
                ),
            )
        )

    @field_validator("locked_decisions")
    @classmethod
    def sort_locked_decisions(
        cls,
        decisions: tuple[LockedDecisionImportV1, ...],
    ) -> tuple[LockedDecisionImportV1, ...]:
        return tuple(
            sorted(
                decisions,
                key=lambda item: (
                    _CASE_TYPE_RANK[item.candidate.case_type],
                    _candidate_stable_target_key(item.candidate),
                ),
            )
        )

    @field_validator("blockers")
    @classmethod
    def sort_blockers(
        cls,
        blockers: tuple[BackfillBlockerV1, ...],
    ) -> tuple[BackfillBlockerV1, ...]:
        return tuple(
            sorted(
                blockers,
                key=lambda item: (
                    _TARGET_TABLE_RANK[item.source_table],
                    item.source_id,
                    item.code,
                ),
            )
        )

    @field_validator("deferred_records")
    @classmethod
    def sort_deferred_records(
        cls,
        records: tuple[BackfillDeferredRecordV1, ...],
    ) -> tuple[BackfillDeferredRecordV1, ...]:
        return tuple(sorted(records, key=lambda item: item.source_pk))

    @field_validator("excluded_records")
    @classmethod
    def sort_excluded_records(
        cls,
        records: tuple[BackfillExcludedRecordV1, ...],
    ) -> tuple[BackfillExcludedRecordV1, ...]:
        return tuple(sorted(records, key=lambda item: item.research_entity_id))

    @field_validator("hard_blockers")
    @classmethod
    def sort_hard_blockers(
        cls,
        blockers: tuple[BackfillBlockerV1, ...],
    ) -> tuple[BackfillBlockerV1, ...]:
        return cls.sort_blockers(blockers)

    @model_validator(mode="after")
    def validate_unique_collections_and_union(self) -> B2B1BackfillPlanV1:
        source_population_keys = tuple(item.population for item in self.source_counts)
        if len(source_population_keys) != len(set(source_population_keys)):
            raise ValueError("source_counts populations must be unique")

        overlap_keys = tuple(item.populations for item in self.overlaps)
        if len(overlap_keys) != len(set(overlap_keys)):
            raise ValueError("overlap population sets must be unique")

        candidate_keys = tuple(
            (item.case_type, _candidate_stable_target_key(item))
            for item in self.candidates
        )
        if len(candidate_keys) != len(set(candidate_keys)):
            raise ValueError("candidates must be unique by case_type and stable_target_key")
        stable_targets = {stable_target_key for _, stable_target_key in candidate_keys}
        if self.union_stable_target_count != len(stable_targets):
            raise ValueError("union_stable_target_count does not match the real target union")

        locked_keys = tuple(
            (item.candidate.case_type, _candidate_stable_target_key(item.candidate))
            for item in self.locked_decisions
        )
        if len(locked_keys) != len(set(locked_keys)):
            raise ValueError("locked_decisions must be unique by case_type and stable_target_key")
        candidate_by_key = {
            (item.case_type, _candidate_stable_target_key(item)): item
            for item in self.candidates
        }
        if any(candidate_by_key.get(key) != decision.candidate for key, decision in zip(locked_keys, self.locked_decisions)):
            raise ValueError("each locked decision must match its plan candidate")

        blocker_keys = tuple(
            (item.source_table, item.source_id, item.code) for item in self.blockers
        )
        if len(blocker_keys) != len(set(blocker_keys)):
            raise ValueError("blockers must be unique by source_table, source_id, and code")
        if self.hard_blockers != self.blockers:
            raise ValueError("hard_blockers must exactly mirror legacy blockers")

        deferred_keys = tuple(
            (item.source_table, item.source_pk, item.source_population)
            for item in self.deferred_records
        )
        if len(deferred_keys) != len(set(deferred_keys)):
            raise ValueError("deferred_records must be unique by source reference")

        excluded_keys = tuple(
            (item.source_table, item.research_entity_id, item.source_population)
            for item in self.excluded_records
        )
        if len(excluded_keys) != len(set(excluded_keys)):
            raise ValueError("excluded_records must be unique by source reference")

        candidate_source_keys = {
            (item.source_table, item.source_id) for item in self.candidates
        }
        blocker_source_keys = {
            (item.source_table, item.source_id) for item in self.hard_blockers
        }
        deferred_source_keys = {
            (item.source_table, item.source_pk) for item in self.deferred_records
        }
        excluded_source_keys = {
            (item.source_table, item.research_entity_id)
            for item in self.excluded_records
        }
        if deferred_source_keys.intersection(candidate_source_keys | blocker_source_keys):
            raise ValueError("deferred records cannot be candidates or hard blockers")
        if excluded_source_keys.intersection(candidate_source_keys | blocker_source_keys):
            raise ValueError("excluded records cannot be candidates or hard blockers")
        return self


class B2B1BackfillApplyResultV1(_ClosedAuditModel):
    schema_version: Literal[1]
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    before_invariants_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    after_invariants_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_review_items: int = Field(ge=0)
    created_decisions: int = Field(ge=0)
    created_overrides: int = Field(ge=0)
    created_identities: int = Field(ge=0)
    created_aliases: int = Field(ge=0)
    created_audit_events: int = Field(ge=0)
    eligible_products_before: int = Field(ge=0)
    eligible_products_after: int = Field(ge=0)


class AuditEventCommandV1(_ClosedAuditModel):
    id: UUID
    event_type: AuditEventType
    aggregate_type: str = Field(min_length=1, max_length=60)
    aggregate_key: str = Field(min_length=1, max_length=320)
    review_item_id: UUID | None
    actor_user_id: int | None
    actor_identifier: str = Field(min_length=1, max_length=180)
    actor_capability: B2BCapability | None
    occurred_at: datetime
    payload: AuditPayloadV1
    correlation_id: UUID
    request_id: UUID | None
    previous_event_id: UUID | None
    corrects_event_id: UUID | None

    @field_validator("aggregate_type", "aggregate_key", "actor_identifier")
    @classmethod
    def reject_blank_identifiers(cls, value: str, info) -> str:
        return _require_nonblank(value, info.field_name)

    @field_validator("occurred_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        return _require_aware(value, "occurred_at")

    @model_validator(mode="after")
    def require_matching_event_and_payload(self) -> AuditEventCommandV1:
        expected_kind = _PAYLOAD_KIND_BY_EVENT_TYPE[self.event_type]
        if self.payload.kind != expected_kind:
            raise ValueError(
                f"event_type {self.event_type.value!r} requires payload kind "
                f"{expected_kind!r}"
            )
        if isinstance(self.payload, CapabilityChangedAuditPayloadV1):
            expected_action = (
                "assigned"
                if self.event_type is AuditEventType.CAPABILITY_ASSIGNED
                else "revoked"
            )
            if self.payload.action != expected_action:
                raise ValueError(
                    f"event_type {self.event_type.value!r} requires action "
                    f"{expected_action!r}"
                )
        if isinstance(self.payload, ScientificDecisionAppliedAuditPayloadV1):
            if self.review_item_id is None:
                raise ValueError(
                    "scientific_decision_applied requires review_item_id"
                )
            if self.payload.review_item_id != self.review_item_id:
                raise ValueError(
                    "scientific decision payload and command review_item_id must match"
                )
        if self.event_type is AuditEventType.AUDIT_CORRECTED:
            if self.corrects_event_id is None:
                raise ValueError("audit_corrected requires corrects_event_id")
            if self.payload.corrects_event_id != self.corrects_event_id:
                raise ValueError("correction payload and command target must match")
        elif self.corrects_event_id is not None:
            raise ValueError("only audit_corrected can set corrects_event_id")
        return self


class AuditCorrectionCommandV1(_ClosedAuditModel):
    original_event_id: UUID
    actor_user_id: int
    actor_identifier: str = Field(min_length=1, max_length=180)
    actor_capability: B2BCapability
    reason: str = Field(min_length=1)
    replacement_payload_schema: str = Field(min_length=1, max_length=80)
    replacement_payload_version: Literal[1]
    replacement_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    occurred_at: datetime
    correlation_id: UUID
    request_id: UUID | None

    @field_validator("actor_identifier", "reason", "replacement_payload_schema")
    @classmethod
    def reject_blank_text(cls, value: str, info) -> str:
        return _require_nonblank(value, info.field_name)

    @field_validator("occurred_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        return _require_aware(value, "occurred_at")


class HumanIdentityProjectionV1(_ClosedAuditModel):
    stable_target_key: str = Field(
        max_length=128,
        pattern=r"^b2b:v1:[a-z_]+:[0-9a-f]{64}$",
    )
    canonical_identity_key: str = Field(min_length=1)
    canonical_name: str = Field(min_length=1)
    decision_id: UUID
    locked: Literal[True]
    aliases: tuple[str, ...]

    @field_validator("canonical_identity_key", "canonical_name")
    @classmethod
    def reject_blank_identity_values(cls, value: str, info) -> str:
        return _require_nonblank(value, info.field_name)

    @field_validator("aliases")
    @classmethod
    def sort_unique_aliases(cls, aliases: tuple[str, ...]) -> tuple[str, ...]:
        if any(not alias.strip() for alias in aliases):
            raise ValueError("aliases cannot contain blank values")
        if len(aliases) != len(set(aliases)):
            raise ValueError("aliases must be unique")
        return tuple(sorted(aliases))


class FunctionalReversalCommandV1(_ClosedAuditModel):
    review_item_id: UUID
    decision_id_to_revert: UUID
    actor_user_id: int = Field(gt=0)
    expected_case_version: int = Field(ge=1)
    reason: str = Field(min_length=1)
    correlation_id: UUID
    request_id: UUID | None

    @field_validator("reason")
    @classmethod
    def reject_blank_reason(cls, value: str) -> str:
        return _require_nonblank(value, "reason")


class OptimisticLockError(RuntimeError):
    pass


class BackfillGateError(RuntimeError):
    pass
