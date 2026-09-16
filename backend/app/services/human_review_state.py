from __future__ import annotations

from types import MappingProxyType
from typing import Mapping
from uuid import UUID

from app.models.human_review_enums import ReviewCaseStatus
from app.schemas.human_review_operations import OptimisticLockError


class HumanReviewDomainError(ValueError):
    code = "HUMAN_REVIEW_VALIDATION"

    def __init__(
        self,
        message: str,
        *,
        correlation_id: UUID | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        self.correlation_id = correlation_id
        self.details = None if details is None else MappingProxyType(dict(details))
        super().__init__(message)


class IncompatibleDecisionError(HumanReviewDomainError):
    code = "INCOMPATIBLE_DECISION"


_ALLOWED_TRANSITIONS: frozenset[tuple[ReviewCaseStatus, ReviewCaseStatus]] = frozenset({
    (ReviewCaseStatus.PENDING, ReviewCaseStatus.IN_REVIEW),
    (ReviewCaseStatus.PENDING, ReviewCaseStatus.RESOLVED),
    (ReviewCaseStatus.PENDING, ReviewCaseStatus.SUPERSEDED),
    (ReviewCaseStatus.IN_REVIEW, ReviewCaseStatus.AWAITING_GESTOR_APPROVAL),
    (ReviewCaseStatus.IN_REVIEW, ReviewCaseStatus.RESOLVED),
    (ReviewCaseStatus.IN_REVIEW, ReviewCaseStatus.CONFLICTED),
    (ReviewCaseStatus.AWAITING_GESTOR_APPROVAL, ReviewCaseStatus.IN_REVIEW),
    (ReviewCaseStatus.AWAITING_GESTOR_APPROVAL, ReviewCaseStatus.RESOLVED),
    (ReviewCaseStatus.RESOLVED, ReviewCaseStatus.REOPENED),
    (ReviewCaseStatus.RESOLVED, ReviewCaseStatus.CONFLICTED),
    (ReviewCaseStatus.RESOLVED, ReviewCaseStatus.SUPERSEDED),
    (ReviewCaseStatus.REOPENED, ReviewCaseStatus.IN_REVIEW),
    (ReviewCaseStatus.REOPENED, ReviewCaseStatus.RESOLVED),
    (ReviewCaseStatus.CONFLICTED, ReviewCaseStatus.IN_REVIEW),
    (ReviewCaseStatus.CONFLICTED, ReviewCaseStatus.RESOLVED),
})


def assert_case_transition(
    current: ReviewCaseStatus,
    target: ReviewCaseStatus,
) -> None:
    try:
        transition = (ReviewCaseStatus(current), ReviewCaseStatus(target))
    except (TypeError, ValueError) as exc:
        raise ValueError("case transition contains an unknown status") from exc
    if transition not in _ALLOWED_TRANSITIONS:
        raise IncompatibleDecisionError(
            "Case transition is incompatible with the current state",
            details={
                "current_status": transition[0].value,
                "target_status": transition[1].value,
            },
        )


def assert_expected_version(actual: int, expected: int) -> None:
    if (
        isinstance(actual, bool)
        or isinstance(expected, bool)
        or not isinstance(actual, int)
        or not isinstance(expected, int)
    ):
        raise TypeError("case versions must be integers")
    if actual < 1 or expected < 1:
        raise ValueError("case versions must be positive")
    if actual != expected:
        raise OptimisticLockError(
            f"case version conflict: expected {expected}, found {actual}"
        )