from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.entities import (
    AcademicPeriod,
    ExternalResearcher,
    PersonRole,
    ScientificProduction,
    ScientificProductionAuthor,
    User,
)
from app.models.human_review_core import ReviewDecision, ReviewItem
from app.models.human_review_enums import (
    AuditEventType,
    B2BAction,
    B2BCapability,
    CanonicalIdentityType,
    DecisionLifecycle,
    DecisionScope,
    OverrideField,
    OverrideScope,
    PersonAliasStatus,
    ReviewActorType,
    ReviewCaseStatus,
    ReviewCaseType,
    ReviewDecisionType,
    ReviewTargetTable,
    ScientificStatus,
)
from app.models.human_review_projection import (
    CanonicalIdentity,
    FieldOverride,
    PersonAlias,
)
from app.schemas.human_review import (
    DecisionReversalPayloadV1,
    DecisionPayloadV1 as PersistenceDecisionPayloadV1,
    FieldOverridePayloadV1,
    IdentityDecisionPayloadV1,
    IdentityMergePayloadV1,
    IdentityProjectionSnapshotV1,
    IdentitySeparationMemberV1,
    IdentitySeparationPayloadV1,
    MaintainSeparatePayloadV1,
    ProjectionAliasSnapshotV1,
    ProjectionOverrideSnapshotV1,
    ReviewProjectionSnapshotV1,
    ScalarOverrideValueV1,
)
from app.schemas.human_review_api import (
    ApplyDecisionRequest,
    ApplyDecisionResponse,
    DiscardRequest,
    ExternalDecisionPayload,
    KpiEffect,
    KpiEffectItem,
    PersonDecisionPayload,
    ProductDecisionPayload,
    RevertRequest,
    RelationDecisionPayload,
    derive_decision_type,
    validate_apply_decision,
)
from app.schemas.human_review_operations import (
    AuditEventCommandV1,
    FunctionalReversalCommandV1,
    OptimisticLockError,
    ScientificDecisionAppliedAuditPayloadV1,
    ScientificDecisionKpiEffectItemV1,
)
from app.services.human_review_audit import append_audit_event_at_current_head
from app.services.human_review_authorization import (
    B2BAccessDenied,
    authorize_b2b_action,
)
from app.services.human_review_scope import (
    ReviewScopeDenied,
    assert_review_item_scope,
    review_scope_predicate,
)
from app.services.human_review_projection import (
    _materialize_identity,
    _materialize_overrides,
    append_functional_reversal,
    set_current_decision,
)
from app.services.human_review_counterparts import resolve_duplicate_counterpart
from app.services.human_review_queries import HumanReviewQueryService
from app.services.human_review_state import (
    HumanReviewDomainError,
    IncompatibleDecisionError,
)
from app.services.human_review_targets import normalize_person_alias
from app.services.kpi_service import KpiService


_DECISION_PAYLOAD_ADAPTER = TypeAdapter(PersistenceDecisionPayloadV1)
_DECISION_PAYLOAD_SCHEMA = "review.decision.v1"
_AUDIT_AGGREGATE_TYPE = "review_item"


class ReviewCaseNotFoundError(HumanReviewDomainError):
    code = "REVIEW_CASE_NOT_FOUND"

    def __init__(self, correlation_id: UUID) -> None:
        super().__init__("Review case was not found", correlation_id=correlation_id)


class ReviewCaseVersionConflictError(HumanReviewDomainError):
    code = "REVIEW_CASE_VERSION_CONFLICT"

    def __init__(self, correlation_id: UUID) -> None:
        super().__init__("Review case version conflict", correlation_id=correlation_id)


class B2BCapabilityRequiredError(HumanReviewDomainError):
    code = "B2B_CAPABILITY_REQUIRED"

    def __init__(self, correlation_id: UUID) -> None:
        super().__init__("B2B capability is required", correlation_id=correlation_id)


class HumanReviewCommandInternalError(HumanReviewDomainError):
    code = "HUMAN_REVIEW_INTERNAL_ERROR"

    def __init__(self, correlation_id: UUID) -> None:
        super().__init__("Request could not be completed", correlation_id=correlation_id)


class InvalidCommandPayloadError(HumanReviewDomainError):
    code = "INVALID_COMMAND_PAYLOAD"

    def __init__(self, correlation_id: UUID) -> None:
        super().__init__("Command payload is invalid", correlation_id=correlation_id)


@dataclass(frozen=True)
class _DecisionPlan:
    decision_type: ReviewDecisionType
    payload: PersistenceDecisionPayloadV1
    projection_after: ReviewProjectionSnapshotV1


def _capture_effective_kpi(
    db: Session,
    item: ReviewItem,
) -> dict[str, int] | None:
    if item.possible_kpi_impact is not True or item.period_id is None:
        return None
    with db.no_autoflush:
        period = db.get(AcademicPeriod, item.period_id)
        if period is None:
            return None
        dashboard = KpiService(db).dashboard(
            period.year_label,
            period.cycle,
            career_id=None,
        )
    return {
        metric: value
        for metric, value in dashboard.model_dump(mode="python").items()
        if type(value) is int
    }


def _diff_effective_kpi(
    before: dict[str, int] | None,
    after: dict[str, int] | None,
) -> KpiEffect:
    if before is None or after is None:
        return KpiEffect(affected=())
    return KpiEffect(affected=tuple(
        KpiEffectItem(
            metric=metric,
            before=before[metric],
            after=after[metric],
            delta=after[metric] - before[metric],
        )
        for metric in sorted(before.keys() & after.keys())
        if before[metric] != after[metric]
    ))


def _string_value(value: str) -> ScalarOverrideValueV1:
    return ScalarOverrideValueV1(kind="string", string_value=value)


