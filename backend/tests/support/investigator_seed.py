from contextlib import contextmanager
from typing import Iterator
from unittest.mock import patch

from app.services.investigator_seed import (
    InvestigatorSeedRecord,
    InvestigatorSeedService,
    generate_name_aliases,
    normalize_name_key,
    official_name_from_inverted,
)


def synthetic_seed_record(
    raw_name: str,
    *,
    identity_number: str,
    project_code: str = "SYN-001",
    role: str = "INVESTIGADOR",
) -> InvestigatorSeedRecord:
    official_name = official_name_from_inverted(raw_name)
    aliases = (official_name, raw_name.title(), *generate_name_aliases(raw_name))
    return InvestigatorSeedRecord(
        row_number=2,
        faculty="FACULTAD SINTETICA",
        identity_number=identity_number,
        raw_name=raw_name,
        official_name=official_name,
        normalized_name=normalize_name_key(official_name),
        inverted_name=raw_name.title(),
        project_name="PROYECTO SINTETICO",
        project_code=project_code,
        year="2026",
        role=role,
        dedication="TC",
        observation=None,
        aliases=aliases,
        alias_keys=tuple(normalize_name_key(alias) for alias in aliases),
    )


def synthetic_seed_service(
    *records: InvestigatorSeedRecord,
) -> InvestigatorSeedService:
    service = InvestigatorSeedService(path="explicit-synthetic-seed.xlsx")
    service._records = list(records)
    return service


@contextmanager
def injected_participant_seed(
    *records: InvestigatorSeedRecord,
) -> Iterator[InvestigatorSeedService]:
    from app.services import participant_identity

    service = synthetic_seed_service(*records)
    participant_identity._cached_seed_match.cache_clear()
    participant_identity._seed_service.cache_clear()
    try:
        with patch.object(participant_identity, "_seed_service", return_value=service):
            yield service
    finally:
        participant_identity._cached_seed_match.cache_clear()
        participant_identity._seed_service.cache_clear()
