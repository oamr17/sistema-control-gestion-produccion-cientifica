import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.pdf_parser import parse_progress_report
from app.services.pdf_parser.participants import build_participant_summary
from app.services import investigator_seed, participant_identity
from app.services.investigator_seed import (
    InvestigatorSeedRecord,
    InvestigatorSeedService,
    generate_name_aliases,
    normalize_name_key,
    official_name_from_inverted,
)
from app.services.participant_identity import resolve_participant_identities


def synthetic_seed_record(raw_name: str) -> InvestigatorSeedRecord:
    official_name = official_name_from_inverted(raw_name)
    aliases = (official_name, raw_name.title(), *generate_name_aliases(raw_name))
    return InvestigatorSeedRecord(
        row_number=2,
        faculty="FACULTAD SINTETICA",
        identity_number="SYNTHETIC-001",
        raw_name=raw_name,
        official_name=official_name,
        normalized_name=normalize_name_key(official_name),
        inverted_name=raw_name.title(),
        project_name="PROYECTO SINTETICO",
        project_code="SYN-001",
        year="2026",
        role="INVESTIGADOR",
        dedication="TC",
        observation=None,
        aliases=aliases,
        alias_keys=tuple(normalize_name_key(alias) for alias in aliases),
    )


def synthetic_seed_service() -> InvestigatorSeedService:
    service = InvestigatorSeedService(path="explicit-synthetic-seed.xlsx")
    service._records = [synthetic_seed_record("ALFA BETA MARIA ELENA")]
    return service


def participant(name: str) -> dict:
    return {
        "canonical_name": name,
        "person_key": normalize_name_key(name),
        "person_type": "docente_interno",
        "kpi_eligible": True,
        "aliases": [],
        "original_texts": [],
        "institutional_roles": ["integrante_interno"],
        "production_roles": [],
        "source_sections": ["integrantes_internos"],
        "authorships": [],
    }


class OptionalInvestigatorSeedTests(unittest.TestCase):
    def setUp(self):
        self._clear_caches()

    def tearDown(self):
        self._clear_caches()

    @staticmethod
    def _clear_caches() -> None:
        participant_identity._cached_seed_match.cache_clear()
        participant_identity._seed_service.cache_clear()

    def test_parse_progress_report_continues_when_default_reference_is_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            missing_default = Path(directory) / "missing-default.xlsx"
            with patch.object(investigator_seed, "DEFAULT_SEED_PATH", missing_default):
                parsed = parse_progress_report(
                    """
                    INFORME PARCIAL DE GRUPOS DE INVESTIGACION
                    DATOS GENERALES DE GRUPOS DE INVESTIGACION
                    CICLO ACADEMICO QUE REPORTA
                    INVESTIGADORES QUE PARTICIPAN EN EL GRUPO
                    NOMBRE COMPLETO
                    FACULTAD
                    CARRERA
                    Maria Elena Alfa
                    FACULTAD SINTETICA
                    CARRERA SINTETICA
                    ACTIVIDADES REALIZADAS POR PROYECTOS FCI
                    """
                )

        self.assertEqual(len(parsed.integrantes_internos), 1)
        self.assertEqual(parsed.integrantes_internos[0]["name"], "Maria Elena Alfa")

    def test_missing_default_keeps_distinct_people_and_adds_no_seed_provenance(self):
        payload = {
            "integrantes_internos": [
                {"name": "Maria Elena Alfa", "faculty": "FACULTAD SINTETICA"},
                {"name": "Maria Elena Beta", "faculty": "FACULTAD SINTETICA"},
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            missing_default = Path(directory) / "missing-default.xlsx"
            with patch.object(investigator_seed, "DEFAULT_SEED_PATH", missing_default):
                summary = build_participant_summary(payload)

        people = summary["normalized_participants"]
        self.assertEqual(
            {person["canonical_name"] for person in people},
            {"Maria Elena Alfa", "Maria Elena Beta"},
        )
        self.assertEqual(len(people), 2)
        for person_data in people:
            resolution = person_data.get("identity_resolution") or {}
            self.assertFalse(str(resolution.get("method") or "").startswith("seed:"))

    def test_explicit_missing_reference_fails_with_clear_context(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "explicit-missing.xlsx"
            with self.assertRaisesRegex(
                FileNotFoundError,
                "explicit investigator reference",
            ):
                InvestigatorSeedService(path=missing).load_records()

    def test_corrupt_default_and_explicit_references_fail_clearly(self):
        with tempfile.TemporaryDirectory() as directory:
            corrupt = Path(directory) / "corrupt.xlsx"
            corrupt.write_bytes(b"not-an-xlsx-workbook")

            with self.subTest(source="explicit"):
                with self.assertRaisesRegex(Exception, "explicit investigator reference.*corrupt.xlsx"):
                    InvestigatorSeedService(path=corrupt).load_records()

            with self.subTest(source="default"):
                with patch.object(investigator_seed, "DEFAULT_SEED_PATH", corrupt):
                    with self.assertRaisesRegex(Exception, "default investigator reference.*corrupt.xlsx"):
                        InvestigatorSeedService().load_records()

    def test_injected_seed_preserves_resolution_without_consulting_default(self):
        service = synthetic_seed_service()
        with patch.object(
            participant_identity,
            "_seed_service",
            side_effect=AssertionError("default seed must not be consulted"),
        ):
            resolved = resolve_participant_identities(
                [participant("Maria Alfa Beta")],
                seed_service=service,
            )

        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["canonical_name"], "Maria Elena Alfa Beta")
        self.assertEqual(resolved[0]["identity_resolution"]["status"], "resolved")
        self.assertTrue(resolved[0]["identity_resolution"]["method"].startswith("seed:"))

    def test_injected_and_missing_default_cases_are_order_independent(self):
        def resolve_injected() -> str:
            return resolve_participant_identities(
                [participant("Maria Alfa Beta")],
                seed_service=synthetic_seed_service(),
            )[0]["canonical_name"]

        def resolve_without_default(missing_default: Path) -> dict:
            self._clear_caches()
            with patch.object(investigator_seed, "DEFAULT_SEED_PATH", missing_default):
                return resolve_participant_identities([participant("Maria Alfa Beta")])[0]

        with tempfile.TemporaryDirectory() as directory:
            missing_default = Path(directory) / "missing-default.xlsx"
            first_injected = resolve_injected()
            then_missing = resolve_without_default(missing_default)
            first_missing = resolve_without_default(missing_default)
            then_injected = resolve_injected()

        self.assertEqual(first_injected, "Maria Elena Alfa Beta")
        self.assertEqual(then_injected, first_injected)
        self.assertEqual(then_missing["canonical_name"], "Maria Alfa Beta")
        self.assertEqual(first_missing["canonical_name"], "Maria Alfa Beta")
        self.assertNotIn("identity_resolution", then_missing)
        self.assertNotIn("identity_resolution", first_missing)


if __name__ == "__main__":
    unittest.main()