def _scope_context(
    item: ReviewItem,
    scope: DecisionScope,
) -> dict[str, object | None]:
    if scope is DecisionScope.DOCUMENT:
        if not item.document_key.strip():
            raise IncompatibleDecisionError("Document scope requires a document")
        return {"document_key": item.document_key}
    if scope is DecisionScope.PERIOD:
        if item.period_id is None:
            raise IncompatibleDecisionError("Period scope requires a period")
        return {"period_id": item.period_id}
    if scope is DecisionScope.RELATIONSHIP:
        if item.relationship_key is None or not item.relationship_key.strip():
            raise IncompatibleDecisionError(
                "Relationship scope requires a relationship"
            )
        return {"relationship_key": item.relationship_key}
    return {}


def _new_override(
    item: ReviewItem,
    field_path: OverrideField,
    value: str,
    scope: DecisionScope,
) -> ProjectionOverrideSnapshotV1:
    override_scope = OverrideScope(scope.value)
    if (
        override_scope is OverrideScope.GLOBAL_IDENTITY
        and field_path
        not in {OverrideField.CANONICAL_IDENTITY_KEY, OverrideField.CANONICAL_NAME}
    ):
        raise IncompatibleDecisionError(
            "The requested scope is incompatible with the projected field"
        )
    return ProjectionOverrideSnapshotV1(
        schema_version=1,
        field_path=field_path,
        projected_value=_string_value(value),
        scope=override_scope,
        stable_target_key=item.stable_target_key,
        target_table=ReviewTargetTable(item.target_table),
        target_pk=item.target_pk,
        locked=True,
        **_scope_context(item, scope),
    )


def _replace_overrides(
    current: tuple[ProjectionOverrideSnapshotV1, ...],
    replacements: tuple[ProjectionOverrideSnapshotV1, ...],
) -> tuple[ProjectionOverrideSnapshotV1, ...]:
    replacement_keys = {
        (
            row.stable_target_key,
            row.field_path,
            row.scope,
            row.document_key,
            row.period_id,
            row.relationship_key,
        )
        for row in replacements
    }
    preserved = tuple(
        row
        for row in current
        if (
            row.stable_target_key,
            row.field_path,
            row.scope,
            row.document_key,
            row.period_id,
            row.relationship_key,
        )
        not in replacement_keys
    )
    return preserved + replacements


def _resolved_snapshot(
    before: ReviewProjectionSnapshotV1,
    decision_id: UUID,
    scientific_status: ScientificStatus,
    *,
    identity: IdentityProjectionSnapshotV1 | None,
    replacements: tuple[ProjectionOverrideSnapshotV1, ...],
) -> ReviewProjectionSnapshotV1:
    return ReviewProjectionSnapshotV1(
        schema_version=1,
        case_status=ReviewCaseStatus.RESOLVED,
        scientific_status=scientific_status,
        current_decision_id=decision_id,
        identity=identity,
        overrides=_replace_overrides(before.overrides, replacements),
    )


def _identity_snapshot(
    key: str,
    name: str,
    identity_type: CanonicalIdentityType,
    aliases: tuple[str, ...],
) -> IdentityProjectionSnapshotV1:
    snapshots = tuple(
        ProjectionAliasSnapshotV1(
            schema_version=1,
            alias_original=value,
            alias_normalized=normalize_person_alias(value),
        )
        for value in aliases
    )
    return IdentityProjectionSnapshotV1(
        schema_version=1,
        canonical_identity_key=key,
        canonical_name=name,
        identity_type=identity_type,
        aliases=snapshots,
    )


def _validate_persistence_text(
    request: ApplyDecisionRequest,
) -> None:
    payload = request.payload
    if not isinstance(payload, PersonDecisionPayload):
        return
    if len(payload.canonical_name) > 220:
        raise InvalidCommandPayloadError(request.correlation_id)
    normalized_aliases: list[str] = []
    for alias in payload.aliases:
        if len(alias) > 320:
            raise InvalidCommandPayloadError(request.correlation_id)
        try:
            normalized = normalize_person_alias(alias)
        except ValueError as error:
            raise InvalidCommandPayloadError(request.correlation_id) from error
        if len(normalized) > 320:
            raise InvalidCommandPayloadError(request.correlation_id)
        normalized_aliases.append(normalized)
    if len(set(normalized_aliases)) != len(normalized_aliases):
        raise InvalidCommandPayloadError(request.correlation_id)


def _validate_existing_identity_type(
    db: Session,
    request: ApplyDecisionRequest,
) -> None:
    payload = request.payload
    if isinstance(payload, PersonDecisionPayload):
        identity_key = payload.canonical_identity_key
        expected_type = CanonicalIdentityType.INTERNAL_PERSON
    elif isinstance(payload, ExternalDecisionPayload):
        identity_key = payload.external_identity_key
        expected_type = CanonicalIdentityType.EXTERNAL_PERSON
    else:
        return
    if identity_key is None:
        return
    existing = db.scalar(select(CanonicalIdentity).where(
        CanonicalIdentity.canonical_identity_key == identity_key
    ))
    existing_type = getattr(existing, "identity_type", None)
    if (
        isinstance(existing_type, str)
        and existing_type != expected_type.value
    ):
        raise IncompatibleDecisionError(
            "Existing canonical identity type is incompatible with the review case",
            correlation_id=request.correlation_id,
        )


def _materialize_generated_identity_key(
    request: ApplyDecisionRequest,
) -> ApplyDecisionRequest:
    """Generate an opaque public reference when a decision creates an identity."""

    payload = request.payload
    if isinstance(payload, PersonDecisionPayload) and not payload.canonical_identity_key:
        return request.model_copy(update={
            "payload": payload.model_copy(update={
                "canonical_identity_key": f"human:identity:{uuid4()}",
            }),
        })
    if isinstance(payload, ExternalDecisionPayload) and not payload.external_identity_key:
        return request.model_copy(update={
            "payload": payload.model_copy(update={
                "external_identity_key": f"human:external:{uuid4()}",
            }),
        })
    return request


