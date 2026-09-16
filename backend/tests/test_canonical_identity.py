import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.canonical_identity import (
    CanonicalIdentityDecision,
    CanonicalIdentityResolver,
    IdentityCandidate,
    IdentityEvidence,
)
from app.services.investigator_seed import (
    InvestigatorSeedRecord,
    InvestigatorSeedService,
    generate_name_aliases,
    normalize_name_key,
    official_name_from_inverted,
)


def candidate(
    name: str,
    identity_number: str,
    *,
    match_type: str = "exact_normalized_name",
    confidence: float = 0.98,
    aliases: tuple[str, ...] = (),
    project_codes: tuple[str, ...] = (),
    institutional_emails: tuple[str, ...] = (),
    supporting_signals: tuple[str, ...] = (),
) -> IdentityCandidate:
    return IdentityCandidate(
        canonical_name=name,
        source="investigator_seed",
        confidence=confidence,
        match_type=match_type,
        identity_number=identity_number,
        aliases=aliases,
        project_codes=project_codes,
        institutional_emails=institutional_emails,
        supporting_signals=supporting_signals,
    )


def seed_record(
    raw_name: str,
    *,
    identity_number: str,
    project_code: str,
) -> InvestigatorSeedRecord:
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
        role="DIRECTOR-FCI",
        dedication="TC",
        observation=None,
        aliases=aliases,
        alias_keys=tuple(normalize_name_key(alias) for alias in aliases),
    )


def seed_service(*records: InvestigatorSeedRecord) -> InvestigatorSeedService:
    service = InvestigatorSeedService(path="unused.xlsx")
    service._records = list(records)
    return service


