import unittest
from copy import deepcopy
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.preflight_canonical_identity import (
    DATASET_QUERIES,
    PROPOSED_COLUMNS,
    assert_snapshot_unchanged,
    build_argument_parser,
    enforce_postgres_read_only,
    operational_product_population,
    simulate_dataset,
)
from app.services.canonical_identity import CanonicalIdentityResolver
from tests.support.investigator_seed import (
    synthetic_seed_record,
    synthetic_seed_service,
)


class RecordingConnection:
    def __init__(self):
        self.statements = []

    def exec_driver_sql(self, statement):
        self.statements.append(statement)


def fixture_dataset():
    return {
        "roles": [
            {
                "id": 1,
                "import_job_id": 10,
                "teacher_id": 568,
                "external_researcher_id": None,
                "scientific_production_id": None,
                "person_key": "MARIA ESTEFANIA SANCHEZ PACHECO",
                "raw_name": "Maria Estefania Sanchez Pacheco",
                "normalized_name": "Maria Estefania Sanchez Pacheco",
                "role_type": "integrante_interno",
                "validation_status": "validated",
                "metadata_json": {"evidence": "pdf"},
            },
            {
                "id": 2,
                "import_job_id": 10,
                "teacher_id": None,
                "external_researcher_id": None,
                "scientific_production_id": 100,
                "person_key": "FERNANDO JOSE ZAMBRANO FARIAS",
                "raw_name": "Fernando Zambrano Farias",
                "normalized_name": "Fernando Jose Zambrano Farias",
                "role_type": "autor_producto",
                "validation_status": "pending_review",
                "metadata_json": None,
            },
            {
                "id": 3,
                "import_job_id": 10,
                "teacher_id": None,
                "external_researcher_id": None,
                "scientific_production_id": None,
                "person_key": "MERCHAN",
                "raw_name": "Merchan",
                "normalized_name": "Merchan",
                "role_type": "director",
                "validation_status": "pending_review",
                "metadata_json": None,
            },
            {
                "id": 4,
                "import_job_id": 11,
                "teacher_id": None,
                "external_researcher_id": None,
                "scientific_production_id": None,
                "person_key": "MERCHAN",
                "raw_name": "Merchan",
                "normalized_name": "Merchan",
                "role_type": "director",
                "validation_status": "pending_review",
                "metadata_json": None,
            },
            {
                "id": 5,
                "import_job_id": 10,
                "effective_current_job_id": 10,
                "teacher_id": None,
                "external_researcher_id": None,
                "scientific_production_id": 103,
                "person_key": "JORGE MRRCHAN",
                "raw_name": "JORGE MRRCHAN",
                "normalized_name": "Jorge Mrrchan",
                "role_type": "autor_producto",
                "validation_status": "pending_review",
                "row_or_block_id": "produccion_cientifica:9",
                "metadata_json": None,
            },
            {
                "id": 6,
                "import_job_id": 10,
                "effective_current_job_id": 10,
                "teacher_id": None,
                "external_researcher_id": None,
                "scientific_production_id": 104,
                "person_key": "ANA MANUAL PEREZ",
                "raw_name": "Ana Manual Perez",
                "normalized_name": "Ana Manual Perez",
                "role_type": "autor_producto",
                "validation_status": "validated",
                "row_or_block_id": "produccion_cientifica:10",
                "canonical_identity_key": "manual:locked-ana",
                "canonical_name": "Ana Manual Perez",
                "identity_source": "manual",
                "identity_confidence": 1.0,
                "identity_reason": "Reviewed by operator.",
                "identity_locked": True,
                "metadata_json": None,
            },
        ],
        "authors": [
            {
                "id": 20,
                "production_id": 100,
                "import_job_id": 10,
                "teacher_id": None,
                "external_researcher_id": None,
                "raw_author_name": "Maria Estefania Sanchez",
                "normalized_author_name": "Maria Estefania Sanchez",
                "author_type": "unresolved",
                "validation_status": "pending_author_resolution",
                "metadata_json": {"source": "author"},
            },
            {
                "id": 21,
                "production_id": 101,
                "import_job_id": 10,
                "teacher_id": None,
                "external_researcher_id": None,
                "raw_author_name": "Fernando Zambrano Farias",
                "normalized_author_name": "Fernando Zambrano Farias",
                "author_type": "unresolved",
                "validation_status": "validated",
                "metadata_json": None,
            },
            {
                "id": 22,
                "production_id": 103,
                "import_job_id": None,
                "effective_current_job_id": 10,
                "teacher_id": None,
                "external_researcher_id": None,
                "raw_author_name": "Jorge Mrrchan",
                "normalized_author_name": "Jorge Mrrchan",
                "author_type": "unresolved",
                "validation_status": "pending_author_resolution",
                "row_or_block_id": "produccion_cientifica:9",
                "metadata_json": {"audit_evidence_job_id": 10},
            },
            {
                "id": 23,
                "production_id": 104,
                "import_job_id": 9,
                "effective_current_job_id": 10,
                "audit_evidence_job_id": 10,
                "teacher_id": None,
                "external_researcher_id": None,
                "raw_author_name": "Ana Manual Perez",
                "normalized_author_name": "Ana Manual Perez",
                "author_type": "unresolved",
                "validation_status": "validated",
                "row_or_block_id": "produccion_cientifica:10",
                "metadata_json": {"audit_evidence_job_id": 10},
            },
        ],
        "teachers": [
            {"id": 568, "full_name": "Maria Estefania Sanchez Pacheco"},
        ],
        "externals": [],
        "productions": [
            {"id": 100, "import_job_id": 10, "effective_current_job_id": 10, "title": "Related product", "validation_status": "validated"},
            {"id": 101, "import_job_id": 10, "effective_current_job_id": 10, "title": "Fernando product", "validation_status": "validated"},
            {"id": 102, "import_job_id": 10, "effective_current_job_id": 10, "title": "Same job only", "validation_status": "validated"},
            {"id": 103, "import_job_id": 9, "effective_current_job_id": 10, "title": "Audited historical product", "validation_status": "validated"},
            {"id": 104, "import_job_id": 9, "effective_current_job_id": 10, "title": "Locked author product", "validation_status": "validated"},
        ],
        "audited_people": [
            "Carlos Parrales Choez",
            "Maria del Carmen Valls Martinez",
            "Maria Estefania Sanchez Pacheco",
            "Jose Manuel Santos Jaen",
            "Fernando Zambrano Farias",
        ],
    }