def _comparison_values(
    db: Session,
    item: ReviewItem,
    request: ApplyDecisionRequest,
) -> tuple[object, object] | None:
    if item.target_pk is None:
        return None
    payload = request.payload
    if isinstance(payload, ProductDecisionPayload):
        target = db.get(ScientificProduction, item.target_pk)
        if target is None:
            return None
        normalized = (
            getattr(target, "title", None)
            or getattr(target, "normalized_title", None)
        )
        final = (
            payload.product_title
            if payload.product_title is not None
            else getattr(target, "title", None)
        )
    elif isinstance(payload, PersonDecisionPayload):
        target_model = (
            PersonRole
            if payload.case_type == ReviewCaseType.PERSON_IDENTITY.value
            else ScientificProductionAuthor
        )
        target = db.get(target_model, item.target_pk)
        if target is None:
            return None
        normalized = (
            getattr(target, "normalized_name", None)
            or getattr(target, "normalized_author_name", None)
            or getattr(target, "canonical_name", None)
        )
        final = payload.canonical_name
    else:
        return None
    if normalized is None or final is None:
        return None
    return normalized, final


def _derive_apply_matrix(
    item: ReviewItem,
    request: ApplyDecisionRequest,
) -> ReviewDecisionType:
    try:
        return derive_decision_type(
            ReviewCaseType(item.case_type),
            request.action,
            request.payload,
        )
    except (TypeError, ValueError, IncompatibleDecisionError) as error:
        raise IncompatibleDecisionError(
            "Action is incompatible with the review case type",
            correlation_id=request.correlation_id,
        ) from error


def _validate_apply_payload(
    db: Session,
    item: ReviewItem,
    request: ApplyDecisionRequest,
) -> ReviewDecisionType:
    if request.payload.scientific_status is not ScientificStatus.VALIDATED:
        raise IncompatibleDecisionError(
            "Scientific apply decisions must resolve as validated",
            correlation_id=request.correlation_id,
        )
    _validate_existing_identity_type(db, request)
    _validate_persistence_text(request)
    comparison = (
        _comparison_values(db, item, request)
        if request.action == "approve"
        and (request.reason is None or not request.reason.strip())
        else None
    )
    if comparison is None:
        return validate_apply_decision(
            request,
            case_type=ReviewCaseType(item.case_type),
        )
    return validate_apply_decision(
        request,
        case_type=ReviewCaseType(item.case_type),
        normalized_value=comparison[0],
        final_value=comparison[1],
    )


def _field_replacements(
    item: ReviewItem,
    request: ApplyDecisionRequest,
) -> tuple[ProjectionOverrideSnapshotV1, ...]:
    payload = request.payload
    fields: list[tuple[OverrideField, str]] = []
    if isinstance(payload, PersonDecisionPayload):
        identity_field = (
            OverrideField.CANONICAL_IDENTITY_KEY
            if payload.case_type == ReviewCaseType.PERSON_IDENTITY.value
            else OverrideField.AUTHOR_IDENTITY_KEY
        )
        fields.extend((
            (identity_field, payload.canonical_identity_key),
            (OverrideField.CANONICAL_NAME, payload.canonical_name),
        ))
    elif isinstance(payload, ProductDecisionPayload):
        if payload.product_title is not None:
            fields.append((OverrideField.PRODUCT_TITLE, payload.product_title))
    elif isinstance(payload, RelationDecisionPayload):
        if payload.project_director_identity_key is not None:
            fields.append((
                OverrideField.PROJECT_DIRECTOR_IDENTITY_KEY,
                payload.project_director_identity_key,
            ))
        fields.append((
            OverrideField.PROJECT_DIRECTOR_RELATIONSHIP_STATUS,
            payload.relationship_status,
        ))
    elif isinstance(payload, ExternalDecisionPayload):
        fields.append((OverrideField.EXTERNAL_IDENTITY_KEY, payload.external_identity_key))
        if payload.external_institution is not None:
            fields.append((
                OverrideField.EXTERNAL_INSTITUTION,
                payload.external_institution,
            ))
    fields.append((OverrideField.SCIENTIFIC_STATUS, ScientificStatus.VALIDATED.value))
    replacements: list[ProjectionOverrideSnapshotV1] = []
    for field_path, value in fields:
        field_scope = request.scope
        if (
            field_path is OverrideField.SCIENTIFIC_STATUS
            and field_scope is DecisionScope.GLOBAL_IDENTITY
        ):
            field_scope = DecisionScope.RECORD
        replacements.append(_new_override(item, field_path, value, field_scope))
    return tuple(replacements)


def _external_identity(
    db: Session,
    item: ReviewItem,
    payload: ExternalDecisionPayload,
) -> IdentityProjectionSnapshotV1:
    existing = db.scalar(select(CanonicalIdentity).where(
        CanonicalIdentity.canonical_identity_key == payload.external_identity_key
    ))
    name = existing.display_name if existing is not None else None
    if name is None and item.target_pk is not None:
        target = db.get(ExternalResearcher, item.target_pk)
        name = (
            getattr(target, "normalized_name", None)
            or getattr(target, "full_name", None)
            if target is not None
            else None
        )
    if not isinstance(name, str) or not name.strip():
        raise IncompatibleDecisionError(
            "External identity requires a complete persisted identity projection"
        )
    return _identity_snapshot(
        payload.external_identity_key,
        name,
        CanonicalIdentityType.EXTERNAL_PERSON,
        (),
    )


