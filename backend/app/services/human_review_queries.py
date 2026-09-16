from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
import re
from typing import Any
from urllib.parse import unquote
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import String, and_, cast, func, or_, select, tuple_
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.models.entities import (
    ExternalResearcher,
    ImportedOcrTrace,
    ImportJob,
    PersonRole,
    ResearchEntity,
    ScientificProduction,
    ScientificProductionAuthor,
    User,
)
from app.models.human_review_audit import AuditEvent
from app.models.human_review_core import ReviewDecision, ReviewItem
from app.models.human_review_enums import (
    AuditEventType,
    BackfillSourceMembership,
    DecisionLifecycle,
    OverrideScope,
    ReviewCaseStatus,
    ReviewCaseType,
    ReviewTargetTable,
)
from app.models.human_review_projection import FieldOverride
from app.schemas.human_review import DecisionPayloadV1, ScalarOverrideValueV1
from app.schemas.human_review_api import (
    AuditTimelineResponse,
    CASE_ACTION_DECISION_TYPES,
    DecisionAction,
    EvidenceResponse,
    RelatedReviewEntity,
    RelatedReviewItem,
    RelatedReviewResponse,
    ReviewCaseDetail,
    ReviewQueueItem,
    ReviewQueueQuery,
    ReviewQueueResponse,
)
from app.schemas.human_review_operations import AuditPayloadV1
from app.services.human_review_state import HumanReviewDomainError
from app.services.human_review_scope import (
    assert_review_item_scope,
    review_scope_predicate,
)
from app.services.human_review_demo_evidence import (
    DEMO_EVIDENCE_FILENAME,
    DEMO_EVIDENCE_PAGES,
    DEMO_EVIDENCE_SHA256,
    DEMO_EVIDENCE_SIZE,
    DEMO_EVIDENCE_SOURCE,
)
from app.services.human_review_counterparts import public_counterpart_options
from app.services.human_review_projection import (
    EffectiveHumanProjectionSource,
    load_identity_projection,
)
from app.services.human_review_related import (
    RelatedEntityOutOfScopeError,
    TargetRef,
    find_related_target_refs,
    resolve_related_entity,
)
from app.services.human_review_targets import (
    REVIEW_CASE_TARGETS as _CASE_TARGETS,
    REVIEW_TARGET_MODELS as _TARGET_MODELS,
    raw_value_sha256,
)
from app.services.import_batching import dropbox_document_key, dropbox_fingerprint


MAX_EVIDENCE_FRAGMENT_CHARS = 4000
MAX_EVIDENCE_PDF_BYTES = 20 * 1024 * 1024


def _is_allowed_evidence_source(source_type: str) -> bool:
    return source_type == "PROGRESS_PDF" or (
        settings.demo_mode and source_type == DEMO_EVIDENCE_SOURCE
    )


@dataclass(frozen=True, slots=True)
class ResolvedEvidence:
    """Validated public metadata paired with a private storage locator."""

    public: EvidenceResponse
    source_path: str
    expected_size: int | None = None
    expected_page_count: int | None = None
    expected_content_hash: str | None = None


@dataclass(frozen=True, slots=True)
class _RelatedEvidenceSignatures:
    is_valid: bool
    sources: tuple[tuple[str, str, int, str], ...]


@dataclass(frozen=True, slots=True)
class _RelatedEvidenceTrace:
    import_job_id: int
    source_filename: str
    source_path: str
    parsed_payload: object


@dataclass(frozen=True, slots=True)
class _RelatedListContext:
    targets: Mapping[tuple[ReviewTargetTable, int], Any]
    import_jobs: Mapping[tuple[ReviewTargetTable, int], ImportJob | None]
    decisions: Mapping[UUID, ReviewDecision]
    identities: Mapping[str, tuple[str, bool] | None]
    evidence: Mapping[UUID, EvidenceResponse | None]


@dataclass(frozen=True, slots=True)
class _QueueItemContext:
    document_id: int | None = None
    document_name: str | None = None
    detected_value: str | None = None
    normalized_value: str | None = None
    canonical_value: str | None = None


class ReviewCaseNotFoundError(HumanReviewDomainError):
    code = "REVIEW_CASE_NOT_FOUND"

    def __init__(self, correlation_id: UUID) -> None:
        super().__init__("Review case was not found", correlation_id=correlation_id)


class HumanReviewQueryInternalError(HumanReviewDomainError):
    code = "HUMAN_REVIEW_INTERNAL_ERROR"

    def __init__(self, correlation_id: UUID) -> None:
        super().__init__("Request could not be completed", correlation_id=correlation_id)


class EvidenceUnavailableError(HumanReviewDomainError):
    code = "EVIDENCE_UNAVAILABLE"

    def __init__(self, correlation_id: UUID) -> None:
        super().__init__("Evidence is unavailable", correlation_id=correlation_id)


_TARGET_VALUES: dict[ReviewTargetTable, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    ReviewTargetTable.PERSON_ROLES: (("raw_value", "raw_name", "canonical_name"), ("normalized_value", "normalized_name"), ("canonical_name",)),
    ReviewTargetTable.SCIENTIFIC_PRODUCTION_AUTHORS: (("raw_author_name", "normalized_author_name"), ("normalized_author_name",), ("canonical_name",)),
    ReviewTargetTable.SCIENTIFIC_PRODUCTIONS: (("raw_value", "raw_title", "title"), ("normalized_value", "normalized_title"), ("title",)),
    ReviewTargetTable.RESEARCH_ENTITIES: (("raw_value", "name", "code", "director_name"), ("normalized_value", "normalized_director_name"), ("director_name",)),
    ReviewTargetTable.EXTERNAL_RESEARCHERS: (("raw_value", "raw_name", "full_name"), ("normalized_value", "normalized_name"), ("full_name",)),
}
_MEMBERSHIP_BY_CASE: dict[ReviewCaseType, BackfillSourceMembership] = {
    ReviewCaseType.PERSON_IDENTITY: BackfillSourceMembership.CANONICAL_PENDING,
    ReviewCaseType.AUTHOR_IDENTITY: BackfillSourceMembership.CANONICAL_PENDING,
    ReviewCaseType.PRODUCT: BackfillSourceMembership.PRODUCT_PENDING,
    ReviewCaseType.PROJECT_DIRECTOR_RELATION: BackfillSourceMembership.DIRECTOR_RELATION_PENDING,
    ReviewCaseType.EXTERNAL_IDENTITY: BackfillSourceMembership.EXTERNAL_PENDING,
    ReviewCaseType.POSSIBLE_DUPLICATE: BackfillSourceMembership.POSSIBLE_MATCH,
}
_DECISION_PAYLOAD = TypeAdapter(DecisionPayloadV1)
_AUDIT_PAYLOAD = TypeAdapter(AuditPayloadV1)
_AUDIT_KIND_BY_EVENT = {
    AuditEventType.CASE_BACKFILLED: "case_backfilled",
    AuditEventType.LOCKED_DECISION_IMPORTED: "locked_decision_imported",
    AuditEventType.IDENTITY_CREATED: "identity_created",
    AuditEventType.ALIAS_CREATED: "alias_created",
    AuditEventType.OVERRIDE_CREATED: "override_created",
    AuditEventType.CAPABILITY_ASSIGNED: "capability_changed",
    AuditEventType.CAPABILITY_REVOKED: "capability_changed",
    AuditEventType.AUDIT_CORRECTED: "audit_correction",
    AuditEventType.FUNCTIONAL_REVERSION: "functional_reversal",
    AuditEventType.SCIENTIFIC_DECISION_APPLIED: "scientific_decision_applied",
}
_AUDIT_SUMMARIES = {event: event.value.replace("_", " ").capitalize() for event in AuditEventType}


