from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Literal

from .normalizer import normalize_key


DocumentStatus = Literal["accepted", "requires_review", "ignored"]


REPORT_SIGNALS = [
    "INFORME PARCIAL",
    "GRUPOS DE INVESTIGACION",
    "DATOS GENERALES",
    "CICLO ACADEMICO",
    "INVESTIGADORES",
    "PROYECTOS FCI",
]

FORM_STRUCTURE_SIGNALS = [
    "DATOS GENERALES DE GRUPOS DE INVESTIGACION",
    "DATOS GENERALES DEL PROYECTO DE INVESTIGACION",
    "DATOS GENERALES",
    "NOMBRE DEL GRUPO DE INVESTIGACION",
    "NOMBRE DEL GRUPO",
    "CICLO ACADEMICO QUE REPORTA",
    "CICLO ACADEMICO",
    "INVESTIGADORES QUE PARTICIPAN",
    "INVESTIGADORES QUE PARTICIPAN EN EL GRUPO",
    "INVESTIGADORES QUE PARTICIPAN EN EL PROYECTO",
    "INSTITUCIONES E INVESTIGADORES EXTERNOS",
    "INVESTIGADORES EXTERNOS",
    "SEGUIMIENTO A PROYECTOS FCI",
    "ACTIVIDADES REALIZADAS POR PROYECTOS FCI",
    "PROYECTOS FCI VINCULADOS",
]

EMAIL_CAPTURE_SIGNALS = [
    "OUTLOOK",
    "BANDEJA DE ENTRADA",
    "DESDE",
    "PARA",
    "CC",
    "ASUNTO",
    "ENVIADO",
    "1 ARCHIVO ADJUNTO",
    "OUTLOOK.CLOUD.MICROSOFT",
    "MAIL/ID",
    "MENSAJE ORIGINAL",
    "RE:",
    "RV:",
]

IGNORED_SIGNALS = [
    "CARTA",
    "OFICIO",
    "SOLICITUD",
    "PRESENTACION",
    "MEMORANDO",
]


@dataclass(slots=True)
class DocumentClassification:
    document_type: str
    status: DocumentStatus
    score: float
    matched_signals: list[str] = field(default_factory=list)
    ignored_signals: list[str] = field(default_factory=list)
    requires_review: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def classify_document(text: str | None, filename: str | None = None) -> DocumentClassification:
    text_key = normalize_key(text)
    filename_key = normalize_key(filename)
    combined_key = f"{filename_key} {text_key}".strip()

    if not combined_key:
        return DocumentClassification(
            document_type="empty_pdf",
            status="ignored",
            score=0,
            requires_review=True,
            warnings=["No se pudo extraer texto suficiente del PDF."],
        )

    email_matches = [signal for signal in EMAIL_CAPTURE_SIGNALS if signal in combined_key]
    structure_matches = [signal for signal in FORM_STRUCTURE_SIGNALS if signal in combined_key]
    if len(email_matches) >= 4:
        return DocumentClassification(
            document_type="email_capture",
            status="ignored",
            score=100,
            matched_signals=structure_matches,
            ignored_signals=email_matches,
            warnings=[
                "PDF corresponde a captura/exportacion de correo Outlook, no al formulario de informe."
            ],
        )

    matched = [signal for signal in REPORT_SIGNALS if signal in combined_key]
    if re.search(r"\bGI\d{3,}", filename_key):
        matched.append("FILENAME_GI")
    ignored = [signal for signal in IGNORED_SIGNALS if signal in combined_key]
    report_score = min(100, (len(matched) / len(REPORT_SIGNALS)) * 100)

    if len(matched) >= 3 and len(structure_matches) >= 2:
        warnings = []
        if ignored:
            warnings.append(
                "El documento contiene palabras de oficio/carta, pero tambien suficientes senales de informe."
            )
        return DocumentClassification(
            document_type="progress_report",
            status="accepted",
            score=report_score,
            matched_signals=[*matched, *structure_matches],
            ignored_signals=ignored,
            warnings=warnings,
        )

    if ignored and len(matched) <= 1:
        return DocumentClassification(
            document_type="administrative_document",
            status="ignored",
            score=max(report_score, 40),
            matched_signals=matched,
            ignored_signals=ignored,
            warnings=["El PDF parece ser carta/oficio/solicitud/memorando, no un informe parcial."],
        )

    return DocumentClassification(
        document_type="unknown",
        status="requires_review",
        score=report_score,
        matched_signals=matched,
        ignored_signals=ignored,
        requires_review=True,
        warnings=["No hay suficientes senales para confirmar que sea un informe parcial de GI."],
    )
