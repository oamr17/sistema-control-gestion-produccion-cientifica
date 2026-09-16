from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.entities import User
from app.models.enums import UserRole
from app.models.human_review_access import UserB2BCapability
from app.models.human_review_audit import AuditEvent
from app.models.human_review_enums import AuditEventType, B2BCapability
from app.schemas.human_review_operations import (
    ApprovedAccountAssignmentV1,
    ApprovedAccountAssignmentsV1,
    AuditEventCommandV1,
    CapabilityAssignmentPlanEntryV1,
    CapabilityAssignmentPlanV1,
    CapabilityAssignmentResultV1,
    CapabilityChangedAuditPayloadV1,
)
from app.services.human_review_audit import append_audit_event


_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_AUDIT_AGGREGATE_TYPE = "user_b2b_capability"


class CapabilityAssignmentConflict(RuntimeError):
    pass


def _canonical_manifest_sha256(
    manifest: ApprovedAccountAssignmentsV1,
) -> str:
    validated = ApprovedAccountAssignmentsV1.model_validate(manifest)
    canonical_json = json.dumps(
        validated.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def _role_value(user: User) -> str:
    return getattr(user.role, "value", user.role)


def _entry(
    assignment: ApprovedAccountAssignmentV1,
    action: str,
    reason: str,
) -> CapabilityAssignmentPlanEntryV1:
    return CapabilityAssignmentPlanEntryV1(
        user_id=assignment.user_id,
        email=assignment.email,
        capability=assignment.capability,
        action=action,
        reason=reason,
    )


def _classify_assignment(
    db: Session,
    assignment: ApprovedAccountAssignmentV1,
) -> CapabilityAssignmentPlanEntryV1:
    user = db.get(User, assignment.user_id)
    if user is None:
        return _entry(assignment, "conflict", "user_not_found")
    if user.email != str(assignment.email):
        return _entry(assignment, "conflict", "email_mismatch")
    if not user.is_active:
        return _entry(assignment, "conflict", "user_inactive")
    if _role_value(user) == UserRole.CAREER_MANAGER.value:
        return _entry(assignment, "conflict", "career_manager_denied")

    active_assignments = tuple(db.scalars(
        select(UserB2BCapability)
        .where(
            UserB2BCapability.user_id == assignment.user_id,
            UserB2BCapability.is_active.is_(True),
        )
        .order_by(UserB2BCapability.id)
    ))
    if not active_assignments:
        return _entry(assignment, "insert", "eligible_for_insert")
    if len(active_assignments) != 1:
        return _entry(
            assignment,
            "conflict",
            "conflicting_active_assignments",
        )
    try:
        active_capability = B2BCapability(active_assignments[0].capability)
    except ValueError:
        return _entry(assignment, "conflict", "active_capability_invalid")
    if active_capability is assignment.capability:
        return _entry(assignment, "unchanged", "active_assignment_matches")
    return _entry(assignment, "conflict", "different_active_capability")


def plan_capability_assignments(
    db: Session,
    manifest: ApprovedAccountAssignmentsV1,
) -> CapabilityAssignmentPlanV1:
    manifest = ApprovedAccountAssignmentsV1.model_validate(manifest)
    entries = tuple(
        _classify_assignment(db, assignment)
        for assignment in manifest.assignments
    )
    return CapabilityAssignmentPlanV1(
        schema_version=1,
        manifest_sha256=_canonical_manifest_sha256(manifest),
        entries=entries,
        insert_count=sum(entry.action == "insert" for entry in entries),
        unchanged_count=sum(entry.action == "unchanged" for entry in entries),
        conflict_count=sum(entry.action == "conflict" for entry in entries),
    )


def _current_audit_head(
    db: Session,
    aggregate_key: str,
) -> AuditEvent | None:
    return db.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.aggregate_type == _AUDIT_AGGREGATE_TYPE,
            AuditEvent.aggregate_key == aggregate_key,
        )
        .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
        .limit(1)
    ).first()


