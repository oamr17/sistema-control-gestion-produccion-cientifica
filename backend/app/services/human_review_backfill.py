from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from itertools import combinations
import json
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select, text
from sqlalchemy.orm import Session, joinedload

from app.models.entities import (
    ExternalResearcher,
    PersonRole,
    ResearchEntity,
    ResearchProject,
    ScientificProduction,
    ScientificProductionAuthor,
)
from app.models.human_review_audit import AuditEvent
from app.models.human_review_core import ReviewDecision, ReviewItem
from app.models.human_review_enums import (
    AuditEventType,
    BackfillSourceMembership,
    CanonicalIdentityOrigin,
    CanonicalIdentityStatus,
    CanonicalIdentityType,
    DecisionLifecycle,
    OverrideField,
    OverrideScope,
    PersonAliasClass,
    PersonAliasScope,
    PersonAliasStatus,
    ReviewActorType,
    ReviewCaseStatus,
    ReviewCaseType,
    ReviewDecisionType,
    ReviewTargetTable,
    ScientificStatus,
)
from app.models.human_review_projection import CanonicalIdentity, FieldOverride, PersonAlias
from app.schemas.human_review import (
    IdentityDecisionPayloadV1,
    IdentityProjectionSnapshotV1,
    ProjectionAliasSnapshotV1,
    ProjectionOverrideSnapshotV1,
    ReviewProjectionSnapshotV1,
    ScalarOverrideValueV1,
    StableTargetV1,
)
from app.schemas.human_review_operations import (
    AliasCreatedAuditPayloadV1,
    AuditEventCommandV1,
    B2B1BackfillApplyResultV1,
    B2B1BackfillPlanV1,
    B2B1InvariantSnapshotV1,
    BackfillBlockerV1,
    BackfillCandidateV1,
    BackfillDeferredRecordV1,
    BackfillExcludedRecordV1,
    BackfillGateError,
    CaseBackfilledAuditPayloadV1,
    IdentityCreatedAuditPayloadV1,
    LockedDecisionImportV1,
    LockedDecisionImportedAuditPayloadV1,
    OverrideCreatedAuditPayloadV1,
    SourcePopulationCountV1,
    SourcePopulationOverlapV1,
)
from app.services.human_review_audit import (
    append_audit_event_at_current_head,
    compute_audit_event_hash,
)
from app.services.human_review_invariants import (
    _capture_database_state,
    _database_state_changes,
    _participant_metrics,
    capture_b2b1_invariants,
    compare_b2b1_invariants,
    snapshot_sha256,
)
from app.services.human_review_targets import (
    build_stable_target_key,
    normalize_person_alias,
    raw_value_sha256,
)
from app.services.human_review_scope import ResolvedReviewScope, resolve_target_scope
from app.services.validated_read_service import ValidatedReadService


SOURCE_TABLES = (
    "external_researchers",
    "person_roles",
    "research_entities",
    "research_projects",
    "scientific_production_authors",
    "scientific_productions",
)
GLOBAL_BACKFILL_LOCK_NAME = "human-review-b2b1-backfill"
_DECISION_PAYLOAD_SCHEMA = "review.decision.v1"
_OVERRIDE_VALUE_SCHEMA = "override.scalar.v1"
_AUDIT_AGGREGATE_TYPE = "review_item"


@dataclass(frozen=True)
class _ProjectEvidence:
    evidence_source: Literal["research_projects", "research_entity_self"]
    row: ResearchProject | ResearchEntity
    match_criterion: Literal["code", "name"]


@dataclass(frozen=True)
class _ProjectEvidenceResolution:
    evidence: _ProjectEvidence | None
    blocker_code: str | None = None
    blocker_message: str | None = None


@dataclass(frozen=True)
class _Discovery:
    candidates: tuple[BackfillCandidateV1, ...]
    locked_decisions: tuple[LockedDecisionImportV1, ...]
    blockers: tuple[BackfillBlockerV1, ...]
    deferred_records: tuple[BackfillDeferredRecordV1, ...]
    excluded_records: tuple[BackfillExcludedRecordV1, ...]
    population_rows: dict[BackfillSourceMembership, frozenset[tuple[str, int]]]
    population_targets: dict[BackfillSourceMembership, frozenset[str]]
    target_memberships: dict[str, frozenset[BackfillSourceMembership]]


def _uuid(domain: str, key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"human-review-b2b1:{domain}:{key}")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def backfill_plan_sha256(plan: B2B1BackfillPlanV1) -> str:
    validated = B2B1BackfillPlanV1.model_validate(plan)
    semantic_material = validated.model_dump(mode="json")
    semantic_material.pop("captured_at")
    return sha256(_canonical_json(semantic_material)).hexdigest()


def _target_fingerprint(target: StableTargetV1) -> str:
    material = target.model_dump(
        mode="json",
        exclude={"case_type", "target_pk"},
    )
    return sha256(_canonical_json(material)).hexdigest()