class CanonicalIdentityResolverTests(unittest.TestCase):
    def setUp(self):
        self.resolver = CanonicalIdentityResolver()

    def evidence(self, **changes) -> IdentityEvidence:
        values = {
            "source_name": "Fernando Jose Zambrano Farias",
            "source_key": "fernando jose zambrano farias",
            "import_job_id": "job-1",
        }
        values.update(changes)
        return IdentityEvidence(**values)

    def test_candidate_linked_evidence_has_typed_public_fields(self):
        self.assertIn("aliases", IdentityCandidate.__dataclass_fields__)
        self.assertIn("project_codes", IdentityCandidate.__dataclass_fields__)
        self.assertIn("institutional_emails", IdentityCandidate.__dataclass_fields__)
        self.assertIn("trusted_institutional_identifier", IdentityEvidence.__dataclass_fields__)
        self.assertIn("trusted_institutional_email", IdentityEvidence.__dataclass_fields__)

    def test_manual_lock_is_returned_unchanged_before_other_identifiers(self):
        locked = CanonicalIdentityDecision(
            key="manual:77",
            canonical_name="Nombre Revisado",
            source="manual",
            confidence=1.0,
            reason="Locked by reviewer.",
            status="resolved",
            candidate_count=4,
            supporting_signals=("manual_lock",),
        )

        decision = self.resolver.resolve(
            self.evidence(locked_decision=locked, teacher_id=12, external_id=9)
        )

        self.assertIs(decision, locked)

    def test_teacher_id_has_priority_over_external_and_candidates(self):
        decision = self.resolver.resolve(
            self.evidence(
                teacher_id=12,
                external_id=9,
                candidates=(candidate("Otra Persona", "123"),),
            )
        )

        self.assertEqual(decision.key, "teacher:12")
        self.assertEqual(decision.source, "teacher")
        self.assertEqual(decision.status, "resolved")

    def test_external_id_has_priority_over_institutional_and_candidates(self):
        decision = self.resolver.resolve(
            self.evidence(
                external_id="EXT-9",
                institutional_identifier="0917300113",
                candidates=(candidate("Otra Persona", "123"),),
            )
        )

        self.assertEqual(decision.key, "external:EXT-9")
        self.assertEqual(decision.source, "external")

    def test_institutional_key_is_stable_and_opaque(self):
        evidence = self.evidence(
            source_name="Ana Maria Delgado Ruiz",
            trusted_institutional_identifier="0917300113",
            trusted_institutional_email="ana.delgado@example.edu.ec",
        )

        first = self.resolver.resolve(evidence)
        second = self.resolver.resolve(evidence)

        self.assertEqual(first.key, second.key)
        self.assertRegex(first.key, r"^institutional:[0-9a-f-]{36}$")
        self.assertNotIn("0917300113", first.key)
        self.assertNotIn("ANA", first.key.upper())
        self.assertNotIn("DELGADO", first.key.upper())
        self.assertNotIn("EXAMPLE", first.key.upper())

    def test_untrusted_institutional_identifier_does_not_resolve_without_candidate_link(self):
        decision = self.resolver.resolve(
            self.evidence(institutional_identifier="0917300113")
        )

        self.assertEqual(decision.status, "pending")
        self.assertTrue(decision.key.startswith("pending:"))

    def test_pending_key_is_stable_within_document_and_isolated_across_documents(self):
        first = self.resolver.resolve(self.evidence(import_job_id="job-1"))
        repeat = self.resolver.resolve(self.evidence(import_job_id="job-1"))
        other_document = self.resolver.resolve(self.evidence(import_job_id="job-2"))

        self.assertEqual(first.key, repeat.key)
        self.assertNotEqual(first.key, other_document.key)
        self.assertRegex(first.key, r"^pending:[0-9a-f-]{36}$")

    def test_unique_exact_candidate_resolves_at_threshold(self):
        decision = self.resolver.resolve(
            self.evidence(candidates=(candidate("Fernando Jose Zambrano Farias", "0917300113"),))
        )

        self.assertEqual(decision.key, "institutional:" + decision.key.split(":", 1)[1])
        self.assertEqual(decision.canonical_name, "Fernando Jose Zambrano Farias")
        self.assertEqual(decision.confidence, 0.98)
        self.assertEqual(decision.candidate_count, 1)
        self.assertEqual(decision.status, "resolved")

    def test_isolated_prefix_candidate_stays_pending_without_corroboration(self):
        partial = candidate(
            "Fernando Jose Zambrano Farias",
            "0917300113",
            match_type="strict_name_prefix",
            confidence=0.97,
        )

        decision = self.resolver.resolve(self.evidence(source_name="Fernando Zam", candidates=(partial,)))

        self.assertEqual(decision.status, "pending")
        self.assertTrue(decision.key.startswith("pending:"))

    def test_prefix_candidate_resolves_with_independent_project_code(self):
        partial = candidate(
            "Fernando Jose Zambrano Farias",
            "0917300113",
            match_type="strict_name_prefix",
            confidence=0.96,
            project_codes=("FCI-001",),
        )

        decision = self.resolver.resolve(
            self.evidence(source_name="Fernando Zam", candidates=(partial,), project_code="FCI-001")
        )

        self.assertEqual(decision.status, "resolved")
        self.assertIn("project_code", decision.supporting_signals)

    def test_unrelated_typed_evidence_does_not_corroborate_partial_candidate(self):
        partial = candidate(
            "Fernando Jose Zambrano Farias",
            "0917300113",
            match_type="strict_name_prefix",
            confidence=0.97,
            aliases=("Fernando Zambrano Farias",),
            project_codes=("FCI-001",),
            institutional_emails=("fernando.zambrano@example.edu.ec",),
        )

        decision = self.resolver.resolve(
            self.evidence(
                source_name="Fernando Zam",
                candidates=(partial,),
                linked_identity="9999999999",
                fuller_alias_in_document="Fernando Zamora Ruiz",
                document_owner="Maria Perez",
                project_code="FCI-999",
                institutional_identifier="8888888888",
                institutional_email="other@example.edu.ec",
            )
        )

        self.assertEqual(decision.status, "pending")
        self.assertEqual(decision.supporting_signals, ())

    def test_matching_linked_identity_corroborates_partial_candidate(self):
        partial = candidate(
            "Fernando Jose Zambrano Farias",
            "0917300113",
            match_type="strict_name_prefix",
            confidence=0.97,
        )

        decision = self.resolver.resolve(
            self.evidence(
                source_name="Fernando Zam",
                candidates=(partial,),
                linked_identity="0917300113",
            )
        )

        self.assertEqual(decision.status, "resolved")
        self.assertIn("linked_identity", decision.supporting_signals)

    def test_arbitrary_candidate_signals_cannot_bypass_linked_corroboration(self):
        partial = candidate(
            "Fernando Jose Zambrano Farias",
            "0917300113",
            match_type="strict_name_prefix",
            confidence=0.97,
            project_codes=("FCI-001",),
            supporting_signals=("generic_role", "unverified_label"),
        )

        pending = self.resolver.resolve(
            self.evidence(source_name="Fernando Zam", candidates=(partial,))
        )
        resolved = self.resolver.resolve(
            self.evidence(
                source_name="Fernando Zam",
                candidates=(partial,),
                project_code="FCI-001",
            )
        )

        self.assertEqual(pending.status, "pending")
        self.assertEqual(pending.supporting_signals, ())
        self.assertEqual(resolved.status, "resolved")
        self.assertEqual(resolved.supporting_signals, ("project_code",))

    def test_generic_role_does_not_corroborate_prefix(self):
        partial = candidate(
            "Fernando Jose Zambrano Farias",
            "0917300113",
            match_type="strict_name_prefix",
            confidence=0.97,
        )

        decision = self.resolver.resolve(
            self.evidence(source_name="Fernando Zam", candidates=(partial,), generic_role="INVESTIGADOR")
        )

        self.assertEqual(decision.status, "pending")
        self.assertNotIn("generic_role", decision.supporting_signals)

    def test_multiple_compatible_candidates_stay_pending(self):
        decision = self.resolver.resolve(
            self.evidence(
                source_name="Juan Sanchez",
                candidates=(
                    candidate("Juan Carlos Sanchez Vera", "111"),
                    candidate("Juan Pablo Sanchez Ruiz", "222"),
                ),
            )
        )

        self.assertEqual(decision.status, "pending")
        self.assertEqual(decision.candidate_count, 2)

    def test_one_shared_surname_never_corroborates(self):
        partial = candidate(
            "Ana Maria Paredes Ruiz",
            "333",
            match_type="name_token_subset",
            confidence=0.98,
        )

        decision = self.resolver.resolve(
            self.evidence(
                source_name="Ana Maria Paredes Rios",
                candidates=(partial,),
                shared_surname_count=1,
            )
        )

        self.assertEqual(decision.status, "pending")
        self.assertNotIn("shared_surname", decision.supporting_signals)

    def test_single_token_source_never_resolves_exact_candidate(self):
        decision = self.resolver.resolve(
            self.evidence(
                source_name="Merchan",
                source_key="merchan",
                candidates=(candidate("Jorge Misael Merchan Riera", "0920589892"),),
            )
        )

        self.assertEqual(decision.status, "pending")


