from __future__ import annotations

from typing import Any

from .confidence import fuzzy_score
from .normalizer import normalize_key
from .validators import looks_like_person_name, person_key, title_case_name
from app.services.institution_normalizer import normalize_external_institution_display
from app.services.person_name_matching import heuristic_name_merge_allowed
from app.services.participant_identity import resolve_participant_identities


PERSON_TYPE_PRIORITY = {
    "pendiente_clasificacion": 0,
    "participante_especial": 1,
    "graduado": 2,
    "estudiante": 3,
    "investigador_externo": 4,
    "docente_interno": 5,
}

AUTO_MERGE_SCORE = 96
REVIEW_MERGE_SCORE = 86

PERSON_FIELD_SCHEMA = {
    ("director_responsable", "name"),
    ("director_responsable", "responsable"),
    ("integrantes_externos", "name"),
    ("integrantes_internos", "name"),
    ("produccion_cientifica", "authors"),
    ("proyectos_fci", "director"),
    ("research_entities", "director"),
}
AUTHOR_SOURCE_SECTIONS = {"intercambios", "produccion_cientifica"}
AUTHOR_SOURCE_FIELDS = {"author", "authors", "autor", "autor_1", "autor_2", "autor_3", "autor_4", "autor_5"}

FUNCTIONAL_ROLE_MAP = {
    "director": "director_proyecto",
    "director_proyecto": "director_proyecto",
    "coordinador": "coordinador_grupo",
    "coordinador_grupo": "coordinador_grupo",
    "tutor": "tutor_semillero",
    "tutor_semillero": "tutor_semillero",
    "responsable": "responsable_informe",
    "responsable_informe": "responsable_informe",
}

ENTITY_ROLE_BY_TYPE = {
    "proyecto_fci": "director_proyecto",
    "grupo_investigacion": "coordinador_grupo",
    "semillero": "tutor_semillero",
    "informe_seguimiento": "responsable_informe",
}

FCA_FACULTY_KEY = "CIENCIAS ADMINISTRATIVAS"
PARTICIPANT_SCOPES = {
    "internal_fca",
    "internal_other_faculty",
    "external",
    "pending",
    "discarded",
}