def _projection_from_decision(
    db: Session,
    item: ReviewItem,
    correlation_id: UUID,
) -> ReviewProjectionSnapshotV1:
    if item.current_decision_id is None:
        raise IncompatibleDecisionError(
            "A complete locked identity projection is required"
        )
    decision = db.get(ReviewDecision, item.current_decision_id)
    if (
        decision is None
        or decision.review_item_id != item.id
        or decision.decision_lifecycle != DecisionLifecycle.APPROVED.value
        or decision.locks_projection is not True
        or decision.payload_schema != _DECISION_PAYLOAD_SCHEMA
        or decision.payload_version != 1
    ):
        raise HumanReviewCommandInternalError(correlation_id)
    try:
        payload = _DECISION_PAYLOAD_ADAPTER.validate_python(decision.payload)
        projection = payload.projection_after
    except ValidationError as error:
        raise HumanReviewCommandInternalError(correlation_id) from error

    expected_projection_decision_id = (
        payload.restore_decision_id
        if isinstance(payload, DecisionReversalPayloadV1)
        else decision.id
    )
    if (
        projection is None
        or projection.current_decision_id != expected_projection_decision_id
        or projection.case_status.value != item.case_status
        or projection.scientific_status.value != item.scientific_status
    ):
        raise HumanReviewCommandInternalError(correlation_id)
    return projection


def _separated_current_identity(
    current: IdentityProjectionSnapshotV1,
    counterpart: IdentityProjectionSnapshotV1,
    stable_target_key: str,
) -> IdentityProjectionSnapshotV1:
    if current.canonical_identity_key != counterpart.canonical_identity_key:
        return current
    return IdentityProjectionSnapshotV1(
        schema_version=1,
        canonical_identity_key=(
            "human:separated:"
            + sha256(stable_target_key.encode("utf-8")).hexdigest()
        ),
        canonical_name=current.canonical_name,
        identity_type=current.identity_type,
        aliases=(),
    )


def _identity_materialization_before(
    before: ReviewProjectionSnapshotV1,
    after: ReviewProjectionSnapshotV1,
    decision_type: ReviewDecisionType,
) -> ReviewProjectionSnapshotV1:
    if (
        decision_type is ReviewDecisionType.SEPARATED
        and before.identity is not None
        and after.identity is not None
        and before.identity.canonical_identity_key
        != after.identity.canonical_identity_key
    ):
        return before.model_copy(update={"identity": None})
    return before


def _duplicate_plan(
    db: Session,
    item: ReviewItem,
    request: ApplyDecisionRequest,
    decision_id: UUID,
    before: ReviewProjectionSnapshotV1,
    decision_type: ReviewDecisionType,
) -> _DecisionPlan:
    payload = request.payload
    counterpart = resolve_duplicate_counterpart(
        db,
        item,
        payload.counterpart_ref,
        request.correlation_id,
    )
    counterpart_key = counterpart.stable_target_key
    current_identity = before.identity
    counterpart_projection = _projection_before(
        db, counterpart, request.correlation_id
    )
    counterpart_identity = counterpart_projection.identity
    if current_identity is None or counterpart_identity is None:
        raise IncompatibleDecisionError(
            "Duplicate resolution requires two complete identity projections",
            correlation_id=request.correlation_id,
        )
    stable_keys = (item.stable_target_key, counterpart_key)
    identities = (
        current_identity.canonical_identity_key,
        counterpart_identity.canonical_identity_key,
    )
    if decision_type is ReviewDecisionType.MERGED:
        result_identity = counterpart_identity
    elif decision_type is ReviewDecisionType.MAINTAINED_SEPARATE:
        if len(set(identities)) != 2:
            raise IncompatibleDecisionError(
                "Maintaining separation requires distinct persisted identities",
                correlation_id=request.correlation_id,
            )
        result_identity = current_identity
    else:
        result_identity = _separated_current_identity(
            current_identity,
            counterpart_identity,
            item.stable_target_key,
        )
    after = _resolved_snapshot(
        before,
        decision_id,
        ScientificStatus.VALIDATED,
        identity=result_identity,
        replacements=(
            _new_override(
                item,
                OverrideField.SCIENTIFIC_STATUS,
                ScientificStatus.VALIDATED.value,
                DecisionScope.RECORD,
            ),
        ),
    )
    if decision_type is ReviewDecisionType.MERGED:
        persistence_payload = IdentityMergePayloadV1(
            kind="identity_merge",
            schema_version=1,
            target_identity_key=counterpart_identity.canonical_identity_key,
            member_stable_target_keys=stable_keys,
            projection_before=before,
            projection_after=after,
        )
    elif decision_type is ReviewDecisionType.MAINTAINED_SEPARATE:
        persistence_payload = MaintainSeparatePayloadV1(
            kind="maintain_separate",
            schema_version=1,
            stable_target_keys=stable_keys,
            identity_keys=identities,
            projection_before=before,
            projection_after=after,
        )
    else:
        persistence_payload = IdentitySeparationPayloadV1(
            kind="identity_separation",
            schema_version=1,
            source_identity_key=current_identity.canonical_identity_key,
            assignments=(
                IdentitySeparationMemberV1(
                    stable_target_key=item.stable_target_key,
                    target_identity_key=result_identity.canonical_identity_key,
                    target_canonical_name=result_identity.canonical_name,
                ),
                IdentitySeparationMemberV1(
                    stable_target_key=counterpart_key,
                    target_identity_key=counterpart_identity.canonical_identity_key,
                    target_canonical_name=counterpart_identity.canonical_name,
                ),
            ),
            projection_before=before,
            projection_after=after,
        )
    return _DecisionPlan(decision_type, persistence_payload, after)


