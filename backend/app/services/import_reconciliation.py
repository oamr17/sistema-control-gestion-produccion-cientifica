from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath
from typing import Any

from app.services.institution_normalizer import normalize_external_institution_display


TRUNCATED_PRODUCT_REASON = "titulo truncado o incompleto"

PRODUCT_TAIL_MARKERS = (
    " PUBLICAD",
    " PUBLICADO",
    " PUBLICADA",
    " ENVIADO",
    " EN REVISION",
    " ACEPTADO",
    " IMPACTO ",
    " REGIONAL",
    " MUNDIAL",
)

COMMON_PERSON_TOKENS = {
    "ANA",
    "ANIBAL",
    "CARLOS",
    "CARMEN",
    "DANIEL",
    "DENNISE",
    "DOLORES",
    "EMILIO",
    "FERNANDO",
    "GABRIEL",
    "HENRY",
    "JANINA",
    "JANNINA",
    "JORGE",
    "JOSE",
    "JOSUE",
    "MARIA",
    "PILAR",
    "RAFAEL",
    "ROBIN",
    "ROSA",
    "WENDY",
}


def normalize_key(value: object) -> str:
    text = str(value or "").strip().upper()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.split())


def product_key(product: dict[str, Any]) -> str:
    identity = normalize_key(product.get("doi") or product.get("link") or product.get("title"))
    if not identity:
        return ""
    return "|".join(
        [
            normalize_key(product.get("type") or "UNCLASSIFIED"),
            identity,
        ]
    )


def product_dashboard_counted(product: dict[str, Any], *, duplicate: bool = False) -> bool:
    title = clean_product_title(product.get("title"), product.get("authors"))
    if not title or duplicate or product.get("kpi_eligible", True) is False:
        return False
    validation_status = normalize_key(product.get("validation_status"))
    if validation_status in {"PENDING_REVIEW", "PENDIENTE_VALIDACION", "DISCARDED_INVALID", "DESCARTADO"}:
        return False
    if product.get("requires_review"):
        return False
    quality, _ = product_title_quality(title)
    return quality == "valid"


def product_title_quality(title: object) -> tuple[str, str]:
    text = _clean_name(title)
    if not text:
        return "invalid", "Producto sin titulo legible."
    key = normalize_key(text)
    tokens = key.split()
    if not tokens:
        return "invalid", "Producto sin titulo legible."
    if _looks_like_non_product_title(text):
        return "invalid", "Texto compatible con encabezado, rol academico o tabla; no se cuenta como producto cientifico."
    if len(tokens) == 1:
        return "invalid", "Texto de una sola palabra; no alcanza para titulo de producto cientifico."
    if _looks_like_incomplete_reported_product(key):
        return "truncated", "Producto informado como no finalizado o incompleto; requiere revision."
    if _looks_like_url_or_identifier(text, key):
        if _has_embedded_product_title(tokens):
            return "truncated", "Titulo mezclado con URL/identificador o metadatos OCR; requiere revision."
        return "invalid", "Texto compatible con URL, DOI o identificador, no con titulo de producto."
    if _has_field_or_header_noise(key):
        return "invalid", "Texto compatible con campos de tabla o evidencia; no se cuenta como producto cientifico."
    if _has_glued_author_tail(tokens):
        return "truncated", "Titulo con autores pegados; requiere revision antes de contar KPI."
    if _looks_truncated_title(tokens):
        return "truncated", TRUNCATED_PRODUCT_REASON
    if len(key) < 18 or len(tokens) < 4:
        return "truncated", TRUNCATED_PRODUCT_REASON
    return "valid", "Producto cientifico detectado desde seccion valida."


def clean_product_title(title: object, authors: object | None = None) -> str | None:
    text = _clean_name(title)
    if not text:
        return None
    text = _strip_product_tail_noise(text)
    author_values = authors if isinstance(authors, list) else []
    for author in author_values:
        author_text = _clean_name(author)
        if not author_text:
            continue
        marker = normalize_key(author_text)
        title_key = normalize_key(text)
        index = title_key.find(marker)
        if index > 0:
            text = _trim_by_normalized_index(text, index)
            break
    text = _trim_probable_author_tail(text)
    return _clean_name(text)


