from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from app.schemas.human_review_operations import B2B1BackfillPlanV1
from app.services.human_review_backfill import backfill_plan_sha256
from app.services.human_review_invariants import PROTECTED_TABLES
from scripts.configure_human_review_privileges import B2B_TABLE_PRIVILEGES


HISTORICAL_ARTIFACT_SHA256 = (
    "4500639876650bd45fc18a1a437c03c91cdb80b80e1bf2a54a57179fbe168fbe"
)
LEGACY_PLAN_HASH_V1 = (
    "9d12a196431683746850ef1403d57c2c0b6431dc0bd54d2e0191bd9c57624948"
)
HISTORICAL_SEMANTIC_PLAN_HASH_V2 = (
    "f63a204c84e3df03ef07d175b3dedb652516180fcd63b624bc8e2ab3885685e2"
)
SEMANTIC_PLAN_HASH_V2 = HISTORICAL_SEMANTIC_PLAN_HASH_V2
HISTORICAL_PLAN_HASH_UNDER_V3 = (
    "cbe54ac557a3993fbf1d865d9d1e5458f4f15dae5a9f7fcdf78c9c6e4d173558"
)
APPROVED_PLAN_SHA256 = HISTORICAL_PLAN_HASH_UNDER_V3
productive_plan_sha256 = backfill_plan_sha256

CLASSIFICATIONS = frozenset({
    "SOURCE_DATA_DEFECT",
    "DERIVATION_GAP",
    "LEGITIMATE_AMBIGUITY",
    "EXPECTED_PENDING_REVIEW",
    "OUT_OF_SCOPE_RECORD",
    "CONTRACT_MISMATCH",
    "DIAGNOSTIC_FALSE_POSITIVE",
})
_SECRET_KEYS = frozenset({
    "password",
    "passwd",
    "secret",
    "token",
    "jwt",
    "api_key",
    "database_url",
    "url_with_credentials",
})
_FORBIDDEN_LOCATOR_PATTERN = re.compile(
    r"(?:^|[.:_\-])(ctid|row_?number|physical_?order|array_?index)(?:$|[.:_\-])",
    re.IGNORECASE,
)
_READ_ONLY_PREFIXES = ("SELECT", "SHOW", "SET TRANSACTION READ ONLY")
_MUTATING_SQL_PATTERN = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|TRUNCATE|CREATE|ALTER|DROP|GRANT|REVOKE|"
    r"MERGE|COPY|LOCK|VACUUM|ANALYZE|REFRESH|CALL|DO)\b|"
    r"\bFOR\s+(?:UPDATE|NO\s+KEY\s+UPDATE|SHARE|KEY\s+SHARE)\b",
    re.IGNORECASE,
)
_SEMANTIC_LOCATOR_KEYS = frozenset({
    "source_row_id",
    "source_record_id",
    "participant_identifier",
    "teacher_identifier",
    "project_code",
    "record_key",
    "block_key",
    "semantic_key",
})
_SECTION_KEYS = frozenset({"section", "section_name", "source_section"})
_B2B_TABLES = tuple(B2B_TABLE_PRIVILEGES)


