from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import false, select
from sqlalchemy.orm import Session

from app.models.entities import (
    Career,
    PersonRole,
    ProjectTeacher,
    ScientificProduction,
    ScientificProductionAuthor,
    Teacher,
    User,
)
from app.models.enums import UserRole
from app.models.human_review_core import ReviewItem
from app.models.human_review_enums import ReviewTargetTable
from app.services.human_review_state import HumanReviewDomainError
from app.services.human_review_targets import REVIEW_TARGET_MODELS


@dataclass(frozen=True)
class ResolvedReviewScope:
    faculty_id: int | None
    career_id: int | None
    resolution_reason: str | None


class ReviewScopeDenied(HumanReviewDomainError):
    code = "B2B_CAPABILITY_REQUIRED"


def _role_value(user: User) -> str:
    role = getattr(user, "role", None)
    return getattr(role, "value", role)


def _resolved_from_careers(
    career_rows: tuple[tuple[int, int], ...],
) -> ResolvedReviewScope:
    careers = {career_id: faculty_id for career_id, faculty_id in career_rows}
    if not careers:
        return ResolvedReviewScope(None, None, "unresolved_no_persisted_scope")
    faculties = set(careers.values())
    if len(faculties) != 1:
        return ResolvedReviewScope(None, None, "unresolved_cross_faculty")
    faculty_id = next(iter(faculties))
    career_id = next(iter(careers)) if len(careers) == 1 else None
    return ResolvedReviewScope(faculty_id, career_id, None)


def _career_ids_for_target(
    db: Session,
    target_table: ReviewTargetTable,
    target_pk: int,
) -> tuple[int, ...]:
    statements = []
    if target_table is ReviewTargetTable.PERSON_ROLES:
        statements.extend((
            select(Teacher.career_id)
            .join(PersonRole, PersonRole.teacher_id == Teacher.id)
            .where(PersonRole.id == target_pk),
            select(Teacher.career_id)
            .join(ScientificProduction, ScientificProduction.teacher_id == Teacher.id)
            .join(PersonRole, PersonRole.scientific_production_id == ScientificProduction.id)
            .where(PersonRole.id == target_pk),
            select(Teacher.career_id)
            .join(ProjectTeacher, ProjectTeacher.teacher_id == Teacher.id)
            .join(PersonRole, PersonRole.research_project_id == ProjectTeacher.project_id)
            .where(PersonRole.id == target_pk),
        ))
    elif target_table is ReviewTargetTable.SCIENTIFIC_PRODUCTION_AUTHORS:
        statements.extend((
            select(Teacher.career_id)
            .join(ScientificProductionAuthor, ScientificProductionAuthor.teacher_id == Teacher.id)
            .where(ScientificProductionAuthor.id == target_pk),
            select(Teacher.career_id)
            .join(ScientificProduction, ScientificProduction.teacher_id == Teacher.id)
            .join(
                ScientificProductionAuthor,
                ScientificProductionAuthor.production_id == ScientificProduction.id,
            )
            .where(ScientificProductionAuthor.id == target_pk),
        ))
    elif target_table is ReviewTargetTable.SCIENTIFIC_PRODUCTIONS:
        statements.extend((
            select(Teacher.career_id)
            .join(ScientificProduction, ScientificProduction.teacher_id == Teacher.id)
            .where(ScientificProduction.id == target_pk),
            select(Teacher.career_id)
            .join(ScientificProductionAuthor, ScientificProductionAuthor.teacher_id == Teacher.id)
            .where(ScientificProductionAuthor.production_id == target_pk),
        ))
    return tuple({career_id for statement in statements for career_id in db.scalars(statement)})


def resolve_target_scope(
    db: Session,
    target_table: ReviewTargetTable | str,
    target_pk: int | None,
) -> ResolvedReviewScope:
    try:
        table = ReviewTargetTable(target_table)
    except (TypeError, ValueError):
        return ResolvedReviewScope(None, None, "unresolved_missing_target")
    model = REVIEW_TARGET_MODELS[table]
    if target_pk is None or db.get(model, target_pk) is None:
        return ResolvedReviewScope(None, None, "unresolved_missing_target")
    career_ids = _career_ids_for_target(db, table, target_pk)
    if not career_ids:
        return ResolvedReviewScope(None, None, "unresolved_no_persisted_scope")
    rows = tuple(db.execute(
        select(Career.id, Career.faculty_id).where(Career.id.in_(career_ids))
    ))
    if len(rows) != len(set(career_ids)):
        return ResolvedReviewScope(None, None, "unresolved_no_persisted_scope")
    return _resolved_from_careers(tuple((row[0], row[1]) for row in rows))


def review_scope_predicate(user: User):
    role = _role_value(user)
    faculty_id = getattr(user, "faculty_id", None)
    career_id = getattr(user, "career_id", None)
    if role == UserRole.FACULTY_ADMIN.value and faculty_id is not None:
        return ReviewItem.scope_faculty_id == faculty_id
    if role == UserRole.CAREER_MANAGER.value and career_id is not None:
        return (
            ReviewItem.scope_career_id.is_not(None)
            & (ReviewItem.scope_career_id == career_id)
        )
    return false()


def assert_review_item_scope(
    user: User,
    item: ReviewItem,
    *,
    correlation_id: UUID | None = None,
) -> None:
    role = _role_value(user)
    faculty_id = getattr(user, "faculty_id", None)
    career_id = getattr(user, "career_id", None)
    allowed = False
    if role == UserRole.FACULTY_ADMIN.value and faculty_id is not None:
        allowed = (
            item.scope_faculty_id is not None
            and item.scope_faculty_id == faculty_id
        )
    elif role == UserRole.CAREER_MANAGER.value and career_id is not None:
        allowed = (
            item.scope_career_id is not None
            and item.scope_career_id == career_id
        )
    if not allowed:
        raise ReviewScopeDenied(
            "Human Review scope is not authorized",
            correlation_id=correlation_id,
        )
