from __future__ import annotations

import re

from .models import ParseLog
from .normalizer import looks_like_url, normalize_key, normalize_line, rebuild_url
from .validators import (
    title_case_name,
    validate_career,
    validate_faculty,
    validate_institution,
    validate_status,
    looks_like_person_name,
)
from app.services.import_reconciliation import product_title_quality
from app.services.institution_normalizer import normalize_external_institution_display


MONTHS = {"ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"}
STATUS_PATTERNS = (
    (("ENVIADO", "A", "REVISION"), "ENVIADO A REVISION"),
    (("ENVIADO A REVISION",), "ENVIADO A REVISION"),
    (("EN", "REVISION"), "EN REVISION"),
    (("EN REVISION",), "EN REVISION"),
    (("PUBLICADO",), "PUBLICADO"),
    (("ACEPTADO",), "ACEPTADO"),
)
PRODUCT_TITLE_TOKENS = {
    "ANALISIS",
    "BANCARIO",
    "CADENA",
    "CAPITAL",
    "CONOCIMI",
    "CONOCIMIENTO",
    "COOPERATI",
    "CREDITO",
    "ECONOMIC",
    "ECONOMICO",
    "ECUADOR",
    "ECUATORIA",
    "EFICIENCIA",
    "EMPRESAS",
    "ESTRUCTURA",
    "FINANCIERO",
    "GESTION",
    "IMPACT",
    "IMPACTO",
    "INCIDENCIA",
    "INNOVACIO",
    "MODELO",
    "NUBE",
    "PANDEMIA",
    "PORTUARIAS",
    "PYMES",
    "RENTABILID",
    "SUMINISTR",
    "TECNICA",
    "TRABAJO",
    "TURISMO",
}
COMMON_FIRST_NAMES = {
    "ANIBAL",
    "CARLA",
    "CARLOS",
    "DANIEL",
    "DENNISE",
    "EMILIO",
    "FERNANDO",
    "HENRY",
    "JENIFFER",
    "JOSE",
    "JOSUE",
    "KAREN",
    "MARIA",
    "ROBIN",
    "WENDY",
    "YAIMARA",
}

FIELD_LABELS = {
    "NOMBRE COMPLETO",
    "NOMBRE COMPLE TO",
    "FACULTAD",
    "CARRERA",
    "FECHA DE INGRESO",
    "INSTITUCION EXTERNA",
    "INSTITUCIÓN EXTERNA",
    "TITULO",
    "TÍTULO",
    "EDITORIAL",
    "AUTOR",
    "AUTOR 1",
    "AUTOR 2",
    "AUTOR 3",
    "AUTOR 4",
    "AUTOR 5",
    "ESTADO",
    "IMPACTO",
    "IMPACT",
    "LINK",
    "TO",
    "O",
}

WRAPPED_WORD_SUFFIXES = {"l", "n", "o", "ad", "al", "ento", "nal", "nas", "os", "vas", "vo"}


def _is_page_break(line: str | None) -> bool:
    text = str(line or "").strip()
    key = normalize_key(text)
    return bool(
        re.fullmatch(r"\[\[PAGE_BREAK(?::\d+)?\]\]", text, flags=re.IGNORECASE)
        or re.fullmatch(r"\d+\s+DE\s+\d+", key)
        or re.fullmatch(r"\d+/\d+", key)
    )


def _skip_headers(line: str) -> bool:
    key = normalize_key(line)
    if (
        _is_page_break(line)
        or
        key.startswith("FORMULARIO DE INFORME")
        or "FORM.JOTFORM" in key
        or "AGREGAR NUEVO REGISTRO" in key
        or re.fullmatch(r"\d+\s+DE\s+\d+", key)
        or re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}.*", key)
    ):
        return True
    return key in {
        "ABOUT",
        "IMPACT",
        "O",
        "NOMBRE COMPLETO",
        "FACULTAD",
        "CARRERA",
        "FECHA DE INGRESO",
        "INSTITUCION EXTERNA",
        "INSTITUCIÓN EXTERNA",
        "INVESTIGADOR",
        "TITULO",
        "AUTOR 1",
        "AUTOR 2",
        "AUTOR 3",
        "AUTOR 4",
        "AUTOR 5",
        "ESTADO",
        "IMPACTO",
        "LINK",
        "+AGREGAR NUEVO REGISTRO",
    }


