from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.services.investigator_seed import InvestigatorSeedMatch, InvestigatorSeedService, normalize_name_key
from app.services.person_name_matching import heuristic_name_merge_allowed
from app.services.pdf_parser.confidence import fuzzy_score
from app.services.pdf_parser.validators import person_key, title_case_name


PERSON_TYPE_PRIORITY = {
    "pendiente_clasificacion": 0,
    "participante_especial": 1,
    "graduado": 2,
    "estudiante": 3,
    "investigador_externo": 4,
    "docente_interno": 5,
}

AUTO_SEED_SUGGESTION_THRESHOLD = 0.86


def resolve_participant_identities(
    participants: list[dict[str, Any]],
    *,
    merge: bool = True,
    seed_service: InvestigatorSeedService | None = None,
) -> list[dict[str, Any]]:
    resolved = [
        _resolve_seed_identity(dict(participant), seed_service=seed_service)
        for participant in participants
        if isinstance(participant, dict)
    ]
    _resolve_unique_partial_clusters(resolved)
    if merge:
        return _merge_by_person_key(resolved)
    return resolved


def _resolve_seed_identity(
    participant: dict[str, Any],
    *,
    seed_service: InvestigatorSeedService | None = None,
) -> dict[str, Any]:
    match = _best_seed_match(participant, seed_service=seed_service)
    if not _should_apply_seed_match(participant, match, seed_service=seed_service):
        if match.record and match.decision == "identity_suggested":
            participant["identity_resolution"] = {
                "status": "suggested",
                "method": f"seed:{match.match_type}",
                "confidence": match.confidence,
                "official_name": match.record.official_name,
                "identity_number": match.record.identity_number,
                "reason": match.reason,
            }
        return participant

    assert match.record is not None
    source_name = str(participant.get("canonical_name") or "").strip()
    source_key = str(participant.get("person_key") or person_key(source_name))
    official_name = match.record.official_name
    official_key = normalize_name_key(official_name)
    participant["source_canonical_name"] = participant.get("source_canonical_name") or source_name
    participant["source_person_key"] = participant.get("source_person_key") or source_key
    participant["canonical_name"] = official_name
    participant["person_key"] = official_key
    participant["matched_existing_person"] = True
    participant["match_confidence"] = max(float(participant.get("match_confidence") or 0), match.confidence)
    participant["match_reason"] = match.reason
    participant["identity_resolution"] = {
        "status": "resolved",
        "method": f"seed:{match.match_type}",
        "confidence": match.confidence,
        "official_name": official_name,
        "identity_number": match.record.identity_number,
        "matched_alias": match.matched_alias,
        "reason": match.reason,
    }
    _promote_pdf_validated_identity(participant)
    _refresh_nested_identity(participant)
    for alias in (source_name, match.matched_alias):
        _append_unique(participant.setdefault("aliases", []), alias)
    return participant


def _best_seed_match(
    participant: dict[str, Any],
    *,
    seed_service: InvestigatorSeedService | None = None,
) -> InvestigatorSeedMatch:
    role_keys = [*(participant.get("institutional_roles") or []), *(participant.get("production_roles") or [])]
    role_key = tuple(sorted(str(role or "") for role in role_keys if role))
    candidates = _identity_candidates(participant)
    best: InvestigatorSeedMatch | None = None
    for candidate in candidates:
        if not candidate:
            continue
        match = (
            seed_service.match_name(str(candidate), role_keys=list(role_key))
            if seed_service is not None
            else _cached_seed_match(str(candidate), role_key)
        )
        if best is None or _seed_rank(match) > _seed_rank(best):
            best = match
    return best or (
        seed_service.match_name("", role_keys=[])
        if seed_service is not None
        else _cached_seed_match("", ())
    )


@lru_cache(maxsize=1)
def _seed_service() -> InvestigatorSeedService:
    service = InvestigatorSeedService()
    service.load_records()
    return service


@lru_cache(maxsize=4096)
def _cached_seed_match(candidate: str, role_keys: tuple[str, ...]) -> InvestigatorSeedMatch:
    return _seed_service().match_name(candidate, role_keys=list(role_keys))


def _seed_rank(match: InvestigatorSeedMatch) -> tuple[int, float]:
    decision_rank = {"identity_validated": 2, "identity_suggested": 1}.get(match.decision, 0)
    return decision_rank, match.confidence


def _should_apply_seed_match(
    participant: dict[str, Any],
    match: InvestigatorSeedMatch,
    *,
    seed_service: InvestigatorSeedService | None = None,
) -> bool:
    if not match.record:
        return False
    if match.is_identity_validation:
        return True
    if match.match_type == "single_token_name" and _single_token_match_is_unique(
        participant,
        match,
        seed_service=seed_service,
    ):
        return True
    if (
        match.decision == "identity_suggested"
        and match.confidence >= AUTO_SEED_SUGGESTION_THRESHOLD
        and participant.get("person_type") in {"docente_interno", "investigador_externo"}
        and participant.get("kpi_eligible", False)
    ):
        return True
    return False


