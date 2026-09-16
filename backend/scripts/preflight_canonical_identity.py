from __future__ import annotations

import argparse
import csv
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from difflib import SequenceMatcher
import json
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import create_engine, text

from app.core.config import settings
from app.services.canonical_identity import (
    CanonicalIdentityDecision,
    CanonicalIdentityResolver,
    IdentityCandidate,
    IdentityEvidence,
)
from app.services.investigator_seed import (
    DEFAULT_SEED_PATH,
    InvestigatorSeedService,
    normalize_key,
    normalize_name_key,
)


PROPOSED_COLUMNS = (
    "canonical_identity_key",
    "canonical_name",
    "identity_source",
    "identity_confidence",
    "identity_reason",
    "identity_locked",
    "identity_decided_by",
    "identity_decided_at",
)
AUDITED_SURNAMES = ("Delgado", "Zambrano", "Sanchez", "Ramirez", "Merchan")
DEFAULT_AUDITED_PEOPLE = (
    "Carlos Parrales Choez",
    "Maria del Carmen Valls Martinez",
    "Maria Estefania Sanchez Pacheco",
    "Jose Manuel Santos Jaen",
    "Fernando Zambrano Farias",
)
DOCUMENT_LINK_OCR_TOKEN_THRESHOLD = 0.84

DATASET_QUERIES = {
    "roles": """
        SELECT to_jsonb(pr) || jsonb_build_object('effective_current_job_id', role_job.id) AS row_data
        FROM person_roles pr
        JOIN import_jobs role_job ON role_job.id = pr.import_job_id
        WHERE role_job.status = 'SUCCESS' AND role_job.is_current IS TRUE
        ORDER BY pr.id
    """,
    "authors": """
        WITH current_product_evidence AS (
            SELECT p.id AS production_id,
                   max(CASE WHEN direct_job.is_current IS TRUE AND direct_job.status = 'SUCCESS'
                            THEN direct_job.id END) AS direct_current_job_id,
                   min(audit_job.id) AS audit_evidence_job_id,
                   coalesce(
                       max(CASE WHEN direct_job.is_current IS TRUE AND direct_job.status = 'SUCCESS'
                                THEN direct_job.id END),
                       min(audit_job.id)
                   ) AS effective_current_job_id
            FROM scientific_productions p
            LEFT JOIN import_jobs direct_job ON direct_job.id = p.import_job_id
            LEFT JOIN import_normalization_audits audit
              ON audit.normalized_record_id = p.id
             AND audit.entity_type = 'scientific_production'
             AND audit.source_section = 'produccion_cientifica'
            LEFT JOIN import_jobs audit_job
              ON audit_job.id = audit.import_job_id
             AND audit_job.status = 'SUCCESS'
             AND audit_job.is_current IS TRUE
            GROUP BY p.id
            HAVING p.import_job_id IS NULL
                OR max(CASE WHEN direct_job.is_current IS TRUE AND direct_job.status = 'SUCCESS' THEN 1 ELSE 0 END) = 1
                OR count(audit_job.id) > 0
        )
        SELECT to_jsonb(spa) || jsonb_build_object(
                   'effective_current_job_id', evidence.effective_current_job_id,
                   'audit_evidence_job_id', evidence.audit_evidence_job_id,
                   'current_evidence_source',
                       CASE WHEN evidence.direct_current_job_id IS NOT NULL
                            THEN 'direct_current_product'
                            WHEN evidence.audit_evidence_job_id IS NOT NULL
                            THEN 'current_normalization_audit' ELSE 'unscoped_master' END
               ) AS row_data
        FROM scientific_production_authors spa
        JOIN current_product_evidence evidence ON evidence.production_id = spa.production_id
        ORDER BY spa.id
    """,
    "teachers": "SELECT to_jsonb(t) AS row_data FROM teachers t ORDER BY t.id",
    "externals": "SELECT to_jsonb(e) AS row_data FROM external_researchers e ORDER BY e.id",
    "productions": """
        WITH current_product_evidence AS (
            SELECT p.id AS production_id,
                   max(CASE WHEN direct_job.is_current IS TRUE AND direct_job.status = 'SUCCESS'
                            THEN direct_job.id END) AS direct_current_job_id,
                   min(audit_job.id) AS audit_evidence_job_id,
                   coalesce(
                       max(CASE WHEN direct_job.is_current IS TRUE AND direct_job.status = 'SUCCESS'
                                THEN direct_job.id END),
                       min(audit_job.id)
                   ) AS effective_current_job_id
            FROM scientific_productions p
            LEFT JOIN import_jobs direct_job ON direct_job.id = p.import_job_id
            LEFT JOIN import_normalization_audits audit
              ON audit.normalized_record_id = p.id
             AND audit.entity_type = 'scientific_production'
             AND audit.source_section = 'produccion_cientifica'
            LEFT JOIN import_jobs audit_job
              ON audit_job.id = audit.import_job_id
             AND audit_job.status = 'SUCCESS'
             AND audit_job.is_current IS TRUE
            GROUP BY p.id
            HAVING p.import_job_id IS NULL
                OR max(CASE WHEN direct_job.is_current IS TRUE AND direct_job.status = 'SUCCESS' THEN 1 ELSE 0 END) = 1
                OR count(audit_job.id) > 0
        )
        SELECT to_jsonb(p) || jsonb_build_object(
                   'effective_current_job_id', evidence.effective_current_job_id,
                   'audit_evidence_job_id', evidence.audit_evidence_job_id,
                   'current_evidence_source',
                       CASE WHEN evidence.direct_current_job_id IS NOT NULL
                            THEN 'direct_current_product'
                            WHEN evidence.audit_evidence_job_id IS NOT NULL
                            THEN 'current_normalization_audit' ELSE 'unscoped_master' END
               ) AS row_data
        FROM scientific_productions p
        JOIN current_product_evidence evidence ON evidence.production_id = p.id
        ORDER BY p.id
    """,
    "audits": """
        SELECT to_jsonb(audit) || jsonb_build_object('effective_current_job_id', audit_job.id) AS row_data
        FROM import_normalization_audits audit
        JOIN import_jobs audit_job ON audit_job.id = audit.import_job_id
        WHERE audit_job.status = 'SUCCESS'
          AND audit_job.is_current IS TRUE
          AND audit.entity_type = 'scientific_production'
          AND audit.source_section = 'produccion_cientifica'
        ORDER BY audit.id
    """,
}