def parse_internal_members(lines: list[str], logs: list[ParseLog]) -> list[dict]:
    stop_markers = (
        "INVESTIGADORES QUE SE DESVINCULAN",
        "INVESTIGADORES EXTERNOS",
        "ESTUDIANTES QUE PARTICIPAN",
        "RESUMEN DEL PROYECTO",
        "SECCION 2",
    )
    bounded_lines: list[str] = []
    for line in lines:
        key = normalize_key(line)
        if any(marker in key for marker in stop_markers):
            break
        bounded_lines.append(line)
    label_value_members = _parse_internal_label_value_members(bounded_lines, logs)
    segment = [line for line in bounded_lines if not _skip_headers(line)]
    members: list[dict] = []
    index = 0
    while index < len(segment):
        name_parts = [segment[index]]
        if index + 1 < len(segment) and looks_like_person_name(f"{segment[index]} {segment[index + 1]}"):
            name_parts.append(segment[index + 1])
        name = " ".join(name_parts)
        if not looks_like_person_name(name) or not _valid_candidate_person_text(name):
            index += 1
            continue
        cursor = index + len(name_parts)
        faculty_parts: list[str] = []
        career_parts: list[str] = []
        while cursor < len(segment):
            key = normalize_key(segment[cursor])
            if any(month in key for month in MONTHS) or looks_like_person_name(segment[cursor]):
                break
            if _is_date_like(key):
                break
            candidate_career, career_candidate_score, _ = validate_career(segment[cursor])
            faculty_so_far = " ".join(faculty_parts)
            faculty_complete = "ADMINISTRATIVA" in normalize_key(faculty_so_far) or validate_faculty(faculty_so_far)[1] >= 90
            if "LICENCIATURA" in key or career_parts or career_candidate_score >= 82 or faculty_complete:
                career_parts.append(segment[cursor])
            else:
                faculty_parts.append(segment[cursor])
            cursor += 1
        career, career_score, career_review = validate_career(_clean_career_text(" ".join(career_parts)))
        faculty, faculty_score, faculty_review = validate_faculty(" ".join(faculty_parts))
        if career or name:
            members.append({
                "name": title_case_name(name),
                "faculty": faculty,
                "career": career,
                "source_section": "integrantes_internos",
                "requires_review": not bool(career),
                "reason": None if career else "Integrante interno detectado, pero la carrera no pudo validarse automaticamente.",
            })
            logs.append(ParseLog("integrantes_internos.name", name, 100, "layout_table", table="integrantes_internos"))
            logs.append(ParseLog("integrantes_internos.career", career, career_score, "catalog_fuzzy", table="integrantes_internos", requires_review=career_review or not bool(career)))
            if faculty:
                logs.append(ParseLog("integrantes_internos.faculty", faculty, faculty_score, "catalog_fuzzy", table="integrantes_internos", requires_review=faculty_review))
        index = max(cursor, index + 1)
    return _merge_people_by_raw_name([*label_value_members, *members])


def _parse_internal_label_value_members(lines: list[str], logs: list[ParseLog]) -> list[dict]:
    members: list[dict] = []
    index = 0
    while index < len(lines):
        if not _is_name_label(lines[index]):
            index += 1
            continue
        raw_name, cursor = _collect_until_label(lines, index + 1, {"FACULTAD", "CARRERA", "FECHA DE INGRESO", "RESUMEN", "SECCION"})
        raw_faculty = ""
        raw_career = ""
        while cursor < len(lines):
            key = normalize_key(lines[cursor])
            if _is_name_label(lines[cursor]) or key.startswith(("RESUMEN", "SECCION")):
                break
            if key == "FACULTAD":
                raw_faculty, cursor = _collect_until_label(lines, cursor + 1, {"CARRERA", "FECHA DE INGRESO", "NOMBRE COMPLETO", "RESUMEN", "SECCION"})
                continue
            if key == "CARRERA":
                raw_career, cursor = _collect_until_label(lines, cursor + 1, {"FECHA DE INGRESO", "NOMBRE COMPLETO", "RESUMEN", "SECCION"})
                continue
            cursor += 1
        name = _clean_wrapped_person_name(raw_name)
        if not name or not _valid_candidate_person_text(name):
            index = max(cursor, index + 1)
            continue
        career, career_score, career_review = validate_career(_clean_career_text(raw_career))
        faculty, faculty_score, faculty_review = validate_faculty(raw_faculty)
        members.append(
            {
                "name": title_case_name(name),
                "faculty": faculty or raw_faculty or None,
                "career": career or raw_career or None,
                "source_section": "integrantes_internos",
                "requires_review": not bool(career),
                "reason": None if career else "Integrante interno detectado en tabla vertical, pero la carrera requiere revision.",
            }
        )
        logs.append(ParseLog("integrantes_internos.name", name, 95, "jotform_label_value", table="integrantes_internos"))
        logs.append(
            ParseLog(
                "integrantes_internos.career",
                career or raw_career,
                career_score,
                "catalog_fuzzy",
                table="integrantes_internos",
                requires_review=career_review or not bool(career),
            )
        )
        if faculty or raw_faculty:
            logs.append(
                ParseLog(
                    "integrantes_internos.faculty",
                    faculty or raw_faculty,
                    faculty_score,
                    "catalog_fuzzy",
                    table="integrantes_internos",
                    requires_review=faculty_review or not bool(faculty),
                )
            )
        index = max(cursor, index + 1)
    return members


def parse_external_members(lines: list[str], logs: list[ParseLog]) -> list[dict]:
    stop_markers = ("RESUMEN DEL PROYECTO", "SECCION 2")
    bounded_lines: list[str] = []
    for line in lines:
        key = normalize_key(line)
        if any(marker in key for marker in stop_markers):
            break
        bounded_lines.append(line)
    label_value_members = _parse_external_label_value_members(bounded_lines, logs)
    segment = [line for line in bounded_lines if not _skip_headers(line)]
    members: list[dict] = []
    index = 0
    while index < len(segment):
        if index + 1 < len(segment) and not looks_like_person_name(segment[index]) and looks_like_person_name(segment[index + 1]):
            participant_type = _external_participant_type(f"{segment[index]} {segment[index + 1]}")
            institution = validate_institution(segment[index])
            if institution:
                name = title_case_name(segment[index + 1])
                members.append({
                    "name": name,
                    "institution": institution,
                    "is_external": "true",
                    "participant_type": participant_type,
                    "source_section": "integrantes_externos",
                })
                logs.append(ParseLog("integrantes_externos.name", name, 100, "layout_table", table="integrantes_externos"))
                logs.append(ParseLog("integrantes_externos.institution", institution, 90, "institution_validation", table="integrantes_externos"))
                index += 2
                continue

        if not looks_like_person_name(segment[index]):
            index += 1
            continue
        name = title_case_name(segment[index])
        cursor = index + 1
        institution_parts: list[str] = []
        while cursor < len(segment) and not looks_like_person_name(segment[cursor]):
            key = normalize_key(segment[cursor])
            if key.startswith(("17", "18")):
                break
            if not re.fullmatch(r"\d+\s+DE\s+\d+", key) and "JOTFORM" not in key and not looks_like_url(segment[cursor]):
                institution_parts.append(segment[cursor])
            cursor += 1
        raw_context = " ".join([segment[index], *institution_parts])
        participant_type = _external_participant_type(raw_context)
        institution = validate_institution(" ".join(institution_parts))
        if institution:
            members.append({
                "name": name,
                "institution": institution,
                "is_external": "true",
                "participant_type": participant_type,
                "source_section": "integrantes_externos",
            })
            logs.append(ParseLog("integrantes_externos.name", name, 100, "layout_table", table="integrantes_externos"))
            logs.append(ParseLog("integrantes_externos.institution", institution, 90, "institution_validation", table="integrantes_externos"))
        elif participant_type in {"estudiante", "graduado"}:
            members.append({
                "name": name,
                "institution": "Universidad de Guayaquil",
                "is_external": "false",
                "participant_type": participant_type,
                "source_section": "integrantes_externos",
                "requires_review": True,
                "reason": "Participante estudiante/graduado detectado sin institucion externa valida.",
            })
            logs.append(
                ParseLog(
                    "integrantes_externos.participant",
                    raw_context,
                    70,
                    "participant_context",
                    table="integrantes_externos",
                    requires_review=True,
                )
            )
        index = max(cursor, index + 1)
    return _merge_people_by_raw_name([*label_value_members, *members])