def _first_nonblank(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value
    return None


def _document_key(row: object) -> str | None:
    job = getattr(row, "import_job", None)
    production = getattr(row, "production", None)
    production_job = getattr(production, "import_job", None)
    return _first_nonblank(
        getattr(job, "document_key", None),
        getattr(job, "filename", None),
        getattr(row, "source_file", None),
        getattr(production_job, "document_key", None),
        getattr(production_job, "filename", None),
        getattr(production, "source_file", None),
    )


def _source_revision(row: object) -> str | None:
    job = getattr(row, "import_job", None)
    production = getattr(row, "production", None)
    production_job = getattr(production, "import_job", None)
    return _first_nonblank(
        getattr(job, "source_rev", None),
        getattr(row, "parser_version", None),
        getattr(production_job, "source_rev", None),
        getattr(production, "parser_version", None),
    )


def _row_or_block_id(row: object) -> str | None:
    if isinstance(row, PersonRole):
        metadata = row.metadata_json or {}
        return _first_nonblank(
            metadata.get("row_or_block_id"),
            metadata.get("row_id"),
            metadata.get("block_id"),
        )
    if isinstance(row, ScientificProductionAuthor):
        return _first_nonblank(row.row_or_block_id)
    if isinstance(row, ScientificProduction):
        title = _first_nonblank(row.normalized_title, row.title)
        return f"product:{title.casefold()}" if title else None
    if isinstance(row, ResearchEntity):
        metadata = row.metadata_json or {}
        identity = _first_nonblank(
            metadata.get("row_or_block_id"),
            metadata.get("row_id"),
            row.normalized_code,
            row.code,
            row.normalized_name,
            row.name,
        )
        return f"entity:{identity}" if identity else None
    if isinstance(row, ExternalResearcher):
        name = _first_nonblank(row.normalized_name, row.full_name)
        institution = _first_nonblank(row.normalized_institution, row.institution)
        return f"external:{name}|{institution}" if name and institution else None
    return None


def _target_table(row: object) -> ReviewTargetTable:
    if isinstance(row, PersonRole):
        return ReviewTargetTable.PERSON_ROLES
    if isinstance(row, ScientificProductionAuthor):
        return ReviewTargetTable.SCIENTIFIC_PRODUCTION_AUTHORS
    if isinstance(row, ScientificProduction):
        return ReviewTargetTable.SCIENTIFIC_PRODUCTIONS
    if isinstance(row, ResearchEntity):
        return ReviewTargetTable.RESEARCH_ENTITIES
    if isinstance(row, ExternalResearcher):
        return ReviewTargetTable.EXTERNAL_RESEARCHERS
    raise TypeError(f"unsupported backfill source row: {type(row).__name__}")


def _raw_value(row: object) -> str | None:
    if isinstance(row, PersonRole):
        return _first_nonblank(row.raw_value, row.raw_name, row.canonical_name)
    if isinstance(row, ScientificProductionAuthor):
        return _first_nonblank(row.raw_author_name, row.normalized_author_name)
    if isinstance(row, ScientificProduction):
        return _first_nonblank(row.raw_value, row.raw_title, row.title)
    if isinstance(row, ResearchEntity):
        return _first_nonblank(row.raw_value, row.name, row.code, row.director_name)
    if isinstance(row, ExternalResearcher):
        return _first_nonblank(row.raw_value, row.raw_name, row.full_name)
    return None


def _source_section(row: object) -> str | None:
    production = getattr(row, "production", None)
    return _first_nonblank(
        getattr(row, "source_section", None),
        getattr(production, "source_section", None),
    )


def _source_page(row: object) -> int | None:
    production = getattr(row, "production", None)
    return getattr(row, "source_page", None) or getattr(production, "source_page", None)


def _period_id(row: object) -> int | None:
    production = getattr(row, "production", None)
    return getattr(row, "period_id", None) or getattr(production, "period_id", None)


def _normalized_relation_component(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.casefold().split())
    return normalized or None


def _project_match_keys(row: ResearchEntity | ResearchProject) -> frozenset[str]:
    if isinstance(row, ResearchEntity):
        code = _normalized_relation_component(_first_nonblank(row.normalized_code, row.code))
        name = _normalized_relation_component(_first_nonblank(row.normalized_name, row.name))
    else:
        code = _normalized_relation_component(_first_nonblank(row.normalized_code, row.raw_code))
        name = _normalized_relation_component(_first_nonblank(
            row.normalized_project_name,
            row.raw_project_name,
            row.name,
        ))
    return frozenset(
        value
        for value in (
            f"code:{code}" if code is not None else None,
            f"name:{name}" if name is not None else None,
        )
        if value is not None
    )


def _project_components(
    row: ResearchEntity | ResearchProject,
) -> tuple[str | None, str | None]:
    keys = _project_match_keys(row)
    code = next((value[5:] for value in keys if value.startswith("code:")), None)
    name = next((value[5:] for value in keys if value.startswith("name:")), None)
    return code, name


def _project_evidence_contradicts(
    entity: ResearchEntity,
    evidence: ResearchEntity | ResearchProject,
) -> bool:
    if evidence.period_id != entity.period_id:
        return False
    entity_code, entity_name = _project_components(entity)
    evidence_code, evidence_name = _project_components(evidence)
    return bool(
        (
            entity_code
            and evidence_code
            and entity_code == evidence_code
            and entity_name
            and evidence_name
            and entity_name != evidence_name
        )
        or (
            entity_name
            and evidence_name
            and entity_name == evidence_name
            and entity_code
            and evidence_code
            and entity_code != evidence_code
        )
    )


def _project_evidence_matches(
    entity: ResearchEntity,
    evidence: ResearchEntity | ResearchProject,
) -> Literal["code", "name"] | None:
    if evidence.period_id != entity.period_id:
        return None
    entity_code, entity_name = _project_components(entity)
    evidence_code, evidence_name = _project_components(evidence)
    if _project_evidence_contradicts(entity, evidence):
        return None
    if entity_code is not None:
        return "code" if evidence_code == entity_code else None
    if entity_name is not None and evidence_name == entity_name:
        return "name"
    return None


def _matching_project_evidence(
    entity: ResearchEntity,
    projects: tuple[ResearchProject, ...],
) -> tuple[_ProjectEvidence, ...]:
    return tuple(
        _ProjectEvidence(
            evidence_source="research_projects",
            row=project,
            match_criterion=criterion,
        )
        for project in sorted(projects, key=lambda value: value.id)
        if (criterion := _project_evidence_matches(entity, project)) is not None
    )


def _resolve_project_evidence(
    entity: ResearchEntity,
    *,
    entities: tuple[ResearchEntity, ...],
    projects: tuple[ResearchProject, ...],
) -> _ProjectEvidenceResolution:
    entity_type = _normalized_relation_component(entity.type)
    if entity_type in {"grupo_investigacion", "semillero"}:
        return _ProjectEvidenceResolution(
            evidence=None,
            blocker_code="entity_type_incompatible",
            blocker_message="entity type is outside director relation review",
        )

    external_contradictions = tuple(
        project
        for project in projects
        if _project_evidence_contradicts(entity, project)
    )
    if external_contradictions:
        return _ProjectEvidenceResolution(
            evidence=None,
            blocker_code="contradictory_project_evidence",
            blocker_message="research project evidence contradicts project code or name",
        )
    external_matches = _matching_project_evidence(entity, projects)
    if len(external_matches) > 1:
        return _ProjectEvidenceResolution(
            evidence=None,
            blocker_code="ambiguous_project_evidence",
            blocker_message="more than one exact research project evidence row exists",
        )
    if len(external_matches) == 1:
        evidence = external_matches[0]
        if not _document_key(evidence.row) or not _source_section(evidence.row):
            return _ProjectEvidenceResolution(
                evidence=None,
                blocker_code="missing_stable_locator",
                blocker_message="matched research project evidence lacks document context",
            )
        return _ProjectEvidenceResolution(evidence=evidence)

    if entity_type != "proyecto_fci":
        return _ProjectEvidenceResolution(
            evidence=None,
            blocker_code="invariant_mismatch",
            blocker_message="director relation requires exact research project evidence",
        )

    entity_code, entity_name = _project_components(entity)
    if (
        entity.period_id is None
        or not _first_nonblank(entity.normalized_director_name, entity.director_name)
        or not _document_key(entity)
        or not _source_section(entity)
        or not _row_or_block_id(entity)
    ):
        return _ProjectEvidenceResolution(
            evidence=None,
            blocker_code="missing_stable_locator",
            blocker_message="project self evidence lacks required stable provenance",
        )
    if entity_code is None and entity_name is None:
        return _ProjectEvidenceResolution(
            evidence=None,
            blocker_code="invariant_mismatch",
            blocker_message="project self evidence lacks a normalized code or name",
        )

    compatible_entities = tuple(
        candidate
        for candidate in entities
        if _normalized_relation_component(candidate.type) == "proyecto_fci"
        and candidate.period_id == entity.period_id
    )
    contradictions = tuple(
        candidate
        for candidate in compatible_entities
        if _project_evidence_contradicts(entity, candidate)
    )
    if contradictions:
        return _ProjectEvidenceResolution(
            evidence=None,
            blocker_code="contradictory_project_evidence",
            blocker_message="project self evidence has contradictory code or name",
        )
    self_matches = tuple(
        (candidate, criterion)
        for candidate in sorted(compatible_entities, key=lambda value: value.id)
        if (criterion := _project_evidence_matches(entity, candidate)) is not None
    )
    if len(self_matches) != 1:
        return _ProjectEvidenceResolution(
            evidence=None,
            blocker_code=(
                "ambiguous_project_evidence" if len(self_matches) > 1
                else "invariant_mismatch"
            ),
            blocker_message="project self evidence is not unique in the same period",
        )
    matched_entity, criterion = self_matches[0]
    if matched_entity.id != entity.id:
        return _ProjectEvidenceResolution(
            evidence=None,
            blocker_code="ambiguous_project_evidence",
            blocker_message="project self evidence resolves to a different entity",
        )
    return _ProjectEvidenceResolution(
        evidence=_ProjectEvidence(
            evidence_source="research_entity_self",
            row=matched_entity,
            match_criterion=criterion,
        )
    )


def _relationship_key(
    row: object,
    project_evidence: _ProjectEvidence | ResearchProject | None = None,
) -> str | None:
    if not isinstance(row, ResearchEntity):
        return None
    entity = _normalized_relation_component(_first_nonblank(
        row.normalized_code,
        row.code,
        row.normalized_name,
        row.name,
    ))
    director = _normalized_relation_component(_first_nonblank(
        row.normalized_director_name,
        row.director_name,
    ))
    if not entity or not director:
        return None
    material = f"entity:{entity}|director:{director}"
    if project_evidence is not None:
        evidence_row = (
            project_evidence.row
            if isinstance(project_evidence, _ProjectEvidence)
            else project_evidence
        )
        project_keys = sorted(_project_match_keys(evidence_row))
        if not project_keys:
            return None
        material = f"project:{'|'.join(project_keys)}|{material}"
    if len(material) <= 320:
        return material
    return f"project-director:{sha256(material.encode('utf-8')).hexdigest()}"


def _stable_target(
    row: object,
    case_type: ReviewCaseType,
    project_evidence: _ProjectEvidence | ResearchProject | None = None,
) -> StableTargetV1 | None:
    document_key = _document_key(row)
    source_section = _source_section(row)
    row_or_block_id = _row_or_block_id(row)
    if not document_key or not source_section or not row_or_block_id:
        return None
    return StableTargetV1(
        schema_version=1,
        case_type=case_type,
        target_table=_target_table(row),
        target_pk=row.id,
        document_key=document_key,
        source_revision=_source_revision(row),
        source_page=_source_page(row),
        source_section=source_section,
        row_or_block_id=row_or_block_id,
        field_path="case",
        raw_value_sha256=raw_value_sha256(_raw_value(row)),
        period_id=_period_id(row),
        relationship_key=_relationship_key(row, project_evidence),
    )


def _deferred_person_role(
    row: PersonRole,
) -> BackfillDeferredRecordV1 | None:
    document_key = _document_key(row)
    source_section = _source_section(row)
    period_id = _period_id(row)
    identity_reference = _first_nonblank(
        row.canonical_identity_key,
        row.person_key,
    )
    if (
        _row_or_block_id(row) is not None
        or document_key is None
        or source_section is None
        or period_id is None
        or identity_reference is None
    ):
        return None
    return BackfillDeferredRecordV1(
        source_table=ReviewTargetTable.PERSON_ROLES,
        source_pk=row.id,
        source_population=BackfillSourceMembership.CANONICAL_PENDING,
        case_type=ReviewCaseType.PERSON_IDENTITY,
        period_id=period_id,
        document_key=document_key,
        identity_reference=identity_reference,
        reason_code="missing_row_locator",
        disposition="expected_pending_review",
        materializable=False,
        affects_kpi=False,
        evidence_summary=(
            "document, period, identity reference, and source section are retained; "
            "no stable row locator exists"
        ),
    )


def _excluded_director_record(
    entity: ResearchEntity,
) -> BackfillExcludedRecordV1 | None:
    entity_type = _normalized_relation_component(entity.type)
    period_id = _period_id(entity)
    if entity_type not in {"grupo_investigacion", "semillero"} or period_id is None:
        return None
    return BackfillExcludedRecordV1(
        source_table=ReviewTargetTable.RESEARCH_ENTITIES,
        research_entity_id=entity.id,
        source_population=BackfillSourceMembership.DIRECTOR_RELATION_PENDING,
        case_type=ReviewCaseType.PROJECT_DIRECTOR_RELATION,
        period_id=period_id,
        entity_type=entity_type,
        reason_code="entity_type_incompatible",
        disposition="out_of_scope_record",
        materializable=False,
        affects_kpi=False,
        evidence_summary=(
            "normalized entity type is excluded only from director relation review"
        ),
    )


def _identity_type(row: PersonRole | ScientificProductionAuthor) -> CanonicalIdentityType:
    classification = (
        row.person_type if isinstance(row, PersonRole) else row.author_type
    ).casefold()
    if "external" in classification or "externo" in classification:
        return CanonicalIdentityType.EXTERNAL_PERSON
    if "internal" in classification or "interno" in classification or "docente" in classification:
        return CanonicalIdentityType.INTERNAL_PERSON
    return CanonicalIdentityType.UNCLASSIFIED_PERSON


def _load_source_rows(db: Session):
    roles = tuple(db.scalars(
        select(PersonRole).options(joinedload(PersonRole.import_job)).order_by(PersonRole.id)
    ).unique())
    authors = tuple(db.scalars(
        select(ScientificProductionAuthor)
        .options(
            joinedload(ScientificProductionAuthor.import_job),
            joinedload(ScientificProductionAuthor.production).joinedload(
                ScientificProduction.import_job
            ),
        )
        .order_by(ScientificProductionAuthor.id)
    ).unique())
    productions = tuple(db.scalars(
        select(ScientificProduction)
        .options(joinedload(ScientificProduction.import_job))
        .order_by(ScientificProduction.id)
    ).unique())
    entities = tuple(db.scalars(
        select(ResearchEntity)
        .options(joinedload(ResearchEntity.import_job))
        .order_by(ResearchEntity.id)
    ).unique())
    projects = tuple(db.scalars(
        select(ResearchProject)
        .options(joinedload(ResearchProject.import_job))
        .order_by(ResearchProject.id)
    ).unique())
    externals = tuple(db.scalars(
        select(ExternalResearcher)
        .options(joinedload(ExternalResearcher.import_job))
        .order_by(ExternalResearcher.id)
    ).unique())
    return roles, authors, productions, entities, projects, externals


def _discover(db: Session) -> _Discovery:
    reader = ValidatedReadService(db)
    participants = reader.canonical_participants()
    roles, authors, productions, entities, projects, externals = _load_source_rows(db)
    rows_by_source = {
        (ReviewTargetTable.PERSON_ROLES, row.id): row for row in roles
    } | {
        (ReviewTargetTable.SCIENTIFIC_PRODUCTION_AUTHORS, row.id): row for row in authors
    } | {
        (ReviewTargetTable.SCIENTIFIC_PRODUCTIONS, row.id): row for row in productions
    } | {
        (ReviewTargetTable.RESEARCH_ENTITIES, row.id): row for row in entities
    } | {
        (ReviewTargetTable.EXTERNAL_RESEARCHERS, row.id): row for row in externals
    }

    candidate_memberships: dict[
        tuple[ReviewCaseType, ReviewTargetTable, int], set[BackfillSourceMembership]
    ] = defaultdict(set)
    candidate_targets: dict[
        tuple[ReviewCaseType, ReviewTargetTable, int], StableTargetV1
    ] = {}
    population_rows: dict[BackfillSourceMembership, set[tuple[str, int]]] = {
        membership: set() for membership in BackfillSourceMembership
    }
    population_targets: dict[BackfillSourceMembership, set[str]] = {
        membership: set() for membership in BackfillSourceMembership
    }
    target_memberships: dict[str, set[BackfillSourceMembership]] = defaultdict(set)
    blocker_map: dict[tuple[ReviewTargetTable, int, str], BackfillBlockerV1] = {}
    deferred_map: dict[int, BackfillDeferredRecordV1] = {}
    excluded_map: dict[int, BackfillExcludedRecordV1] = {}

    def add_blocker(row: object, code: str, message: str) -> None:
        table = _target_table(row)
        key = (table, row.id, code)
        blocker_map.setdefault(key, BackfillBlockerV1(
            source_table=table,
            source_id=row.id,
            code=code,
            message=message,
        ))

    def add_membership(
        row: object,
        case_type: ReviewCaseType,
        membership: BackfillSourceMembership,
        stable_target_case_type: ReviewCaseType | None = None,
        project_evidence: _ProjectEvidence | ResearchProject | None = None,
    ) -> None:
        table = _target_table(row)
        population_rows[membership].add((table.value, row.id))
        target = _stable_target(
            row,
            stable_target_case_type or case_type,
            project_evidence,
        )
        if target is None:
            if isinstance(row, PersonRole):
                deferred = _deferred_person_role(row)
                if (
                    membership is BackfillSourceMembership.CANONICAL_PENDING
                    and case_type is ReviewCaseType.PERSON_IDENTITY
                    and deferred is not None
                ):
                    deferred_map.setdefault(row.id, deferred)
                    return
                if row.id in deferred_map:
                    return
            add_blocker(
                row,
                "missing_stable_locator",
                "required document, section, or row locator is missing",
            )
            return
        key = (case_type, table, row.id)
        candidate_memberships[key].add(membership)
        candidate_targets[key] = target
        fingerprint = _target_fingerprint(target)
        population_targets[membership].add(fingerprint)
        target_memberships[fingerprint].add(membership)

    pending_identity_rows: set[tuple[ReviewTargetTable, int]] = set()
    for participant in participants:
        for variant in participant["variants"]:
            source_table_value = variant.get("source_table")
            if source_table_value not in {
                ReviewTargetTable.PERSON_ROLES.value,
                ReviewTargetTable.SCIENTIFIC_PRODUCTION_AUTHORS.value,
            }:
                continue
            table = ReviewTargetTable(source_table_value)
            row_key = (table, int(variant["source_id"]))
            row = rows_by_source.get(row_key)
            if row is None:
                continue
            source_pending = (
                str(row.validation_status).casefold() != "validated"
                or str(row.canonical_identity_key or "").startswith("pending:")
            )
            if participant["overall_status"] == "pending_review" and source_pending:
                pending_identity_rows.add(row_key)
                add_membership(
                    row,
                    ReviewCaseType.PERSON_IDENTITY
                    if table is ReviewTargetTable.PERSON_ROLES
                    else ReviewCaseType.AUTHOR_IDENTITY,
                    BackfillSourceMembership.CANONICAL_PENDING,
                )
            if participant.get("possible_matches"):
                add_membership(
                    row,
                    ReviewCaseType.POSSIBLE_DUPLICATE,
                    BackfillSourceMembership.POSSIBLE_MATCH,
                    (
                        ReviewCaseType.PERSON_IDENTITY
                        if table is ReviewTargetTable.PERSON_ROLES
                        else ReviewCaseType.AUTHOR_IDENTITY
                    ),
                )

    for row in (*roles, *authors):
        if not row.identity_locked:
            continue
        add_membership(
            row,
            ReviewCaseType.PERSON_IDENTITY
            if isinstance(row, PersonRole)
            else ReviewCaseType.AUTHOR_IDENTITY,
            BackfillSourceMembership.IDENTITY_LOCKED,
        )

    for view in reader.production_views(visibility="pending"):
        row = rows_by_source[(ReviewTargetTable.SCIENTIFIC_PRODUCTIONS, int(view["id"]))]
        add_membership(
            row,
            ReviewCaseType.PRODUCT,
            BackfillSourceMembership.PRODUCT_PENDING,
        )

    for entity in reader.entity_list():
        state = reader.project_director_state(entity)
        if (
            state["director_identity_validation_status"] != "validated"
            or state["director_relationship_validation_status"] != "validated"
        ):
            excluded = _excluded_director_record(entity)
            if excluded is not None:
                excluded_map.setdefault(entity.id, excluded)
                continue
            resolution = _resolve_project_evidence(
                entity,
                entities=entities,
                projects=projects,
            )
            if resolution.evidence is None:
                population_rows[
                    BackfillSourceMembership.DIRECTOR_RELATION_PENDING
                ].add((ReviewTargetTable.RESEARCH_ENTITIES.value, entity.id))
                add_blocker(
                    entity,
                    resolution.blocker_code or "invariant_mismatch",
                    resolution.blocker_message
                    or "director relation lacks exact project evidence",
                )
                continue
            add_membership(
                entity,
                ReviewCaseType.PROJECT_DIRECTOR_RELATION,
                BackfillSourceMembership.DIRECTOR_RELATION_PENDING,
                project_evidence=resolution.evidence,
            )

    linked_pending_external_ids = {
        row.external_researcher_id
        for row in (*roles, *authors)
        if row.external_researcher_id is not None
        and (_target_table(row), row.id) in pending_identity_rows
    }
    for external in externals:
        if external.requires_review or external.id in linked_pending_external_ids:
            add_membership(
                external,
                ReviewCaseType.EXTERNAL_IDENTITY,
                BackfillSourceMembership.EXTERNAL_PENDING,
            )

    candidates: list[BackfillCandidateV1] = []
    for key, memberships in candidate_memberships.items():
        case_type, source_table, source_id = key
        locked = BackfillSourceMembership.IDENTITY_LOCKED in memberships
        candidates.append(BackfillCandidateV1(
            case_type=case_type,
            stable_target=candidate_targets[key],
            source_table=source_table,
            source_id=source_id,
            memberships=tuple(memberships),
            case_status=(
                ReviewCaseStatus.RESOLVED if locked else ReviewCaseStatus.PENDING
            ),
            scientific_status=(
                ScientificStatus.VALIDATED if locked else ScientificStatus.PENDING
            ),
            possible_kpi_impact=case_type is not ReviewCaseType.POSSIBLE_DUPLICATE,
        ))

    locked_decisions: list[LockedDecisionImportV1] = []
    for candidate in candidates:
        if BackfillSourceMembership.IDENTITY_LOCKED not in candidate.memberships:
            continue
        row = rows_by_source[(candidate.source_table, candidate.source_id)]
        canonical_key = _first_nonblank(row.canonical_identity_key)
        canonical_name = _first_nonblank(row.canonical_name)
        actor = _first_nonblank(row.identity_decided_by)
        decided_at = row.identity_decided_at
        if not canonical_key or not canonical_name or not actor or decided_at is None:
            add_blocker(
                row,
                "incomplete_locked_decision",
                "locked identity lacks canonical identity, actor, or decision timestamp",
            )
            continue
        if decided_at.tzinfo is None or decided_at.utcoffset() is None:
            decided_at = decided_at.replace(tzinfo=timezone.utc)
        alias = (
            _first_nonblank(row.raw_name)
            if isinstance(row, PersonRole)
            else _first_nonblank(row.raw_author_name)
        )
        locked_decisions.append(LockedDecisionImportV1(
            candidate=candidate,
            canonical_identity_key=canonical_key,
            canonical_name=canonical_name,
            identity_type=_identity_type(row),
            legacy_actor_identifier=actor,
            legacy_decided_at=decided_at,
            alias_original=alias,
        ))

    aliases: dict[str, list[LockedDecisionImportV1]] = defaultdict(list)
    for decision in locked_decisions:
        if decision.alias_original:
            aliases[normalize_person_alias(decision.alias_original)].append(decision)
    for decisions in aliases.values():
        if len({item.canonical_identity_key for item in decisions}) <= 1:
            continue
        for decision in decisions:
            row = rows_by_source[
                (decision.candidate.source_table, decision.candidate.source_id)
            ]
            add_blocker(
                row,
                "ambiguous_alias",
                "normalized alias maps to more than one canonical identity",
            )

    return _Discovery(
        candidates=tuple(candidates),
        locked_decisions=tuple(locked_decisions),
        blockers=tuple(blocker_map.values()),
        deferred_records=tuple(deferred_map.values()),
        excluded_records=tuple(excluded_map.values()),
        population_rows={
            key: frozenset(value) for key, value in population_rows.items()
        },
        population_targets={
            key: frozenset(value) for key, value in population_targets.items()
        },
        target_memberships={
            key: frozenset(value) for key, value in target_memberships.items()
        },
    )


def load_backfill_candidates(db: Session) -> tuple[BackfillCandidateV1, ...]:
    case_rank = {
        case_type: index for index, case_type in enumerate(ReviewCaseType)
    }
    return tuple(sorted(
        _discover(db).candidates,
        key=lambda candidate: (
            case_rank[candidate.case_type],
            build_stable_target_key(candidate.stable_target),
        ),
    ))


def _capture_current_invariants(db: Session) -> B2B1InvariantSnapshotV1:
    connection = db.connection()
    if connection.execute(text("SHOW transaction_read_only")).scalar_one() == "on":
        return capture_b2b1_invariants(connection)
    before = _capture_database_state(connection)
    reader = ValidatedReadService(db)
    eligible_products = len(reader.production_views(visibility="eligible"))
    participant_metrics = _participant_metrics(reader)
    after = _capture_database_state(connection)
    changes = _database_state_changes(before, after)
    if changes:
        raise BackfillGateError(
            "invariant capture changed protected data: " + ", ".join(changes)
        )
    return B2B1InvariantSnapshotV1(
        schema_version=1,
        captured_at=datetime.now(timezone.utc),
        migration_versions=before.migration_versions,
        digests=before.digests,
        eligible_products=eligible_products,
        participant_metrics=participant_metrics,
    )


def build_backfill_plan(
    db: Session,
    captured_at: datetime,
) -> B2B1BackfillPlanV1:
    invariants = _capture_current_invariants(db)
    discovery = _discover(db)
    source_counts = tuple(
        SourcePopulationCountV1(
            population=membership,
            row_count=len(discovery.population_rows[membership]),
            stable_target_count=len(discovery.population_targets[membership]),
        )
        for membership in BackfillSourceMembership
    )
    overlap_targets: dict[
        tuple[BackfillSourceMembership, ...], set[str]
    ] = defaultdict(set)
    membership_rank = {
        membership: index for index, membership in enumerate(BackfillSourceMembership)
    }
    for fingerprint, memberships in discovery.target_memberships.items():
        ordered = tuple(sorted(memberships, key=membership_rank.__getitem__))
        for size in range(2, len(ordered) + 1):
            for population_set in combinations(ordered, size):
                overlap_targets[population_set].add(fingerprint)
    overlaps = tuple(
        SourcePopulationOverlapV1(
            populations=population_set,
            stable_target_count=len(targets),
        )
        for population_set, targets in overlap_targets.items()
    )
    union = {
        build_stable_target_key(candidate.stable_target)
        for candidate in discovery.candidates
    }
    return B2B1BackfillPlanV1(
        schema_version=1,
        captured_at=captured_at,
        invariant_snapshot_sha256=snapshot_sha256(invariants),
        source_counts=source_counts,
        overlaps=overlaps,
        union_stable_target_count=len(union),
        candidates=discovery.candidates,
        locked_decisions=discovery.locked_decisions,
        blockers=discovery.blockers,
        deferred_records=discovery.deferred_records,
        excluded_records=discovery.excluded_records,
        hard_blockers=discovery.blockers,
    )


def _validate_apply_context(db: Session) -> None:
    connection = db.connection()
    if connection.dialect.name != "postgresql":
        raise BackfillGateError("backfill apply requires PostgreSQL")
    current_user, session_user = connection.execute(text(
        "SELECT current_user, session_user"
    )).one()
    if current_user == session_user:
        raise BackfillGateError("apply role was not downgraded before functional work")
    role = connection.execute(text(
        """
        SELECT rolsuper, rolcreatedb, rolcreaterole
        FROM pg_roles
        WHERE rolname = current_user
        """
    )).one()
    if any(role):
        raise BackfillGateError("application role attributes are not least privilege")
    for table_name in SOURCE_TABLES:
        for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            if connection.execute(text(
                "SELECT has_table_privilege(current_user, :table_name, :privilege)"
            ), {"table_name": table_name, "privilege": privilege}).scalar_one():
                raise BackfillGateError("application role can mutate a protected source")
    locked_sources = tuple(connection.execute(text(
        """
        SELECT relation.relname
        FROM pg_locks AS lock_row
        JOIN pg_class AS relation ON relation.oid = lock_row.relation
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE lock_row.pid = pg_backend_pid()
          AND lock_row.locktype = 'relation'
          AND lock_row.mode = 'ShareLock'
          AND lock_row.granted
          AND namespace.nspname = current_schema()
          AND relation.relname = ANY(:source_tables)
        ORDER BY relation.relname
        """
    ), {"source_tables": list(SOURCE_TABLES)}).scalars())
    if locked_sources != tuple(sorted(SOURCE_TABLES)):
        raise BackfillGateError("six protected source ShareLocks are required")
    global_advisory_locks = connection.execute(text(
        """
        SELECT COUNT(*)
        FROM pg_locks
        WHERE pid = pg_backend_pid()
          AND locktype = 'advisory'
          AND mode = 'ExclusiveLock'
          AND granted
          AND classid = (
              ((hashtext(:lock_name)::bigint >> 32) & 4294967295)::oid
          )
          AND objid = (hashtext(:lock_name)::bigint & 4294967295)::oid
          AND objsubid = 1
        """
    ), {"lock_name": GLOBAL_BACKFILL_LOCK_NAME}).scalar_one()
    if global_advisory_locks != 1:
        raise BackfillGateError("global backfill advisory lock is missing")


def _projection_payload(
    locked: LockedDecisionImportV1,
    stable_target_key: str,
    decision_id: UUID,
) -> IdentityDecisionPayloadV1:
    alias_normalized = (
        normalize_person_alias(locked.alias_original)
        if locked.alias_original is not None
        else None
    )
    aliases = (
        (
            ProjectionAliasSnapshotV1(
                schema_version=1,
                alias_original=locked.alias_original,
                alias_normalized=alias_normalized,
            ),
        )
        if locked.alias_original is not None and alias_normalized is not None
        else ()
    )
    override_snapshots = tuple(
        ProjectionOverrideSnapshotV1(
            schema_version=1,
            field_path=field_path,
            projected_value=ScalarOverrideValueV1(
                kind="string",
                string_value=value,
            ),
            scope=OverrideScope.RECORD,
            stable_target_key=stable_target_key,
            target_table=locked.candidate.source_table,
            target_pk=locked.candidate.source_id,
            document_key=None,
            period_id=None,
            relationship_key=None,
            locked=True,
        )
        for field_path, value in (
            (OverrideField.CANONICAL_IDENTITY_KEY, locked.canonical_identity_key),
            (OverrideField.CANONICAL_NAME, locked.canonical_name),
        )
    )
    before = ReviewProjectionSnapshotV1(
        schema_version=1,
        case_status=ReviewCaseStatus.REOPENED,
        scientific_status=ScientificStatus.PENDING,
        current_decision_id=None,
        identity=None,
        overrides=(),
    )
    after = ReviewProjectionSnapshotV1(
        schema_version=1,
        case_status=ReviewCaseStatus.RESOLVED,
        scientific_status=ScientificStatus.VALIDATED,
        current_decision_id=decision_id,
        identity=IdentityProjectionSnapshotV1(
            schema_version=1,
            canonical_identity_key=locked.canonical_identity_key,
            canonical_name=locked.canonical_name,
            identity_type=locked.identity_type,
            aliases=aliases,
        ),
        overrides=override_snapshots,
    )
    return IdentityDecisionPayloadV1(
        kind="identity",
        schema_version=1,
        canonical_identity_key=locked.canonical_identity_key,
        canonical_name=locked.canonical_name,
        identity_type=locked.identity_type,
        alias_original=locked.alias_original,
        alias_normalized=alias_normalized,
        projection_before=before,
        projection_after=after,
    )


def _new_review_item(
    candidate: BackfillCandidateV1,
    stable_target_key: str,
    item_id: UUID,
    decision_id: UUID | None,
    scope: ResolvedReviewScope,
) -> ReviewItem:
    target = candidate.stable_target
    return ReviewItem(
        id=item_id,
        case_type=candidate.case_type.value,
        stable_target_key=stable_target_key,
        target_table=candidate.source_table.value,
        target_pk=candidate.source_id,
        scope_faculty_id=scope.faculty_id,
        scope_career_id=scope.career_id,
        scope_resolution_reason=scope.resolution_reason,
        document_key=target.document_key,
        source_revision=target.source_revision,
        source_page=target.source_page,
        source_section=target.source_section,
        row_or_block_id=target.row_or_block_id,
        field_path=target.field_path.value
        if isinstance(target.field_path, OverrideField)
        else target.field_path,
        raw_value_sha256=target.raw_value_sha256,
        period_id=target.period_id,
        relationship_key=target.relationship_key,
        case_status=candidate.case_status.value,
        scientific_status=candidate.scientific_status.value,
        automatic_priority=0,
        manual_priority=None,
        possible_kpi_impact=candidate.possible_kpi_impact,
        current_decision_id=decision_id,
        version=1,
    )


def _assert_item_semantics(
    db: Session,
    item: ReviewItem,
    candidate: BackfillCandidateV1,
    stable_target_key: str,
    decision_id: UUID | None,
) -> None:
    scope = resolve_target_scope(db, candidate.source_table, candidate.source_id)
    expected = _new_review_item(
        candidate, stable_target_key, item.id, decision_id, scope
    )
    fields = (
        "case_type",
        "stable_target_key",
        "target_table",
        "target_pk",
        "scope_faculty_id",
        "scope_career_id",
        "scope_resolution_reason",
        "document_key",
        "source_revision",
        "source_page",
        "source_section",
        "row_or_block_id",
        "field_path",
        "raw_value_sha256",
        "period_id",
        "relationship_key",
        "case_status",
        "scientific_status",
        "possible_kpi_impact",
        "current_decision_id",
        "version",
    )
    if any(getattr(item, field) != getattr(expected, field) for field in fields):
        raise BackfillGateError("existing review item has semantic divergence")


def _backfill_event_command(
    *,
    event_type: AuditEventType,
    payload,
    item_id: UUID,
    stable_target_key: str,
    actor_identifier: str,
    occurred_at: datetime,
    correlation_id: UUID,
) -> AuditEventCommandV1:
    event_id = _uuid(
        "audit-event",
        f"{event_type.value}:{stable_target_key}:{payload.model_dump_json()}",
    )
    return AuditEventCommandV1(
        id=event_id,
        event_type=event_type,
        aggregate_type=_AUDIT_AGGREGATE_TYPE,
        aggregate_key=stable_target_key,
        review_item_id=item_id,
        actor_user_id=None,
        actor_identifier=actor_identifier,
        actor_capability=None,
        occurred_at=occurred_at,
        payload=payload,
        correlation_id=correlation_id,
        request_id=None,
        previous_event_id=None,
        corrects_event_id=None,
    )


def _append_event(
    db: Session,
    *,
    event_type: AuditEventType,
    payload,
    item_id: UUID,
    stable_target_key: str,
    actor_identifier: str,
    occurred_at: datetime,
    correlation_id: UUID,
) -> None:
    append_audit_event_at_current_head(
        db,
        _backfill_event_command(
            event_type=event_type,
            payload=payload,
            item_id=item_id,
            stable_target_key=stable_target_key,
            actor_identifier=actor_identifier,
            occurred_at=occurred_at,
            correlation_id=correlation_id,
        ),
    )


def _materialize_locked(
    db: Session,
    *,
    item: ReviewItem,
    locked: LockedDecisionImportV1,
    stable_target_key: str,
    decision_id: UUID,
    captured_at: datetime,
) -> tuple[int, int, int, int, int]:
    payload = _projection_payload(locked, stable_target_key, decision_id)
    decision = ReviewDecision(
        id=decision_id,
        review_item_id=item.id,
        sequence=1,
        decision_type=ReviewDecisionType.VALIDATED.value,
        decision_lifecycle=DecisionLifecycle.APPROVED.value,
        scope=OverrideScope.RECORD.value,
        payload_schema=_DECISION_PAYLOAD_SCHEMA,
        payload_version=1,
        payload=payload.model_dump(mode="json"),
        reason="Imported from an existing B1 identity lock",
        actor_type=ReviewActorType.LEGACY.value,
        actor_user_id=None,
        actor_identifier=locked.legacy_actor_identifier,
        actor_capability=None,
        decided_at=locked.legacy_decided_at,
        expected_case_version=1,
        previous_decision_id=None,
        corrects_decision_id=None,
        locks_projection=True,
    )
    db.add(decision)

    identity_id = _uuid("canonical-identity", locked.canonical_identity_key)
    identity_by_key = db.scalar(
        select(CanonicalIdentity).where(
            CanonicalIdentity.canonical_identity_key == locked.canonical_identity_key
        )
    )
    if identity_by_key is not None and identity_by_key.id != identity_id:
        raise BackfillGateError("canonical identity key has a non-deterministic row")
    identity = db.get(CanonicalIdentity, identity_id)
    created_identity = 0
    if identity is None:
        identity = CanonicalIdentity(
            id=identity_id,
            canonical_identity_key=locked.canonical_identity_key,
            identity_type=locked.identity_type.value,
            display_name=locked.canonical_name,
            status=CanonicalIdentityStatus.ACTIVE.value,
            origin=CanonicalIdentityOrigin.B1_LOCKED.value,
            created_by_decision_id=decision_id,
            superseded_by_id=None,
            version=1,
        )
        db.add(identity)
        created_identity = 1
    elif (
        identity.canonical_identity_key != locked.canonical_identity_key
        or identity.identity_type != locked.identity_type.value
        or identity.display_name != locked.canonical_name
        or identity.status != CanonicalIdentityStatus.ACTIVE.value
        or identity.origin != CanonicalIdentityOrigin.B1_LOCKED.value
    ):
        raise BackfillGateError("existing canonical identity has semantic divergence")

    alias = None
    created_alias = 0
    alias_normalized = payload.alias_normalized
    if locked.alias_original is not None and alias_normalized is not None:
        alias_id = _uuid("person-alias", alias_normalized)
        alias_by_value = db.scalar(
            select(PersonAlias).where(
                PersonAlias.alias_normalized == alias_normalized,
                PersonAlias.alias_class == PersonAliasClass.PERSON_NAME.value,
                PersonAlias.status == PersonAliasStatus.ACTIVE.value,
            )
        )
        if alias_by_value is not None and alias_by_value.id != alias_id:
            raise BackfillGateError("active alias has a non-deterministic row")
        alias = db.get(PersonAlias, alias_id)
        if alias is None:
            alias = PersonAlias(
                id=alias_id,
                alias_original=locked.alias_original,
                alias_normalized=alias_normalized,
                alias_class=PersonAliasClass.PERSON_NAME.value,
                canonical_identity_id=identity_id,
                decision_id=decision_id,
                scope=PersonAliasScope.GLOBAL_IDENTITY.value,
                status=PersonAliasStatus.ACTIVE.value,
                superseded_by_id=None,
                version=1,
            )
            db.add(alias)
            created_alias = 1
        elif alias.canonical_identity_id != identity_id:
            raise BackfillGateError("existing alias has semantic divergence")

    overrides: list[FieldOverride] = []
    for snapshot in payload.projection_after.overrides:
        override_id = _uuid(
            "field-override",
            f"{stable_target_key}:{snapshot.field_path.value}",
        )
        existing_context = db.scalar(
            select(FieldOverride).where(
                FieldOverride.stable_target_key == stable_target_key,
                FieldOverride.field_path == snapshot.field_path.value,
                FieldOverride.scope == OverrideScope.RECORD.value,
                FieldOverride.is_active.is_(True),
            )
        )
        if existing_context is not None and existing_context.id != override_id:
            raise BackfillGateError("active override has a non-deterministic row")
        override = db.get(FieldOverride, override_id)
        if override is not None:
            raise BackfillGateError("field override exists before its deterministic case")
        override = FieldOverride(
            id=override_id,
            review_item_id=item.id,
            decision_id=decision_id,
            stable_target_key=stable_target_key,
            target_table=locked.candidate.source_table.value,
            target_pk=locked.candidate.source_id,
            field_path=snapshot.field_path.value,
            value_schema=_OVERRIDE_VALUE_SCHEMA,
            value_version=1,
            projected_value=snapshot.projected_value.model_dump(mode="json"),
            scope=OverrideScope.RECORD.value,
            document_key=None,
            period_id=None,
            relationship_key=None,
            locked=True,
            is_active=True,
            valid_from=locked.legacy_decided_at,
            superseded_by_id=None,
            version=1,
        )
        overrides.append(override)
    db.add_all(overrides)
    db.flush()

    correlation_id = _uuid(
        "correlation",
        f"{locked.candidate.case_type.value}:{stable_target_key}",
    )
    sequence = 1
    if created_identity:
        _append_event(
            db,
            event_type=AuditEventType.IDENTITY_CREATED,
            payload=IdentityCreatedAuditPayloadV1(
                kind="identity_created",
                schema_version=1,
                canonical_identity_id=identity_id,
                canonical_identity_key=locked.canonical_identity_key,
                origin="b1_locked",
            ),
            item_id=item.id,
            stable_target_key=stable_target_key,
            actor_identifier=locked.legacy_actor_identifier,
            occurred_at=captured_at + timedelta(microseconds=sequence),
            correlation_id=correlation_id,
        )
        sequence += 1
    if alias is not None and created_alias:
        _append_event(
            db,
            event_type=AuditEventType.ALIAS_CREATED,
            payload=AliasCreatedAuditPayloadV1(
                kind="alias_created",
                schema_version=1,
                person_alias_id=alias.id,
                canonical_identity_id=identity_id,
                alias_normalized=alias.alias_normalized,
            ),
            item_id=item.id,
            stable_target_key=stable_target_key,
            actor_identifier=locked.legacy_actor_identifier,
            occurred_at=captured_at + timedelta(microseconds=sequence),
            correlation_id=correlation_id,
        )
        sequence += 1
    for override in overrides:
        _append_event(
            db,
            event_type=AuditEventType.OVERRIDE_CREATED,
            payload=OverrideCreatedAuditPayloadV1(
                kind="override_created",
                schema_version=1,
                field_override_id=override.id,
                field_path=OverrideField(override.field_path),
                stable_target_key=stable_target_key,
            ),
            item_id=item.id,
            stable_target_key=stable_target_key,
            actor_identifier=locked.legacy_actor_identifier,
            occurred_at=captured_at + timedelta(microseconds=sequence),
            correlation_id=correlation_id,
        )
        sequence += 1
    _append_event(
        db,
        event_type=AuditEventType.LOCKED_DECISION_IMPORTED,
        payload=LockedDecisionImportedAuditPayloadV1(
            kind="locked_decision_imported",
            schema_version=1,
            decision_id=decision_id,
            source_table=locked.candidate.source_table,
            source_id=locked.candidate.source_id,
            legacy_actor_identifier=locked.legacy_actor_identifier,
            legacy_decided_at=locked.legacy_decided_at,
        ),
        item_id=item.id,
        stable_target_key=stable_target_key,
        actor_identifier=locked.legacy_actor_identifier,
        occurred_at=captured_at + timedelta(microseconds=sequence),
        correlation_id=correlation_id,
    )
    return 1, len(overrides), created_identity, created_alias, (
        created_identity + created_alias + len(overrides) + 1
    )


def _locked_creation_owners(
    plan: B2B1BackfillPlanV1,
) -> tuple[dict[str, UUID], dict[str, tuple[UUID, str]]]:
    identity_owners: dict[str, UUID] = {}
    alias_owners: dict[str, tuple[UUID, str]] = {}
    for locked in plan.locked_decisions:
        stable_target_key = build_stable_target_key(locked.candidate.stable_target)
        object_key = f"{locked.candidate.case_type.value}:{stable_target_key}"
        decision_id = _uuid("review-decision", object_key)
        identity_owners.setdefault(locked.canonical_identity_key, decision_id)
        if locked.alias_original is not None:
            alias_normalized = normalize_person_alias(locked.alias_original)
            alias_owners.setdefault(
                alias_normalized,
                (decision_id, locked.alias_original),
            )
    return identity_owners, alias_owners


def _expected_backfill_audit_commands(
    plan: B2B1BackfillPlanV1,
) -> tuple[AuditEventCommandV1, ...]:
    locked_by_key = {
        (
            locked.candidate.case_type,
            build_stable_target_key(locked.candidate.stable_target),
        ): locked
        for locked in plan.locked_decisions
    }
    commands: list[AuditEventCommandV1] = []
    created_identity_keys: set[str] = set()
    created_alias_keys: set[str] = set()
    for candidate_index, candidate in enumerate(plan.candidates):
        stable_target_key = build_stable_target_key(candidate.stable_target)
        object_key = f"{candidate.case_type.value}:{stable_target_key}"
        item_id = _uuid("review-item", object_key)
        correlation_id = _uuid("correlation", object_key)
        locked = locked_by_key.get((candidate.case_type, stable_target_key))
        event_base = plan.captured_at + timedelta(microseconds=candidate_index * 10)
        commands.append(_backfill_event_command(
            event_type=AuditEventType.CASE_BACKFILLED,
            payload=CaseBackfilledAuditPayloadV1(
                kind="case_backfilled",
                schema_version=1,
                review_item_id=item_id,
                case_type=candidate.case_type,
                stable_target_key=stable_target_key,
            ),
            item_id=item_id,
            stable_target_key=stable_target_key,
            actor_identifier=(
                locked.legacy_actor_identifier if locked is not None else "b2b1-backfill"
            ),
            occurred_at=event_base,
            correlation_id=correlation_id,
        ))
        if locked is None:
            continue

        decision_id = _uuid("review-decision", object_key)
        payload = _projection_payload(locked, stable_target_key, decision_id)
        identity_id = _uuid("canonical-identity", locked.canonical_identity_key)
        sequence = 1
        if locked.canonical_identity_key not in created_identity_keys:
            commands.append(_backfill_event_command(
                event_type=AuditEventType.IDENTITY_CREATED,
                payload=IdentityCreatedAuditPayloadV1(
                    kind="identity_created",
                    schema_version=1,
                    canonical_identity_id=identity_id,
                    canonical_identity_key=locked.canonical_identity_key,
                    origin="b1_locked",
                ),
                item_id=item_id,
                stable_target_key=stable_target_key,
                actor_identifier=locked.legacy_actor_identifier,
                occurred_at=event_base + timedelta(microseconds=sequence),
                correlation_id=correlation_id,
            ))
            created_identity_keys.add(locked.canonical_identity_key)
            sequence += 1
        if payload.projection_after.identity is not None:
            for alias_snapshot in payload.projection_after.identity.aliases:
                if alias_snapshot.alias_normalized in created_alias_keys:
                    continue
                alias_id = _uuid("person-alias", alias_snapshot.alias_normalized)
                commands.append(_backfill_event_command(
                    event_type=AuditEventType.ALIAS_CREATED,
                    payload=AliasCreatedAuditPayloadV1(
                        kind="alias_created",
                        schema_version=1,
                        person_alias_id=alias_id,
                        canonical_identity_id=identity_id,
                        alias_normalized=alias_snapshot.alias_normalized,
                    ),
                    item_id=item_id,
                    stable_target_key=stable_target_key,
                    actor_identifier=locked.legacy_actor_identifier,
                    occurred_at=event_base + timedelta(microseconds=sequence),
                    correlation_id=correlation_id,
                ))
                created_alias_keys.add(alias_snapshot.alias_normalized)
                sequence += 1
        for override_snapshot in payload.projection_after.overrides:
            override_id = _uuid(
                "field-override",
                f"{stable_target_key}:{override_snapshot.field_path.value}",
            )
            commands.append(_backfill_event_command(
                event_type=AuditEventType.OVERRIDE_CREATED,
                payload=OverrideCreatedAuditPayloadV1(
                    kind="override_created",
                    schema_version=1,
                    field_override_id=override_id,
                    field_path=override_snapshot.field_path,
                    stable_target_key=stable_target_key,
                ),
                item_id=item_id,
                stable_target_key=stable_target_key,
                actor_identifier=locked.legacy_actor_identifier,
                occurred_at=event_base + timedelta(microseconds=sequence),
                correlation_id=correlation_id,
            ))
            sequence += 1
        commands.append(_backfill_event_command(
            event_type=AuditEventType.LOCKED_DECISION_IMPORTED,
            payload=LockedDecisionImportedAuditPayloadV1(
                kind="locked_decision_imported",
                schema_version=1,
                decision_id=decision_id,
                source_table=locked.candidate.source_table,
                source_id=locked.candidate.source_id,
                legacy_actor_identifier=locked.legacy_actor_identifier,
                legacy_decided_at=locked.legacy_decided_at,
            ),
            item_id=item_id,
            stable_target_key=stable_target_key,
            actor_identifier=locked.legacy_actor_identifier,
            occurred_at=event_base + timedelta(microseconds=sequence),
            correlation_id=correlation_id,
        ))
    return tuple(commands)


def _verify_backfill_audit(
    db: Session,
    plan: B2B1BackfillPlanV1,
) -> None:
    commands = _expected_backfill_audit_commands(plan)
    review_item_ids = {command.review_item_id for command in commands}
    actual_rows = tuple(db.scalars(
        select(AuditEvent).where(AuditEvent.review_item_id.in_(review_item_ids))
    ))
    actual_by_id = {row.id: row for row in actual_rows}
    if len(actual_by_id) != len(actual_rows) or set(actual_by_id) != {
        command.id for command in commands
    }:
        raise BackfillGateError("backfill audit has semantic divergence")

    previous_by_aggregate: dict[str, tuple[UUID, str]] = {}
    for command in commands:
        previous = previous_by_aggregate.get(command.aggregate_key)
        previous_event_id = previous[0] if previous is not None else None
        previous_event_hash = previous[1] if previous is not None else None
        row = actual_by_id[command.id]
        resolved = command.model_copy(update={
            "previous_event_id": previous_event_id,
            "occurred_at": row.occurred_at,
        })
        expected_hash = compute_audit_event_hash(resolved, previous_event_hash)
        if (
            row.event_type != resolved.event_type.value
            or row.aggregate_type != resolved.aggregate_type
            or row.aggregate_key != resolved.aggregate_key
            or row.review_item_id != resolved.review_item_id
            or row.actor_user_id != resolved.actor_user_id
            or row.actor_identifier != resolved.actor_identifier
            or row.actor_capability is not None
            or row.payload_schema != f"audit.{resolved.event_type.value}.v1"
            or row.payload_version != resolved.payload.schema_version
            or row.payload != resolved.payload.model_dump(mode="json")
            or row.correlation_id != resolved.correlation_id
            or row.request_id != resolved.request_id
            or row.previous_event_id != previous_event_id
            or row.corrects_event_id is not None
            or (
                row.previous_event_hash.strip()
                if row.previous_event_hash is not None
                else None
            ) != previous_event_hash
            or row.event_hash.strip() != expected_hash
        ):
            raise BackfillGateError("backfill audit has semantic divergence")
        previous_by_aggregate[command.aggregate_key] = (row.id, expected_hash)


def _verify_locked_existing(
    db: Session,
    *,
    item: ReviewItem,
    locked: LockedDecisionImportV1,
    stable_target_key: str,
    decision_id: UUID,
    identity_creator_decision_id: UUID,
    alias_creator: tuple[UUID, str] | None,
) -> None:
    decision = db.get(ReviewDecision, decision_id)
    expected_payload = _projection_payload(locked, stable_target_key, decision_id)
    if decision is None or (
        decision.review_item_id != item.id
        or decision.sequence != 1
        or decision.decision_type != ReviewDecisionType.VALIDATED.value
        or decision.decision_lifecycle != DecisionLifecycle.APPROVED.value
        or decision.scope != OverrideScope.RECORD.value
        or decision.payload_schema != _DECISION_PAYLOAD_SCHEMA
        or decision.payload_version != 1
        or decision.actor_type != ReviewActorType.LEGACY.value
        or decision.actor_user_id is not None
        or decision.actor_identifier != locked.legacy_actor_identifier
        or decision.actor_capability is not None
        or decision.decided_at != locked.legacy_decided_at
        or decision.expected_case_version != 1
        or decision.previous_decision_id is not None
        or decision.corrects_decision_id is not None
        or decision.locks_projection is not True
        or decision.reason != "Imported from an existing B1 identity lock"
        or decision.payload != expected_payload.model_dump(mode="json")
    ):
        raise BackfillGateError("existing locked decision has semantic divergence")
    identity_id = _uuid("canonical-identity", locked.canonical_identity_key)
    identity = db.get(CanonicalIdentity, identity_id)
    if identity is None or (
        identity.canonical_identity_key != locked.canonical_identity_key
        or identity.identity_type != locked.identity_type.value
        or identity.display_name != locked.canonical_name
        or identity.status != CanonicalIdentityStatus.ACTIVE.value
        or identity.origin != CanonicalIdentityOrigin.B1_LOCKED.value
        or identity.created_by_decision_id != identity_creator_decision_id
        or identity.superseded_by_id is not None
        or identity.version != 1
    ):
        raise BackfillGateError("existing canonical identity has semantic divergence")

    expected_aliases: tuple[ProjectionAliasSnapshotV1, ...] = (
        expected_payload.projection_after.identity.aliases
        if expected_payload.projection_after.identity is not None
        else ()
    )
    if bool(expected_aliases) != (alias_creator is not None):
        raise BackfillGateError("existing alias has semantic divergence")
    for snapshot in expected_aliases:
        expected_alias_id = _uuid("person-alias", snapshot.alias_normalized)
        alias = db.get(PersonAlias, expected_alias_id)
        if alias_creator is None:
            raise BackfillGateError("existing alias has semantic divergence")
        alias_creator_decision_id, alias_creator_original = alias_creator
        if alias is None:
            raise BackfillGateError("existing alias has semantic divergence")
        if (
            alias.id != expected_alias_id
            or alias.alias_original != alias_creator_original
            or alias.alias_normalized != snapshot.alias_normalized
            or alias.alias_class != PersonAliasClass.PERSON_NAME.value
            or alias.canonical_identity_id != identity_id
            or alias.decision_id != alias_creator_decision_id
            or alias.scope != PersonAliasScope.GLOBAL_IDENTITY.value
            or alias.status != PersonAliasStatus.ACTIVE.value
            or alias.superseded_by_id is not None
            or alias.version != 1
        ):
            raise BackfillGateError("existing alias has semantic divergence")

    expected_override_ids = {
        _uuid("field-override", f"{stable_target_key}:{field.value}")
        for field in (OverrideField.CANONICAL_IDENTITY_KEY, OverrideField.CANONICAL_NAME)
    }
    actual_overrides = tuple(db.scalars(
        select(FieldOverride).where(FieldOverride.review_item_id == item.id)
    ))
    if {override.id for override in actual_overrides} != expected_override_ids:
        raise BackfillGateError("existing locked decision overrides have semantic divergence")
    snapshots_by_field = {
        snapshot.field_path.value: snapshot
        for snapshot in expected_payload.projection_after.overrides
    }
    for override in actual_overrides:
        snapshot = snapshots_by_field.get(override.field_path)
        if snapshot is None or (
            override.review_item_id != item.id
            or override.decision_id != decision_id
            or override.stable_target_key != stable_target_key
            or override.target_table != locked.candidate.source_table.value
            or override.target_pk != locked.candidate.source_id
            or override.value_schema != _OVERRIDE_VALUE_SCHEMA
            or override.value_version != 1
            or override.projected_value != snapshot.projected_value.model_dump(mode="json")
            or override.scope != OverrideScope.RECORD.value
            or override.document_key is not None
            or override.period_id is not None
            or override.relationship_key is not None
            or override.locked is not True
            or override.is_active is not True
            or override.valid_from != locked.legacy_decided_at
            or override.superseded_by_id is not None
            or override.version != 1
        ):
            raise BackfillGateError("existing locked decision overrides have semantic divergence")


def apply_backfill_plan(
    db: Session,
    plan: B2B1BackfillPlanV1,
    approved_plan_sha256: str,
    expected_invariants_sha256: str,
) -> B2B1BackfillApplyResultV1:
    plan = B2B1BackfillPlanV1.model_validate(plan)
    _validate_apply_context(db)
    actual_plan_hash = backfill_plan_sha256(plan)
    if approved_plan_sha256 != actual_plan_hash:
        raise BackfillGateError("approved plan hash does not match the plan")
    if expected_invariants_sha256 != plan.invariant_snapshot_sha256:
        raise BackfillGateError("expected invariant hash does not match the plan")

    before = _capture_current_invariants(db)
    before_hash = snapshot_sha256(before)
    if before_hash != expected_invariants_sha256:
        raise BackfillGateError("current invariant hash differs from the approved snapshot")
    rebuilt = build_backfill_plan(db, plan.captured_at)
    if backfill_plan_sha256(rebuilt) != actual_plan_hash:
        raise BackfillGateError("rebuilt plan differs from the approved plan")
    if rebuilt.hard_blockers:
        raise BackfillGateError("backfill plan contains blockers")

    locked_by_key = {
        (
            locked.candidate.case_type,
            build_stable_target_key(locked.candidate.stable_target),
        ): locked
        for locked in plan.locked_decisions
    }
    identity_owners, alias_owners = _locked_creation_owners(plan)
    counters = {
        "review_items": 0,
        "decisions": 0,
        "overrides": 0,
        "identities": 0,
        "aliases": 0,
        "audit_events": 0,
    }
    for candidate_index, candidate in enumerate(plan.candidates):
        stable_target_key = build_stable_target_key(candidate.stable_target)
        object_key = f"{candidate.case_type.value}:{stable_target_key}"
        item_id = _uuid("review-item", object_key)
        locked = locked_by_key.get((candidate.case_type, stable_target_key))
        decision_id = (
            _uuid("review-decision", object_key) if locked is not None else None
        )
        event_base = plan.captured_at + timedelta(microseconds=candidate_index * 10)
        logical_items = tuple(db.scalars(
            select(ReviewItem).where(
                ReviewItem.case_type == candidate.case_type.value,
                ReviewItem.stable_target_key == stable_target_key,
            ).limit(2)
        ))
        if any(existing.id != item_id for existing in logical_items):
            raise BackfillGateError("logical review case has a non-deterministic row")
        item = db.get(ReviewItem, item_id)
        if item is not None:
            _assert_item_semantics(
                db,
                item,
                candidate,
                stable_target_key,
                decision_id,
            )
            if locked is not None and decision_id is not None:
                _verify_locked_existing(
                    db,
                    item=item,
                    locked=locked,
                    stable_target_key=stable_target_key,
                    decision_id=decision_id,
                    identity_creator_decision_id=identity_owners[
                        locked.canonical_identity_key
                    ],
                    alias_creator=(
                        alias_owners.get(normalize_person_alias(locked.alias_original))
                        if locked.alias_original is not None
                        else None
                    ),
                )
            continue

        scope = resolve_target_scope(
            db,
            candidate.source_table,
            candidate.source_id,
        )
        item = _new_review_item(
            candidate,
            stable_target_key,
            item_id,
            decision_id,
            scope,
        )
        db.add(item)
        db.flush()
        _append_event(
            db,
            event_type=AuditEventType.CASE_BACKFILLED,
            payload=CaseBackfilledAuditPayloadV1(
                kind="case_backfilled",
                schema_version=1,
                review_item_id=item_id,
                case_type=candidate.case_type,
                stable_target_key=stable_target_key,
            ),
            item_id=item_id,
            stable_target_key=stable_target_key,
            actor_identifier=(
                locked.legacy_actor_identifier if locked is not None else "b2b1-backfill"
            ),
            occurred_at=event_base,
            correlation_id=_uuid("correlation", object_key),
        )
        counters["review_items"] += 1
        counters["audit_events"] += 1
        if locked is not None and decision_id is not None:
            decision_counts = _materialize_locked(
                db,
                item=item,
                locked=locked,
                stable_target_key=stable_target_key,
                decision_id=decision_id,
                captured_at=event_base,
            )
            counters["decisions"] += decision_counts[0]
            counters["overrides"] += decision_counts[1]
            counters["identities"] += decision_counts[2]
            counters["aliases"] += decision_counts[3]
            counters["audit_events"] += decision_counts[4]

    db.flush()
    _verify_backfill_audit(db, plan)
    after = _capture_current_invariants(db)
    compare_b2b1_invariants(before, after)
    after_hash = snapshot_sha256(after)
    return B2B1BackfillApplyResultV1(
        schema_version=1,
        plan_sha256=actual_plan_hash,
        before_invariants_sha256=before_hash,
        after_invariants_sha256=after_hash,
        created_review_items=counters["review_items"],
        created_decisions=counters["decisions"],
        created_overrides=counters["overrides"],
        created_identities=counters["identities"],
        created_aliases=counters["aliases"],
        created_audit_events=counters["audit_events"],
        eligible_products_before=before.eligible_products,
        eligible_products_after=after.eligible_products,
    )
