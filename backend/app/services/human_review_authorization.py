from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import User
from app.models.enums import UserRole
from app.models.human_review_access import UserB2BCapability
from app.models.human_review_enums import B2BAction, B2BCapability


class B2BAccessDenied(PermissionError):
    pass


_ALLOWED_ACTIONS: dict[B2BCapability, frozenset[B2BAction]] = {
    B2BCapability.RESEARCH_MANAGER: frozenset({
        B2BAction.VIEW_FOUNDATIONS,
        B2BAction.VIEW_AUDIT,
        B2BAction.APPLY_SCIENTIFIC,
        B2BAction.PROPOSE_SCIENTIFIC,
        B2BAction.REVERT_SCIENTIFIC,
    }),
    B2BCapability.SYSTEM_ADMIN: frozenset({
        B2BAction.VIEW_FOUNDATIONS,
        B2BAction.VIEW_AUDIT,
        B2BAction.MANAGE_TECHNICAL_ACCESS,
    }),
}

_SCOPED_ROLE_ACTIONS = frozenset({
    B2BAction.VIEW_FOUNDATIONS,
    B2BAction.VIEW_AUDIT,
    B2BAction.APPLY_SCIENTIFIC,
    B2BAction.REVERT_SCIENTIFIC,
})


def _scoped_role_actions(user: User | None) -> frozenset[B2BAction]:
    if user is None or not user.is_active:
        return frozenset()
    role = _role_value(user)
    if role == UserRole.FACULTY_ADMIN.value and getattr(user, "faculty_id", None) is not None:
        return _SCOPED_ROLE_ACTIONS
    if role == UserRole.CAREER_MANAGER.value and getattr(user, "career_id", None) is not None:
        return _SCOPED_ROLE_ACTIONS
    return frozenset()


def effective_b2b_access(
    db: Session,
    user_id: int,
) -> tuple[B2BCapability | None, tuple[B2BAction, ...]]:
    """Return the effective persisted capability and its deterministic actions."""

    user = db.get(User, user_id)
    capability = resolve_b2b_capability(db, user_id)
    allowed = set(_scoped_role_actions(user))
    if allowed and capability is not None:
        allowed.update(_ALLOWED_ACTIONS[capability])
    actions = tuple(sorted(allowed, key=lambda action: action.value))
    return capability, actions


def _role_value(user: User) -> str:
    return getattr(user.role, "value", user.role)


def resolve_b2b_capability(
    db: Session,
    user_id: int,
) -> B2BCapability | None:
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        return None
    if _role_value(user) == UserRole.CAREER_MANAGER.value:
        return None

    assignments = tuple(db.scalars(
        select(UserB2BCapability).where(
            UserB2BCapability.user_id == user_id,
            UserB2BCapability.is_active.is_(True),
        )
    ))
    if len(assignments) != 1:
        return None
    try:
        return B2BCapability(assignments[0].capability)
    except ValueError:
        return None


def authorize_b2b_action(
    db: Session,
    user: User,
    action: B2BAction,
) -> B2BCapability:
    capability = resolve_b2b_capability(db, user.id)
    try:
        requested_action = B2BAction(action)
    except (TypeError, ValueError):
        requested_action = None
    role_actions = _scoped_role_actions(user)
    if not role_actions:
        raise B2BAccessDenied("B2B action is not authorized")
    capability_actions = (
        frozenset() if capability is None else _ALLOWED_ACTIONS[capability]
    )
    if requested_action is None or requested_action not in role_actions | capability_actions:
        raise B2BAccessDenied("B2B action is not authorized")
    if capability is not None and requested_action in capability_actions:
        return capability
    return B2BCapability.RESEARCH_MANAGER