def _parse_external_label_value_members(lines: list[str], logs: list[ParseLog]) -> list[dict]:
    members: list[dict] = []
    section_key = normalize_key(" ".join(lines[:8]))
    default_participant_type = (
        "estudiante"
        if "ESTUDIANTE" in section_key
        else ("graduado" if "GRADUADO" in section_key or "EGRESADO" in section_key else "investigador_externo")
    )
    index = 0
    while index < len(lines):
        if not _is_name_label(lines[index]):
            index += 1
            continue
        raw_name, cursor = _collect_until_label(lines, index + 1, {"INSTITUCION EXTERNA", "FACULTAD", "CARRERA", "NOMBRE COMPLETO", "RESUMEN", "SECCION"})
        raw_institution = ""
        raw_faculty = ""
        raw_career = ""
        while cursor < len(lines):
            key = normalize_key(lines[cursor])
            if _is_name_label(lines[cursor]) or key.startswith(("RESUMEN", "SECCION")):
                break
            if "INSTITUCION EXTERNA" in key:
                raw_institution, cursor = _collect_until_label(lines, cursor + 1, {"NOMBRE COMPLETO", "RESUMEN", "SECCION"})
                continue
            if key == "FACULTAD":
                raw_faculty, cursor = _collect_until_label(lines, cursor + 1, {"CARRERA", "NOMBRE COMPLETO", "RESUMEN", "SECCION"})
                continue
            if key == "CARRERA":
                raw_career, cursor = _collect_until_label(lines, cursor + 1, {"NOMBRE COMPLETO", "RESUMEN", "SECCION"})
                continue
            cursor += 1
        name = _clean_wrapped_person_name(raw_name)
        if not name or not _valid_candidate_person_text(name):
            index = max(cursor, index + 1)
            continue
        context = " ".join([name, raw_institution, raw_faculty, raw_career])
        participant_type = _external_participant_type(context)
        if not raw_institution and (raw_faculty or raw_career):
            participant_type = "estudiante"
        if participant_type == "no_clasificado":
            participant_type = default_participant_type
        valid_institution = validate_institution(raw_institution)
        institution = (
            valid_institution
            if participant_type == "investigador_externo"
            else ("Universidad de Guayaquil" if participant_type in {"estudiante", "graduado"} else None)
        )
        members.append(
            {
                "name": title_case_name(name),
                "institution": institution,
                "is_external": "true" if participant_type == "investigador_externo" else "false",
                "participant_type": participant_type,
                "source_section": "integrantes_externos",
                "requires_review": participant_type in {"estudiante", "graduado"} or not bool(valid_institution),
                "reason": "Participante detectado en tabla vertical de externos/estudiantes.",
            }
        )
        logs.append(
            ParseLog(
                "integrantes_externos.participant",
                f"{name} | {raw_institution or raw_faculty or raw_career}",
                75 if participant_type in {"estudiante", "graduado"} else 90,
                "jotform_label_value",
                table="integrantes_externos",
                requires_review=participant_type in {"estudiante", "graduado"} or not bool(valid_institution),
            )
        )
        index = max(cursor, index + 1)
    return members


def _external_participant_type(value: str | None) -> str:
    key = normalize_key(value)
    if "ESTUDIANTE" in key or "ALUMNO" in key:
        return "estudiante"
    if "GRADUADO" in key or "EGRESADO" in key:
        return "graduado"
    if not normalize_external_institution_display(value):
        return "no_clasificado"
    if "UNIVERSIDAD" in key or "INSTITUTO" in key or "ESCUELA" in key or "CENTRO" in key:
        return "investigador_externo"
    return "no_clasificado"


def parse_director_responsible(lines: list[str], logs: list[ParseLog], title: str | None = None) -> list[dict]:
    useful = [line for line in lines if not _skip_headers(line)]
    role_key = normalize_key(title)
    role_type = "director" if "DIRECTOR" in role_key or "COORDINADOR" in role_key else "responsable_informe"
    participants: list[dict] = []
    for line in useful[:12]:
        if not looks_like_person_name(line):
            continue
        name = title_case_name(line)
        participants.append(
            {
                "name": name,
                "role_type": role_type,
                "source_section": "director_responsable",
            }
        )
        logs.append(
            ParseLog(
                f"director_responsable.{role_type}",
                name,
                80,
                "section_context",
                table="director_responsable",
                requires_review=True,
            )
        )
        break
    return participants