SNAPSHOT_QUERIES = {
    "roles": """
        SELECT count(*)::bigint AS count,
               md5(coalesce(string_agg(
                   concat_ws('|', id, raw_name, person_key, import_job_id), E'\\n' ORDER BY id
               ), '')) AS hash
        FROM person_roles pr
    """,
    "authors": """
        SELECT count(*)::bigint AS count,
               md5(coalesce(string_agg(
                   concat_ws('|', id, raw_author_name, import_job_id, production_id), E'\\n' ORDER BY id
               ), '')) AS hash
        FROM scientific_production_authors spa
    """,
    "traces": """
        SELECT count(*)::bigint AS count,
               md5(coalesce(string_agg(
                   concat_ws('|', id, import_job_id, extracted_text, parsed_payload::text), E'\\n' ORDER BY id
               ), '')) AS hash
        FROM imported_ocr_traces t
    """,
    "role_records_full": """
        SELECT count(*)::bigint AS count,
               md5(coalesce(string_agg(to_jsonb(pr)::text, '|' ORDER BY pr.id), '')) AS hash
        FROM person_roles pr
    """,
    "author_records_full": """
        SELECT count(*)::bigint AS count,
               md5(coalesce(string_agg(to_jsonb(spa)::text, '|' ORDER BY spa.id), '')) AS hash
        FROM scientific_production_authors spa
    """,
    "trace_records_full": """
        SELECT count(*)::bigint AS count,
               md5(coalesce(string_agg(to_jsonb(t)::text, '|' ORDER BY t.id), '')) AS hash
        FROM imported_ocr_traces t
    """,
    "raw_names": """
        SELECT count(*)::bigint AS count,
               md5(coalesce(string_agg(item, '|' ORDER BY source_table, row_id), '')) AS hash
        FROM (
            SELECT 'person_roles' AS source_table, id AS row_id, coalesce(raw_name, '') AS item
            FROM person_roles
            UNION ALL
            SELECT 'scientific_production_authors', id, coalesce(raw_author_name, '')
            FROM scientific_production_authors
        ) names
    """,
    "original_person_keys": """
        SELECT count(*)::bigint AS count,
               md5(coalesce(string_agg(coalesce(person_key, ''), '|' ORDER BY id), '')) AS hash
        FROM person_roles
    """,
    "import_jobs": """
        SELECT count(*)::bigint AS count,
               md5(coalesce(string_agg(to_jsonb(j)::text, '|' ORDER BY j.id), '')) AS hash
        FROM import_jobs j
    """,
    "current_success_jobs": """
        SELECT count(*)::bigint AS count,
               md5(coalesce(string_agg(id::text, '|' ORDER BY id), '')) AS hash
        FROM import_jobs
        WHERE status = 'SUCCESS' AND is_current IS TRUE
    """,
    "parsed_payloads": """
        SELECT count(*) FILTER (WHERE parsed_payload IS NOT NULL)::bigint AS count,
               md5(coalesce(string_agg(coalesce(parsed_payload::text, ''), '|' ORDER BY id), '')) AS hash
        FROM imported_ocr_traces
    """,
    "evidence": """
        SELECT count(*)::bigint AS count,
               md5(coalesce(string_agg(item, '|' ORDER BY source_table, row_id), '')) AS hash
        FROM (
            SELECT 'scientific_productions' AS source_table, id AS row_id,
                   jsonb_build_object('evidence_url', evidence_url, 'source_file', source_file,
                                      'source_page', source_page, 'raw_value', raw_value)::text AS item
            FROM scientific_productions
            UNION ALL
            SELECT 'import_review_items', id, to_jsonb(r)::text FROM import_review_items r
            UNION ALL
            SELECT 'import_normalization_audits', id, to_jsonb(a)::text FROM import_normalization_audits a
        ) evidence_rows
    """,
}


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only canonical identity preflight.")
    parser.add_argument(
        "--output-dir",
        default="reports/canonical_identity_preflight_20260711",
        help="Directory for generated read-only simulation artifacts.",
    )
    parser.add_argument("--database-url", default=settings.database_url)
    parser.add_argument("--seed-file", default=str(DEFAULT_SEED_PATH))
    return parser


def enforce_postgres_read_only(connection: Any) -> None:
    connection.exec_driver_sql("SET TRANSACTION READ ONLY")


def assert_snapshot_unchanged(before: dict[str, Any], after: dict[str, Any]) -> None:
    if before != after:
        changed = sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key))
        raise RuntimeError(f"database changed during read-only preflight: {', '.join(changed)}")