class DiagnosticError(RuntimeError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DiagnosticError(message)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_bytes(plan: B2B1BackfillPlanV1) -> bytes:
    return plan.model_dump_json(indent=2).encode("utf-8") + b"\n"


def verify_historical_artifact(path: Path | str) -> dict[str, object]:
    artifact = Path(path)
    _require(artifact.is_file(), "historical plan artifact is required")
    artifact_hash = _file_sha256(artifact)
    _require(
        artifact_hash == HISTORICAL_ARTIFACT_SHA256,
        "historical artifact SHA-256 differs",
    )
    try:
        plan = B2B1BackfillPlanV1.model_validate_json(artifact.read_bytes())
    except Exception as error:
        raise DiagnosticError("historical plan artifact is invalid") from error
    _require(
        plan.deferred_records == () and plan.excluded_records == (),
        "historical plan must load with empty v3 additive collections",
    )
    _require(
        plan.hard_blockers == plan.blockers,
        "historical blockers must load as v3 hard blockers",
    )
    semantic_hash = productive_plan_sha256(plan)
    _require(
        semantic_hash == HISTORICAL_PLAN_HASH_UNDER_V3,
        "historical plan SHA-256 under v3 differs",
    )
    return {
        "historical_artifact_sha256": artifact_hash,
        "legacy_plan_hash_v1": LEGACY_PLAN_HASH_V1,
        "historical_semantic_plan_sha256": semantic_hash,
        "historical_semantic_plan_hash_v2": HISTORICAL_SEMANTIC_PLAN_HASH_V2,
        "semantic_plan_hash_v2": HISTORICAL_SEMANTIC_PLAN_HASH_V2,
        "historical_plan_hash_under_v3": semantic_hash,
        "plan": plan,
    }


def verify_semantic_reproduction(
    historical: B2B1BackfillPlanV1,
    reproduced: B2B1BackfillPlanV1,
    reproduced_artifact_path: Path | None = None,
) -> dict[str, object]:
    historical_semantic = historical.model_dump(mode="json", exclude={"captured_at"})
    reproduced_semantic = reproduced.model_dump(mode="json", exclude={"captured_at"})
    _require(
        historical_semantic == reproduced_semantic,
        "non-temporal semantic difference detected",
    )
    historical_hash = productive_plan_sha256(historical)
    reproduced_hash = productive_plan_sha256(reproduced)
    _require(
        historical_hash == HISTORICAL_PLAN_HASH_UNDER_V3,
        "historical v3 hash differs",
    )
    _require(
        reproduced_hash == HISTORICAL_PLAN_HASH_UNDER_V3,
        "reproduced v3 hash differs",
    )
    historical_bytes = _artifact_bytes(historical)
    reproduced_bytes = (
        reproduced_artifact_path.read_bytes()
        if reproduced_artifact_path is not None
        else _artifact_bytes(reproduced)
    )
    return {
        "legacy_plan_hash_v1": LEGACY_PLAN_HASH_V1,
        "historical_semantic_plan_sha256": historical_hash,
        "historical_semantic_plan_hash_v2": HISTORICAL_SEMANTIC_PLAN_HASH_V2,
        "semantic_plan_hash_v2": HISTORICAL_SEMANTIC_PLAN_HASH_V2,
        "historical_plan_hash_under_v3": historical_hash,
        "reproduced_semantic_plan_sha256": reproduced_hash,
        "reproduced_artifact_sha256": hashlib.sha256(reproduced_bytes).hexdigest(),
        "semantic_match": True,
        "artifact_byte_match": historical_bytes == reproduced_bytes,
        "semantic_differences": (
            ["captured_at"] if historical.captured_at != reproduced.captured_at else []
        ),
    }


def verify_reproduction(
    *,
    candidate_count: int,
    stable_target_count: int,
    blocker_counts: Mapping[str, int],
    plan_sha256: str,
) -> None:
    expected = {
        "missing_stable_locator": 19,
        "invariant_mismatch": 7,
    }
    _require(
        candidate_count == 74
        and stable_target_count == 67
        and dict(blocker_counts) == expected
        and plan_sha256 == APPROVED_PLAN_SHA256,
        "exact reproduction gate failed",
    )


def _normalized(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.casefold().split())
    return normalized or None


def _presence(value: object) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _short_hash(value: object) -> str | None:
    normalized = _normalized(value)
    if normalized is None:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def validate_locator_candidate(value: str) -> None:
    _require(bool(value.strip()), "locator candidate is blank")
    _require(
        _FORBIDDEN_LOCATOR_PATTERN.search(value) is None,
        "unstable locator candidate is forbidden",
    )


def validate_classification(record: Mapping[str, object]) -> None:
    value = record.get("classification")
    _require(
        isinstance(value, str) and value in CLASSIFICATIONS,
        "each case requires exactly one closed classification",
    )


def validate_remediation_proposal(proposal: Mapping[str, object]) -> None:
    _require(
        proposal.get("execution_authorized") is False,
        "remediation proposal must not authorize mutation",
    )


def ensure_read_only_sql(statement: str) -> None:
    normalized = " ".join(statement.strip().split())
    _require(bool(normalized), "read-only SQL statement is blank")
    _require(
        _MUTATING_SQL_PATTERN.search(normalized) is None,
        "diagnostic SQL must remain read-only",
    )
    upper = normalized.upper()
    _require(
        upper.startswith(_READ_ONLY_PREFIXES),
        "diagnostic SQL must remain read-only",
    )


def verify_database_unchanged(before: str, after: str) -> None:
    _require(before == after, "database changed during read-only diagnosis")


def sanitize(value: object) -> object:
    if isinstance(value, Mapping):
        sanitized: dict[str, object] = {}
        for raw_key, child in value.items():
            key = str(raw_key)
            lowered = key.casefold()
            if lowered in _SECRET_KEYS or any(secret in lowered for secret in _SECRET_KEYS):
                sanitized[key] = "[REDACTED]"
            else:
                sanitized[key] = sanitize(child)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [sanitize(child) for child in value]
    if isinstance(value, str) and re.search(r"\w+://[^\s:@]+:[^\s@]+@", value):
        return "[REDACTED]"
    return value


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        sanitize(value),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        default=str,
    ) + "\n"
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)


def _write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flat_rows: list[dict[str, object]] = []
    keys: set[str] = set()
    for row in rows:
        flat: dict[str, object] = {}
        for key, value in sanitize(row).items():  # type: ignore[union-attr]
            flat[key] = (
                json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
                if isinstance(value, (dict, list, tuple))
                else value
            )
        flat_rows.append(flat)
        keys.update(flat)
    fieldnames = sorted(keys)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(sorted(flat_rows, key=lambda row: str(row.get("case_key", ""))))
    temporary.replace(path)


def group_person_role_diagnostics(rows: Sequence[Mapping[str, object]]) -> dict[str, int]:
    return {
        "unique_rows": len({row.get("case_key", index) for index, row in enumerate(rows)}),
        "affected_identities": len({row.get("identity_group_key") for row in rows}),
        "affected_documents": len({row.get("document_group_key") for row in rows}),
        "affected_periods": len({row.get("period_id") for row in rows}),
    }


