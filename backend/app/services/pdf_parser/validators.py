from __future__ import annotations

import re

from .confidence import best_alias
from .normalizer import normalize_key, normalize_line
from app.services.institution_normalizer import normalize_external_institution_display


CAREER_CATALOG = [
    "Licenciatura en Administracion de Empresas",
    "Licenciatura en Contabilidad y Auditoria",
    "Licenciatura en Comercio Exterior",
    "Licenciatura en Mercadotecnia",
    "Licenciatura en Finanzas",
    "Licenciatura en Gestion de la Informacion Gerencial",
    "Licenciatura en Turismo",
    "Licenciatura en Negocios Internacionales",
    "Licenciatura en Pedagogia de las Ciencias Experimentales de las Matematicas",
]
FACULTY_CATALOG = ["CIENCIAS ADMINISTRATIVAS", "FILOSOFIA Y LETRAS DE LA EDUCACION"]
STATUS_CATALOG = ["Vigente", "Aprobado", "Publicado", "Enviado a revision", "Aceptado"]
IMPACT_CATALOG = ["Impacto mundial", "Impacto regional"]
TYPE_CATALOG = ["Conferencia", "Ponencia", "Charla", "Taller"]

NON_PERSON_WORDS = {
    "ADMINISTRACION",
    "ADMINISTRATIVAS",
    "ACLARATORIA",
    "ANALISIS",
    "AUDITORIA",
    "CARRERA",
    "CIENCIAS",
    "CLAVES",
    "COMERCIO",
    "CONTABILIDAD",
    "CORRECTAMENTE",
    "CRECIMIE",
    "CRECIMIENTO",
    "DEBE",
    "DESARROLLO",
    "DOCENTE",
    "EDUCACION",
    "ELECTRONICAMENTE",
    "ECUATORI",
    "ECUATORIANO",
    "ECUATORIANA",
    "EDOR",
    "EMPREND",
    "EMPRENDEDOR",
    "EMPRENDEDORA",
    "EMPRESAS",
    "ESTADO",
    "EXITO",
    "EXTERIOR",
    "EXPERIMENTALES",
    "FACULTAD",
    "FACULTADES",
    "FIRMADO",
    "IMPACTO",
    "GRUPO",
    "INGENIERIA",
    "INFORMACION",
    "INCOMPLETO",
    "INVESTIGACION",
    "INVESTIGADOR",
    "INVESTIGADORA",
    "INVESTIGADORES",
    "LICENCIATURA",
    "MUNDIAL",
    "MATEMATICAS",
    "NACION",
    "NOMBRE",
    "NOTA",
    "OBJETIVO",
    "PARA",
    "PARTICIPACION",
    "PARTICIPARON",
    "PEDAGOGIA",
    "PRESIONAR",
    "POSTULACION",
    "PRODUCTO",
    "PUBLICADO",
    "PROYECTO",
    "QUIMICA",
    "REGIONAL",
    "REVISION",
    "SEMILLERO",
    "SOBRE",
    "TITULO",
    "TRIBUTARIA",
    "UNIVERSIDAD",
    "ENVIAR",
    "INTERVINIENTES",
}

NON_PERSON_JOINED_FRAGMENTS = {
    "ADMINISTRACION",
    "CRECIMIENTO",
    "ECUATORIANO",
    "ECUATORIANA",
    "EMPRENDEDOR",
    "EMPRENDEDORA",
    "FORMULARIO",
    "INVESTIGACION",
    "INTERVINIENTES",
    "LICENCIATURA",
    "POSTULACION",
    "SEMILLERO",
}

PERSON_PARTICLES = {"DE", "DEL", "LA", "LAS", "LOS", "Y"}
PHRASE_FUNCTION_WORDS = PERSON_PARTICLES | {"A", "AL", "COMO", "CON", "EL", "EN", "PARA", "POR", "UN", "UNA"}


def looks_like_person_name(value: str | None) -> bool:
    key = normalize_key(str(value or "").replace("...", " ").replace("…", " "))
    tokens = key.split()
    if len(tokens) < 2 or len(tokens) > 7:
        return False
    semantic_tokens = [token for token in tokens if token not in PHRASE_FUNCTION_WORDS]
    if len(semantic_tokens) < 2:
        return False
    if len(tokens) >= 4 and sum(1 for token in tokens if token in PHRASE_FUNCTION_WORDS) / len(tokens) >= 0.4:
        return False
    if re.search(r"\d|/|\\|_|PDF|SIGNED|INFSEM|GI\d", key):
        return False
    if any(token in NON_PERSON_WORDS or len(token) <= 1 for token in tokens):
        return False
    joined_tokens = "".join(tokens)
    if any(fragment in joined_tokens for fragment in NON_PERSON_JOINED_FRAGMENTS):
        return False
    return all(re.fullmatch(r"[A-ZÑ]+", token) for token in tokens)


def person_key(value: str | None) -> str:
    particles = {"DE", "DEL", "LA", "LAS", "LOS", "Y"}
    key = normalize_key(str(value or "").replace("...", " ").replace("…", " "))
    return " ".join(token for token in key.split() if token not in particles)


def title_case_name(value: str) -> str:
    return " ".join(part.capitalize() for part in normalize_key(value).lower().split())


def validate_catalog(value: str | None, catalog: list[str], threshold: float = 75) -> tuple[str | None, float, bool]:
    if not value:
        return None, 0, True
    alias, score = best_alias(value, catalog)
    if alias and score >= threshold:
        return alias, score, score < 90
    return normalize_line(value), score, True


def validate_career(value: str | None) -> tuple[str | None, float, bool]:
    return validate_catalog(value, CAREER_CATALOG)


def validate_faculty(value: str | None) -> tuple[str | None, float, bool]:
    return validate_catalog(value, FACULTY_CATALOG)


def validate_status(value: str | None) -> tuple[str | None, float, bool]:
    return validate_catalog(value, STATUS_CATALOG)


def validate_institution(value: str | None) -> str | None:
    text = normalize_line(value)
    key = normalize_key(text)
    if not key or looks_like_person_name(text):
        return None
    return normalize_external_institution_display(text)