def build_research_entities(
    payload: dict[str, Any] | None,
    *,
    source_filename: str | None = None,
    source_path: str | None = None,
    review_status: str | None = None,
    confidence_score: float | None = None,
) -> list[dict[str, Any]]:
    payload = payload or {}
    document = payload.get("document") if isinstance(payload.get("document"), dict) else {}
    filename = source_filename or document.get("filename")
    path = source_path or document.get("source_path")
    source_text = " ".join(str(item or "") for item in (filename, path))
    source_key = normalize_key(source_text)

    entities: list[dict[str, Any]] = []
    seen: set[str] = set()

    for project in _payload_list(payload, "proyectos_fci"):
        code = str(project.get("code") or "").strip()
        name = _clean_name(project.get("name"))
        director = str(project.get("director") or "").strip()
        validation_status = "validado" if code and name and director and "NO DETECTADO" not in normalize_key(director) else "pendiente_validacion"
        reason = (
            "Proyecto FCI detectado desde seccion de proyectos."
            if validation_status == "validado"
            else "Proyecto FCI detectado, pero faltan datos clave para validacion completa."
        )
        _append_entity(
            entities,
            seen,
            {
                "type": "proyecto_fci",
                "code": code,
                "name": name,
                "director": director or None,
                "progress_percentage": _progress_percent(project.get("progress")),
                "status": project.get("status"),
                "validation_status": validation_status,
                "source_file": filename,
                "source_page": project.get("source_page"),
                "source_section": project.get("source_section") or "proyectos_fci",
                "confidence_score": confidence_score,
                "kpi_eligible": validation_status == "validado",
                "dashboard_counted": bool(code or name),
                "reconciliation_status": "counted" if validation_status == "validado" else "pending_review",
                "reason": reason,
            },
        )

    if _looks_like_group(source_key):
        code = _extract_code(source_key, r"\bGI\s*[-.]?\s*\d{1,4}(?:\s*[-.]?\s*\d{2,4})?\b")
        _append_entity(
            entities,
            seen,
            _marker_entity(
                entity_type="grupo_investigacion",
                code=code,
                name=_source_name(filename) or "Grupo de investigacion detectado",
                filename=filename,
                confidence_score=confidence_score,
                reason="Grupo de investigacion detectado por patron estructural del documento o ruta.",
            ),
        )

    if _looks_like_seedbed(source_key):
        code = _extract_code(source_key, r"\bS\s*\.?\s*I\s*[-.]?\s*\d{1,4}\b")
        _append_entity(
            entities,
            seen,
            _marker_entity(
                entity_type="semillero",
                code=code,
                name=_source_name(filename) or "Semillero detectado",
                filename=filename,
                confidence_score=confidence_score,
                reason="Semillero detectado por patron estructural del documento o ruta.",
            ),
        )

    if not entities and review_status == "PENDIENTE_REVISION":
        _append_entity(
            entities,
            seen,
            {
                "type": "pendiente_ocr",
                "code": None,
                "name": _source_name(filename) or "Documento pendiente de OCR",
                "director": None,
                "progress_percentage": None,
                "status": None,
                "validation_status": "pendiente_ocr",
                "source_file": filename,
                "source_page": None,
                "source_section": "pendiente_ocr",
                "confidence_score": confidence_score,
                "kpi_eligible": False,
                "dashboard_counted": True,
                "reconciliation_status": "pending_ocr",
                "reason": "La traza requiere revision OCR y no produjo una entidad investigativa estructurada.",
            },
        )

    return entities