def _assemble_apply_plan(
    db: Session,
    item: ReviewItem,
    request: ApplyDecisionRequest,
    decision_id: UUID,
    before: ReviewProjectionSnapshotV1,
    decision_type: ReviewDecisionType,
) -> _DecisionPlan:
    case_type = ReviewCaseType(item.case_type)
    if case_type is ReviewCaseType.POSSIBLE_DUPLICATE:
        return _duplicate_plan(
            db, item, request, decision_id, before, decision_type
        )
    replacements = _field_replacements(item, request)
    identity = before.identity
    payload = request.payload
    if isinstance(payload, PersonDecisionPayload):
        identity = _identity_snapshot(
            payload.canonical_identity_key,
            payload.canonical_name,
            CanonicalIdentityType.INTERNAL_PERSON,
            payload.aliases,
        )
    elif isinstance(payload, ExternalDecisionPayload):
        identity = _external_identity(db, item, payload)
    after = _resolved_snapshot(
        before,
        decision_id,
        ScientificStatus.VALIDATED,
        identity=identity,
        replacements=replacements,
    )
    if isinstance(payload, (PersonDecisionPayload, ExternalDecisionPayload)):
        persistence_payload = IdentityDecisionPayloadV1(
            kind="identity",
            schema_version=1,
            canonical_identity_key=identity.canonical_identity_key,
            canonical_name=identity.canonical_name,
            identity_type=identity.identity_type,
            alias_original=(
                identity.aliases[0].alias_original if identity.aliases else None
            ),
            alias_normalized=(
                identity.aliases[0].alias_normalized if identity.aliases else None
            ),
            projection_before=before,
            projection_after=after,
        )
    else:
        if isinstance(payload, ProductDecisionPayload) and payload.product_title is not None:
            primary_field = OverrideField.PRODUCT_TITLE
            primary_value = payload.product_title
        elif isinstance(payload, RelationDecisionPayload):
            primary_field = OverrideField.PROJECT_DIRECTOR_RELATIONSHIP_STATUS
            primary_value = payload.relationship_status
        else:
            primary_field = OverrideField.SCIENTIFIC_STATUS
            primary_value = ScientificStatus.VALIDATED.value
        primary_override = next(
            row for row in replacements if row.field_path is primary_field
        )
        persistence_payload = FieldOverridePayloadV1(
            kind="field_override",
            schema_version=1,
            field_path=primary_field,
            value=_string_value(primary_value),
            scope=primary_override.scope,
            stable_target_key=(
                item.stable_target_key
                if primary_override.scope is OverrideScope.RECORD
                else None
            ),
            document_key=primary_override.document_key,
            period_id=primary_override.period_id,
            relationship_key=primary_override.relationship_key,
            projection_before=before,
            projection_after=after,
        )
    return _DecisionPlan(decision_type, persistence_payload, after)


def _build_apply_plan(
    db: Session,
    item: ReviewItem,
    request: ApplyDecisionRequest,
    decision_id: UUID,
    before: ReviewProjectionSnapshotV1,
) -> _DecisionPlan:
    _derive_apply_matrix(item, request)
    decision_type = _validate_apply_payload(db, item, request)
    return _assemble_apply_plan(
        db, item, request, decision_id, before, decision_type
    )


def _build_discard_plan(
    item: ReviewItem,
    request: DiscardRequest,
    decision_id: UUID,
    before: ReviewProjectionSnapshotV1,
) -> _DecisionPlan:
    replacement = _new_override(
        item,
        OverrideField.SCIENTIFIC_STATUS,
        ScientificStatus.DISCARDED.value,
        DecisionScope.RECORD,
    )
    after = _resolved_snapshot(
        before,
        decision_id,
        ScientificStatus.DISCARDED,
        identity=before.identity,
        replacements=(replacement,),
    )
    payload = FieldOverridePayloadV1(
        kind="field_override",
        schema_version=1,
        field_path=OverrideField.SCIENTIFIC_STATUS,
        value=_string_value(ScientificStatus.DISCARDED.value),
        scope=OverrideScope.RECORD,
        stable_target_key=item.stable_target_key,
        projection_before=before,
        projection_after=after,
    )
    return _DecisionPlan(ReviewDecisionType.DISCARDED, payload, after)


def _snapshot_override(row: FieldOverride) -> ProjectionOverrideSnapshotV1:
    if (
        row.value_schema != "override.scalar.v1"
        or row.value_version != 1
        or row.locked is not True
    ):
        raise ValueError("active projection override is not locked and typed")
    return ProjectionOverrideSnapshotV1(
        schema_version=1,
        field_path=row.field_path,
        projected_value=ScalarOverrideValueV1.model_validate(row.projected_value),
        scope=row.scope,
        stable_target_key=row.stable_target_key,
        target_table=row.target_table,
        target_pk=row.target_pk,
        document_key=row.document_key,
        period_id=row.period_id,
        relationship_key=row.relationship_key,
        locked=True,
    )


def _identity_from_overrides(
    db: Session,
    overrides: tuple[ProjectionOverrideSnapshotV1, ...],
) -> IdentityProjectionSnapshotV1 | None:
    values = {
        row.field_path: row.projected_value.string_value
        for row in overrides
        if row.projected_value.kind == "string"
    }
    key = (
        values.get(OverrideField.CANONICAL_IDENTITY_KEY)
        or values.get(OverrideField.AUTHOR_IDENTITY_KEY)
        or values.get(OverrideField.EXTERNAL_IDENTITY_KEY)
    )
    if key is None:
        return None
    identity = db.scalar(select(CanonicalIdentity).where(
        CanonicalIdentity.canonical_identity_key == key
    ))
    if identity is None:
        return None
    aliases = tuple(db.scalars(select(PersonAlias).where(
        PersonAlias.canonical_identity_id == identity.id,
        PersonAlias.status == PersonAliasStatus.ACTIVE.value,
    )))
    return IdentityProjectionSnapshotV1(
        schema_version=1,
        canonical_identity_key=identity.canonical_identity_key,
        canonical_name=identity.display_name,
        identity_type=identity.identity_type,
        aliases=tuple(
            ProjectionAliasSnapshotV1(
                schema_version=1,
                alias_original=row.alias_original,
                alias_normalized=row.alias_normalized,
            )
            for row in aliases
        ),
    )


