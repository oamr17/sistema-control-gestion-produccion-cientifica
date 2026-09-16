import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import backfill_canonical_identity as backfill


def make_role_row(row_id: int, **overrides):
    row = {
        "id": row_id,
        "raw_name": f"Role Person {row_id}",
        "person_key": f"ROLE PERSON {row_id}",
        "import_job_id": 1000 + row_id,
        "scientific_production_id": 2000 + row_id,
        "validation_status": "pending_review",
        "normalized_name": f"Role Person {row_id}",
        "metadata_json": {"row": row_id, "source": "role"},
        "source_file": f"role-{row_id}.pdf",
        "source_page": row_id,
        "raw_value": f"Role raw value {row_id}",
        "canonical_identity_key": None,
        "canonical_name": None,
        "identity_source": None,
        "identity_confidence": None,
        "identity_reason": None,
        "identity_locked": False,
        "identity_decided_by": None,
        "identity_decided_at": None,
    }
    row.update(overrides)
    return row


def make_author_row(row_id: int, **overrides):
    row = {
        "id": row_id,
        "raw_author_name": f"Author Person {row_id}",
        "import_job_id": 3000 + row_id,
        "production_id": 4000 + row_id,
        "validation_status": "pending_author_resolution",
        "normalized_author_name": f"Author Person {row_id}",
        "metadata_json": {"row": row_id, "source": "author"},
        "row_or_block_id": f"produccion_cientifica:{row_id}",
        "audit_evidence_job_id": 5000 + row_id,
        "canonical_identity_key": None,
        "canonical_name": None,
        "identity_source": None,
        "identity_confidence": None,
        "identity_reason": None,
        "identity_locked": False,
        "identity_decided_by": None,
        "identity_decided_at": None,
    }
    row.update(overrides)
    return row


def fake_database_hashes():
    hashes = {
        label: {"count": index + 1, "hash": f"{label}-hash-{index + 1}"}
        for index, label in enumerate(backfill.DATABASE_HASH_LABELS)
    }
    hashes["canonical_columns"] = [
        {"table_name": table_name, "column_name": column_name}
        for table_name in ("person_roles", "scientific_production_authors")
        for column_name in backfill.CANONICAL_FIELDS
    ]
    hashes["migration_table"] = "alembic_version"
    hashes["schema_migrations"] = [backfill.MIGRATION_VERSION]
    return hashes


def approved_result_fixture():
    return {
        "summary": {
            "identities_before": 85,
            "identities_after": 54,
            "person_document_participations_before": 122,
            "person_document_participations_after": 83,
            "roles_affected": 120,
            "authors_affected": 43,
            "pending_changes": 53,
            "product_kpi_impact": {
                "before_eligible_products": 2,
                "expected_eligible_products": 2,
            },
            "consistency_errors": [],
        },
        "automatic_merges": [
            {"canonical_name": "Fernando Jose Zambrano Farias"},
            {"canonical_name": "Merchan Riera Jorge Misael"},
            {"canonical_name": "Maria Estefania Sanchez Pacheco"},
        ],
        "row_changes": [
            {
                "source_table": "person_roles",
                "id": 1,
                "raw_name": "Delgado Litardo",
                "canonical_name": "Delgado Litardo",
                "identity_status": "pending",
            },
            {
                "source_table": "scientific_production_authors",
                "id": 2,
                "raw_author_name": "Ramirez Granda",
                "canonical_name": "Ramirez Granda",
                "identity_status": "pending",
            },
            {
                "source_table": "person_roles",
                "id": 3,
                "raw_name": "Fernando Zambrano Farias",
                "canonical_name": "Fernando Jose Zambrano Farias",
                "identity_status": "resolved",
            },
        ],
    }