class HumanReviewQueryService:
    """Closed, read-only projections of human-review persistence."""

    def __init__(
        self,
        db: Session,
        *,
        actor: User | None = None,
        correlation_id: UUID | None = None,
    ) -> None:
        self._db = db
        self._actor = actor
        self._correlation_id = correlation_id or uuid4()

    def list_cases(self, query: ReviewQueueQuery) -> ReviewQueueResponse:
        query = ReviewQueueQuery.model_validate(query)
        predicates = self._queue_predicates(query)
        with self._db.no_autoflush:
            total = int(self._db.scalar(select(func.count()).select_from(ReviewItem).where(*predicates)) or 0)
            facets = self._queue_facets(predicates)
            rows = tuple(self._db.scalars(
                select(ReviewItem)
                .where(*predicates)
                .order_by(
                    ReviewItem.manual_priority.desc().nulls_last(),
                    ReviewItem.automatic_priority.desc(),
                    ReviewItem.created_at.asc(),
                    ReviewItem.id.asc(),
                )
                .offset((query.page - 1) * query.page_size)
                .limit(query.page_size)
            ))
            queue_context = self._queue_context(rows)
        return ReviewQueueResponse(
            items=tuple(self._queue_item(row, queue_context.get(row.id)) for row in rows),
            total=total,
            page=query.page,
            page_size=query.page_size,
            facets=facets,
            correlation_id=self._correlation_id,
        )

    def get_case(self, review_item_id: UUID) -> ReviewCaseDetail:
        with self._db.no_autoflush:
            item = self._db.get(ReviewItem, review_item_id)
            if item is None:
                raise ReviewCaseNotFoundError(self._correlation_id)
            self._assert_scope(item)
            target_table, target = self._target_for_detail(item)
            detected, normalized, fallback_canonical = self._target_values(target_table, target)
            if raw_value_sha256(detected) != item.raw_value_sha256:
                raise HumanReviewQueryInternalError(self._correlation_id)
            decision, canonical, identity_locked = self._current_decision(
                item, fallback_canonical
            )
            overrides = self._safe_overrides(item)
            document_id = self._target_import_job_id(target)
            import_job = self._db.get(ImportJob, document_id) if document_id is not None else None
            try:
                evidence = self._resolve_evidence(item, target).public
            except EvidenceUnavailableError:
                evidence = None
        base = self._queue_item(
            item,
            _QueueItemContext(
                document_id=document_id,
                document_name=None if import_job is None else import_job.filename,
                detected_value=detected[:MAX_EVIDENCE_FRAGMENT_CHARS],
                normalized_value=None if normalized is None else normalized[:MAX_EVIDENCE_FRAGMENT_CHARS],
                canonical_value=None if canonical is None else canonical[:MAX_EVIDENCE_FRAGMENT_CHARS],
            ),
        ).model_dump(exclude={"detected_value", "normalized_value", "canonical_value"})
        return ReviewCaseDetail(
            **base,
            target_table=target_table,
            target_pk=item.target_pk,
            field_path=item.field_path,
            detected_value=detected[:MAX_EVIDENCE_FRAGMENT_CHARS],
            normalized_value=(
                None
                if normalized is None
                else normalized[:MAX_EVIDENCE_FRAGMENT_CHARS]
            ),
            canonical_value=(
                None
                if canonical is None
                else canonical[:MAX_EVIDENCE_FRAGMENT_CHARS]
            ),
            current_decision_id=None if decision is None else decision.id,
            overrides=overrides,
            effective_memberships=self._effective_memberships(
                item, identity_locked=identity_locked
            ),
            counterpart_options=public_counterpart_options(self._db, item),
            evidence_summary=self._evidence_summary(
                target,
                import_job=import_job,
                evidence=evidence,
            ),
        )

    def get_related(
        self,
        review_item_id: UUID,
        *,
        limit: int = 50,
    ) -> RelatedReviewResponse:
        bounded = min(max(limit, 1), 50)
        with self._db.no_autoflush:
            anchor = self._get_item_or_raise(review_item_id)
            try:
                entity = resolve_related_entity(self._db, anchor)
            except RelatedEntityOutOfScopeError:
                raise ReviewCaseNotFoundError(self._correlation_id) from None
            except (LookupError, ValueError) as error:
                raise HumanReviewQueryInternalError(self._correlation_id) from error
            except Exception as error:
                raise HumanReviewQueryInternalError(self._correlation_id) from error
            targets = find_related_target_refs(self._db, entity, limit=200)
            rows = self._select_pending_related(targets, limit=bounded + 1)
            selected = rows[:bounded]
            context = self._related_list_context(selected)
            items = tuple(
                self._related_item(row, context)
                for row in selected
            )
        truncated = len(rows) > bounded
        return RelatedReviewResponse(
            entity=RelatedReviewEntity(
                public_type=entity.public_type,
                public_id=entity.public_id,
                display_name=entity.display_name,
            ),
            items=items,
            total_pending=len(items) + int(truncated),
            truncated=truncated,
            correlation_id=self._correlation_id,
        )

    def get_evidence(self, review_item_id: UUID) -> ResolvedEvidence:
        """Resolve existing evidence without exposing its storage locator."""

        with self._db.no_autoflush:
            item = self._db.get(ReviewItem, review_item_id)
            if item is None:
                raise ReviewCaseNotFoundError(self._correlation_id)
            self._assert_scope(item)
            try:
                _target_table, target = self._target_for_detail(item)
            except HumanReviewQueryInternalError as error:
                raise EvidenceUnavailableError(self._correlation_id) from error
            return self._resolve_evidence(item, target)

    def get_audit(self, review_item_id: UUID, page: int, page_size: int) -> AuditTimelineResponse:
        if not isinstance(page, int) or not isinstance(page_size, int) or isinstance(page, bool) or isinstance(page_size, bool) or page < 1 or not 1 <= page_size <= 100:
            raise HumanReviewDomainError("page and page_size are invalid", correlation_id=self._correlation_id)
        with self._db.no_autoflush:
            item = self._db.get(ReviewItem, review_item_id)
            if item is None:
                raise ReviewCaseNotFoundError(self._correlation_id)
            self._assert_scope(item)
            predicates = (AuditEvent.review_item_id == review_item_id,)
            total = int(self._db.scalar(select(func.count()).select_from(AuditEvent).where(*predicates)) or 0)
            events = tuple(self._db.scalars(
                select(AuditEvent)
                .where(*predicates)
                .order_by(AuditEvent.occurred_at.asc(), AuditEvent.id.asc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ))
        return AuditTimelineResponse(
            items=tuple(self._audit_item(event) for event in events),
            page=page,
            page_size=page_size,
            total=total,
            correlation_id=self._correlation_id,
        )

    def _queue_predicates(self, query: ReviewQueueQuery) -> tuple[object, ...]:
        if query.created_from is not None and query.created_to is not None and query.created_from > query.created_to:
            raise HumanReviewDomainError("created_from must not be after created_to", correlation_id=self._correlation_id)
        predicates: list[object] = []
        if self._actor is not None:
            predicates.append(review_scope_predicate(self._actor))
        if query.statuses:
            predicates.append(ReviewItem.case_status.in_([status.value for status in query.statuses]))
        if query.case_types:
            predicates.append(ReviewItem.case_type.in_([case_type.value for case_type in query.case_types]))
        if query.period_id is not None:
            predicates.append(ReviewItem.period_id == query.period_id)
        if query.document_id is not None:
            predicates.append(self._document_id_predicate(query.document_id))
        if query.source_revision is not None:
            predicates.append(ReviewItem.source_revision == query.source_revision)
        if query.created_from is not None:
            predicates.append(ReviewItem.created_at >= query.created_from)
        if query.created_to is not None:
            predicates.append(ReviewItem.created_at <= query.created_to)
        if query.q is not None:
            pattern = self._escaped_like_pattern(query.q)
            search_predicates: list[object] = [
                cast(ReviewItem.id, String).ilike(pattern, escape="\\"),
                ReviewItem.source_revision.ilike(pattern, escape="\\"),
                ReviewItem.source_section.ilike(pattern, escape="\\"),
                ReviewItem.document_key.ilike(pattern, escape="\\"),
                ReviewItem.stable_target_key.ilike(pattern, escape="\\"),
                self._public_queue_text_predicate(pattern),
            ]
            document_match = re.fullmatch(r"(?:documento\s*)?(\d+)", query.q.strip(), re.IGNORECASE)
            if document_match is not None:
                document_id = int(document_match.group(1))
                if document_id > 0:
                    search_predicates.append(self._document_id_predicate(document_id))
            predicates.append(or_(*search_predicates))
        return tuple(predicates)

    @staticmethod
    def _escaped_like_pattern(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"

    @staticmethod
    def _document_id_predicate(document_id: int) -> object:
        return or_(*(
            and_(
                ReviewItem.target_table == table.value,
                ReviewItem.target_pk.in_(
                    select(model.id).where(model.import_job_id == document_id)
                ),
            )
            for table, model in _TARGET_MODELS.items()
        ))

    @staticmethod
    def _public_queue_text_predicate(pattern: str) -> object:
        table_predicates: list[object] = []
        document_name_match = select(ImportJob.id).where(ImportJob.filename.ilike(pattern, escape="\\"))
        for table, model in _TARGET_MODELS.items():
            text_predicates = [
                cast(getattr(model, field), String).ilike(pattern, escape="\\")
                for field_group in _TARGET_VALUES[table]
                for field in field_group
                if hasattr(model, field)
            ]
            if hasattr(model, "import_job_id"):
                text_predicates.append(model.import_job_id.in_(document_name_match))
            if not text_predicates:
                continue
            table_predicates.append(and_(
                ReviewItem.target_table == table.value,
                ReviewItem.target_pk.in_(select(model.id).where(or_(*text_predicates))),
            ))
        return or_(*table_predicates)

    def _get_item_or_raise(self, review_item_id: UUID) -> ReviewItem:
        item = self._db.get(ReviewItem, review_item_id)
        if item is None:
            raise ReviewCaseNotFoundError(self._correlation_id)
        self._assert_scope(item)
        return item

    def _assert_scope(self, item: ReviewItem) -> None:
        if self._actor is not None:
            assert_review_item_scope(
                self._actor,
                item,
                correlation_id=self._correlation_id,
            )

    def _select_pending_related(
        self,
        targets: Sequence[TargetRef],
        *,
        limit: int,
    ) -> tuple[ReviewItem, ...]:
        if not targets:
            return ()
        target_pairs = tuple(
            (target.target_table.value, target.target_pk)
            for target in targets
        )
        readable_cases = or_(*(
            and_(
                ReviewItem.case_type == case_type.value,
                ReviewItem.target_table.in_(tuple(
                    target.value
                    for target in sorted(tables, key=lambda item: item.value)
                )),
            )
            for case_type, tables in _CASE_TARGETS.items()
        ))
        statement = (
            select(ReviewItem)
            .where(
                tuple_(ReviewItem.target_table, ReviewItem.target_pk).in_(target_pairs),
                ReviewItem.case_status.in_((
                    ReviewCaseStatus.PENDING.value,
                    ReviewCaseStatus.REOPENED.value,
                )),
                readable_cases,
                *(
                    (review_scope_predicate(self._actor),)
                    if self._actor is not None
                    else ()
                ),
            )
            .order_by(
                ReviewItem.manual_priority.desc().nulls_last(),
                ReviewItem.automatic_priority.desc(),
                ReviewItem.created_at.asc(),
                ReviewItem.id.asc(),
            )
            .limit(limit)
        )
        return tuple(self._db.scalars(statement))

    def _related_list_context(
        self,
        rows: Sequence[ReviewItem],
    ) -> _RelatedListContext:
        targets = self._prefetch_related_targets(rows)
        import_job_ids: dict[tuple[ReviewTargetTable, int], int | None] = {
            key: self._target_import_job_id(target)
            for key, target in targets.items()
        }
        job_ids = {
            job_id for job_id in import_job_ids.values() if job_id is not None
        }
        jobs_by_id = {
            job.id: job
            for job in self._db.scalars(
                select(ImportJob).where(ImportJob.id.in_(job_ids))
            )
        } if job_ids else {}
        import_jobs = {
            key: jobs_by_id.get(job_id) if job_id is not None else None
            for key, job_id in import_job_ids.items()
        }
        decision_ids = {
            item.current_decision_id
            for item in rows
            if item.current_decision_id is not None
        }
        decisions = {
            decision.id: decision
            for decision in self._db.scalars(
                select(ReviewDecision).where(ReviewDecision.id.in_(decision_ids))
            )
        } if decision_ids else {}
        identity_keys = {
            item.stable_target_key
            for item in rows
            if item.current_decision_id is not None
            and item.case_type in {
                ReviewCaseType.PERSON_IDENTITY.value,
                ReviewCaseType.AUTHOR_IDENTITY.value,
                ReviewCaseType.POSSIBLE_DUPLICATE.value,
            }
        }
        identities: dict[str, tuple[str, bool] | None] = {}
        if identity_keys:
            source = EffectiveHumanProjectionSource(self._db)
            with source.read_scope():
                source.prefetch_stable_target_keys(identity_keys)
                for key in identity_keys:
                    projection = source.for_stable_target_key(key)
                    identities[key] = (
                        None
                        if projection is None
                        or projection.canonical_name is None
                        or projection.identity_type is None
                        else (projection.canonical_name, True)
                    )
        signatures = self._prefetch_related_evidence_signatures(jobs_by_id)
        evidence: dict[UUID, EvidenceResponse | None] = {}
        for item in rows:
            key = self._related_target_key(item)
            target = targets.get(key)
            if target is None:
                raise HumanReviewQueryInternalError(self._correlation_id)
            evidence[item.id] = self._related_evidence(
                item,
                target,
                import_jobs.get(key),
                signatures,
            )
        return _RelatedListContext(
            targets=targets,
            import_jobs=import_jobs,
            decisions=decisions,
            identities=identities,
            evidence=evidence,
        )

    def _prefetch_related_targets(
        self,
        rows: Sequence[ReviewItem],
    ) -> dict[tuple[ReviewTargetTable, int], Any]:
        ids_by_table: dict[ReviewTargetTable, set[int]] = {}
        for item in rows:
            table, target_pk = self._related_target_key(item)
            ids_by_table.setdefault(table, set()).add(target_pk)
        targets: dict[tuple[ReviewTargetTable, int], Any] = {}
        for table, target_ids in ids_by_table.items():
            statement = select(_TARGET_MODELS[table]).where(
                _TARGET_MODELS[table].id.in_(target_ids)
            )
            if table is ReviewTargetTable.SCIENTIFIC_PRODUCTION_AUTHORS:
                statement = statement.options(
                    selectinload(ScientificProductionAuthor.production)
                )
            for target in self._db.scalars(statement):
                targets[(table, target.id)] = target
        if len(targets) != sum(len(ids) for ids in ids_by_table.values()):
            raise HumanReviewQueryInternalError(self._correlation_id)
        return targets

    def _related_target_key(
        self,
        item: ReviewItem,
    ) -> tuple[ReviewTargetTable, int]:
        try:
            case_type = ReviewCaseType(item.case_type)
            table = ReviewTargetTable(item.target_table)
        except ValueError as error:
            raise HumanReviewQueryInternalError(self._correlation_id) from error
        if (
            table not in _CASE_TARGETS.get(case_type, frozenset())
            or not isinstance(item.target_pk, int)
            or isinstance(item.target_pk, bool)
        ):
            raise HumanReviewQueryInternalError(self._correlation_id)
        return table, item.target_pk

    def _prefetch_related_evidence_signatures(
        self,
        jobs_by_id: Mapping[int, ImportJob],
    ) -> dict[int, _RelatedEvidenceSignatures]:
        if not jobs_by_id:
            return {}
        statement = (
            select(
                ImportedOcrTrace.import_job_id,
                ImportedOcrTrace.source_filename,
                ImportedOcrTrace.source_path,
                ImportedOcrTrace.parsed_payload,
            )
            .where(ImportedOcrTrace.import_job_id.in_(jobs_by_id))
            .execution_options(stream_results=True, yield_per=1)
        )
        grouped: dict[int, list[tuple[str, str, int, str]]] = {}
        invalid_jobs: set[int] = set()
        rows = self._db.execute(statement)
        try:
            for import_job_id, source_filename, source_path, parsed_payload in rows:
                sources = grouped.setdefault(import_job_id, [])
                if import_job_id in invalid_jobs or len(sources) == 2:
                    continue
                source = self._validated_trace_source(
                    _RelatedEvidenceTrace(
                        import_job_id=import_job_id,
                        source_filename=source_filename,
                        source_path=source_path,
                        parsed_payload=parsed_payload,
                    ),
                    jobs_by_id[import_job_id],
                )
                if source is None:
                    invalid_jobs.add(import_job_id)
                elif source not in sources:
                    sources.append(source)
        finally:
            rows.close()
        return {
            job_id: _RelatedEvidenceSignatures(
                is_valid=job_id not in invalid_jobs,
                sources=tuple(sources),
            )
            for job_id, sources in grouped.items()
        }

    def _related_evidence(
        self,
        item: ReviewItem,
        target: Any,
        job: ImportJob | None,
        signatures: Mapping[int, _RelatedEvidenceSignatures],
    ) -> EvidenceResponse | None:
        detected = self._evidence_detected_value(target)
        if detected is None or raw_value_sha256(detected) != item.raw_value_sha256:
            return None
        production = getattr(target, "production", None)
        target_document_key = self._first_nonblank(
            None if job is None else job.document_key,
            None if job is None else job.filename,
            getattr(target, "source_file", None),
            getattr(production, "source_file", None),
        )
        target_revision = self._first_nonblank(
            None if job is None else job.source_rev,
            getattr(target, "parser_version", None),
            getattr(production, "parser_version", None),
        )
        target_page = self._target_source_page(target)
        target_section = self._target_source_section(target)
        target_locator = self._target_row_or_block_id(target)
        if (
            job is None
            or target_document_key != item.document_key
            or target_revision != item.source_revision
            or target_page != item.source_page
            or target_section != item.source_section
            or target_locator != item.row_or_block_id
            or not self._safe_locator(item.row_or_block_id)
            or (
                item.source_page is not None
                and (
                    not isinstance(item.source_page, int)
                    or isinstance(item.source_page, bool)
                    or item.source_page < 1
                )
            )
            or not isinstance(item.source_section, str)
            or not item.source_section.strip()
            or not _is_allowed_evidence_source(job.source_type)
            or job.status != "SUCCESS"
            or job.is_current is not True
            or job.document_key != item.document_key
            or job.source_rev != item.source_revision
            or not isinstance(job.page_count, int)
            or isinstance(job.page_count, bool)
            or job.page_count < 1
            or (
                item.source_page is not None
                and item.source_page > job.page_count
            )
        ):
            return None
        batch = signatures.get(job.id)
        if batch is None or not batch.is_valid or len(batch.sources) != 1:
            return None
        _source_path, document_name, _expected_size, _content_hash = (
            batch.sources[0]
        )
        return EvidenceResponse(
            document_name=document_name,
            page=item.source_page,
            section=item.source_section,
            locator=item.row_or_block_id,
            fragment=detected[:MAX_EVIDENCE_FRAGMENT_CHARS],
            stream_path=f"human-review/cases/{item.id}/evidence",
            correlation_id=self._correlation_id,
        )

    def _related_item(
        self,
        item: ReviewItem,
        context: _RelatedListContext,
    ) -> RelatedReviewItem:
        target_table, target_pk = self._related_target_key(item)
        target = context.targets[(target_table, target_pk)]
        detected, normalized, fallback_canonical = self._target_values(
            target_table,
            target,
        )
        if raw_value_sha256(detected) != item.raw_value_sha256:
            raise HumanReviewQueryInternalError(self._correlation_id)
        decision, canonical, _identity_locked = self._current_decision(
            item,
            fallback_canonical,
            decision_cache=context.decisions,
            identity_cache=context.identities,
        )
        import_job = context.import_jobs[(target_table, target_pk)]
        evidence = context.evidence[item.id]
        case_type = ReviewCaseType(item.case_type)
        allowed_actions: tuple[DecisionAction, ...] = tuple(
            action
            for matrix_case, action in CASE_ACTION_DECISION_TYPES
            if matrix_case is case_type
        )
        return RelatedReviewItem(
            case_id=item.id,
            case_type=case_type,
            case_status=ReviewCaseStatus(item.case_status),
            scientific_status=item.scientific_status,
            version=item.version,
            current_decision_id=None if decision is None else decision.id,
            detected_value=detected,
            normalized_value=normalized,
            canonical_value=canonical,
            possible_kpi_impact=item.possible_kpi_impact,
            allowed_actions=allowed_actions,
            evidence_summary=self._evidence_summary(
                target,
                import_job=import_job,
                evidence=evidence,
            ),
        )

    def _evidence_summary(
        self,
        target: Any,
        *,
        import_job: ImportJob | None,
        evidence: EvidenceResponse | None,
    ) -> dict[str, object]:
        if evidence is None:
            return {
                "available": False,
                "count": 0,
                "document_name": None if import_job is None else import_job.filename,
                "page": self._target_source_page(target),
                "section": self._target_source_section(target),
                "fragment": None,
                "stream_path": None,
            }
        return {
            "available": True,
            "count": 1,
            **evidence.model_dump(
                mode="python",
                exclude={"correlation_id"},
            ),
        }

    def _queue_facets(self, predicates: Sequence[object]) -> dict[str, dict[str, int]]:
        statuses = {status.value: 0 for status in ReviewCaseStatus}
        case_types = {case_type.value: 0 for case_type in ReviewCaseType}
        for value, count in self._db.execute(select(ReviewItem.case_status, func.count()).where(*predicates).group_by(ReviewItem.case_status)):
            if value in statuses:
                statuses[value] = int(count)
        for value, count in self._db.execute(select(ReviewItem.case_type, func.count()).where(*predicates).group_by(ReviewItem.case_type)):
            if value in case_types:
                case_types[value] = int(count)
        return {"statuses": statuses, "case_types": case_types}

    def _queue_context(self, rows: Sequence[ReviewItem]) -> dict[UUID, _QueueItemContext]:
        ids_by_target: dict[ReviewTargetTable, set[int]] = {}
        for row in rows:
            try:
                table = ReviewTargetTable(row.target_table)
            except ValueError:
                continue
            if row.target_pk is not None:
                ids_by_target.setdefault(table, set()).add(row.target_pk)
        targets: dict[tuple[ReviewTargetTable, int], Any] = {}
        import_job_ids: dict[tuple[ReviewTargetTable, int], int | None] = {}
        for table, target_ids in ids_by_target.items():
            model = _TARGET_MODELS[table]
            for target in self._db.scalars(select(model).where(model.id.in_(target_ids))):
                key = (table, target.id)
                targets[key] = target
                import_job_ids[key] = self._target_import_job_id(target)
        job_ids = {job_id for job_id in import_job_ids.values() if job_id is not None}
        jobs_by_id = {
            job.id: job
            for job in self._db.scalars(select(ImportJob).where(ImportJob.id.in_(job_ids)))
        } if job_ids else {}
        result: dict[UUID, _QueueItemContext] = {}
        for row in rows:
            try:
                table = ReviewTargetTable(row.target_table)
            except ValueError:
                result[row.id] = _QueueItemContext()
                continue
            key = (table, row.target_pk)
            target = targets.get(key) if row.target_pk is not None else None
            document_id = import_job_ids.get(key) if row.target_pk is not None else None
            job = jobs_by_id.get(document_id) if document_id is not None else None
            detected, normalized, canonical = self._queue_public_values(table, target, row)
            result[row.id] = _QueueItemContext(
                document_id=document_id,
                document_name=None if job is None else job.filename,
                detected_value=detected,
                normalized_value=normalized,
                canonical_value=canonical,
            )
        return result

    def _queue_public_values(
        self,
        table: ReviewTargetTable,
        target: Any,
        row: ReviewItem,
    ) -> tuple[str | None, str | None, str | None]:
        if target is None:
            return None, None, None
        try:
            detected, normalized, canonical = self._target_values(table, target)
        except HumanReviewQueryInternalError:
            return None, None, None
        if raw_value_sha256(detected) != row.raw_value_sha256:
            return None, None, None
        return (
            detected[:MAX_EVIDENCE_FRAGMENT_CHARS],
            None if normalized is None else normalized[:MAX_EVIDENCE_FRAGMENT_CHARS],
            None if canonical is None else canonical[:MAX_EVIDENCE_FRAGMENT_CHARS],
        )

    def _queue_item(self, row: ReviewItem, context: _QueueItemContext | None) -> ReviewQueueItem:
        context = context or _QueueItemContext()
        return ReviewQueueItem(
            id=row.id,
            case_type=row.case_type,
            case_status=row.case_status,
            scientific_status=row.scientific_status,
            document_id=context.document_id,
            document_name=context.document_name,
            source_revision=row.source_revision,
            source_page=row.source_page,
            source_section=row.source_section,
            detected_value=context.detected_value,
            normalized_value=context.normalized_value,
            canonical_value=context.canonical_value,
            automatic_priority=row.automatic_priority,
            manual_priority=row.manual_priority,
            possible_kpi_impact=row.possible_kpi_impact,
            version=row.version,
            created_at=row.created_at,
        )

    def _target_for_detail(self, item: ReviewItem) -> tuple[ReviewTargetTable, Any]:
        try:
            case_type = ReviewCaseType(item.case_type)
            table = ReviewTargetTable(item.target_table)
        except ValueError as error:
            raise HumanReviewQueryInternalError(self._correlation_id) from error
        if table not in _CASE_TARGETS.get(case_type, frozenset()) or item.target_pk is None:
            raise HumanReviewQueryInternalError(self._correlation_id)
        target = self._db.get(_TARGET_MODELS[table], item.target_pk)
        if target is None:
            raise HumanReviewQueryInternalError(self._correlation_id)
        return table, target

    def _resolve_evidence(self, item: ReviewItem, target: Any) -> ResolvedEvidence:
        detected = self._evidence_detected_value(target)
        if detected is None or raw_value_sha256(detected) != item.raw_value_sha256:
            raise EvidenceUnavailableError(self._correlation_id)

        target_page = self._target_source_page(target)
        target_section = self._target_source_section(target)
        target_locator = self._target_row_or_block_id(target)
        target_revision = self._target_source_revision(target)
        if (
            self._target_document_key(target) != item.document_key
            or target_page != item.source_page
            or target_section != item.source_section
            or target_locator != item.row_or_block_id
            or target_revision != item.source_revision
            or not self._safe_locator(item.row_or_block_id)
            or (
                item.source_page is not None
                and (
                    not isinstance(item.source_page, int)
                    or isinstance(item.source_page, bool)
                    or item.source_page < 1
                )
            )
            or not isinstance(item.source_section, str)
            or not item.source_section.strip()
        ):
            raise EvidenceUnavailableError(self._correlation_id)

        import_job_id = self._target_import_job_id(target)
        if import_job_id is None:
            raise EvidenceUnavailableError(self._correlation_id)
        job = self._db.get(ImportJob, import_job_id)
        if (
            job is None
            or not _is_allowed_evidence_source(job.source_type)
            or job.status != "SUCCESS"
            or job.is_current is not True
            or job.document_key != item.document_key
            or job.source_rev != item.source_revision
            or not isinstance(job.page_count, int)
            or isinstance(job.page_count, bool)
            or job.page_count < 1
            or (
                item.source_page is not None
                and item.source_page > job.page_count
            )
        ):
            raise EvidenceUnavailableError(self._correlation_id)

        traces = tuple(self._db.scalars(
            select(ImportedOcrTrace)
            .where(ImportedOcrTrace.import_job_id == job.id)
            .order_by(ImportedOcrTrace.id.desc())
        ))
        if not traces:
            raise EvidenceUnavailableError(self._correlation_id)

        validated = tuple(
            self._validated_trace_source(trace, job)
            for trace in traces
        )
        if any(source is None for source in validated):
            raise EvidenceUnavailableError(self._correlation_id)
        unique_sources = {source for source in validated if source is not None}
        if len(unique_sources) != 1:
            raise EvidenceUnavailableError(self._correlation_id)
        source_path, document_name, expected_size, expected_content_hash = (
            unique_sources.pop()
        )

        public = EvidenceResponse(
            document_name=document_name,
            page=item.source_page,
            section=item.source_section,
            locator=item.row_or_block_id,
            fragment=detected[:MAX_EVIDENCE_FRAGMENT_CHARS],
            stream_path=f"human-review/cases/{item.id}/evidence",
            correlation_id=self._correlation_id,
        )
        return ResolvedEvidence(
            public=public,
            source_path=source_path,
            expected_size=expected_size,
            expected_page_count=job.page_count,
            expected_content_hash=expected_content_hash,
        )

    def _validated_trace_source(
        self,
        trace: ImportedOcrTrace | _RelatedEvidenceTrace,
        job: ImportJob,
    ) -> tuple[str, str, int, str] | None:
        if trace.import_job_id != job.id:
            return None
        payload = trace.parsed_payload
        document = payload.get("document") if isinstance(payload, dict) else None
        if not isinstance(document, dict):
            return None

        if job.source_type == DEMO_EVIDENCE_SOURCE:
            if not settings.demo_mode:
                return None
            if (
                trace.source_path != DEMO_EVIDENCE_SOURCE
                or trace.source_filename != DEMO_EVIDENCE_FILENAME
                or document != {
                    "source": DEMO_EVIDENCE_SOURCE,
                    "name": DEMO_EVIDENCE_FILENAME,
                    "size": DEMO_EVIDENCE_SIZE,
                    "content_hash": DEMO_EVIDENCE_SHA256,
                    "page_count": DEMO_EVIDENCE_PAGES,
                }
            ):
                return None
            return (
                DEMO_EVIDENCE_SOURCE,
                DEMO_EVIDENCE_FILENAME,
                DEMO_EVIDENCE_SIZE,
                DEMO_EVIDENCE_SHA256,
            )

        source_path = self._first_nonblank(
            document.get("path_lower"),
            document.get("path_display"),
            document.get("source_path"),
            trace.source_path,
        )
        if source_path is None or not self._safe_dropbox_path(source_path):
            return None
        if (
            not isinstance(trace.source_path, str)
            or trace.source_path.casefold() != source_path.casefold()
            or dropbox_document_key(document) != job.document_key
            or document.get("rev") != job.source_rev
        ):
            return None

        if job.source_fingerprint:
            if dropbox_fingerprint(document) != job.source_fingerprint:
                return None

        document_name = self._first_nonblank(
            document.get("name"),
            document.get("filename"),
            trace.source_filename,
        )
        if document_name is None or len(document_name) > 320:
            return None
        public_document_name = PurePosixPath(
            document_name.replace("\\", "/")
        ).name
        trace_document_name = PurePosixPath(
            trace.source_filename.replace("\\", "/")
        ).name
        if (
            not public_document_name
            or len(public_document_name) > 320
            or public_document_name.casefold() != trace_document_name.casefold()
        ):
            return None

        raw_size = document.get("size")
        if (
            isinstance(raw_size, int)
            and not isinstance(raw_size, bool)
            and 0 < raw_size <= MAX_EVIDENCE_PDF_BYTES
        ):
            expected_size = raw_size
        else:
            return None
        content_hash = document.get("content_hash")
        if (
            not isinstance(content_hash, str)
            or re.fullmatch(r"[0-9a-f]{64}", content_hash) is None
        ):
            return None
        return source_path, public_document_name, expected_size, content_hash

    @staticmethod
    def _target_import_job_id(target: Any) -> int | None:
        direct = getattr(target, "import_job_id", None)
        production = getattr(target, "production", None)
        inherited = getattr(production, "import_job_id", None)
        if direct is not None and inherited is not None and direct != inherited:
            return None
        value = direct if direct is not None else inherited
        return (
            value
            if isinstance(value, int) and not isinstance(value, bool) and value > 0
            else None
        )

    @staticmethod
    def _first_nonblank(*values: object) -> str | None:
        for value in values:
            if isinstance(value, str) and value.strip():
                return value
        return None

    def _target_document_key(self, target: Any) -> str | None:
        job = getattr(target, "import_job", None)
        production = getattr(target, "production", None)
        production_job = getattr(production, "import_job", None)
        return self._first_nonblank(
            getattr(job, "document_key", None),
            getattr(job, "filename", None),
            getattr(target, "source_file", None),
            getattr(production_job, "document_key", None),
            getattr(production_job, "filename", None),
            getattr(production, "source_file", None),
        )

    def _target_source_revision(self, target: Any) -> str | None:
        job = getattr(target, "import_job", None)
        production = getattr(target, "production", None)
        production_job = getattr(production, "import_job", None)
        return self._first_nonblank(
            getattr(job, "source_rev", None),
            getattr(target, "parser_version", None),
            getattr(production_job, "source_rev", None),
            getattr(production, "parser_version", None),
        )

    def _target_row_or_block_id(self, target: Any) -> str | None:
        if isinstance(target, PersonRole):
            metadata = target.metadata_json or {}
            return self._first_nonblank(
                metadata.get("row_or_block_id"),
                metadata.get("row_id"),
                metadata.get("block_id"),
            )
        if isinstance(target, ScientificProductionAuthor):
            return self._first_nonblank(target.row_or_block_id)
        if isinstance(target, ScientificProduction):
            title = self._first_nonblank(target.normalized_title, target.title)
            return f"product:{title.casefold()}" if title else None
        if isinstance(target, ResearchEntity):
            metadata = target.metadata_json or {}
            identity = self._first_nonblank(
                metadata.get("row_or_block_id"),
                metadata.get("row_id"),
                target.normalized_code,
                target.code,
                target.normalized_name,
                target.name,
            )
            return f"entity:{identity}" if identity else None
        if isinstance(target, ExternalResearcher):
            name = self._first_nonblank(target.normalized_name, target.full_name)
            institution = self._first_nonblank(
                target.normalized_institution,
                target.institution,
            )
            return f"external:{name}|{institution}" if name and institution else None
        return None

    @staticmethod
    def _target_source_page(target: Any) -> int | None:
        production = getattr(target, "production", None)
        return getattr(target, "source_page", None) or getattr(
            production, "source_page", None
        )

    def _target_source_section(self, target: Any) -> str | None:
        production = getattr(target, "production", None)
        return self._first_nonblank(
            getattr(target, "source_section", None),
            getattr(production, "source_section", None),
        )

    def _evidence_detected_value(self, target: Any) -> str | None:
        if isinstance(target, PersonRole):
            return self._first_nonblank(
                target.raw_value,
                target.raw_name,
                target.canonical_name,
            )
        if isinstance(target, ScientificProductionAuthor):
            return self._first_nonblank(
                target.raw_author_name,
                target.normalized_author_name,
            )
        if isinstance(target, ScientificProduction):
            return self._first_nonblank(target.raw_value, target.raw_title, target.title)
        if isinstance(target, ResearchEntity):
            return self._first_nonblank(
                target.raw_value,
                target.name,
                target.code,
                target.director_name,
            )
        if isinstance(target, ExternalResearcher):
            return self._first_nonblank(
                target.raw_value,
                target.raw_name,
                target.full_name,
            )
        return None

    @staticmethod
    def _safe_locator(value: object) -> bool:
        if (
            not isinstance(value, str)
            or not value.strip()
            or value != value.strip()
            or len(value) > 500
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
            or "\\" in value
        ):
            return False
        decoded = unquote(value)
        if decoded != value:
            return False
        lowered = decoded.casefold()
        allowed_prefixes = frozenset({
            "product",
            "entity",
            "external",
            "produccion_cientifica",
            "participant",
            "row",
            *(target.value for target in ReviewTargetTable),
        })
        if ":" in decoded:
            prefix, body = decoded.split(":", 1)
            if prefix.casefold() not in allowed_prefixes:
                return False
        else:
            body = decoded
        if (
            re.search(
                r"(?i)(?:^|[^a-z0-9_])(?:dropbox(?:_path)?|minio|s3|gs|file|https?|"
                r"ftp|sftp|postgres(?:ql)?|redis|jdbc|urn):",
                body,
            )
            or re.search(r"(?i)(?:^|[^a-z0-9])[a-z]:/", body)
        ):
            return False
        parts = body.split("/")
        return bool(body.strip()) and all(part not in {"", ".", ".."} for part in parts)

    @staticmethod
    def _safe_dropbox_path(value: object) -> bool:
        if (
            not isinstance(value, str)
            or not value.startswith("/")
            or len(value) > 500
            or "\\" in value
            or ":" in value
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            return False
        decoded = unquote(value)
        if decoded != value:
            return False
        parts = decoded.split("/")
        return all(part not in {"", ".", ".."} for part in parts[1:])

    @staticmethod
    def _first_text(target: Any, fields: Sequence[str]) -> str | None:
        for field in fields:
            value = getattr(target, field, None)
            if isinstance(value, str) and value.strip():
                return value
        return None

    def _target_values(self, table: ReviewTargetTable, target: Any) -> tuple[str, str | None, str | None]:
        detected_fields, normalized_fields, canonical_fields = _TARGET_VALUES[table]
        detected = self._first_text(target, detected_fields)
        if detected is None:
            raise HumanReviewQueryInternalError(self._correlation_id)
        return detected, self._first_text(target, normalized_fields), self._first_text(target, canonical_fields)

    def _current_decision(
        self,
        item: ReviewItem,
        fallback: str | None,
        *,
        decision_cache: Mapping[UUID, ReviewDecision] | None = None,
        identity_cache: Mapping[str, tuple[str, bool] | None] | None = None,
    ) -> tuple[ReviewDecision | None, str | None, bool]:
        if item.current_decision_id is None:
            return None, fallback, False
        decision = (
            self._db.get(ReviewDecision, item.current_decision_id)
            if decision_cache is None
            else decision_cache.get(item.current_decision_id)
        )
        if decision is None or decision.review_item_id != item.id or decision.decision_lifecycle != DecisionLifecycle.APPROVED.value or decision.locks_projection is not True or decision.payload_schema != "review.decision.v1" or decision.payload_version != 1:
            raise HumanReviewQueryInternalError(self._correlation_id)
        try:
            projection = _DECISION_PAYLOAD.validate_python(decision.payload).projection_after
        except ValidationError as error:
            raise HumanReviewQueryInternalError(self._correlation_id) from error
        if projection is None:
            raise HumanReviewQueryInternalError(self._correlation_id)
        try:
            case_type = ReviewCaseType(item.case_type)
        except ValueError as error:
            raise HumanReviewQueryInternalError(self._correlation_id) from error
        if case_type in {
            ReviewCaseType.PERSON_IDENTITY,
            ReviewCaseType.AUTHOR_IDENTITY,
            ReviewCaseType.POSSIBLE_DUPLICATE,
        }:
            if identity_cache is None:
                projection = load_identity_projection(
                    self._db,
                    item.stable_target_key,
                )
                identity = (
                    None
                    if projection is None
                    else (projection.canonical_name, projection.locked)
                )
            else:
                identity = identity_cache.get(item.stable_target_key)
            return (
                decision,
                fallback if identity is None else identity[0],
                identity is not None and identity[1] is True,
            )
        field_by_case = {
            ReviewCaseType.PRODUCT: "product_title",
            ReviewCaseType.PROJECT_DIRECTOR_RELATION: "project_director_relationship_status",
            ReviewCaseType.EXTERNAL_IDENTITY: "external_identity_key",
        }
        field = field_by_case.get(case_type)
        if field is None:
            return decision, fallback, False
        for override in projection.overrides:
            if override.field_path.value == field:
                return (
                    decision,
                    self._scalar_value(override.projected_value),
                    False,
                )
        return decision, fallback, False

    def _safe_overrides(self, item: ReviewItem) -> tuple[dict[str, object], ...]:
        rows = tuple(self._db.scalars(
            select(FieldOverride)
            .where(FieldOverride.stable_target_key == item.stable_target_key, FieldOverride.is_active.is_(True), FieldOverride.locked.is_(True))
            .order_by(FieldOverride.field_path.asc(), FieldOverride.scope.asc(), FieldOverride.created_at.asc(), FieldOverride.id.asc())
        ))
        result: list[dict[str, object]] = []
        for row in rows:
            if not self._override_applies(row, item):
                continue
            if row.value_schema != "override.scalar.v1" or row.value_version != 1:
                raise HumanReviewQueryInternalError(self._correlation_id)
            try:
                value = self._scalar_value(ScalarOverrideValueV1.model_validate(row.projected_value))
            except ValidationError as error:
                raise HumanReviewQueryInternalError(self._correlation_id) from error
            scope_id: object | None = None
            if row.scope == OverrideScope.RECORD.value:
                scope_id = str(item.id)
            elif row.scope == OverrideScope.PERIOD.value:
                scope_id = item.period_id
            result.append({"decision_id": str(row.decision_id), "field_path": row.field_path, "scope": row.scope, "scope_id": scope_id, "value": value, "created_at": row.created_at})
        return tuple(result)

    @staticmethod
    def _override_applies(row: FieldOverride, item: ReviewItem) -> bool:
        if row.stable_target_key != item.stable_target_key or row.target_table != item.target_table or row.target_pk != item.target_pk:
            return False
        return {
            OverrideScope.RECORD.value: True,
            OverrideScope.DOCUMENT.value: row.document_key == item.document_key,
            OverrideScope.PERIOD.value: row.period_id == item.period_id,
            OverrideScope.RELATIONSHIP.value: row.relationship_key == item.relationship_key,
            OverrideScope.GLOBAL_IDENTITY.value: True,
        }.get(row.scope, False)

    @staticmethod
    def _scalar_value(value: ScalarOverrideValueV1) -> object:
        return {
            "string": value.string_value,
            "integer": value.integer_value,
            "decimal": value.decimal_value,
            "boolean": value.boolean_value,
            "null": None,
        }[value.kind]

    def _effective_memberships(
        self,
        item: ReviewItem,
        *,
        identity_locked: bool,
    ) -> tuple[str, ...]:
        try:
            case_type = ReviewCaseType(item.case_type)
        except ValueError as error:
            raise HumanReviewQueryInternalError(self._correlation_id) from error
        membership = _MEMBERSHIP_BY_CASE.get(case_type)
        if item.case_status not in {ReviewCaseStatus.PENDING.value, ReviewCaseStatus.IN_REVIEW.value, ReviewCaseStatus.AWAITING_GESTOR_APPROVAL.value, ReviewCaseStatus.REOPENED.value, ReviewCaseStatus.CONFLICTED.value} or item.scientific_status != "pending":
            membership = None
        if identity_locked and case_type in {
            ReviewCaseType.PERSON_IDENTITY,
            ReviewCaseType.AUTHOR_IDENTITY,
        }:
            membership = BackfillSourceMembership.IDENTITY_LOCKED if item.case_status == ReviewCaseStatus.RESOLVED.value and item.scientific_status == "validated" else None
        memberships = {membership} if membership is not None else set()
        return tuple(membership.value for membership in BackfillSourceMembership if membership in memberships)

    def _audit_item(self, event: AuditEvent) -> dict[str, object]:
        try:
            event_type = AuditEventType(event.event_type)
            payload = _AUDIT_PAYLOAD.validate_python(event.payload)
        except (ValueError, ValidationError) as error:
            raise HumanReviewQueryInternalError(self._correlation_id) from error
        if payload.kind != _AUDIT_KIND_BY_EVENT[event_type]:
            raise HumanReviewQueryInternalError(self._correlation_id)
        if event.payload_schema != f"audit.{event_type.value}.v1" or event.payload_version != 1:
            raise HumanReviewQueryInternalError(self._correlation_id)
        payload_review_item_id = getattr(payload, "review_item_id", None)
        if (
            payload_review_item_id is not None
            and payload_review_item_id != event.review_item_id
        ):
            raise HumanReviewQueryInternalError(self._correlation_id)
        raw = payload.model_dump(mode="json")
        public_payload: dict[str, object] = {"kind": event_type.value, "schema_version": raw["schema_version"]}
        for key in ("decision_id", "decision_type", "previous_case_status", "resulting_case_status", "review_item_id", "kpi_effect"):
            if key in raw:
                public_payload[key] = raw[key]
        return {
            "id": str(event.id),
            "event_type": event_type.value,
            "actor_id": event.actor_user_id,
            "review_item_id": str(event.review_item_id),
            "decision_id": public_payload.get("decision_id"),
            "created_at": event.occurred_at,
            "correlation_id": str(event.correlation_id),
            "summary": _AUDIT_SUMMARIES[event_type],
            "payload": public_payload,
        }