def _single_token_match_is_unique(
    participant: dict[str, Any],
    match: InvestigatorSeedMatch,
    *,
    seed_service: InvestigatorSeedService | None = None,
) -> bool:
    candidate_tokens = {
        token
        for candidate in _identity_candidates(participant)
        for token in normalize_name_key(candidate).split()
        if len(token) > 2
    }
    if len(candidate_tokens) != 1:
        return False
    token = next(iter(candidate_tokens))
    matching_records = {
        record.identity_number or record.normalized_name
        for record in (
            seed_service.load_records()
            if seed_service is not None
            else _seed_service().load_records()
        )
        if token in record.normalized_name.split()
        or any(token in alias_key.split() for alias_key in record.alias_keys)
    }
    return len(matching_records) == 1 and bool(match.record and (match.record.identity_number or match.record.normalized_name) in matching_records)


def _identity_candidates(participant: dict[str, Any]) -> list[Any]:
    return [
        participant.get("canonical_name"),
        *(participant.get("aliases") or []),
        *(participant.get("original_texts") or []),
    ]


def _resolve_unique_partial_clusters(participants: list[dict[str, Any]]) -> None:
    for participant in participants:
        if participant.get("identity_resolution", {}).get("status") == "resolved":
            continue
        candidates = [
            candidate
            for candidate in participants
            if candidate is not participant and _is_auto_identity_match(participant, candidate)
        ]
        unique_keys = {candidate.get("person_key") for candidate in candidates if candidate.get("person_key")}
        if len(unique_keys) != 1:
            continue
        target = max(candidates, key=_identity_preference_score)
        _apply_cluster_identity(participant, target)


def _is_auto_identity_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_key = str(left.get("person_key") or person_key(left.get("canonical_name"))).strip()
    right_key = str(right.get("person_key") or person_key(right.get("canonical_name"))).strip()
    if not left_key or not right_key or left_key == right_key:
        return False
    left_tokens = set(left_key.split())
    right_tokens = set(right_key.split())
    if len(left_tokens) < 2 or len(right_tokens) < 2:
        return False
    if not heuristic_name_merge_allowed(left_key, right_key):
        return False
    if left_tokens.issubset(right_tokens) or right_tokens.issubset(left_tokens):
        return True
    return _token_alias_score(left_tokens, right_tokens) >= 0.98 and _has_unique_overlap(left_tokens, right_tokens)


def _has_unique_overlap(left_tokens: set[str], right_tokens: set[str]) -> bool:
    shorter = left_tokens if len(left_tokens) <= len(right_tokens) else right_tokens
    return len(shorter) >= 2


def _apply_cluster_identity(participant: dict[str, Any], target: dict[str, Any]) -> None:
    source_name = str(participant.get("canonical_name") or "").strip()
    source_key = str(participant.get("person_key") or person_key(source_name))
    participant["source_canonical_name"] = participant.get("source_canonical_name") or source_name
    participant["source_person_key"] = participant.get("source_person_key") or source_key
    participant["canonical_name"] = target.get("canonical_name")
    participant["person_key"] = target.get("person_key")
    participant["matched_existing_person"] = True
    participant["match_confidence"] = max(float(participant.get("match_confidence") or 0), 0.94)
    participant["match_reason"] = "Identidad fusionada por cluster unico de tokens parciales entre participantes del batch."
    participant["identity_resolution"] = {
        "status": "resolved",
        "method": "batch:unique_partial_tokens",
        "confidence": 0.94,
        "official_name": target.get("canonical_name"),
        "identity_number": (target.get("identity_resolution") or {}).get("identity_number"),
        "reason": participant["match_reason"],
    }
    _promote_pdf_validated_identity(participant)
    _refresh_nested_identity(participant)
    _append_unique(participant.setdefault("aliases", []), source_name)


def _promote_pdf_validated_identity(participant: dict[str, Any]) -> None:
    if participant.get("person_type") not in {"docente_interno", "investigador_externo"}:
        return
    if not _has_institutional_pdf_evidence(participant):
        return
    participant["validation_status"] = "validado"
    participant["status_reason"] = "Identidad completada y validada con semilla unica sobre evidencia PDF institucional."
    participant["review_reason"] = participant["status_reason"]
    participant["review_bucket"] = "valid_person"
    participant["name_truncated"] = False
    participant["show_in_participants"] = True
    participant["kpi_eligible"] = True
    if participant.get("person_type") == "docente_interno":
        participant["kpi_reason"] = "Cuenta para KPIs de docentes internos por evidencia PDF institucional e identidad completada con semilla."
    else:
        participant["kpi_reason"] = "Cuenta para KPIs de investigadores externos por evidencia PDF e identidad completada con semilla."


def _has_institutional_pdf_evidence(participant: dict[str, Any]) -> bool:
    sections = {str(section or "") for section in participant.get("source_sections") or []}
    roles = {str(role or "") for role in participant.get("institutional_roles") or []}
    institutional_sections = {
        "integrantes_internos",
        "integrantes_externos",
        "proyectos_fci",
        "research_entities",
        "director_responsable",
    }
    institutional_roles = {
        "integrante_interno",
        "investigador_externo",
        "director_proyecto",
        "coordinador_grupo",
        "tutor_semillero",
        "responsable_informe",
    }
    return bool(sections.intersection(institutional_sections) or roles.intersection(institutional_roles))