def parse_projects_fci(lines: list[str], logs: list[ParseLog]) -> list[dict]:
    projects: list[dict] = []
    indexes = [idx for idx, line in enumerate(lines) if re.fullmatch(r"FCI\d{3,}-?\d{4}", normalize_key(line))]
    for position, start in enumerate(indexes):
        end = indexes[position + 1] if position + 1 < len(indexes) else len(lines)
        code = lines[start]
        block = [line for line in lines[start + 1 : end] if not _skip_headers(line)]
        progress_index = next((idx for idx, line in enumerate(block) if re.fullmatch(r"\d+%", line.strip())), None)
        if progress_index is None:
            continue
        before_progress = block[:progress_index]
        director = ""
        director_start = len(before_progress)
        for size in range(min(4, len(before_progress)), 0, -1):
            candidate = " ".join(before_progress[-size:])
            if looks_like_person_name(candidate):
                director = title_case_name(candidate)
                director_start = len(before_progress) - size
                break
        status, score, review = validate_status(block[progress_index + 1] if progress_index + 1 < len(block) else None)
        project = {
            "code": code,
            "name": " ".join(before_progress[:director_start]).strip(),
            "director": director or "Director no detectado",
            "progress": block[progress_index],
            "status": status or "",
            "source_section": "proyectos_fci",
        }
        projects.append(project)
        logs.append(ParseLog("proyectos_fci.code", code, 100, "layout_table", table="proyectos_fci"))
        logs.append(ParseLog("proyectos_fci.status", project["status"], score, "catalog_fuzzy", table="proyectos_fci", requires_review=review))
    return projects


def parse_scientific_products(lines: list[str], logs: list[ParseLog]) -> list[dict]:
    label_value_products = _parse_label_value_scientific_products(lines, logs)
    stacked_products = _parse_stacked_scientific_products(lines, logs)

    products: list[dict] = []
    block: list[str] = []
    useful_lines = [item for item in lines if not _skip_headers(item)]
    for index, line in enumerate(useful_lines):
        block.append(line)
        next_line = useful_lines[index + 1] if index + 1 < len(useful_lines) else None
        if _should_close_product_block(block, line, next_line):
            product = _product_from_block(block)
            if product:
                products.append(product)
                logs.append(ParseLog("produccion_cientifica.title", product["title"], 80, "layout_table", table="produccion_cientifica", requires_review=True))
            block = []
    product = _product_from_block(block)
    if product:
        products.append(product)
    structured_products = [*label_value_products, *stacked_products]
    if structured_products:
        return _select_best_product_candidates(structured_products)
    return _select_best_product_candidates(products)


def _should_close_product_block(block: list[str], current_line: str, next_line: str | None) -> bool:
    if looks_like_url(current_line):
        return True
    has_status = any(_status_from_key(normalize_key(line)) for line in block)
    if not has_status:
        return False
    current_key = normalize_key(current_line)
    current_is_row_tail = bool(_status_from_key(current_key)) or _is_product_tail_line(current_line)
    return current_is_row_tail and not _is_product_tail_line(next_line)


def _is_product_tail_line(line: str | None) -> bool:
    if not line:
        return False
    key = normalize_key(line)
    return key in {"IMPACTO", "MUNDIAL", "REGIONAL", "IMPACTO MUNDIAL", "IMPACTO REGIONAL"} or looks_like_url(line)


def _select_best_product_candidates(products: list[dict]) -> list[dict]:
    selected: dict[str, dict] = {}
    order: list[str] = []
    has_substantive_product = any(not _is_low_value_discarded_product(product) for product in products)
    for product in products:
        if has_substantive_product and _is_low_value_discarded_product(product):
            continue
        title_key = normalize_key(product.get("title"))
        key = title_key or f"LINK:{normalize_key(product.get('link'))}"
        if not key:
            key = f"ROW:{len(order)}"
        if _is_low_value_discarded_product(product) and key in selected:
            continue
        current = selected.get(key)
        if current is None:
            selected[key] = product
            order.append(key)
            continue
        if _product_candidate_score(product) > _product_candidate_score(current):
            selected[key] = product
    return _collapse_product_variants([selected[key] for key in order])


def _collapse_product_variants(products: list[dict]) -> list[dict]:
    collapsed: list[dict] = []
    for product in products:
        variant_index = next(
            (index for index, current in enumerate(collapsed) if _same_product_variant(current, product)),
            None,
        )
        if variant_index is None:
            collapsed.append(product)
            continue
        if _product_candidate_score(product) > _product_candidate_score(collapsed[variant_index]):
            collapsed[variant_index] = product
    return collapsed


def _same_product_variant(left: dict, right: dict) -> bool:
    left_title = normalize_key(left.get("title"))
    right_title = normalize_key(right.get("title"))
    if not left_title or not right_title or left_title == right_title:
        return False
    shorter, longer = sorted((left_title, right_title), key=len)
    if not (longer.startswith(shorter) or longer.endswith(shorter)):
        return False
    if len(longer.split()) - len(shorter.split()) > 4:
        return False
    left_authors = {normalize_key(author) for author in left.get("authors") or [] if normalize_key(author)}
    right_authors = {normalize_key(author) for author in right.get("authors") or [] if normalize_key(author)}
    return bool(
        left_authors
        and left_authors == right_authors
        and normalize_key(left.get("status")) == normalize_key(right.get("status"))
    )


