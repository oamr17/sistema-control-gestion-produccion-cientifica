from __future__ import annotations

from .deduplicator import dedupe_externals, dedupe_internals, dedupe_products, dedupe_projects
from .models import ParseLog, ParsedReport
from .participants import build_participant_summary
from .research_entity_parser import parse_research_entities
from .section_detector import detect_sections
from .table_classifier import classify_table
from .table_parser import (
    parse_external_members,
    parse_director_responsible,
    parse_interchanges,
    parse_internal_members,
    parse_projects_fci,
    parse_scientific_products,
)

DATA_SECTIONS = {
    "integrantes_internos",
    "integrantes_externos",
    "director_responsable",
    "proyectos_fci",
    "produccion_cientifica",
    "intercambios",
}


def _with_source_page(items: list[dict], page: int | None) -> list[dict]:
    for item in items:
        item.setdefault("source_page", page)
    return items


def _stamp_product_candidates(items: list[dict]) -> list[dict]:
    for index, item in enumerate(items, start=1):
        item.setdefault("row_or_block_id", f"produccion_cientifica:{index}")
        item.setdefault("source_field", "title")
        fields_detected = set(item.get("fields_detected") or [])
        if item.get("title"):
            fields_detected.add("title")
        if item.get("authors"):
            fields_detected.add("authors")
        if item.get("status"):
            fields_detected.add("status")
        if item.get("impact"):
            fields_detected.add("impact")
        if item.get("link") or item.get("doi"):
            fields_detected.add("link")
        item["fields_detected"] = sorted(fields_detected)
        required_fields = {"title", "authors", "status"}
        item["fields_missing"] = sorted(required_fields - fields_detected)
    return items


def parse_progress_report(text: str | None) -> ParsedReport:
    report = ParsedReport()
    report.research_entities = parse_research_entities(text)
    for section in detect_sections(text):
        table_kind, table_score = classify_table(section.lines, section.kind)
        effective_kind = section.kind if section.kind in DATA_SECTIONS else section.kind
        report.logs.append(
            ParseLog(
                field="section",
                value=section.title,
                score=section.score,
                method="section_alias_fuzzy",
                table=effective_kind,
                requires_review=section.requires_review or table_score < 90,
            )
        )
        if section.kind not in DATA_SECTIONS:
            continue
        if effective_kind == "integrantes_internos":
            report.integrantes_internos.extend(_with_source_page(parse_internal_members(section.lines, report.logs), section.page))
        elif effective_kind == "integrantes_externos":
            report.integrantes_externos.extend(_with_source_page(parse_external_members(section.lines, report.logs), section.page))
        elif effective_kind == "director_responsable":
            report.responsables.extend(_with_source_page(parse_director_responsible(section.lines, report.logs, section.title), section.page))
        elif effective_kind == "proyectos_fci":
            report.proyectos_fci.extend(_with_source_page(parse_projects_fci(section.lines, report.logs), section.page))
        elif effective_kind == "produccion_cientifica":
            report.produccion_cientifica.extend(_with_source_page(parse_scientific_products(section.lines, report.logs), section.page))
        elif effective_kind == "intercambios":
            report.intercambios.extend(_with_source_page(parse_interchanges(section.lines, report.logs), section.page))

    report.integrantes_internos = dedupe_internals(report.integrantes_internos)
    report.integrantes_externos = dedupe_externals(report.integrantes_externos)
    report.proyectos_fci = dedupe_projects(report.proyectos_fci)
    report.produccion_cientifica = _stamp_product_candidates(dedupe_products(report.produccion_cientifica))
    participant_summary = build_participant_summary(report.to_public_dict())
    report.normalized_participants = participant_summary["normalized_participants"]
    report.participants_audit = participant_summary["participants_audit"]
    report.person_aliases = participant_summary["person_aliases"]
    report.possible_merge_review = participant_summary["possible_merge_review"]
    report.participants_summary = participant_summary["participants_summary"]
    if any(log.requires_review for log in report.logs):
        report.estado = "requires_review"
    return report
