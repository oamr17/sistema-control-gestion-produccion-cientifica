from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import verify_human_review_b2b1 as verifier


class VerifyHumanReviewArtifactsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)

    def _paths(self) -> tuple[Path, Path, Path]:
        return (
            self.root / "backup.dump",
            self.root / "backup.dump.sha256",
            self.root / "backup.toc.txt",
        )

    def _write_valid_artifacts(self) -> tuple[Path, Path, Path]:
        backup, sha256_path, toc = self._paths()
        backup.write_bytes(b"PGDMP\x01task13")
        sha256_path.write_text(
            hashlib.sha256(backup.read_bytes()).hexdigest(),
            encoding="ascii",
        )
        toc.write_text(self._valid_toc(), encoding="utf-8")
        return backup, sha256_path, toc

    @staticmethod
    def _valid_toc() -> str:
        return "\n".join(
            f"{index}; 1259 {16000 + index} TABLE public {table_name} postgres"
            for index, table_name in enumerate(verifier.REQUIRED_TOC_TABLES, 1)
        )

    def test_backup_absent_is_rejected(self) -> None:
        _backup, sha256_path, toc = self._paths()
        with self.assertRaisesRegex(
            verifier.VerificationError,
            "backup artifact is required",
        ):
            verifier.verify_backup_artifacts(None, sha256_path, toc)

    def test_sha256_file_absent_is_rejected(self) -> None:
        backup, sha256_path, toc = self._write_valid_artifacts()
        sha256_path.unlink()
        with self.assertRaisesRegex(verifier.VerificationError, "SHA-256 artifact"):
            verifier.verify_backup_artifacts(backup, sha256_path, toc)

    def test_toc_absent_is_rejected(self) -> None:
        backup, sha256_path, toc = self._write_valid_artifacts()
        toc.unlink()
        with self.assertRaisesRegex(verifier.VerificationError, "TOC artifact"):
            verifier.verify_backup_artifacts(backup, sha256_path, toc)

    def test_empty_backup_is_rejected(self) -> None:
        backup, sha256_path, toc = self._write_valid_artifacts()
        backup.write_bytes(b"")
        sha256_path.write_text(hashlib.sha256(b"").hexdigest(), encoding="ascii")
        with self.assertRaisesRegex(verifier.VerificationError, "backup artifact is empty"):
            verifier.verify_backup_artifacts(backup, sha256_path, toc)

    def test_wrong_backup_hash_is_rejected(self) -> None:
        backup, sha256_path, toc = self._write_valid_artifacts()
        sha256_path.write_text("0" * 64, encoding="ascii")
        with self.assertRaisesRegex(verifier.VerificationError, "backup SHA-256 mismatch"):
            verifier.verify_backup_artifacts(backup, sha256_path, toc)

    def test_toc_without_schema_migrations_is_rejected(self) -> None:
        backup, sha256_path, toc = self._write_valid_artifacts()
        toc.write_text(
            self._valid_toc().replace("TABLE public schema_migrations ", "TABLE public absent "),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verifier.VerificationError, "schema_migrations"):
            verifier.verify_backup_artifacts(backup, sha256_path, toc)

    def test_toc_without_protected_scientific_table_is_rejected(self) -> None:
        backup, sha256_path, toc = self._write_valid_artifacts()
        toc.write_text(
            self._valid_toc().replace(
                "TABLE public scientific_productions ",
                "TABLE public absent_science ",
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verifier.VerificationError, "scientific_productions"):
            verifier.verify_backup_artifacts(backup, sha256_path, toc)

    def test_toc_accepts_physical_academic_periods_table(self) -> None:
        backup, sha256_path, toc = self._write_valid_artifacts()
        proof = verifier.verify_backup_artifacts(backup, sha256_path, toc)
        self.assertIn("academic_periods", proof.toc_tables)

    def test_toc_rejects_missing_physical_academic_periods_table(self) -> None:
        backup, sha256_path, toc = self._write_valid_artifacts()
        toc.write_text(
            self._valid_toc().replace(
                "TABLE public academic_periods ",
                "TABLE public absent_periods ",
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(verifier.VerificationError, "academic_periods"):
            verifier.verify_backup_artifacts(backup, sha256_path, toc)

    def test_toc_does_not_require_literal_periods_table(self) -> None:
        backup, sha256_path, toc = self._write_valid_artifacts()
        proof = verifier.verify_backup_artifacts(backup, sha256_path, toc)
        self.assertNotIn("periods", verifier.REQUIRED_TOC_TABLES)
        self.assertNotIn("periods", proof.toc_tables)


class VerifyHumanReviewLedgerTests(unittest.TestCase):
    def _initial(self) -> tuple[str, ...]:
        return verifier.REGISTRY_VERSIONS[:16] + (verifier.HISTORICAL_MARKER,)

    def _upgraded(self) -> tuple[str, ...]:
        return verifier.REGISTRY_VERSIONS + (verifier.HISTORICAL_MARKER,)

    def test_historical_marker_does_not_change_initial_effective_head(self) -> None:
        state = verifier.validate_ledger(self._initial(), verifier.LedgerPhase.INITIAL)
        self.assertEqual(state.effective_registry_head, verifier.INITIAL_HEAD)
        self.assertEqual(state.total_ledger_rows, 17)
        self.assertEqual(state.historical_marker_count, 1)

    def test_upgrade_has_twenty_registry_revisions_plus_marker(self) -> None:
        state = verifier.validate_ledger(self._upgraded(), verifier.LedgerPhase.UPGRADED)
        self.assertEqual(state.effective_registry_head, verifier.FINAL_HEAD)
        self.assertEqual(state.registry_revision_count, 20)
        self.assertEqual(state.total_ledger_rows, 21)

    def test_downgrade_has_sixteen_registry_revisions_plus_marker(self) -> None:
        state = verifier.validate_ledger(self._initial(), verifier.LedgerPhase.DOWNGRADED)
        self.assertEqual(state.effective_registry_head, verifier.INITIAL_HEAD)
        self.assertEqual(state.registry_revision_count, 16)
        self.assertEqual(state.total_ledger_rows, 17)

    def test_reupgrade_has_twenty_registry_revisions_plus_marker(self) -> None:
        state = verifier.validate_ledger(self._upgraded(), verifier.LedgerPhase.REUPGRADED)
        self.assertEqual(state.effective_registry_head, verifier.FINAL_HEAD)
        self.assertEqual(state.total_ledger_rows, 21)

    def test_reupgrade_evidence_round_trip_matches_typed_ledger_state(self) -> None:
        state = verifier.validate_ledger(self._upgraded(), verifier.LedgerPhase.REUPGRADED)
        json_evidence = json.loads(json.dumps(verifier._ledger_state_evidence(state)))
        self.assertEqual(json_evidence, verifier._ledger_state_evidence(state))
        self.assertIsInstance(json_evidence["registry_revisions_present"], list)

    def test_duplicate_registry_revision_is_rejected(self) -> None:
        versions = self._initial() + (verifier.REGISTRY_VERSIONS[0],)
        with self.assertRaisesRegex(verifier.VerificationError, "duplicate ledger revision"):
            verifier.validate_ledger(versions, verifier.LedgerPhase.INITIAL)

    def test_duplicate_historical_marker_is_rejected(self) -> None:
        versions = self._initial() + (verifier.HISTORICAL_MARKER,)
        with self.assertRaisesRegex(verifier.VerificationError, "historical marker"):
            verifier.validate_ledger(versions, verifier.LedgerPhase.INITIAL)

    def test_unapproved_unknown_revision_is_rejected(self) -> None:
        versions = self._initial() + ("20260716_9999_unapproved",)
        with self.assertRaisesRegex(verifier.VerificationError, "unknown ledger revision"):
            verifier.validate_ledger(versions, verifier.LedgerPhase.INITIAL)

    def test_effective_head_uses_registry_order_not_ledger_order(self) -> None:
        versions = (verifier.HISTORICAL_MARKER,) + tuple(reversed(verifier.REGISTRY_VERSIONS[:16]))
        self.assertEqual(
            verifier.effective_registry_head(versions),
            verifier.INITIAL_HEAD,
        )

    def test_historical_marker_row_is_identical_across_all_phases(self) -> None:
        marker = verifier.HistoricalMarkerRow(
            version=verifier.HISTORICAL_MARKER,
            applied_at="2026-06-29T00:00:00",
        )
        for candidate in (marker, marker, marker, marker):
            verifier.verify_historical_marker_preserved(marker, candidate)

    def test_changed_historical_marker_row_is_rejected(self) -> None:
        before = verifier.HistoricalMarkerRow(verifier.HISTORICAL_MARKER, "original")
        after = verifier.HistoricalMarkerRow(verifier.HISTORICAL_MARKER, "changed")
        with self.assertRaisesRegex(verifier.VerificationError, "historical marker row changed"):
            verifier.verify_historical_marker_preserved(before, after)


class VerifyHumanReviewEvidenceTests(unittest.TestCase):
    def test_direct_script_invocation_reaches_backup_gate(self) -> None:
        backend_root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "scripts/verify_human_review_b2b1.py",
                "--database-url-env",
                "MIGRATION_DATABASE_URL",
                "--report-dir",
                ".",
                "--expected-head",
                verifier.FINAL_HEAD,
            ],
            cwd=backend_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("backup artifact is required", result.stderr)
        self.assertNotIn("ModuleNotFoundError", result.stderr)

    def test_inaccessible_restore_is_rejected(self) -> None:
        with self.assertRaisesRegex(verifier.VerificationError, "restored database is inaccessible"):
            verifier.verify_database_access(_FailingEngine())

    def test_incomplete_migration_is_not_accepted_as_registered(self) -> None:
        with self.assertRaisesRegex(verifier.VerificationError, "migration objects are incomplete"):
            verifier.verify_migration_registration("20260713_0018_human_review_core", False)

    def test_downgrade_residual_objects_are_rejected(self) -> None:
        with self.assertRaisesRegex(verifier.VerificationError, "downgrade left B2B.1 objects"):
            verifier.verify_no_b2b_objects({"tables": ["review_items"]})

    def test_skipped_postgresql_test_is_rejected(self) -> None:
        with self.assertRaisesRegex(verifier.VerificationError, "PostgreSQL test suite contains skips"):
            verifier.verify_postgres_test_log("OK (skipped=1)")

    def test_kpi_other_than_two_is_rejected(self) -> None:
        with self.assertRaisesRegex(verifier.VerificationError, "eligible_products must remain 2"):
            verifier.verify_kpi(3)

    def test_non_idempotent_second_backfill_is_rejected(self) -> None:
        result = {name: 0 for name in verifier.BACKFILL_CREATED_FIELDS}
        result["created_review_items"] = 1
        with self.assertRaisesRegex(verifier.VerificationError, "second backfill run is not idempotent"):
            verifier.verify_second_backfill(result)

    def test_semantic_plan_digest_uses_the_product_hash_contract(self) -> None:
        expected = "dc2299a29e0485eb8fd9fee712fad7574b875abb535b174169fb4561f5741e41"
        with patch.object(verifier, "backfill_plan_sha256", return_value=expected) as digest:
            self.assertEqual(verifier.verify_semantic_plan_hash(object(), expected), expected)
        digest.assert_called_once()

    def test_test_log_minimum_count_is_enforced_without_skips(self) -> None:
        verifier.verify_test_log("Ran 68 tests in 1.0s\n\nOK\n", minimum=68)
        with self.assertRaisesRegex(verifier.VerificationError, "at least 69"):
            verifier.verify_test_log("Ran 68 tests in 1.0s\n\nOK\n", minimum=69)

    def test_successful_main_prints_only_status_pass(self) -> None:
        stdout = StringIO()
        stderr = StringIO()
        with patch.object(verifier, "run_verification", return_value=None):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = verifier.main(
                    [
                        "--database-url-env",
                        "MIGRATION_DATABASE_URL",
                        "--report-dir",
                        ".",
                        "--expected-head",
                        verifier.FINAL_HEAD,
                        "--backup",
                        "backup.dump",
                        "--backup-sha256",
                        "backup.dump.sha256",
                        "--backup-toc",
                        "backup.toc.txt",
                    ]
                )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout.getvalue(), "STATUS=PASS\n")
        self.assertEqual(stderr.getvalue(), "")

    def test_final_evidence_accepts_closed_values(self) -> None:
        evidence = {
            "initial_head": verifier.INITIAL_HEAD,
            "upgrade_head": verifier.FINAL_HEAD,
            "downgrade_head": verifier.INITIAL_HEAD,
            "reupgrade_head": verifier.FINAL_HEAD,
            "postgres_skips": 0,
            "kpi_before": 2,
            "kpi_after": 2,
            "residual_objects": 0,
            "residual_containers": 0,
            "second_backfill": {name: 0 for name in verifier.BACKFILL_CREATED_FIELDS},
        }
        verifier.verify_completion_evidence(evidence)


class _FailingEngine:
    def connect(self):
        raise RuntimeError("connection refused")


if __name__ == "__main__":
    unittest.main()
