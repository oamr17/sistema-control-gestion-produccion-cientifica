from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid5

from app.services.investigator_seed import InvestigatorSeedService, normalize_key, normalize_name_key


APPLICATION_NAMESPACE = UUID("73dd43b1-f9a8-5fec-b690-1d3f8f60fd3a")
EXACT_MATCH_TYPES = {"identity_number", "exact_normalized_name", "inverted_name"}


@dataclass(frozen=True)
class IdentityCandidate:
    canonical_name: str
    source: str
    confidence: float
    match_type: str
    identity_number: str | None = None
    aliases: tuple[str, ...] = field(default_factory=tuple)
    project_codes: tuple[str, ...] = field(default_factory=tuple)
    institutional_emails: tuple[str, ...] = field(default_factory=tuple)
    supporting_signals: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CanonicalIdentityDecision:
    key: str
    canonical_name: str
    source: str
    confidence: float
    reason: str
    status: str
    candidate_count: int
    supporting_signals: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class IdentityEvidence:
    source_name: str
    source_key: str
    import_job_id: str
    locked_decision: CanonicalIdentityDecision | None = None
    teacher_id: int | str | None = None
    teacher_name: str | None = None
    external_id: int | str | None = None
    external_name: str | None = None
    institutional_identifier: str | None = None
    institutional_email: str | None = None
    trusted_institutional_identifier: str | None = None
    trusted_institutional_email: str | None = None
    candidates: tuple[IdentityCandidate, ...] = field(default_factory=tuple)
    linked_identity: str | None = None
    fuller_alias_in_document: str | None = None
    document_owner: str | None = None
    project_code: str | None = None
    generic_role: str | None = None
    shared_surname_count: int = 0


