from __future__ import annotations

from .confidence import fuzzy_score
from .normalizer import normalize_key


HEADER_ALIASES: dict[str, list[list[str]]] = {
    "integrantes_internos": [
        ["NOMBRE COMPLETO", "FACULTAD", "CARRERA"],
        ["DOCENTE", "FACULTAD", "CARRERA"],
    ],
    "integrantes_externos": [
        ["INSTITUCION EXTERNA", "INVESTIGADOR"],
        ["NOMBRE COMPLETO", "INSTITUCION EXTERNA"],
    ],
    "proyectos_fci": [["CODIGO FCI", "PROYECTO", "DIRECTOR", "AVANCE", "ESTADO"]],
    "produccion_cientifica": [["TITULO", "AUTOR", "ESTADO", "IMPACTO"]],
    "intercambios": [["FACULTAD INTERVINIENTE", "TIPO", "TITULO"]],
}


def classify_table(lines: list[str], fallback: str | None = None) -> tuple[str | None, float]:
    if not lines:
        return fallback, 0.0
    header_text = normalize_key(" ".join(lines[:12]))
    best_kind = fallback
    best_score = 0.0
    for kind, signatures in HEADER_ALIASES.items():
        for signature in signatures:
            scores = [max(fuzzy_score(token, line) for line in lines[:12]) for token in signature]
            score = sum(scores) / len(scores)
            if score > best_score:
                best_kind = kind
                best_score = score
    if best_score >= 75:
        return best_kind, best_score
    return fallback, best_score if fallback else 0.0
