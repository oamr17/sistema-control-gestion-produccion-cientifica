from __future__ import annotations

import inspect
from contextlib import nullcontext
from hashlib import sha256
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch
from uuid import UUID

from app.models.entities import PersonRole, ScientificProduction, User
from app.models.enums import UserRole
from app.models.human_review_core import ReviewItem
from app.models.human_review_enums import B2BCapability, ReviewDecisionType
from app.models.human_review_core import ReviewDecision
from app.schemas.human_review import (
    IdentityDecisionPayloadV1,
    IdentityProjectionSnapshotV1,
    ProjectionAliasSnapshotV1,
    ReviewProjectionSnapshotV1,
)
from app.schemas.human_review_api import ApplyDecisionRequest, DiscardRequest
from app.services import human_review_commands as commands
from app.services.human_review_state import (
    HumanReviewDomainError,
    IncompatibleDecisionError,
)


DECISION_ID = UUID("10000000-0000-0000-0000-000000000001")
CORRELATION_ID = UUID("20000000-0000-0000-0000-000000000001")


class _ScalarRows:
    def __init__(self, first: object | None = None) -> None:
        self._first = first

    def first(self) -> object | None:
        return self._first

    def __iter__(self):
        return iter(())


def _item(case_type: str, target_table: str) -> ReviewItem:
    return ReviewItem(
        id=UUID("30000000-0000-0000-0000-000000000001"),
        case_type=case_type,
        stable_target_key=f"b2b:v1:{case_type}:{'a' * 64}",
        target_table=target_table,
        target_pk=41,
        document_key="document:fixture.pdf",
        source_revision="revision-1",
        source_page=1,
        source_section="fixture",
        row_or_block_id="row-41",
        field_path="case",
        raw_value_sha256="b" * 64,
        period_id=2026,
        relationship_key="relationship:41",
        case_status="pending",
        scientific_status="pending",
        current_decision_id=None,
        version=1,
        scope_faculty_id=1,
        scope_career_id=10,
        scope_resolution_reason=None,
    )


def _before() -> ReviewProjectionSnapshotV1:
    return ReviewProjectionSnapshotV1(
        schema_version=1,
        case_status="pending",
        scientific_status="pending",
        current_decision_id=None,
        identity=None,
        overrides=(),
    )


def _request(payload: dict[str, object], action: str = "approve") -> ApplyDecisionRequest:
    return ApplyDecisionRequest(
        expected_version=1,
        expected_current_decision_id=None,
        action=action,
        scope="record",
        payload=payload,
        reason="Reviewed against the source fixture",
        correlation_id=CORRELATION_ID,
    )