class CanonicalIdentityResolver:
    EXACT_THRESHOLD = 0.98
    PARTIAL_THRESHOLD = 0.96

    def __init__(self, seed_service: InvestigatorSeedService | None = None):
        self.seed_service = seed_service

    def resolve(self, evidence: IdentityEvidence) -> CanonicalIdentityDecision:
        if evidence.locked_decision is not None:
            return evidence.locked_decision

        if evidence.teacher_id is not None:
            return self._direct_decision(
                key=f"teacher:{evidence.teacher_id}",
                name=evidence.teacher_name or evidence.source_name,
                source="teacher",
                reason="Existing teacher identifier has priority.",
            )

        if evidence.external_id is not None:
            return self._direct_decision(
                key=f"external:{evidence.external_id}",
                name=evidence.external_name or evidence.source_name,
                source="external",
                reason="Existing external-person identifier has priority.",
            )

        institutional_value = (
            evidence.trusted_institutional_identifier
            or evidence.trusted_institutional_email
        )
        if institutional_value:
            signals = (
                "institutional_identifier"
                if evidence.trusted_institutional_identifier
                else "institutional_email",
            )
            return CanonicalIdentityDecision(
                key=self._institutional_key(institutional_value),
                canonical_name=evidence.source_name,
                source="institutional",
                confidence=1.0,
                reason="Institutional identifier provides a deterministic canonical identity.",
                status="resolved",
                candidate_count=0,
                supporting_signals=signals,
            )

        candidates = evidence.candidates or self._seed_candidates(evidence)
        signals = self._independent_signals(evidence, candidates[0]) if len(candidates) == 1 else ()
        if len(candidates) == 1 and self._candidate_is_accepted(
            evidence,
            candidates[0],
            signals,
        ):
            selected = candidates[0]
            identity_input = selected.identity_number or normalize_name_key(selected.canonical_name)
            return CanonicalIdentityDecision(
                key=self._institutional_key(identity_input),
                canonical_name=selected.canonical_name,
                source=selected.source,
                confidence=selected.confidence,
                reason=self._candidate_reason(selected),
                status="resolved",
                candidate_count=1,
                supporting_signals=signals,
            )

        return CanonicalIdentityDecision(
            key=self._pending_key(evidence),
            canonical_name=evidence.source_name,
            source="pending",
            confidence=0.0,
            reason=self._pending_reason(candidates),
            status="pending",
            candidate_count=len(candidates),
            supporting_signals=signals,
        )

    @staticmethod
    def _direct_decision(*, key: str, name: str, source: str, reason: str) -> CanonicalIdentityDecision:
        return CanonicalIdentityDecision(
            key=key,
            canonical_name=name,
            source=source,
            confidence=1.0,
            reason=reason,
            status="resolved",
            candidate_count=0,
            supporting_signals=(f"{source}_id",),
        )

    def _seed_candidates(self, evidence: IdentityEvidence) -> tuple[IdentityCandidate, ...]:
        if self.seed_service is None:
            return ()
        matches = self.seed_service.match_candidates(
            evidence.source_name,
            project_codes=[evidence.project_code] if evidence.project_code else None,
            role_keys=[evidence.generic_role] if evidence.generic_role else None,
        )
        records = self.seed_service.load_records()

        def records_for(match_identity: str) -> list[Any]:
            return [
                record
                for record in records
                if (record.identity_number or record.normalized_name) == match_identity
            ]

        candidates: list[IdentityCandidate] = []
        for match in matches:
            if match.record is None:
                continue
            identity_key = match.record.identity_number or match.record.normalized_name
            related_records = records_for(identity_key)
            aliases = tuple(
                dict.fromkeys(
                    alias
                    for record in related_records
                    for alias in (record.official_name, record.inverted_name, *record.aliases)
                    if alias
                )
            )
            project_codes = tuple(
                dict.fromkeys(
                    record.project_code
                    for record in related_records
                    if record.project_code
                )
            )
            candidates.append(
                IdentityCandidate(
                    canonical_name=match.record.official_name,
                    source="investigator_seed",
                    confidence=match.confidence,
                    match_type=match.match_type,
                    identity_number=match.record.identity_number,
                    aliases=aliases,
                    project_codes=project_codes,
                )
            )
        return tuple(candidates)

    def _candidate_is_accepted(
        self,
        evidence: IdentityEvidence,
        candidate: IdentityCandidate,
        signals: tuple[str, ...],
    ) -> bool:
        if len(normalize_name_key(evidence.source_name).split()) < 2:
            return False
        if candidate.match_type in EXACT_MATCH_TYPES:
            return candidate.confidence >= self.EXACT_THRESHOLD
        return candidate.confidence >= self.PARTIAL_THRESHOLD and bool(signals)

    @staticmethod
    def _independent_signals(
        evidence: IdentityEvidence,
        candidate: IdentityCandidate,
    ) -> tuple[str, ...]:
        signals: list[str] = []
        candidate_names = {
            normalize_name_key(value)
            for value in (candidate.canonical_name, *candidate.aliases)
            if normalize_name_key(value)
        }
        source_token_count = len(normalize_name_key(evidence.source_name).split())
        fuller_alias_key = normalize_name_key(evidence.fuller_alias_in_document)

        if (
            evidence.linked_identity
            and candidate.identity_number
            and normalize_key(evidence.linked_identity) == normalize_key(candidate.identity_number)
        ):
            signals.append("linked_identity")
        if (
            fuller_alias_key in candidate_names
            and len(fuller_alias_key.split()) > source_token_count
        ):
            signals.append("fuller_alias_in_document")
        if normalize_name_key(evidence.document_owner) in candidate_names:
            signals.append("document_owner")
        if (
            evidence.institutional_identifier
            and candidate.identity_number
            and normalize_key(evidence.institutional_identifier) == normalize_key(candidate.identity_number)
        ):
            signals.append("institutional_identifier")
        if (
            evidence.institutional_email
            and evidence.institutional_email.strip().casefold()
            in {email.strip().casefold() for email in candidate.institutional_emails}
        ):
            signals.append("institutional_email")
        if (
            evidence.project_code
            and normalize_key(evidence.project_code).replace(" ", "")
            in {
                normalize_key(code).replace(" ", "")
                for code in candidate.project_codes
            }
        ):
            signals.append("project_code")
        return tuple(signals)

    @staticmethod
    def _candidate_reason(candidate: IdentityCandidate) -> str:
        if candidate.match_type in EXACT_MATCH_TYPES:
            return "A unique exact or inverted Excel candidate met the 0.98 threshold."
        return "A unique partial or OCR Excel candidate met the 0.96 threshold with independent corroboration."

    @staticmethod
    def _pending_reason(candidates: tuple[IdentityCandidate, ...]) -> str:
        if len(candidates) > 1:
            return "Multiple compatible Excel candidates require manual review."
        if len(candidates) == 1:
            return "The sole Excel candidate lacks the required threshold or independent corroboration."
        return "No canonical identity evidence met an automatic resolution rule."

    @staticmethod
    def _institutional_key(value: Any) -> str:
        opaque_uuid = uuid5(APPLICATION_NAMESPACE, f"institutional|{normalize_key(value)}")
        return f"institutional:{opaque_uuid}"

    @staticmethod
    def _pending_key(evidence: IdentityEvidence) -> str:
        normalized_source = normalize_name_key(evidence.source_key or evidence.source_name)
        opaque_uuid = uuid5(
            APPLICATION_NAMESPACE,
            f"pending|{evidence.import_job_id}|{normalized_source}",
        )
        return f"pending:{opaque_uuid}"