def build_participant_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Build normalized people, role, and authorship views from parsed PDF payload."""
    registry: dict[str, dict[str, Any]] = {}
    audit: list[dict[str, Any]] = []
    aliases: list[dict[str, Any]] = []
    possible_merges: list[dict[str, Any]] = []

    for member in _payload_list(payload, "integrantes_internos"):
        _register_person(
            registry=registry,
            audit=audit,
            aliases=aliases,
            possible_merges=possible_merges,
            original_name=member.get("name"),
            person_type="docente_interno",
            institutional_role="integrante_interno",
            validation_status=_status_for_name(member.get("name")),
            status_reason=_reason_for_name(
                member.get("name"),
                "Investigador interno detectado en una seccion institucional clara.",
            ),
            source_section="integrantes_internos",
            source_field="name",
            participation={
                "type": "participacion_institucional",
                "faculty": member.get("faculty"),
                "career": member.get("career"),
            },
            evidence={
                "section": "integrantes_internos",
                "field": "name",
                "text": member.get("name"),
                "faculty": member.get("faculty"),
                "career": member.get("career"),
            },
            action="validated",
        )

    for researcher in _payload_list(payload, "integrantes_externos"):
        external_classification = _classify_external_participant(researcher)
        _register_person(
            registry=registry,
            audit=audit,
            aliases=aliases,
            possible_merges=possible_merges,
            original_name=researcher.get("name"),
            person_type=external_classification["person_type"],
            institutional_role=external_classification["institutional_role"],
            validation_status=_status_for_name(researcher.get("name")),
            status_reason=_reason_for_name(
                researcher.get("name"),
                external_classification["reason"],
            ),
            source_section="integrantes_externos",
            source_field="name",
            participation=external_classification["participation"],
            evidence={
                "section": "integrantes_externos",
                "field": "name",
                "text": researcher.get("name"),
                "institution": researcher.get("institution"),
                "participant_type": researcher.get("participant_type"),
            },
            action=external_classification["action"],
        )

    for project in _payload_list(payload, "proyectos_fci"):
        director = project.get("director")
        if not director or normalize_key(director) == "DIRECTOR NO DETECTADO":
            continue
        _register_person(
            registry=registry,
            audit=audit,
            aliases=aliases,
            possible_merges=possible_merges,
            original_name=director,
            person_type="docente_interno",
            institutional_role="director_proyecto",
            validation_status=_status_for_name(director),
            status_reason=_reason_for_name(
                director,
                "Director detectado en la seccion de proyectos FCI.",
            ),
            source_section="proyectos_fci",
            source_field="director",
            participation={
                "type": "proyecto_fci",
                "code": project.get("code"),
                "name": project.get("name"),
                "role": "director_proyecto",
            },
            evidence={
                "section": "proyectos_fci",
                "field": "director",
                "text": director,
                "project_code": project.get("code"),
            },
            action="validated",
        )

    for responsible in _payload_list(payload, "responsables"):
        role = _functional_role_from_value(responsible.get("role_type"))
        name = responsible.get("name")
        if not role or not name:
            continue
        _register_person(
            registry=registry,
            audit=audit,
            aliases=aliases,
            possible_merges=possible_merges,
            original_name=name,
            person_type="docente_interno",
            institutional_role=role,
            validation_status=_status_for_name(name),
            status_reason=_reason_for_name(
                name,
                "Rol funcional detectado en datos generales del informe.",
            ),
            source_section="director_responsable",
            source_field="name",
            participation={
                "type": "rol_funcional_informe",
                "role": role,
                "source_section": responsible.get("source_section") or "director_responsable",
            },
            evidence={
                "section": responsible.get("source_section") or "director_responsable",
                "field": "name",
                "text": name,
                "role": role,
                "source_page": responsible.get("source_page"),
            },
            action="validated",
        )

    for entity in _payload_list(payload, "research_entities"):
        director = entity.get("director")
        if not director or normalize_key(director) == "DIRECTOR NO DETECTADO":
            continue
        entity_type = str(entity.get("type") or "")
        role = _functional_role_for_entity(entity_type)
        _register_person(
            registry=registry,
            audit=audit,
            aliases=aliases,
            possible_merges=possible_merges,
            original_name=director,
            person_type="docente_interno",
            institutional_role=role,
            validation_status=_status_for_name(director),
            status_reason=_reason_for_name(
                director,
                "Rol funcional detectado desde entidad investigativa.",
            ),
            source_section="research_entities",
            source_field="director",
            participation={
                "type": "entidad_investigativa",
                "entity_type": entity_type or None,
                "code": entity.get("code"),
                "name": entity.get("name"),
                "role": role,
                "status": entity.get("status"),
                "validation_status": entity.get("validation_status"),
            },
            evidence={
                "section": entity.get("source_section") or "research_entities",
                "field": "director",
                "text": director,
                "entity_type": entity_type or None,
                "entity_code": entity.get("code"),
                "entity_name": entity.get("name"),
                "role": role,
            },
            action="validated",
        )

    for product_index, product in enumerate(_payload_list(payload, "produccion_cientifica")):
        authorships: list[dict[str, Any]] = []
        authors = product.get("authors") if isinstance(product.get("authors"), list) else []
        row_or_block_id = str(product.get("row_or_block_id") or f"produccion_cientifica:{product_index + 1}")
        authors_source = str(product.get("authors_source") or "authors_field")
        for author_order, author in enumerate(authors, start=1):
            if authors_source in {"title_fragment", "untrusted_text_fragment"}:
                _discard_invalid(
                    audit=audit,
                    original_text=str(author or ""),
                    source_section=str(product.get("source_section") or "produccion_cientifica"),
                    source_field="authors",
                    reason="Campo de origen no corresponde a persona/autoria verificable; no se crea participante.",
                    product={
                        "title": product.get("title"),
                        "type": product.get("type"),
                        "source_section": product.get("source_section") or "produccion_cientifica",
                        "row_or_block_id": row_or_block_id,
                    },
                )
                continue
            authorship = {
                "type": "autoria_producto",
                "product_index": product_index,
                "row_or_block_id": row_or_block_id,
                "author_order": author_order,
                "product_title": product.get("title"),
                "product_type": product.get("type"),
                "source_section": product.get("source_section") or "produccion_cientifica",
                "source_field": "authors",
                "role": "autor_producto",
            }
            person = _register_person(
                registry=registry,
                audit=audit,
                aliases=aliases,
                possible_merges=possible_merges,
                original_name=author,
                person_type="pendiente_clasificacion",
                production_role="autor_producto",
                validation_status="pendiente_validacion",
                status_reason="Autor detectado, pero no asociado todavia a una categoria institucional.",
                source_section=str(product.get("source_section") or "produccion_cientifica"),
                source_field="authors",
                authorship=authorship,
                evidence={
                    "section": product.get("source_section") or "produccion_cientifica",
                    "field": "authors",
                    "text": author,
                    "product_title": product.get("title"),
                    "row_or_block_id": row_or_block_id,
                    "author_order": author_order,
                },
                product={
                    "title": product.get("title"),
                    "type": product.get("type"),
                    "source_section": product.get("source_section") or "produccion_cientifica",
                    "row_or_block_id": row_or_block_id,
                },
                action="pending_classification",
            )
            if person:
                assigned_authorship = {**authorship, "person_key": person["person_key"], "author_name": person["canonical_name"]}
                authorships.append(assigned_authorship)
                product_author_alias = _alias_for_audit(author, person)
                if product_author_alias:
                    authorships[-1]["alias_applied"] = product_author_alias
        if authorships:
            product["authorships"] = authorships

        for fragment in product.get("discarded_author_fragments", []) or []:
            _discard_invalid(
                audit=audit,
                original_text=fragment,
                source_section=str(product.get("source_section") or "produccion_cientifica"),
                source_field="authors",
                reason="Texto no compatible con nombre de persona; probable fragmento de titulo o produccion cientifica.",
                product={
                    "title": product.get("title"),
                    "type": product.get("type"),
                    "source_section": product.get("source_section") or "produccion_cientifica",
                },
            )

    participants = resolve_participant_identities(sorted(registry.values(), key=lambda item: item["canonical_name"]))
    _sync_resolved_aliases(participants, aliases)
    for participant in participants:
        _refresh_review_metadata(participant)
        _refresh_participant_scope(participant)
    _refresh_audit_participant_scopes(audit, participants)
    summary_counts = _summary_counts(participants, audit, aliases, possible_merges)
    return {
        "normalized_participants": participants,
        "participants_audit": audit,
        "person_aliases": aliases,
        "possible_merge_review": possible_merges,
        "participants_summary": summary_counts,
    }


def _sync_resolved_aliases(participants: list[dict[str, Any]], aliases: list[dict[str, Any]]) -> None:
    for participant in participants:
        canonical_name = str(participant.get("canonical_name") or "")
        for alias in participant.get("aliases") or []:
            alias_text = str(alias or "").strip()
            if not alias_text or normalize_key(alias_text) == normalize_key(canonical_name):
                continue
            _append_unique_dict(
                aliases,
                {
                    "person_key": participant.get("person_key"),
                    "canonical_name": canonical_name,
                    "alias": alias_text,
                    "action": "alias_created",
                    "reason": "Alias conservado despues de resolver la identidad contra la base semilla.",
                },
            )


def enrich_participant_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Add participant scope metadata to persisted summaries without rebuilding them."""
    normalized: list[dict[str, Any]] = []
    for raw_participant in summary.get("normalized_participants") or []:
        if not isinstance(raw_participant, dict):
            continue
        participant = dict(raw_participant)
        _refresh_review_metadata(participant)
        _refresh_participant_scope(participant)
        normalized.append(participant)
    normalized = resolve_participant_identities(normalized)
    for participant in normalized:
        _refresh_review_metadata(participant)
        _refresh_participant_scope(participant)

    aliases = list(summary.get("person_aliases") or [])
    possible_merges = _filter_possible_merges(normalized, list(summary.get("possible_merge_review") or []))
    _clear_stale_merge_statuses(normalized, possible_merges)

    audit: list[dict[str, Any]] = []
    for raw_item in summary.get("participants_audit") or []:
        if isinstance(raw_item, dict):
            audit.append(dict(raw_item))
    _refresh_audit_participant_scopes(audit, normalized)

    participants_summary = _summary_counts(normalized, audit, aliases, possible_merges)
    return {
        "normalized_participants": normalized,
        "participants_audit": audit,
        "person_aliases": aliases,
        "possible_merge_review": possible_merges,
        "participants_summary": participants_summary,
    }