def _product_candidate_score(product: dict) -> int:
    title = str(product.get("title") or "")
    quality, _ = product_title_quality(title)
    score = {"valid": 60, "truncated": 25, "invalid": -40}.get(quality, 0)
    score += min(len(product.get("authors") or []), 5) * 8
    if product.get("status"):
        score += 8
    if product.get("impact"):
        score += 4
    if product.get("link"):
        score += 4
    if product.get("title_recovery_status") == "recovered_from_pdf_context":
        score += 6
    if product.get("validation_status") == "validado":
        score += 12
    if product.get("validation_status") == "discarded_invalid":
        score -= 80
    return score


def _is_low_value_discarded_product(product: dict) -> bool:
    return product.get("validation_status") == "discarded_invalid" and not (product.get("authors") or product.get("status"))


def _title_from_contiguous_parts(title_parts: list[str]) -> tuple[str, dict]:
    title = _join_wrapped_text_fragments(title_parts)
    if len(title_parts) < 2:
        return title, {}
    original_title = str(title_parts[0] or "").strip()
    if not original_title:
        return title, {}
    original_quality, _ = product_title_quality(original_title)
    recovered_quality, _ = product_title_quality(title)
    if original_quality == "truncated" and recovered_quality == "valid":
        return title, {
            "original_title": original_title,
            "source_fragment": [str(part).strip() for part in title_parts if str(part or "").strip()],
            "title_recovery_status": "recovered_from_pdf_context",
        }
    return title, {}


def _join_wrapped_text_fragments(parts: list[str]) -> str:
    text = ""
    for raw_part in parts:
        part = str(raw_part or "").strip()
        if not part:
            continue
        if not text:
            text = part
            continue
        first_token, separator, remainder = part.partition(" ")
        suffix = re.sub(r"[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ]", "", first_token).lower()
        previous_token = text.rsplit(" ", 1)[-1].rstrip("-–—")
        joins_previous_word = (
            text.endswith("-")
            or (
                suffix in WRAPPED_WORD_SUFFIXES
                and len(re.sub(r"[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ]", "", previous_token)) >= 4
            )
        )
        if joins_previous_word:
            text += first_token
            if separator and remainder:
                text += f" {remainder}"
        else:
            text += f" {part}"
    return text.strip()


def _parse_stacked_scientific_products(lines: list[str], logs: list[ParseLog]) -> list[dict]:
    useful, page_break_starts = _stacked_useful_lines(lines)
    products: list[dict] = []
    cursor = 0
    while cursor < len(useful):
        status_index, status, status_size = _next_status(useful, cursor)
        if status_index is None:
            break
        title_author_block = useful[cursor:status_index]
        if cursor in page_break_starts and products:
            split_index = _page_continuation_split_index(title_author_block)
            if split_index:
                _extend_previous_product_title(products[-1], title_author_block[:split_index])
                title_author_block = title_author_block[split_index:]
        title_parts, authors = _split_title_and_authors(title_author_block)
        title, title_recovery = _title_from_contiguous_parts(title_parts)
        quality, quality_reason = product_title_quality(title)
        if quality == "invalid" and title:
            products.append(_discarded_product(title, status, None, None, quality_reason))
            cursor = status_index + status_size
            continue
        if quality in {"valid", "truncated"} and authors:
            after_status = status_index + status_size
            impact_parts: list[str] = []
            link_parts: list[str] = []
            while after_status < len(useful):
                key = normalize_key(useful[after_status])
                if key == "IMPACTO" or key in {"MUNDIAL", "REGIONAL", "IMPACTO MUNDIAL", "IMPACTO REGIONAL"}:
                    impact_parts.append(useful[after_status])
                    after_status += 1
                    continue
                if _is_url_continuation(useful, after_status, link_parts, page_break_starts):
                    link_parts.append(useful[after_status])
                    after_status += 1
                    continue
                break
            requires_review = quality != "valid" or bool(title_parts and not title_parts[0][:1].isupper())
            product = {
                "type": "UNCLASSIFIED",
                "title": title,
                "authors": authors,
                "authors_source": "stacked_row_tail",
                "status": status,
                "impact": " ".join(impact_parts).strip() or None,
                "link": rebuild_url(link_parts),
                "source_section": "produccion_cientifica",
                "source_field": "title",
                "requires_review": requires_review,
                "validation_status": "pending_review" if requires_review else "validado",
                "kpi_eligible": not requires_review,
                "reason": quality_reason if requires_review else "Producto cientifico detectado desde seccion de produccion.",
                **title_recovery,
            }
            products.append(product)
            logs.append(
                ParseLog(
                    "produccion_cientifica.title",
                    product["title"],
                    85 if len(product["title"]) > 20 else 60,
                    "stacked_layout_table",
                    table="produccion_cientifica",
                    requires_review=requires_review,
                )
            )
            cursor = after_status
            continue
        cursor = status_index + status_size
    return products


def _stacked_useful_lines(lines: list[str]) -> tuple[list[str], set[int]]:
    useful: list[str] = []
    page_break_starts: set[int] = set()
    crossed_page = False
    previous_key = ""
    for line in lines:
        if _is_page_break(line):
            crossed_page = True
            previous_key = normalize_key(line)
            continue
        key = normalize_key(line)
        contextual_fragment = key == "ABOUT" or (key == "O" and previous_key != "IMPACT")
        if _skip_headers(line) and not contextual_fragment:
            previous_key = key
            continue
        if crossed_page:
            page_break_starts.add(len(useful))
            crossed_page = False
        useful.append(line)
        previous_key = key
    return useful, page_break_starts