def _next_event_identity(
    head: AuditEvent | None,
) -> tuple[datetime, object]:
    occurred_at = datetime.now(timezone.utc)
    event_id = uuid4()
    if head is not None:
        if occurred_at < head.occurred_at:
            occurred_at = head.occurred_at
        while occurred_at == head.occurred_at and event_id.int <= head.id.int:
            event_id = uuid4()
    return occurred_at, event_id


def _append_assignment_audit(
    db: Session,
    assignment: UserB2BCapability,
    operator_identifier: str,
    correlation_id,
) -> None:
    aggregate_key = f"user:{assignment.user_id}"
    head = _current_audit_head(db, aggregate_key)
    occurred_at, event_id = _next_event_identity(head)
    append_audit_event(
        db,
        AuditEventCommandV1(
            id=event_id,
            event_type=AuditEventType.CAPABILITY_ASSIGNED,
            aggregate_type=_AUDIT_AGGREGATE_TYPE,
            aggregate_key=aggregate_key,
            review_item_id=None,
            actor_user_id=None,
            actor_identifier=operator_identifier,
            actor_capability=None,
            occurred_at=occurred_at,
            payload=CapabilityChangedAuditPayloadV1(
                kind="capability_changed",
                schema_version=1,
                assignment_id=assignment.id,
                user_id=assignment.user_id,
                capability=B2BCapability(assignment.capability),
                action="assigned",
                approved_input_sha256=assignment.approved_input_sha256,
            ),
            correlation_id=correlation_id,
            request_id=None,
            previous_event_id=head.id if head is not None else None,
            corrects_event_id=None,
        ),
    )


def _abort(db: Session, message: str) -> None:
    db.rollback()
    raise CapabilityAssignmentConflict(message)


def apply_capability_assignments(
    db: Session,
    manifest: ApprovedAccountAssignmentsV1,
    approved_manifest_sha256: str,
    operator_identifier: str,
) -> CapabilityAssignmentResultV1:
    manifest = ApprovedAccountAssignmentsV1.model_validate(manifest)
    manifest_sha256 = _canonical_manifest_sha256(manifest)
    if (
        _SHA256_HEX.fullmatch(approved_manifest_sha256) is None
        or not hmac.compare_digest(approved_manifest_sha256, manifest_sha256)
    ):
        _abort(db, "approved manifest SHA-256 does not match canonical manifest")
    if not operator_identifier.strip() or len(operator_identifier) > 180:
        _abort(db, "operator_identifier must be nonblank and at most 180 characters")

    plan = plan_capability_assignments(db, manifest)
    if plan.conflict_count:
        _abort(db, "capability assignment manifest contains conflicts")

    entries_to_insert = tuple(
        entry for entry in plan.entries if entry.action == "insert"
    )
    if not entries_to_insert:
        return CapabilityAssignmentResultV1(
            schema_version=1,
            manifest_sha256=manifest_sha256,
            inserted=0,
            unchanged=plan.unchanged_count,
            audit_events_created=0,
        )

    assignments = tuple(
        UserB2BCapability(
            id=uuid4(),
            user_id=entry.user_id,
            capability=entry.capability.value,
            is_active=True,
            approval_reference=manifest.approval_reference,
            approved_input_sha256=manifest_sha256,
            assigned_by_identifier=operator_identifier,
        )
        for entry in entries_to_insert
    )
    correlation_id = uuid4()
    try:
        db.add_all(assignments)
        db.flush()
        for assignment in assignments:
            _append_assignment_audit(
                db,
                assignment,
                operator_identifier,
                correlation_id,
            )
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise CapabilityAssignmentConflict(
            "capability assignment integrity conflict; transaction rolled back"
        ) from exc

    return CapabilityAssignmentResultV1(
        schema_version=1,
        manifest_sha256=manifest_sha256,
        inserted=len(assignments),
        unchanged=plan.unchanged_count,
        audit_events_created=len(assignments),
    )