def _projection_before(
    db: Session,
    item: ReviewItem,
    correlation_id: UUID,
) -> ReviewProjectionSnapshotV1:
    if item.current_decision_id is not None:
        try:
            projection = _projection_from_decision(db, item, correlation_id)
        except HumanReviewCommandInternalError as error:
            raise HumanReviewCommandInternalError(correlation_id) from error
        return projection
    rows = tuple(db.scalars(select(FieldOverride).where(
        FieldOverride.stable_target_key == item.stable_target_key,
        FieldOverride.is_active.is_(True),
        FieldOverride.locked.is_(True),
    )))
    try:
        overrides = tuple(_snapshot_override(row) for row in rows)
        identity = _identity_from_overrides(db, overrides)
        return ReviewProjectionSnapshotV1(
            schema_version=1,
            case_status=ReviewCaseStatus(item.case_status),
            scientific_status=ScientificStatus(item.scientific_status),
            current_decision_id=None,
            identity=identity,
            overrides=overrides,
        )
    except (TypeError, ValueError, ValidationError) as error:
        raise HumanReviewCommandInternalError(correlation_id) from error


def _load_actor_and_item(
    db: Session,
    actor: User,
    review_item_id: UUID,
    correlation_id: UUID,
) -> tuple[User, ReviewItem]:
    try:
        capability = authorize_b2b_action(
            db, actor, B2BAction.APPLY_SCIENTIFIC
        )
    except B2BAccessDenied as error:
        raise B2BCapabilityRequiredError(correlation_id) from error
    if capability is not B2BCapability.RESEARCH_MANAGER:
        raise B2BCapabilityRequiredError(correlation_id)
    persisted_actor = db.get(User, actor.id)
    if persisted_actor is None or not persisted_actor.is_active:
        raise B2BCapabilityRequiredError(correlation_id)
    with db.no_autoflush:
        item = db.scalars(
            select(ReviewItem)
            .where(
                ReviewItem.id == review_item_id,
                review_scope_predicate(persisted_actor),
            )
            .with_for_update()
        ).first()
    if item is None:
        if db.get(ReviewItem, review_item_id) is not None:
            raise B2BCapabilityRequiredError(correlation_id)
        raise ReviewCaseNotFoundError(correlation_id)
    try:
        assert_review_item_scope(
            persisted_actor,
            item,
            correlation_id=correlation_id,
        )
    except ReviewScopeDenied as error:
        raise B2BCapabilityRequiredError(correlation_id) from error
    return persisted_actor, item


def _validate_expected_state(
    item: ReviewItem,
    expected_version: int,
    expected_current_decision_id: UUID | None,
    correlation_id: UUID,
) -> None:
    if (
        item.version != expected_version
        or item.current_decision_id != expected_current_decision_id
    ):
        raise ReviewCaseVersionConflictError(correlation_id)
    if (
        item.case_status not in {
            ReviewCaseStatus.PENDING.value,
            ReviewCaseStatus.REOPENED.value,
        }
        or item.scientific_status != ScientificStatus.PENDING.value
    ):
        raise IncompatibleDecisionError(
            "Case transition is incompatible with the current state",
            correlation_id=correlation_id,
            details={
                "current_status": item.case_status,
                "target_status": ReviewCaseStatus.RESOLVED.value,
            },
        )


def _derive_discard_matrix(
    item: ReviewItem,
    correlation_id: UUID,
) -> None:
    try:
        case_type = ReviewCaseType(item.case_type)
    except ValueError as error:
        raise IncompatibleDecisionError(
            "Case type does not support scientific decisions",
            correlation_id=correlation_id,
        ) from error
    if case_type not in {
        ReviewCaseType.PERSON_IDENTITY,
        ReviewCaseType.AUTHOR_IDENTITY,
        ReviewCaseType.PRODUCT,
        ReviewCaseType.PROJECT_DIRECTOR_RELATION,
        ReviewCaseType.EXTERNAL_IDENTITY,
        ReviewCaseType.POSSIBLE_DUPLICATE,
    }:
        raise IncompatibleDecisionError(
            "Case type does not support discard",
            correlation_id=correlation_id,
        )


