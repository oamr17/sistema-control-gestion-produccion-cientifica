from __future__ import annotations

import inspect
from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID

from app.models.entities import User
from app.models.enums import UserRole
from app.models.human_review_core import ReviewItem
from app.models.human_review_enums import B2BCapability
from app.schemas.human_review import (
    DecisionReversalPayloadV1,
    ReviewProjectionSnapshotV1,
)
from app.schemas.human_review_api import (
    ApplyDecisionResponse,
    ReviewCaseDetail,
    RevertRequest,
)
from app.schemas.human_review_operations import OptimisticLockError
from app.services import human_review_commands as commands
from app.services.human_review_state import HumanReviewDomainError


CASE_ID = UUID("51000000-0000-0000-0000-000000000001")
CURRENT_ID = UUID("51000000-0000-0000-0000-000000000002")
TARGET_ID = UUID("51000000-0000-0000-0000-000000000003")
RESTORE_ID = UUID("51000000-0000-0000-0000-000000000004")
REVERSAL_ID = UUID("51000000-0000-0000-0000-000000000005")
CORRELATION_ID = UUID("52000000-0000-0000-0000-000000000001")


class _ScalarRows:
    def __init__(self, first: object | None = None) -> None:
        self._first = first

    def first(self) -> object | None:
        return self._first

    def __iter__(self):
        return iter(())


def _actor(*, active: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        id=17,
        email="manager@example.invalid",
        is_active=active,
        role=UserRole.FACULTY_ADMIN,
        faculty_id=1,
        career_id=None,
    )


def _item() -> ReviewItem:
    return ReviewItem(
        id=CASE_ID,
        case_type="product",
        stable_target_key=f"b2b:v1:product:{'a' * 64}",
        target_table="scientific_productions",
        target_pk=41,
        document_key="dropbox_path:/internal/never-return.pdf",
        source_revision="revision-1",
        source_page=1,
        source_section="fixture",
        row_or_block_id="row-41",
        field_path="title",
        raw_value_sha256="b" * 64,
        period_id=2026,
        relationship_key=None,
        case_status="resolved",
        scientific_status="validated",
        current_decision_id=CURRENT_ID,
        version=3,
        possible_kpi_impact=True,
        scope_faculty_id=1,
        scope_career_id=10,
        scope_resolution_reason=None,
    )


def _request(
    *,
    expected_version: int = 3,
    expected_current_decision_id: UUID = CURRENT_ID,
    decision_id_to_revert: UUID = TARGET_ID,
) -> RevertRequest:
    return RevertRequest(
        expected_version=expected_version,
        expected_current_decision_id=expected_current_decision_id,
        decision_id_to_revert=decision_id_to_revert,
        reason="The prior scientific decision must be restored",
        correlation_id=CORRELATION_ID,
    )


def _reversal_payload() -> DecisionReversalPayloadV1:
    return DecisionReversalPayloadV1(
        kind="decision_reversal",
        schema_version=1,
        decision_id_to_revert=TARGET_ID,
        restore_decision_id=RESTORE_ID,
        projection_before=ReviewProjectionSnapshotV1(
            schema_version=1,
            case_status="resolved",
            scientific_status="validated",
            current_decision_id=CURRENT_ID,
            identity=None,
            overrides=(),
        ),
        projection_after=ReviewProjectionSnapshotV1(
            schema_version=1,
            case_status="conflicted",
            scientific_status="pending",
            current_decision_id=RESTORE_ID,
            identity=None,
            overrides=(),
        ),
    )


def _db_for(item: ReviewItem | None, actor: SimpleNamespace | None = None) -> MagicMock:
    db = MagicMock()
    db.no_autoflush = nullcontext()

    def get(model, identity):
        if model is User:
            return actor
        if model is ReviewItem and identity == CASE_ID:
            return item
        return None

    db.get.side_effect = get
    db.scalars.return_value = _ScalarRows(item)
    return db