def diagnose_person_role(
    row: Mapping[str, object],
    related: Mapping[str, object],
) -> dict[str, object]:
    document = row.get("document_key")
    section = row.get("source_section")
    row_locator = row.get("row_or_block_id")
    if isinstance(row_locator, str):
        validate_locator_candidate(row_locator)
    metadata_paths = tuple(str(value) for value in related.get("metadata_locator_paths", ()))
    ocr_paths = tuple(str(value) for value in related.get("ocr_locator_paths", ()))
    for path in metadata_paths:
        validate_locator_candidate(path)
    source_matches = int(related.get("source_record_matches", 0))
    name_only_source_matches = int(related.get("name_only_source_record_matches", 0))
    conflicts = bool(related.get("conflicting_identity_fields"))
    missing = [
        label
        for label, value in (
            ("document_key", document),
            ("source_section", section),
            ("row_or_block_id", row_locator),
        )
        if not _presence(value)
    ]

    if not _presence(document):
        reason_code = "missing_source_document"
        classification = "SOURCE_DATA_DEFECT"
        confidence = "high"
        action = "recover a unique document reference from approved source evidence"
        option = "A"
    elif conflicts:
        reason_code = "conflicting_identity_fields"
        classification = "SOURCE_DATA_DEFECT"
        confidence = "high"
        action = "resolve conflicting identity fields from documentary evidence"
        option = "A"
    elif not missing:
        reason_code = "diagnostic_false_positive"
        classification = "DIAGNOSTIC_FALSE_POSITIVE"
        confidence = "high"
        action = "recheck diagnostic selection against productive target construction"
        option = "E"
    elif metadata_paths or len(ocr_paths) == 1 or source_matches == 1:
        reason_code = "locator_available_but_not_consumed"
        classification = "DERIVATION_GAP"
        confidence = "medium"
        action = "formally reopen Task 12 to consume deterministic provenance"
        option = "B"
    elif len(ocr_paths) > 1 or source_matches > 1:
        reason_code = "ambiguous_duplicate"
        classification = "LEGITIMATE_AMBIGUITY"
        confidence = "high"
        action = "retain for a human provenance decision"
        option = "C"
    elif not _presence(section) and not _presence(row_locator):
        reason_code = "historical_row_without_provenance"
        classification = "EXPECTED_PENDING_REVIEW"
        confidence = "high"
        action = "retain for human review; do not invent a locator"
        option = "C"
    elif not _presence(section):
        reason_code = "missing_source_section"
        classification = "SOURCE_DATA_DEFECT"
        confidence = "high"
        action = "recover the unique source section from documentary evidence"
        option = "A"
    else:
        reason_code = "missing_row_locator"
        classification = "EXPECTED_PENDING_REVIEW"
        confidence = "high"
        action = "retain for human review; do not use physical row order"
        option = "C"

    result: dict[str, object] = {
        "case_key": f"person_roles:{row.get('id')}:missing_stable_locator",
        "source_table": "person_roles",
        "source_id": row.get("id"),
        "period_id": row.get("period_id"),
        "academic_period": row.get("academic_period"),
        "import_job_id": row.get("import_job_id"),
        "document_group_key": _short_hash(document) or "missing",
        "identity_group_key": row.get("identity_group_key") or "missing",
        "source_document_sha256": _short_hash(document),
        "source_section": section,
        "source_page": row.get("source_page"),
        "row_or_block_id": row_locator,
        "parser_locator_paths": sorted(set(metadata_paths + ocr_paths)),
        "field_presence": {
            key: _presence(row.get(key))
            for key in (
                "person_key",
                "canonical_identity_key",
                "canonical_name_present",
                "raw_name_present",
                "normalized_name_present",
                "role_type",
                "career_id",
                "document_key",
                "source_section",
                "row_or_block_id",
                "parser_version",
            )
        },
        "stable_source_pk": row.get("id"),
        "stable_source_pk_is_locator": False,
        "missing_components": missing,
        "source_record_matches": source_matches,
        "name_only_source_record_matches": name_only_source_matches,
        "ocr_match_count": len(ocr_paths),
        "reason_code": reason_code,
        "classification": classification,
        "evidence": [
            f"missing={','.join(missing) if missing else 'none'}",
            f"metadata_locator_candidates={len(metadata_paths)}",
            f"ocr_locator_candidates={len(ocr_paths)}",
            f"source_record_matches={source_matches}",
            f"name_only_source_record_matches={name_only_source_matches}",
        ],
        "confidence": confidence,
        "recommended_action": action,
        "recommended_option": option,
        "risk": "high" if classification == "LEGITIMATE_AMBIGUITY" else "medium",
        "affected_components": (
            ["app.services.human_review_backfill._row_or_block_id"]
            if classification == "DERIVATION_GAP"
            else []
        ),
        "human_decision_required": classification in {
            "LEGITIMATE_AMBIGUITY",
            "EXPECTED_PENDING_REVIEW",
        },
    }
    validate_classification(result)
    return result


def _project_value(row: Mapping[str, object], normalized_key: str, raw_key: str) -> str | None:
    return _normalized(row.get(normalized_key)) or _normalized(row.get(raw_key))


def _criterion_match_counts(
    matrix: Sequence[Mapping[str, object]],
) -> dict[str, int]:
    return {
        "exact_normalized_code": sum(
            1 for row in matrix if "code" in row["matching_criteria"]
        ),
        "exact_normalized_name": sum(
            1 for row in matrix if "name" in row["matching_criteria"]
        ),
        "exact_code_and_name": sum(
            1
            for row in matrix
            if {"code", "name"}.issubset(row["matching_criteria"])
        ),
        "same_period": sum(
            1 for row in matrix if "period" in row["matching_criteria"]
        ),
        "same_document": sum(
            1 for row in matrix if "document" in row["matching_criteria"]
        ),
        "same_section": sum(
            1 for row in matrix if "section" in row["matching_criteria"]
        ),
    }