class HumanReviewCommandContractTests(unittest.TestCase):
    def test_command_signatures_are_closed(self) -> None:
        self.assertEqual(
            tuple(inspect.signature(commands.apply_decision).parameters),
            ("db", "actor", "review_item_id", "request"),
        )
        self.assertEqual(
            tuple(inspect.signature(commands.discard_case).parameters),
            ("db", "actor", "review_item_id", "request"),
        )

    def test_public_apply_generates_an_opaque_person_identity_key_only_when_absent(self) -> None:
        generated_request = {
            "expected_version": 1,
            "expected_current_decision_id": None,
            "action": "approve",
            "scope": "record",
            "payload": {
                "case_type": "person_identity",
                "canonical_identity_key": None,
                "canonical_name": "Ada Lovelace",
                "aliases": (),
                "scientific_status": "validated",
            },
            "reason": "Reviewed against the source fixture",
            "correlation_id": CORRELATION_ID,
        }
        supplied_request = _request({
            "case_type": "person_identity",
            "canonical_identity_key": "person:already-public",
            "canonical_name": "Ada Lovelace",
            "aliases": (),
            "scientific_status": "validated",
        })
        for request, expected in ((generated_request, None), (supplied_request, "person:already-public")):
            db = Mock()
            response = SimpleNamespace()
            with self.subTest(supplied=expected is not None), patch.object(
                commands, "_prepare_command", return_value=response
            ) as prepare:
                self.assertIs(commands.apply_decision(db, Mock(), _item("person_identity", "person_roles").id, request), response)
            prepared = prepare.call_args.args[3]
            key = prepared.payload.canonical_identity_key
            if expected is not None:
                self.assertEqual(key, expected)
            else:
                self.assertRegex(key, r"^human:identity:[0-9a-f]{8}-[0-9a-f-]{27}$")
                self.assertNotIn("Ada", key)
            db.commit.assert_called_once()

    def test_public_apply_generates_an_opaque_external_identity_key_only_when_absent(self) -> None:
        generated_request = {
            "expected_version": 1,
            "expected_current_decision_id": None,
            "action": "correct",
            "scope": "global_identity",
            "payload": {
                "case_type": "external_identity",
                "external_identity_key": None,
                "external_institution": "Universidad de Murcia",
                "scientific_status": "validated",
            },
            "reason": "Reviewed against the source fixture",
            "correlation_id": CORRELATION_ID,
        }
        supplied_request = _request({
            "case_type": "external_identity",
            "external_identity_key": "external:already-public",
            "external_institution": "Universidad de Murcia",
            "scientific_status": "validated",
        }, "correct")
        for request, expected in ((generated_request, None), (supplied_request, "external:already-public")):
            db = Mock()
            response = SimpleNamespace()
            with self.subTest(supplied=expected is not None), patch.object(
                commands, "_prepare_command", return_value=response
            ) as prepare:
                self.assertIs(commands.apply_decision(db, Mock(), _item("external_identity", "external_researchers").id, request), response)
            prepared = prepare.call_args.args[3]
            key = prepared.payload.external_identity_key
            if expected is not None:
                self.assertEqual(key, expected)
            else:
                self.assertRegex(key, r"^human:external:[0-9a-f]{8}-[0-9a-f-]{27}$")
                self.assertNotIn("Murcia", key)
            db.commit.assert_called_once()

    def test_apply_plan_materializes_the_closed_case_action_matrix(self) -> None:
        rows = (
            (
                _item("person_identity", "person_roles"),
                _request({
                    "case_type": "person_identity",
                    "canonical_identity_key": "person:41",
                    "canonical_name": "Ada Lovelace",
                    "aliases": ("A. Lovelace",),
                    "scientific_status": "validated",
                }),
                ReviewDecisionType.VALIDATED,
                "identity",
                {"canonical_identity_key", "canonical_name", "scientific_status"},
                "internal_person",
            ),
            (
                _item("author_identity", "scientific_production_authors"),
                _request({
                    "case_type": "author_identity",
                    "canonical_identity_key": "author:41",
                    "canonical_name": "Grace Hopper",
                    "aliases": (),
                    "scientific_status": "validated",
                }, "correct"),
                ReviewDecisionType.CORRECTED,
                "identity",
                {"author_identity_key", "canonical_name", "scientific_status"},
                "internal_person",
            ),
            (
                _item("product", "scientific_productions"),
                _request({
                    "case_type": "product",
                    "product_title": "A corrected title",
                    "scientific_status": "validated",
                }),
                ReviewDecisionType.VALIDATED,
                "field_override",
                {"product_title", "scientific_status"},
                None,
            ),
            (
                _item("project_director_relation", "research_entities"),
                _request({
                    "case_type": "project_director_relation",
                    "project_director_identity_key": "person:director",
                    "relationship_status": "linked",
                    "scientific_status": "validated",
                }, "link"),
                ReviewDecisionType.LINKED,
                "field_override",
                {
                    "project_director_identity_key",
                    "project_director_relationship_status",
                    "scientific_status",
                },
                None,
            ),
            (
                _item("external_identity", "external_researchers"),
                _request({
                    "case_type": "external_identity",
                    "external_identity_key": "external:41",
                    "external_institution": "Analytical Engine Institute",
                    "scientific_status": "validated",
                }, "correct"),
                ReviewDecisionType.CORRECTED,
                "identity",
                {"external_identity_key", "external_institution", "scientific_status"},
                "external_person",
            ),
        )
        for item, request, decision_type, kind, fields, identity_type in rows:
            db = Mock()
            db.scalar.return_value = None
            db.get.return_value = SimpleNamespace(
                full_name="External Researcher",
                normalized_name="external researcher",
            )
            with self.subTest(case_type=item.case_type, action=request.action):
                plan = commands._build_apply_plan(
                    db, item, request, DECISION_ID, _before()
                )
                self.assertEqual(plan.decision_type, decision_type)
                self.assertEqual(plan.payload.kind, kind)
                self.assertEqual(plan.projection_after.case_status.value, "resolved")
                self.assertEqual(plan.projection_after.scientific_status.value, "validated")
                self.assertEqual(plan.projection_after.current_decision_id, DECISION_ID)
                self.assertEqual(
                    {override.field_path.value for override in plan.projection_after.overrides},
                    fields,
                )
                if identity_type is not None:
                    self.assertEqual(
                        plan.projection_after.identity.identity_type.value,
                        identity_type,
                    )
                if item.case_type == "person_identity":
                    self.assertEqual(
                        tuple(
                            alias.alias_original
                            for alias in plan.projection_after.identity.aliases
                        ),
                        ("A. Lovelace",),
                    )

    def test_apply_plan_supports_every_link_resolution(self) -> None:
        item = _item("person_identity", "person_roles")
        expected = {
            "linked": ReviewDecisionType.LINKED,
            "maintained_separate": ReviewDecisionType.MAINTAINED_SEPARATE,
            "separated": ReviewDecisionType.SEPARATED,
        }
        for resolution, decision_type in expected.items():
            request = _request({
                "case_type": "person_identity",
                "canonical_identity_key": f"person:{resolution}",
                "canonical_name": "Resolved Person",
                "aliases": (),
                "scientific_status": "validated",
                "resolution": resolution,
            }, "link")
            with self.subTest(resolution=resolution):
                plan = commands._build_apply_plan(
                    Mock(), item, request, DECISION_ID, _before()
                )
                self.assertEqual(plan.decision_type, decision_type)

    def test_possible_duplicate_derives_all_payloads_from_two_locked_projections(self) -> None:
        item = _item("possible_duplicate", "person_roles")
        current_identity = IdentityProjectionSnapshotV1(
            schema_version=1,
            canonical_identity_key="person:source",
            canonical_name="Source Person",
            identity_type="internal_person",
            aliases=(),
        )
        before = _before().model_copy(update={"identity": current_identity})
        counterpart_id = UUID("40000000-0000-0000-0000-000000000001")
        counterpart_decision_id = UUID("50000000-0000-0000-0000-000000000001")
        counterpart_identity = IdentityProjectionSnapshotV1(
            schema_version=1,
            canonical_identity_key="person:counterpart",
            canonical_name="Counterpart Person",
            identity_type="internal_person",
            aliases=(),
        )
        counterpart_after = ReviewProjectionSnapshotV1(
            schema_version=1,
            case_status="resolved",
            scientific_status="validated",
            current_decision_id=counterpart_decision_id,
            identity=counterpart_identity,
            overrides=(),
        )
        counterpart = _item("person_identity", "person_roles")
        counterpart.id = counterpart_id
        counterpart.stable_target_key = f"b2b:v1:person_identity:{'c' * 64}"
        counterpart.case_status = "resolved"
        counterpart.scientific_status = "validated"
        counterpart.current_decision_id = counterpart_decision_id
        counterpart_decision = ReviewDecision(
            id=counterpart_decision_id,
            review_item_id=counterpart_id,
            sequence=1,
            decision_type="validated",
            decision_lifecycle="approved",
            scope="record",
            payload_schema="review.decision.v1",
            payload_version=1,
            payload=IdentityDecisionPayloadV1(
                kind="identity",
                schema_version=1,
                canonical_identity_key="person:counterpart",
                canonical_name="Counterpart Person",
                identity_type="internal_person",
                projection_before=_before(),
                projection_after=counterpart_after,
            ).model_dump(mode="json"),
            reason="fixture",
            actor_type="legacy",
            actor_user_id=None,
            actor_identifier="fixture",
            actor_capability=None,
            expected_case_version=1,
            locks_projection=True,
        )
        expected_kind = {
            "merged": "identity_merge",
            "maintained_separate": "maintain_separate",
            "separated": "identity_separation",
        }
        for resolution, kind in expected_kind.items():
            request = _request({
                "case_type": "possible_duplicate",
                "counterpart_ref": {
                    "target_type": "person_roles",
                    "target_id": 42,
                },
                "resolution": resolution,
                "scientific_status": "validated",
            }, "link")
            db = Mock()
            db.get.return_value = counterpart_decision
            with self.subTest(resolution=resolution), patch.object(
                commands,
                "resolve_duplicate_counterpart",
                return_value=counterpart,
                create=True,
            ) as resolve_counterpart:
                plan = commands._build_apply_plan(db, item, request, DECISION_ID, before)
                resolve_counterpart.assert_called_once_with(
                    db,
                    item,
                    request.payload.counterpart_ref,
                    CORRELATION_ID,
                )
                self.assertEqual(plan.payload.kind, kind)
                self.assertEqual(
                    plan.projection_after.current_decision_id, DECISION_ID
                )

    def test_apply_rejects_a_nonvalidated_scientific_status(self) -> None:
        item = _item("product", "scientific_productions")
        request = _request({
            "case_type": "product",
            "product_title": "Title",
            "scientific_status": "discarded",
        })
        with self.assertRaises(IncompatibleDecisionError) as raised:
            commands._build_apply_plan(
                Mock(), item, request, DECISION_ID, _before()
            )
        self.assertEqual(raised.exception.correlation_id, CORRELATION_ID)

    def test_discard_is_a_resolved_locked_scientific_override_not_a_delete(self) -> None:
        item = _item("product", "scientific_productions")
        request = DiscardRequest(
            expected_version=1,
            expected_current_decision_id=None,
            reason="The record is outside the scientific corpus",
            correlation_id=CORRELATION_ID,
        )
        plan = commands._build_discard_plan(
            item, request, DECISION_ID, _before()
        )
        self.assertEqual(plan.decision_type, ReviewDecisionType.DISCARDED)
        self.assertEqual(plan.payload.kind, "field_override")
        self.assertEqual(plan.projection_after.case_status.value, "resolved")
        self.assertEqual(plan.projection_after.scientific_status.value, "discarded")
        self.assertEqual(
            {override.field_path.value for override in plan.projection_after.overrides},
            {"scientific_status"},
        )

    def test_kpi_effect_contains_only_actual_typed_metric_deltas(self) -> None:
        effect = commands._diff_effective_kpi(
            {
                "scientific_output_kpi_eligible": 2,
                "scientific_output_pending_review": 1,
                "reports_received": 7,
            },
            {
                "scientific_output_kpi_eligible": 3,
                "scientific_output_pending_review": 0,
                "reports_received": 7,
            },
        )
        self.assertEqual(
            effect.model_dump(mode="json"),
            {
                "affected": [
                    {
                        "metric": "scientific_output_kpi_eligible",
                        "before": 2,
                        "after": 3,
                        "delta": 1,
                    },
                    {
                        "metric": "scientific_output_pending_review",
                        "before": 1,
                        "after": 0,
                        "delta": -1,
                    },
                ]
            },
        )
        self.assertEqual(
            commands._diff_effective_kpi(None, None).affected,
            (),
        )

    def test_response_validation_failure_occurs_before_commit_and_rolls_back(self) -> None:
        item = _item("product", "scientific_productions")
        actor = SimpleNamespace(
            id=7,
            email="manager@example.invalid",
            is_active=True,
            role=UserRole.FACULTY_ADMIN,
            faculty_id=1,
            career_id=None,
        )
        db = MagicMock()
        db.no_autoflush = nullcontext()

        def get(model, identity):
            if model is User:
                return actor
            if model is ReviewItem:
                return item
            return None

        db.get.side_effect = get
        db.scalars.return_value = _ScalarRows(item)
        db.scalar.return_value = 1

        def cas(_db, _item_id, decision_id, expected, *_statuses):
            return SimpleNamespace(
                id=item.id,
                version=expected + 1,
                current_decision_id=decision_id,
            )

        with (
            patch.object(
                commands,
                "authorize_b2b_action",
                return_value=B2BCapability.RESEARCH_MANAGER,
            ),
            patch.object(commands, "set_current_decision", side_effect=cas),
            patch.object(commands, "_materialize_identity"),
            patch.object(commands, "_materialize_overrides"),
            patch.object(
                commands,
                "append_audit_event_at_current_head",
                side_effect=lambda _db, command: SimpleNamespace(
                    review_item_id=command.review_item_id
                ),
            ),
            patch.object(
                commands.HumanReviewQueryService,
                "get_case",
                side_effect=RuntimeError("injected response failure"),
            ),
        ):
            with self.assertRaises(HumanReviewDomainError) as raised:
                commands.apply_decision(
                    db,
                    actor,
                    item.id,
                    _request({
                        "case_type": "product",
                        "product_title": "Title",
                        "scientific_status": "validated",
                    }),
                )
        self.assertEqual(raised.exception.code, "HUMAN_REVIEW_INTERNAL_ERROR")
        db.commit.assert_not_called()
        db.rollback.assert_called_once()

    def test_matrix_incompatibility_precedes_stale_version(self) -> None:
        item = _item("product", "scientific_productions")
        item.version = 2
        actor = SimpleNamespace(
            id=7,
            email="manager@example.invalid",
            is_active=True,
            role=UserRole.FACULTY_ADMIN,
            faculty_id=1,
            career_id=None,
        )
        db = MagicMock()
        db.no_autoflush = nullcontext()
        db.get.side_effect = lambda model, identity: (
            actor if model is User else item if model is ReviewItem else None
        )
        db.scalars.return_value = _ScalarRows(item)
        incompatible = ApplyDecisionRequest(
            expected_version=1,
            expected_current_decision_id=None,
            action="link",
            scope="record",
            payload={
                "case_type": "product",
                "product_title": "Title",
                "scientific_status": "validated",
            },
            reason="fixture",
            correlation_id=CORRELATION_ID,
        )
        with patch.object(
            commands,
            "authorize_b2b_action",
            return_value=B2BCapability.RESEARCH_MANAGER,
        ):
            with self.assertRaises(HumanReviewDomainError) as raised:
                commands.apply_decision(db, actor, item.id, incompatible)
        self.assertEqual(raised.exception.code, "INCOMPATIBLE_DECISION")
        db.commit.assert_not_called()

    def test_reasonless_unchanged_approve_is_proven_from_typed_target(self) -> None:
        rows = (
            (
                _item("product", "scientific_productions"),
                ScientificProduction,
                SimpleNamespace(normalized_title="Same title", title="Same title"),
                {
                    "case_type": "product",
                    "product_title": "Same title",
                    "scientific_status": "validated",
                },
            ),
            (
                _item("person_identity", "person_roles"),
                PersonRole,
                SimpleNamespace(normalized_name="Same Person", canonical_name="Same Person"),
                {
                    "case_type": "person_identity",
                    "canonical_identity_key": "person:same",
                    "canonical_name": "Same Person",
                    "aliases": (),
                    "scientific_status": "validated",
                },
            ),
        )
        for item, target_model, target, payload in rows:
            request = ApplyDecisionRequest(
                expected_version=1,
                expected_current_decision_id=None,
                action="approve",
                scope="record",
                payload=payload,
                reason=None,
                correlation_id=CORRELATION_ID,
            )
            db = Mock()
            db.get.side_effect = lambda model, identity, tm=target_model, t=target: (
                t if model is tm else None
            )
            db.scalar.return_value = None
            with self.subTest(case_type=item.case_type):
                plan = commands._build_apply_plan(
                    db, item, request, DECISION_ID, _before()
                )
                self.assertEqual(plan.decision_type, ReviewDecisionType.VALIDATED)

    def test_persistence_text_limits_fail_closed_without_truncation(self) -> None:
        base = {
            "case_type": "person_identity",
            "canonical_identity_key": "person:limits",
            "canonical_name": "N" * 220,
            "aliases": ("A" * 320,),
            "scientific_status": "validated",
        }
        accepted = _request(base)
        commands._build_apply_plan(
            Mock(), _item("person_identity", "person_roles"),
            accepted, DECISION_ID, _before()
        )
        invalid_payloads = (
            base | {"canonical_name": "N" * 221},
            base | {"aliases": ("A" * 321,)},
            base | {"aliases": ("İ" * 161,)},
            base | {"aliases": ("Ada-Lovelace", "ada lovelace")},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(HumanReviewDomainError) as raised:
                    commands._build_apply_plan(
                        Mock(),
                        _item("person_identity", "person_roles"),
                        _request(payload),
                        DECISION_ID,
                        _before(),
                    )
                self.assertEqual(
                    raised.exception.code, "INVALID_COMMAND_PAYLOAD"
                )

    def test_existing_identity_key_must_match_case_identity_type(self) -> None:
        rows = (
            (
                _item("person_identity", "person_roles"),
                {
                    "case_type": "person_identity",
                    "canonical_identity_key": "identity:wrong-person-type",
                    "canonical_name": "Internal Person",
                    "aliases": (),
                    "scientific_status": "validated",
                },
                "external_person",
            ),
            (
                _item("external_identity", "external_researchers"),
                {
                    "case_type": "external_identity",
                    "external_identity_key": "identity:wrong-external-type",
                    "external_institution": "Institute",
                    "scientific_status": "validated",
                },
                "internal_person",
            ),
        )
        for item, payload, existing_type in rows:
            db = Mock()
            db.scalar.return_value = SimpleNamespace(
                canonical_identity_key=(
                    payload.get("canonical_identity_key")
                    or payload.get("external_identity_key")
                ),
                identity_type=existing_type,
                display_name="Persisted Identity",
            )
            with self.subTest(case_type=item.case_type):
                with self.assertRaises(IncompatibleDecisionError) as raised:
                    commands._build_apply_plan(
                        db,
                        item,
                        _request(payload),
                        DECISION_ID,
                        _before(),
                    )
                self.assertEqual(raised.exception.code, "INCOMPATIBLE_DECISION")
                self.assertEqual(
                    raised.exception.correlation_id, CORRELATION_ID
                )

    def test_corrupt_counterpart_error_preserves_request_correlation(self) -> None:
        item = _item("possible_duplicate", "person_roles")
        current_identity = IdentityProjectionSnapshotV1(
            schema_version=1,
            canonical_identity_key="person:source",
            canonical_name="Source",
            identity_type="internal_person",
            aliases=(),
        )
        before = _before().model_copy(update={"identity": current_identity})
        counterpart = _item("person_identity", "person_roles")
        counterpart.id = UUID("60000000-0000-0000-0000-000000000001")
        counterpart.stable_target_key = f"b2b:v1:person_identity:{'d' * 64}"
        counterpart.case_status = "resolved"
        counterpart.scientific_status = "validated"
        counterpart.current_decision_id = UUID(
            "70000000-0000-0000-0000-000000000001"
        )
        corrupt = SimpleNamespace(
            id=counterpart.current_decision_id,
            review_item_id=counterpart.id,
            decision_lifecycle="approved",
            locks_projection=True,
            payload_schema="review.decision.v1",
            payload_version=1,
            payload={"kind": "not-a-payload"},
        )
        request = _request({
            "case_type": "possible_duplicate",
            "counterpart_ref": {
                "target_type": "person_roles",
                "target_id": 42,
            },
            "resolution": "merged",
            "scientific_status": "validated",
        }, "link")
        db = Mock()
        db.get.return_value = corrupt
        with patch.object(
            commands,
            "resolve_duplicate_counterpart",
            return_value=counterpart,
            create=True,
        ), self.assertRaises(HumanReviewDomainError) as raised:
            commands._build_apply_plan(db, item, request, DECISION_ID, before)
        self.assertEqual(raised.exception.code, "HUMAN_REVIEW_INTERNAL_ERROR")
        self.assertEqual(raised.exception.correlation_id, CORRELATION_ID)

    def test_separation_derives_a_new_current_identity_when_both_share_one(self) -> None:
        shared = IdentityProjectionSnapshotV1(
            schema_version=1,
            canonical_identity_key="person:shared",
            canonical_name="Shared Person",
            identity_type="internal_person",
            aliases=(
                ProjectionAliasSnapshotV1(
                    schema_version=1,
                    alias_original="Shared Alias",
                    alias_normalized="shared alias",
                ),
            ),
        )
        stable_target_key = f"b2b:v1:possible_duplicate:{'e' * 64}"
        separated = commands._separated_current_identity(
            shared, shared, stable_target_key
        )
        self.assertEqual(
            separated.canonical_identity_key,
            "human:separated:"
            + sha256(stable_target_key.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(separated.canonical_name, shared.canonical_name)
        self.assertEqual(separated.identity_type, shared.identity_type)
        self.assertEqual(separated.aliases, ())

        before = _before().model_copy(update={"identity": shared})
        after = ReviewProjectionSnapshotV1(
            schema_version=1,
            case_status="resolved",
            scientific_status="validated",
            current_decision_id=DECISION_ID,
            identity=separated,
            overrides=(),
        )
        materialization_before = commands._identity_materialization_before(
            before, after, ReviewDecisionType.SEPARATED
        )
        self.assertIsNone(materialization_before.identity)
        self.assertEqual(before.identity, shared)


if __name__ == "__main__":
    unittest.main()