class HumanReviewReversalCommandTests(unittest.TestCase):
    def test_public_signature_is_exact(self) -> None:
        self.assertEqual(
            tuple(inspect.signature(commands.revert_case).parameters),
            ("db", "actor", "review_item_id", "request"),
        )

    def test_valid_historical_target_builds_exact_command_and_returns_typed_response(
        self,
    ) -> None:
        item = _item()
        actor = _actor()
        db = _db_for(item, actor)
        request = _request()
        public_case = ReviewCaseDetail.model_construct(id=CASE_ID)
        captured = {}

        def append(_db, command):
            captured["command"] = command
            item.version = 4
            item.current_decision_id = REVERSAL_ID
            item.case_status = "conflicted"
            item.scientific_status = "pending"
            return SimpleNamespace(
                id=REVERSAL_ID,
                payload=_reversal_payload().model_dump(mode="json"),
            )

        with (
            patch.object(
                commands,
                "authorize_b2b_action",
                return_value=B2BCapability.RESEARCH_MANAGER,
            ),
            patch.object(
                commands,
                "append_functional_reversal",
                side_effect=append,
            ),
            patch.object(
                commands.HumanReviewQueryService,
                "get_case",
                return_value=public_case,
            ),
        ):
            response = commands.revert_case(db, actor, CASE_ID, request)

        self.assertIsInstance(response, ApplyDecisionResponse)
        self.assertEqual(response.decision_id, REVERSAL_ID)
        self.assertEqual(response.correlation_id, CORRELATION_ID)
        self.assertEqual(
            response.kpi_effect.model_dump(mode="json"),
            {"affected": []},
        )
        command = captured["command"]
        self.assertEqual(command.review_item_id, CASE_ID)
        self.assertEqual(command.decision_id_to_revert, TARGET_ID)
        self.assertEqual(command.expected_case_version, 3)
        self.assertEqual(command.actor_user_id, actor.id)
        self.assertEqual(command.reason, request.reason)
        self.assertEqual(command.correlation_id, CORRELATION_ID)
        self.assertIsNone(command.request_id)
        db.commit.assert_called_once()
        db.rollback.assert_not_called()

    def test_missing_case_is_public_not_found_and_has_no_partial_write(self) -> None:
        actor = _actor()
        db = _db_for(None, actor)
        with patch.object(
            commands,
            "authorize_b2b_action",
            return_value=B2BCapability.RESEARCH_MANAGER,
        ):
            with self.assertRaises(HumanReviewDomainError) as raised:
                commands.revert_case(db, actor, CASE_ID, _request())
        self.assertEqual(raised.exception.code, "REVIEW_CASE_NOT_FOUND")
        self.assertEqual(raised.exception.correlation_id, CORRELATION_ID)
        db.commit.assert_not_called()
        db.rollback.assert_called_once()

    def test_stale_version_or_current_head_is_a_conflict(self) -> None:
        rows = (
            _request(expected_version=2),
            _request(
                expected_current_decision_id=UUID(
                    "51000000-0000-0000-0000-000000000099"
                )
            ),
        )
        for request in rows:
            with self.subTest(request=request):
                item = _item()
                actor = _actor()
                db = _db_for(item, actor)
                with (
                    patch.object(
                        commands,
                        "authorize_b2b_action",
                        return_value=B2BCapability.RESEARCH_MANAGER,
                    ),
                    patch.object(
                        commands,
                        "append_functional_reversal",
                    ) as append,
                ):
                    with self.assertRaises(HumanReviewDomainError) as raised:
                        commands.revert_case(db, actor, CASE_ID, request)
                self.assertEqual(
                    raised.exception.code,
                    "REVIEW_CASE_VERSION_CONFLICT",
                )
                self.assertEqual(
                    raised.exception.correlation_id,
                    CORRELATION_ID,
                )
                append.assert_not_called()
                db.commit.assert_not_called()
                db.rollback.assert_called_once()

    def test_domain_cas_is_translated_to_version_conflict(self) -> None:
        item = _item()
        actor = _actor()
        db = _db_for(item, actor)
        with (
            patch.object(
                commands,
                "authorize_b2b_action",
                return_value=B2BCapability.RESEARCH_MANAGER,
            ),
            patch.object(
                commands,
                "append_functional_reversal",
                side_effect=OptimisticLockError("concurrent winner"),
            ),
        ):
            with self.assertRaises(HumanReviewDomainError) as raised:
                commands.revert_case(db, actor, CASE_ID, _request())
        self.assertEqual(
            raised.exception.code,
            "REVIEW_CASE_VERSION_CONFLICT",
        )
        self.assertEqual(raised.exception.correlation_id, CORRELATION_ID)
        db.commit.assert_not_called()
        db.rollback.assert_called()

    def test_invalid_reversal_target_is_sanitized_as_incompatible(self) -> None:
        messages = (
            "decision to revert does not exist",
            "decision to revert belongs to another case",
            "decision to revert must be approved and projection-locked",
            "decision to revert has no complete restorable snapshot pair",
            "restore decision projection does not match projection_before",
            f"decision {TARGET_ID} has an invalid typed payload",
        )
        for message in messages:
            with self.subTest(message=message):
                item = _item()
                actor = _actor()
                db = _db_for(item, actor)
                with (
                    patch.object(
                        commands,
                        "authorize_b2b_action",
                        return_value=B2BCapability.RESEARCH_MANAGER,
                    ),
                    patch.object(
                        commands,
                        "append_functional_reversal",
                        side_effect=ValueError(message),
                    ),
                ):
                    with self.assertRaises(HumanReviewDomainError) as raised:
                        commands.revert_case(db, actor, CASE_ID, _request())
                self.assertEqual(
                    raised.exception.code,
                    "INCOMPATIBLE_DECISION",
                )
                self.assertEqual(
                    str(raised.exception),
                    "Decision cannot be reversed",
                )
                self.assertEqual(
                    raised.exception.correlation_id,
                    CORRELATION_ID,
                )
                db.commit.assert_not_called()
                db.rollback.assert_called()

    def test_corrupt_current_head_is_a_sanitized_internal_error(self) -> None:
        item = _item()
        actor = _actor()
        db = _db_for(item, actor)
        with (
            patch.object(
                commands,
                "authorize_b2b_action",
                return_value=B2BCapability.RESEARCH_MANAGER,
            ),
            patch.object(
                commands,
                "append_functional_reversal",
                side_effect=ValueError(
                    "review item current decision is invalid"
                ),
            ),
        ):
            with self.assertRaises(HumanReviewDomainError) as raised:
                commands.revert_case(db, actor, CASE_ID, _request())
        self.assertEqual(
            raised.exception.code,
            "HUMAN_REVIEW_INTERNAL_ERROR",
        )
        self.assertEqual(
            str(raised.exception),
            "Request could not be completed",
        )
        self.assertEqual(raised.exception.correlation_id, CORRELATION_ID)
        db.commit.assert_not_called()
        db.rollback.assert_called()

    def test_unexpected_domain_value_error_defaults_to_internal(self) -> None:
        item = _item()
        actor = _actor()
        db = _db_for(item, actor)
        with (
            patch.object(
                commands,
                "authorize_b2b_action",
                return_value=B2BCapability.RESEARCH_MANAGER,
            ),
            patch.object(
                commands,
                "append_functional_reversal",
                side_effect=ValueError(
                    "unexpected materialization invariant failure"
                ),
            ),
        ):
            with self.assertRaises(HumanReviewDomainError) as raised:
                commands.revert_case(db, actor, CASE_ID, _request())
        self.assertEqual(
            raised.exception.code,
            "HUMAN_REVIEW_INTERNAL_ERROR",
        )
        self.assertEqual(
            str(raised.exception),
            "Request could not be completed",
        )
        self.assertEqual(raised.exception.correlation_id, CORRELATION_ID)
        db.commit.assert_not_called()
        db.rollback.assert_called()

    def test_malformed_typed_payload_on_current_head_is_internal(self) -> None:
        item = _item()
        actor = _actor()
        db = _db_for(item, actor)
        with (
            patch.object(
                commands,
                "authorize_b2b_action",
                return_value=B2BCapability.RESEARCH_MANAGER,
            ),
            patch.object(
                commands,
                "append_functional_reversal",
                side_effect=ValueError(
                    f"decision {CURRENT_ID} has an invalid typed payload"
                ),
            ),
        ):
            with self.assertRaises(HumanReviewDomainError) as raised:
                commands.revert_case(db, actor, CASE_ID, _request())
        self.assertEqual(
            raised.exception.code,
            "HUMAN_REVIEW_INTERNAL_ERROR",
        )
        self.assertEqual(
            str(raised.exception),
            "Request could not be completed",
        )
        self.assertEqual(raised.exception.correlation_id, CORRELATION_ID)
        db.commit.assert_not_called()
        db.rollback.assert_called()

    def test_inactive_or_unauthorized_actor_is_capability_error(self) -> None:
        rows = (
            (_actor(active=False), B2BCapability.RESEARCH_MANAGER),
            (_actor(), B2BCapability.SYSTEM_ADMIN),
        )
        for actor, capability in rows:
            with self.subTest(actor=actor, capability=capability):
                item = _item()
                db = _db_for(item, actor)
                with (
                    patch.object(
                        commands,
                        "authorize_b2b_action",
                        return_value=capability,
                    ),
                    patch.object(
                        commands,
                        "append_functional_reversal",
                    ) as append,
                ):
                    with self.assertRaises(HumanReviewDomainError) as raised:
                        commands.revert_case(db, actor, CASE_ID, _request())
                self.assertEqual(
                    raised.exception.code,
                    "B2B_CAPABILITY_REQUIRED",
                )
                self.assertEqual(
                    raised.exception.correlation_id,
                    CORRELATION_ID,
                )
                append.assert_not_called()
                db.commit.assert_not_called()
                db.rollback.assert_called_once()

    def test_missing_persisted_actor_is_capability_error(self) -> None:
        item = _item()
        actor = _actor()
        db = _db_for(item, None)
        with (
            patch.object(
                commands,
                "authorize_b2b_action",
                return_value=B2BCapability.RESEARCH_MANAGER,
            ),
            patch.object(
                commands,
                "append_functional_reversal",
            ) as append,
        ):
            with self.assertRaises(HumanReviewDomainError) as raised:
                commands.revert_case(db, actor, CASE_ID, _request())
        self.assertEqual(
            raised.exception.code,
            "B2B_CAPABILITY_REQUIRED",
        )
        self.assertEqual(raised.exception.correlation_id, CORRELATION_ID)
        append.assert_not_called()
        db.commit.assert_not_called()
        db.rollback.assert_called_once()

    def test_domain_permission_error_is_capability_error(self) -> None:
        item = _item()
        actor = _actor()
        db = _db_for(item, actor)
        with (
            patch.object(
                commands,
                "authorize_b2b_action",
                return_value=B2BCapability.RESEARCH_MANAGER,
            ),
            patch.object(
                commands,
                "append_functional_reversal",
                side_effect=PermissionError("internal authorization detail"),
            ),
        ):
            with self.assertRaises(HumanReviewDomainError) as raised:
                commands.revert_case(db, actor, CASE_ID, _request())
        self.assertEqual(raised.exception.code, "B2B_CAPABILITY_REQUIRED")
        self.assertEqual(raised.exception.correlation_id, CORRELATION_ID)
        db.commit.assert_not_called()
        db.rollback.assert_called()

    def test_response_failure_rolls_back_reversal_before_commit(self) -> None:
        item = _item()
        actor = _actor()
        db = _db_for(item, actor)

        def append(_db, _command):
            return SimpleNamespace(
                id=REVERSAL_ID,
                payload=_reversal_payload().model_dump(mode="json"),
            )

        with (
            patch.object(
                commands,
                "authorize_b2b_action",
                return_value=B2BCapability.RESEARCH_MANAGER,
            ),
            patch.object(
                commands,
                "append_functional_reversal",
                side_effect=append,
            ),
            patch.object(
                commands.HumanReviewQueryService,
                "get_case",
                side_effect=RuntimeError("injected response failure"),
            ),
        ):
            with self.assertRaises(HumanReviewDomainError) as raised:
                commands.revert_case(db, actor, CASE_ID, _request())
        self.assertEqual(
            raised.exception.code,
            "HUMAN_REVIEW_INTERNAL_ERROR",
        )
        self.assertEqual(raised.exception.correlation_id, CORRELATION_ID)
        db.commit.assert_not_called()
        db.rollback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
