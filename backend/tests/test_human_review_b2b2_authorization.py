from __future__ import annotations

import importlib
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import UUID

from fastapi import HTTPException

from app.models.enums import UserRole
from app.models.human_review_enums import B2BAction, B2BCapability
from app.services.human_review_authorization import B2BAccessDenied, authorize_b2b_action


DEPENDENCIES_MODULE = "app.api.dependencies"
UUID_A = UUID("11111111-1111-1111-1111-111111111111")


def _user(role=UserRole.FACULTY_ADMIN, user_id: int = 7, *, assigned: bool = True):
    return SimpleNamespace(
        id=user_id,
        role=role,
        is_active=True,
        faculty_id=1 if assigned and role is UserRole.FACULTY_ADMIN else None,
        career_id=10 if assigned and role is UserRole.CAREER_MANAGER else None,
    )


def _db(user, capability: B2BCapability | None):
    db = Mock()
    db.get.return_value = user
    assignments = [] if capability is None else [SimpleNamespace(capability=capability.value)]
    db.scalars.return_value = assignments
    return db


def _dependency(
    action: B2BAction,
):
    module = importlib.import_module(DEPENDENCIES_MODULE)
    factory = getattr(module, "require_b2b_action", None)
    return module, factory(action)


def _pre_task2_unsafe_dependency(action: B2BAction):
    """Test-side baseline for the absent Task 2 dependency surface."""

    def dependency(*, request, user, db):
        return user

    return dependency


class _PreTask2DependencyBehaviorProofs(unittest.TestCase):
    def test_absent_dependency_lacks_safe_scientific_denial(self) -> None:
        dependency = _pre_task2_unsafe_dependency(B2BAction.APPLY_SCIENTIFIC)
        actor = _user()
        request = SimpleNamespace(state=SimpleNamespace(correlation_id=UUID_A))
        with self.assertRaises(
            HTTPException,
            msg="PRE-TASK2 BEHAVIOR [dependency_safe_403] permitted an unassigned scientific action",
        ):
            dependency(request=request, user=actor, db=Mock())

    def test_absent_dependency_does_not_preserve_career_denial(self) -> None:
        dependency = _pre_task2_unsafe_dependency(B2BAction.APPLY_SCIENTIFIC)
        career = _user(UserRole.CAREER_MANAGER)
        request = SimpleNamespace(state=SimpleNamespace(correlation_id=UUID_A))
        with self.assertRaises(
            HTTPException,
            msg="PRE-TASK2 BEHAVIOR [dependency_career_denial] permitted CAREER_MANAGER",
        ):
            dependency(request=request, user=career, db=Mock())