def _merge_by_person_key(participants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for participant in participants:
        key = str(participant.get("person_key") or person_key(participant.get("canonical_name")))
        if not key:
            continue
        if key not in merged:
            merged[key] = participant
            continue
        _merge_participant(merged[key], participant)
    return sorted(merged.values(), key=lambda item: str(item.get("canonical_name") or ""))


def _merge_participant(target: dict[str, Any], source: dict[str, Any]) -> None:
    if _identity_preference_score(source) > _identity_preference_score(target):
        preserved = dict(target)
        target.clear()
        target.update(source)
        source = preserved

    _upgrade_person_type(target, source.get("person_type"))
    _merge_bool(target, source, "matched_existing_person")
    _merge_bool(target, source, "name_truncated")
    _merge_bool(target, source, "show_in_participants")
    if source.get("kpi_eligible"):
        target["kpi_eligible"] = True
        target["kpi_reason"] = source.get("kpi_reason") or target.get("kpi_reason")
    if float(source.get("match_confidence") or 0) > float(target.get("match_confidence") or 0):
        target["match_confidence"] = source.get("match_confidence")
        target["match_reason"] = source.get("match_reason")
    for key in (
        "aliases",
        "institutional_roles",
        "production_roles",
        "participations",
        "authorships",
        "evidences",
        "original_texts",
        "source_sections",
        "source_fields",
    ):
        _merge_list(target, source, key)
    for alias in (source.get("canonical_name"), source.get("source_canonical_name")):
        _append_unique(target.setdefault("aliases", []), alias)
    target["products_authored_count"] = len(
        {
            str(item.get("row_or_block_id") or item.get("product_title") or item.get("product_index"))
            for item in target.get("authorships", [])
            if isinstance(item, dict)
        }
    )
    target["products_entity_count"] = max(
        int(target.get("products_entity_count") or 0),
        int(source.get("products_entity_count") or 0),
    )
    _refresh_nested_identity(target)


def _refresh_nested_identity(participant: dict[str, Any]) -> None:
    person_key_value = participant.get("person_key")
    canonical_name = participant.get("canonical_name")
    for authorship in participant.get("authorships") or []:
        if isinstance(authorship, dict):
            authorship["person_key"] = person_key_value
            authorship["author_name"] = canonical_name


def _identity_preference_score(participant: dict[str, Any]) -> tuple[int, int, int, int]:
    resolution = participant.get("identity_resolution") if isinstance(participant.get("identity_resolution"), dict) else {}
    seed_score = 2 if str(resolution.get("method") or "").startswith("seed:") else 0
    kpi_score = 1 if participant.get("kpi_eligible") else 0
    type_score = PERSON_TYPE_PRIORITY.get(str(participant.get("person_type") or ""), 0)
    token_count = len(person_key(participant.get("canonical_name")).split())
    return seed_score, kpi_score, type_score, token_count


def _upgrade_person_type(target: dict[str, Any], candidate_type: Any) -> None:
    if PERSON_TYPE_PRIORITY.get(str(candidate_type or ""), 0) > PERSON_TYPE_PRIORITY.get(str(target.get("person_type") or ""), 0):
        target["person_type"] = candidate_type


def _merge_bool(target: dict[str, Any], source: dict[str, Any], key: str) -> None:
    target[key] = bool(target.get(key) or source.get(key))


def _merge_list(target: dict[str, Any], source: dict[str, Any], key: str) -> None:
    values = target.setdefault(key, [])
    for value in source.get(key) or []:
        _append_unique(values, value)


def _append_unique(items: list[Any], value: Any) -> None:
    if value in (None, ""):
        return
    marker = _marker(value)
    if all(_marker(item) != marker for item in items):
        items.append(value)


def _marker(value: Any) -> str:
    if isinstance(value, dict):
        return repr(sorted(value.items()))
    return str(value)


def _token_alias_score(left_tokens: set[str], right_tokens: set[str]) -> float:
    left = [token for token in left_tokens if len(token) > 1]
    right = [token for token in right_tokens if len(token) > 1]
    if not left or not right:
        return 0.0
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    matched_longer: set[str] = set()
    matches = 0
    for token in shorter:
        candidate = _best_token_match(token, longer, matched_longer)
        if candidate:
            matched_longer.add(candidate)
            matches += 1
    if len(shorter) < 2:
        return 0.0
    return matches / len(shorter)


def _best_token_match(token: str, candidates: list[str], used: set[str]) -> str | None:
    if token in candidates and token not in used:
        return token
    best_candidate: str | None = None
    best_score = 0.0
    for candidate in candidates:
        if candidate in used:
            continue
        score = fuzzy_score(token, candidate)
        if score > best_score:
            best_score = score
            best_candidate = candidate
    return best_candidate if best_score >= 84 else None
