from __future__ import annotations

import unittest

from sqlalchemy import ForeignKeyConstraint, Index, UniqueConstraint

from app.models.entities import Career, User
from app.models.enums import UserRole
from app.models.human_review_core import ReviewItem
from app.models.human_review_enums import B2BAction, B2BCapability
from app.services.human_review_authorization import (
    B2BAccessDenied,
    authorize_b2b_action,
    effective_b2b_access,
)
from app.services.human_review_scope import (
    ReviewScopeDenied,
    assert_review_item_scope,
    resolve_target_scope,
)


class _FakeDb:
    def __init__(self, *, target=True, scalar_batches=(), career_rows=(), user=None):
        self.target = object() if target else None
        self.scalar_batches = list(scalar_batches)
        self.career_rows = career_rows
        self.user = user

    def get(self, model, key):
        if model is User:
            return self.user
        return self.target

    def scalars(self, statement):
        return iter(self.scalar_batches.pop(0) if self.scalar_batches else ())

    def execute(self, statement):
        return iter(self.career_rows)


def _user(role, *, faculty_id=None, career_id=None):
    return User(
        id=1,
        email="demo@example.test",
        full_name="Demo",
        hashed_password="not-a-secret",
        role=role,
        faculty_id=faculty_id,
        career_id=career_id,
        is_active=True,
    )


def _item(*, faculty_id=None, career_id=None, reason=None):
    return ReviewItem(
        scope_faculty_id=faculty_id,
        scope_career_id=career_id,
        scope_resolution_reason=reason,
    )


class HumanReviewScopeTests(unittest.TestCase):
    def test_model_contains_only_the_approved_scope_schema(self):
        user_fk = next(
            fk for fk in User.__table__.foreign_key_constraints
            if fk.name == "fk_users_faculty_id_faculties"
        )
        self.assertEqual(tuple(user_fk.column_keys), ("faculty_id",))
        self.assertEqual(user_fk.ondelete, "RESTRICT")
        self.assertTrue(User.__table__.c.faculty_id.nullable)
        self.assertIn("ix_users_faculty_id", {index.name for index in User.__table__.indexes})

        uniques = {
            constraint.name: tuple(constraint.columns.keys())
            for constraint in Career.__table__.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        self.assertEqual(uniques["uq_careers_id_faculty_id"], ("id", "faculty_id"))

        review = ReviewItem.__table__
        self.assertTrue(review.c.scope_faculty_id.nullable)
        self.assertTrue(review.c.scope_career_id.nullable)
        self.assertEqual(review.c.scope_resolution_reason.type.length, 80)
        composite = next(
            constraint for constraint in review.constraints
            if isinstance(constraint, ForeignKeyConstraint)
            and constraint.name == "fk_review_items_scope_career_faculty_careers"
        )
        self.assertEqual(tuple(composite.column_keys), ("scope_career_id", "scope_faculty_id"))
        indexes = {
            index.name: tuple(column.name for column in index.columns)
            for index in review.indexes
            if isinstance(index, Index)
        }
        self.assertEqual(
            indexes["ix_review_items_scope_career_queue"],
            ("scope_career_id", "case_status", "automatic_priority", "created_at", "id"),
        )

    def test_fk_only_resolver_outcomes(self):
        single = resolve_target_scope(
            _FakeDb(scalar_batches=((10,), (), ()), career_rows=((10, 1),)),
            "person_roles",
            7,
        )
        self.assertEqual((single.faculty_id, single.career_id, single.resolution_reason), (1, 10, None))

        same_faculty = resolve_target_scope(
            _FakeDb(scalar_batches=((10,), (11,), ()), career_rows=((10, 1), (11, 1))),
            "person_roles",
            7,
        )
        self.assertEqual((same_faculty.faculty_id, same_faculty.career_id, same_faculty.resolution_reason), (1, None, None))

        cross_faculty = resolve_target_scope(
            _FakeDb(scalar_batches=((10,), (20,), ()), career_rows=((10, 1), (20, 2))),
            "person_roles",
            7,
        )
        self.assertEqual(cross_faculty.resolution_reason, "unresolved_cross_faculty")

        missing = resolve_target_scope(_FakeDb(target=False), "person_roles", 7)
        self.assertEqual(missing.resolution_reason, "unresolved_missing_target")
        unlinked = resolve_target_scope(
            _FakeDb(scalar_batches=((), (), ())), "person_roles", 7
        )
        self.assertEqual(unlinked.resolution_reason, "unresolved_no_persisted_scope")

    def test_role_action_matrix_requires_persisted_scope(self):
        faculty = _user(UserRole.FACULTY_ADMIN, faculty_id=1)
        capability, actions = effective_b2b_access(_FakeDb(user=faculty), faculty.id)
        self.assertIsNone(capability)
        self.assertEqual(set(actions), {
            B2BAction.VIEW_FOUNDATIONS,
            B2BAction.VIEW_AUDIT,
            B2BAction.APPLY_SCIENTIFIC,
            B2BAction.REVERT_SCIENTIFIC,
        })
        self.assertEqual(
            authorize_b2b_action(_FakeDb(user=faculty), faculty, B2BAction.APPLY_SCIENTIFIC),
            B2BCapability.RESEARCH_MANAGER,
        )

        incomplete = _user(UserRole.FACULTY_ADMIN)
        self.assertEqual(effective_b2b_access(_FakeDb(user=incomplete), 1), (None, ()))
        with self.assertRaises(B2BAccessDenied):
            authorize_b2b_action(
                _FakeDb(user=incomplete), incomplete, B2BAction.VIEW_FOUNDATIONS
            )

    def test_scope_assertion_is_fail_closed(self):
        faculty = _user(UserRole.FACULTY_ADMIN, faculty_id=1)
        career = _user(UserRole.CAREER_MANAGER, career_id=10)
        assert_review_item_scope(faculty, _item(faculty_id=1, career_id=11))
        assert_review_item_scope(career, _item(faculty_id=1, career_id=10))
        for actor, item in (
            (faculty, _item(faculty_id=2, career_id=20)),
            (career, _item(faculty_id=1, career_id=None)),
            (career, _item(faculty_id=1, career_id=11)),
            (faculty, _item(reason="unresolved_no_persisted_scope")),
        ):
            with self.subTest(role=actor.role, item=item):
                with self.assertRaises(ReviewScopeDenied):
                    assert_review_item_scope(actor, item)


if __name__ == "__main__":
    unittest.main()
