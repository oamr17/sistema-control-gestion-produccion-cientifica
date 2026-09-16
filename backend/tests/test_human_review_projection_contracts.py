from __future__ import annotations

import unittest
from uuid import UUID

from pydantic import ValidationError

from app.schemas import human_review as decision_contracts
from app.schemas import human_review_operations as operation_contracts


class HumanReviewProjectionContractTests(unittest.TestCase):
    stable_target_key = f"b2b:v1:person_identity:{'a' * 64}"
    other_stable_target_key = f"b2b:v1:person_identity:{'b' * 64}"
    current_decision_id = UUID("11111111-1111-1111-1111-111111111111")
    reverted_decision_id = UUID("22222222-2222-2222-2222-222222222222")

    def _contract(self, module: object, name: str):
        self.assertTrue(hasattr(module, name), f"required contract {name} is missing")
        return getattr(module, name)

    def _alias(self, *, original: str = "Ana Pérez", normalized: str = "ana pérez"):
        contract = self._contract(decision_contracts, "ProjectionAliasSnapshotV1")
        return contract(
            schema_version=1,
            alias_original=original,
            alias_normalized=normalized,
        )

    def _override(
        self,
        *,
        stable_target_key: str | None = None,
        field_path: str = "canonical_name",
        value: str = "Ana Pérez",
        scope: str = "record",
        document_key: str | None = None,
        period_id: int | None = None,
        relationship_key: str | None = None,
    ):
        contract = self._contract(decision_contracts, "ProjectionOverrideSnapshotV1")
        return contract(
            schema_version=1,
            field_path=field_path,
            projected_value={"kind": "string", "string_value": value},
            scope=scope,
            stable_target_key=stable_target_key or self.stable_target_key,
            target_table="person_roles",
            target_pk=7,
            document_key=document_key,
            period_id=period_id,
            relationship_key=relationship_key,
            locked=True,
        )

    def _identity(self, *, aliases=None):
        contract = self._contract(decision_contracts, "IdentityProjectionSnapshotV1")
        return contract(
            schema_version=1,
            canonical_identity_key="human:ana-perez",
            canonical_name="Ana Pérez",
            identity_type="unclassified_person",
            aliases=(self._alias(),) if aliases is None else aliases,
        )

    def _snapshot(self, *, aliases=None, overrides=None):
        contract = self._contract(decision_contracts, "ReviewProjectionSnapshotV1")
        return contract(
            schema_version=1,
            case_status="resolved",
            scientific_status="validated",
            current_decision_id=self.current_decision_id,
            identity=self._identity(aliases=aliases),
            overrides=(self._override(),) if overrides is None else overrides,
        )

    def test_missing_public_contracts_are_materialized_with_exact_fields(self):
        identity_projection = self._contract(operation_contracts, "HumanIdentityProjectionV1")
        reversal_command = self._contract(operation_contracts, "FunctionalReversalCommandV1")
        optimistic_error = self._contract(operation_contracts, "OptimisticLockError")

        self.assertEqual(
            tuple(identity_projection.model_fields),
            (
                "stable_target_key",
                "canonical_identity_key",
                "canonical_name",
                "decision_id",
                "locked",
                "aliases",
            ),
        )
        self.assertEqual(
            tuple(reversal_command.model_fields),
            (
                "review_item_id",
                "decision_id_to_revert",
                "actor_user_id",
                "expected_case_version",
                "reason",
                "correlation_id",
                "request_id",
            ),
        )
        self.assertTrue(issubclass(optimistic_error, RuntimeError))
        self.assertEqual(str(optimistic_error("case version conflict")), "case version conflict")

    def test_snapshot_contracts_are_closed_frozen_and_versioned(self):
        snapshot = self._snapshot()
        for contract in (
            self._alias().__class__,
            self._override().__class__,
            self._identity().__class__,
            snapshot.__class__,
        ):
            with self.subTest(contract=contract.__name__):
                self.assertIn("schema_version", contract.model_fields)
                self.assertEqual(contract.model_config["extra"], "forbid")
                self.assertTrue(contract.model_config["frozen"])
        with self.assertRaises(ValidationError):
            snapshot.case_status = "pending"

    def test_snapshot_rejects_unknown_raw_and_domain_collection_fields(self):
        contract = self._contract(decision_contracts, "ReviewProjectionSnapshotV1")
        base = self._snapshot().model_dump(mode="python")
        forbidden = (
            "unexpected",
            "raw_name",
            "raw_author_name",
            "raw_title",
            "raw_value",
            "parsed_payload",
            "ocr_trace",
            "source_page",
            "bounding_box",
            "evidence",
            "roles",
            "products",
            "authorships",
            "director_relations",
            "periods",
            "kpi",
        )
        for field in forbidden:
            with self.subTest(field=field), self.assertRaises(ValidationError):
                contract.model_validate(base | {field: "forbidden"})

        override_contract = self._contract(decision_contracts, "ProjectionOverrideSnapshotV1")
        override = self._override().model_dump(mode="python")
        for field in ("raw_name", "raw_author_name", "raw_title", "raw_value", "parsed_payload"):
            with self.subTest(override_field=field), self.assertRaises(ValidationError):
                override_contract.model_validate(override | {field: "forbidden"})
        with self.assertRaises(ValidationError):
            override_contract.model_validate(override | {"field_path": "raw_name"})

    def test_aliases_are_sorted_canonically_and_duplicate_normalized_values_are_rejected(self):
        zeta = self._alias(original="Ζήτα", normalized="ζήτα")
        ana = self._alias(original="Ána", normalized="ána")
        identity = self._identity(aliases=(zeta, ana))
        self.assertEqual(tuple(alias.alias_normalized for alias in identity.aliases), ("ána", "ζήτα"))

        duplicate = self._alias(original="ANA", normalized="ána")
        with self.assertRaises(ValidationError):
            self._identity(aliases=(ana, duplicate))

    def test_overrides_are_sorted_canonically_and_duplicate_contexts_are_rejected(self):
        later = self._override(stable_target_key=self.other_stable_target_key, value="B")
        earlier = self._override(stable_target_key=self.stable_target_key, value="A")
        snapshot = self._snapshot(overrides=(later, earlier))
        self.assertEqual(
            tuple(item.stable_target_key for item in snapshot.overrides),
            (self.stable_target_key, self.other_stable_target_key),
        )

        conflicting_duplicate = self._override(stable_target_key=self.stable_target_key, value="Other")
        with self.assertRaises(ValidationError):
            self._snapshot(overrides=(earlier, conflicting_duplicate))

    def test_override_snapshot_enforces_scope_context_and_target_rules(self):
        valid = (
            self._override(scope="record"),
            self._override(scope="document", document_key="imports/identity.pdf"),
            self._override(scope="period", field_path="scientific_status", period_id=20261),
            self._override(
                scope="relationship",
                field_path="project_director_relationship_status",
                relationship_key="person:ana|project:one",
            ),
            self._override(scope="global_identity", field_path="canonical_identity_key"),
        )
        self.assertEqual(len(valid), 5)

        invalid = (
            {"scope": "document"},
            {"scope": "document", "document_key": "   "},
            {"scope": "document", "document_key": "doc", "period_id": 20261},
            {"scope": "period"},
            {"scope": "relationship", "relationship_key": "\t"},
            {"scope": "global_identity", "field_path": "product_title"},
            {"scope": "global_identity", "document_key": "doc"},
        )
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self._override(**changes)

        contract = self._contract(decision_contracts, "ProjectionOverrideSnapshotV1")
        with self.assertRaises(ValidationError):
            contract.model_validate(
                self._override().model_dump(mode="python") | {"stable_target_key": "not-a-stable-key"}
            )

    def test_projection_pair_is_all_or_none_for_projectable_payloads(self):
        snapshot = self._snapshot()
        base = {
            "kind": "identity",
            "schema_version": 1,
            "canonical_identity_key": "human:ana-perez",
            "canonical_name": "Ana Pérez",
            "identity_type": "unclassified_person",
        }
        for lone_field in ("projection_before", "projection_after"):
            with self.subTest(lone_field=lone_field), self.assertRaisesRegex(
                ValidationError,
                "projection_before and projection_after must be provided together",
            ):
                decision_contracts.IdentityDecisionPayloadV1.model_validate(base | {lone_field: snapshot})

        legacy = decision_contracts.IdentityDecisionPayloadV1.model_validate(base)
        self.assertIsNone(legacy.projection_before)
        self.assertIsNone(legacy.projection_after)

    def test_all_projection_capable_payloads_expose_the_snapshot_pair(self):
        names = (
            "IdentityDecisionPayloadV1",
            "FieldOverridePayloadV1",
            "IdentityMergePayloadV1",
            "IdentitySeparationPayloadV1",
            "MaintainSeparatePayloadV1",
            "DecisionReversalPayloadV1",
        )
        for name in names:
            contract = self._contract(decision_contracts, name)
            with self.subTest(contract=name):
                self.assertIn("projection_before", contract.model_fields)
                self.assertIn("projection_after", contract.model_fields)

    def test_reversal_requires_complete_snapshots_and_keeps_v1_identity(self):
        contract = self._contract(decision_contracts, "DecisionReversalPayloadV1")
        base = {
            "kind": "decision_reversal",
            "schema_version": 1,
            "decision_id_to_revert": self.reverted_decision_id,
            "restore_decision_id": self.current_decision_id,
        }
        with self.assertRaises(ValidationError):
            contract.model_validate(base)
        snapshot = self._snapshot()
        payload = contract.model_validate(
            base | {"projection_before": snapshot, "projection_after": snapshot}
        )
        self.assertEqual(payload.kind, "decision_reversal")
        self.assertEqual(payload.schema_version, 1)
        self.assertEqual(payload.decision_id_to_revert, self.reverted_decision_id)
        self.assertEqual(payload.restore_decision_id, self.current_decision_id)

    def test_snapshot_serialization_is_canonical_and_repeatable(self):
        first_alias = self._alias(original="Ána", normalized="ána")
        second_alias = self._alias(original="Ζήτα", normalized="ζήτα")
        first_override = self._override(stable_target_key=self.stable_target_key, value="A")
        second_override = self._override(stable_target_key=self.other_stable_target_key, value="B")
        reverse_input = self._snapshot(
            aliases=(second_alias, first_alias),
            overrides=(second_override, first_override),
        )
        canonical_input = self._snapshot(
            aliases=(first_alias, second_alias),
            overrides=(first_override, second_override),
        )
        self.assertEqual(reverse_input.model_dump_json(), reverse_input.model_dump_json())
        self.assertEqual(reverse_input.model_dump_json(), canonical_input.model_dump_json())

    def test_snapshot_preserves_case_and_scientific_status(self):
        snapshot = self._snapshot()
        self.assertEqual(snapshot.case_status.value, "resolved")
        self.assertEqual(snapshot.scientific_status.value, "validated")
        self.assertEqual(snapshot.current_decision_id, self.current_decision_id)

    def test_functional_reversal_command_rejects_client_snapshots_and_blank_reason(self):
        contract = self._contract(operation_contracts, "FunctionalReversalCommandV1")
        command = {
            "review_item_id": UUID("33333333-3333-3333-3333-333333333333"),
            "decision_id_to_revert": self.reverted_decision_id,
            "actor_user_id": 17,
            "expected_case_version": 4,
            "reason": "Restore the previous approved projection",
            "correlation_id": UUID("44444444-4444-4444-4444-444444444444"),
            "request_id": UUID("55555555-5555-5555-5555-555555555555"),
        }
        validated = contract.model_validate(command)
        self.assertEqual(validated.expected_case_version, 4)
        with self.assertRaises(ValidationError):
            contract.model_validate(command | {"projection_before": self._snapshot()})
        with self.assertRaises(ValidationError):
            contract.model_validate(command | {"projection_after": self._snapshot()})
        with self.assertRaises(ValidationError):
            contract.model_validate(command | {"reason": "   "})

    def test_human_identity_projection_is_identity_only_and_deterministic(self):
        contract = self._contract(operation_contracts, "HumanIdentityProjectionV1")
        projection = contract(
            stable_target_key=self.stable_target_key,
            canonical_identity_key="human:ana-perez",
            canonical_name="Ana Pérez",
            decision_id=self.current_decision_id,
            locked=True,
            aliases=("ζήτα", "ána"),
        )
        self.assertEqual(projection.aliases, ("ána", "ζήτα"))
        for field in ("roles", "products", "authorships", "periods", "scientific_status", "kpi"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                contract.model_validate(projection.model_dump(mode="python") | {field: "forbidden"})
        with self.assertRaises(ValidationError):
            contract.model_validate(projection.model_dump(mode="python") | {"locked": False})


if __name__ == "__main__":
    unittest.main()
