from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.core.database import SessionLocal
from app.models.entities import ImportedOcrTrace, ImportJob
from app.services.investigator_seed import (
    DEFAULT_SEED_PATH,
    InvestigatorSeedMatch,
    InvestigatorSeedRecord,
    InvestigatorSeedService,
    normalize_name_key,
)


SPECIFIC_CASES = [
    "Fernando Zambrano Farias",
    "Gianella Giler Valverder",
    "Roberth Fabian Ramirez Grand",
    "Dolores Ortiz Guevara",
    "Ortiz Luzuriag",
    "Ordonez Guart",
    "Delgado Litard",
    "Montesdeoca",
    "Rafael Apolinario",
    "Ira Campove Rde Jorge",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit PDF participants against the FCA investigator seed file.")
    parser.add_argument("--batch-id", type=int, default=167)
    parser.add_argument("--seed-file", default=str(DEFAULT_SEED_PATH))
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    service = InvestigatorSeedService(args.seed_file)
    with SessionLocal() as db:
        traces = (
            db.query(ImportedOcrTrace)
            .join(ImportJob, ImportedOcrTrace.import_job_id == ImportJob.id)
            .filter(ImportJob.batch_id == args.batch_id)
            .order_by(ImportedOcrTrace.id.asc())
            .all()
        )
        result = audit_batch(args.batch_id, traces, service)

    output = args.output or f"backend/reports/investigator_seed_batch_{args.batch_id}.json"
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(_console_summary(result, output_path), ensure_ascii=False, indent=2, default=str))


def audit_batch(batch_id: int, traces: list[ImportedOcrTrace], service: InvestigatorSeedService) -> dict[str, Any]:
    participant_rows: list[dict[str, Any]] = []
    for trace in traces:
        payload = trace.parsed_payload or {}
        trace_project_codes = _trace_project_codes(payload)
        participants = payload.get("normalized_participants") if isinstance(payload.get("normalized_participants"), list) else []
        for participant in participants:
            if participant.get("show_in_participants") is False or participant.get("validation_status") == "descartado":
                continue
            participant_rows.append(_audit_participant(trace, participant, trace_project_codes, service))

    kpi_rows = [
        row
        for row in participant_rows
        if row["person_type"] == "docente_interno" and row["kpi_eligible"] is True
    ]
    pending_rows = [
        row
        for row in participant_rows
        if row["kpi_decision"] != "suma_kpi_pdf" and row["review_bucket"] in {"pending_person", "pending_author_classification", "pending_merge"}
    ]
    specific_cases = {
        case: _rows_for_case(participant_rows, case, service)
        for case in SPECIFIC_CASES
    }

    return {
        "batch_id": batch_id,
        "seed_file": str(service.path),
        "seed_stats": service.stats(),
        "rules": [
            "Cedula exacta valida identidad, si aparece disponible.",
            "Nombre normalizado exacto o nombre invertido valida identidad.",
            "Similitud fuerte valida identidad solamente para resolver nombre/alias, no para activar KPI.",
            "Coincidencia por codigo de proyecto y rol queda como sugerencia si el nombre esta truncado.",
            "Similitud debil queda como sugerencia y no fusiona automaticamente.",
            "El KPI solo se mantiene cuando ya existe evidencia PDF en la normalizacion persistida.",
            "El Excel no crea participantes ni autores y no suma KPI por si solo.",
        ],
        "summary": _summary(participant_rows, kpi_rows, pending_rows),
        "kpi_internal_teachers": sorted(kpi_rows, key=lambda row: row["detected_name"]),
        "pending_or_review": sorted(pending_rows, key=lambda row: row["detected_name"]),
        "specific_cases": specific_cases,
    }


