from __future__ import annotations

import re

from .normalizer import normalize_key, normalize_line, split_lines
from .section_detector import detect_sections


STOP_LABELS = (
    "CORREO",
    "FECHA",
    "CICLO",
    "ANO",
    "AÑO",
    "UNIDAD",
    "CARRERA",
    "FACULTADES",
    "FACULTAD",
    "COORDINADOR",
    "DIRECTOR",
    "TUTOR",
    "INVESTIGADORES",
    "ESTUDIANTES",
    "RESUMEN",
    "SECCION",
    "SECCIÓN",
)


def parse_research_entities(text: str | None) -> list[dict]:
    lines = split_lines(text)
    if not lines:
        return []

    full_key = normalize_key(" ".join(lines[:120]))
    if _looks_like_non_relevant_document(full_key):
        return [
            {
                "type": "documento_no_relevante",
                "validation_status": "discarded_invalid",
                "reason": "Documento parece correo/captura o bandeja, no informe de investigacion.",
                "source_section": "document_classification",
                "confidence_score": 0.9,
            }
        ]

    entity_type = _document_type(full_key)
    code = _first_value_after_labels(
        lines,
        (
            "CODIGO DE PROYECTO",
            "CODIGO DEL PROYECTO",
            "CODIGO DE SEMILLERO",
            "CODIGO DEL SEMILLERO",
            "CODIGO FCI",
        ),
    )
    title = _first_value_after_labels(
        lines,
        (
            "TITULO DEL PROYECTO",
            "TITULO DEL SEMILLERO",
            "TITULO DE PROYECTO",
            "NOMBRE DEL GRUPO",
            "NOMBRE DEL GRUPO DE INVESTIGACION",
        ),
        max_lines=8,
    )
    director = _first_value_after_labels(
        lines,
        (
            "DIRECTOR DEL PROYECTO",
            "TUTOR DEL SEMILLERO",
            "COORDINADOR DEL GRUPO",
            "COORDINADOR DEL GRUPO DE INVESTIGACION",
            "RESPONSABLE DEL INFORME",
        ),
        max_lines=4,
    )
    year = _first_value_after_labels(lines, ("ANO DE CONVOCATORIA", "AÑO DE CONVOCATORIA"), max_lines=2)
    cycle = _first_value_after_labels(lines, ("CICLO ACADEMICO QUE REPORTA", "CICLO ACADEMICO"), max_lines=2)
    academic_unit = _first_value_after_labels(lines, ("UNIDAD ACADEMICA PROPONENTE", "UNIDAD ACADEMICA"), max_lines=3)
    career = _first_value_after_labels(
        lines,
        ("CARRERA DEL DIRECTOR", "CARRERA DEL TUTOR", "CARRERA DEL COORDINADOR"),
        max_lines=4,
    )
    email = _first_email(lines)
    progress = _first_progress(lines)
    status = _first_status(lines)

    fallback_code = _fallback_code(full_key, entity_type)
    code = _normalize_code(code, entity_type) or _normalize_code(fallback_code, entity_type)
    title = normalize_line(title)
    director = normalize_line(director)
    source_page = _source_page_for_entity(text)
    present_fields = [bool(code), bool(title), bool(director)]
    confidence = 0.35 + (sum(present_fields) * 0.2)
    if entity_type in {"proyecto_fci", "grupo_investigacion", "semillero"} and (code or title):
        validation_status = "validated" if title and director else "pending_review"
        reason = (
            "Entidad investigativa detectada desde datos generales del PDF."
            if validation_status == "validated"
            else "Entidad investigativa legible parcialmente; requiere revision de campos faltantes."
        )
    else:
        validation_status = "pending_review"
        reason = "No se pudo clasificar completamente la entidad investigativa del PDF."

    if not any([code, title, director]) and entity_type == "pendiente_clasificacion":
        return []

    return [
        {
            "type": entity_type,
            "code": code,
            "title": title or None,
            "director": director or None,
            "year": _first_year(year),
            "cycle": _cycle_number(cycle),
            "academic_unit": academic_unit,
            "career": career,
            "email": email,
            "progress_percentage": _progress_number(progress),
            "status": status,
            "validation_status": validation_status,
            "source_page": source_page,
            "source_section": "datos_generales",
            "confidence_score": min(confidence, 0.95),
            "reason": reason,
        }
    ]


def _looks_like_non_relevant_document(full_key: str) -> bool:
    if any(marker in full_key for marker in ("OUTLOOK", "BANDEJA DE ENTRADA", "RE:", "FW:", "CAPTURA DE PANTALLA")):
        return "PROYECTO" not in full_key and "INVESTIGACION" not in full_key
    return False


def _document_type(full_key: str) -> str:
    if "SEMILLERO" in full_key:
        return "semillero"
    if "GRUPO DE INVESTIGACION" in full_key or re.search(r"\bGI\d{3,}", full_key):
        return "grupo_investigacion"
    if "PROYECTO" in full_key or "FCI" in full_key:
        return "proyecto_fci"
    if "INFORME" in full_key and "INVESTIGACION" in full_key:
        return "informe_seguimiento"
    return "pendiente_clasificacion"


