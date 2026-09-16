from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import re
from uuid import UUID, uuid4

from pydantic import BaseModel, ValidationError
from sqlalchemy import Select, select, text
from sqlalchemy.orm import Session

from app.models.human_review_audit import AuditEvent
from app.models.human_review_enums import AuditEventType
from app.schemas.human_review_operations import (
    AuditCorrectionCommandV1,
    AuditCorrectionPayloadV1,
    AuditEventCommandV1,
)


_SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_LOCK_NAMESPACE = "b2b:audit-lock:v1"
_PAYLOAD_SCHEMA_BY_EVENT_TYPE = {
    event_type: f"audit.{event_type.value}.v1"
    for event_type in AuditEventType
}


def _canonicalize(value: object) -> object:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Enum):
        return _canonicalize(value.value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("audit timestamps must be timezone-aware")
        normalized = value.astimezone(timezone.utc)
        return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, BaseModel):
        return _canonicalize(value.model_dump(mode="python"))
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("audit envelope mappings require string keys")
        return {key: _canonicalize(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonicalize(item) for item in value]
    raise TypeError(f"unsupported audit envelope value: {type(value).__name__}")


def _canonical_json(value: object) -> str:
    return json.dumps(
        _canonicalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _advisory_lock_key(aggregate_type: str, aggregate_key: str) -> int:
    if not aggregate_type.strip() or not aggregate_key.strip():
        raise ValueError("aggregate lock components cannot be blank")
    material = json.dumps(
        [_LOCK_NAMESPACE, aggregate_type, aggregate_key],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(material).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _payload_schema(event_type: AuditEventType) -> str:
    return _PAYLOAD_SCHEMA_BY_EVENT_TYPE[event_type]


def compute_audit_event_hash(
    command: AuditEventCommandV1,
    previous_hash: str | None,
) -> str:
    command = AuditEventCommandV1.model_validate(command)
    has_previous_id = command.previous_event_id is not None
    has_previous_hash = previous_hash is not None
    if has_previous_id != has_previous_hash:
        raise ValueError(
            "previous_event_id and previous_hash must either both be set or both be null"
        )
    if previous_hash is not None and _SHA256_HEX_PATTERN.fullmatch(previous_hash) is None:
        raise ValueError("previous_hash must be 64 lowercase hexadecimal characters")
    envelope = {
        "id": command.id,
        "event_type": command.event_type,
        "aggregate_type": command.aggregate_type,
        "aggregate_key": command.aggregate_key,
        "review_item_id": command.review_item_id,
        "actor_user_id": command.actor_user_id,
        "actor_identifier": command.actor_identifier,
        "actor_capability": command.actor_capability,
        "occurred_at": command.occurred_at,
        "payload_schema": _payload_schema(command.event_type),
        "payload_version": command.payload.schema_version,
        "payload": command.payload,
        "correlation_id": command.correlation_id,
        "request_id": command.request_id,
        "previous_event_id": command.previous_event_id,
        "corrects_event_id": command.corrects_event_id,
        "previous_event_hash": previous_hash,
    }
    return hashlib.sha256(_canonical_json(envelope).encode("utf-8")).hexdigest()


def _require_postgresql(db: Session) -> None:
    if db.get_bind().dialect.name != "postgresql":
        raise RuntimeError("human review audit appends require PostgreSQL")


def _acquire_aggregate_lock(
    db: Session,
    aggregate_type: str,
    aggregate_key: str,
) -> None:
    _require_postgresql(db)
    db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": _advisory_lock_key(aggregate_type, aggregate_key)},
    )


def _head_statement(aggregate_type: str, aggregate_key: str) -> Select:
    return (
        select(AuditEvent)
        .where(
            AuditEvent.aggregate_type == aggregate_type,
            AuditEvent.aggregate_key == aggregate_key,
        )
        .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
        .limit(1)
    )


def _load_head(
    db: Session,
    aggregate_type: str,
    aggregate_key: str,
) -> AuditEvent | None:
    return db.scalars(_head_statement(aggregate_type, aggregate_key)).first()


def _strip_hash(value: str | None) -> str | None:
    return value.strip() if value is not None else None


def _row_as_command(row: AuditEvent) -> AuditEventCommandV1:
    try:
        return AuditEventCommandV1.model_validate({
            "id": row.id,
            "event_type": row.event_type,
            "aggregate_type": row.aggregate_type,
            "aggregate_key": row.aggregate_key,
            "review_item_id": row.review_item_id,
            "actor_user_id": row.actor_user_id,
            "actor_identifier": row.actor_identifier,
            "actor_capability": row.actor_capability,
            "occurred_at": row.occurred_at,
            "payload": row.payload,
            "correlation_id": row.correlation_id,
            "request_id": row.request_id,
            "previous_event_id": row.previous_event_id,
            "corrects_event_id": row.corrects_event_id,
        })
    except ValidationError as exc:
        raise RuntimeError(f"stored audit head payload is invalid: {exc}") from exc


def _validate_stored_head(db: Session, head: AuditEvent) -> None:
    stored_previous_hash = _strip_hash(head.previous_event_hash)
    if head.previous_event_id is None:
        if stored_previous_hash is not None:
            raise RuntimeError(
                "stored audit head previous_event_hash is inconsistent with a null predecessor"
            )
    else:
        predecessor = db.get(AuditEvent, head.previous_event_id)
        if predecessor is None:
            raise RuntimeError("stored audit head previous_event_id does not exist")
        if (
            predecessor.aggregate_type != head.aggregate_type
            or predecessor.aggregate_key != head.aggregate_key
        ):
            raise RuntimeError("stored audit head previous_event_id belongs to another aggregate")
        predecessor_hash = _strip_hash(predecessor.event_hash)
        if stored_previous_hash != predecessor_hash:
            raise RuntimeError(
                "stored audit head previous_event_hash does not match its predecessor"
            )

    head_command = _row_as_command(head)
    if head.payload_schema != _payload_schema(head_command.event_type):
        raise RuntimeError("stored audit head payload_schema is inconsistent")
    if head.payload_version != head_command.payload.schema_version:
        raise RuntimeError("stored audit head payload_version is inconsistent")
    expected_hash = compute_audit_event_hash(head_command, stored_previous_hash)
    if _strip_hash(head.event_hash) != expected_hash:
        raise RuntimeError("stored audit head event_hash is inconsistent")


def _validate_expected_head(
    db: Session,
    command: AuditEventCommandV1,
    head: AuditEvent | None,
) -> str | None:
    if head is None:
        if command.previous_event_id is not None:
            raise RuntimeError(
                "previous_event_id must be null when the aggregate has no audit head"
            )
        return None

    _validate_stored_head(db, head)
    if command.previous_event_id != head.id:
        raise RuntimeError(
            "previous_event_id does not match the current audit head for this aggregate"
        )
    if command.occurred_at < head.occurred_at or (
        command.occurred_at == head.occurred_at and command.id.int <= head.id.int
    ):
        raise RuntimeError(
            "new audit event must sort after the current head by occurred_at and id"
        )
    return _strip_hash(head.event_hash)


def _append_locked(
    db: Session,
    command: AuditEventCommandV1,
    head: AuditEvent | None,
) -> AuditEvent:
    previous_hash = _validate_expected_head(db, command, head)
    event = AuditEvent(
        id=command.id,
        event_type=command.event_type.value,
        aggregate_type=command.aggregate_type,
        aggregate_key=command.aggregate_key,
        review_item_id=command.review_item_id,
        actor_user_id=command.actor_user_id,
        actor_identifier=command.actor_identifier,
        actor_capability=(
            command.actor_capability.value
            if command.actor_capability is not None
            else None
        ),
        occurred_at=command.occurred_at,
        payload_schema=_payload_schema(command.event_type),
        payload_version=command.payload.schema_version,
        payload=command.payload.model_dump(mode="json"),
        correlation_id=command.correlation_id,
        request_id=command.request_id,
        previous_event_id=command.previous_event_id,
        corrects_event_id=command.corrects_event_id,
        previous_event_hash=previous_hash,
        event_hash=compute_audit_event_hash(command, previous_hash),
    )
    db.add(event)
    db.flush()
    return event


def append_audit_event(
    db: Session,
    command: AuditEventCommandV1,
) -> AuditEvent:
    command = AuditEventCommandV1.model_validate(command)
    _acquire_aggregate_lock(db, command.aggregate_type, command.aggregate_key)
    head = _load_head(db, command.aggregate_type, command.aggregate_key)
    return _append_locked(db, command, head)


def append_audit_event_at_current_head(
    db: Session,
    command: AuditEventCommandV1,
) -> AuditEvent:
    command = AuditEventCommandV1.model_validate(command)
    if command.previous_event_id is not None:
        raise ValueError(
            "previous_event_id must be null when appending at the current audit head"
        )
    _acquire_aggregate_lock(db, command.aggregate_type, command.aggregate_key)
    head = _load_head(db, command.aggregate_type, command.aggregate_key)
    resolved_command = command.model_copy(update={
        "previous_event_id": head.id if head is not None else None,
    })
    return _append_locked(db, resolved_command, head)


def _new_correction_id(head: AuditEvent, occurred_at: datetime) -> UUID:
    candidate = uuid4()
    while occurred_at == head.occurred_at and candidate.int <= head.id.int:
        candidate = uuid4()
    return candidate


def append_audit_correction(
    db: Session,
    command: AuditCorrectionCommandV1,
) -> AuditEvent:
    command = AuditCorrectionCommandV1.model_validate(command)
    _require_postgresql(db)
    original = db.get(AuditEvent, command.original_event_id)
    if original is None:
        raise RuntimeError("original audit event does not exist")

    _acquire_aggregate_lock(db, original.aggregate_type, original.aggregate_key)
    head = _load_head(db, original.aggregate_type, original.aggregate_key)
    if head is None:
        raise RuntimeError("audit aggregate unexpectedly has no head")

    payload = AuditCorrectionPayloadV1(
        kind="audit_correction",
        schema_version=1,
        corrects_event_id=original.id,
        reason=command.reason,
        replacement_payload_schema=command.replacement_payload_schema,
        replacement_payload_version=command.replacement_payload_version,
        replacement_payload_sha256=command.replacement_payload_sha256,
    )
    event_command = AuditEventCommandV1(
        id=_new_correction_id(head, command.occurred_at),
        event_type=AuditEventType.AUDIT_CORRECTED,
        aggregate_type=original.aggregate_type,
        aggregate_key=original.aggregate_key,
        review_item_id=original.review_item_id,
        actor_user_id=command.actor_user_id,
        actor_identifier=command.actor_identifier,
        actor_capability=command.actor_capability,
        occurred_at=command.occurred_at,
        payload=payload,
        correlation_id=command.correlation_id,
        request_id=command.request_id,
        previous_event_id=head.id,
        corrects_event_id=original.id,
    )
    return _append_locked(db, event_command, head)
