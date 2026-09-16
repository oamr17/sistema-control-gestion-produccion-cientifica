from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.schemas.human_review_operations import B2B1BackfillPlanV1
from app.services.human_review_backfill import backfill_plan_sha256
from scripts import diagnose_human_review_backfill_blockers as diagnostic
from tests.support.backfill_plan import synthetic_backfill_plan


class DiagnoseHumanReviewBackfillBlockersTests(unittest.TestCase):
    def test_historical_artifact_keeps_exact_approved_hash(self) -> None:
        with self._approved_synthetic_artifact() as (path, _, _):
            proof = diagnostic.verify_historical_artifact(path)
            self.assertEqual(
                proof["historical_artifact_sha256"],
                diagnostic.HISTORICAL_ARTIFACT_SHA256,
            )

    def test_changing_any_historical_artifact_byte_changes_hash(self) -> None:
        with self._approved_synthetic_artifact() as (_, _, original):
            changed = original[:-1] + bytes([original[-1] ^ 1])
            self.assertNotEqual(
                hashlib.sha256(changed).hexdigest(),
                diagnostic.HISTORICAL_ARTIFACT_SHA256,
            )

    def test_different_captured_at_keeps_productive_semantic_hash(self) -> None:
        historical = synthetic_backfill_plan()
        reproduced = historical.model_copy(
            update={"captured_at": historical.captured_at + timedelta(seconds=1)}
        )
        self.assertEqual(
            diagnostic.productive_plan_sha256(historical),
            diagnostic.productive_plan_sha256(reproduced),
        )
        with patch.object(
            diagnostic,
            "HISTORICAL_PLAN_HASH_UNDER_V3",
            diagnostic.productive_plan_sha256(historical),
        ):
            self.assertEqual(
                diagnostic.productive_plan_sha256(reproduced),
                diagnostic.HISTORICAL_PLAN_HASH_UNDER_V3,
            )

    def test_semantic_difference_changes_productive_hash(self) -> None:
        historical = synthetic_backfill_plan()
        material = historical.model_dump(mode="json")
        material["blockers"][0]["message"] += " with semantic detail"
        material["hard_blockers"][0]["message"] += " with semantic detail"
        changed = B2B1BackfillPlanV1.model_validate(material)
        self.assertNotEqual(
            diagnostic.productive_plan_sha256(historical),
            diagnostic.productive_plan_sha256(changed),
        )

    def test_legacy_and_v2_hashes_are_distinct_frozen_evidence(self) -> None:
        self.assertEqual(
            diagnostic.LEGACY_PLAN_HASH_V1,
            "9d12a196431683746850ef1403d57c2c0b6431dc0bd54d2e0191bd9c57624948",
        )
        self.assertEqual(
            diagnostic.HISTORICAL_SEMANTIC_PLAN_HASH_V2,
            "f63a204c84e3df03ef07d175b3dedb652516180fcd63b624bc8e2ab3885685e2",
        )
        self.assertNotEqual(
            diagnostic.LEGACY_PLAN_HASH_V1,
            diagnostic.HISTORICAL_SEMANTIC_PLAN_HASH_V2,
        )

    def test_historical_v2_evidence_is_separate_from_productive_v3_hash(self) -> None:
        with self._approved_synthetic_artifact() as (path, _, _):
            proof = diagnostic.verify_historical_artifact(path)
            self.assertEqual(
                proof["historical_semantic_plan_hash_v2"],
                diagnostic.HISTORICAL_SEMANTIC_PLAN_HASH_V2,
            )
            self.assertEqual(
                proof["historical_plan_hash_under_v3"],
                diagnostic.HISTORICAL_PLAN_HASH_UNDER_V3,
            )
            self.assertNotEqual(
                proof["historical_semantic_plan_hash_v2"],
                proof["historical_plan_hash_under_v3"],
            )

    def test_diagnostic_uses_productive_hash_function_directly(self) -> None:
        self.assertIs(diagnostic.productive_plan_sha256, backfill_plan_sha256)

    def test_new_artifact_byte_hash_may_differ_when_only_time_differs(self) -> None:
        historical = synthetic_backfill_plan()
        reproduced = historical.model_copy(
            update={"captured_at": historical.captured_at + timedelta(seconds=2)}
        )
        with patch.object(
            diagnostic,
            "HISTORICAL_PLAN_HASH_UNDER_V3",
            diagnostic.productive_plan_sha256(historical),
        ):
            proof = diagnostic.verify_semantic_reproduction(historical, reproduced)
            self.assertTrue(proof["semantic_match"])
            self.assertFalse(proof["artifact_byte_match"])
            self.assertEqual(proof["semantic_differences"], ["captured_at"])

    def test_any_non_temporal_difference_fails_semantic_gate(self) -> None:
        historical = synthetic_backfill_plan()
        material = historical.model_dump(mode="json")
        material["blockers"][0]["message"] += " with semantic detail"
        material["hard_blockers"][0]["message"] += " with semantic detail"
        changed = B2B1BackfillPlanV1.model_validate(material)
        with self.assertRaisesRegex(diagnostic.DiagnosticError, "non-temporal"):
            diagnostic.verify_semantic_reproduction(historical, changed)

    def test_exact_reproduction_accepts_approved_counts_and_hash(self) -> None:
        diagnostic.verify_reproduction(
            candidate_count=74,
            stable_target_count=67,
            blocker_counts={"missing_stable_locator": 19, "invariant_mismatch": 7},
            plan_sha256=diagnostic.APPROVED_PLAN_SHA256,
        )

    def test_exact_reproduction_rejects_any_additional_blocker(self) -> None:
        with self.assertRaisesRegex(diagnostic.DiagnosticError, "exact reproduction"):
            diagnostic.verify_reproduction(
                candidate_count=74,
                stable_target_count=67,
                blocker_counts={
                    "missing_stable_locator": 19,
                    "invariant_mismatch": 7,
                    "ambiguous_alias": 1,
                },
                plan_sha256=diagnostic.APPROVED_PLAN_SHA256,
            )

    def test_person_role_reason_code_reports_missing_document(self) -> None:
        result = diagnostic.diagnose_person_role(
            self._role(document=False, section=True, row_locator=True),
            self._related(),
        )
        self.assertEqual(result["reason_code"], "missing_source_document")

    def test_person_role_identifies_locator_present_but_not_consumed(self) -> None:
        related = self._related(metadata_locator_paths=("semantic.block_key",))
        result = diagnostic.diagnose_person_role(
            self._role(document=True, section=True, row_locator=False),
            related,
        )
        self.assertEqual(result["reason_code"], "locator_available_but_not_consumed")
        self.assertEqual(result["classification"], "DERIVATION_GAP")

    def test_person_role_name_only_source_match_is_not_a_stable_locator(self) -> None:
        related = self._related(
            source_record_matches=0,
            name_only_source_record_matches=1,
        )
        result = diagnostic.diagnose_person_role(
            self._role(document=True, section=True, row_locator=False),
            related,
        )
        self.assertEqual(result["reason_code"], "missing_row_locator")
        self.assertEqual(result["classification"], "EXPECTED_PENDING_REVIEW")
        self.assertEqual(result["name_only_source_record_matches"], 1)

    def test_ctid_is_forbidden_as_locator(self) -> None:
        with self.assertRaisesRegex(diagnostic.DiagnosticError, "unstable locator"):
            diagnostic.validate_locator_candidate("ctid")

    def test_physical_row_order_is_forbidden_as_locator(self) -> None:
        for candidate in ("row_number:7", "physical_order:7", "array_index:3"):
            with self.subTest(candidate=candidate):
                with self.assertRaisesRegex(diagnostic.DiagnosticError, "unstable locator"):
                    diagnostic.validate_locator_candidate(candidate)

    def test_person_roles_group_by_identity_document_and_period(self) -> None:
        rows = [
            {"identity_group_key": "i1", "document_group_key": "d1", "period_id": 1},
            {"identity_group_key": "i1", "document_group_key": "d1", "period_id": 1},
            {"identity_group_key": "i2", "document_group_key": "d2", "period_id": 2},
        ]
        groups = diagnostic.group_person_role_diagnostics(rows)
        self.assertEqual(groups["unique_rows"], 3)
        self.assertEqual(groups["affected_identities"], 2)
        self.assertEqual(groups["affected_documents"], 2)
        self.assertEqual(groups["affected_periods"], 2)

    def test_project_matrix_records_matching_and_nonmatching_criteria(self) -> None:
        entity = self._entity(code="P-1", name="Project One", period_id=1)
        projects = [
            self._project(10, code="P-1", name="Different", period_id=1),
            self._project(11, code="P-2", name="Project One", period_id=2),
        ]
        result = diagnostic.correlate_director_relation(entity, projects)
        self.assertEqual(
            result["matrix"][0]["matching_criteria"],
            ["code", "document", "period", "section"],
        )
        self.assertIn("name", result["matrix"][0]["nonmatching_criteria"])
        self.assertIn("period", result["matrix"][1]["nonmatching_criteria"])
        self.assertEqual(
            result["criterion_match_counts"],
            {
                "exact_normalized_code": 1,
                "exact_normalized_name": 1,
                "exact_code_and_name": 0,
                "same_period": 1,
                "same_document": 2,
                "same_section": 2,
            },
        )

    def test_project_correlation_detects_zero_candidates(self) -> None:
        result = diagnostic.correlate_director_relation(
            self._entity(code="P-1", name="One", period_id=1),
            [self._project(10, code="P-2", name="Two", period_id=1)],
        )
        self.assertEqual(result["cardinality_result"], "ZERO_CANDIDATES")

    def test_project_correlation_detects_multiple_candidates(self) -> None:
        result = diagnostic.correlate_director_relation(
            self._entity(code="P-1", name="One", period_id=1),
            [
                self._project(10, code="P-1", name="Other", period_id=1),
                self._project(11, code="Other", name="One", period_id=1),
            ],
        )
        self.assertEqual(result["cardinality_result"], "MULTIPLE_CANDIDATES")
        self.assertEqual(result["classification"], "LEGITIMATE_AMBIGUITY")

    def test_project_correlation_detects_one_deterministic_candidate(self) -> None:
        result = diagnostic.correlate_director_relation(
            self._entity(code="P-1", name="One", period_id=1),
            [self._project(10, code="P-1", name="Other", period_id=1)],
        )
        self.assertEqual(result["cardinality_result"], "ONE_DETERMINISTIC_CANDIDATE")
        self.assertEqual(result["candidate_project_ids"], [10])

    def test_fci_project_without_same_period_project_evidence_is_contract_mismatch(self) -> None:
        entity = self._entity(code="FCI-043", name="Project", period_id=3)
        entity["type"] = "proyecto_fci"
        result = diagnostic.correlate_director_relation(
            entity,
            [self._project(10, code="FCI-043", name="Project", period_id=4)],
        )
        self.assertEqual(result["cardinality_result"], "ZERO_CANDIDATES")
        self.assertEqual(result["reason_code"], "missing_same_period_project_evidence")
        self.assertEqual(result["classification"], "CONTRACT_MISMATCH")
        self.assertEqual(result["recommended_option"], "B")

    def test_group_and_seedbed_entity_types_are_outside_project_population(self) -> None:
        for entity_type in ("grupo_investigacion", "semillero"):
            with self.subTest(entity_type=entity_type):
                entity = self._entity(code="E-1", name="Entity", period_id=1)
                entity["type"] = entity_type
                result = diagnostic.correlate_director_relation(entity, [])
                self.assertEqual(result["cardinality_result"], "OUT_OF_SCOPE")
                self.assertEqual(result["classification"], "OUT_OF_SCOPE_RECORD")
                self.assertEqual(result["recommended_option"], "D")

    def test_each_case_must_have_one_closed_classification(self) -> None:
        record = {"classification": "DERIVATION_GAP"}
        diagnostic.validate_classification(record)
        with self.assertRaisesRegex(diagnostic.DiagnosticError, "classification"):
            diagnostic.validate_classification(
                {"classification": ["DERIVATION_GAP", "SOURCE_DATA_DEFECT"]}
            )

    def test_proposal_is_declarative_and_does_not_authorize_mutation(self) -> None:
        diagnostic.validate_remediation_proposal(
            {
                "option": "B",
                "execution_authorized": False,
                "affected_components": ["human_review_backfill._row_or_block_id"],
            }
        )
        with self.assertRaisesRegex(diagnostic.DiagnosticError, "mutation"):
            diagnostic.validate_remediation_proposal(
                {"option": "A", "execution_authorized": True}
            )

    def test_remediation_lists_only_components_supported_by_each_option(self) -> None:
        markdown = diagnostic._remediation_markdown(
            [
                {
                    "case_key": "research_entities:128:invariant_mismatch",
                    "recommended_option": "B",
                    "affected_components": [
                        "app.services.human_review_backfill._matching_project_evidence",
                        "app.services.human_review_backfill._discover director population rule",
                    ],
                },
                {
                    "case_key": "research_entities:130:invariant_mismatch",
                    "recommended_option": "D",
                    "affected_components": [
                        "app.services.human_review_backfill._discover director population rule",
                    ],
                },
                {
                    "case_key": "person_roles:2361:missing_stable_locator",
                    "recommended_option": "C",
                    "affected_components": [],
                },
            ]
        )
        option_b = markdown.split("## OPCIÓN B", 1)[1].split("## OPCIÓN C", 1)[0]
        option_c = markdown.split("## OPCIÓN C", 1)[1].split("## OPCIÓN D", 1)[0]
        self.assertIn("_matching_project_evidence", option_b)
        self.assertIn("_discover director population rule", option_b)
        self.assertNotIn("_row_or_block_id", option_b)
        self.assertIn("Nueva migración: no", option_b)
        self.assertIn("Snapshots del plan: sí", option_b)
        self.assertIn("Fórmula de stable keys: no", option_b)
        self.assertIn("Contrato de auditoría: no", option_b)
        self.assertIn("Mecanismo de idempotencia: no", option_b)
        self.assertIn("Casos condicionados: 1", option_c)
        self.assertIn("Materializables ahora: 0", option_c)
        self.assertNotIn("Blockers: 1", option_c)
        self.assertIn(
            "CONTRACT_MISMATCH demostrado",
            markdown,
        )

    def test_sql_guard_rejects_insert_update_delete_and_ddl(self) -> None:
        for statement in (
            "INSERT INTO review_items VALUES (1)",
            "UPDATE person_roles SET raw_name = 'x'",
            "DELETE FROM research_entities",
            "CREATE TABLE forbidden(id integer)",
            "ALTER TABLE person_roles ADD COLUMN forbidden integer",
            "DROP TABLE person_roles",
        ):
            with self.subTest(statement=statement):
                with self.assertRaisesRegex(diagnostic.DiagnosticError, "read-only"):
                    diagnostic.ensure_read_only_sql(statement)

    def test_sql_guard_accepts_select_and_read_only_transaction_control(self) -> None:
        for statement in (
            "SELECT * FROM person_roles",
            "SET TRANSACTION READ ONLY",
            "SHOW server_version",
        ):
            with self.subTest(statement=statement):
                diagnostic.ensure_read_only_sql(statement)

    def test_database_fingerprint_change_is_rejected(self) -> None:
        with self.assertRaisesRegex(diagnostic.DiagnosticError, "database changed"):
            diagnostic.verify_database_unchanged("before", "after")

    def test_secret_values_are_sanitized(self) -> None:
        value = {
            "password": "secret",
            "database_url": "postgresql://user:pass@example/db",
            "safe": "value",
        }
        sanitized = diagnostic.sanitize(value)
        self.assertEqual(sanitized["password"], "[REDACTED]")
        self.assertEqual(sanitized["database_url"], "[REDACTED]")
        self.assertEqual(sanitized["safe"], "value")

    def test_complete_report_requires_exactly_26_unique_cases(self) -> None:
        cases = [
            {
                "case_key": f"case:{index}",
                "classification": "EXPECTED_PENDING_REVIEW",
                "evidence": ["technical evidence"],
                "confidence": "medium",
                "recommended_action": "human review",
                "risk": "low",
                "affected_components": [],
                "human_decision_required": True,
            }
            for index in range(26)
        ]
        diagnostic.validate_complete_case_set(cases)
        with self.assertRaisesRegex(diagnostic.DiagnosticError, "26 unique cases"):
            diagnostic.validate_complete_case_set(cases[:-1])

    def test_artifact_writer_produces_stable_bytes(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            first = Path(temporary_directory) / "first.json"
            second = Path(temporary_directory) / "second.json"
            value = {"cases": [{"id": 2}, {"id": 1}]}
            diagnostic.write_json(first, value)
            diagnostic.write_json(second, value)
            self.assertEqual(first.read_bytes(), second.read_bytes())

    @staticmethod
    @contextmanager
    def _approved_synthetic_artifact():
        plan = synthetic_backfill_plan()
        artifact = diagnostic._artifact_bytes(plan)
        artifact_sha256 = hashlib.sha256(artifact).hexdigest()
        semantic_sha256 = diagnostic.productive_plan_sha256(plan)
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "synthetic-approved-plan.json"
            path.write_bytes(artifact)
            with patch.multiple(
                diagnostic,
                HISTORICAL_ARTIFACT_SHA256=artifact_sha256,
                HISTORICAL_PLAN_HASH_UNDER_V3=semantic_sha256,
            ):
                yield path, plan, artifact

    @staticmethod
    def _role(
        *,
        document: bool,
        section: bool,
        row_locator: bool,
    ) -> dict[str, object]:
        return {
            "id": 1,
            "period_id": 1,
            "import_job_id": 1,
            "document_key": "document" if document else None,
            "source_section": "section" if section else None,
            "row_or_block_id": "row:key" if row_locator else None,
            "person_key": "person:1",
            "canonical_identity_key": "pending:1",
            "canonical_name_present": True,
            "raw_name_present": True,
            "normalized_name_present": True,
            "role_type": "director",
        }

    @staticmethod
    def _related(**changes: object) -> dict[str, object]:
        value: dict[str, object] = {
            "metadata_locator_paths": (),
            "ocr_locator_paths": (),
            "source_record_matches": 0,
            "conflicting_identity_fields": False,
        }
        value.update(changes)
        return value

    @staticmethod
    def _entity(*, code: str | None, name: str | None, period_id: int) -> dict[str, object]:
        return {
            "id": 1,
            "period_id": period_id,
            "type": "project",
            "code": code,
            "name": name,
            "document_key": "document",
            "source_section": "projects",
        }

    @staticmethod
    def _project(
        project_id: int,
        *,
        code: str | None,
        name: str | None,
        period_id: int,
    ) -> dict[str, object]:
        return {
            "id": project_id,
            "period_id": period_id,
            "code": code,
            "name": name,
            "document_key": "document",
            "source_section": "projects",
        }


if __name__ == "__main__":
    unittest.main()