class CanonicalIdentityBackfillContractTests(unittest.TestCase):
    def test_metric_gate_accepts_the_approved_contract_fixture(self):
        metrics = backfill.assert_approved_gates(approved_result_fixture())

        self.assertEqual(metrics["total_targets"], 163)
        self.assertEqual(metrics["automatic_groups"], backfill.APPROVED_AUTOMATIC_GROUPS)
        self.assertEqual(metrics["pending_rows"], 53)

    def test_metric_gate_rejects_drift_from_the_approved_contract(self):
        for label, mutate in (
            (
                "count drift",
                lambda result: result["summary"].__setitem__("identities_after", 55),
            ),
            (
                "extra merge",
                lambda result: result["automatic_merges"].append(
                    {"canonical_name": "Persona No Aprobada"}
                ),
            ),
            (
                "delgado resolved",
                lambda result: result["row_changes"][0].__setitem__("identity_status", "resolved"),
            ),
        ):
            with self.subTest(label=label):
                result = approved_result_fixture()
                mutate(result)

                with self.assertRaises(RuntimeError):
                    backfill.assert_approved_gates(result)

    def test_snapshot_manifest_is_stable_and_complete_for_120_roles_and_43_authors(self):
        rows_by_table = {
            "person_roles": [make_role_row(index) for index in range(1, 121)],
            "scientific_production_authors": [make_author_row(index) for index in range(1, 44)],
        }
        database_hashes = fake_database_hashes()

        first = backfill.build_snapshot_document(
            rows_by_table,
            database_hashes,
            captured_at_utc="2026-07-12T01:00:00+00:00",
            approved_metrics=backfill.assert_approved_gates(approved_result_fixture()),
        )
        second = backfill.build_snapshot_document(
            rows_by_table,
            database_hashes,
            captured_at_utc="2026-07-12T09:30:00+00:00",
            approved_metrics=backfill.assert_approved_gates(approved_result_fixture()),
        )

        self.assertEqual(first["counts"]["person_roles"], 120)
        self.assertEqual(first["counts"]["scientific_production_authors"], 43)
        self.assertEqual(first["counts"]["total_targets"], 163)
        self.assertEqual(first["manifest_sha256"], second["manifest_sha256"])

        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = Path(temp_dir) / "canonical_identity_snapshot.json"
            backfill.write_snapshot_document(first, snapshot_path)

            self.assertTrue(snapshot_path.exists())
            self.assertTrue(backfill.snapshot_sidecar_path(snapshot_path).exists())

    def test_verify_snapshot_rejects_missing_extra_tampered_and_canonical_drift(self):
        rows_by_table = {
            "person_roles": [
                make_role_row(1),
                make_role_row(2, canonical_identity_key="teacher:2", canonical_name="Locked Two"),
            ],
            "scientific_production_authors": [make_author_row(10)],
        }
        snapshot = backfill.build_snapshot_document(
            rows_by_table,
            fake_database_hashes(),
            captured_at_utc="2026-07-12T01:00:00+00:00",
            approved_metrics=backfill.assert_approved_gates(approved_result_fixture()),
        )

        backfill.verify_snapshot(snapshot, deepcopy(rows_by_table), fake_database_hashes())

        cases = [
            (
                "missing role",
                {"person_roles": [rows_by_table["person_roles"][0]], "scientific_production_authors": rows_by_table["scientific_production_authors"]},
                r"person_roles ids",
            ),
            (
                "extra author",
                {
                    "person_roles": rows_by_table["person_roles"],
                    "scientific_production_authors": rows_by_table["scientific_production_authors"]
                    + [make_author_row(11)],
                },
                r"scientific_production_authors ids",
            ),
            (
                "immutable tamper",
                {
                    "person_roles": [
                        make_role_row(1, raw_name="Role Person Tampered"),
                        rows_by_table["person_roles"][1],
                    ],
                    "scientific_production_authors": rows_by_table["scientific_production_authors"],
                },
                r"immutable field mismatch",
            ),
            (
                "canonical tamper",
                {
                    "person_roles": [
                        rows_by_table["person_roles"][0],
                        make_role_row(
                            2,
                            canonical_identity_key="teacher:999",
                            canonical_name="Changed Name",
                        ),
                    ],
                    "scientific_production_authors": rows_by_table["scientific_production_authors"],
                },
                r"canonical field mismatch",
            ),
        ]

        for label, current_rows, expected_message in cases:
            with self.subTest(label=label):
                with self.assertRaisesRegex(RuntimeError, expected_message):
                    backfill.verify_snapshot(snapshot, current_rows, fake_database_hashes())

    def test_verify_snapshot_allow_canonical_mismatch_preserves_evidence_checks(self):
        rows_by_table = {
            "person_roles": [make_role_row(1)],
            "scientific_production_authors": [make_author_row(10)],
        }
        snapshot = backfill.build_snapshot_document(
            rows_by_table,
            fake_database_hashes(),
            captured_at_utc="2026-07-12T01:00:00+00:00",
            approved_metrics=backfill.assert_approved_gates(approved_result_fixture()),
        )

        current_rows = {
            "person_roles": [
                make_role_row(
                    1,
                    canonical_identity_key="teacher:1",
                    canonical_name="Teacher One",
                    identity_source="teacher_id",
                    identity_confidence=1.0,
                    identity_reason="Matched teacher.",
                )
            ],
            "scientific_production_authors": [make_author_row(10)],
        }

        backfill.verify_snapshot(
            snapshot,
            current_rows,
            fake_database_hashes(),
            allow_canonical_mismatch=True,
        )

        tampered_rows = {
            "person_roles": [make_role_row(1, raw_name="Role Person Tampered")],
            "scientific_production_authors": [make_author_row(10)],
        }
        with self.assertRaisesRegex(RuntimeError, "immutable field mismatch"):
            backfill.verify_snapshot(
                snapshot,
                tampered_rows,
                fake_database_hashes(),
                allow_canonical_mismatch=True,
            )

    def test_verify_snapshot_rejects_snapshot_row_structure_drift_even_with_valid_manifest(self):
        rows_by_table = {
            "person_roles": [make_role_row(1), make_role_row(2)],
            "scientific_production_authors": [make_author_row(10)],
        }
        snapshot = backfill.build_snapshot_document(
            rows_by_table,
            fake_database_hashes(),
            captured_at_utc="2026-07-12T01:00:00+00:00",
            approved_metrics=backfill.assert_approved_gates(approved_result_fixture()),
        )

        missing_row_snapshot = deepcopy(snapshot)
        missing_row_snapshot["rows"]["person_roles"] = missing_row_snapshot["rows"]["person_roles"][:-1]
        missing_row_snapshot["manifest_sha256"] = backfill.calculate_manifest_sha256(missing_row_snapshot)
        with self.assertRaisesRegex(RuntimeError, "snapshot rows do not match target_ids"):
            backfill.verify_snapshot(
                missing_row_snapshot,
                rows_by_table,
                fake_database_hashes(),
            )

        extra_row_snapshot = deepcopy(snapshot)
        extra_row_snapshot["rows"]["scientific_production_authors"].append(
            deepcopy(extra_row_snapshot["rows"]["scientific_production_authors"][0])
        )
        extra_row_snapshot["rows"]["scientific_production_authors"][-1]["id"] = 11
        extra_row_snapshot["manifest_sha256"] = backfill.calculate_manifest_sha256(extra_row_snapshot)
        with self.assertRaisesRegex(RuntimeError, "snapshot rows do not match target_ids"):
            backfill.verify_snapshot(
                extra_row_snapshot,
                {
                    "person_roles": rows_by_table["person_roles"],
                    "scientific_production_authors": rows_by_table["scientific_production_authors"] + [make_author_row(11)],
                },
                fake_database_hashes(),
            )

    def test_apply_plan_preserves_locked_rows_and_is_idempotent_on_second_pass(self):
        current_rows = {
            "person_roles": [
                make_role_row(1),
                make_role_row(
                    2,
                    canonical_identity_key="manual:locked",
                    canonical_name="Locked Person",
                    identity_source="manual",
                    identity_confidence=1.0,
                    identity_reason="Reviewed",
                    identity_locked=True,
                    identity_decided_by="operator",
                    identity_decided_at="2026-07-12T01:00:00+00:00",
                ),
            ],
            "scientific_production_authors": [],
        }
        proposed_rows = {
            "person_roles": [
                make_role_row(
                    1,
                    canonical_identity_key="teacher:1",
                    canonical_name="Teacher One",
                    identity_source="teacher_id",
                    identity_confidence=1.0,
                    identity_reason="Matched teacher.",
                ),
                make_role_row(
                    2,
                    canonical_identity_key="teacher:2",
                    canonical_name="Replacement",
                    identity_source="teacher_id",
                    identity_confidence=0.95,
                    identity_reason="Would replace lock.",
                ),
            ],
            "scientific_production_authors": [],
        }

        plan = backfill.build_apply_plan(current_rows, proposed_rows)

        self.assertEqual(plan["total_targets"], 2)
        self.assertEqual(plan["changed_rows"], 1)
        self.assertEqual(plan["unchanged_rows"], 0)
        self.assertEqual(plan["locked_rows"], 1)
        self.assertEqual([item["id"] for item in plan["updates"]["person_roles"]], [1])

        engine = create_engine("sqlite+pysqlite:///:memory:")
        try:
            self._create_target_tables(engine)
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO person_roles (
                            id, raw_name, person_key, import_job_id, scientific_production_id,
                            validation_status, notes, canonical_identity_key, canonical_name,
                            identity_source, identity_confidence, identity_reason, identity_locked,
                            identity_decided_by, identity_decided_at
                        ) VALUES
                            (1, 'Role Person 1', 'ROLE PERSON 1', 1001, 2001, 'pending_review', 'keep-role', NULL, NULL, NULL, NULL, NULL, 0, NULL, NULL),
                            (2, 'Role Person 2', 'ROLE PERSON 2', 1002, 2002, 'pending_review', 'keep-lock', 'manual:locked', 'Locked Person', 'manual', 1.0, 'Reviewed', 1, 'operator', '2026-07-12T01:00:00+00:00')
                        """
                    )
                )

            backfill.run_update_batch(engine, plan["updates"])

            current_after = self._fetch_current_rows(engine)
            second_plan = backfill.build_apply_plan(current_after, proposed_rows)

            self.assertEqual(second_plan["changed_rows"], 0)
            self.assertEqual(second_plan["locked_rows"], 1)
        finally:
            engine.dispose()

    def test_apply_guard_accepts_second_apply_when_rows_match_snapshot_or_proposed(self):
        snapshot_rows = {
            "person_roles": [
                make_role_row(1),
                make_role_row(
                    2,
                    canonical_identity_key="manual:locked",
                    canonical_name="Locked Person",
                    identity_source="manual",
                    identity_confidence=1.0,
                    identity_reason="Reviewed",
                    identity_locked=True,
                    identity_decided_by="operator",
                    identity_decided_at="2026-07-12T01:00:00+00:00",
                ),
            ],
            "scientific_production_authors": [],
        }
        snapshot = backfill.build_snapshot_document(
            snapshot_rows,
            fake_database_hashes(),
            captured_at_utc="2026-07-12T01:00:00+00:00",
            approved_metrics=backfill.assert_approved_gates(approved_result_fixture()),
        )
        proposed_rows = {
            "person_roles": [
                make_role_row(
                    1,
                    canonical_identity_key="teacher:1",
                    canonical_name="Teacher One",
                    identity_source="teacher_id",
                    identity_confidence=1.0,
                    identity_reason="Matched teacher.",
                ),
                make_role_row(
                    2,
                    canonical_identity_key="manual:locked",
                    canonical_name="Locked Person",
                    identity_source="manual",
                    identity_confidence=1.0,
                    identity_reason="Reviewed",
                    identity_locked=True,
                    identity_decided_by="operator",
                    identity_decided_at="2026-07-12T01:00:00+00:00",
                ),
            ],
            "scientific_production_authors": [],
        }
        current_rows = deepcopy(proposed_rows)

        backfill.assert_applyable_canonical_states(snapshot, current_rows, proposed_rows)

        plan = backfill.build_apply_plan(current_rows, proposed_rows)
        self.assertEqual(plan["changed_rows"], 0)
        self.assertEqual(plan["locked_rows"], 1)

    def test_apply_guard_rejects_arbitrary_canonical_drift(self):
        snapshot_rows = {
            "person_roles": [make_role_row(1)],
            "scientific_production_authors": [],
        }
        snapshot = backfill.build_snapshot_document(
            snapshot_rows,
            fake_database_hashes(),
            captured_at_utc="2026-07-12T01:00:00+00:00",
            approved_metrics=backfill.assert_approved_gates(approved_result_fixture()),
        )
        proposed_rows = {
            "person_roles": [
                make_role_row(
                    1,
                    canonical_identity_key="teacher:1",
                    canonical_name="Teacher One",
                    identity_source="teacher_id",
                    identity_confidence=1.0,
                    identity_reason="Matched teacher.",
                )
            ],
            "scientific_production_authors": [],
        }
        drifted_rows = {
            "person_roles": [
                make_role_row(
                    1,
                    canonical_identity_key="teacher:999",
                    canonical_name="Unexpected Person",
                    identity_source="manual",
                    identity_confidence=0.42,
                    identity_reason="Drifted by hand.",
                    identity_locked=False,
                    identity_decided_by="someone",
                    identity_decided_at="2026-07-12T04:00:00+00:00",
                )
            ],
            "scientific_production_authors": [],
        }

        with self.assertRaisesRegex(RuntimeError, "canonical drift"):
            backfill.assert_applyable_canonical_states(snapshot, drifted_rows, proposed_rows)

    def test_metric_gate_rejects_consistency_conflicts(self):
        result = approved_result_fixture()
        result["summary"]["consistency_errors"] = ["conflicting manual locks"]

        with self.assertRaisesRegex(RuntimeError, "consistency errors"):
            backfill.assert_approved_gates(result)

    def test_backup_guard_requires_pgdmp_header_and_exact_sha256(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            backup_path = Path(temp_dir) / "snapshot.dump"
            backup_bytes = b"PGDMP\x01\x00review-backed"
            backup_path.write_bytes(backup_bytes)
            expected_sha256 = hashlib.sha256(backup_bytes).hexdigest()

            self.assertEqual(
                backfill.verify_backup_artifact(backup_path, expected_sha256),
                expected_sha256,
            )

            with self.assertRaisesRegex(RuntimeError, "backup sha256 mismatch"):
                backfill.verify_backup_artifact(backup_path, "0" * 64)

            wrong_header_path = Path(temp_dir) / "not_pg_dump.dump"
            wrong_header_path.write_bytes(b"HELLO\x01\x00review-backed")
            wrong_sha256 = hashlib.sha256(wrong_header_path.read_bytes()).hexdigest()
            with self.assertRaisesRegex(RuntimeError, "PGDMP"):
                backfill.verify_backup_artifact(wrong_header_path, wrong_sha256)

    def test_update_batch_rolls_back_when_second_update_fails(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        try:
            self._create_target_tables(engine)
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO person_roles (
                            id, raw_name, person_key, import_job_id, scientific_production_id,
                            validation_status, notes, canonical_identity_key, canonical_name,
                            identity_source, identity_confidence, identity_reason, identity_locked,
                            identity_decided_by, identity_decided_at
                        ) VALUES
                            (1, 'Role Person 1', 'ROLE PERSON 1', 1001, 2001, 'pending_review', 'keep-role', NULL, NULL, NULL, NULL, NULL, 0, NULL, NULL)
                        """
                    )
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO scientific_production_authors (
                            id, raw_author_name, import_job_id, production_id,
                            validation_status, notes, canonical_identity_key, canonical_name,
                            identity_source, identity_confidence, identity_reason, identity_locked,
                            identity_decided_by, identity_decided_at
                        ) VALUES
                            (10, 'Author Person 10', 3010, 4010, 'pending_author_resolution', 'keep-author', NULL, NULL, NULL, NULL, NULL, 0, NULL, NULL)
                        """
                    )
                )
                connection.execute(
                    text(
                        """
                        CREATE TRIGGER fail_author_canonical_update
                        BEFORE UPDATE OF canonical_name ON scientific_production_authors
                        WHEN NEW.canonical_name = 'Author Ten'
                        BEGIN
                            SELECT RAISE(FAIL, 'author update blocked');
                        END
                        """
                    )
                )

            updates = {
                "person_roles": [
                    {
                        "id": 1,
                        "canonical_identity_key": "teacher:1",
                        "canonical_name": "Teacher One",
                        "identity_source": "teacher_id",
                        "identity_confidence": 1.0,
                        "identity_reason": "Matched teacher.",
                        "identity_locked": False,
                        "identity_decided_by": None,
                        "identity_decided_at": None,
                    }
                ],
                "scientific_production_authors": [
                    {
                        "id": 10,
                        "canonical_identity_key": "institutional:author-10",
                        "canonical_name": "Author Ten",
                        "identity_source": "resolver",
                        "identity_confidence": 0.9,
                        "identity_reason": "Matched author.",
                        "identity_locked": False,
                        "identity_decided_by": None,
                        "identity_decided_at": None,
                    }
                ],
            }

            with self.assertRaisesRegex(Exception, "author update blocked"):
                backfill.run_update_batch(engine, updates)

            with engine.begin() as connection:
                role = connection.execute(
                    text(
                        """
                        SELECT canonical_identity_key, canonical_name, notes
                        FROM person_roles
                        WHERE id = 1
                        """
                    )
                ).mappings().one()
                author = connection.execute(
                    text(
                        """
                        SELECT canonical_identity_key, canonical_name, notes
                        FROM scientific_production_authors
                        WHERE id = 10
                        """
                    )
                ).mappings().one()

            self.assertIsNone(role["canonical_identity_key"])
            self.assertIsNone(role["canonical_name"])
            self.assertEqual(role["notes"], "keep-role")
            self.assertIsNone(author["canonical_identity_key"])
            self.assertIsNone(author["canonical_name"])
            self.assertEqual(author["notes"], "keep-author")
        finally:
            engine.dispose()

    def test_update_batch_only_touches_the_whitelist(self):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        try:
            self._create_target_tables(engine)
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO person_roles (
                            id, raw_name, person_key, import_job_id, scientific_production_id,
                            validation_status, notes, canonical_identity_key, canonical_name,
                            identity_source, identity_confidence, identity_reason, identity_locked,
                            identity_decided_by, identity_decided_at
                        ) VALUES
                            (1, 'Role Person 1', 'ROLE PERSON 1', 1001, 2001, 'pending_review', 'keep-role', NULL, NULL, NULL, NULL, NULL, 0, NULL, NULL)
                        """
                    )
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO scientific_production_authors (
                            id, raw_author_name, import_job_id, production_id,
                            validation_status, notes, canonical_identity_key, canonical_name,
                            identity_source, identity_confidence, identity_reason, identity_locked,
                            identity_decided_by, identity_decided_at
                        ) VALUES
                            (10, 'Author Person 10', 3010, 4010, 'pending_author_resolution', 'keep-author', NULL, NULL, NULL, NULL, NULL, 0, NULL, NULL)
                        """
                    )
                )

            updates = {
                "person_roles": [
                    {
                        "id": 1,
                        "canonical_identity_key": "teacher:1",
                        "canonical_name": "Teacher One",
                        "identity_source": "teacher_id",
                        "identity_confidence": 1.0,
                        "identity_reason": "Matched teacher.",
                        "identity_locked": False,
                        "identity_decided_by": None,
                        "identity_decided_at": None,
                    }
                ],
                "scientific_production_authors": [
                    {
                        "id": 10,
                        "canonical_identity_key": "institutional:author-10",
                        "canonical_name": "Author Ten",
                        "identity_source": "resolver",
                        "identity_confidence": 0.9,
                        "identity_reason": "Matched author.",
                        "identity_locked": False,
                        "identity_decided_by": None,
                        "identity_decided_at": None,
                    }
                ],
            }

            backfill.run_update_batch(engine, updates)

            with engine.begin() as connection:
                role = connection.execute(
                    text("SELECT raw_name, notes, canonical_name FROM person_roles WHERE id = 1")
                ).mappings().one()
                author = connection.execute(
                    text("SELECT raw_author_name, notes, canonical_name FROM scientific_production_authors WHERE id = 10")
                ).mappings().one()

            self.assertEqual(role["raw_name"], "Role Person 1")
            self.assertEqual(role["notes"], "keep-role")
            self.assertEqual(role["canonical_name"], "Teacher One")
            self.assertEqual(author["raw_author_name"], "Author Person 10")
            self.assertEqual(author["notes"], "keep-author")
            self.assertEqual(author["canonical_name"], "Author Ten")
        finally:
            engine.dispose()

    def test_restore_plan_restores_the_exact_snapshot_values(self):
        snapshot_rows = {
            "person_roles": [
                make_role_row(
                    1,
                    canonical_identity_key="manual:before",
                    canonical_name="Before Role",
                    identity_source="manual",
                    identity_confidence=1.0,
                    identity_reason="Reviewed before.",
                    identity_locked=True,
                    identity_decided_by="operator",
                    identity_decided_at="2026-07-12T01:00:00+00:00",
                )
            ],
            "scientific_production_authors": [
                make_author_row(
                    10,
                    canonical_identity_key="author:before",
                    canonical_name="Before Author",
                    identity_source="manual",
                    identity_confidence=0.8,
                    identity_reason="Author review.",
                    identity_locked=False,
                    identity_decided_by="operator",
                    identity_decided_at="2026-07-12T02:00:00+00:00",
                )
            ],
        }
        snapshot = backfill.build_snapshot_document(
            snapshot_rows,
            fake_database_hashes(),
            captured_at_utc="2026-07-12T01:00:00+00:00",
            approved_metrics=backfill.assert_approved_gates(approved_result_fixture()),
        )

        engine = create_engine("sqlite+pysqlite:///:memory:")
        try:
            self._create_target_tables(engine)
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO person_roles (
                            id, raw_name, person_key, import_job_id, scientific_production_id,
                            validation_status, notes, canonical_identity_key, canonical_name,
                            identity_source, identity_confidence, identity_reason, identity_locked,
                            identity_decided_by, identity_decided_at
                        ) VALUES
                            (1, 'Role Person 1', 'ROLE PERSON 1', 1001, 2001, 'pending_review', 'keep-role', 'teacher:after', 'After Role', 'resolver', 0.4, 'Changed later.', 0, NULL, NULL)
                        """
                    )
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO scientific_production_authors (
                            id, raw_author_name, import_job_id, production_id,
                            validation_status, notes, canonical_identity_key, canonical_name,
                            identity_source, identity_confidence, identity_reason, identity_locked,
                            identity_decided_by, identity_decided_at
                        ) VALUES
                            (10, 'Author Person 10', 3010, 4010, 'pending_author_resolution', 'keep-author', 'author:after', 'After Author', 'resolver', 0.2, 'Changed later.', 1, 'later', '2026-07-12T03:00:00+00:00')
                        """
                    )
                )

            restore_plan = backfill.build_restore_plan(self._fetch_current_rows(engine), snapshot)
            backfill.run_update_batch(engine, restore_plan["updates"])

            restored = self._fetch_current_rows(engine)
            self.assertEqual(
                restored["person_roles"][0]["canonical_identity_key"],
                "manual:before",
            )
            self.assertEqual(
                restored["person_roles"][0]["identity_decided_by"],
                "operator",
            )
            self.assertEqual(
                restored["scientific_production_authors"][0]["canonical_identity_key"],
                "author:before",
            )
            self.assertEqual(
                restored["scientific_production_authors"][0]["identity_reason"],
                "Author review.",
            )
        finally:
            engine.dispose()

    def test_cli_argument_validation_requires_snapshot_backup_hash_database_url_and_confirm_restore(self):
        parser = backfill.build_argument_parser()

        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args([])

        apply_args = parser.parse_args(["--apply"])
        with self.assertRaisesRegex(ValueError, "--snapshot-path"):
            backfill.validate_cli_args(apply_args)

        apply_without_database = parser.parse_args(
            [
                "--apply",
                "--snapshot-path",
                "snapshot.json",
                "--backup-path",
                "backup.dump",
                "--backup-sha256",
                "a" * 64,
            ]
        )
        with self.assertRaisesRegex(ValueError, "--database-url"):
            backfill.validate_cli_args(apply_without_database)

        restore_args = parser.parse_args(["--restore-snapshot", "--snapshot-path", "snapshot.json"])
        with self.assertRaisesRegex(ValueError, "--database-url"):
            backfill.validate_cli_args(restore_args)

        restore_with_database = parser.parse_args(
            [
                "--restore-snapshot",
                "--snapshot-path",
                "snapshot.json",
                "--database-url",
                "postgresql+psycopg://user:pass@host/db",
            ]
        )
        with self.assertRaisesRegex(ValueError, "--confirm-restore"):
            backfill.validate_cli_args(restore_with_database)

        snapshot_args = parser.parse_args(["--snapshot-only", "--snapshot-path", "snapshot.json"])
        backfill.validate_cli_args(snapshot_args)

    def _create_target_tables(self, engine) -> None:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE person_roles (
                        id INTEGER PRIMARY KEY,
                        raw_name TEXT,
                        person_key TEXT,
                        import_job_id INTEGER,
                        scientific_production_id INTEGER,
                        validation_status TEXT,
                        notes TEXT,
                        canonical_identity_key TEXT,
                        canonical_name TEXT,
                        identity_source TEXT,
                        identity_confidence REAL,
                        identity_reason TEXT,
                        identity_locked BOOLEAN NOT NULL DEFAULT 0,
                        identity_decided_by TEXT,
                        identity_decided_at TEXT
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE scientific_production_authors (
                        id INTEGER PRIMARY KEY,
                        raw_author_name TEXT,
                        import_job_id INTEGER,
                        production_id INTEGER,
                        validation_status TEXT,
                        notes TEXT,
                        canonical_identity_key TEXT,
                        canonical_name TEXT,
                        identity_source TEXT,
                        identity_confidence REAL,
                        identity_reason TEXT,
                        identity_locked BOOLEAN NOT NULL DEFAULT 0,
                        identity_decided_by TEXT,
                        identity_decided_at TEXT
                    )
                    """
                )
            )

    def _fetch_current_rows(self, engine):
        with engine.begin() as connection:
            role_rows = [
                dict(row)
                for row in connection.execute(
                    text(
                        """
                        SELECT id, raw_name, person_key, import_job_id, scientific_production_id,
                               validation_status, canonical_identity_key, canonical_name,
                               identity_source, identity_confidence, identity_reason,
                               identity_locked, identity_decided_by, identity_decided_at
                        FROM person_roles
                        ORDER BY id
                        """
                    )
                ).mappings()
            ]
            author_rows = [
                dict(row)
                for row in connection.execute(
                    text(
                        """
                        SELECT id, raw_author_name, import_job_id, production_id,
                               validation_status, canonical_identity_key, canonical_name,
                               identity_source, identity_confidence, identity_reason,
                               identity_locked, identity_decided_by, identity_decided_at
                        FROM scientific_production_authors
                        ORDER BY id
                        """
                    )
                ).mappings()
            ]
        return {
            "person_roles": role_rows,
            "scientific_production_authors": author_rows,
        }


class CanonicalIdentityBackfillPostgresIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_url = os.environ.get("CANONICAL_IDENTITY_TEST_DATABASE_URL")
        if not cls.database_url:
            raise unittest.SkipTest("CANONICAL_IDENTITY_TEST_DATABASE_URL is not set")

    def test_dry_run_snapshot_apply_second_apply_and_restore_round_trip(self):
        run_token = uuid4().hex
        with tempfile.TemporaryDirectory(prefix=f"canonical-backfill-{run_token}-") as temp_dir:
            temp_path = Path(temp_dir)
            snapshot_path = temp_path / "canonical_identity_snapshot.json"
            restored_snapshot_path = temp_path / "canonical_identity_snapshot_restored.json"
            backup_path, backup_sha256 = self._write_backup_artifact(temp_path)

            dry_run = backfill.run_dry_run(self.database_url)
            snapshot_result = backfill.run_snapshot_only(self.database_url, snapshot_path)
            first_apply = backfill.run_apply(
                self.database_url,
                snapshot_path=snapshot_path,
                backup_path=backup_path,
                backup_sha256=backup_sha256,
            )
            second_apply = backfill.run_apply(
                self.database_url,
                snapshot_path=snapshot_path,
                backup_path=backup_path,
                backup_sha256=backup_sha256,
            )
            restore_result = backfill.run_restore_snapshot(
                self.database_url,
                snapshot_path=snapshot_path,
                confirm_restore=True,
            )
            restored_snapshot = backfill.run_snapshot_only(
                self.database_url,
                restored_snapshot_path,
            )

            self.assertEqual(dry_run["approved_metrics"]["total_targets"], 163)
            self.assertEqual(snapshot_result["counts"]["total_targets"], 163)
            self.assertEqual(first_apply["total_targets"], 163)
            self.assertEqual(second_apply["changed_rows"], 0)
            self.assertEqual(restore_result["restored_rows"], 163)

            original_document = json.loads(snapshot_path.read_text(encoding="utf-8"))
            restored_document = json.loads(restored_snapshot_path.read_text(encoding="utf-8"))
            self.assertEqual(
                original_document["manifest_sha256"],
                restored_document["manifest_sha256"],
            )

    def test_lock_snapshot_targets_detects_real_nowait_conflict(self):
        run_token = uuid4().hex
        with tempfile.TemporaryDirectory(prefix=f"canonical-backfill-locks-{run_token}-") as temp_dir:
            temp_path = Path(temp_dir)
            snapshot_path = temp_path / "canonical_identity_snapshot.json"
            backfill.run_snapshot_only(self.database_url, snapshot_path)
            snapshot_document = backfill.load_snapshot_document(snapshot_path)
            target_id = snapshot_document["target_ids"]["person_roles"][0]

            engine = create_engine(self.database_url, pool_pre_ping=True)
            try:
                first_connection = engine.connect()
                second_connection = engine.connect()
                first_tx = first_connection.begin()
                second_tx = second_connection.begin()
                try:
                    first_connection.execute(
                        text("SELECT id FROM person_roles WHERE id = :id FOR UPDATE"),
                        {"id": target_id},
                    )
                    with self.assertRaisesRegex(RuntimeError, "conflicting locks"):
                        backfill._lock_snapshot_targets(second_connection, snapshot_document)
                finally:
                    second_tx.rollback()
                    first_tx.rollback()
                    second_connection.close()
                    first_connection.close()
            finally:
                engine.dispose()

    def test_run_apply_aborts_when_target_rows_are_locked(self):
        run_token = uuid4().hex
        with tempfile.TemporaryDirectory(prefix=f"canonical-backfill-apply-locks-{run_token}-") as temp_dir:
            temp_path = Path(temp_dir)
            snapshot_path = temp_path / "canonical_identity_snapshot.json"
            backup_path, backup_sha256 = self._write_backup_artifact(temp_path)
            backfill.run_snapshot_only(self.database_url, snapshot_path)
            snapshot_document = backfill.load_snapshot_document(snapshot_path)
            target_id = snapshot_document["target_ids"]["person_roles"][0]

            engine = create_engine(self.database_url, pool_pre_ping=True)
            try:
                connection = engine.connect()
                transaction = connection.begin()
                try:
                    connection.execute(
                        text("SELECT id FROM person_roles WHERE id = :id FOR UPDATE"),
                        {"id": target_id},
                    )
                    with self.assertRaisesRegex(RuntimeError, "conflicting locks"):
                        backfill.run_apply(
                            self.database_url,
                            snapshot_path=snapshot_path,
                            backup_path=backup_path,
                            backup_sha256=backup_sha256,
                        )
                finally:
                    transaction.rollback()
                    connection.close()
            finally:
                engine.dispose()

    def _write_backup_artifact(self, temp_path: Path):
        backup_path = temp_path / "disposable.backup"
        backup_bytes = b"PGDMP\x01\x0ereview-backup-body"
        backup_path.write_bytes(backup_bytes)
        return backup_path, hashlib.sha256(backup_bytes).hexdigest()


if __name__ == "__main__":
    unittest.main()
