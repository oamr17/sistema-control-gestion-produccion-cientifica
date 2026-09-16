from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


ReviewStatus = Literal["accepted", "requires_review", "discarded"]


@dataclass(slots=True)
class ParseLog:
    field: str
    value: Any
    score: float
    method: str
    page: int | None = None
    table: str | None = None
    requires_review: bool = False
    warning: str | None = None


@dataclass(slots=True)
class ParsedSection:
    kind: str
    title: str
    lines: list[str]
    score: float
    page: int | None = None
    requires_review: bool = False


@dataclass(slots=True)
class ParsedReport:
    grupo: dict[str, Any] = field(default_factory=dict)
    coordinador: dict[str, Any] = field(default_factory=dict)
    responsables: list[dict[str, Any]] = field(default_factory=list)
    research_entities: list[dict[str, Any]] = field(default_factory=list)
    integrantes_internos: list[dict[str, Any]] = field(default_factory=list)
    integrantes_externos: list[dict[str, Any]] = field(default_factory=list)
    proyectos_fci: list[dict[str, Any]] = field(default_factory=list)
    actividades: list[dict[str, Any]] = field(default_factory=list)
    produccion_cientifica: list[dict[str, Any]] = field(default_factory=list)
    fondos: list[dict[str, Any]] = field(default_factory=list)
    intercambios: list[dict[str, Any]] = field(default_factory=list)
    normalized_participants: list[dict[str, Any]] = field(default_factory=list)
    participants_audit: list[dict[str, Any]] = field(default_factory=list)
    person_aliases: list[dict[str, Any]] = field(default_factory=list)
    possible_merge_review: list[dict[str, Any]] = field(default_factory=list)
    participants_summary: dict[str, Any] = field(default_factory=dict)
    estado: str = "accepted"
    logs: list[ParseLog] = field(default_factory=list)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "grupo": self.grupo,
            "coordinador": self.coordinador,
            "responsables": self.responsables,
            "research_entities": self.research_entities,
            "integrantes_internos": self.integrantes_internos,
            "integrantes_externos": self.integrantes_externos,
            "proyectos_fci": self.proyectos_fci,
            "actividades": self.actividades,
            "produccion_cientifica": self.produccion_cientifica,
            "fondos": self.fondos,
            "intercambios": self.intercambios,
            "normalized_participants": self.normalized_participants,
            "participants_audit": self.participants_audit,
            "person_aliases": self.person_aliases,
            "possible_merge_review": self.possible_merge_review,
            "participants_summary": self.participants_summary,
            "estado": self.estado,
            "logs": [asdict(log) for log in self.logs],
            "extraction_log": [asdict(log) for log in self.logs],
        }
