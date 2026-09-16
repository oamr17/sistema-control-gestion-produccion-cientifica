from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

from app.models.entities import ScientificProduction
from app.models.human_review_core import ReviewItem
from app.models.human_review_enums import ReviewCaseStatus, ReviewDecisionType
from app.schemas.human_review import DecisionReversalPayloadV1, ReviewProjectionSnapshotV1
from app.schemas.human_review_api import ApplyDecisionRequest, HumanReviewValidationError
from app.services import human_review_commands as commands
from app.services.human_review_state import assert_case_transition


CORRELATION_ID = UUID("20000000-0000-0000-0000-000000000099")
DECISION_ID = UUID("10000000-0000-0000-0000-000000000099")
PREVIOUS_DECISION_ID = UUID("10000000-0000-0000-0000-000000000098")
REVERTED_DECISION_ID = UUID("10000000-0000-0000-0000-000000000097")


def _product_item() -> ReviewItem:
    return ReviewItem(
        id=UUID("30000000-0000-0000-0000-000000000099"),
        case_type="product",
        stable_target_key=f"b2b:v1:product:{'a' * 64}",
        target_table="scientific_productions",
        target_pk=41,
        scope_faculty_id=1,
        scope_career_id=1,
        document_key="document:product.pdf",
        source_revision="revision-1",
        source_page=1,
        source_section="products",
        row_or_block_id="product:41",
        field_path="product_title",
        raw_value_sha256="b" * 64,
        period_id=2026,
        relationship_key=None,
        case_status="pending",
        scientific_status="pending",
        current_decision_id=None,
        version=1,
    )


def _before(
    *,
    case_status: str = "pending",
    current_decision_id: UUID | None = None,
) -> ReviewProjectionSnapshotV1:
    return ReviewProjectionSnapshotV1(
        schema_version=1,
        case_status=case_status,
        scientific_status="pending",
        current_decision_id=current_decision_id,
        identity=None,
        overrides=(),
    )


def _request(
    title: str,
    *,
    action: str,
    reason: str | None = None,
    expected_version: int = 1,
    expected_current_decision_id: UUID | None = None,
) -> ApplyDecisionRequest:
    return ApplyDecisionRequest(
        expected_version=expected_version,
        expected_current_decision_id=expected_current_decision_id,
        action=action,
        scope="record",
        payload={
            "case_type": "product",
            "product_title": title,
            "scientific_status": "validated",
        },
        reason=reason,
        correlation_id=CORRELATION_ID,
    )


def _approve(title: str, reason: str | None = None) -> ApplyDecisionRequest:
    return _request(title, action="approve", reason=reason)


class ProductApprovalRegressionTests(unittest.TestCase):
    def test_product_approve_accepts_the_same_effective_title_without_reason(self) -> None:
        """Approve compares against the canonical/effective title presented by review UI."""
        item = _product_item()
        target = SimpleNamespace(
            title="Título efectivo mostrado",
            normalized_title="titulo efectivo mostrado",
        )
        db = Mock()
        db.get.side_effect = lambda model, identity: (
            target if model is ScientificProduction and identity == item.target_pk else None
        )

        plan = commands._build_apply_plan(
            db,
            item,
            _approve("Título efectivo mostrado"),
            DECISION_ID,
            _before(),
        )

        self.assertEqual(plan.decision_type, ReviewDecisionType.VALIDATED)
        self.assertEqual(plan.projection_after.case_status.value, "resolved")
        self.assertEqual(plan.projection_after.scientific_status.value, "validated")

    def test_product_approve_changed_effective_title_still_requires_reason(self) -> None:
        item = _product_item()
        target = SimpleNamespace(
            title="Título efectivo mostrado",
            normalized_title="titulo efectivo mostrado",
        )
        db = Mock()
        db.get.side_effect = lambda model, identity: (
            target if model is ScientificProduction and identity == item.target_pk else None
        )

        with self.assertRaises(HumanReviewValidationError) as raised:
            commands._build_apply_plan(
                db,
                item,
                _approve("Un título realmente diferente"),
                DECISION_ID,
                _before(),
            )

        self.assertEqual(raised.exception.details, {"field": "reason"})

    def test_product_correct_materializes_the_edited_title(self) -> None:
        item = _product_item()
        plan = commands._build_apply_plan(
            Mock(),
            item,
            _request(
                "Título corregido por el gestor",
                action="correct",
                reason="Se verificó el título contra la evidencia.",
            ),
            DECISION_ID,
            _before(),
        )

        self.assertEqual(plan.decision_type, ReviewDecisionType.CORRECTED)
        projected = {
            override.field_path.value: override.projected_value.string_value
            for override in plan.projection_after.overrides
        }
        self.assertEqual(projected["product_title"], "Título corregido por el gestor")
        self.assertEqual(projected["scientific_status"], "validated")

    def test_reopened_product_can_be_corrected_and_resolved_again(self) -> None:
        item = _product_item()
        item.case_status = "reopened"
        item.current_decision_id = PREVIOUS_DECISION_ID
        item.version = 2
        request = _request(
            "Título corregido después de reabrir",
            action="correct",
            reason="La revisión reabierta requiere una nueva corrección.",
            expected_version=2,
            expected_current_decision_id=PREVIOUS_DECISION_ID,
        )

        commands._validate_expected_state(
            item,
            request.expected_version,
            request.expected_current_decision_id,
            request.correlation_id,
        )
        assert_case_transition(ReviewCaseStatus.REOPENED, ReviewCaseStatus.RESOLVED)
        plan = commands._build_apply_plan(
            Mock(),
            item,
            request,
            DECISION_ID,
            _before(
                case_status="reopened",
                current_decision_id=PREVIOUS_DECISION_ID,
            ),
        )

        self.assertEqual(plan.decision_type, ReviewDecisionType.CORRECTED)
        self.assertEqual(plan.projection_after.case_status.value, "resolved")
        self.assertEqual(plan.projection_after.current_decision_id, DECISION_ID)

    def test_reopened_reversal_head_restores_its_effective_projection_for_a_new_command(self) -> None:
        """A reversal is the current decision head but its effective snapshot points to the restored head."""
        item = _product_item()
        item.case_status = "reopened"
        item.scientific_status = "pending"
        item.current_decision_id = REVERTED_DECISION_ID
        item.version = 2
        resolved_before_reversal = ReviewProjectionSnapshotV1(
            schema_version=1,
            case_status="resolved",
            scientific_status="validated",
            current_decision_id=PREVIOUS_DECISION_ID,
            identity=None,
            overrides=(),
        )
        restored = _before(case_status="reopened", current_decision_id=None)
        reversal_payload = DecisionReversalPayloadV1(
            kind="decision_reversal",
            schema_version=1,
            decision_id_to_revert=PREVIOUS_DECISION_ID,
            restore_decision_id=None,
            projection_before=resolved_before_reversal,
            projection_after=restored,
        )
        reversal = SimpleNamespace(
            id=REVERTED_DECISION_ID,
            review_item_id=item.id,
            decision_type="reverted",
            decision_lifecycle="approved",
            locks_projection=True,
            payload_schema="review.decision.v1",
            payload_version=1,
            payload=reversal_payload.model_dump(mode="json"),
        )
        db = Mock()
        db.get.return_value = reversal

        projection = commands._projection_from_decision(db, item, CORRELATION_ID)

        self.assertEqual(projection.case_status.value, "reopened")
        self.assertEqual(projection.scientific_status.value, "pending")
        self.assertIsNone(projection.current_decision_id)


if __name__ == "__main__":
    unittest.main()