def _audit_participant(
    trace: ImportedOcrTrace,
    participant: dict[str, Any],
    trace_project_codes: list[str],
    service: InvestigatorSeedService,
) -> dict[str, Any]:
    detected_name = str(participant.get("canonical_name") or "")
    role_keys = _role_keys(participant)
    project_codes = list(dict.fromkeys([*_participant_project_codes(participant), *trace_project_codes]))
    match = service.match_name(
        detected_name,
        project_codes=project_codes,
        role_keys=role_keys,
    )
    return _row_from_match(
        trace=trace,
        participant=participant,
        detected_name=detected_name,
        match=match,
        service=service,
    )


def _row_from_match(
    *,
    trace: ImportedOcrTrace,
    participant: dict[str, Any],
    detected_name: str,
    match: InvestigatorSeedMatch,
    service: InvestigatorSeedService,
) -> dict[str, Any]:
    record = match.record if match.decision != "no_match" else None
    kpi_eligible = bool(participant.get("kpi_eligible"))
    review_bucket = str(participant.get("review_bucket") or "")
    person_type = str(participant.get("person_type") or "")
    kpi_decision, kpi_reason = _kpi_decision(participant, match)
    return {
        "detected_name": detected_name,
        "official_suggested": record.official_name if record else None,
        "seed_raw_name": record.raw_name if record else None,
        "identity_number": record.identity_number if record else None,
        "seed_roles": _seed_roles(service, record),
        "seed_project_codes": _seed_project_codes(service, record),
        "pdf": trace.source_filename,
        "trace_id": trace.id,
        "person_type": person_type,
        "review_bucket": review_bucket,
        "validation_status": participant.get("validation_status"),
        "kpi_eligible": kpi_eligible,
        "source_sections": participant.get("source_sections") or [],
        "source_fields": participant.get("source_fields") or [],
        "match_type": match.match_type,
        "match_confidence": round(match.confidence, 3),
        "match_decision": match.decision,
        "match_reason": match.reason,
        "matched_alias": match.matched_alias,
        "project_code_match": match.project_code_match,
        "role_match": match.role_match,
        "kpi_decision": kpi_decision,
        "kpi_reason": kpi_reason,
    }


def _kpi_decision(participant: dict[str, Any], match: InvestigatorSeedMatch) -> tuple[str, str]:
    if participant.get("person_type") == "docente_interno" and participant.get("kpi_eligible") is True:
        if match.decision == "identity_validated":
            return "suma_kpi_pdf", "Suma por evidencia PDF; la semilla respalda la identidad."
        if match.decision == "identity_suggested":
            return "suma_kpi_pdf", "Suma por evidencia PDF; la semilla solo sugiere revisar el alias."
        return "suma_kpi_pdf", "Suma por evidencia PDF; no depende de la semilla."
    if participant.get("review_bucket") == "pending_author_classification":
        return "pendiente_autor", "No suma KPI: es autoria pendiente aunque la semilla sugiera identidad."
    if participant.get("review_bucket") == "pending_person":
        return "pendiente_persona", "No suma KPI: el PDF tiene nombre incompleto o truncado."
    if participant.get("review_bucket") == "pending_merge":
        return "pendiente_merge", "No suma KPI hasta resolver posible duplicado."
    return "no_suma_kpi", "No es docente interno elegible en la normalizacion PDF."


def _summary(
    participant_rows: list[dict[str, Any]],
    kpi_rows: list[dict[str, Any]],
    pending_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "participants_compared": len(participant_rows),
        "kpi_internal_teachers": len({row["detected_name"] for row in kpi_rows}),
        "pending_or_review": len({row["detected_name"] for row in pending_rows}),
        "identity_validated": sum(1 for row in participant_rows if row["match_decision"] == "identity_validated"),
        "identity_suggested": sum(1 for row in participant_rows if row["match_decision"] == "identity_suggested"),
        "no_seed_match": sum(1 for row in participant_rows if row["match_decision"] == "no_match"),
        "kpi_seed_validated": sum(1 for row in kpi_rows if row["match_decision"] == "identity_validated"),
        "kpi_seed_suggested": sum(1 for row in kpi_rows if row["match_decision"] == "identity_suggested"),
        "kpi_without_seed_match": sum(1 for row in kpi_rows if row["match_decision"] == "no_match"),
    }


