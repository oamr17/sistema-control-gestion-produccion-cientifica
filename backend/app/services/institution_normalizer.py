from __future__ import annotations

import unicodedata


EXTERNAL_INSTITUTION_ALIASES = {
    "UNIVERSIDAD DE ALMERIA": "Universidad de Almería",
    "UNIVERSIDAD DE MURCIA": "Universidad de Murcia",
    "UNIVERSIDAD ESTATAL DE MILAGRO": "Universidad Estatal de Milagro",
    "UNIVERSIDAD REGIONAL AMAZONICA": "Universidad Regional Amazónica",
}


def institution_key(value: object) -> str:
    text = str(value or "").replace(":", " ").strip().upper()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.split())


def normalize_external_institution_display(value: object) -> str | None:
    text = " ".join(str(value or "").replace(":", " ").split()).strip(" -")
    key = institution_key(text)
    if not key or len(key) < 6:
        return None
    if "GRADUADO" in key or "EGRESADO" in key:
        return None
    if key in {"UG", "UNIVERSIDAD DE GUAYAQUIL"} or "UNIVERSIDAD DE GUAYAQU" in key:
        return None
    if not any(marker in key for marker in ("UNIVERSIDAD", "INSTITUTO", "ESCUELA", "CENTRO", "FUNDACION", "POLITECNICA")):
        return None
    return EXTERNAL_INSTITUTION_ALIASES.get(key, _pretty_institution_name(text))


def _pretty_institution_name(value: str) -> str:
    small_words = {"DE", "DEL", "LA", "LAS", "LOS", "Y"}
    parts: list[str] = []
    for token in institution_key(value).split():
        if token in small_words:
            parts.append(token.lower())
        elif len(token) <= 3:
            parts.append(token.upper())
        else:
            parts.append(token.capitalize())
    return " ".join(parts)[:180] if parts else ""