def _first_value_after_labels(lines: list[str], labels: tuple[str, ...], max_lines: int = 5) -> str | None:
    label_keys = tuple(normalize_key(label) for label in labels)
    for index, line in enumerate(lines):
        line_key = normalize_key(line)
        if not any(_line_matches_label(line_key, label) for label in label_keys):
            continue
        parts: list[str] = []
        for candidate in lines[index + 1 : index + 1 + max_lines]:
            key = normalize_key(candidate)
            cleaned_key = _strip_numbered_label_prefix(key)
            if not key or _is_noise_line(key):
                continue
            if any(cleaned_key.startswith(stop) for stop in STOP_LABELS):
                break
            if any(_line_matches_label(key, label) for label in label_keys):
                continue
            parts.append(candidate)
        value = normalize_line(" ".join(parts))
        if value:
            return value
    return None


def _line_matches_label(line_key: str, label_key: str) -> bool:
    cleaned = _strip_numbered_label_prefix(line_key).strip("*: ")
    return cleaned == label_key or cleaned.startswith(f"{label_key} ") or cleaned.startswith(f"{label_key}*")


def _strip_numbered_label_prefix(line_key: str) -> str:
    return re.sub(r"^\d+\s*[.)-]?\s*", "", line_key).strip()


def _is_noise_line(key: str) -> bool:
    return bool(
        re.fullmatch(r"\d+\s*/\s*\d+", key)
        or re.fullmatch(r"\d+\s+DE\s+\d+", key)
        or "FORMULARIO DE INFORME" in key
        or "JOTFORM" in key
        or key in {"FECHA", "SELECCIONAR", "LIMPIAR"}
    )


def _first_email(lines: list[str]) -> str | None:
    for line in lines:
        match = re.search(r"[\w.\-]+@[\w.\-]+\.\w+", line, flags=re.IGNORECASE)
        if match and "@local.import" not in match.group(0).lower():
            return match.group(0)
    return None


def _first_progress(lines: list[str]) -> str | None:
    for index, line in enumerate(lines):
        key = normalize_key(line)
        if "AVANCE" not in key and "PROGRESO" not in key:
            continue
        window = " ".join(lines[index : index + 8])
        match = re.search(r"(\d{1,3})\s*%", window)
        if match:
            return f"{match.group(1)}%"
    return None


def _first_status(lines: list[str]) -> str | None:
    for line in lines:
        key = normalize_key(line)
        if "APROBADO" in key:
            return "aprobado"
        if "VIGENTE" in key or "EJECUCION" in key:
            return "vigente"
        if "EN PROCESO" in key:
            return "en_proceso"
    return None


def _fallback_code(full_key: str, entity_type: str) -> str | None:
    if entity_type == "grupo_investigacion":
        match = re.search(r"\bGI\d{3,}(?:-\d{4})?\b", full_key)
        return match.group(0) if match else None
    if entity_type == "semillero":
        match = re.search(r"\bS\.?\s*I\.?\s*-?\s*\d{2,4}\b", full_key)
        return match.group(0) if match else None
    match = re.search(r"\bFCI-?\d{2,4}(?:-\d{4})?\b", full_key)
    return match.group(0) if match else None


def _normalize_code(value: str | None, entity_type: str) -> str | None:
    key = normalize_key(value)
    if not key:
        return None
    if entity_type == "semillero":
        match = re.search(r"(?:S\.?\s*I\.?\s*-?\s*)?(\d{2,4})", key)
        return f"S.I-{match.group(1).zfill(3)}" if match else None
    if entity_type == "grupo_investigacion":
        match = re.search(r"\bGI\d{3,}(?:-\d{4})?\b", key)
        return match.group(0) if match else None
    match = re.search(r"(?:FCI-?)?(\d{2,4})(?:-(\d{4}))?", key)
    if match and ("FCI" in key or entity_type == "proyecto_fci"):
        code = f"FCI-{match.group(1).zfill(3)}"
        return f"{code}-{match.group(2)}" if match.group(2) else code
    return None


def _first_year(value: str | None) -> int | None:
    match = re.search(r"(20\d{2})", value or "")
    return int(match.group(1)) if match else None


def _cycle_number(value: str | None) -> int | None:
    key = normalize_key(value)
    if "II" in key or key.endswith("2"):
        return 2
    if "I" in key or key.endswith("1"):
        return 1
    return None


def _progress_number(value: str | None) -> float | None:
    match = re.search(r"(\d{1,3})", value or "")
    if not match:
        return None
    return min(100.0, float(match.group(1)))


def _source_page_for_entity(text: str | None) -> int | None:
    for section in detect_sections(text):
        if section.kind == "grupo":
            return section.page
    return None