def _rows_for_case(
    participant_rows: list[dict[str, Any]],
    case: str,
    service: InvestigatorSeedService,
) -> list[dict[str, Any]]:
    case_key = normalize_name_key(case)
    case_tokens = set(case_key.split())
    rows = [
        row
        for row in participant_rows
        if row["detected_name"] and (
            normalize_name_key(row["detected_name"]) == case_key
            or case_tokens.issubset(set(normalize_name_key(row["detected_name"]).split()))
        )
    ]
    if rows:
        return rows
    match = service.match_name(case)
    record = match.record if match.decision != "no_match" else None
    return [
        {
            "detected_name": case,
            "official_suggested": record.official_name if record else None,
            "seed_raw_name": record.raw_name if record else None,
            "identity_number": record.identity_number if record else None,
            "seed_roles": _seed_roles(service, record),
            "seed_project_codes": _seed_project_codes(service, record),
            "match_type": match.match_type,
            "match_confidence": round(match.confidence, 3),
            "match_decision": match.decision,
            "match_reason": match.reason,
            "kpi_decision": "not_detected_in_batch",
            "kpi_reason": "No se encontro como participante normalizado en el batch.",
        }
    ]


def _trace_project_codes(payload: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    for key in ("research_entities", "proyectos_fci"):
        value = payload.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, dict) and item.get("code"):
                codes.append(str(item.get("code")))
    return list(dict.fromkeys(codes))


def _participant_project_codes(participant: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    for participation in participant.get("participations") or []:
        if isinstance(participation, dict) and participation.get("code"):
            codes.append(str(participation.get("code")))
    return list(dict.fromkeys(codes))


def _role_keys(participant: dict[str, Any]) -> list[str]:
    role_keys: list[str] = []
    role_keys.extend(str(item) for item in participant.get("institutional_roles") or [])
    role_keys.extend(str(item) for item in participant.get("production_roles") or [])
    for participation in participant.get("participations") or []:
        if isinstance(participation, dict) and participation.get("role"):
            role_keys.append(str(participation.get("role")))
    return list(dict.fromkeys(role_keys))


def _seed_roles(service: InvestigatorSeedService, record: InvestigatorSeedRecord | None) -> list[str]:
    if not record:
        return []
    return sorted(
        {
            " / ".join(part for part in [item.role, item.project_code, item.year] if part)
            for item in _related_records(service, record)
            if item.role
        }
    )


def _seed_project_codes(service: InvestigatorSeedService, record: InvestigatorSeedRecord | None) -> list[str]:
    if not record:
        return []
    return sorted({item.project_code for item in _related_records(service, record) if item.project_code})


def _related_records(service: InvestigatorSeedService, record: InvestigatorSeedRecord) -> list[InvestigatorSeedRecord]:
    return [
        item
        for item in service.load_records()
        if (record.identity_number and item.identity_number == record.identity_number)
        or (not record.identity_number and item.normalized_name == record.normalized_name)
    ]


def _console_summary(result: dict[str, Any], output_path: Path) -> dict[str, Any]:
    return {
        "output": str(output_path),
        "seed_stats": result["seed_stats"],
        "summary": result["summary"],
        "specific_cases": {
            case: [
                {
                    "detected_name": row.get("detected_name"),
                    "official_suggested": row.get("official_suggested"),
                    "identity_number": row.get("identity_number"),
                    "match_type": row.get("match_type"),
                    "match_decision": row.get("match_decision"),
                    "kpi_decision": row.get("kpi_decision"),
                }
                for row in rows
            ]
            for case, rows in result["specific_cases"].items()
        },
    }


if __name__ == "__main__":
    main()