def _is_url_continuation(
    lines: list[str],
    index: int,
    current_parts: list[str],
    page_break_starts: set[int],
) -> bool:
    if index in page_break_starts:
        return False
    line = lines[index].strip()
    if looks_like_url(line) or "/" in line or "." in line:
        return True
    if not current_parts or not re.fullmatch(r"[a-z0-9_-]+", line):
        return False
    next_line = lines[index + 1].strip() if index + 1 < len(lines) else ""
    current_url = "".join(current_parts)
    return current_url.endswith("/") or next_line.startswith(".") or "/" in next_line


def _page_continuation_split_index(block: list[str]) -> int | None:
    if not block or not block[0][:1].islower():
        return None
    for index, line in enumerate(block[1:5], start=1):
        if line[:1].isupper():
            return index
    return None


def _extend_previous_product_title(product: dict, continuation_parts: list[str]) -> None:
    previous_title = str(product.get("title") or "").strip()
    continuation = _join_wrapped_text_fragments(continuation_parts)
    if not continuation or not previous_title:
        return
    product.setdefault("original_title", previous_title)
    source_fragment = list(product.get("source_fragment") or [previous_title])
    source_fragment.extend(part.strip() for part in continuation_parts if part.strip())
    product["source_fragment"] = source_fragment
    product["title"] = _join_wrapped_text_fragments([previous_title, *continuation_parts])
    product["title_recovery_status"] = "recovered_across_page_break"
    quality, quality_reason = product_title_quality(product["title"])
    requires_review = not (quality == "valid" and product.get("authors") and product.get("status"))
    product["requires_review"] = requires_review
    product["validation_status"] = "pending_review" if requires_review else "validado"
    product["kpi_eligible"] = not requires_review
    product["reason"] = quality_reason if requires_review else "Producto cientifico recuperado a traves de salto de pagina."


def _next_status(lines: list[str], start: int) -> tuple[int | None, str | None, int]:
    normalized = [normalize_key(line) for line in lines]
    for index in range(start, len(lines)):
        for size in range(1, min(4, len(lines) - index) + 1):
            status = _status_from_key(" ".join(normalized[index : index + size]))
            if status:
                return index, status, size
        for pattern, status in STATUS_PATTERNS:
            window = tuple(normalized[index : index + len(pattern)])
            if window == pattern:
                return index, status, len(pattern)
    return None, None, 0


def _split_title_and_authors(block: list[str]) -> tuple[list[str], list[str]]:
    cursor = len(block)
    authors: list[str] = []
    while cursor > 0:
        found_size = 0
        found_name = ""
        for size in range(min(4, cursor), 0, -1):
            candidate_lines = block[cursor - size : cursor]
            candidate = " ".join(candidate_lines).strip()
            if _looks_like_product_author(candidate, candidate_lines):
                found_size = size
                found_name = _source_case_name(candidate)
                break
        if not found_size:
            break
        authors.insert(0, found_name)
        cursor -= found_size
    return block[:cursor], authors


def _source_case_name(value: str) -> str:
    particles = {"de", "del", "la", "las", "los", "y"}
    parts = normalize_line(value).lower().split()
    return " ".join(
        part if index > 0 and part in particles else part.capitalize()
        for index, part in enumerate(parts)
    )


def _looks_like_product_author(candidate: str, candidate_lines: list[str]) -> bool:
    first_line = candidate_lines[0] if candidate_lines else ""
    if not first_line[:1].isupper():
        return False
    if len(candidate_lines) > 1 and all(
        looks_like_person_name(line) and len(normalize_key(line).split()) >= 2 for line in candidate_lines
    ):
        return False
    first_key = normalize_key(first_line)
    second_key = normalize_key(candidate_lines[1]) if len(candidate_lines) > 1 else ""
    if (
        len(candidate_lines) == 4
        and len(first_key.split()) == 1
        and first_key not in COMMON_FIRST_NAMES
        and second_key.split()[:1]
        and second_key.split()[0] in COMMON_FIRST_NAMES
    ):
        return False
    key = normalize_key(candidate)
    if any(token in PRODUCT_TITLE_TOKENS for token in key.split()):
        return False
    if looks_like_person_name(candidate):
        return True
    tokens = key.split()
    if 2 <= len(tokens) <= 5 and all(len(token) >= 3 and token not in PRODUCT_TITLE_TOKENS for token in tokens):
        return not any(token in {"IMPACTO", "PUBLICADO", "ENVIADO", "REVISION", "REGIONAL", "MUNDIAL"} for token in tokens)
    return False