def _refresh_audit_participant_scopes(audit: list[dict[str, Any]], participants: list[dict[str, Any]]) -> None:
    scope_by_key: dict[str, dict[str, Any]] = {}
    for participant in participants:
        for key in (participant.get("person_key"), participant.get("source_person_key")):
            if key:
                scope_by_key[str(key)] = participant
    for item in audit:
        participant = scope_by_key.get(str(item.get("person_key")))
        if not participant and item.get("source_person_key"):
            participant = scope_by_key.get(str(item.get("source_person_key")))
        if participant:
            _copy_participant_scope(item, participant)
        else:
            _refresh_participant_scope(item)


def _payload_list(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload.get(key)
    return value if isinstance(value, list) else []


def _classify_external_participant(researcher: dict[str, Any]) -> dict[str, Any]:
    raw_institution = str(researcher.get("institution") or "").strip()
    participant_type = normalize_key(researcher.get("participant_type"))
    context_key = normalize_key(
        " ".join(
            str(item or "")
            for item in (
                researcher.get("participant_type"),
                researcher.get("institution"),
            )
        )
    )
    institution = normalize_external_institution_display(raw_institution)
    if institution:
        return {
            "person_type": "investigador_externo",
            "institutional_role": "investigador_externo",
            "participation": {
                "type": "participacion_externa",
                "institution": institution,
                "raw_institution": raw_institution or None,
            },
            "reason": "Investigador externo detectado con institucion externa normalizada.",
            "action": "validated",
        }
    if participant_type == "GRADUADO" or "GRADUADO" in context_key or "EGRESADO" in context_key:
        return {
            "person_type": "graduado",
            "institutional_role": "graduado",
            "participation": {
                "type": "participacion_graduado",
                "institution": "Universidad de Guayaquil",
                "raw_institution": raw_institution or None,
            },
            "reason": "Graduado de Universidad de Guayaquil detectado en tabla de participantes; no es investigador externo KPI.",
            "action": "validated",
        }
    if participant_type == "ESTUDIANTE" or "ESTUDIANTE" in context_key or "ALUMNO" in context_key:
        return {
            "person_type": "participante_especial",
            "institutional_role": "participante_semillero",
            "participation": {
                "type": "participante_semillero",
                "institution": "Universidad de Guayaquil",
                "raw_institution": raw_institution or None,
            },
            "reason": "Participante estudiante/semillero detectado; no es investigador externo KPI.",
            "action": "validated",
        }
    return {
        "person_type": "participante_especial",
        "institutional_role": "participante_semillero",
        "participation": {
            "type": "participante_no_externo",
            "institution": None,
            "raw_institution": raw_institution or None,
        },
        "reason": "Participante detectado sin institucion externa valida; no cuenta como investigador externo KPI.",
        "action": "pending_review",
    }


def _field_allows_person(source_section: str, source_field: str, *, production_role: str | None) -> bool:
    section = str(source_section or "").strip().lower()
    field = str(source_field or "").strip().lower()
    if production_role == "autor_producto":
        return section in AUTHOR_SOURCE_SECTIONS and field in AUTHOR_SOURCE_FIELDS
    return (section, field) in PERSON_FIELD_SCHEMA


def _functional_role_from_value(value: Any) -> str | None:
    key = str(value or "").strip().lower()
    return FUNCTIONAL_ROLE_MAP.get(key)


def _functional_role_for_entity(entity_type: str) -> str:
    return ENTITY_ROLE_BY_TYPE.get(str(entity_type or "").strip().lower(), "responsable_informe")


def _register_person(
    *,
    registry: dict[str, dict[str, Any]],
    audit: list[dict[str, Any]],
    aliases: list[dict[str, Any]],
    possible_merges: list[dict[str, Any]],
    original_name: Any,
    person_type: str,
    institutional_role: str | None = None,
    production_role: str | None = None,
    validation_status: str,
    status_reason: str,
    source_section: str,
    source_field: str,
    participation: dict[str, Any] | None = None,
    authorship: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    product: dict[str, Any] | None = None,
    action: str,
) -> dict[str, Any] | None:
    original_text = str(original_name or "").strip()
    candidate_text, name_truncated_reason = _clean_ocr_person_text(original_text)
    if not _field_allows_person(source_section, source_field, production_role=production_role):
        _discard_invalid(
            audit=audit,
            original_text=original_text,
            source_section=source_section,
            source_field=source_field,
            reason="Campo de origen no corresponde a persona; la seccion y el campo no permiten crear participantes.",
            product=product,
        )
        return None
    candidate_tokens = person_key(candidate_text).split()
    allows_truncated_single_token = (
        production_role is None
        and len(candidate_tokens) == 1
        and len(candidate_tokens[0]) >= 4
        and source_section
        in {"integrantes_internos", "integrantes_externos", "proyectos_fci", "research_entities", "director_responsable"}
    )
    if not looks_like_person_name(candidate_text) and not allows_truncated_single_token:
        _discard_invalid(
            audit=audit,
            original_text=original_text,
            source_section=source_section,
            source_field=source_field,
            reason="Texto no compatible con nombre de persona; probable fragmento de titulo, objetivo o encabezado.",
            product=product,
        )
        return None

    if name_truncated_reason:
        validation_status = "pendiente_validacion"
        status_reason = name_truncated_reason
    canonical_name = title_case_name(candidate_text)
    key = person_key(canonical_name)
    matched_key, match_reason, match_confidence = _find_match(registry, canonical_name)
    matched_existing_person = matched_key is not None
    if matched_key is None:
        has_possible_merge = _collect_possible_merges(registry, canonical_name, possible_merges)
        person = _new_person(key, canonical_name, person_type, validation_status, status_reason)
        if has_possible_merge and production_role != "autor_producto":
            person["validation_status"] = "pendiente_merge"
            person["status_reason"] = "Posible duplicado detectado; requiere revision antes de fusionar."
            person["review_reason"] = person["status_reason"]
        registry[key] = person
        matched_key = key
        match_reason = None
        match_confidence = 0.0
    else:
        person = registry[matched_key]
        person["matched_existing_person"] = True
        if match_confidence > float(person.get("match_confidence") or 0):
            person["match_confidence"] = round(match_confidence, 2)
            person["match_reason"] = match_reason
        _upgrade_person_type(person, person_type)
        _merge_validation_status(person, validation_status, status_reason)
        alias = _alias_for_person(canonical_name, person)
        if alias:
            _append_unique(person["aliases"], alias)
            aliases.append(
                {
                    "person_key": person["person_key"],
                    "canonical_name": person["canonical_name"],
                    "alias": alias,
                    "action": "alias_created",
                    "reason": match_reason or "Coincidencia por normalizacion de nombre.",
                }
            )
            _prefer_more_complete_name(person, canonical_name)

    _append_unique(person["original_texts"], original_text)
    _append_unique(person["source_sections"], source_section)
    _append_unique(person["source_fields"], source_field)
    person["matched_existing_person"] = bool(person.get("matched_existing_person") or matched_existing_person)
    if matched_existing_person and match_confidence > float(person.get("match_confidence") or 0):
        person["match_confidence"] = round(match_confidence, 2)
        person["match_reason"] = match_reason
    if institutional_role:
        _append_unique(person["institutional_roles"], institutional_role)
    if production_role:
        _append_unique(person["production_roles"], production_role)
    duplicate_evidence = False
    if participation:
        duplicate_evidence = not _append_unique_dict(person["participations"], participation) or duplicate_evidence
    if authorship:
        duplicate_evidence = not _append_unique_dict(person["authorships"], authorship) or duplicate_evidence
    if evidence:
        duplicate_evidence = not _append_unique_dict(person["evidences"], evidence) or duplicate_evidence
    person["products_authored_count"] = len(
        {
            str(item.get("row_or_block_id") or item.get("product_title") or item.get("product_index"))
            for item in person.get("authorships", [])
            if isinstance(item, dict)
        }
    )
    person["name_truncated"] = bool(person.get("name_truncated") or name_truncated_reason or _looks_incomplete(candidate_text))
    if name_truncated_reason:
        person["status_reason"] = name_truncated_reason
        person["review_reason"] = name_truncated_reason
    _refresh_review_metadata(person)
    _refresh_participant_scope(person)
    person["exclusion_reason"] = None if person.get("kpi_eligible") else person.get("kpi_reason")

    resolved_action = action
    review_bucket = person["review_bucket"]
    show_in_participants = person["show_in_participants"]
    if duplicate_evidence:
        review_bucket = "duplicate_evidence"
        show_in_participants = False
    if production_role == "autor_producto" and person["person_type"] != "pendiente_clasificacion":
        resolved_action = "validated"

    audit.append(
        {
            "pdf": None,
            "source_file": None,
            "seccion": source_section,
            "source_section": source_section,
            "campo_origen": source_field,
            "source_field": source_field,
            "texto_original": original_text,
            "original_text": original_text,
            "persona_normalizada": person["canonical_name"],
            "normalized_person": person["canonical_name"],
            "alias_aplicado": _alias_for_audit(original_text, person),
            "aliases": list(person["aliases"]),
            "tipo_persona": person["person_type"],
            "person_type": person["person_type"],
            "rol_asignado": production_role or institutional_role,
            "roles_institucionales": list(person["institutional_roles"]),
            "institutional_roles": list(person["institutional_roles"]),
            "roles_produccion": list(person["production_roles"]),
            "production_roles": list(person["production_roles"]),
            "estado_validacion": person["validation_status"],
            "validation_status": person["validation_status"],
            "motivo_estado": person["status_reason"],
            "review_reason": person["review_reason"],
            "accepted_as_person": True,
            "detected_as_person": True,
            "razon_aceptacion": person["kpi_reason"],
            "razon_descarte": None,
            "participacion_asociada": participation,
            "producto_asociado": product,
            "evidences": list(person["evidences"]),
            "original_texts": list(person["original_texts"]),
            "matched_existing_person": person["matched_existing_person"],
            "match_confidence": person["match_confidence"],
            "match_reason": person["match_reason"],
            "name_truncated": person["name_truncated"],
            "products_authored_count": person["products_authored_count"],
            "products_entity_count": person["products_entity_count"],
            "exclusion_reason": person["exclusion_reason"],
            "show_in_participants": show_in_participants,
            "kpi_eligible": person["kpi_eligible"],
            "kpi_reason": person["kpi_reason"],
            "participant_scope": person["participant_scope"],
            "institution_scope": person["institution_scope"],
            "faculty_scope": person["faculty_scope"],
            "detected_faculty": person["detected_faculty"],
            "participant_scope_reason": person["participant_scope_reason"],
            "alcance_participante": person["participant_scope"],
            "facultad_detectada": person["detected_faculty"],
            "motivo_alcance": person["participant_scope_reason"],
            "accion_aplicada": resolved_action,
            "action": resolved_action,
            "review_bucket": review_bucket,
            "person_key": person["person_key"],
        }
    )
    return person


def _new_person(
    key: str,
    canonical_name: str,
    person_type: str,
    validation_status: str,
    status_reason: str,
) -> dict[str, Any]:
    kpi_eligible, kpi_reason = _kpi_eligibility(person_type)
    return {
        "person_key": key,
        "canonical_name": canonical_name,
        "aliases": [],
        "person_type": person_type,
        "institutional_roles": [],
        "production_roles": [],
        "participations": [],
        "authorships": [],
        "evidences": [],
        "original_texts": [],
        "source_sections": [],
        "source_fields": [],
        "matched_existing_person": False,
        "match_confidence": 0.0,
        "match_reason": None,
        "name_truncated": _looks_incomplete(canonical_name),
        "products_authored_count": 0,
        "products_entity_count": 0,
        "exclusion_reason": None if kpi_eligible else kpi_reason,
        "validation_status": validation_status,
        "status_reason": status_reason,
        "review_reason": status_reason,
        "kpi_eligible": kpi_eligible,
        "kpi_reason": kpi_reason,
        "participant_scope": "pending",
        "institution_scope": "pending",
        "faculty_scope": "pending",
        "detected_faculty": None,
        "participant_scope_reason": "Clasificacion institucional pendiente.",
        "show_in_participants": True,
        "review_bucket": "valid_person",
    }


def _find_match(registry: dict[str, dict[str, Any]], name: str) -> tuple[str | None, str | None, float]:
    key = person_key(name)
    if key in registry:
        return key, "Coincidencia exacta por clave normalizada.", 1.0

    tokens = set(key.split())
    best_key: str | None = None
    best_score = 0.0
    for existing_key in registry:
        existing_tokens = set(existing_key.split())
        if tokens == existing_tokens:
            return existing_key, "Coincidencia por los mismos tokens de nombre en distinto orden.", 1.0
        if len(tokens) >= 2 and len(existing_tokens) >= 2 and (
            tokens.issubset(existing_tokens) or existing_tokens.issubset(tokens)
        ) and heuristic_name_merge_allowed(key, existing_key):
            return existing_key, "Coincidencia por alias parcial compatible.", 0.94
        token_score = _token_alias_score(tokens, existing_tokens)
        if token_score >= 0.98 and heuristic_name_merge_allowed(key, existing_key):
            return existing_key, "Coincidencia por tokens equivalentes considerando OCR, orden o nombre parcial.", token_score
        score = fuzzy_score(key, existing_key)
        if score > best_score:
            best_key = existing_key
            best_score = score

    if best_key and best_score >= AUTO_MERGE_SCORE and heuristic_name_merge_allowed(key, best_key):
        return best_key, f"Coincidencia difusa alta ({best_score:.1f}).", best_score / 100
    return None, None, 0.0


def _collect_possible_merges(
    registry: dict[str, dict[str, Any]],
    name: str,
    possible_merges: list[dict[str, Any]],
) -> bool:
    key = person_key(name)
    found = False
    for existing_key, existing in registry.items():
        score = _merge_review_score(key, existing_key)
        if REVIEW_MERGE_SCORE <= score < AUTO_MERGE_SCORE:
            candidate = {
                "left_person_key": existing["person_key"],
                "left_name": existing["canonical_name"],
                "right_person_key": key,
                "right_name": title_case_name(name),
                "score": round(score, 1),
                "status": "pending_merge",
                "action": "pending_merge",
            }
            if _append_unique_dict(possible_merges, candidate):
                found = True
    return found


def _merge_review_score(left_key: str, right_key: str) -> float:
    if not heuristic_name_merge_allowed(left_key, right_key):
        return 0.0
    left_tokens = set(person_key(left_key).split())
    right_tokens = set(person_key(right_key).split())
    return max(
        fuzzy_score(left_key, right_key),
        _token_alias_score(left_tokens, right_tokens) * 100,
    )


def _filter_possible_merges(participants: list[dict[str, Any]], possible_merges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    participants_by_key: dict[str, dict[str, Any]] = {}
    for participant in participants:
        for key in (participant.get("person_key"), participant.get("source_person_key")):
            if key:
                participants_by_key[str(key)] = participant

    filtered: list[dict[str, Any]] = []
    for merge in possible_merges:
        if not isinstance(merge, dict):
            continue
        left_key = str(merge.get("left_person_key") or "")
        right_key = str(merge.get("right_person_key") or "")
        left = participants_by_key.get(left_key)
        right = participants_by_key.get(right_key)
        if not left or not right or left is right:
            continue
        score = _merge_review_score(person_key(left.get("canonical_name") or left_key), person_key(right.get("canonical_name") or right_key))
        if REVIEW_MERGE_SCORE <= score < AUTO_MERGE_SCORE:
            candidate = dict(merge)
            candidate["score"] = round(score, 1)
            if _append_unique_dict(filtered, candidate):
                continue
    return filtered


def _clear_stale_merge_statuses(participants: list[dict[str, Any]], possible_merges: list[dict[str, Any]]) -> None:
    pending_keys: set[str] = set()
    for merge in possible_merges:
        for key in (merge.get("left_person_key"), merge.get("right_person_key")):
            if key:
                pending_keys.add(str(key))

    for participant in participants:
        key = str(participant.get("person_key") or participant.get("source_person_key") or "")
        if key in pending_keys:
            continue
        if participant.get("validation_status") == "pendiente_merge" or participant.get("review_bucket") == "pending_merge":
            participant["validation_status"] = "validado"
            participant["status_reason"] = "Posible duplicado descartado por reglas actuales de identidad."
            participant["review_reason"] = participant["status_reason"]
            _refresh_review_metadata(participant)
            _refresh_participant_scope(participant)


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


def _upgrade_person_type(person: dict[str, Any], candidate_type: str) -> None:
    current_priority = PERSON_TYPE_PRIORITY.get(person["person_type"], 0)
    candidate_priority = PERSON_TYPE_PRIORITY.get(candidate_type, 0)
    if candidate_priority > current_priority:
        person["person_type"] = candidate_type
    person["kpi_eligible"], person["kpi_reason"] = _kpi_eligibility(person["person_type"])
    _refresh_review_metadata(person)


def _merge_validation_status(person: dict[str, Any], candidate_status: str, reason: str) -> None:
    if person["person_type"] != "pendiente_clasificacion" and candidate_status == "pendiente_validacion":
        return
    if person["validation_status"] != "validado":
        person["validation_status"] = candidate_status
        person["status_reason"] = reason
        person["review_reason"] = reason
        _refresh_review_metadata(person)
    elif candidate_status != "validado":
        person["validation_status"] = candidate_status
        person["status_reason"] = reason
        person["review_reason"] = reason
        _refresh_review_metadata(person)


def _prefer_more_complete_name(person: dict[str, Any], candidate_name: str) -> None:
    current_tokens = person_key(person["canonical_name"]).split()
    candidate_tokens = person_key(candidate_name).split()
    if len(candidate_tokens) > len(current_tokens) and candidate_tokens[: len(current_tokens)] == current_tokens:
        return
    if len(candidate_tokens) > len(current_tokens):
        _append_unique(person["aliases"], person["canonical_name"])
        person["canonical_name"] = title_case_name(candidate_name)


def _alias_for_person(name: str, person: dict[str, Any]) -> str | None:
    candidate = title_case_name(name)
    if normalize_key(candidate) == normalize_key(person["canonical_name"]):
        return None
    return candidate


def _alias_for_audit(original_text: Any, person: dict[str, Any]) -> str | None:
    original = title_case_name(str(original_text or ""))
    if normalize_key(original) == normalize_key(person["canonical_name"]):
        return None
    return original


def _clean_ocr_person_text(value: str) -> tuple[str, str | None]:
    text = " ".join(str(value or "").replace(".", " ").replace(":", " ").split()).strip(" -")
    tokens = normalize_key(text).split()
    if len(tokens) >= 2 and len(tokens[0]) <= 2 and tokens[0] not in {"DE", "LA", "EL"}:
        cleaned = " ".join(tokens[1:])
        return cleaned, "Nombre con prefijo OCR corto removido; posible nombre truncado que requiere validacion."
    return text, None


def _status_for_name(value: Any) -> str:
    return "pendiente_validacion" if _looks_incomplete(value) else "validado"


def _reason_for_name(value: Any, default: str) -> str:
    if _looks_incomplete(value):
        return "Nombre corto, incompleto o truncado; requiere validacion de identidad."
    return default


def _looks_incomplete(value: Any) -> bool:
    text = str(value or "")
    key = person_key(text)
    tokens = key.split()
    if any(len(token) <= 2 for token in tokens):
        return True
    return "..." in text or "…" in text or len(tokens) < 3


def _discard_invalid(
    *,
    audit: list[dict[str, Any]],
    original_text: str,
    source_section: str,
    source_field: str,
    reason: str,
    product: dict[str, Any] | None = None,
) -> None:
    if not original_text:
        return
    audit.append(
        {
            "pdf": None,
            "source_file": None,
            "seccion": source_section,
            "source_section": source_section,
            "campo_origen": source_field,
            "source_field": source_field,
            "texto_original": original_text,
            "original_text": original_text,
            "persona_normalizada": None,
            "normalized_person": None,
            "alias_aplicado": None,
            "aliases": [],
            "tipo_persona": None,
            "person_type": None,
            "rol_asignado": None,
            "roles_institucionales": [],
            "institutional_roles": [],
            "roles_produccion": [],
            "production_roles": [],
            "estado_validacion": "descartado",
            "validation_status": "descartado",
            "motivo_estado": reason,
            "review_reason": reason,
            "accepted_as_person": False,
            "detected_as_person": False,
            "razon_aceptacion": None,
            "razon_descarte": reason,
            "participacion_asociada": None,
            "producto_asociado": product,
            "evidences": [],
            "show_in_participants": False,
            "kpi_eligible": False,
            "kpi_reason": "Texto descartado; no es persona normalizada.",
            "participant_scope": "discarded",
            "institution_scope": "discarded",
            "faculty_scope": "discarded",
            "detected_faculty": None,
            "participant_scope_reason": "Texto descartado; no es persona normalizada.",
            "alcance_participante": "discarded",
            "facultad_detectada": None,
            "motivo_alcance": "Texto descartado; no es persona normalizada.",
            "accion_aplicada": "discarded_invalid",
            "action": "discarded_invalid",
            "review_bucket": "invalid_text_fragment",
            "person_key": None,
        }
    )


def _append_unique(items: list[Any], value: Any) -> None:
    if value not in items:
        items.append(value)


def _append_unique_dict(items: list[dict[str, Any]], value: dict[str, Any]) -> bool:
    marker = normalize_key(repr(sorted(value.items())))
    if all(normalize_key(repr(sorted(item.items()))) != marker for item in items):
        items.append(value)
        return True
    return False


def _count_type(participants: list[dict[str, Any]], person_type: str) -> int:
    return sum(1 for participant in participants if participant["person_type"] == person_type)


def _kpi_eligibility(person_type: str) -> tuple[bool, str]:
    if person_type == "docente_interno":
        return True, "Cuenta para KPIs de docentes internos por tener evidencia institucional clara."
    if person_type == "investigador_externo":
        return True, "Cuenta para KPIs de investigadores externos por tener evidencia de institucion externa."
    return False, "No cuenta para KPIs principales hasta completar clasificacion institucional."


def _refresh_review_metadata(person: dict[str, Any]) -> None:
    person["show_in_participants"] = True
    person["review_bucket"] = _review_bucket_for_person(person)
    if person["review_bucket"] == "pending_merge":
        person["kpi_eligible"] = False
        person["kpi_reason"] = "No cuenta para KPIs hasta resolver posible duplicado."
    elif person["review_bucket"] == "pending_person":
        person["kpi_eligible"] = False
        person["kpi_reason"] = "No cuenta para KPIs hasta validar identidad incompleta o ambigua."
    elif person["review_bucket"] == "valid_person":
        person["kpi_eligible"], person["kpi_reason"] = _kpi_eligibility(person.get("person_type") or "")


def _refresh_participant_scope(person: dict[str, Any]) -> None:
    scope, detected_faculty, reason = _participant_scope(person)
    person["participant_scope"] = scope
    person["institution_scope"] = scope
    person["faculty_scope"] = scope
    person["detected_faculty"] = detected_faculty
    person["participant_scope_reason"] = reason


def _participant_scope(person: dict[str, Any]) -> tuple[str, str | None, str]:
    validation_status = person.get("validation_status") or person.get("estado_validacion")
    if validation_status == "descartado" or person.get("accepted_as_person") is False:
        return "discarded", None, "Texto descartado; no es persona normalizada."

    person_type = person.get("person_type") or person.get("tipo_persona")
    review_bucket = person.get("review_bucket")
    detected_faculty = _best_detected_faculty(person)

    if review_bucket in {"pending_person", "pending_author_classification", "pending_merge"} or validation_status in {
        "pendiente_validacion",
        "pendiente_clasificacion",
        "pendiente_merge",
    }:
        return "pending", detected_faculty, person.get("review_reason") or person.get("status_reason") or "Pendiente de revision."

    if person_type == "docente_interno":
        if detected_faculty:
            if _is_fca_faculty(detected_faculty):
                return (
                    "internal_fca",
                    detected_faculty,
                    "Suma como interno FCA por evidencia PDF con facultad Ciencias Administrativas.",
                )
            return (
                "internal_other_faculty",
                detected_faculty,
                (
                    "Suma como interno UG por evidencia PDF, pero se clasifica como interno de otra facultad "
                    f"porque la facultad detectada es {detected_faculty}."
                ),
            )
        return (
            "internal_fca",
            None,
            "Suma como interno FCA por evidencia PDF institucional; no se detecto otra facultad.",
        )

    if person_type == "investigador_externo":
        return "external", detected_faculty, "Investigador externo validado por evidencia PDF de institucion externa."

    return (
        "pending",
        detected_faculty,
        "Participante detectado sin clasificacion interna/externa KPI; queda pendiente para desglose institucional.",
    )


def _best_detected_faculty(person: dict[str, Any]) -> str | None:
    faculties = _participant_faculties(person)
    fca = next((faculty for faculty in faculties if _is_fca_faculty(faculty)), None)
    if fca:
        return fca
    return faculties[0] if faculties else None


def _participant_faculties(person: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    for collection_key in ("participations", "evidences"):
        for item in person.get(collection_key) or []:
            if isinstance(item, dict):
                _extend_faculty_candidates(candidates, item)
    for item_key in ("participacion_asociada", "participation"):
        item = person.get(item_key)
        if isinstance(item, dict):
            _extend_faculty_candidates(candidates, item)
    _extend_faculty_candidates(candidates, person)
    return _unique_clean_values(candidates)


def _extend_faculty_candidates(target: list[str], item: dict[str, Any]) -> None:
    for key in ("faculty", "raw_faculty", "normalized_faculty", "detected_faculty", "facultad_detectada"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            target.append(value.strip())


def _unique_clean_values(values: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = normalize_key(value)
        if key and key not in seen:
            unique.append(value)
            seen.add(key)
    return unique


def _is_fca_faculty(value: Any) -> bool:
    key = normalize_key(value)
    return FCA_FACULTY_KEY in key


def _copy_participant_scope(target: dict[str, Any], source: dict[str, Any]) -> None:
    target["source_person_key"] = source.get("source_person_key") or target.get("person_key")
    target["source_canonical_name"] = source.get("source_canonical_name") or target.get("normalized_person")
    target["person_key"] = source.get("person_key")
    target["persona_normalizada"] = source.get("canonical_name")
    target["normalized_person"] = source.get("canonical_name")
    target["identity_resolution"] = source.get("identity_resolution")
    target["aliases"] = list(source.get("aliases") or target.get("aliases") or [])
    for key in (
        "participant_scope",
        "institution_scope",
        "faculty_scope",
        "detected_faculty",
        "participant_scope_reason",
    ):
        target[key] = source.get(key)
    target["alcance_participante"] = source.get("participant_scope")
    target["facultad_detectada"] = source.get("detected_faculty")
    target["motivo_alcance"] = source.get("participant_scope_reason")


def _review_bucket_for_person(person: dict[str, Any]) -> str:
    if person.get("validation_status") == "pendiente_merge":
        return "pending_merge"
    if (
        person.get("person_type") == "pendiente_clasificacion"
        and "autor_producto" in (person.get("production_roles") or [])
        and not (person.get("institutional_roles") or [])
    ):
        return "pending_author_classification"
    if person.get("validation_status") in {"pendiente_validacion", "pendiente_clasificacion"}:
        return "pending_person"
    return "valid_person"


def _summary_counts(
    participants: list[dict[str, Any]],
    audit: list[dict[str, Any]],
    aliases: list[dict[str, Any]],
    possible_merges: list[dict[str, Any]],
) -> dict[str, int]:
    pending_people = _count_bucket(participants, "pending_person")
    pending_authors = _count_bucket(participants, "pending_author_classification")
    pending_merges = _count_bucket(participants, "pending_merge") + len(possible_merges)
    invalid_fragments = sum(1 for item in audit if item.get("review_bucket") == "invalid_text_fragment")
    duplicate_evidence = sum(1 for item in audit if item.get("review_bucket") == "duplicate_evidence")
    return {
        "total": len(participants),
        "show_in_participants_count": sum(1 for item in participants if item.get("show_in_participants")),
        "docente_interno": _count_type(participants, "docente_interno"),
        "investigador_externo": _count_type(participants, "investigador_externo"),
        "kpi_eligible": sum(1 for item in participants if item.get("kpi_eligible")),
        "pendiente_clasificacion": _count_type(participants, "pendiente_clasificacion"),
        "pending_people_count": pending_people,
        "pending_author_classification_count": pending_authors,
        "pending_product_count": 0,
        "pending_entity_count": 0,
        "pending_ocr_count": 0,
        "pending_merge_count": pending_merges,
        "invalid_text_fragments_count": invalid_fragments,
        "duplicate_evidence_count": duplicate_evidence,
        "autores_pendientes_clasificacion": pending_authors,
        "aliases": len(aliases),
        "possible_merges": len(possible_merges),
        "discarded_invalid": invalid_fragments,
        **_participant_scope_counts(participants, audit),
    }


def _count_bucket(participants: list[dict[str, Any]], bucket: str) -> int:
    return sum(1 for participant in participants if participant.get("review_bucket") == bucket)


def _participant_scope_counts(participants: list[dict[str, Any]], audit: list[dict[str, Any]] | None = None) -> dict[str, int]:
    counts = {
        "internal_fca_count": 0,
        "internal_other_faculty_count": 0,
        "external_count": 0,
        "pending_scope_count": 0,
        "discarded_scope_count": 0,
    }
    for participant in participants:
        scope = participant.get("participant_scope")
        if scope not in PARTICIPANT_SCOPES:
            scope, _, _ = _participant_scope(participant)
        if scope == "internal_fca":
            counts["internal_fca_count"] += 1
        elif scope == "internal_other_faculty":
            counts["internal_other_faculty_count"] += 1
        elif scope == "external":
            counts["external_count"] += 1
        elif scope == "discarded":
            counts["discarded_scope_count"] += 1
        else:
            counts["pending_scope_count"] += 1
    if audit:
        counts["discarded_scope_count"] += sum(
            1 for item in audit if item.get("participant_scope") == "discarded" or item.get("review_bucket") == "invalid_text_fragment"
        )
    return counts
