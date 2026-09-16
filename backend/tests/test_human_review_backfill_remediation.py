from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.models.entities import PersonRole, ResearchEntity, ResearchProject
from app.models.human_review_enums import ReviewCaseType
from app.schemas import human_review_operations as contracts
from app.services import human_review_backfill as service
from app.services.human_review_backfill import backfill_plan_sha256
from app.services.human_review_targets import build_stable_target_key
from scripts import backfill_human_review_b2b1 as cli
from tests.support.backfill_plan import (
    synthetic_backfill_plan,
    synthetic_legacy_backfill_plan_json,
)


class HumanReviewBackfillRemediationContractTests(unittest.TestCase):
    def test_project_evidence_hard_blocker_codes_are_closed_contract_values(self) -> None:
        for code in (
            "ambiguous_project_evidence",
            "contradictory_project_evidence",
        ):
            with self.subTest(code=code):
                blocker = contracts.BackfillBlockerV1(
                    source_table="research_entities",
                    source_id=1,
                    code=code,
                    message="project evidence cannot be materialized safely",
                )
                self.assertEqual(blocker.code, code)

    def test_deferred_record_contract_exists_with_exact_required_fields(self) -> None:
        self.assertTrue(
            hasattr(contracts, "BackfillDeferredRecordV1"),
            "BackfillDeferredRecordV1 is absent",
        )
        contract = contracts.BackfillDeferredRecordV1
        self.assertEqual(
            tuple(contract.model_fields),
            (
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
        )

    def test_excluded_record_contract_exists_with_exact_required_fields(self) -> None:
        self.assertTrue(
            hasattr(contracts, "BackfillExcludedRecordV1"),
            "BackfillExcludedRecordV1 is absent",
        )
        contract = contracts.BackfillExcludedRecordV1
        self.assertEqual(
            tuple(contract.model_fields),
            (
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
        )

    def test_plan_contract_exposes_additive_nonmaterializable_collections(self) -> None:
        fields = contracts.B2B1BackfillPlanV1.model_fields
        self.assertIn("deferred_records", fields)
        self.assertIn("excluded_records", fields)
        self.assertIn("hard_blockers", fields)

    def test_historical_plan_interprets_absent_additive_fields_as_empty(self) -> None:
        historical = contracts.B2B1BackfillPlanV1.model_validate_json(
            synthetic_legacy_backfill_plan_json()
        )
        self.assertEqual(getattr(historical, "deferred_records", None), ())
        self.assertEqual(getattr(historical, "excluded_records", None), ())
        self.assertEqual(getattr(historical, "hard_blockers", None), historical.blockers)

    def test_additive_contracts_are_frozen_closed_and_enforce_fixed_dispositions(self) -> None:
        deferred = self._deferred()
        excluded = self._excluded()
        self.assertTrue(deferred.model_config["frozen"])
        self.assertEqual(deferred.model_config["extra"], "forbid")
        self.assertTrue(excluded.model_config["frozen"])
        self.assertEqual(excluded.model_config["extra"], "forbid")
        with self.assertRaises(ValidationError):
            deferred.source_pk = 99
        with self.assertRaises(ValidationError):
            contracts.BackfillDeferredRecordV1.model_validate(
                deferred.model_dump(mode="python") | {"materializable": True}
            )
        with self.assertRaises(ValidationError):
            contracts.BackfillExcludedRecordV1.model_validate(
                excluded.model_dump(mode="python") | {"entity_type": "proyecto_fci"}
            )

    def test_round_trip_preserves_additive_contracts_and_hard_blockers(self) -> None:
        historical = synthetic_backfill_plan()
        material = historical.model_dump(mode="python") | {
            "deferred_records": (self._deferred(),),
            "excluded_records": (self._excluded(),),
            "hard_blockers": historical.blockers,
        }
        plan = contracts.B2B1BackfillPlanV1.model_validate(material)
        restored = contracts.B2B1BackfillPlanV1.model_validate_json(
            plan.model_dump_json()
        )
        self.assertEqual(restored, plan)
        self.assertEqual(restored.deferred_records, (self._deferred(),))
        self.assertEqual(restored.excluded_records, (self._excluded(),))
        self.assertEqual(restored.hard_blockers, restored.blockers)

    def test_deferred_and_excluded_records_participate_in_productive_hash(self) -> None:
        historical = synthetic_backfill_plan()
        baseline = backfill_plan_sha256(historical)
        deferred = contracts.B2B1BackfillPlanV1.model_validate(
            historical.model_dump(mode="python")
            | {"deferred_records": (self._deferred(),)}
        )
        excluded = contracts.B2B1BackfillPlanV1.model_validate(
            historical.model_dump(mode="python")
            | {"excluded_records": (self._excluded(),)}
        )
        self.assertNotEqual(backfill_plan_sha256(deferred), baseline)
        self.assertNotEqual(backfill_plan_sha256(excluded), baseline)
        shifted = historical.model_copy(
            update={"captured_at": historical.captured_at.replace(microsecond=1)}
        )
        self.assertEqual(backfill_plan_sha256(shifted), baseline)

    def test_missing_row_locator_person_role_becomes_a_typed_deferred_record(self) -> None:
        row = self._role()
        deferred = service._deferred_person_role(row)
        self.assertEqual(deferred.source_pk, row.id)
        self.assertEqual(deferred.document_key, row.source_file)
        self.assertEqual(deferred.period_id, row.period_id)
        self.assertEqual(deferred.identity_reference, row.canonical_identity_key)
        self.assertEqual(deferred.reason_code, "missing_row_locator")
        self.assertFalse(deferred.materializable)
        self.assertFalse(deferred.affects_kpi)
        self.assertIsNone(
            service._stable_target(row, ReviewCaseType.PERSON_IDENTITY)
        )

    def test_person_role_is_not_deferred_when_any_required_context_is_missing(self) -> None:
        for field in ("source_file", "source_section", "period_id"):
            with self.subTest(field=field):
                row = self._role()
                setattr(row, field, None)
                self.assertIsNone(service._deferred_person_role(row))
        row = self._role(canonical_identity_key=None, person_key=None)
        self.assertIsNone(service._deferred_person_role(row))

    def test_only_exact_group_and_seedbed_types_are_excluded(self) -> None:
        for entity_type in ("grupo_investigacion", "semillero"):
            with self.subTest(entity_type=entity_type):
                excluded = service._excluded_director_record(
                    self._entity(entity_type=entity_type)
                )
                self.assertEqual(excluded.entity_type, entity_type)
                self.assertEqual(excluded.reason_code, "entity_type_incompatible")
                self.assertFalse(excluded.affects_kpi)
        for entity_type in ("proyecto_fci", "project", "grupo-investigacion"):
            with self.subTest(entity_type=entity_type):
                self.assertIsNone(
                    service._excluded_director_record(
                        self._entity(entity_type=entity_type)
                    )
                )

    def test_exact_external_project_evidence_keeps_priority_over_self_evidence(self) -> None:
        entity = self._entity()
        external = self._project(project_id=7)
        resolution = service._resolve_project_evidence(
            entity,
            entities=(entity,),
            projects=(external,),
        )
        self.assertIsNone(resolution.blocker_code)
        self.assertEqual(resolution.evidence.evidence_source, "research_projects")
        self.assertEqual(resolution.evidence.row.id, external.id)

    def test_multiple_external_matches_are_a_hard_blocker_and_never_use_self(self) -> None:
        entity = self._entity()
        resolution = service._resolve_project_evidence(
            entity,
            entities=(entity,),
            projects=(self._project(project_id=7), self._project(project_id=8)),
        )
        self.assertIsNone(resolution.evidence)
        self.assertEqual(resolution.blocker_code, "ambiguous_project_evidence")

    def test_zero_external_matches_allows_complete_project_fci_self_evidence(self) -> None:
        entity = self._entity()
        resolution = service._resolve_project_evidence(
            entity,
            entities=(entity,),
            projects=(),
        )
        self.assertIsNone(resolution.blocker_code)
        self.assertEqual(resolution.evidence.evidence_source, "research_entity_self")
        self.assertEqual(resolution.evidence.row.id, entity.id)

    def test_self_evidence_requires_the_existing_stable_entity_locator(self) -> None:
        entity = self._entity(metadata_json={}, code=None, name=None)
        resolution = service._resolve_project_evidence(
            entity,
            entities=(entity,),
            projects=(),
        )
        self.assertIsNone(resolution.evidence)
        self.assertEqual(resolution.blocker_code, "missing_stable_locator")

    def test_duplicate_self_evidence_code_is_a_hard_blocker(self) -> None:
        entity = self._entity(entity_id=1)
        duplicate = self._entity(entity_id=2)
        resolution = service._resolve_project_evidence(
            entity,
            entities=(duplicate, entity),
            projects=(),
        )
        self.assertIsNone(resolution.evidence)
        self.assertEqual(resolution.blocker_code, "ambiguous_project_evidence")

    def test_duplicate_self_evidence_name_without_code_is_a_hard_blocker(self) -> None:
        entity = self._entity(entity_id=1, code=None)
        duplicate = self._entity(entity_id=2, code=None)
        resolution = service._resolve_project_evidence(
            entity,
            entities=(entity, duplicate),
            projects=(),
        )
        self.assertIsNone(resolution.evidence)
        self.assertEqual(resolution.blocker_code, "ambiguous_project_evidence")

    def test_code_and_name_contradiction_is_a_hard_blocker(self) -> None:
        entity = self._entity()
        contradictory = self._project(project_id=7, name="Contradictory name")
        resolution = service._resolve_project_evidence(
            entity,
            entities=(entity,),
            projects=(contradictory,),
        )
        self.assertIsNone(resolution.evidence)
        self.assertEqual(resolution.blocker_code, "contradictory_project_evidence")

    def test_group_and_seedbed_never_use_project_self_evidence(self) -> None:
        for entity_type in ("grupo_investigacion", "semillero"):
            with self.subTest(entity_type=entity_type):
                entity = self._entity(entity_type=entity_type)
                resolution = service._resolve_project_evidence(
                    entity,
                    entities=(entity,),
                    projects=(),
                )
                self.assertIsNone(resolution.evidence)
                self.assertEqual(resolution.blocker_code, "entity_type_incompatible")

    def test_self_evidence_stable_target_is_deterministic_and_order_independent(self) -> None:
        entity = self._entity()
        unrelated = self._entity(entity_id=2, code="FCI-OTHER", name="Other")
        first = service._resolve_project_evidence(
            entity,
            entities=(entity, unrelated),
            projects=(),
        )
        second = service._resolve_project_evidence(
            entity,
            entities=(unrelated, entity),
            projects=(),
        )
        first_target = service._stable_target(
            entity,
            ReviewCaseType.PROJECT_DIRECTOR_RELATION,
            first.evidence,
        )
        second_target = service._stable_target(
            entity,
            ReviewCaseType.PROJECT_DIRECTOR_RELATION,
            second.evidence,
        )
        self.assertEqual(
            build_stable_target_key(first_target),
            build_stable_target_key(second_target),
        )

    def test_self_evidence_stable_target_changes_with_period_document_or_locator(self) -> None:
        baseline = self._entity()
        variants = (
            self._entity(period_id=4),
            self._entity(source_file="document:other"),
            self._entity(metadata_json={"row_or_block_id": "entity:other"}),
        )
        baseline_resolution = service._resolve_project_evidence(
            baseline,
            entities=(baseline,),
            projects=(),
        )
        baseline_key = build_stable_target_key(service._stable_target(
            baseline,
            ReviewCaseType.PROJECT_DIRECTOR_RELATION,
            baseline_resolution.evidence,
        ))
        for variant in variants:
            with self.subTest(variant=variant):
                resolution = service._resolve_project_evidence(
                    variant,
                    entities=(variant,),
                    projects=(),
                )
                target = service._stable_target(
                    variant,
                    ReviewCaseType.PROJECT_DIRECTOR_RELATION,
                    resolution.evidence,
                )
                self.assertNotEqual(build_stable_target_key(target), baseline_key)

    def test_plan_gate_accepts_deferred_and_excluded_records_with_zero_hard_blockers(self) -> None:
        historical = synthetic_backfill_plan()
        material = historical.model_dump(mode="python") | {
            "blockers": (),
            "hard_blockers": (),
            "deferred_records": (self._deferred(),),
            "excluded_records": (self._excluded(),),
        }
        plan = contracts.B2B1BackfillPlanV1.model_validate(material)
        cli._require_no_hard_blockers(plan)
        self.assertEqual(
            cli._plan_summary(plan),
            {
                "materializable_candidates": len(plan.candidates),
                "stable_targets": plan.union_stable_target_count,
                "deferred_records": 1,
                "excluded_records": 1,
                "hard_blockers": 0,
            },
        )

    def test_plan_gate_rejects_one_hard_blocker(self) -> None:
        historical = synthetic_backfill_plan()
        with self.assertRaisesRegex(contracts.BackfillGateError, "hard blockers"):
            cli._require_no_hard_blockers(historical)

    @staticmethod
    def _role(**changes):
        values = {
            "id": 2361,
            "period_id": 3,
            "role_type": "director",
            "person_type": "pendiente_clasificacion",
            "person_key": "person:pending:2361",
            "canonical_identity_key": "pending:person:2361",
            "canonical_name": "Pending Person",
            "identity_locked": False,
            "raw_name": "Pending Person",
            "normalized_name": "pending person",
            "source_file": "document:participants",
            "source_section": "participants",
            "metadata_json": {},
            "validation_status": "pending_review",
        }
        values.update(changes)
        return PersonRole(**values)

    @staticmethod
    def _entity(**changes):
        values = {
            "id": 128,
            "period_id": 3,
            "type": "proyecto_fci",
            "code": "FCI-043",
            "normalized_code": "fci-043",
            "name": "Project FCI 043",
            "normalized_name": "project fci 043",
            "director_name": "Director Pending",
            "normalized_director_name": "director pending",
            "validation_status": "validated",
            "source_file": "document:research",
            "source_section": "research_entities",
            "metadata_json": {"row_or_block_id": "entity:fci-043"},
        }
        aliases = {
            "entity_id": "id",
            "entity_type": "type",
        }
        for key, value in changes.items():
            values[aliases.get(key, key)] = value
        if "code" in changes and "normalized_code" not in changes:
            values["normalized_code"] = changes["code"].casefold() if changes["code"] else None
        if "name" in changes and "normalized_name" not in changes:
            values["normalized_name"] = changes["name"].casefold() if changes["name"] else None
        return ResearchEntity(**values)

    @staticmethod
    def _project(*, project_id: int, **changes):
        values = {
            "id": project_id,
            "period_id": 3,
            "name": "Project FCI 043",
            "raw_project_name": "Project FCI 043",
            "normalized_project_name": "project fci 043",
            "raw_code": "FCI-043",
            "normalized_code": "fci-043",
            "source_file": "document:research-project",
            "source_section": "research_projects",
        }
        values.update(changes)
        if "name" in changes and "normalized_project_name" not in changes:
            values["normalized_project_name"] = changes["name"].casefold() if changes["name"] else None
        return ResearchProject(**values)

    @staticmethod
    def _deferred():
        return contracts.BackfillDeferredRecordV1(
            source_table="person_roles",
            source_pk=999_991,
            source_population="canonical_pending",
            case_type="person_identity",
            period_id=3,
            document_key="document:sha256:example",
            identity_reference="pending:identity:example",
            reason_code="missing_row_locator",
            disposition="expected_pending_review",
            materializable=False,
            affects_kpi=False,
            evidence_summary="document and section retained; stable row locator absent",
        )

    @staticmethod
    def _excluded():
        return contracts.BackfillExcludedRecordV1(
            source_table="research_entities",
            research_entity_id=999_992,
            source_population="director_relation_pending",
            case_type="project_director_relation",
            period_id=3,
            entity_type="grupo_investigacion",
            reason_code="entity_type_incompatible",
            disposition="out_of_scope_record",
            materializable=False,
            affects_kpi=False,
            evidence_summary="entity type is outside director_relation_pending",
        )

if __name__ == "__main__":
    unittest.main()