def build_product_reconciliation_rows(
    payload: dict[str, Any] | None,
    *,
    source_filename: str | None = None,
    source_path: str | None = None,
    seen_product_keys: set[str] | None = None,
) -> list[dict[str, Any]]:
    payload = payload or {}
    rows: list[dict[str, Any]] = []
    seen = seen_product_keys if seen_product_keys is not None else set()
    for product in _payload_products(payload):
        key = product_key(product)
        duplicate = bool(key and key in seen)
        if key:
            seen.add(key)
        raw_text = product.get("original_text") or product.get("raw_title") or product.get("title")
        clean_title = clean_product_title(product.get("title"), product.get("authors"))
        title = clean_title or _clean_name(product.get("title"))
        quality, quality_reason = product_title_quality(title)
        validation_status = product.get("validation_status") or ("validado" if title else "descartado")
        if quality == "truncated":
            validation_status = "pending_review"
        elif quality == "invalid":
            validation_status = "discarded_invalid"
        kpi_eligible = product_dashboard_counted({**product, "validation_status": validation_status}, duplicate=duplicate)
        if duplicate:
            reconciliation_status = "duplicate_evidence"
            reason = "Producto duplicado por titulo/enlace/tipo; se conserva evidencia sin duplicar KPI."
        elif not title:
            reconciliation_status = "discarded_invalid"
            reason = "Producto sin titulo legible."
        elif quality == "invalid":
            reconciliation_status = "discarded_invalid"
            reason = quality_reason
        elif quality == "truncated":
            reconciliation_status = "pending_review_text_fragment"
            reason = quality_reason
        elif validation_status in {"pending_review", "pendiente_validacion"}:
            reconciliation_status = "pending_review"
            reason = product.get("reason") or "Producto detectado con campos pendientes."
        else:
            reconciliation_status = "counted" if kpi_eligible else "detected_not_counted"
            reason = product.get("reason") or "Producto cientifico detectado desde seccion valida."
        rows.append(
            {
                "source_file": source_filename,
                "source_path": source_path,
                "source_page": product.get("source_page"),
                "source_section": product.get("source_section") or "produccion_cientifica",
                "source_field": product.get("source_field") or "title",
                "row_or_block_id": product.get("row_or_block_id"),
                "original_text": raw_text,
                "raw_text": raw_text,
                "clean_title": clean_title,
                "title": title,
                "type": product.get("type") or "UNCLASSIFIED",
                "status": product.get("status"),
                "impact": product.get("impact"),
                "doi": product.get("doi"),
                "link": product.get("link"),
                "authors": product.get("authors") if isinstance(product.get("authors"), list) else [],
                "authors_detected": product.get("authors") if isinstance(product.get("authors"), list) else [],
                "authors_resolved": [
                    item.get("author_name") or item.get("normalized_author_name")
                    for item in (product.get("authorships") or [])
                    if isinstance(item, dict) and (item.get("person_key") or item.get("author_name") or item.get("normalized_author_name"))
                ],
                "authors_pending": [
                    item
                    for item in (product.get("authors") if isinstance(product.get("authors"), list) else [])
                    if normalize_key(item)
                    not in {
                        normalize_key(authorship.get("raw_author_name") or authorship.get("author_name"))
                        for authorship in (product.get("authorships") or [])
                        if isinstance(authorship, dict)
                    }
                ],
                "authors_source": product.get("authors_source"),
                "fields_detected": product.get("fields_detected") or [],
                "fields_missing": product.get("fields_missing") or [],
                "associated_entity": product.get("associated_entity"),
                "validation_status": validation_status,
                "confidence_score": product.get("confidence_score"),
                "kpi_eligible": kpi_eligible,
                "dashboard_counted": kpi_eligible,
                "reconciliation_status": reconciliation_status,
                "reason": reason,
            }
        )
    return rows