def _product_from_block(block: list[str]) -> dict | None:
    useful = [
        line
        for line in block
        if normalize_key(line)
        not in {"TITULO", "AUTOR 1", "AUTOR 2", "AUTOR 3", "AUTOR 4", "AUTOR 5", "ESTADO", "IMPACTO", "LINK"}
    ]
    if not useful:
        return None
    author_indexes = [idx for idx, line in enumerate(useful) if idx > 0 and looks_like_person_name(line)]
    status = next((line for line in useful if normalize_key(line) in {"PUBLICADO", "ENVIADO A REVISION", "ACEPTADO", "EN REVISION"}), None)
    impact = next((line for line in useful if "IMPACTO" in normalize_key(line)), None)
    url = rebuild_url([line for line in useful if looks_like_url(line)])
    if not author_indexes:
        title_parts = [line for line in useful if line not in {status, impact} and not looks_like_url(line)]
        title, title_recovery = _title_from_contiguous_parts(title_parts)
        if not title and url:
            return _discarded_product(url, status, impact, url, "Enlace detectado sin titulo de producto asociado.")
        quality, quality_reason = product_title_quality(title)
        if quality == "invalid":
            return _discarded_product(title, status, impact, url, quality_reason) if title else None
        return {
            "type": "UNCLASSIFIED",
            "title": title,
            "authors": [],
            "authors_source": "none",
            "status": status,
            "impact": impact,
            "link": url,
            "source_section": "produccion_cientifica",
            "source_field": "title",
            "validation_status": "pending_review",
            "kpi_eligible": False,
            "requires_review": True,
            "reason": quality_reason if quality == "truncated" else "Producto detectado en seccion de produccion, pero faltan autores o campos para validacion.",
            **title_recovery,
        }
    title_parts = useful[: author_indexes[0]]
    title, title_recovery = _title_from_contiguous_parts(title_parts)
    quality, quality_reason = product_title_quality(title)
    if quality == "invalid":
        return _discarded_product(title, status, impact, url, quality_reason)
    requires_review = quality != "valid"
    return {
        "type": "UNCLASSIFIED",
        "title": title,
        "authors": [title_case_name(useful[idx]) for idx in author_indexes[:5]],
        "authors_source": "block_person_lines",
        "status": status,
        "impact": impact,
        "link": url,
        "source_section": "produccion_cientifica",
        "source_field": "title",
        "validation_status": "pending_review" if requires_review else "validado",
        "kpi_eligible": not requires_review,
        "requires_review": requires_review,
        "reason": quality_reason if requires_review else "Producto cientifico detectado desde seccion de produccion.",
        **title_recovery,
    }


def _discarded_product(title: str, status: str | None, impact: str | None, url: str | None, reason: str) -> dict:
    return {
        "type": "UNCLASSIFIED",
        "title": title,
        "authors": [],
        "authors_source": "none",
        "status": status,
        "impact": impact,
        "link": url,
        "source_section": "produccion_cientifica",
        "source_field": "title",
        "validation_status": "discarded_invalid",
        "kpi_eligible": False,
        "requires_review": True,
        "reason": reason,
    }


def _parse_label_value_scientific_products(lines: list[str], logs: list[ParseLog]) -> list[dict]:
    lines = _preserve_incomplete_row_across_page_break(lines)
    products: list[dict] = []
    index = 0
    while index < len(lines):
        if not _is_title_label(lines[index]):
            index += 1
            continue
        title, cursor = _collect_until_label(
            lines,
            index + 1,
            {"EDITORIAL", "AUTOR", "AUTOR 1", "AUTOR 2", "AUTOR 3", "AUTOR 4", "AUTOR 5", "ESTADO", "IMPACTO", "IMPACT", "LINK", "TITULO"},
        )
        authors: list[str] = []
        status_value = None
        impact_value = None
        link_value = None
        while cursor < len(lines):
            key = normalize_key(lines[cursor])
            if _is_title_label(lines[cursor]):
                break
            if key.startswith("AUTOR"):
                author, cursor = _collect_until_label(
                    lines,
                    cursor + 1,
                    {"AUTOR", "AUTOR 1", "AUTOR 2", "AUTOR 3", "AUTOR 4", "AUTOR 5", "ESTADO", "IMPACTO", "IMPACT", "LINK", "TITULO"},
                )
                cleaned_author = _clean_wrapped_person_name(author)
                if cleaned_author and looks_like_person_name(cleaned_author):
                    authors.append(title_case_name(cleaned_author))
                continue
            if key == "ESTADO":
                status_raw, cursor = _collect_until_label(lines, cursor + 1, {"IMPACTO", "IMPACT", "LINK", "TITULO", "AUTOR"})
                status_value = _status_from_key(normalize_key(status_raw)) or status_raw
                continue
            if key in {"IMPACTO", "IMPACT"}:
                impact_raw, cursor = _collect_until_label(lines, cursor + 1, {"LINK", "TITULO", "AUTOR", "ESTADO"})
                impact_value = _impact_from_text(impact_raw)
                continue
            if key == "LINK":
                link_raw, cursor = _collect_until_label(lines, cursor + 1, {"TITULO", "AUTOR", "ESTADO", "IMPACTO", "IMPACT"})
                link_value = rebuild_url(link_raw.split())
                continue
            cursor += 1
        cleaned_title = _clean_wrapped_title(title)
        quality, quality_reason = product_title_quality(cleaned_title)
        if cleaned_title and quality == "invalid" and (authors or status_value):
            products.append(_discarded_product(cleaned_title, status_value, impact_value, link_value, quality_reason))
            logs.append(
                ParseLog(
                    "produccion_cientifica.title",
                    cleaned_title,
                    30,
                    "jotform_label_value",
                    table="produccion_cientifica",
                    requires_review=True,
                )
            )
        elif cleaned_title and quality != "invalid" and (authors or status_value or quality == "valid"):
            requires_review = not (quality == "valid" and authors and status_value)
            products.append(
                {
                    "type": "UNCLASSIFIED",
                    "title": cleaned_title,
                    "authors": authors,
                    "authors_source": "author_field",
                    "status": status_value,
                    "impact": impact_value,
                    "link": link_value,
                    "source_section": "produccion_cientifica",
                    "source_field": "title",
                    "requires_review": requires_review,
                    "validation_status": "pending_review" if requires_review else "validado",
                    "kpi_eligible": not requires_review,
                    "reason": None if not requires_review else quality_reason,
                }
            )
            logs.append(
                ParseLog(
                    "produccion_cientifica.title",
                    cleaned_title,
                    85 if quality == "valid" and authors else 55,
                    "jotform_label_value",
                    table="produccion_cientifica",
                    requires_review=requires_review,
                )
            )
        index = max(cursor, index + 1)
    return products