def _persist_plan(
    db: Session,
    actor: User,
    item: ReviewItem,
    request: ApplyDecisionRequest | DiscardRequest,
    plan: _DecisionPlan,
    decision_id: UUID,
) -> KpiEffect:
    kpi_before = _capture_effective_kpi(db, item)
    next_sequence = db.scalar(select(
        func.coalesce(func.max(ReviewDecision.sequence), 0) + 1
    ).where(ReviewDecision.review_item_id == item.id))
    decided_at = datetime.now(timezone.utc)
    decision = ReviewDecision(
        id=decision_id,
        review_item_id=item.id,
        sequence=int(next_sequence or 1),
        decision_type=plan.decision_type.value,
        decision_lifecycle=DecisionLifecycle.APPROVED.value,
        scope=(
            request.scope.value
            if isinstance(request, ApplyDecisionRequest)
            else DecisionScope.RECORD.value
        ),
        payload_schema=_DECISION_PAYLOAD_SCHEMA,
        payload_version=1,
        payload=plan.payload.model_dump(mode="json"),
        reason=request.reason,
        actor_type=ReviewActorType.HUMAN.value,
        actor_user_id=actor.id,
        actor_identifier=actor.email,
        actor_capability=B2BCapability.RESEARCH_MANAGER.value,
        decided_at=decided_at,
        expected_case_version=request.expected_version,
        previous_decision_id=item.current_decision_id,
        corrects_decision_id=None,
        locks_projection=True,
    )
    db.add(decision)
    updated = set_current_decision(
        db,
        item.id,
        decision.id,
        request.expected_version,
        ReviewCaseStatus.RESOLVED,
        plan.projection_after.scientific_status,
    )
    _materialize_identity(
        db,
        decision,
        _identity_materialization_before(
            plan.payload.projection_before,
            plan.projection_after,
            plan.decision_type,
        ),
        plan.projection_after,
    )
    _materialize_overrides(db, updated, decision, plan.projection_after)
    kpi_effect = _diff_effective_kpi(
        kpi_before,
        _capture_effective_kpi(db, updated),
    )
    event = append_audit_event_at_current_head(
        db,
        AuditEventCommandV1(
            id=uuid4(),
            event_type=AuditEventType.SCIENTIFIC_DECISION_APPLIED,
            aggregate_type=_AUDIT_AGGREGATE_TYPE,
            aggregate_key=item.stable_target_key,
            review_item_id=item.id,
            actor_user_id=actor.id,
            actor_identifier=actor.email,
            actor_capability=B2BCapability.RESEARCH_MANAGER,
            occurred_at=datetime.now(timezone.utc),
            payload=ScientificDecisionAppliedAuditPayloadV1(
                kind="scientific_decision_applied",
                schema_version=1,
                decision_id=decision.id,
                decision_type=plan.decision_type,
                previous_case_status=plan.payload.projection_before.case_status,
                resulting_case_status=ReviewCaseStatus.RESOLVED,
                kpi_effect=tuple(
                    ScientificDecisionKpiEffectItemV1.model_validate(
                        row.model_dump(mode="python")
                    )
                    for row in kpi_effect.affected
                ),
                review_item_id=item.id,
            ),
            correlation_id=request.correlation_id,
            request_id=None,
            previous_event_id=None,
            corrects_event_id=None,
        ),
    )
    db.flush()
    if (
        updated.version != request.expected_version + 1
        or updated.current_decision_id != decision.id
        or decision.decision_lifecycle != DecisionLifecycle.APPROVED.value
        or decision.locks_projection is not True
        or event.review_item_id != item.id
    ):
        raise RuntimeError("atomic command postconditions failed")
    return kpi_effect


def _prepare_command(
    db: Session,
    actor: User,
    review_item_id: UUID,
    request: ApplyDecisionRequest | DiscardRequest,
) -> ApplyDecisionResponse:
    actor, item = _load_actor_and_item(
        db,
        actor,
        review_item_id,
        request.correlation_id,
    )
    if isinstance(request, ApplyDecisionRequest):
        decision_type = _derive_apply_matrix(item, request)
        _validate_expected_state(
            item,
            request.expected_version,
            request.expected_current_decision_id,
            request.correlation_id,
        )
        validated_type = _validate_apply_payload(db, item, request)
        if validated_type is not decision_type:
            raise HumanReviewCommandInternalError(request.correlation_id)
    else:
        _derive_discard_matrix(item, request.correlation_id)
        _validate_expected_state(
            item,
            request.expected_version,
            request.expected_current_decision_id,
            request.correlation_id,
        )
        decision_type = ReviewDecisionType.DISCARDED
    before = _projection_before(db, item, request.correlation_id)
    decision_id = uuid4()
    if isinstance(request, ApplyDecisionRequest):
        plan = _assemble_apply_plan(
            db, item, request, decision_id, before, decision_type
        )
    else:
        plan = _build_discard_plan(item, request, decision_id, before)
    kpi_effect = _persist_plan(
        db, actor, item, request, plan, decision_id
    )
    case = HumanReviewQueryService(
        db, actor=actor, correlation_id=request.correlation_id
    ).get_case(item.id)
    return ApplyDecisionResponse(
        case=case,
        decision_id=decision_id,
        kpi_effect=kpi_effect,
        correlation_id=request.correlation_id,
    )


def _load_reversal_actor_and_item(
    db: Session,
    actor: User,
    review_item_id: UUID,
    correlation_id: UUID,
) -> tuple[User, ReviewItem]:
    try:
        capability = authorize_b2b_action(
            db, actor, B2BAction.REVERT_SCIENTIFIC
        )
    except B2BAccessDenied as error:
        raise B2BCapabilityRequiredError(correlation_id) from error
    if capability is not B2BCapability.RESEARCH_MANAGER:
        raise B2BCapabilityRequiredError(correlation_id)
    persisted_actor = db.get(User, actor.id)
    if persisted_actor is None or not persisted_actor.is_active:
        raise B2BCapabilityRequiredError(correlation_id)
    with db.no_autoflush:
        item = db.scalars(
            select(ReviewItem)
            .where(
                ReviewItem.id == review_item_id,
                review_scope_predicate(persisted_actor),
            )
            .with_for_update()
        ).first()
    if item is None:
        if db.get(ReviewItem, review_item_id) is not None:
            raise B2BCapabilityRequiredError(correlation_id)
        raise ReviewCaseNotFoundError(correlation_id)
    try:
        assert_review_item_scope(
            persisted_actor,
            item,
            correlation_id=correlation_id,
        )
    except ReviewScopeDenied as error:
        raise B2BCapabilityRequiredError(correlation_id) from error
    return persisted_actor, item


def _validate_reversal_expected_state(
    item: ReviewItem,
    request: RevertRequest,
) -> None:
    if (
        item.version != request.expected_version
        or item.current_decision_id
        != request.expected_current_decision_id
    ):
        raise ReviewCaseVersionConflictError(request.correlation_id)