def _payload_list(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload.get(key)
    return value if isinstance(value, list) else []


def _payload_products(payload: dict[str, Any]) -> list[dict[str, Any]]:
    products = list(_payload_list(payload, "produccion_cientifica"))
    for item in _payload_list(payload, "intercambios"):
        products.append(
            {
                "type": "PRESENTATION",
                "title": item.get("title") or "Intercambio de conocimiento",
                "authors": [],
                "status": item.get("type"),
                "impact": item.get("faculty"),
                "link": None,
                "source_section": "intercambios",
            }
        )
    return products


def _append_entity(items: list[dict[str, Any]], seen: set[str], entity: dict[str, Any]) -> None:
    key = "|".join([normalize_key(entity.get("type")), normalize_key(entity.get("code") or entity.get("name"))])
    if not key.strip("|") or key in seen:
        return
    seen.add(key)
    items.append(entity)


def _marker_entity(
    *,
    entity_type: str,
    code: str | None,
    name: str,
    filename: str | None,
    confidence_score: float | None,
    reason: str,
) -> dict[str, Any]:
    return {
        "type": entity_type,
        "code": code,
        "name": name,
        "director": None,
        "progress_percentage": None,
        "status": None,
        "validation_status": "validado",
        "source_file": filename,
        "source_page": None,
        "source_section": "datos_grupo_investigacion" if entity_type == "grupo_investigacion" else "datos_semillero",
        "confidence_score": confidence_score,
        "kpi_eligible": True,
        "dashboard_counted": True,
        "reconciliation_status": "counted",
        "reason": reason,
    }


def _looks_like_group(source_key: str) -> bool:
    return "GRUPO DE INVESTIGACION" in source_key or bool(re.search(r"\bGI\s*[-.]?\s*\d{1,4}", source_key))


def _looks_like_seedbed(source_key: str) -> bool:
    return "SEMILLERO" in source_key or bool(re.search(r"\bS\s*\.?\s*I\s*[-.]?\s*\d{1,4}\b", source_key))


def _extract_code(source_key: str, pattern: str) -> str | None:
    match = re.search(pattern, source_key)
    if not match:
        return None
    return re.sub(r"\s+", "", match.group(0).replace(".", "-"))


def _source_name(filename: str | None) -> str | None:
    if not filename:
        return None
    stem = PurePosixPath(str(filename)).stem
    for marker in ("-signed-signed", "-signed"):
        stem = stem.replace(marker, "")
    return _clean_name(stem.replace("_", " ").replace("-", " "))


def _clean_name(value: object) -> str | None:
    text = " ".join(str(value or "").split()).strip(" -")
    return text[:240] if text else None


def _strip_product_tail_noise(text: str) -> str:
    key = normalize_key(text)
    cut_index: int | None = None
    for marker in PRODUCT_TAIL_MARKERS:
        index = key.find(marker)
        if index >= 18 and (cut_index is None or index < cut_index):
            cut_index = index
    if cut_index is None:
        return text
    return _trim_by_normalized_index(text, cut_index)


def _trim_by_normalized_index(text: str, normalized_index: int) -> str:
    normalized_size = 0
    for index, char in enumerate(text):
        normalized_char = unicodedata.normalize("NFKD", char.upper())
        normalized_char = "".join(item for item in normalized_char if not unicodedata.combining(item))
        if normalized_char.strip():
            normalized_size += len(normalized_char)
        if normalized_size >= normalized_index:
            return text[:index].strip(" -.,;")
    return text.strip(" -.,;")


def _trim_probable_author_tail(text: str) -> str:
    sentences = [item.strip(" -") for item in re.split(r"\.\s+", text) if item.strip(" -")]
    if len(sentences) > 1:
        first_tokens = normalize_key(sentences[0]).split()
        rest_tokens = normalize_key(" ".join(sentences[1:])).split()
        if len(first_tokens) >= 4 and sum(1 for token in rest_tokens if token in COMMON_PERSON_TOKENS) >= 2:
            return sentences[0].strip(" -.,;")

    tokens = text.split()
    normalized_tokens = [normalize_key(token).strip(".,;:") for token in tokens]
    if len(tokens) < 12:
        return text
    for index in range(5, len(tokens) - 2):
        tail = normalized_tokens[index:]
        if sum(1 for token in tail if token in COMMON_PERSON_TOKENS) >= 3:
            before = " ".join(tokens[:index]).strip(" -.,;")
            if len(normalize_key(before).split()) >= 4:
                return before
    return text


def _looks_like_non_product_title(title: str) -> bool:
    key = normalize_key(title)
    tokens = key.split()
    if not tokens:
        return True
    role_markers = {"TUTOR", "ESTUDIANTE", "DOCTORANTE", "DOCENTE", "AUTOR"}
    if any(marker in tokens for marker in role_markers) and len(tokens) <= 6:
        return True
    if key in {"TUTOR DOCTORANTE", "TUTOR ESTUDIANTE", "TUTOR ESTUDIANTE 1 ESTUDIANTE 2"}:
        return True
    return False


def _looks_like_url_or_identifier(text: str, key: str) -> bool:
    if "/" in key or any(marker in key for marker in ("HTTP", "WWW", " DOI ", "ISSN", "ISBN", "INDEX PHP", "DRIVE GOOGLE")):
        return True
    if re.search(r"\b[A-Z]*\d[A-Z0-9]*\b", key) and re.search(r"\.|/", text):
        return True
    if re.search(r"\.[A-Z]{1,3}\s+[A-Z]{1,3}\b", key):
        return True
    return False


def _looks_like_incomplete_reported_product(key: str) -> bool:
    return "LIBRO" in key and ("NO HA SIDO FINALIZADO" in key or "PLANIFICADO" in key)


def _has_embedded_product_title(tokens: list[str]) -> bool:
    title_terms = {
        "ANALISIS",
        "CADENA",
        "CONOCIMIENTO",
        "COOPERATIVAS",
        "CREDITO",
        "DECISIONES",
        "DESARROLLO",
        "ECUADOR",
        "EFICIENCIA",
        "EMPRESARIAL",
        "FINANCIERA",
        "GESTION",
        "GUAYAQUIL",
        "IMPORTANCIA",
        "INTELIGENTE",
        "MARCO",
        "MICROEMPRESAS",
        "PANDEMIA",
        "PLANIFICACION",
        "POTENCIAR",
        "RECURSOS",
        "TECNICA",
        "TECNOLOGIAS",
        "TEORIC",
        "TOMA",
        "TRIBUTARIA",
    }
    return sum(1 for token in tokens if token in title_terms) >= 2


def _has_field_or_header_noise(key: str) -> bool:
    invalid_markers = (
        "EVIDENCIA",
        "EVIDENCIAS",
        "ANEXO",
        "ADJUNTO",
        "OBSERVACION",
        "DESCRIPCION",
        "RESUMEN",
        "AVANCE",
        "AGREGAR NUEVO REGISTRO",
        "ACCIONES EJECUTADAS",
        "ACTIVIDADES REALIZADAS",
        "SE ADJUNTA",
        "CARTA DE ACEPTACION",
        "CARTA DE INVITACION",
        "LINK DE EVIDENCIA",
        "ENLACE DE EVIDENCIA",
        "PIRAMIDE CIENTIFICA",
        "EDITORIAL ENVIADO",
    )
    if key in {"REGIONAL", "REGIONAL .", "MUNDIAL", "IMPACTO", "ESTADO", "LINK", "PUBLICADO", "ACEPTADO"}:
        return True
    return any(marker in key for marker in invalid_markers)


def _has_glued_author_tail(tokens: list[str]) -> bool:
    if len(tokens) < 10:
        return False
    name_tokens = {
        "ANA",
        "CARLOS",
        "CARMEN",
        "DOLORES",
        "FERNANDO",
        "JANINA",
        "JOSE",
        "JOSUE",
        "MARIA",
        "PILAR",
        "ROSA",
        "VERA",
        "VITERI",
        "ZAMBRANO",
    }
    tail = tokens[max(0, len(tokens) - 12) :]
    return sum(1 for token in tail if token in name_tokens) >= 3


def _looks_truncated_title(tokens: list[str]) -> bool:
    if tokens[-1] in {"A", "AL", "COMO", "CON", "DE", "DEL", "EL", "EN", "LA", "LAS", "LOS", "PARA", "POR", "Y"}:
        return True
    truncated_tokens = {
        "CORP",
        "CORPORAT",
        "ECONOMIC",
        "EMPR",
        "FINANCIE",
        "TEORIC",
        "SUMINISTR",
    }
    if tokens[-1] in truncated_tokens:
        return True
    if len(tokens) <= 3 and any(token in truncated_tokens for token in tokens):
        return True
    return False


def _progress_percent(value: object) -> int | None:
    match = re.search(r"\d+", str(value or ""))
    if not match:
        return None
    return max(0, min(100, int(match.group(0))))
