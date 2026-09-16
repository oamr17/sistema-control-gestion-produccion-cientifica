from __future__ import annotations

import re

from .confidence import REVIEW_THRESHOLD, best_alias
from .models import ParsedSection
from .normalizer import normalize_key, split_lines


SECTION_ALIASES: dict[str, list[str]] = {
    "integrantes_internos": [
        "Investigadores que participan en el grupo",
        "Investigadores que participan en el lupo",
        "Investigadores que participan en el proyecto",
        "Integrantes del grupo de investigacion",
        "Integrantes del grupo",
        "Integrantes del proyecto investigativo",
        "Integrantes del GI",
        "Equipo investigador",
        "Docentes investigadores",
        "Investigadores participantes",
        "Participantes internos",
    ],
    "integrantes_externos": [
        "Instituciones e investigadores externos",
        "Investigadores externos que participan del proyecto",
        "Investigadores externos",
        "Colaboradores externos",
        "Participantes externos",
        "Estudiantes participantes",
        "Estudiantes que participan",
        "Graduados participantes",
        "Egresados participantes",
        "Institucion externa investigador",
    ],
    "director_responsable": [
        "Director del proyecto",
        "Director del informe",
        "Responsable del informe",
        "Docente responsable",
        "Coordinador del grupo de investigacion",
        "Coordinador del proyecto",
        "Nombre del director",
        "Nombre del responsable",
    ],
    "proyectos_fci": [
        "Actividades realizadas por proyectos FCI vinculados a grupos de investigacion",
        "Codigo FCI proyecto director avance estado",
        "Proyectos FCI",
        "Proyectos vinculados",
    ],
    "produccion_cientifica": [
        "Produccion cientifica impacto mundial y regional",
        "Produccion cientifica",
        "Titulo autor estado impacto link",
    ],
    "intercambios": [
        "Intercambio de conocimiento participacion en grupos de investigacion",
        "Intercambio de conocimiento",
        "Participacion en grupos de investigacion",
    ],
    "actividades": [
        "Actividades realizadas por el proyecto y sus integrantes",
        "Actividades realizadas",
    ],
    "fondos": [
        "Fondos externos obtenidos por el grupo de investigacion",
        "Fondos externos",
    ],
    "grupo": ["Datos generales del proyecto de investigacion", "Datos generales", "Grupo de investigacion"],
}

NEGATIVE_SECTION_MARKERS = (
    "INVESTIGADORES QUE SE DESVINCULAN",
    "INVESTIGADORES QUE INGRESAN",
    "FIRMAS",
    "EVIDENCIAS",
)

TABLE_HEADER_MARKERS = {
    "INVESTIGADOR",
    "NOMBRE COMPLETO",
    "INSTITUCION EXTERNA",
    "FACULTAD",
    "CARRERA",
    "CODIGO FCI",
    "PROYECTO",
    "DIRECTOR",
    "ESTADO",
    "TITULO",
    "AUTOR",
    "AUTOR 1",
    "AUTOR 2",
    "AUTOR 3",
    "AUTOR 4",
    "AUTOR 5",
    "IMPACTO",
    "LINK",
    "FACULTAD INTERVINIENTE",
    "TIPO",
}

ALIAS_KEYS = tuple(
    normalize_key(alias)
    for aliases in SECTION_ALIASES.values()
    for alias in aliases
)


def _is_section_start_candidate(raw_line: str, line_key: str) -> bool:
    if len(line_key) < 12:
        return False
    return any(
        len(alias_key) >= 12 and alias_key in line_key
        for alias_key in ALIAS_KEYS
    )


def detect_sections(text: str | None) -> list[ParsedSection]:
    lines = split_lines(text)
    line_pages: list[int | None] = []
    current_page: int | None = None
    for line in lines:
        line_key = normalize_key(line)
        implicit_page_break = re.fullmatch(r"\[\[PAGE_BREAK\]\]", line.strip(), flags=re.IGNORECASE)
        page_match = (
            re.fullmatch(r"\[\[PAGE_BREAK:(\d+)\]\]", line.strip(), flags=re.IGNORECASE)
            or re.fullmatch(r"(\d+)\s+DE\s+\d+", line_key)
            or re.fullmatch(r"(\d+)/\d+", line_key)
        )
        if implicit_page_break:
            current_page = (current_page or 1) + 1
        elif page_match:
            current_page = int(page_match.group(1))
        line_pages.append(current_page)

    starts: list[tuple[int, str, str, float]] = []
    for index, line in enumerate(lines):
        line_key = normalize_key(re.sub(r"^\d{1,2}\.\s*", "", line))
        if line_key in TABLE_HEADER_MARKERS:
            continue
        if any(marker in line_key for marker in NEGATIVE_SECTION_MARKERS):
            continue
        if not _is_section_start_candidate(line, line_key):
            continue
        best_kind: str | None = None
        best_score = 0.0
        best_alias_length = 0
        for kind, aliases in SECTION_ALIASES.items():
            alias, score = best_alias(line_key, aliases)
            alias_length = len(normalize_key(alias))
            if score > best_score or (score == best_score and alias_length > best_alias_length):
                best_kind = kind
                best_score = score
                best_alias_length = alias_length
        if best_kind and best_score >= REVIEW_THRESHOLD:
            starts.append((index, best_kind, line, best_score))

    sections: list[ParsedSection] = []
    for position, (start, kind, title, score) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        section_lines = lines[start + 1 : end]
        for line_index, line in enumerate(section_lines):
            line_key = normalize_key(line)
            if any(marker in line_key for marker in NEGATIVE_SECTION_MARKERS):
                section_lines = section_lines[:line_index]
                break
        sections.append(
            ParsedSection(
                kind=kind,
                title=title,
                lines=section_lines,
                score=score,
                page=line_pages[start] if start < len(line_pages) else None,
                requires_review=score < 90,
            )
        )
    return sections
