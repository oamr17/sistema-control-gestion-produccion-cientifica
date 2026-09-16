import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.investigator_seed import (
    InvestigatorSeedRecord,
    InvestigatorSeedService,
    generate_name_aliases,
    normalize_name_key,
    official_name_from_inverted,
)


def seed_record(raw_name: str, *, identity_number: str = "0917300113", project_code: str = "FCI-001", role: str = "DIRECTOR-FCI"):
    official = official_name_from_inverted(raw_name)
    aliases = (official, raw_name.title(), *generate_name_aliases(raw_name))
    return InvestigatorSeedRecord(
        row_number=2,
        faculty="CIENCIAS ADMINISTRATIVAS",
        identity_number=identity_number,
        raw_name=raw_name,
        official_name=official,
        normalized_name=normalize_name_key(official),
        inverted_name=raw_name.title(),
        project_name="Proyecto",
        project_code=project_code,
        year="2025",
        role=role,
        dedication="TC",
        observation=None,
        aliases=aliases,
        alias_keys=tuple(normalize_name_key(alias) for alias in aliases),
    )


class InvestigatorSeedTests(unittest.TestCase):
    def service_with(self, *records: InvestigatorSeedRecord) -> InvestigatorSeedService:
        service = InvestigatorSeedService(path="unused.xlsx")
        service._records = list(records)
        return service

    def test_official_name_from_inverted(self):
        self.assertEqual(
            official_name_from_inverted("ZAMBRANO FARIAS FERNANDO JOSE"),
            "Fernando Jose Zambrano Farias",
        )

    def test_alias_matches_missing_middle_name(self):
        service = self.service_with(seed_record("ZAMBRANO FARIAS FERNANDO JOSE"))
        match = service.match_name("Fernando Zambrano Farias")
        self.assertEqual(match.decision, "identity_validated")
        self.assertIn(match.match_type, {"exact_normalized_name", "strong_name_similarity"})

    def test_strict_normalized_prefix_is_high_confidence(self):
        service = self.service_with(seed_record("ZAMBRANO FARIAS FERNANDO JOSE"))

        match = service.match_name("Fernando Jose Zam")

        self.assertEqual(match.decision, "identity_validated")
        self.assertEqual(match.match_type, "strict_name_prefix")
        self.assertGreaterEqual(match.confidence, 0.95)

    def test_candidate_token_subset_is_high_confidence_despite_length(self):
        service = self.service_with(seed_record("ZAMBRANO FARIAS FERNANDO JOSE"))

        match = service.match_name("Fernando Farias")

        self.assertEqual(match.decision, "identity_validated")
        self.assertEqual(match.match_type, "name_token_subset")
        self.assertGreaterEqual(match.confidence, 0.95)

    def test_strong_ocr_suffix_match(self):
        service = self.service_with(seed_record("RAMIREZ GRANDA ROBERTH FABIAN", identity_number="1102675285"))
        match = service.match_name("Roberth Fabian Ramirez Grand")
        self.assertEqual(match.decision, "identity_validated")
        self.assertIn(match.match_type, {"strict_name_prefix", "strong_name_similarity"})

    def test_single_token_stays_suggestion(self):
        service = self.service_with(seed_record("MONTESDEOCA PERALTA MARLENE DE JESUS", identity_number="0911773745"))
        match = service.match_name("Montesdeoca")
        self.assertEqual(match.decision, "identity_suggested")
        self.assertEqual(match.match_type, "single_token_name")

    def test_ocr_suffix_fragments_stay_suggestion(self):
        service = self.service_with(seed_record("RIVADENEIRA CAMPOVERDE JORGE XAVIER", identity_number="0910755669"))
        match = service.match_name("Ira Campove Rde Jorge")
        self.assertEqual(match.decision, "identity_suggested")
        self.assertEqual(match.match_type, "weak_name_similarity")

    def test_match_candidates_enumerates_every_compatible_record(self):
        service = self.service_with(
            seed_record("SANCHEZ VERA JUAN CARLOS", identity_number="111"),
            seed_record("SANCHEZ RUIZ JUAN PABLO", identity_number="222"),
        )

        matches = service.match_candidates("Juan Sanchez")

        self.assertEqual({match.record.identity_number for match in matches}, {"111", "222"})

    def test_match_candidates_collapses_repeated_rows_for_same_identity(self):
        service = self.service_with(
            seed_record("ZAMBRANO FARIAS FERNANDO JOSE", identity_number="0917300113", project_code="FCI-001"),
            seed_record("ZAMBRANO FARIAS FERNANDO JOSE", identity_number="0917300113", project_code="GI-001"),
        )

        matches = service.match_candidates("Fernando Jose Zambrano Farias")

        self.assertEqual(len(matches), 1)


if __name__ == "__main__":
    unittest.main()