class CanonicalIdentitySeedIntegrationTests(unittest.TestCase):
    def evidence(self, source_name: str, **changes) -> IdentityEvidence:
        values = {
            "source_name": source_name,
            "source_key": source_name,
            "import_job_id": "job-integration",
        }
        values.update(changes)
        return IdentityEvidence(**values)

    def test_seed_enumeration_deduplicates_rows_and_resolves_unique_exact_identity(self):
        service = seed_service(
            seed_record(
                "ZAMBRANO FARIAS FERNANDO JOSE",
                identity_number="0917300113",
                project_code="FCI-001",
            ),
            seed_record(
                "ZAMBRANO FARIAS FERNANDO JOSE",
                identity_number="0917300113",
                project_code="GI-001",
            ),
        )

        decision = CanonicalIdentityResolver(seed_service=service).resolve(
            self.evidence("Fernando Jose Zambrano Farias")
        )

        self.assertEqual(decision.status, "resolved")
        self.assertEqual(decision.candidate_count, 1)

    def test_seed_partial_candidate_requires_matching_project_code(self):
        service = seed_service(
            seed_record(
                "ZAMBRANO FARIAS FERNANDO JOSE",
                identity_number="0917300113",
                project_code="FCI-001",
            )
        )
        resolver = CanonicalIdentityResolver(seed_service=service)

        unrelated = resolver.resolve(self.evidence("Fernando Jose Zam", project_code="FCI-999"))
        linked = resolver.resolve(self.evidence("Fernando Jose Zam", project_code="FCI-001"))

        self.assertEqual(unrelated.status, "pending")
        self.assertEqual(linked.status, "resolved")
        self.assertIn("project_code", linked.supporting_signals)

    def test_seed_multiple_compatible_identities_stay_pending(self):
        service = seed_service(
            seed_record("SANCHEZ VERA JUAN CARLOS", identity_number="111", project_code="FCI-1"),
            seed_record("SANCHEZ RUIZ JUAN PABLO", identity_number="222", project_code="FCI-2"),
        )

        decision = CanonicalIdentityResolver(seed_service=service).resolve(
            self.evidence("Juan Sanchez")
        )

        self.assertEqual(decision.status, "pending")
        self.assertEqual(decision.candidate_count, 2)

    def test_sparse_single_token_seed_alias_stays_pending(self):
        service = seed_service(
            seed_record(
                "MERCHAN RIERA JORGE MISAEL",
                identity_number="0920589892",
                project_code="FCI-002",
            )
        )

        decision = CanonicalIdentityResolver(seed_service=service).resolve(
            self.evidence("Merchan", project_code="FCI-002")
        )

        self.assertEqual(decision.status, "pending")


if __name__ == "__main__":
    unittest.main()