class HumanReviewB2B2AuthorizationTests(unittest.TestCase):
    def test_independent_complete_capability_action_matrix(self) -> None:
        expected = {
            B2BCapability.RESEARCH_MANAGER: frozenset({
                B2BAction.VIEW_FOUNDATIONS,
                B2BAction.VIEW_AUDIT,
                B2BAction.PROPOSE_SCIENTIFIC,
                B2BAction.APPLY_SCIENTIFIC,
                B2BAction.REVERT_SCIENTIFIC,
            }),
            B2BCapability.SYSTEM_ADMIN: frozenset({
                B2BAction.VIEW_FOUNDATIONS,
                B2BAction.VIEW_AUDIT,
                B2BAction.APPLY_SCIENTIFIC,
                B2BAction.REVERT_SCIENTIFIC,
                B2BAction.MANAGE_TECHNICAL_ACCESS,
            }),
        }
        for capability in B2BCapability:
            user = _user()
            for action in B2BAction:
                db = _db(user, capability)
                with self.subTest(capability=capability, action=action):
                    if action in expected[capability]:
                        expected_capability = (
                            B2BCapability.RESEARCH_MANAGER
                            if capability is B2BCapability.SYSTEM_ADMIN
                            and action in {
                                B2BAction.APPLY_SCIENTIFIC,
                                B2BAction.REVERT_SCIENTIFIC,
                            }
                            else capability
                        )
                        self.assertEqual(
                            authorize_b2b_action(db, user, action),
                            expected_capability,
                        )
                    else:
                        with self.assertRaises(B2BAccessDenied):
                            authorize_b2b_action(db, user, action)

    def test_career_scope_actions_and_unassigned_denial(self) -> None:
        for action in B2BAction:
            for capability in (B2BCapability.RESEARCH_MANAGER, B2BCapability.SYSTEM_ADMIN):
                career = _user(UserRole.CAREER_MANAGER)
                db = _db(career, capability)
                with self.subTest(actor="career", capability=capability, action=action):
                    if action in {
                        B2BAction.VIEW_FOUNDATIONS,
                        B2BAction.VIEW_AUDIT,
                        B2BAction.APPLY_SCIENTIFIC,
                        B2BAction.REVERT_SCIENTIFIC,
                    }:
                        self.assertEqual(
                            authorize_b2b_action(db, career, action),
                            B2BCapability.RESEARCH_MANAGER,
                        )
                    else:
                        with self.assertRaises(B2BAccessDenied):
                            authorize_b2b_action(db, career, action)
                db.scalars.assert_not_called()
            unassigned = _user(assigned=False)
            with self.subTest(actor="unassigned", action=action), self.assertRaises(B2BAccessDenied):
                authorize_b2b_action(_db(unassigned, None), unassigned, action)

    def test_institutional_roles_grant_only_scoped_review_actions(self) -> None:
        for role in UserRole:
            for action in B2BAction:
                user = _user(role)
                with self.subTest(role=role, action=action):
                    if action in {
                        B2BAction.VIEW_FOUNDATIONS,
                        B2BAction.VIEW_AUDIT,
                        B2BAction.APPLY_SCIENTIFIC,
                        B2BAction.REVERT_SCIENTIFIC,
                    }:
                        self.assertEqual(
                            authorize_b2b_action(_db(user, None), user, action),
                            B2BCapability.RESEARCH_MANAGER,
                        )
                    else:
                        with self.assertRaises(B2BAccessDenied):
                            authorize_b2b_action(_db(user, None), user, action)

    def test_research_manager_allows_each_approved_scientific_action(self) -> None:
        user = _user()
        db = _db(user, B2BCapability.RESEARCH_MANAGER)
        for action in (
            B2BAction.VIEW_FOUNDATIONS,
            B2BAction.VIEW_AUDIT,
            B2BAction.PROPOSE_SCIENTIFIC,
            B2BAction.APPLY_SCIENTIFIC,
            B2BAction.REVERT_SCIENTIFIC,
        ):
            with self.subTest(action=action):
                self.assertEqual(authorize_b2b_action(db, user, action), B2BCapability.RESEARCH_MANAGER)

    def test_system_admin_is_read_and_technical_only_not_scientific(self) -> None:
        user = _user()
        db = _db(user, B2BCapability.SYSTEM_ADMIN)
        for action in (B2BAction.VIEW_FOUNDATIONS, B2BAction.VIEW_AUDIT, B2BAction.MANAGE_TECHNICAL_ACCESS):
            with self.subTest(action=action):
                self.assertEqual(authorize_b2b_action(db, user, action), B2BCapability.SYSTEM_ADMIN)
        for action in (B2BAction.PROPOSE_SCIENTIFIC,):
            with self.subTest(action=action), self.assertRaises(B2BAccessDenied):
                authorize_b2b_action(db, user, action)

    def test_career_manager_is_role_authorized_and_unassigned_users_are_denied(self) -> None:
        career = _user(UserRole.CAREER_MANAGER)
        for capability in (None, B2BCapability.RESEARCH_MANAGER, B2BCapability.SYSTEM_ADMIN):
            db = _db(career, capability)
            with self.subTest(capability=capability):
                self.assertEqual(
                    authorize_b2b_action(db, career, B2BAction.VIEW_FOUNDATIONS),
                    B2BCapability.RESEARCH_MANAGER,
                )
            db.scalars.assert_not_called()
        unassigned = _user(assigned=False)
        with self.assertRaises(B2BAccessDenied):
            authorize_b2b_action(_db(unassigned, None), unassigned, B2BAction.VIEW_FOUNDATIONS)

    def test_institutional_role_action_does_not_require_persisted_capability(self) -> None:
        for role in UserRole:
            user = _user(role)
            db = _db(user, None)
            with self.subTest(role=role):
                self.assertEqual(
                    authorize_b2b_action(db, user, B2BAction.VIEW_AUDIT),
                    B2BCapability.RESEARCH_MANAGER,
                )

    def test_dependency_preserves_authenticated_actor(self) -> None:
        module, dependency = _dependency(B2BAction.APPLY_SCIENTIFIC)
        actor = _user()
        request = SimpleNamespace(state=SimpleNamespace(correlation_id=UUID_A))
        db = Mock()
        with patch.object(module, "authorize_b2b_action", return_value=B2BCapability.RESEARCH_MANAGER) as authorize:
            self.assertIs(dependency(request=request, user=actor, db=db), actor)
        authorize.assert_called_once_with(db, actor, B2BAction.APPLY_SCIENTIFIC)

    def test_dependency_maps_denial_to_safe_403_envelope(self) -> None:
        module, dependency = _dependency(B2BAction.APPLY_SCIENTIFIC)
        actor = _user()
        request = SimpleNamespace(state=SimpleNamespace(correlation_id=UUID_A))
        db = Mock()
        internal = "SELECT password_hash FROM users at C:\\private\\auth.py token=secret"
        with patch.object(module, "authorize_b2b_action", side_effect=B2BAccessDenied(internal)):
            with self.assertRaises(HTTPException) as raised:
                dependency(request=request, user=actor, db=db)
        error = raised.exception
        self.assertEqual(error.status_code, 403)
        self.assertIsInstance(error.detail, dict)
        self.assertEqual(error.detail["code"], "B2B_CAPABILITY_REQUIRED")
        self.assertEqual(error.detail["correlation_id"], str(UUID_A))
        serialized = str(error.detail).lower()
        for forbidden in ("select password", "c:\\private", "token=secret"):
            self.assertNotIn(forbidden, serialized)


def load_tests(loader, standard_tests, pattern):
    try:
        module = importlib.import_module(DEPENDENCIES_MODULE)
    except ImportError:
        module = None
    if module is None or getattr(module, "require_b2b_action", None) is None:
        return loader.loadTestsFromTestCase(_PreTask2DependencyBehaviorProofs)
    return loader.loadTestsFromTestCase(HumanReviewB2B2AuthorizationTests)


if __name__ == "__main__":
    unittest.main()