def _raise_reversal_domain_failure(
    error: Exception,
    correlation_id: UUID,
    current_decision_id: UUID,
) -> None:
    if isinstance(error, HumanReviewDomainError):
        if error.correlation_id is None:
            error.correlation_id = correlation_id
        raise error
    if isinstance(error, (B2BAccessDenied, PermissionError)):
        raise B2BCapabilityRequiredError(correlation_id) from error
    if isinstance(error, OptimisticLockError):
        raise ReviewCaseVersionConflictError(correlation_id) from error
    if isinstance(error, (ValueError, ValidationError)):
        message = str(error)
        incompatible_reversal_failures = (
            "decision to revert does not exist",
            "decision to revert belongs to another case",
            "decision to revert must be approved and projection-locked",
            "decision to revert has no complete restorable snapshot pair",
            "projection_before cannot restore the decision being reverted",
            "projection_before restore decision does not exist",
            "projection_before references a decision from another case",
            "restore decision must be approved and projection-locked",
            "restore decision projection does not match projection_before",
            "restored projection overrides must be locked",
            "restored canonical identity type does not match its snapshot",
        )
        malformed_current_payload = (
            f"decision {current_decision_id} has an invalid typed payload"
        )
        malformed_noncurrent_payload = (
            message.startswith("decision ")
            and message.endswith(" has an invalid typed payload")
            and message != malformed_current_payload
        )
        if (
            message in incompatible_reversal_failures
            or malformed_noncurrent_payload
        ):
            raise IncompatibleDecisionError(
                "Decision cannot be reversed",
                correlation_id=correlation_id,
            ) from error
        raise HumanReviewCommandInternalError(correlation_id) from error
    raise error


def _prepare_reversal(
    db: Session,
    actor: User,
    review_item_id: UUID,
    request: RevertRequest,
) -> ApplyDecisionResponse:
    persisted_actor, item = _load_reversal_actor_and_item(
        db,
        actor,
        review_item_id,
        request.correlation_id,
    )
    _validate_reversal_expected_state(item, request)
    command = FunctionalReversalCommandV1(
        review_item_id=review_item_id,
        decision_id_to_revert=request.decision_id_to_revert,
        actor_user_id=persisted_actor.id,
        expected_case_version=request.expected_version,
        reason=request.reason,
        correlation_id=request.correlation_id,
        request_id=None,
    )
    kpi_before = _capture_effective_kpi(db, item)
    try:
        decision = append_functional_reversal(db, command)
    except Exception as error:
        _raise_reversal_domain_failure(
            error,
            request.correlation_id,
            request.expected_current_decision_id,
        )
        raise AssertionError("unreachable")
    try:
        payload = _DECISION_PAYLOAD_ADAPTER.validate_python(
            decision.payload
        )
    except (TypeError, ValueError, ValidationError) as error:
        raise HumanReviewCommandInternalError(
            request.correlation_id
        ) from error
    if (
        not isinstance(payload, DecisionReversalPayloadV1)
        or payload.projection_before is None
        or payload.projection_after is None
    ):
        raise HumanReviewCommandInternalError(request.correlation_id)
    kpi_effect = _diff_effective_kpi(
        kpi_before,
        _capture_effective_kpi(db, item),
    )
    case = HumanReviewQueryService(
        db, actor=persisted_actor, correlation_id=request.correlation_id
    ).get_case(item.id)
    return ApplyDecisionResponse(
        case=case,
        decision_id=decision.id,
        kpi_effect=kpi_effect,
        correlation_id=request.correlation_id,
    )


def _translate_failure(
    db: Session,
    error: Exception,
    correlation_id: UUID,
) -> None:
    print(f"HUMAN_REVIEW_DEBUG correlation={correlation_id} error={type(error).__name__}: {error}", flush=True)
    db.rollback()
    if isinstance(error, HumanReviewDomainError):
        if error.correlation_id is None:
            error.correlation_id = correlation_id
        raise error
    if isinstance(error, B2BAccessDenied):
        raise B2BCapabilityRequiredError(correlation_id) from error
    if isinstance(error, OptimisticLockError):
        raise ReviewCaseVersionConflictError(correlation_id) from error
    if isinstance(error, (SQLAlchemyError, ValidationError, TypeError, ValueError, RuntimeError)):
        raise HumanReviewCommandInternalError(correlation_id) from error
    raise HumanReviewCommandInternalError(correlation_id) from error


def apply_decision(
    db: Session,
    actor: User,
    review_item_id: UUID,
    request: ApplyDecisionRequest,
) -> ApplyDecisionResponse:
    request = ApplyDecisionRequest.model_validate(request)
    request = _materialize_generated_identity_key(request)
    try:
        response = _prepare_command(
            db, actor, review_item_id, request
        )
        db.commit()
        return response
    except Exception as error:
        _translate_failure(db, error, request.correlation_id)
        raise AssertionError("unreachable")


def discard_case(
    db: Session,
    actor: User,
    review_item_id: UUID,
    request: DiscardRequest,
) -> ApplyDecisionResponse:
    request = DiscardRequest.model_validate(request)
    try:
        response = _prepare_command(
            db, actor, review_item_id, request
        )
        db.commit()
        return response
    except Exception as error:
        _translate_failure(db, error, request.correlation_id)
        raise AssertionError("unreachable")


def revert_case(
    db: Session,
    actor: User,
    review_item_id: UUID,
    request: RevertRequest,
) -> ApplyDecisionResponse:
    request = RevertRequest.model_validate(request)
    try:
        response = _prepare_reversal(
            db, actor, review_item_id, request
        )
        db.commit()
        return response
    except Exception as error:
        _translate_failure(db, error, request.correlation_id)
        raise AssertionError("unreachable")