def simulate_with_synthetic_seed(dataset):
    resolver = CanonicalIdentityResolver(
        seed_service=synthetic_seed_service(
            synthetic_seed_record(
                "ZAMBRANO FARIAS FERNANDO JOSE",
                identity_number="SYN-FERNANDO",
            ),
            synthetic_seed_record(
                "MERCHAN RIERA JORGE MISAEL",
                identity_number="SYN-MERCHAN",
            ),
        )
    )
    return simulate_dataset(dataset, resolver=resolver)


class CanonicalIdentityPreflightTests(unittest.TestCase):
    def test_population_uses_current_audit_evidence_for_historical_products_and_all_their_authors(self):
        production_query = " ".join(DATASET_QUERIES["productions"].split()).lower()
        author_query = " ".join(DATASET_QUERIES["authors"].split()).lower()

        self.assertIn("import_normalization_audits", production_query)
        self.assertIn("entity_type = 'scientific_production'", production_query)
        self.assertIn("source_section = 'produccion_cientifica'", production_query)
        self.assertIn("effective_current_job_id", production_query)
        self.assertIn("spa.production_id", author_query)
        self.assertIn("effective_current_job_id", author_query)
        self.assertNotIn("author_job.id = spa.import_job_id", author_query)

    def test_operational_population_executes_null_job_product_and_author_fixture(self):
        productions, authors = operational_product_population(
            jobs=[{"id": 10, "status": "SUCCESS", "is_current": True}],
            productions=[
                {"id": 200, "import_job_id": None, "title": "Operational master"},
                {"id": 201, "import_job_id": 9, "title": "Historical only"},
            ],
            authors=[
                {"id": 300, "production_id": 200, "import_job_id": None},
                {"id": 301, "production_id": 201, "import_job_id": None},
            ],
            audits=[],
        )

        self.assertEqual([row["id"] for row in productions], [200])
        self.assertEqual([row["id"] for row in authors], [300])
        self.assertIsNone(productions[0]["effective_current_job_id"])
        self.assertIsNone(authors[0]["effective_current_job_id"])

    def test_audit_from_other_section_does_not_activate_historical_product(self):
        productions, authors = operational_product_population(
            jobs=[{"id": 10, "status": "SUCCESS", "is_current": True}],
            productions=[{"id": 202, "import_job_id": 9, "title": "Wrong section"}],
            authors=[{"id": 302, "production_id": 202, "import_job_id": None}],
            audits=[
                {
                    "normalized_record_id": 202,
                    "import_job_id": 10,
                    "entity_type": "scientific_production",
                    "source_section": "intercambios",
                }
            ],
        )

        self.assertEqual(productions, [])
        self.assertEqual(authors, [])

    def test_postgres_guard_is_explicitly_read_only(self):
        connection = RecordingConnection()

        enforce_postgres_read_only(connection)

        self.assertEqual(connection.statements, ["SET TRANSACTION READ ONLY"])

    def test_cli_exposes_no_mutation_mode(self):
        parser = build_argument_parser()
        option_names = {option for action in parser._actions for option in action.option_strings}

        self.assertFalse(option_names & {"--apply", "--backfill", "--migrate", "--reprocess"})

    def test_snapshot_comparison_detects_any_mutation(self):
        before = {"roles": {"count": 2, "hash": "a"}}
        assert_snapshot_unchanged(before, deepcopy(before))

        with self.assertRaisesRegex(RuntimeError, "database changed"):
            assert_snapshot_unchanged(before, {"roles": {"count": 3, "hash": "b"}})

    def test_every_source_row_is_retained_with_virtual_fields(self):
        dataset = fixture_dataset()

        result = simulate_dataset(dataset)

        self.assertEqual(len(result["row_changes"]), 10)
        role = next(row for row in result["row_changes"] if row["source_table"] == "person_roles")
        author = next(row for row in result["row_changes"] if row["source_table"] == "scientific_production_authors")
        self.assertEqual(role["metadata_json"], {"evidence": "pdf"})
        self.assertEqual(author["metadata_json"], {"source": "author"})
        self.assertTrue(set(PROPOSED_COLUMNS).issubset(role))
        self.assertTrue(set(PROPOSED_COLUMNS).issubset(author))

    def test_document_registry_gives_equivalent_role_and_author_one_teacher_decision(self):
        result = simulate_dataset(fixture_dataset())
        rows = result["row_changes"]
        role = next(row for row in rows if row["source_table"] == "person_roles" and row["id"] == 1)
        author = next(row for row in rows if row["source_table"] == "scientific_production_authors" and row["id"] == 20)

        self.assertEqual(role["canonical_identity_key"], "teacher:568")
        self.assertEqual(author["canonical_identity_key"], "teacher:568")
        self.assertEqual(role["evidence_registry_id"], author["evidence_registry_id"])

    def test_unique_candidate_inherits_exact_linked_document_teacher(self):
        dataset = {
            "roles": [
                {
                    "id": 50,
                    "import_job_id": 20,
                    "effective_current_job_id": 20,
                    "teacher_id": 565,
                    "raw_name": "Merchan Riera Jorge Misael",
                    "normalized_name": "Merchan Riera Jorge Misael",
                    "person_key": "MERCHAN RIERA JORGE MISAEL",
                    "role_type": "director",
                    "validation_status": "validated",
                },
                {
                    "id": 51,
                    "import_job_id": 20,
                    "effective_current_job_id": 20,
                    "scientific_production_id": 300,
                    "raw_name": "Jorge Mrrchan",
                    "normalized_name": "Jorge Mrrchan",
                    "person_key": "JORGE MRRCHAN",
                    "role_type": "autor_producto",
                    "validation_status": "pending_review",
                },
                {
                    "id": 52,
                    "import_job_id": 20,
                    "effective_current_job_id": 20,
                    "raw_name": "MERCHAN RIERA JORGE MISAEL",
                    "normalized_name": "Merchan Riera Jorge Misael",
                    "person_key": "MERCHAN RIERA JORGE MISAEL",
                    "role_type": "autor_producto",
                    "validation_status": "pending_review",
                },
            ],
            "authors": [
                {
                    "id": 60,
                    "production_id": 300,
                    "import_job_id": 20,
                    "effective_current_job_id": 20,
                    "raw_author_name": "Jorge Mrrchan",
                    "normalized_author_name": "Jorge Mrrchan",
                    "author_type": "unresolved",
                    "validation_status": "pending_author_resolution",
                }
            ],
            "teachers": [{"id": 565, "full_name": "Merchan Riera Jorge Misael"}],
            "externals": [],
            "productions": [{"id": 300, "validation_status": "pending_review"}],
            "audited_people": [],
        }

        rows = simulate_with_synthetic_seed(dataset)["row_changes"]
        inherited = [row for row in rows if row["id"] in {51, 52, 60}]
        mrrchan = [row for row in rows if row["id"] in {51, 60}]

        self.assertTrue(all(row["canonical_identity_key"] == "teacher:565" for row in inherited))
        self.assertTrue(all("linked_document_identity" in row["supporting_signals"] for row in inherited))
        self.assertTrue(all(row["candidate_count"] == 1 for row in mrrchan))

    def test_candidate_competitor_or_nonmatching_peer_stays_pending(self):
        for mode in ("competitor", "nonmatching"):
            with self.subTest(mode=mode):
                dataset = {
                    "roles": [
                        {
                            "id": 51,
                            "import_job_id": 20,
                            "effective_current_job_id": 20,
                            "scientific_production_id": 300,
                            "raw_name": "Jorge Mrrchan",
                            "normalized_name": "Jorge Mrrchan",
                            "person_key": "JORGE MRRCHAN",
                            "role_type": "autor_producto",
                            "validation_status": "pending_review",
                        }
                    ],
                    "authors": [],
                    "teachers": [
                        {"id": 565, "full_name": "Merchan Riera Jorge Misael"},
                        {
                            "id": 566,
                            "full_name": (
                                "Merchan Riera Jorge Misael"
                                if mode == "competitor"
                                else "Persona Distinta Sin Coincidencia"
                            ),
                        },
                    ],
                    "externals": [],
                    "productions": [],
                    "audited_people": [],
                }
                peer_names = (
                    [
                        (52, 565, "Merchan Riera Jorge Misael"),
                        (53, 566, "Merchan Riera Jorge Misael"),
                    ]
                    if mode == "competitor"
                    else [(52, 566, "Persona Distinta Sin Coincidencia")]
                )
                dataset["roles"].extend(
                    {
                        "id": row_id,
                        "import_job_id": 20,
                        "effective_current_job_id": 20,
                        "teacher_id": teacher_id,
                        "raw_name": name,
                        "normalized_name": name,
                        "person_key": name.upper(),
                        "role_type": "director",
                        "validation_status": "validated",
                    }
                    for row_id, teacher_id, name in peer_names
                )

                row = next(
                    row
                    for row in simulate_dataset(dataset)["row_changes"]
                    if row["id"] == 51
                )

                self.assertEqual(row["identity_status"], "pending")

    def test_concatenated_or_foreign_name_tokens_do_not_inherit_linked_teacher(self):
        dataset = {
            "roles": [
                {
                    "id": 90,
                    "import_job_id": 42,
                    "effective_current_job_id": 42,
                    "teacher_id": 581,
                    "raw_name": "Maria Del Pilar Viteri Vera",
                    "normalized_name": "Maria Del Pilar Viteri Vera",
                    "person_key": "MARIA DEL PILAR VITERI VERA",
                    "role_type": "director",
                    "validation_status": "validated",
                },
                {
                    "id": 91,
                    "import_job_id": 42,
                    "effective_current_job_id": 42,
                    "scientific_production_id": 501,
                    "raw_name": "Guevara Dolores Viteri Vera",
                    "normalized_name": "Guevara Dolores Viteri Vera",
                    "person_key": "GUEVARA DOLORES VITERI VERA",
                    "role_type": "autor_producto",
                    "validation_status": "pending_review",
                },
                {
                    "id": 92,
                    "import_job_id": 42,
                    "effective_current_job_id": 42,
                    "scientific_production_id": 502,
                    "raw_name": "Maria del Pilar Ortega",
                    "normalized_name": "Maria del Pilar Ortega",
                    "person_key": "MARIA DEL PILAR ORTEGA",
                    "role_type": "autor_producto",
                    "validation_status": "pending_review",
                },
            ],
            "authors": [
                {
                    "id": 93,
                    "production_id": 501,
                    "import_job_id": 42,
                    "effective_current_job_id": 42,
                    "raw_author_name": "Guevara Dolores Viteri Vera",
                    "normalized_author_name": "Guevara Dolores Viteri Vera",
                    "author_type": "unresolved",
                    "validation_status": "pending_author_resolution",
                },
                {
                    "id": 94,
                    "production_id": 502,
                    "import_job_id": 42,
                    "effective_current_job_id": 42,
                    "raw_author_name": "Maria del Pilar Ortega",
                    "normalized_author_name": "Maria del Pilar Ortega",
                    "author_type": "unresolved",
                    "validation_status": "pending_author_resolution",
                },
            ],
            "teachers": [{"id": 581, "full_name": "Maria Del Pilar Viteri Vera"}],
            "externals": [],
            "productions": [
                {"id": 501, "validation_status": "pending_review"},
                {"id": 502, "validation_status": "pending_review"},
            ],
            "audited_people": [],
        }

        rows = simulate_dataset(dataset)["row_changes"]
        fragments = [row for row in rows if row["id"] in {91, 92, 93, 94}]

        self.assertTrue(all(row["identity_status"] == "pending" for row in fragments))
        self.assertTrue(all(row["canonical_identity_key"] != "teacher:581" for row in fragments))

    def test_production_relation_uses_effective_job_and_propagates_locked_manual_decision(self):
        rows = simulate_dataset(fixture_dataset())["row_changes"]
        role = next(row for row in rows if row["source_table"] == "person_roles" and row["id"] == 6)
        author = next(
            row for row in rows
            if row["source_table"] == "scientific_production_authors" and row["id"] == 23
        )

        self.assertEqual(role["effective_current_job_id"], 10)
        self.assertEqual(author["effective_current_job_id"], 10)
        self.assertEqual(role["evidence_registry_id"], author["evidence_registry_id"])
        self.assertIn("production_id:104", author["evidence_relations"])
        self.assertIn("audit_evidence_job_id:10", author["evidence_relations"])
        self.assertEqual(role["canonical_identity_key"], "manual:locked-ana")
        self.assertEqual(author["canonical_identity_key"], "manual:locked-ana")
        self.assertEqual(author["identity_source"], "manual")

    def test_conflicting_manual_locks_are_preserved_and_block_unlocked_members(self):
        dataset = {
            "roles": [
                {
                    "id": 70,
                    "import_job_id": 30,
                    "effective_current_job_id": 30,
                    "scientific_production_id": 400,
                    "raw_name": "Ana Manual Perez",
                    "normalized_name": "Ana Manual Perez",
                    "person_key": "ANA MANUAL PEREZ",
                    "role_type": "autor_producto",
                    "identity_locked": True,
                    "canonical_identity_key": "manual:ana-a",
                    "canonical_name": "Ana Manual Perez",
                    "identity_source": "manual",
                    "validation_status": "validated",
                },
                {
                    "id": 71,
                    "import_job_id": 30,
                    "effective_current_job_id": 30,
                    "scientific_production_id": 400,
                    "raw_name": "Ana Manual Perez",
                    "normalized_name": "Ana Manual Perez",
                    "person_key": "ANA MANUAL PEREZ",
                    "role_type": "autor_producto",
                    "validation_status": "pending_review",
                },
            ],
            "authors": [
                {
                    "id": 80,
                    "production_id": 400,
                    "import_job_id": 30,
                    "effective_current_job_id": 30,
                    "raw_author_name": "Ana Manual Perez",
                    "normalized_author_name": "Ana Manual Perez",
                    "author_type": "unresolved",
                    "identity_locked": True,
                    "canonical_identity_key": "manual:ana-b",
                    "canonical_name": "Ana Manual Perez B",
                    "identity_source": "manual",
                    "validation_status": "validated",
                }
            ],
            "teachers": [],
            "externals": [],
            "productions": [{"id": 400, "validation_status": "validated"}],
            "audited_people": [],
        }

        result = simulate_dataset(dataset)
        rows = {(row["source_table"], row["id"]): row for row in result["row_changes"]}

        self.assertEqual(rows[("person_roles", 70)]["canonical_identity_key"], "manual:ana-a")
        self.assertEqual(rows[("scientific_production_authors", 80)]["canonical_identity_key"], "manual:ana-b")
        self.assertIsNone(rows[("person_roles", 71)]["canonical_identity_key"])
        self.assertEqual(rows[("person_roles", 71)]["identity_status"], "lock_conflict")
        self.assertTrue(any("conflicting manual locks" in error for error in result["summary"]["consistency_errors"]))

    def test_resolved_and_pending_keys_are_private_and_pending_is_document_scoped(self):
        result = simulate_with_synthetic_seed(fixture_dataset())
        rows = result["row_changes"]
        zambrano = next(row for row in rows if row["source_table"] == "person_roles" and row["id"] == 2)
        pending = [row for row in rows if row["source_table"] == "person_roles" and row["id"] in {3, 4}]

        self.assertRegex(zambrano["canonical_identity_key"], r"^institutional:[0-9a-f-]{36}$")
        self.assertNotIn("ZAMBRANO", zambrano["canonical_identity_key"].upper())
        self.assertTrue(all(row["identity_source"] == "pending" for row in pending))
        self.assertNotEqual(pending[0]["canonical_identity_key"], pending[1]["canonical_identity_key"])
        self.assertTrue(all("MERCHAN" not in row["canonical_identity_key"].upper() for row in pending))

    def test_shared_registry_keeps_ambiguous_pending_keys_scoped_per_document(self):
        dataset = fixture_dataset()
        dataset["roles"].extend(
            [
                {
                    "id": 30,
                    "import_job_id": 20,
                    "effective_current_job_id": 20,
                    "scientific_production_id": 200,
                    "person_key": "JORGE MRRCHAN",
                    "raw_name": "Jorge Mrrchan",
                    "normalized_name": "Jorge Mrrchan",
                    "role_type": "autor_producto",
                    "validation_status": "pending_review",
                },
                {
                    "id": 31,
                    "import_job_id": 21,
                    "effective_current_job_id": 21,
                    "scientific_production_id": 200,
                    "person_key": "JORGE MRRCHAN",
                    "raw_name": "Jorge Mrrchan",
                    "normalized_name": "Jorge Mrrchan",
                    "role_type": "autor_producto",
                    "validation_status": "pending_review",
                },
            ]
        )
        dataset["authors"].extend(
            [
                {
                    "id": 40,
                    "production_id": 200,
                    "import_job_id": 20,
                    "effective_current_job_id": 20,
                    "raw_author_name": "Jorge Mrrchan",
                    "normalized_author_name": "Jorge Mrrchan",
                    "author_type": "unresolved",
                    "validation_status": "pending_author_resolution",
                },
                {
                    "id": 41,
                    "production_id": 200,
                    "import_job_id": 21,
                    "effective_current_job_id": 21,
                    "raw_author_name": "Jorge Mrrchan",
                    "normalized_author_name": "Jorge Mrrchan",
                    "author_type": "unresolved",
                    "validation_status": "pending_author_resolution",
                },
            ]
        )

        result = simulate_dataset(dataset)
        rows = {row["id"]: row for row in result["row_changes"] if row["id"] in {30, 31, 40, 41}}

        self.assertEqual(rows[30]["evidence_registry_id"], rows[31]["evidence_registry_id"])
        self.assertEqual(rows[30]["canonical_identity_key"], rows[40]["canonical_identity_key"])
        self.assertEqual(rows[31]["canonical_identity_key"], rows[41]["canonical_identity_key"])
        self.assertNotEqual(rows[30]["canonical_identity_key"], rows[31]["canonical_identity_key"])
        self.assertTrue(all(row["identity_status"] == "pending" for row in rows.values()))
        self.assertEqual(result["summary"]["consistency_errors"], [])

    def test_audited_groups_include_all_required_surnames(self):
        groups = simulate_with_synthetic_seed(fixture_dataset())["audited_groups"]

        self.assertEqual(set(groups), {"Delgado", "Zambrano", "Sanchez", "Ramirez", "Merchan"})
        self.assertIn(2, groups["Zambrano"]["role_keys"])
        self.assertIn(21, groups["Zambrano"]["author_keys"])
        self.assertIn(5, groups["Merchan"]["role_keys"])
        self.assertIn(22, groups["Merchan"]["author_keys"])

    def test_products_are_assigned_only_by_author_production_id(self):
        products = simulate_dataset(fixture_dataset())["product_associations"]
        maria = products["people"]["Maria Estefania Sanchez Pacheco"]

        self.assertEqual([item["production_id"] for item in maria["expected_products"]], [100])
        self.assertNotIn(102, [item["production_id"] for item in maria["expected_products"]])
        self.assertTrue(products["proof"]["import_job_only_associations"] == [])
        self.assertEqual(products["proof"]["association_column"], "scientific_production_authors.production_id")

    def test_virtual_grouping_does_not_change_validation_kpi(self):
        summary = simulate_dataset(fixture_dataset())["summary"]

        self.assertEqual(summary["product_kpi_impact"]["before_eligible_products"], 2)
        self.assertEqual(summary["product_kpi_impact"]["expected_eligible_products"], 2)
        self.assertEqual(summary["product_kpi_impact"]["delta"], 0)
        self.assertEqual(summary["product_kpi_impact"]["before_validated_authors"], 2)
        self.assertEqual(summary["product_kpi_impact"]["expected_validated_authors"], 2)
        self.assertEqual(summary["writes"], 0)

    def test_repeated_dry_run_is_stable_and_does_not_mutate_input(self):
        dataset = fixture_dataset()
        original = deepcopy(dataset)

        first = simulate_dataset(dataset)
        second = simulate_dataset(dataset)

        self.assertEqual(first, second)
        self.assertEqual(dataset, original)


if __name__ == "__main__":
    unittest.main()
