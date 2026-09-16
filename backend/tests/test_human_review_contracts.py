from __future__ import annotations

import hashlib
import json
import re
import unittest
from typing import Any, get_args, get_origin, get_type_hints
from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from app.models.human_review_enums import (
    AuditEventType,
    B2BAction,
    B2BCapability,
    BackfillSourceMembership,
    CanonicalIdentityOrigin,
    CanonicalIdentityStatus,
    CanonicalIdentityType,
    DecisionLifecycle,
    DecisionScope,
    OverrideField,
    OverrideScope,
    PersonAliasClass,
    PersonAliasScope,
    PersonAliasStatus,
    ReviewActorType,
    ReviewCaseStatus,
    ReviewCaseType,
    ReviewDecisionType,
    ReviewTargetTable,
    ScientificStatus,
)
from app.schemas.human_review import (
    DecisionPayloadV1,
    DecisionReversalPayloadV1,
    FieldOverridePayloadV1,
    IdentityDecisionPayloadV1,
    IdentityMergePayloadV1,
    IdentitySeparationMemberV1,
    IdentitySeparationPayloadV1,
    MaintainSeparatePayloadV1,
    ScalarOverrideValueV1,
    StableTargetV1,
)
from app.services.human_review_targets import (
    build_stable_target_key,
    normalize_person_alias,
    raw_value_sha256,
)


class HumanReviewContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = {
            "schema_version": 1,
            "case_type": "person_identity",
            "target_table": "person_roles",
            "document_key": "imports/participants.pdf",
            "source_revision": "rev-7",
            "source_page": 12,
            "source_section": "Participantes",
            "row_or_block_id": "row-4",
            "field_path": "canonical_identity_key",
            "raw_value_sha256": "a" * 64,
            "period_id": 20261,
            "relationship_key": "person:ana|project:p-7",
        }
        self.stable_target_key = f"b2b:v1:person_identity:{'a' * 64}"

    def test_persisted_enum_values_are_exact(self):
        expected = {
            B2BCapability: ("RESEARCH_MANAGER", "SYSTEM_ADMIN"),
            B2BAction: (
                "view_foundations",
                "view_audit",
                "apply_scientific",
                "propose_scientific",
                "revert_scientific",
                "manage_technical_access",
            ),
            ReviewCaseType: (
                "person_identity",
                "author_identity",
                "product",
                "project_director_relation",
                "external_identity",
                "possible_duplicate",
                "invalid_text",
                "new_evidence_conflict",
            ),
            ReviewCaseStatus: (
                "pending",
                "in_review",
                "awaiting_gestor_approval",
                "resolved",
                "reopened",
                "conflicted",
                "superseded",
            ),
            ScientificStatus: ("pending", "validated", "rejected", "discarded"),
            ReviewDecisionType: (
                "validated",
                "corrected",
                "linked",
                "merged",
                "maintained_separate",
                "separated",
                "rejected",
                "discarded",
                "maintained",
                "reverted",
            ),
            DecisionLifecycle: ("proposed", "approved", "declined", "superseded"),
            DecisionScope: ("global_identity", "record", "document", "relationship", "period"),
            ReviewActorType: ("human", "legacy"),
            ReviewTargetTable: (
                "person_roles",
                "scientific_production_authors",
                "scientific_productions",
                "research_entities",
                "external_researchers",
            ),
            CanonicalIdentityType: ("internal_person", "external_person", "unclassified_person"),
            CanonicalIdentityStatus: ("active", "merged", "superseded"),
            CanonicalIdentityOrigin: ("b1_locked", "human"),
            PersonAliasClass: ("person_name",),
            PersonAliasScope: ("global_identity",),
            PersonAliasStatus: ("active", "superseded"),
            OverrideScope: ("global_identity", "record", "document", "relationship", "period"),
            AuditEventType: (
                "case_backfilled",
                "locked_decision_imported",
                "identity_created",
                "alias_created",
                "override_created",
                "capability_assigned",
                "capability_revoked",
                "audit_corrected",
                "functional_reversion",
                "scientific_decision_applied",
            ),
            BackfillSourceMembership: (
                "canonical_pending",
                "product_pending",
                "director_relation_pending",
                "external_pending",
                "possible_match",
                "identity_locked",
            ),
        }
        for enum_type, values in expected.items():
            with self.subTest(enum=enum_type.__name__):
                self.assertEqual(tuple(item.value for item in enum_type), values)

    def test_override_field_is_exact_allowlist(self):
        self.assertEqual(
            {item.value for item in OverrideField},
            {
                "canonical_identity_key",
                "canonical_name",
                "product_title",
                "author_identity_key",
                "project_director_identity_key",
                "project_director_relationship_status",
                "external_identity_key",
                "external_institution",
                "scientific_status",
            },
        )

    def test_payload_rejects_unknown_key(self):
        payload = {
            "kind": "identity",
            "schema_version": 1,
            "canonical_identity_key": "human:one",
            "canonical_name": "Persona Uno",
            "identity_type": "unclassified_person",
            "unexpected": True,
        }
        with self.assertRaises(ValidationError) as context:
            IdentityDecisionPayloadV1.model_validate(payload)
        self.assertTrue(
            any(error["type"] == "extra_forbidden" and error["loc"] == ("unexpected",) for error in context.exception.errors())
        )

    def test_raw_field_cannot_be_override_target(self):
        for field in ("raw_name", "raw_author_name", "raw_title", "raw_value", "person_key", "parsed_payload"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                FieldOverridePayloadV1(
                    kind="field_override",
                    schema_version=1,
                    field_path=field,
                    value=ScalarOverrideValueV1(kind="string", string_value="x"),
                    scope="record",
                )

    def test_stable_key_ignores_surrogate_row_id(self):
        first = StableTargetV1.model_validate(self.target | {"target_pk": 10})
        second = StableTargetV1.model_validate(self.target | {"target_pk": 99})
        self.assertEqual(build_stable_target_key(first), build_stable_target_key(second))

    def test_stable_key_is_independent_of_input_key_order(self):
        ordered_items = list((self.target | {"target_pk": 10}).items())
        first = StableTargetV1.model_validate(dict(ordered_items))
        second = StableTargetV1.model_validate(dict(reversed(ordered_items)))
        self.assertEqual(build_stable_target_key(first), build_stable_target_key(second))

    def test_stable_key_uses_exact_prefix_and_canonical_material(self):
        target = StableTargetV1.model_validate(self.target | {"target_pk": 10})
        material = target.model_dump(mode="json", exclude={"target_pk"})
        canonical_json = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        expected = f"b2b:v1:person_identity:{hashlib.sha256(canonical_json).hexdigest()}"
        self.assertEqual(build_stable_target_key(target), expected)

    def test_each_stable_locator_changes_stable_key(self):
        baseline = StableTargetV1.model_validate(self.target | {"target_pk": 10})
        baseline_key = build_stable_target_key(baseline)
        mutations = {
            "case_type": "author_identity",
            "target_table": "scientific_production_authors",
            "document_key": "imports/other.pdf",
            "source_revision": "rev-8",
            "source_page": 13,
            "source_section": "Autores",
            "row_or_block_id": "row-5",
            "field_path": "canonical_name",
            "relationship_key": "person:ana|project:p-8",
            "period_id": 20262,
            "raw_value_sha256": "b" * 64,
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                changed = StableTargetV1.model_validate(self.target | {"target_pk": 10, field: value})
                self.assertNotEqual(build_stable_target_key(changed), baseline_key)

    def test_target_hash_helper_is_deterministic(self):
        self.assertEqual(raw_value_sha256("\u00c1rbol"), hashlib.sha256("\u00c1rbol".encode("utf-8")).hexdigest())
        self.assertEqual(raw_value_sha256(None), hashlib.sha256(b"").hexdigest())

    def test_alias_normalization_preserves_unicode_text(self):
        examples = {
            "  Jos\u00e9   de-la  Torre ": "jos\u00e9 de la torre",
            "\u674e\u96f7": "\u674e\u96f7",
            "\u00d8yvind": "\u00f8yvind",
            "Ana\u2665\u674e/\u96f7": "ana \u674e \u96f7",
            "\u0915\u0941": "\u0915\u0941",
        }
        for source, expected in examples.items():
            with self.subTest(source=source):
                self.assertEqual(normalize_person_alias(source), expected)

    def test_alias_normalization_rejects_empty_result(self):
        for source in ("", "   ", "---", "\u2665 / !!!"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                normalize_person_alias(source)

    def test_scalar_override_accepts_each_closed_variant(self):
        valid_values = (
            ScalarOverrideValueV1(kind="string", string_value="x"),
            ScalarOverrideValueV1(kind="integer", integer_value=7),
            ScalarOverrideValueV1(kind="decimal", decimal_value="12.50"),
            ScalarOverrideValueV1(kind="boolean", boolean_value=True),
            ScalarOverrideValueV1(kind="null"),
        )
        self.assertEqual(tuple(value.kind for value in valid_values), ("string", "integer", "decimal", "boolean", "null"))

    def test_scalar_override_rejects_missing_or_mismatched_values(self):
        invalid_values = (
            {"kind": "string"},
            {"kind": "integer", "string_value": "7"},
            {"kind": "decimal", "decimal_value": "1.0", "integer_value": 1},
            {"kind": "boolean", "boolean_value": True, "string_value": "true"},
            {"kind": "null", "string_value": "not-null"},
        )
        for payload in invalid_values:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                ScalarOverrideValueV1.model_validate(payload)

    def test_integer_override_is_strict(self):
        for value in (True, False, 7.0, "7"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                ScalarOverrideValueV1(kind="integer", integer_value=value)

    def test_boolean_override_is_strict(self):
        for value in (0, 1, "true", "false"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                ScalarOverrideValueV1(kind="boolean", boolean_value=value)

    def test_decimal_override_requires_finite_nonempty_string(self):
        invalid_values = (1, 1.25, True, "", "   ", "NaN", "nan", "Infinity", "-Infinity", "inf")
        for value in invalid_values:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                ScalarOverrideValueV1(kind="decimal", decimal_value=value)
        for value in ("0", "-12.50", "1e3"):
            with self.subTest(value=value):
                self.assertEqual(ScalarOverrideValueV1(kind="decimal", decimal_value=value).decimal_value, value)

    def test_decision_payload_union_is_discriminated_by_kind(self):
        adapter = TypeAdapter(DecisionPayloadV1)
        payload = adapter.validate_python(
            {
                "kind": "identity",
                "schema_version": 1,
                "canonical_identity_key": "human:one",
                "canonical_name": "Persona Uno",
                "identity_type": "unclassified_person",
            }
        )
        self.assertIsInstance(payload, IdentityDecisionPayloadV1)
        with self.assertRaises(ValidationError):
            adapter.validate_python({"kind": "role_change", "schema_version": 1})

    def test_merge_requires_two_unique_stable_targets(self):
        for members in (("one",), ("one", "one")):
            with self.subTest(members=members), self.assertRaises(ValidationError):
                IdentityMergePayloadV1(
                    kind="identity_merge",
                    schema_version=1,
                    target_identity_key="human:target",
                    member_stable_target_keys=members,
                )
        valid = IdentityMergePayloadV1(
            kind="identity_merge",
            schema_version=1,
            target_identity_key="human:target",
            member_stable_target_keys=("one", "two"),
        )
        self.assertEqual(valid.member_stable_target_keys, ("one", "two"))

    def test_separation_rejects_duplicate_stable_targets(self):
        assignment = {
            "stable_target_key": "b2b:v1:person_identity:one",
            "target_identity_key": "human:one",
            "target_canonical_name": "Persona Uno",
        }
        with self.assertRaises(ValidationError):
            IdentitySeparationPayloadV1.model_validate(
                {
                    "kind": "identity_separation",
                    "schema_version": 1,
                    "source_identity_key": "human:source",
                    "assignments": (assignment, assignment),
                }
            )

    def test_separation_requires_at_least_one_assignment(self):
        with self.assertRaises(ValidationError):
            IdentitySeparationPayloadV1(
                kind="identity_separation",
                schema_version=1,
                source_identity_key="human:source",
                assignments=(),
            )

    def test_maintain_separate_requires_parallel_unique_nonempty_members(self):
        invalid_pairs = (
            ((), ()),
            (("one",), ("human:one",)),
            (("one", "two"), ("human:one",)),
            (("one", "one"), ("human:one", "human:two")),
            (("one", "two"), ("human:one", "human:one")),
        )
        for stable_targets, identities in invalid_pairs:
            with self.subTest(stable_targets=stable_targets, identities=identities), self.assertRaises(ValidationError):
                MaintainSeparatePayloadV1(
                    kind="maintain_separate",
                    schema_version=1,
                    stable_target_keys=stable_targets,
                    identity_keys=identities,
                )

    def test_identity_operation_payloads_have_no_scientific_or_role_fields(self):
        base_merge = {
            "kind": "identity_merge",
            "schema_version": 1,
            "target_identity_key": "human:target",
            "member_stable_target_keys": ("one", "two"),
        }
        base_separation = {
            "kind": "identity_separation",
            "schema_version": 1,
            "source_identity_key": "human:source",
            "assignments": (
                {
                    "stable_target_key": "one",
                    "target_identity_key": "human:one",
                    "target_canonical_name": "Persona Uno",
                },
            ),
        }
        for field in ("role", "product", "scientific_status", "possible_kpi_impact"):
            with self.subTest(payload="merge", field=field), self.assertRaises(ValidationError):
                IdentityMergePayloadV1.model_validate(base_merge | {field: "forbidden"})
            with self.subTest(payload="separation", field=field), self.assertRaises(ValidationError):
                IdentitySeparationPayloadV1.model_validate(base_separation | {field: "forbidden"})

    def test_remaining_decision_payload_shapes(self):
        separation_member = IdentitySeparationMemberV1(
            stable_target_key="one",
            target_identity_key="human:one",
            target_canonical_name="Persona Uno",
        )
        maintained = MaintainSeparatePayloadV1(
            kind="maintain_separate",
            schema_version=1,
            stable_target_keys=("one", "two"),
            identity_keys=("human:one", "human:two"),
        )
        with self.assertRaises(ValidationError):
            DecisionReversalPayloadV1(
                kind="decision_reversal",
                schema_version=1,
                decision_id_to_revert=UUID("11111111-1111-1111-1111-111111111111"),
                restore_decision_id=None,
            )
        self.assertEqual(separation_member.target_identity_key, "human:one")
        self.assertEqual(maintained.identity_keys, ("human:one", "human:two"))

    def test_override_context_matrix_accepts_only_matching_context(self):
        valid_payloads = (
            {
                "field_path": "product_title",
                "scope": "record",
                "stable_target_key": self.stable_target_key,
            },
            {
                "field_path": "product_title",
                "scope": "document",
                "document_key": "imports/products.pdf",
            },
            {
                "field_path": "scientific_status",
                "scope": "period",
                "period_id": 20261,
            },
            {
                "field_path": "project_director_relationship_status",
                "scope": "relationship",
                "relationship_key": "person:one|project:two",
            },
            {
                "field_path": "canonical_identity_key",
                "scope": "global_identity",
            },
            {
                "field_path": "canonical_name",
                "scope": "global_identity",
            },
        )
        for context in valid_payloads:
            with self.subTest(context=context):
                try:
                    payload = FieldOverridePayloadV1(
                        kind="field_override",
                        schema_version=1,
                        value=ScalarOverrideValueV1(kind="string", string_value="x"),
                        **context,
                    )
                except ValidationError as error:
                    self.fail(f"valid override context was rejected: {error}")
                self.assertEqual(payload.scope.value, context["scope"])

    def test_field_override_declares_record_stable_target(self):
        self.assertIn("stable_target_key", FieldOverridePayloadV1.model_fields)

    def test_override_context_matrix_rejects_missing_or_extraneous_context(self):
        missing_contexts = (
            {"field_path": "product_title", "scope": "record"},
            {"field_path": "product_title", "scope": "document"},
            {"field_path": "scientific_status", "scope": "period"},
            {"field_path": "project_director_relationship_status", "scope": "relationship"},
        )
        for context in missing_contexts:
            with self.subTest(kind="missing", context=context), self.assertRaises(ValidationError):
                FieldOverridePayloadV1(
                    kind="field_override",
                    schema_version=1,
                    value=ScalarOverrideValueV1(kind="string", string_value="x"),
                    **context,
                )

        valid_by_scope = {
            "record": {"stable_target_key": self.stable_target_key},
            "document": {"document_key": "imports/products.pdf"},
            "period": {"period_id": 20261},
            "relationship": {"relationship_key": "person:one|project:two"},
            "global_identity": {},
        }
        forbidden_by_scope = {
            "record": {"document_key": "doc", "period_id": 1, "relationship_key": "rel"},
            "document": {"stable_target_key": self.stable_target_key, "period_id": 1, "relationship_key": "rel"},
            "period": {
                "stable_target_key": self.stable_target_key,
                "document_key": "doc",
                "relationship_key": "rel",
            },
            "relationship": {"stable_target_key": self.stable_target_key, "document_key": "doc", "period_id": 1},
            "global_identity": {
                "stable_target_key": self.stable_target_key,
                "document_key": "doc",
                "period_id": 1,
                "relationship_key": "rel",
            },
        }
        for scope, base_context in valid_by_scope.items():
            for forbidden_field, forbidden_value in forbidden_by_scope[scope].items():
                with self.subTest(kind="extraneous", scope=scope, field=forbidden_field), self.assertRaises(
                    ValidationError
                ):
                    FieldOverridePayloadV1(
                        kind="field_override",
                        schema_version=1,
                        field_path="canonical_name" if scope == "global_identity" else "product_title",
                        value=ScalarOverrideValueV1(kind="string", string_value="x"),
                        scope=scope,
                        **(base_context | {forbidden_field: forbidden_value}),
                    )

    def test_record_override_rejects_blank_malformed_or_oversized_stable_key(self):
        invalid_keys = (
            "",
            "   ",
            "b2b:v1:person_identity:short",
            f"b2b:v1:person_identity:{'A' * 64}",
            f"b2b:v1:{'a' * 57}:{'a' * 64}",
        )
        for stable_target_key in invalid_keys:
            with self.subTest(stable_target_key=stable_target_key), self.assertRaises(ValidationError):
                FieldOverridePayloadV1(
                    kind="field_override",
                    schema_version=1,
                    field_path="canonical_name",
                    value=ScalarOverrideValueV1(kind="string", string_value="x"),
                    scope="record",
                    stable_target_key=stable_target_key,
                )

    def test_override_rejects_blank_document_or_relationship_context(self):
        for scope, field, context in (
            ("document", "document_key", "   "),
            ("relationship", "relationship_key", "\t"),
        ):
            with self.subTest(scope=scope), self.assertRaises(ValidationError):
                FieldOverridePayloadV1(
                    kind="field_override",
                    schema_version=1,
                    field_path="product_title",
                    value=ScalarOverrideValueV1(kind="string", string_value="x"),
                    scope=scope,
                    **{field: context},
                )

    def test_global_identity_override_rejects_incompatible_field(self):
        with self.assertRaises(ValidationError):
            FieldOverridePayloadV1(
                kind="field_override",
                schema_version=1,
                field_path="product_title",
                value=ScalarOverrideValueV1(kind="string", string_value="x"),
                scope="global_identity",
            )

    def test_stable_target_rejects_invalid_raw_hash(self):
        invalid_hashes = ("", "a" * 63, "a" * 65, "A" * 64, "g" * 64)
        for raw_hash in invalid_hashes:
            with self.subTest(raw_hash=raw_hash), self.assertRaises(ValidationError):
                StableTargetV1.model_validate(self.target | {"raw_value_sha256": raw_hash})

    def test_stable_target_requires_nonblank_locators_without_rewriting(self):
        for field in ("document_key", "source_section", "row_or_block_id"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                StableTargetV1.model_validate(self.target | {field: "   "})

        original_values = {
            "document_key": "  imports/participants.pdf  ",
            "source_section": "  Participantes  ",
            "row_or_block_id": "  row-4  ",
        }
        target = StableTargetV1.model_validate(self.target | original_values)
        for field, expected in original_values.items():
            with self.subTest(field=field):
                self.assertEqual(getattr(target, field), expected)

    def test_stable_target_accepts_opaque_row_or_block_ids_without_a_length_cap(self):
        exact_fixture_prefix = "product:scientific-productions:id=400:"
        locator_248 = exact_fixture_prefix + "x" * (248 - len(exact_fixture_prefix))
        for index, length in enumerate((179, 180, 181, 248, 512), start=1):
            locator = locator_248 if length == 248 else f"L{index}:" + "x" * (length - len(f"L{index}:"))
            with self.subTest(length=length):
                target = StableTargetV1.model_validate(
                    self.target | {"row_or_block_id": locator}
                )
                self.assertEqual(target.row_or_block_id, locator)
                self.assertEqual(target.row_or_block_id.encode("utf-8"), locator.encode("utf-8"))

    def test_stable_keys_use_full_digest_and_fit_approved_width(self):
        for case_type in ReviewCaseType:
            with self.subTest(case_type=case_type.value):
                target = StableTargetV1.model_validate(self.target | {"case_type": case_type.value})
                key = build_stable_target_key(target)
                prefix = f"b2b:v1:{case_type.value}:"
                self.assertTrue(key.startswith(prefix))
                digest = key[len(prefix) :]
                self.assertRegex(digest, r"^[0-9a-f]{64}$")
                self.assertEqual(len(digest), 64)
                self.assertLessEqual(len(key), 128)
        director_target = StableTargetV1.model_validate(self.target | {"case_type": "project_director_relation"})
        director_key = build_stable_target_key(director_target)
        self.assertEqual(len(director_key), 97)
        self.assertTrue(re.fullmatch(r"b2b:v1:project_director_relation:[0-9a-f]{64}", director_key))

    def test_persisted_payload_annotations_contain_no_any_or_open_dict(self):
        payload_types = (
            ScalarOverrideValueV1,
            IdentityDecisionPayloadV1,
            FieldOverridePayloadV1,
            IdentityMergePayloadV1,
            IdentitySeparationMemberV1,
            IdentitySeparationPayloadV1,
            MaintainSeparatePayloadV1,
            DecisionReversalPayloadV1,
        )

        def contains_open_type(annotation: object) -> bool:
            if annotation is Any or get_origin(annotation) is dict:
                return True
            return any(contains_open_type(argument) for argument in get_args(annotation))

        for payload_type in payload_types:
            for field, annotation in get_type_hints(payload_type).items():
                with self.subTest(payload=payload_type.__name__, field=field):
                    self.assertFalse(contains_open_type(annotation))


if __name__ == "__main__":
    unittest.main()
