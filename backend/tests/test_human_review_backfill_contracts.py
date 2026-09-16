from __future__ import annotations

import unittest
from datetime import datetime, timezone

from pydantic import ValidationError

from app.schemas import human_review_operations as contracts
from app.schemas.human_review import StableTargetV1
from app.services.human_review_targets import build_stable_target_key


class HumanReviewBackfillContractTests(unittest.TestCase):
    sha256 = "a" * 64
    captured_at = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)

    def _contract(self, name: str):
        self.assertTrue(hasattr(contracts, name), f"required contract {name} is missing")
        return getattr(contracts, name)

    def _target(
        self,
        *,
        case_type: str = "person_identity",
        target_table: str = "person_roles",
        target_pk: int = 7,
        raw_value_sha256: str | None = None,
    ) -> StableTargetV1:
        return StableTargetV1(
            schema_version=1,
            case_type=case_type,
            target_table=target_table,
            target_pk=target_pk,
            document_key=f"b1/{target_table}/source.pdf",
            source_revision="revision-1",
            source_page=1,
            source_section="participants",
            row_or_block_id=f"{target_table}:stable-row",
            field_path="case",
            raw_value_sha256=raw_value_sha256 or self.sha256,
            period_id=None,
            relationship_key=None,
        )

    def _candidate(
        self,
        *,
        case_type: str = "person_identity",
        target_table: str = "person_roles",
        source_id: int = 7,
        memberships: tuple[str, ...] = ("canonical_pending",),
        case_status: str = "pending",
        scientific_status: str = "pending",
        possible_kpi_impact: bool = True,
        target_pk: int | None = None,
    ):
        candidate = self._contract("BackfillCandidateV1")
        stable_target_case = case_type
        if case_type == "possible_duplicate":
            stable_target_case = (
                "person_identity"
                if target_table == "person_roles"
                else "author_identity"
            )
        return candidate(
            case_type=case_type,
            stable_target=self._target(
                case_type=stable_target_case,
                target_table=target_table,
                target_pk=source_id if target_pk is None else target_pk,
            ),
            source_table=target_table,
            source_id=source_id,
            memberships=memberships,
            case_status=case_status,
            scientific_status=scientific_status,
            possible_kpi_impact=possible_kpi_impact,
        )

    def _locked(self, *, alias_original: str | None = "Ana Pérez"):
        locked = self._contract("LockedDecisionImportV1")
        return locked(
            candidate=self._candidate(
                memberships=("identity_locked", "canonical_pending"),
                case_status="resolved",
                scientific_status="validated",
            ),
            canonical_identity_key="legacy:person:7",
            canonical_name="Ana Pérez",
            identity_type="unclassified_person",
            legacy_actor_identifier="legacy-import",
            legacy_decided_at=self.captured_at,
            alias_original=alias_original,
        )

    def test_nine_public_contracts_exist_with_exact_fields(self):
        membership = self._contract("BackfillSourceMembership")
        expected_values = (
            "canonical_pending",
            "product_pending",
            "director_relation_pending",
            "external_pending",
            "possible_match",
            "identity_locked",
        )
        self.assertEqual(tuple(item.value for item in membership), expected_values)

        exact_fields = {
            "SourcePopulationCountV1": ("population", "row_count", "stable_target_count"),
            "SourcePopulationOverlapV1": ("populations", "stable_target_count"),
            "BackfillCandidateV1": (
                "case_type",
                "stable_target",
                "source_table",
                "source_id",
                "memberships",
                "case_status",
                "scientific_status",
                "possible_kpi_impact",
            ),
            "LockedDecisionImportV1": (
                "candidate",
                "canonical_identity_key",
                "canonical_name",
                "identity_type",
                "legacy_actor_identifier",
                "legacy_decided_at",
                "alias_original",
            ),
            "BackfillBlockerV1": ("source_table", "source_id", "code", "message"),
            "BackfillDeferredRecordV1": (
                "source_table",
                "source_pk",
                "source_population",
                "case_type",
                "period_id",
                "document_key",
                "identity_reference",
                "reason_code",
                "disposition",
                "materializable",
                "affects_kpi",
                "evidence_summary",
            ),
            "BackfillExcludedRecordV1": (
                "source_table",
                "research_entity_id",
                "source_population",
                "case_type",
                "period_id",
                "entity_type",
                "reason_code",
                "disposition",
                "materializable",
                "affects_kpi",
                "evidence_summary",
            ),
            "B2B1BackfillPlanV1": (
                "schema_version",
                "captured_at",
                "invariant_snapshot_sha256",
                "source_counts",
                "overlaps",
                "union_stable_target_count",
                "candidates",
                "locked_decisions",
                "blockers",
                "deferred_records",
                "excluded_records",
                "hard_blockers",
            ),
            "B2B1BackfillApplyResultV1": (
                "schema_version",
                "plan_sha256",
                "before_invariants_sha256",
                "after_invariants_sha256",
                "created_review_items",
                "created_decisions",
                "created_overrides",
                "created_identities",
                "created_aliases",
                "created_audit_events",
                "eligible_products_before",
                "eligible_products_after",
            ),
        }
        for name, fields in exact_fields.items():
            with self.subTest(contract=name):
                model = self._contract(name)
                self.assertEqual(tuple(model.model_fields), fields)
                self.assertEqual(model.model_config["extra"], "forbid")
                self.assertTrue(model.model_config["frozen"])

        gate_error = self._contract("BackfillGateError")
        self.assertTrue(issubclass(gate_error, RuntimeError))
        self.assertEqual(str(gate_error("plan hash mismatch")), "plan hash mismatch")

    def test_population_counts_and_overlaps_are_canonical_and_consistent(self):
        count = self._contract("SourcePopulationCountV1")
        overlap = self._contract("SourcePopulationOverlapV1")

        valid = count(population="canonical_pending", row_count=4, stable_target_count=3)
        self.assertEqual(valid.stable_target_count, 3)
        with self.assertRaises(ValidationError):
            count(population="canonical_pending", row_count=2, stable_target_count=3)
        with self.assertRaises(ValidationError):
            valid.row_count = 9

        canonical = overlap(
            populations=("possible_match", "canonical_pending", "identity_locked"),
            stable_target_count=2,
        )
        self.assertEqual(
            tuple(item.value for item in canonical.populations),
            ("canonical_pending", "possible_match", "identity_locked"),
        )
        for populations in (
            ("canonical_pending",),
            ("canonical_pending", "canonical_pending"),
        ):
            with self.subTest(populations=populations), self.assertRaises(ValidationError):
                overlap(populations=populations, stable_target_count=1)

    def test_candidate_accepts_only_the_closed_population_source_case_matrix(self):
        valid = (
            self._candidate(),
            self._candidate(
                case_type="author_identity",
                target_table="scientific_production_authors",
            ),
            self._candidate(
                case_type="product",
                target_table="scientific_productions",
                memberships=("product_pending",),
            ),
            self._candidate(
                case_type="project_director_relation",
                target_table="research_entities",
                memberships=("director_relation_pending",),
            ),
            self._candidate(
                case_type="external_identity",
                target_table="external_researchers",
                memberships=("external_pending",),
            ),
            self._candidate(
                case_type="possible_duplicate",
                target_table="person_roles",
                memberships=("possible_match",),
                possible_kpi_impact=False,
            ),
            self._candidate(
                case_type="possible_duplicate",
                target_table="scientific_production_authors",
                memberships=("possible_match",),
                possible_kpi_impact=False,
            ),
            self._candidate(
                memberships=("identity_locked", "canonical_pending"),
                case_status="resolved",
                scientific_status="validated",
            ),
        )
        self.assertEqual(len(valid), 8)
        self.assertEqual(
            tuple(item.value for item in valid[-1].memberships),
            ("canonical_pending", "identity_locked"),
        )

        invalid = (
            {"case_type": "invalid_text"},
            {"case_type": "product", "target_table": "person_roles", "memberships": ("product_pending",)},
            {"memberships": ()},
            {"memberships": ("canonical_pending", "canonical_pending")},
            {"memberships": ("canonical_pending", "possible_match")},
            {"memberships": ("identity_locked",), "case_status": "pending"},
            {"memberships": ("identity_locked",), "scientific_status": "pending", "case_status": "resolved"},
            {"case_status": "resolved"},
            {"scientific_status": "validated"},
            {
                "case_type": "possible_duplicate",
                "memberships": ("possible_match",),
                "possible_kpi_impact": True,
            },
        )
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self._candidate(**changes)

    def test_candidate_target_contract_must_match_candidate_source_and_case(self):
        candidate = self._contract("BackfillCandidateV1")
        base = self._candidate().model_dump(mode="python")
        with self.assertRaises(ValidationError):
            candidate.model_validate(
                base | {"stable_target": self._target(case_type="author_identity")}
            )
        with self.assertRaises(ValidationError):
            candidate.model_validate(
                base
                | {
                    "stable_target": self._target(
                        case_type="person_identity",
                        target_table="scientific_production_authors",
                    )
                }
            )

    def test_possible_duplicate_reuses_the_identity_stable_target(self):
        identity = self._candidate()
        duplicate = self._candidate(
            case_type="possible_duplicate",
            memberships=("possible_match",),
            possible_kpi_impact=False,
        )
        self.assertEqual(identity.stable_target, duplicate.stable_target)
        self.assertEqual(
            build_stable_target_key(identity.stable_target),
            build_stable_target_key(duplicate.stable_target),
        )
        self.assertNotEqual(identity.case_type, duplicate.case_type)

    def test_locked_decision_requires_locked_identity_and_preserved_legacy_context(self):
        locked = self._locked(alias_original="   ")
        self.assertIsNone(locked.alias_original)
        self.assertEqual(locked.candidate.case_status.value, "resolved")
        self.assertEqual(locked.candidate.scientific_status.value, "validated")

        contract = self._contract("LockedDecisionImportV1")
        base = self._locked().model_dump(mode="python")
        invalid = (
            {"candidate": self._candidate()},
            {"canonical_identity_key": "   "},
            {"canonical_name": ""},
            {"legacy_actor_identifier": "\t"},
            {"legacy_decided_at": datetime(2026, 7, 15, 12, 0)},
        )
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                contract.model_validate(base | changes)
        for forbidden in ("roles", "products", "authorships", "kpi"):
            with self.subTest(forbidden=forbidden), self.assertRaises(ValidationError):
                contract.model_validate(base | {forbidden: ()})

    def test_blockers_reject_blank_or_secret_bearing_messages(self):
        blocker = self._contract("BackfillBlockerV1")
        valid = blocker(
            source_table="person_roles",
            source_id=7,
            code="missing_stable_locator",
            message="document_key is missing",
        )
        self.assertEqual(valid.code, "missing_stable_locator")
        for message in (
            "   ",
            "password=unsafe",
            "DATABASE_URL=postgresql://user:pass@host/db",
            "Bearer token-value",
        ):
            with self.subTest(message=message), self.assertRaises(ValidationError):
                blocker(
                    source_table="person_roles",
                    source_id=7,
                    code="invariant_mismatch",
                    message=message,
                )

    def test_plan_sorts_all_collections_and_rejects_duplicate_or_inconsistent_counts(self):
        plan = self._contract("B2B1BackfillPlanV1")
        count = self._contract("SourcePopulationCountV1")
        overlap = self._contract("SourcePopulationOverlapV1")
        blocker = self._contract("BackfillBlockerV1")
        person = self._candidate()
        product = self._candidate(
            case_type="product",
            target_table="scientific_productions",
            source_id=9,
            memberships=("product_pending",),
        )
        valid = plan(
            schema_version=1,
            captured_at=self.captured_at,
            invariant_snapshot_sha256=self.sha256,
            source_counts=(
                count(population="product_pending", row_count=1, stable_target_count=1),
                count(population="canonical_pending", row_count=1, stable_target_count=1),
            ),
            overlaps=(
                overlap(
                    populations=("product_pending", "canonical_pending"),
                    stable_target_count=0,
                ),
            ),
            union_stable_target_count=2,
            candidates=(product, person),
            locked_decisions=(),
            blockers=(
                blocker(
                    source_table="scientific_productions",
                    source_id=9,
                    code="missing_stable_locator",
                    message="document_key is missing",
                ),
                blocker(
                    source_table="person_roles",
                    source_id=7,
                    code="ambiguous_alias",
                    message="multiple normalized aliases",
                ),
            ),
        )
        self.assertEqual(
            tuple(item.population.value for item in valid.source_counts),
            ("canonical_pending", "product_pending"),
        )
        self.assertEqual(
            tuple(item.case_type.value for item in valid.candidates),
            ("person_identity", "product"),
        )
        self.assertEqual(valid.blockers[0].source_table.value, "person_roles")

        base = valid.model_dump(mode="python")
        duplicate_target_pk_only = self._candidate(target_pk=999)
        invalid = (
            {"captured_at": datetime(2026, 7, 15, 12, 0)},
            {"invariant_snapshot_sha256": "A" * 64},
            {"union_stable_target_count": 3},
            {"source_counts": (valid.source_counts[0], valid.source_counts[0])},
            {"candidates": (person, duplicate_target_pk_only)},
            {
                "blockers": (
                    valid.blockers[0],
                    valid.blockers[0].model_copy(update={"message": "another message"}),
                )
            },
        )
        for changes in invalid:
            with self.subTest(changes=tuple(changes)), self.assertRaises(ValidationError):
                plan.model_validate(base | changes)

    def test_plan_rejects_duplicate_or_unmatched_locked_decisions(self):
        plan = self._contract("B2B1BackfillPlanV1")
        locked = self._locked()
        base = {
            "schema_version": 1,
            "captured_at": self.captured_at,
            "invariant_snapshot_sha256": self.sha256,
            "source_counts": (),
            "overlaps": (),
            "union_stable_target_count": 1,
            "candidates": (locked.candidate,),
            "locked_decisions": (locked,),
            "blockers": (),
        }
        self.assertEqual(len(plan(**base).locked_decisions), 1)
        with self.assertRaises(ValidationError):
            plan(**(base | {"locked_decisions": (locked, locked)}))
        with self.assertRaises(ValidationError):
            plan(
                **(
                    base
                    | {
                        "candidates": (self._candidate(),),
                        "locked_decisions": (locked,),
                    }
                )
            )

    def test_apply_result_is_closed_versioned_and_requires_valid_hashes_and_counts(self):
        result = self._contract("B2B1BackfillApplyResultV1")
        base = {
            "schema_version": 1,
            "plan_sha256": "a" * 64,
            "before_invariants_sha256": "b" * 64,
            "after_invariants_sha256": "c" * 64,
            "created_review_items": 2,
            "created_decisions": 1,
            "created_overrides": 2,
            "created_identities": 1,
            "created_aliases": 1,
            "created_audit_events": 7,
            "eligible_products_before": 2,
            "eligible_products_after": 2,
        }
        validated = result(**base)
        self.assertEqual(validated.schema_version, 1)
        with self.assertRaises(ValidationError):
            result(**(base | {"plan_sha256": "A" * 64}))
        with self.assertRaises(ValidationError):
            result(**(base | {"created_review_items": -1}))
        with self.assertRaises(ValidationError):
            result(**(base | {"unexpected": True}))


if __name__ == "__main__":
    unittest.main()