def snapshot_database(connection: Any) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for label, query in SNAPSHOT_QUERIES.items():
        row = connection.execute(text(query)).mappings().one()
        snapshot[label] = {"count": int(row["count"]), "hash": row["hash"]}
    schema_rows = connection.execute(
        text(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name IN ('person_roles', 'scientific_production_authors')
              AND column_name IN (
                  'canonical_identity_key', 'canonical_name', 'identity_source',
                  'identity_confidence', 'identity_reason', 'identity_locked',
                  'identity_decided_by', 'identity_decided_at'
              )
            ORDER BY table_name, column_name
            """
        )
    ).mappings().all()
    snapshot["canonical_columns"] = [dict(row) for row in schema_rows]
    snapshot["migration_table"] = connection.execute(
        text("SELECT coalesce(to_regclass('public.alembic_version')::text, '')")
    ).scalar_one()
    return snapshot


def load_dataset(connection: Any) -> dict[str, Any]:
    def json_rows(query: str) -> list[dict[str, Any]]:
        return [dict(row["row_data"]) for row in connection.execute(text(query)).mappings().all()]

    return {
        "roles": json_rows(DATASET_QUERIES["roles"]),
        "authors": json_rows(DATASET_QUERIES["authors"]),
        "teachers": json_rows(DATASET_QUERIES["teachers"]),
        "externals": json_rows(DATASET_QUERIES["externals"]),
        "productions": json_rows(DATASET_QUERIES["productions"]),
        "audits": json_rows(DATASET_QUERIES["audits"]),
        "audited_people": list(DEFAULT_AUDITED_PEOPLE),
    }


def operational_product_population(
    *,
    jobs: list[dict[str, Any]],
    productions: list[dict[str, Any]],
    authors: list[dict[str, Any]],
    audits: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Executable reference for ValidatedReadService current/audit population semantics."""
    current_job_ids = {
        row.get("id")
        for row in jobs
        if row.get("is_current") is True and row.get("status") == "SUCCESS"
    }
    audited_jobs: dict[Any, list[Any]] = {}
    for audit in audits:
        if (
            audit.get("entity_type") == "scientific_production"
            and audit.get("source_section") == "produccion_cientifica"
            and audit.get("normalized_record_id") is not None
            and audit.get("import_job_id") in current_job_ids
        ):
            audited_jobs.setdefault(audit["normalized_record_id"], []).append(audit["import_job_id"])

    selected_productions: list[dict[str, Any]] = []
    selected_ids: set[Any] = set()
    for production in productions:
        own_job_id = production.get("import_job_id")
        audit_job_ids = sorted(set(audited_jobs.get(production.get("id"), [])))
        if own_job_id is not None and own_job_id not in current_job_ids and not audit_job_ids:
            continue
        output = deepcopy(production)
        output["effective_current_job_id"] = (
            own_job_id if own_job_id in current_job_ids else (audit_job_ids[0] if audit_job_ids else None)
        )
        output["audit_evidence_job_id"] = audit_job_ids[0] if audit_job_ids else None
        output["current_evidence_source"] = (
            "direct_current_product"
            if own_job_id in current_job_ids
            else "current_normalization_audit"
            if audit_job_ids
            else "unscoped_master"
        )
        selected_productions.append(output)
        selected_ids.add(production.get("id"))

    evidence_by_product = {row.get("id"): row for row in selected_productions}
    selected_authors: list[dict[str, Any]] = []
    for author in authors:
        evidence = evidence_by_product.get(author.get("production_id"))
        if evidence is None:
            continue
        output = deepcopy(author)
        for field in ("effective_current_job_id", "audit_evidence_job_id", "current_evidence_source"):
            output[field] = evidence.get(field)
        selected_authors.append(output)
    return selected_productions, selected_authors


def _source_name(entry: dict[str, Any]) -> str:
    row = entry["row"]
    if entry["source_table"] == "person_roles":
        return str(row.get("normalized_name") or row.get("raw_name") or row.get("person_key") or "").strip()
    return str(row.get("normalized_author_name") or row.get("raw_author_name") or "").strip()


def _raw_source_name(entry: dict[str, Any]) -> str:
    row = entry["row"]
    value = row.get("raw_name") if entry["source_table"] == "person_roles" else row.get("raw_author_name")
    return str(value or _source_name(entry)).strip()


def _tokens(value: Any) -> set[str]:
    return set(normalize_name_key(value).split())


def _names_equivalent(left: Any, right: Any) -> bool:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return False
    if left_tokens == right_tokens:
        return True
    shorter, longer = (left_tokens, right_tokens) if len(left_tokens) <= len(right_tokens) else (right_tokens, left_tokens)
    return len(shorter) >= 2 and shorter.issubset(longer)


def _locked_decision(row: dict[str, Any]) -> CanonicalIdentityDecision | None:
    if not row.get("identity_locked") or not row.get("canonical_identity_key"):
        return None
    return CanonicalIdentityDecision(
        key=str(row["canonical_identity_key"]),
        canonical_name=str(row.get("canonical_name") or ""),
        source=str(row.get("identity_source") or "manual"),
        confidence=float(row.get("identity_confidence") or 1.0),
        reason=str(row.get("identity_reason") or "Locked manual decision."),
        status="resolved",
        candidate_count=0,
        supporting_signals=("manual_lock",),
    )


def _effective_job_id(row: dict[str, Any]) -> Any:
    return row.get("effective_current_job_id") or row.get("import_job_id")


def _row_or_block_id(row: dict[str, Any]) -> str | None:
    direct = row.get("row_or_block_id")
    if direct:
        return str(direct)
    metadata = row.get("metadata_json")
    if isinstance(metadata, dict) and metadata.get("row_or_block_id"):
        return str(metadata["row_or_block_id"])
    return None


def _entry_key(entry: dict[str, Any]) -> str:
    return f"{entry['source_table']}:{entry['row'].get('id')}"


def _audit_evidence_job_id(row: dict[str, Any]) -> Any:
    if row.get("audit_evidence_job_id") is not None:
        return row["audit_evidence_job_id"]
    metadata = row.get("metadata_json")
    if isinstance(metadata, dict):
        return metadata.get("audit_evidence_job_id")
    return None


def _build_evidence_registry(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parents = list(range(len(entries)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[max(left_root, right_root)] = min(left_root, right_root)

    relation_sets: list[set[str]] = [
        (
            {f"audit_evidence_job_id:{_audit_evidence_job_id(entry['row'])}"}
            if _audit_evidence_job_id(entry["row"]) is not None
            else set()
        )
        for entry in entries
    ]
    for left_index, left in enumerate(entries):
        for right_index in range(left_index + 1, len(entries)):
            right = entries[right_index]
            if left["source_table"] == right["source_table"]:
                continue
            if not _names_equivalent(_source_name(left), _source_name(right)):
                continue
            role = left if left["source_table"] == "person_roles" else right
            author = right if right["source_table"] == "scientific_production_authors" else left
            role_row, author_row = role["row"], author["row"]
            relations: list[str] = []
            production_id = role_row.get("scientific_production_id")
            if production_id is not None and production_id == author_row.get("production_id"):
                relations.append(f"production_id:{production_id}")
            row_block = _row_or_block_id(role_row)
            if row_block and row_block == _row_or_block_id(author_row):
                relations.append(f"row_or_block_id:{row_block}")
            effective_job = _effective_job_id(role_row)
            if effective_job is not None and effective_job == _effective_job_id(author_row):
                relations.append(f"effective_current_job_id:{effective_job}")
            if not relations:
                continue
            union(left_index, right_index)
            relation_sets[left_index].update(relations)
            relation_sets[right_index].update(relations)

    groups: dict[int, list[int]] = {}
    for index in range(len(entries)):
        groups.setdefault(find(index), []).append(index)
    registry: list[dict[str, Any]] = []
    for member_indexes in groups.values():
        members = [entries[index] for index in member_indexes]
        stable_member = min(_entry_key(member) for member in members)
        effective_jobs = sorted(
            {str(_effective_job_id(member["row"])) for member in members if _effective_job_id(member["row"]) is not None}
        )
        registry.append(
            {
                "id": f"registry:{effective_jobs[0] if effective_jobs else 'missing'}:{stable_member}",
                "members": members,
                "relations": sorted({relation for index in member_indexes for relation in relation_sets[index]}),
                "effective_job_id": effective_jobs[0] if effective_jobs else None,
            }
        )
    return sorted(registry, key=lambda item: item["id"])


def _linked_document_identity(
    entry: dict[str, Any],
    peers: list[dict[str, Any]],
    teachers: dict[Any, dict[str, Any]],
    externals: dict[Any, dict[str, Any]],
    candidates: tuple[IdentityCandidate, ...] = (),
) -> tuple[Any, str | None, Any, str | None]:
    row = entry["row"]
    teacher_id = row.get("teacher_id")
    external_id = row.get("external_researcher_id")
    if teacher_id is not None:
        teacher = teachers.get(teacher_id, {})
        return teacher_id, teacher.get("full_name") or _source_name(entry), None, None
    if external_id is not None:
        external = externals.get(external_id, {})
        return None, None, external_id, external.get("full_name") or _source_name(entry)

    if len(candidates) != 1:
        return None, None, None, None
    candidate = candidates[0]
    if not _source_matches_candidate(entry, candidate):
        return None, None, None, None
    candidate_names = {
        normalize_name_key(value)
        for value in (candidate.canonical_name, *candidate.aliases)
        if normalize_name_key(value)
    }
    candidate_identifier = normalize_key(candidate.identity_number)

    def peer_matches_candidate(peer: dict[str, Any], entity: dict[str, Any]) -> bool:
        names = {
            normalize_name_key(value)
            for value in (
                _source_name(peer),
                entity.get("full_name"),
                entity.get("normalized_name"),
                entity.get("raw_name"),
            )
            if normalize_name_key(value)
        }
        if candidate_names & names:
            return True
        if not candidate_identifier:
            return False
        identifiers = {
            normalize_key(value)
            for value in (
                peer["row"].get("institutional_identifier"),
                peer["row"].get("identity_number"),
                peer["row"].get("national_id"),
                entity.get("institutional_identifier"),
                entity.get("identity_number"),
                entity.get("national_id"),
            )
            if normalize_key(value)
        }
        return candidate_identifier in identifiers

    linked: list[tuple[int, Any, str]] = []
    for peer in peers:
        peer_row = peer["row"]
        peer_teacher_id = peer_row.get("teacher_id")
        peer_external_id = peer_row.get("external_researcher_id")
        if peer_teacher_id is not None:
            teacher = teachers.get(peer_teacher_id, {})
            name = str(teacher.get("full_name") or _source_name(peer))
            if peer_matches_candidate(peer, teacher):
                linked.append((0, peer_teacher_id, name))
        if peer_external_id is not None:
            external = externals.get(peer_external_id, {})
            name = str(external.get("full_name") or _source_name(peer))
            if peer_matches_candidate(peer, external):
                linked.append((1, peer_external_id, name))
    unique = {(kind, identifier, name) for kind, identifier, name in linked}
    if len(unique) != 1:
        return None, None, None, None
    kind, identifier, name = next(iter(unique))
    return (identifier, name, None, None) if kind == 0 else (None, None, identifier, name)


def _source_matches_candidate(entry: dict[str, Any], candidate: IdentityCandidate) -> bool:
    row = entry["row"]
    candidate_identifier = normalize_key(candidate.identity_number)
    if candidate_identifier:
        source_identifiers = {
            normalize_key(value)
            for value in (
                row.get("institutional_identifier"),
                row.get("trusted_institutional_identifier"),
                row.get("identity_number"),
                row.get("national_id"),
            )
            if normalize_key(value)
        }
        if candidate_identifier in source_identifiers:
            return True

    source_tokens = normalize_name_key(_source_name(entry)).split()
    if len(source_tokens) < 2:
        return False
    candidate_names = tuple(
        dict.fromkeys(
            normalize_name_key(value)
            for value in (candidate.canonical_name, *candidate.aliases)
            if normalize_name_key(value)
        )
    )
    source_token_set = set(source_tokens)
    if any(source_token_set.issubset(set(name.split())) for name in candidate_names):
        return True

    # OCR inheritance requires every source token to align one-to-one at 0.84 or better.
    for candidate_name in candidate_names:
        candidate_tokens = candidate_name.split()
        used: set[int] = set()
        compatible = True
        for source_token in source_tokens:
            matches = [
                (SequenceMatcher(None, source_token, token).ratio(), index)
                for index, token in enumerate(candidate_tokens)
                if index not in used
            ]
            best_score, best_index = max(matches, default=(0.0, -1))
            if best_score < DOCUMENT_LINK_OCR_TOKEN_THRESHOLD:
                compatible = False
                break
            used.add(best_index)
        if compatible:
            return True
    return False


def _fuller_alias(source_name: str, peers: Iterable[dict[str, Any]]) -> str | None:
    source_tokens = _tokens(source_name)
    candidates = []
    for peer in peers:
        peer_name = _source_name(peer)
        peer_tokens = _tokens(peer_name)
        if len(source_tokens) >= 2 and source_tokens < peer_tokens:
            candidates.append(peer_name)
    return sorted(candidates, key=lambda item: (len(_tokens(item)), normalize_name_key(item)))[0] if candidates else None


def _project_code(row: dict[str, Any]) -> str | None:
    metadata = row.get("metadata_json")
    if not isinstance(metadata, dict):
        return None
    stack: list[Any] = [metadata]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                if key in {"project_code", "codigo_proyecto", "code"} and value:
                    return str(value)
                stack.append(value)
        elif isinstance(item, list):
            stack.extend(item)
    return None


def _decision_fields(decision: CanonicalIdentityDecision, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "canonical_identity_key": decision.key,
        "canonical_name": decision.canonical_name,
        "identity_source": decision.source,
        "identity_confidence": decision.confidence,
        "identity_reason": decision.reason,
        "identity_locked": bool(row.get("identity_locked", False)),
        "identity_decided_by": row.get("identity_decided_by"),
        "identity_decided_at": row.get("identity_decided_at"),
        "identity_status": decision.status,
        "candidate_count": decision.candidate_count,
        "supporting_signals": list(decision.supporting_signals),
    }


def _before_identity(row: dict[str, Any], source_table: str) -> str:
    if row.get("teacher_id") is not None:
        return f"teacher:{row['teacher_id']}"
    if row.get("external_researcher_id") is not None:
        return f"external:{row['external_researcher_id']}"
    if source_table == "person_roles" and row.get("person_key"):
        return f"role-key:{normalize_name_key(row['person_key'])}"
    name = row.get("normalized_author_name") or row.get("raw_author_name") or row.get("normalized_name") or row.get("raw_name")
    return f"unresolved:{row.get('import_job_id')}:{normalize_name_key(name)}"


def _build_automatic_merges(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row["identity_status"] == "resolved":
            grouped.setdefault(row["canonical_identity_key"], []).append(row)
    output = []
    for key, members in sorted(grouped.items()):
        variants = sorted({_raw_name_from_output(row) for row in members if _raw_name_from_output(row)})
        normalized_variants = {normalize_name_key(value) for value in variants}
        if len(normalized_variants) < 2:
            continue
        output.append(
            {
                "canonical_identity_key": key,
                "canonical_name": members[0]["canonical_name"],
                "variants": variants,
                "role_keys": sorted(row["id"] for row in members if row["source_table"] == "person_roles"),
                "author_keys": sorted(
                    row["id"] for row in members if row["source_table"] == "scientific_production_authors"
                ),
                "supporting_evidence": sorted({signal for row in members for signal in row["supporting_signals"]}),
            }
        )
    return output


def _raw_name_from_output(row: dict[str, Any]) -> str:
    return str(row.get("raw_name") or row.get("raw_author_name") or "").strip()


def _build_ambiguities(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        if row["identity_status"] != "pending":
            continue
        output.append(
            {
                "source_table": row["source_table"],
                "row_id": row["id"],
                "import_job_id": row.get("import_job_id"),
                "source_name": _raw_name_from_output(row),
                "proposed_pending_key": row["canonical_identity_key"],
                "candidate_count": row["candidate_count"],
                "confidence": row["identity_confidence"],
                "missing_corroboration_or_competition": row["identity_reason"],
                "decision": "excluded_from_automatic_merge",
            }
        )
    return output


def _build_audited_groups(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, Any] = {}
    for surname in AUDITED_SURNAMES:
        matching = [
            row
            for row in rows
            if any(
                normalize_name_key(surname) in _tokens(value)
                for value in (
                    _raw_name_from_output(row),
                    row.get("canonical_name"),
                    *(row.get("candidate_canonical_names") or []),
                )
            )
        ]
        groups[surname] = {
            "role_keys": sorted(row["id"] for row in matching if row["source_table"] == "person_roles"),
            "author_keys": sorted(
                row["id"] for row in matching if row["source_table"] == "scientific_production_authors"
            ),
            "decisions": [
                {
                    "source_table": row["source_table"],
                    "row_id": row["id"],
                    "source_key": _before_identity(row, row["source_table"]),
                    "proposed_key": row["canonical_identity_key"],
                    "candidate_count": row["candidate_count"],
                    "confidence": row["identity_confidence"],
                    "corroboration": row["supporting_signals"],
                    "candidate_canonical_names": row.get("candidate_canonical_names", []),
                    "evidence_registry_id": row.get("evidence_registry_id"),
                    "evidence_relations": row.get("evidence_relations", []),
                    "decision": row["identity_status"],
                    "exclusion_reason": row["identity_reason"] if row["identity_status"] == "pending" else None,
                }
                for row in matching
            ],
        }
    return groups


def _person_matches(value: Any, person: str) -> bool:
    return _names_equivalent(value, person)


def _product_item(production: dict[str, Any]) -> dict[str, Any]:
    return {
        "production_id": production.get("id"),
        "title": production.get("title"),
        "validation_status": production.get("validation_status"),
        "import_job_id": production.get("import_job_id"),
    }


def _build_product_associations(dataset: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    production_by_id = {row.get("id"): row for row in dataset.get("productions", [])}
    authors = [row for row in rows if row["source_table"] == "scientific_production_authors"]
    people: dict[str, Any] = {}
    excluded_same_job: list[dict[str, Any]] = []
    for person in dataset.get("audited_people") or DEFAULT_AUDITED_PEOPLE:
        matching_keys = {
            row["canonical_identity_key"]
            for row in authors
            if _person_matches(row.get("canonical_name"), person)
            or _person_matches(row.get("normalized_author_name") or row.get("raw_author_name"), person)
        }
        current_authors = [
            row
            for row in authors
            if _person_matches(row.get("normalized_author_name") or row.get("raw_author_name"), person)
        ]
        expected_authors = [row for row in authors if row["canonical_identity_key"] in matching_keys]

        def products_for(author_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            ids = sorted({row.get("production_id") for row in author_rows if row.get("production_id") is not None})
            return [_product_item(production_by_id.get(identifier, {"id": identifier})) for identifier in ids]

        expected_ids = {row.get("production_id") for row in expected_authors}
        related_jobs = {row.get("import_job_id") for row in expected_authors if row.get("import_job_id") is not None}
        for production in dataset.get("productions", []):
            if production.get("import_job_id") in related_jobs and production.get("id") not in expected_ids:
                excluded_same_job.append(
                    {
                        "person": person,
                        "production_id": production.get("id"),
                        "import_job_id": production.get("import_job_id"),
                        "reason": "No scientific_production_authors row links this canonical person to the product.",
                    }
                )
        people[person] = {
            "current_products": products_for(current_authors),
            "expected_products": products_for(expected_authors),
            "current_count": len(products_for(current_authors)),
            "expected_count": len(products_for(expected_authors)),
        }
    return {
        "people": people,
        "proof": {
            "association_column": "scientific_production_authors.production_id",
            "import_job_only_associations": [],
            "excluded_same_import_job_products": excluded_same_job,
            "statement": "import_job_id was used only to prove exclusions and never to assign a product.",
        },
    }


def _consistency_errors(rows: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    by_registry: dict[Any, list[dict[str, Any]]] = {}
    for row in rows:
        by_registry.setdefault(row.get("evidence_registry_id"), []).append(row)
    for registry_id, members in by_registry.items():
        locked_decisions = {
            row["canonical_identity_key"]
            for row in members
            if row.get("identity_locked") and row.get("canonical_identity_key")
        }
        if len(locked_decisions) > 1:
            member_keys = ", ".join(f"{row['source_table']}:{row['id']}" for row in members)
            errors.append(
                f"{registry_id}: conflicting manual locks in relational evidence ({member_keys})"
            )
            continue
        resolved_decisions = {
            row["canonical_identity_key"] for row in members if row["identity_status"] == "resolved"
        }
        if len(resolved_decisions) > 1:
            member_keys = ", ".join(f"{row['source_table']}:{row['id']}" for row in members)
            errors.append(
                f"{registry_id}: relationally equivalent resolved evidence ({member_keys}) has different decisions"
            )
        pending_by_document: dict[Any, list[dict[str, Any]]] = {}
        for row in members:
            if row["identity_status"] == "pending":
                pending_by_document.setdefault(_effective_job_id(row), []).append(row)
        for job_id, document_members in pending_by_document.items():
            decisions = {row["canonical_identity_key"] for row in document_members}
            if len(decisions) > 1:
                member_keys = ", ".join(
                    f"{row['source_table']}:{row['id']}" for row in document_members
                )
                errors.append(
                    f"{registry_id}/effective_current_job_id={job_id}: equivalent pending evidence "
                    f"({member_keys}) has different decisions"
                )
    return errors


def _summary(dataset: dict[str, Any], rows: list[dict[str, Any]], products: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    before_keys = {_before_identity(row, row["source_table"]) for row in rows}
    after_keys = {row["canonical_identity_key"] for row in rows}
    before_participations = {
        (_before_identity(row, row["source_table"]), _effective_job_id(row)) for row in rows
    }
    after_participations = {(row["canonical_identity_key"], _effective_job_id(row)) for row in rows}
    validated_authors = sum(
        1
        for row in rows
        if row["source_table"] == "scientific_production_authors" and row.get("validation_status") == "validated"
    )
    validated_author_products = {
        row.get("production_id")
        for row in rows
        if row["source_table"] == "scientific_production_authors"
        and row.get("validation_status") == "validated"
        and row.get("production_id") is not None
    }
    eligible_products = sum(
        1
        for production in dataset.get("productions", [])
        if production.get("validation_status") == "validated"
        and production.get("id") in validated_author_products
    )
    external_before = {
        row.get("external_researcher_id") for row in rows if row.get("external_researcher_id") is not None
    }
    external_after = {
        row["canonical_identity_key"] for row in rows if row["identity_source"] == "external"
    }
    return {
        "identities_before": len(before_keys),
        "identities_after": len(after_keys),
        "person_document_participations_before": len(before_participations),
        "person_document_participations_after": len(after_participations),
        "roles_affected": sum(1 for row in rows if row["source_table"] == "person_roles"),
        "authors_affected": sum(1 for row in rows if row["source_table"] == "scientific_production_authors"),
        "pending_changes": sum(1 for row in rows if row["identity_status"] == "pending"),
        "external_counts": {"before": len(external_before), "expected": len(external_after)},
        "product_counts": {
            person: {"current": value["current_count"], "expected": value["expected_count"]}
            for person, value in products["people"].items()
        },
        "product_kpi_impact": {
            "before_eligible_products": eligible_products,
            "expected_eligible_products": eligible_products,
            "before_validated_authors": validated_authors,
            "expected_validated_authors": validated_authors,
            "delta": 0,
            "reason": (
                "Eligibility requires product validation_status=validated and at least one validated author; "
                "canonical grouping changes neither status."
            ),
        },
        "writes": 0,
        "database_writes": 0,
        "consistency_errors": errors,
    }


def simulate_dataset(
    dataset: dict[str, Any],
    resolver: CanonicalIdentityResolver | None = None,
) -> dict[str, Any]:
    source = deepcopy(dataset)
    resolver = resolver or CanonicalIdentityResolver(seed_service=InvestigatorSeedService())
    teachers = {row.get("id"): row for row in source.get("teachers", [])}
    externals = {row.get("id"): row for row in source.get("externals", [])}
    entries = [
        {"source_table": "person_roles", "row": row} for row in source.get("roles", [])
    ] + [
        {"source_table": "scientific_production_authors", "row": row}
        for row in source.get("authors", [])
    ]
    documents: dict[Any, list[dict[str, Any]]] = {}
    for entry in entries:
        documents.setdefault(_effective_job_id(entry["row"]), []).append(entry)

    row_changes: list[dict[str, Any]] = []
    for registry in _build_evidence_registry(entries):
        members = registry["members"]
        representative = sorted(
            members,
            key=lambda item: (-len(_tokens(_source_name(item))), _entry_key(item)),
        )[0]
        representative_row = representative["row"]
        locked = [
            decision
            for member in members
            if (decision := _locked_decision(member["row"])) is not None
        ]
        locked_keys = {decision.key for decision in locked}
        if len(locked_keys) > 1:
            conflict_reason = "Conflicting manual locks require operator review; no proposal was emitted."
            for entry in members:
                row = entry["row"]
                member_decision = _locked_decision(row)
                if member_decision is None:
                    member_decision = CanonicalIdentityDecision(
                        key="",
                        canonical_name=_source_name(entry),
                        source="lock_conflict",
                        confidence=0.0,
                        reason=conflict_reason,
                        status="lock_conflict",
                        candidate_count=0,
                        supporting_signals=("manual_lock_conflict",),
                    )
                output_row = {"source_table": entry["source_table"], **deepcopy(row)}
                output_row.setdefault("effective_current_job_id", _effective_job_id(row))
                output_row.update(_decision_fields(member_decision, row))
                if member_decision.status == "lock_conflict":
                    output_row["canonical_identity_key"] = None
                output_row["evidence_registry_id"] = registry["id"]
                output_row["evidence_relations"] = registry["relations"]
                output_row["candidate_canonical_names"] = []
                row_changes.append(output_row)
            continue
        teacher_members = [member for member in members if member["row"].get("teacher_id") is not None]
        external_members = [member for member in members if member["row"].get("external_researcher_id") is not None]
        teacher_member = teacher_members[0] if teacher_members else representative
        external_member = external_members[0] if external_members else representative
        teacher_id = teacher_member["row"].get("teacher_id")
        external_id = external_member["row"].get("external_researcher_id")
        teacher_name = teachers.get(teacher_id, {}).get("full_name") if teacher_id is not None else None
        external_name = externals.get(external_id, {}).get("full_name") if external_id is not None else None
        peers = documents.get(_effective_job_id(representative_row), [])
        evidence = IdentityEvidence(
            source_name=_source_name(representative),
            source_key=str(representative_row.get("person_key") or _source_name(representative)),
            import_job_id=str(
                registry["effective_job_id"]
                or f"missing-{representative['source_table']}-{representative_row.get('id')}"
            ),
            locked_decision=locked[0] if locked else None,
            teacher_id=teacher_id,
            teacher_name=teacher_name,
            external_id=external_id,
            external_name=external_name,
            fuller_alias_in_document=_fuller_alias(_source_name(representative), peers),
            document_owner=teacher_name or external_name,
            project_code=next((_project_code(member["row"]) for member in members if _project_code(member["row"])), None),
            generic_role=str(representative_row.get("role_type") or representative_row.get("author_type") or ""),
        )
        candidates = resolver._seed_candidates(evidence)
        evidence = replace(evidence, candidates=candidates)
        decision = resolver.resolve(evidence)
        candidate_names = sorted({candidate.canonical_name for candidate in candidates})
        decisions_by_member = {_entry_key(member): decision for member in members}
        if decision.status == "pending" or len(candidates) == 1:
            members_by_document: dict[Any, list[dict[str, Any]]] = {}
            for member in members:
                members_by_document.setdefault(_effective_job_id(member["row"]), []).append(member)
            for effective_job_id, document_members in members_by_document.items():
                document_representative = sorted(
                    document_members,
                    key=lambda item: (-len(_tokens(_source_name(item))), _entry_key(item)),
                )[0]
                document_row = document_representative["row"]
                document_scope = (
                    str(effective_job_id)
                    if effective_job_id is not None
                    else f"unscoped:{registry['id']}"
                )
                document_evidence = replace(
                    evidence,
                    source_name=_source_name(document_representative),
                    source_key=str(document_row.get("person_key") or _source_name(document_representative)),
                    import_job_id=document_scope,
                    fuller_alias_in_document=_fuller_alias(
                        _source_name(document_representative), documents.get(effective_job_id, [])
                    ),
                )
                linked_teacher_id, linked_teacher_name, linked_external_id, linked_external_name = (
                    _linked_document_identity(
                        document_representative,
                        documents.get(effective_job_id, []),
                        teachers,
                        externals,
                        document_evidence.candidates,
                    )
                )
                document_evidence = replace(
                    document_evidence,
                    teacher_id=linked_teacher_id,
                    teacher_name=linked_teacher_name,
                    external_id=linked_external_id,
                    external_name=linked_external_name,
                )
                linked_identity_found = linked_teacher_id is not None or linked_external_id is not None
                if not linked_identity_found and decision.status != "pending":
                    continue
                document_decision = resolver.resolve(document_evidence)
                if linked_identity_found:
                    document_decision = replace(
                        document_decision,
                        candidate_count=len(document_evidence.candidates),
                        supporting_signals=tuple(
                            dict.fromkeys((*document_decision.supporting_signals, "linked_document_identity"))
                        ),
                    )
                for member in document_members:
                    decisions_by_member[_entry_key(member)] = document_decision
        for entry in members:
            row = entry["row"]
            member_decision = decisions_by_member[_entry_key(entry)]
            output_row = {"source_table": entry["source_table"], **deepcopy(row)}
            output_row.setdefault("effective_current_job_id", _effective_job_id(row))
            output_row.update(_decision_fields(member_decision, row))
            output_row["evidence_registry_id"] = registry["id"]
            output_row["evidence_relations"] = registry["relations"]
            output_row["candidate_canonical_names"] = candidate_names
            row_changes.append(output_row)

    row_changes.sort(key=lambda row: (row["source_table"], int(row.get("id") or 0)))
    errors = _consistency_errors(row_changes)
    products = _build_product_associations(source, row_changes)
    return {
        "summary": _summary(source, row_changes, products, errors),
        "row_changes": row_changes,
        "automatic_merges": _build_automatic_merges(row_changes),
        "ambiguous_exclusions": _build_ambiguities(row_changes),
        "audited_groups": _build_audited_groups(row_changes),
        "product_associations": products,
    }


def _json_dump(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _csv_dump(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(value, ensure_ascii=False, default=str) if isinstance(value, (dict, list, tuple)) else value
                    for key, value in row.items()
                }
            )


def _render_snapshot(snapshot: dict[str, Any]) -> str:
    lines = [f"captured_at={datetime.now(timezone.utc).isoformat()}"]
    columns = snapshot.get("canonical_columns", [])
    lines.append(f"canonical_columns_present={len(columns)}")
    for item in columns:
        lines.append(f"canonical_column={item['table_name']}.{item['column_name']}")
    lines.extend(
        [
            f"role_rows={snapshot['roles']['count']}",
            f"role_immutable_hash={snapshot['roles']['hash']}",
            f"author_rows={snapshot['authors']['count']}",
            f"author_immutable_hash={snapshot['authors']['hash']}",
            f"trace_rows={snapshot['traces']['count']}",
            f"trace_evidence_hash={snapshot['traces']['hash']}",
            f"current_success_jobs={snapshot['current_success_jobs']['count']}",
            f"migration_table={snapshot['migration_table']}",
            "",
            "comprehensive_read_only_snapshot:",
        ]
    )
    for label in SNAPSHOT_QUERIES:
        item = snapshot[label]
        lines.append(f"{label}_count={item['count']}")
        lines.append(f"{label}_hash={item['hash']}")
    return "\n".join(lines) + "\n"


def _preflight_markdown(result: dict[str, Any], before: dict[str, Any], after: dict[str, Any]) -> str:
    summary = result["summary"]
    missing = [f"{table}.{column}" for table in ("person_roles", "scientific_production_authors") for column in PROPOSED_COLUMNS]
    present = {f"{row['table_name']}.{row['column_name']}" for row in after.get("canonical_columns", [])}
    missing = [column for column in missing if column not in present]
    lines = [
        "# Canonical Identity Preflight",
        "",
        "## Result",
        "",
        f"- Database writes: {summary['database_writes']}",
        f"- Consistency errors: {len(summary['consistency_errors'])}",
        f"- Source identities: {summary['identities_before']}",
        f"- Proposed identities: {summary['identities_after']}",
        f"- Roles/authors considered: {summary['roles_affected']}/{summary['authors_affected']}",
        f"- Pending decisions: {summary['pending_changes']}",
        (
            "- KPI-eligible products: "
            f"{summary['product_kpi_impact']['before_eligible_products']} before / "
            f"{summary['product_kpi_impact']['expected_eligible_products']} expected / "
            f"delta {summary['product_kpi_impact']['delta']}"
        ),
        (
            "- Validated authors (reported separately): "
            f"{summary['product_kpi_impact']['before_validated_authors']} before / "
            f"{summary['product_kpi_impact']['expected_validated_authors']} expected"
        ),
        "",
        "## Database Integrity",
        "",
    ]
    for label in SNAPSHOT_QUERIES:
        lines.append(
            f"- {label}: before `{before[label]['count']} / {before[label]['hash']}`, "
            f"after `{after[label]['count']} / {after[label]['hash']}`"
        )
    lines.extend(["", "## Canonical Columns Still Absent", ""])
    lines.extend(f"- `{column}`" for column in missing)
    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "- This is a virtual identity proposal only; no schema or data changes were made.",
            "- Ambiguous and isolated-prefix evidence remains pending for manual review.",
            "- Product attribution uses only scientific_production_authors.production_id.",
            "- Product and author validation statuses are unchanged, so KPI eligibility is unchanged.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_outputs(output_dir: Path, result: dict[str, Any], before: dict[str, Any], after: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _json_dump(output_dir / "summary.json", result["summary"])
    row_fieldnames = list(dict.fromkeys(key for row in result["row_changes"] for key in row))
    _csv_dump(output_dir / "row_changes.csv", result["row_changes"], row_fieldnames)
    _csv_dump(
        output_dir / "automatic_merges.csv",
        result["automatic_merges"],
        ["canonical_identity_key", "canonical_name", "variants", "role_keys", "author_keys", "supporting_evidence"],
    )
    _csv_dump(
        output_dir / "ambiguous_exclusions.csv",
        result["ambiguous_exclusions"],
        [
            "source_table", "row_id", "import_job_id", "source_name", "proposed_pending_key",
            "candidate_count", "confidence", "missing_corroboration_or_competition", "decision",
        ],
    )
    _json_dump(output_dir / "audited_groups.json", result["audited_groups"])
    _json_dump(output_dir / "product_associations.json", result["product_associations"])
    (output_dir / "database_after.txt").write_text(_render_snapshot(after), encoding="utf-8")
    (output_dir / "preflight_report.md").write_text(
        _preflight_markdown(result, before, after), encoding="utf-8"
    )


def run_preflight(database_url: str, output_dir: Path, seed_file: str) -> dict[str, Any]:
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                enforce_postgres_read_only(connection)
                before = snapshot_database(connection)
                dataset = load_dataset(connection)
                resolver = CanonicalIdentityResolver(seed_service=InvestigatorSeedService(seed_file))
                result = simulate_dataset(dataset, resolver=resolver)
                after = snapshot_database(connection)
                assert_snapshot_unchanged(before, after)
            finally:
                transaction.rollback()
        if result["summary"]["consistency_errors"]:
            raise RuntimeError("canonical identity consistency errors detected")
        write_outputs(output_dir, result, before, after)
        return result
    finally:
        engine.dispose()


def main() -> None:
    args = build_argument_parser().parse_args()
    result = run_preflight(args.database_url, Path(args.output_dir), args.seed_file)
    print(json.dumps({"output_dir": args.output_dir, **result["summary"]}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