def _preserve_incomplete_row_across_page_break(lines: list[str]) -> list[str]:
    prepared: list[str] = []
    row_open = False
    row_has_tail = False
    crossed_page = False
    for line in lines:
        if _is_page_break(line):
            crossed_page = row_open
            prepared.append(line)
            continue
        key = normalize_key(line)
        if _is_title_label(line):
            if row_open and crossed_page and not row_has_tail:
                crossed_page = False
                continue
            row_open = True
            row_has_tail = False
            crossed_page = False
        elif row_open and (
            key.startswith("AUTOR")
            or key in {"ESTADO", "IMPACTO", "IMPACT", "LINK"}
        ):
            row_has_tail = True
            crossed_page = False
        prepared.append(line)
    return prepared


def _is_date_like(key: str) -> bool:
    return bool(re.search(r"\d{1,4}[-/]\d{1,2}[-/]\d{1,4}", key))


def _is_name_label(line: str) -> bool:
    return normalize_key(line).startswith("NOMBRE COMPLE")


def _is_title_label(line: str) -> bool:
    return normalize_key(line) in {"TITULO", "TITULO"} or normalize_key(line).startswith("TITULO ")


def _is_stop_label(line: str, stop_labels: set[str]) -> bool:
    key = normalize_key(line)
    if not key:
        return False
    if key in stop_labels or any(key.startswith(label) for label in stop_labels):
        return True
    if re.fullmatch(r"\d{1,2}\.?", key) or key.startswith("SECCION"):
        return True
    return False


def _collect_until_label(lines: list[str], start: int, stop_labels: set[str]) -> tuple[str, int]:
    parts: list[str] = []
    cursor = start
    while cursor < len(lines):
        if _is_stop_label(lines[cursor], stop_labels):
            break
        key = normalize_key(lines[cursor])
        if _skip_headers(lines[cursor]) or key in FIELD_LABELS:
            cursor += 1
            continue
        if _is_date_like(key):
            break
        parts.append(lines[cursor])
        cursor += 1
    return " ".join(parts).strip(), cursor


def _clean_wrapped_person_name(value: str | None) -> str:
    text = " ".join(str(value or "").replace("\n", " ").split()).strip()
    text = re.sub(r"\b(SELECCIONAR|LIMPIAR|FECHA|FACULTAD|CARRERA)\b", " ", text, flags=re.IGNORECASE)
    text = " ".join(text.split())
    text = re.sub(r"^(TO|O)\s+", "", text, flags=re.IGNORECASE).strip()
    return text


def _clean_wrapped_title(value: str | None) -> str:
    text = " ".join(str(value or "").replace("\n", " ").split()).strip()
    text = re.sub(r"\b(AGREGAR NUEVO REGISTRO|SELECCIONAR|LIMPIAR)\b", " ", text, flags=re.IGNORECASE)
    text = " ".join(text.split())
    return text


def _merge_people_by_raw_name(items: list[dict]) -> list[dict]:
    selected: dict[str, dict] = {}
    for item in items:
        key = normalize_key(item.get("name"))
        if not key:
            continue
        current = selected.get(key)
        if not current:
            selected[key] = item
            continue
        if not current.get("career") and item.get("career"):
            selected[key] = item
    return list(selected.values())


def _valid_candidate_person_text(value: str | None) -> bool:
    key = normalize_key(value)
    tokens = key.split()
    if len(tokens) < 2:
        return False
    blocked = {
        "ADMINISTRACION",
        "ADMINISTRATIVAS",
        "CARRERA",
        "CIENCIAS",
        "COMERCIO",
        "CONTABILIDAD",
        "FACULTAD",
        "FECHA",
        "FINANZAS",
        "FUNDACION",
        "GESTION",
        "INGENIERIA",
        "INGRES",
        "INFORMACION",
        "INSTITUTO",
        "LICENCIATURA",
        "MERCADOTECNIA",
        "QUIM",
        "QUIMICA",
        "UNIVERSIDAD",
        "UNIVERSITARIA",
    }
    return not any(token in blocked for token in tokens)


def _clean_career_text(value: str | None) -> str:
    text = " ".join(str(value or "").split())
    key = normalize_key(text)
    if key.startswith("CIENCIAS ADMINISTRATIVAS"):
        text = " ".join(text.split()[2:])
    if key.startswith("FACULTAD CIENCIAS ADMINISTRATIVAS"):
        text = " ".join(text.split()[3:])
    return text


def _status_from_key(key: str | None) -> str | None:
    value = normalize_key(key)
    compact = value.replace(" ", "")
    if compact.startswith("PUBLIC") and len(compact) <= 10:
        return "PUBLICADO"
    if compact.startswith("ACEPT") and len(compact) <= 10:
        return "ACEPTADO"
    if compact.startswith("ENVIADOAREV") or compact == "ENVIADOAREVISION":
        return "ENVIADO A REVISION"
    if compact.startswith("ENREV"):
        return "EN REVISION"
    return None


def _impact_from_text(value: str | None) -> str | None:
    key = normalize_key(value)
    if "MUND" in key:
        return "IMPACTO MUNDIAL"
    if "REGION" in key:
        return "IMPACTO REGIONAL"
    return value or None


def parse_interchanges(lines: list[str], logs: list[ParseLog]) -> list[dict]:
    useful = [line for line in lines if not _skip_headers(line)]
    items: list[dict] = []
    index = 0
    while index + 2 < len(useful):
        faculty, kind, title = useful[index : index + 3]
        if normalize_key(kind) in {"CONFERENCIA", "PONENCIA", "CHARLA", "TALLER"}:
            items.append({"faculty": faculty, "type": kind, "title": title, "source_section": "intercambios"})
            logs.append(ParseLog("intercambios.type", kind, 100, "layout_table", table="intercambios"))
            index += 3
            continue
        index += 1
    return items