def correlate_director_relation(
    entity: Mapping[str, object],
    projects: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    entity_code = _project_value(entity, "normalized_code", "code")
    entity_name = _project_value(entity, "normalized_name", "name")
    matrix: list[dict[str, object]] = []
    candidates: list[int] = []
    for project in sorted(projects, key=lambda row: int(row["id"])):
        project_code = _project_value(project, "normalized_code", "code")
        project_name = _project_value(project, "normalized_name", "name")
        criteria = {
            "code": bool(entity_code and project_code and entity_code == project_code),
            "name": bool(entity_name and project_name and entity_name == project_name),
            "period": project.get("period_id") == entity.get("period_id"),
            "document": bool(
                entity.get("document_key")
                and project.get("document_key")
                and entity.get("document_key") == project.get("document_key")
            ),
            "section": bool(
                entity.get("source_section")
                and project.get("source_section")
                and entity.get("source_section") == project.get("source_section")
            ),
        }
        is_candidate = criteria["period"] and (criteria["code"] or criteria["name"])
        if is_candidate:
            candidates.append(int(project["id"]))
        matrix.append({
            "project_id": project.get("id"),
            "project_code_normalized": project_code,
            "project_name_normalized": project_name,
            "project_period_id": project.get("period_id"),
            "matching_criteria": sorted(key for key, match in criteria.items() if match),
            "nonmatching_criteria": sorted(key for key, match in criteria.items() if not match),
            "productive_candidate": is_candidate,
        })

    entity_type = _normalized(entity.get("type"))
    compatible_type = entity_type in {
        "project",
        "research_project",
        "proyecto",
        "proyectos",
        "proyecto_fci",
    }
    criterion_match_counts = _criterion_match_counts(matrix)
    same_period_count = criterion_match_counts["same_period"]
    if not compatible_type:
        cardinality = "OUT_OF_SCOPE"
        reason = "entity_type_incompatible"
        classification = "OUT_OF_SCOPE_RECORD"
        action = "exclude only after confirming population semantics"
        option = "D"
        affected_components = [
            "app.services.human_review_backfill._discover director population rule"
        ]
        human_decision_required = False
    elif len(candidates) == 0:
        cardinality = "ZERO_CANDIDATES"
        if same_period_count == 0:
            reason = "missing_same_period_project_evidence"
            classification = "CONTRACT_MISMATCH"
            action = (
                "formally reopen Task 12 to decide whether project entities may "
                "supply their own relation evidence"
            )
            option = "B"
            affected_components = [
                "app.services.human_review_backfill._matching_project_evidence",
                "app.services.human_review_backfill._discover director population rule",
            ]
            human_decision_required = False
        elif not entity_code and not entity_name:
            reason = "missing_entity_code_and_name"
            classification = "SOURCE_DATA_DEFECT"
            action = "recover unique entity identity from approved documentary evidence"
            option = "A"
            affected_components = ["research_entities"]
            human_decision_required = False
        else:
            reason = "project_evidence_does_not_match"
            classification = "EXPECTED_PENDING_REVIEW"
            action = "retain for human review; do not infer a project relation"
            option = "C"
            affected_components = []
            human_decision_required = True
    elif len(candidates) > 1:
        cardinality = "MULTIPLE_CANDIDATES"
        reason = "multiple_projects_candidates"
        classification = "LEGITIMATE_AMBIGUITY"
        action = "retain for a human project/director relation decision"
        option = "C"
        affected_components = []
        human_decision_required = True
    else:
        cardinality = "ONE_DETERMINISTIC_CANDIDATE"
        reason = "diagnostic_false_positive"
        classification = "DIAGNOSTIC_FALSE_POSITIVE"
        action = "verify productive diagnostic parity"
        option = "E"
        affected_components = [
            "app.services.human_review_backfill._matching_project_evidence"
        ]
        human_decision_required = False

    result: dict[str, object] = {
        "case_key": f"research_entities:{entity.get('id')}:invariant_mismatch",
        "source_table": "research_entities",
        "source_id": entity.get("id"),
        "research_entity_id": entity.get("id"),
        "period_id": entity.get("period_id"),
        "academic_period": entity.get("academic_period"),
        "entity_type": entity_type,
        "entity_code_original_present": _presence(entity.get("code")),
        "entity_code_normalized": entity_code,
        "entity_name_original_present": _presence(entity.get("name")),
        "entity_name_normalized": entity_name,
        "director_detected": _presence(entity.get("director_name")),
        "director_identity_sha256": _short_hash(
            entity.get("normalized_director_name") or entity.get("director_name")
        ),
        "document_sha256": _short_hash(entity.get("document_key")),
        "source_section": entity.get("source_section"),
        "candidate_project_ids": candidates,
        "candidate_count": len(candidates),
        "matrix": matrix,
        "criterion_match_counts": criterion_match_counts,
        "cardinality_result": cardinality,
        "reason_code": reason,
        "classification": classification,
        "evidence": [
            f"productive_candidate_count={len(candidates)}",
            f"same_period_projects={same_period_count}",
            "criterion_match_counts=" + json.dumps(
                criterion_match_counts,
                sort_keys=True,
                separators=(",", ":"),
            ),
        ],
        "confidence": "high",
        "recommended_action": action,
        "recommended_option": option,
        "risk": "high" if len(candidates) != 1 else "medium",
        "affected_components": affected_components,
        "human_decision_required": human_decision_required,
    }
    validate_classification(result)
    return result


def validate_complete_case_set(cases: Sequence[Mapping[str, object]]) -> None:
    keys = [case.get("case_key") for case in cases]
    _require(len(cases) == 26 and len(set(keys)) == 26, "report requires 26 unique cases")
    required = {
        "classification",
        "evidence",
        "confidence",
        "recommended_action",
        "risk",
        "affected_components",
        "human_decision_required",
    }
    for case in cases:
        _require(required.issubset(case), "case evidence is incomplete")
        validate_classification(case)


def _read_only_guard(
    _connection,
    _cursor,
    statement: str,
    _parameters,
    _context,
    _executemany,
) -> None:
    ensure_read_only_sql(statement)


def _query(connection: Connection, statement: str, parameters: Mapping[str, object] | None = None):
    ensure_read_only_sql(statement)
    return connection.execute(text(statement), dict(parameters or {}))


def _database_fingerprint(connection: Connection) -> tuple[str, dict[str, int]]:
    tables = tuple(PROTECTED_TABLES) + _B2B_TABLES + ("schema_migrations",)
    material: list[object] = []
    counts: dict[str, int] = {}
    for table_name in tables:
        rows = [dict(row) for row in _query(
            connection,
            f'SELECT * FROM "{table_name}" ORDER BY id'
            if table_name != "schema_migrations"
            else "SELECT * FROM schema_migrations ORDER BY version",
        ).mappings()]
        counts[table_name] = len(rows)
        material.append({"table": table_name, "rows": rows})
    payload = json.dumps(material, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest(), counts


def _metadata_candidates(metadata: object) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if not isinstance(metadata, Mapping):
        return (), ()
    locators: list[str] = []
    sections: list[str] = []
    for raw_key, value in metadata.items():
        key = str(raw_key).casefold()
        if not _presence(value):
            continue
        if key in _SEMANTIC_LOCATOR_KEYS:
            candidate = f"metadata.{key}"
            validate_locator_candidate(candidate)
            locators.append(candidate)
        if key in _SECTION_KEYS:
            sections.append(f"metadata.{key}")
    return tuple(sorted(set(locators))), tuple(sorted(set(sections)))


def _matching_payload_paths(value: object, tokens: set[str], path: str = "parsed_payload") -> list[str]:
    matches: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            matches.extend(_matching_payload_paths(child, tokens, f"{path}.{key}"))
    elif isinstance(value, list):
        for child in value:
            matches.extend(_matching_payload_paths(child, tokens, f"{path}.array_index"))
    else:
        normalized = _normalized(value)
        if normalized and normalized in tokens:
            matches.append(path)
    return matches


def _load_person_role_rows(connection: Connection, ids: Sequence[int]) -> list[dict[str, object]]:
    statement = """
        SELECT
            role_row.id, role_row.period_id, period.year_label, period.cycle,
            role_row.import_job_id, import_job.document_key, import_job.filename,
            import_job.source_rev, role_row.source_file, role_row.source_page,
            role_row.source_section, role_row.parser_version, role_row.metadata_json,
            role_row.person_key, role_row.canonical_identity_key,
            role_row.canonical_name, role_row.raw_name, role_row.normalized_name,
            role_row.role_type, role_row.teacher_id, teacher.career_id,
            career.name AS career_name, role_row.research_project_id,
            role_row.research_entity_id, role_row.scientific_production_id,
            role_row.external_researcher_id
        FROM person_roles AS role_row
        LEFT JOIN academic_periods AS period ON period.id = role_row.period_id
        LEFT JOIN import_jobs AS import_job ON import_job.id = role_row.import_job_id
        LEFT JOIN teachers AS teacher ON teacher.id = role_row.teacher_id
        LEFT JOIN careers AS career ON career.id = teacher.career_id
        WHERE role_row.id = ANY(:ids)
        ORDER BY role_row.id
    """
    return [dict(row) for row in _query(connection, statement, {"ids": list(ids)}).mappings()]


def _load_job_evidence(connection: Connection, job_ids: Sequence[int]) -> dict[int, dict[str, object]]:
    evidence: dict[int, dict[str, object]] = defaultdict(lambda: {
        "ocr_payloads": [],
        "research_records": [],
        "project_participants": [],
        "progress_reports": [],
    })
    if not job_ids:
        return evidence
    queries = (
        ("ocr_payloads", "SELECT import_job_id, parsed_payload FROM imported_ocr_traces WHERE import_job_id = ANY(:ids) ORDER BY id"),
        ("research_records", "SELECT import_job_id, national_id, full_name FROM imported_research_records WHERE import_job_id = ANY(:ids) ORDER BY id"),
        ("project_participants", "SELECT import_job_id, participant_identifier, project_code FROM imported_project_participants WHERE import_job_id = ANY(:ids) ORDER BY id"),
        ("progress_reports", "SELECT import_job_id, teacher_identifier, teacher_name FROM imported_progress_reports WHERE import_job_id = ANY(:ids) ORDER BY id"),
    )
    for label, statement in queries:
        for row in _query(connection, statement, {"ids": list(job_ids)}).mappings():
            evidence[int(row["import_job_id"])][label].append(dict(row))
    return evidence


def _person_role_diagnostics(connection: Connection, blocker_ids: Sequence[int]) -> list[dict[str, object]]:
    rows = _load_person_role_rows(connection, blocker_ids)
    _require(len(rows) == 19, "person-role blocker rows differ from 19")
    job_ids = sorted({int(row["import_job_id"]) for row in rows if row.get("import_job_id")})
    evidence_by_job = _load_job_evidence(connection, job_ids)
    diagnostics: list[dict[str, object]] = []
    for row in rows:
        document = row.get("document_key") or row.get("filename") or row.get("source_file")
        metadata = row.get("metadata_json")
        consumed_locator = None
        if isinstance(metadata, Mapping):
            consumed_locator = next(
                (
                    metadata.get(key)
                    for key in ("row_or_block_id", "row_id", "block_id")
                    if _presence(metadata.get(key))
                ),
                None,
            )
        metadata_paths, metadata_sections = _metadata_candidates(metadata)
        tokens = {
            normalized
            for normalized in (
                _normalized(row.get("person_key")),
                _normalized(row.get("canonical_name")),
                _normalized(row.get("raw_name")),
                _normalized(row.get("normalized_name")),
            )
            if normalized
        }
        job_evidence = evidence_by_job.get(int(row.get("import_job_id") or 0), {})
        ocr_paths: list[str] = []
        for payload_row in job_evidence.get("ocr_payloads", []):
            ocr_paths.extend(_matching_payload_paths(payload_row.get("parsed_payload"), tokens))
        stable_ocr_paths = [path for path in ocr_paths if "array_index" not in path]
        source_matches = 0
        name_only_source_matches = 0
        for label, identity_key, name_key in (
            ("research_records", "national_id", "full_name"),
            ("project_participants", "participant_identifier", None),
            ("progress_reports", "teacher_identifier", "teacher_name"),
        ):
            for evidence_row in job_evidence.get(label, []):
                identifier = _normalized(evidence_row.get(identity_key))
                name = _normalized(evidence_row.get(name_key)) if name_key else None
                if identifier and identifier in tokens:
                    source_matches += 1
                elif name and name in tokens:
                    name_only_source_matches += 1

        identity_value = (
            row.get("canonical_identity_key")
            or row.get("person_key")
            or row.get("normalized_name")
            or row.get("canonical_name")
        )
        role_input = {
            "id": row.get("id"),
            "period_id": row.get("period_id"),
            "academic_period": (
                f"{row.get('year_label')}:{row.get('cycle')}"
                if row.get("year_label") is not None
                else None
            ),
            "import_job_id": row.get("import_job_id"),
            "document_key": document,
            "source_section": row.get("source_section") or (
                metadata_sections[0] if len(metadata_sections) == 1 else None
            ),
            "source_page": row.get("source_page"),
            "row_or_block_id": consumed_locator,
            "person_key": row.get("person_key"),
            "canonical_identity_key": row.get("canonical_identity_key"),
            "canonical_name_present": _presence(row.get("canonical_name")),
            "raw_name_present": _presence(row.get("raw_name")),
            "normalized_name_present": _presence(row.get("normalized_name")),
            "role_type": row.get("role_type"),
            "career_id": row.get("career_id"),
            "parser_version": row.get("parser_version"),
            "identity_group_key": _short_hash(identity_value) or "missing",
        }
        related = {
            "metadata_locator_paths": metadata_paths,
            "ocr_locator_paths": tuple(sorted(set(stable_ocr_paths))),
            "source_record_matches": source_matches,
            "name_only_source_record_matches": name_only_source_matches,
            "conflicting_identity_fields": bool(
                row.get("person_key")
                and row.get("canonical_identity_key")
                and str(row.get("canonical_identity_key")).startswith("person:")
                and row.get("person_key") != row.get("canonical_identity_key")
            ),
        }
        diagnostic = diagnose_person_role(role_input, related)
        diagnostic["career_name_normalized"] = _normalized(row.get("career_name"))
        diagnostic["related_source_ids"] = {
            "teacher_id": row.get("teacher_id"),
            "research_project_id": row.get("research_project_id"),
            "research_entity_id": row.get("research_entity_id"),
            "scientific_production_id": row.get("scientific_production_id"),
            "external_researcher_id": row.get("external_researcher_id"),
        }
        diagnostics.append(diagnostic)
    return diagnostics


def _director_diagnostics(connection: Connection, blocker_ids: Sequence[int]) -> list[dict[str, object]]:
    entities = [dict(row) for row in _query(connection, """
        SELECT entity.id, entity.period_id, period.year_label, period.cycle,
               entity.type, entity.code, entity.normalized_code,
               entity.name, entity.normalized_name,
               entity.director_name, entity.normalized_director_name,
               entity.source_file, entity.source_section, entity.source_page,
               entity.import_job_id, import_job.document_key,
               import_job.filename, import_job.source_rev
        FROM research_entities AS entity
        LEFT JOIN academic_periods AS period ON period.id = entity.period_id
        LEFT JOIN import_jobs AS import_job ON import_job.id = entity.import_job_id
        WHERE entity.id = ANY(:ids)
        ORDER BY entity.id
    """, {"ids": list(blocker_ids)}).mappings()]
    _require(len(entities) == 7, "director-relation blocker rows differ from 7")
    projects = [dict(row) for row in _query(connection, """
        SELECT project.id, project.period_id, project.raw_code AS code,
               project.normalized_code, project.raw_project_name AS name,
               project.normalized_project_name AS normalized_name,
               project.source_file, project.source_section, project.source_page,
               project.import_job_id, import_job.document_key,
               import_job.filename, import_job.source_rev
        FROM research_projects AS project
        LEFT JOIN import_jobs AS import_job ON import_job.id = project.import_job_id
        ORDER BY project.id
    """).mappings()]
    normalized_projects = []
    for project in projects:
        project["document_key"] = (
            project.get("document_key") or project.get("filename") or project.get("source_file")
        )
        normalized_projects.append(project)
    diagnostics = []
    for entity in entities:
        entity["document_key"] = (
            entity.get("document_key") or entity.get("filename") or entity.get("source_file")
        )
        entity["academic_period"] = (
            f"{entity.get('year_label')}:{entity.get('cycle')}"
            if entity.get("year_label") is not None
            else None
        )
        diagnostics.append(correlate_director_relation(entity, normalized_projects))
    return diagnostics


def _remediation_markdown(cases: Sequence[Mapping[str, object]]) -> str:
    by_option: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for case in cases:
        by_option[str(case.get("recommended_option"))].append(case)
    lines = [
        "# Task 13A — Alternativas de remediación",
        "",
        "Ninguna alternativa está autorizada para ejecución durante Task 13A.",
        "",
    ]
    definitions = (
        {
            "option": "A",
            "title": "Corrección controlada de datos fuente",
            "default_components": "ninguno en la evidencia actual",
            "impact": (
                "- Modifica datos científicos: sí, pero la evidencia actual respalda 0 correcciones.\n"
                "- Modifica lógica cerrada de Task 12: no.\n"
                "- Nueva migración: no; una corrección futura usaría el mecanismo de trazabilidad de fuente expresamente autorizado.\n"
                "- Snapshots del plan: sí solo si una corrección futura cambia la fuente; impacto actual: 0.\n"
                "- Fórmula de stable keys: no; una fuente corregida podría cambiar el valor de una clave sin cambiar su fórmula.\n"
                "- Contrato de auditoría: no; cualquier corrección futura necesitaría su propia traza de fuente.\n"
                "- Mecanismo de idempotencia: no; el dry-run y la segunda ejecución tendrían que revalidarse.\n"
                "- KPI: no debe cambiar; gate obligatorio 2 → 2.\n"
                "- Pruebas requeridas: evidencia documental unívoca, dry-run, invariantes de fuente/raw, PostgreSQL real, idempotencia y rollback.\n"
                "- Riesgo de regresión: alto mientras no exista evidencia única; alternativa no recomendada para estos 26 casos."
            ),
        },
        {
            "option": "B",
            "title": "Reapertura controlada de Task 12",
            "default_components": "regla exacta indicada por los casos",
            "impact": (
                "- Modifica datos científicos: no.\n"
                "- Modifica lógica cerrada de Task 12: sí, solo la evidencia de proyecto y la selección de población documentadas.\n"
                "- Nueva migración: no; no se identificó cambio de esquema.\n"
                "- Snapshots del plan: sí, incorporaría snapshots para 5 relaciones hoy bloqueadas; los existentes deben permanecer byte a byte.\n"
                "- Fórmula de stable keys: no; crearía targets con la fórmula vigente.\n"
                "- Contrato de auditoría: no; un apply futuro usaría la cadena append-only vigente.\n"
                "- Mecanismo de idempotencia: no; debe revalidarse que el nuevo resultado sea determinista y la segunda ejecución sea cero.\n"
                "- KPI: no debe cambiar; gate obligatorio 2 → 2.\n"
                "- Pruebas requeridas: RED/GREEN de evidencia propia de `proyecto_fci`, negativos de período/tipo, focal completa de Task 12, PostgreSQL real, snapshots, stable keys, auditoría, concurrencia, idempotencia y rollback.\n"
                "- Riesgo de regresión: alto; una regla amplia podría asociar proyectos sin evidencia compatible."
            ),
        },
        {
            "option": "C",
            "title": "Materializar casos pendientes sin decisión automática",
            "default_components": "review_items pendientes; sin review_decisions",
            "impact": (
                "- Materializables ahora: 0; los 19 carecen de un stable locator válido.\n"
                "- Modifica datos científicos: no.\n"
                "- Modifica lógica cerrada de Task 12: no como alternativa aislada; primero debe existir un locator autorizado.\n"
                "- Nueva migración: no; el esquema fundacional ya admite casos pendientes.\n"
                "- Snapshots del plan: no ahora; tras aportar locators válidos se crearían 19 snapshots nuevos.\n"
                "- Fórmula de stable keys: no; está prohibido sustituir el locator por nombre, PK, ctid u orden físico.\n"
                "- Contrato de auditoría: no; no se crea decisión ni evento durante Task 13A.\n"
                "- Mecanismo de idempotencia: no; la futura unicidad depende de locators estables y debe revalidarse.\n"
                "- KPI: no cambia porque no se crea decisión; gate obligatorio 2 → 2.\n"
                "- Pruebas requeridas: validez/unicidad del locator, stable key exacta, cero decisiones, caso pendiente, segunda ejecución cero, auditoría y KPI.\n"
                "- Riesgo de regresión: alto mientras falte el locator; materializar ahora provocaría claves inestables o duplicados."
            ),
        },
        {
            "option": "D",
            "title": "Excluir registros fuera de población",
            "default_components": "regla de selección de población",
            "impact": (
                "- Modifica datos científicos: no.\n"
                "- Modifica lógica cerrada de Task 12: sí, limita la población a entidades que representan proyectos.\n"
                "- Nueva migración: no; no se identificó cambio de esquema.\n"
                "- Snapshots del plan: no elimina snapshots existentes; retira 2 blockers que nunca produjeron snapshot.\n"
                "- Fórmula de stable keys: no; esos 2 blockers nunca produjeron stable key.\n"
                "- Contrato de auditoría: no; no existe decisión que corregir.\n"
                "- Mecanismo de idempotencia: no; debe revalidarse la determinación exacta de la población.\n"
                "- KPI: no debe cambiar; gate obligatorio 2 → 2.\n"
                "- Pruebas requeridas: inclusión exacta de tipos de proyecto, exclusión exacta de grupo/semillero, conteos de plan, PostgreSQL real, idempotencia y rollback.\n"
                "- Riesgo de regresión: alto si la exclusión se generaliza a tipos no aprobados; debe cerrarse por enum/tipo exacto."
            ),
        },
    )
    for definition in definitions:
        option = str(definition["option"])
        title = str(definition["title"])
        default_components = str(definition["default_components"])
        option_cases = by_option.get(option, [])
        case_keys = sorted(str(case["case_key"]) for case in option_cases)
        affected_components = sorted({
            str(component)
            for case in option_cases
            for component in case.get("affected_components", [])
        })
        components = ", ".join(affected_components) or default_components
        if option == "C":
            case_summary = f"- Casos condicionados: {len(case_keys)} ({', '.join(case_keys) if case_keys else 'ninguno'})."
        else:
            case_summary = f"- Blockers que resolvería: {len(case_keys)} ({', '.join(case_keys) if case_keys else 'ninguno'})."
        lines.extend([
            f"## OPCIÓN {option} — {title}",
            "",
            case_summary,
            f"- Archivos o tablas afectados: {components}.",
            str(definition["impact"]),
            "",
        ])
    lines.extend([
        "## Recomendación",
        "",
        "La evidencia actual no respalda correcciones de datos fuente. Reabrir Task 12 solo para DERIVATION_GAP o CONTRACT_MISMATCH demostrado; mantener LEGITIMATE_AMBIGUITY y EXPECTED_PENDING_REVIEW para decisión humana, sin materializarlos hasta disponer de un stable locator válido; excluir OUT_OF_SCOPE_RECORD solo tras aprobar formalmente la semántica de población. No efectuar correcciones masivas para forzar blockers cero.",
        "",
    ])
    return "\n".join(lines)


def _write_reports(
    report_dir: Path,
    person_cases: list[dict[str, object]],
    director_cases: list[dict[str, object]],
    hash_proof: Mapping[str, object],
    before_hash: str,
    after_hash: str,
    before_counts: Mapping[str, int],
    after_counts: Mapping[str, int],
) -> None:
    cases = person_cases + director_cases
    validate_complete_case_set(cases)
    category_counts = dict(sorted(Counter(str(case["classification"]) for case in cases).items()))
    reason_counts = dict(sorted(Counter(str(case["reason_code"]) for case in cases).items()))
    groups = group_person_role_diagnostics(person_cases)
    locator_coverage = {
        **groups,
        "field_presence_counts": {
            field: sum(
                1 for case in person_cases if case.get("field_presence", {}).get(field)
            )
            for field in sorted({
                field
                for case in person_cases
                for field in case.get("field_presence", {})
            })
        },
        "reason_counts": dict(sorted(Counter(str(case["reason_code"]) for case in person_cases).items())),
        "classification_counts": dict(sorted(Counter(str(case["classification"]) for case in person_cases).items())),
        "resolvable_with_existing_data": sum(1 for case in person_cases if case["classification"] == "DERIVATION_GAP"),
        "requires_source_change": sum(1 for case in person_cases if case["classification"] == "SOURCE_DATA_DEFECT"),
        "requires_logic_change": sum(1 for case in person_cases if case["classification"] in {"DERIVATION_GAP", "CONTRACT_MISMATCH"}),
        "requires_human_review": sum(1 for case in person_cases if case["human_decision_required"]),
    }
    summary = {
        "status": "PASS",
        "candidate_count": 74,
        "stable_target_count": 67,
        "blocker_count": 26,
        "blocker_distribution": {
            "person_roles.missing_stable_locator": 19,
            "research_entities.invariant_mismatch": 7,
        },
        "classification_counts": category_counts,
        "reason_counts": reason_counts,
        "hashes": dict(hash_proof),
        "read_only": {
            "transaction_read_only": True,
            "database_fingerprint_before": before_hash,
            "database_fingerprint_after": after_hash,
            "database_unchanged": before_hash == after_hash,
            "b2b_counts_before": {table: before_counts[table] for table in _B2B_TABLES},
            "b2b_counts_after": {table: after_counts[table] for table in _B2B_TABLES},
            "apply_executed": False,
        },
    }
    write_json(report_dir / "blocker-summary.json", summary)
    _write_csv(report_dir / "blocker-summary.csv", cases)
    write_json(report_dir / "person-role-blockers.json", person_cases)
    _write_csv(report_dir / "person-role-blockers.csv", person_cases)
    write_json(report_dir / "director-relation-blockers.json", director_cases)
    _write_csv(report_dir / "director-relation-blockers.csv", director_cases)
    write_json(report_dir / "locator-field-coverage.json", locator_coverage)
    write_json(
        report_dir / "project-correlation-matrix.json",
        [
            {
                "case_key": case["case_key"],
                "criterion_match_counts": case["criterion_match_counts"],
                "matrix": case["matrix"],
            }
            for case in director_cases
        ],
    )
    write_json(report_dir / "plan-semantic-diff.json", dict(hash_proof))
    (report_dir / "proposed-remediation.md").write_text(
        _remediation_markdown(cases),
        encoding="utf-8",
    )


def run_diagnosis(args: argparse.Namespace) -> None:
    historical_path = Path(args.historical_plan).resolve()
    reproduced_path = Path(args.reproduced_plan).resolve()
    report_dir = Path(args.report_dir).resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    historical_proof = verify_historical_artifact(historical_path)
    try:
        reproduced = B2B1BackfillPlanV1.model_validate_json(reproduced_path.read_bytes())
    except Exception as error:
        raise DiagnosticError("reproduced plan artifact is invalid") from error
    historical = historical_proof["plan"]
    _require(isinstance(historical, B2B1BackfillPlanV1), "historical plan model is missing")
    semantic_proof = verify_semantic_reproduction(historical, reproduced, reproduced_path)
    blocker_counts = Counter(blocker.code for blocker in reproduced.blockers)
    table_code_counts = Counter(
        (blocker.source_table.value, blocker.code) for blocker in reproduced.blockers
    )
    verify_reproduction(
        candidate_count=len(reproduced.candidates),
        stable_target_count=reproduced.union_stable_target_count,
        blocker_counts=blocker_counts,
        plan_sha256=productive_plan_sha256(reproduced),
    )
    _require(
        table_code_counts == Counter({
            ("person_roles", "missing_stable_locator"): 19,
            ("research_entities", "invariant_mismatch"): 7,
        }),
        "blocker source-table distribution differs",
    )
    database_url = os.environ.get(args.database_url_env, "").strip()
    _require(bool(database_url), f"environment variable {args.database_url_env!r} is required")
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            event.listen(connection, "before_cursor_execute", _read_only_guard)
            try:
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                before_hash, before_counts = _database_fingerprint(connection)
                person_ids = [
                    blocker.source_id
                    for blocker in reproduced.blockers
                    if blocker.source_table.value == "person_roles"
                ]
                entity_ids = [
                    blocker.source_id
                    for blocker in reproduced.blockers
                    if blocker.source_table.value == "research_entities"
                ]
                person_cases = _person_role_diagnostics(connection, person_ids)
                director_cases = _director_diagnostics(connection, entity_ids)
                after_hash, after_counts = _database_fingerprint(connection)
                verify_database_unchanged(before_hash, after_hash)
                _require(before_counts == after_counts, "database row counts changed")
                hash_proof = {
                    "historical_artifact_sha256": historical_proof["historical_artifact_sha256"],
                    "historical_semantic_plan_sha256": historical_proof["historical_semantic_plan_sha256"],
                    **semantic_proof,
                }
                _write_reports(
                    report_dir,
                    person_cases,
                    director_cases,
                    hash_proof,
                    before_hash,
                    after_hash,
                    before_counts,
                    after_counts,
                )
            finally:
                transaction.rollback()
                event.remove(connection, "before_cursor_execute", _read_only_guard)
    finally:
        engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Diagnose the approved B2B.1 backfill blockers without mutations."
    )
    parser.add_argument("--database-url-env", required=True)
    parser.add_argument("--historical-plan", required=True)
    parser.add_argument("--reproduced-plan", required=True)
    parser.add_argument("--report-dir", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        run_diagnosis(args)
    except (DiagnosticError, SQLAlchemyError, OSError) as error:
        parser.error(str(error))
    print("STATUS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
