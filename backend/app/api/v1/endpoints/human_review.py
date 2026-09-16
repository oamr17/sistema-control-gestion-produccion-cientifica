from __future__ import annotations

import asyncio
from datetime import datetime
from hashlib import sha256
from io import BytesIO
import json
from pathlib import PurePosixPath
import re
from typing import Literal
import unicodedata
from uuid import UUID, uuid4

import fitz
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, require_b2b_action
from app.core.config import settings
from app.core.database import get_db
from app.models.entities import User
from app.models.human_review_core import ReviewDecision, ReviewItem
from app.models.human_review_enums import (
    B2BAction,
    ReviewCaseStatus,
    ReviewCaseType,
)
from app.schemas.human_review_api import (
    ApplyDecisionRequest,
    ApplyDecisionResponse,
    AuditTimelineResponse,
    DiscardRequest,
    EffectiveDataRevisionResponse,
    EffectiveCapabilitiesResponse,
    EvidenceResponse,
    HumanReviewErrorResponse,
    RelatedReviewResponse,
    ReviewCaseDetail,
    ReviewQueueQuery,
    ReviewQueueResponse,
    RevertRequest,
)
from app.services.human_review_authorization import effective_b2b_access
from app.services.human_review_scope import review_scope_predicate
from app.services.human_review_demo_evidence import (
    DEMO_EVIDENCE_SOURCE,
    load_demo_evidence_pdf,
)
from app.services.human_review_commands import (
    apply_decision,
    discard_case,
    revert_case,
)
from app.services.human_review_queries import (
    EvidenceUnavailableError,
    HumanReviewQueryInternalError,
    HumanReviewQueryService,
    MAX_EVIDENCE_PDF_BYTES,
    ReviewCaseNotFoundError,
)
from app.services.human_review_state import HumanReviewDomainError
from app.services.import_service import ImportService


MAX_EVIDENCE_BYTES = MAX_EVIDENCE_PDF_BYTES

_ERROR_STATUS_BY_CODE = {
    "HUMAN_REVIEW_VALIDATION": status.HTTP_400_BAD_REQUEST,
    "AUTHENTICATION_REQUIRED": status.HTTP_401_UNAUTHORIZED,
    "B2B_CAPABILITY_REQUIRED": status.HTTP_403_FORBIDDEN,
    "REVIEW_CASE_NOT_FOUND": status.HTTP_404_NOT_FOUND,
    "REVIEW_CASE_VERSION_CONFLICT": status.HTTP_409_CONFLICT,
    "INCOMPATIBLE_DECISION": status.HTTP_409_CONFLICT,
    "INVALID_COMMAND_PAYLOAD": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "HUMAN_REVIEW_INTERNAL_ERROR": status.HTTP_500_INTERNAL_SERVER_ERROR,
    "EVIDENCE_UNAVAILABLE": status.HTTP_503_SERVICE_UNAVAILABLE,
}
_ERROR_MESSAGE_BY_CODE = {
    "HUMAN_REVIEW_VALIDATION": "Human review request is invalid",
    "AUTHENTICATION_REQUIRED": "Authentication is required",
    "B2B_CAPABILITY_REQUIRED": "B2B capability is required for this action",
    "REVIEW_CASE_NOT_FOUND": "Review case was not found",
    "REVIEW_CASE_VERSION_CONFLICT": "Review case version conflict",
    "INCOMPATIBLE_DECISION": "Decision is incompatible with the review case",
    "INVALID_COMMAND_PAYLOAD": "Command payload is invalid",
    "HUMAN_REVIEW_INTERNAL_ERROR": "Request could not be completed",
    "EVIDENCE_UNAVAILABLE": "Evidence is unavailable",
}
_COMMON_ERROR_RESPONSES = {
    code: {"model": HumanReviewErrorResponse}
    for code in (400, 401, 403, 404, 409, 422, 500)
}
_READ_ERROR_RESPONSES = {
    code: {"model": HumanReviewErrorResponse}
    for code in (400, 401, 403, 404, 422, 500)
}
_EVIDENCE_ERROR_RESPONSES = {
    **_READ_ERROR_RESPONSES,
    503: {"model": HumanReviewErrorResponse},
}
_RELATED_ERROR_RESPONSES = {
    **_READ_ERROR_RESPONSES,
    503: {"model": HumanReviewErrorResponse},
}
_READ_VALIDATION_STATUS_OPENAPI_KEY = "x-human-review-read-validation-status"
_APPROVED_QUEUE_PARAMETERS = frozenset({
    "page",
    "page_size",
    "status",
    "case_type",
    "period_id",
    "document_id",
    "source_revision",
    "created_from",
    "created_to",
    "q",
    "sort",
})


def _correlation_from_request(request: Request) -> UUID:
    candidate: object = getattr(request.state, "correlation_id", None)
    if request.headers.get("X-Correlation-ID") is not None:
        candidate = request.headers["X-Correlation-ID"]
    if request.method in {"POST", "PUT", "PATCH"}:
        try:
            payload = json.loads(request.scope.get("_human_review_body", b"{}"))
            if isinstance(payload, dict) and payload.get("correlation_id") is not None:
                candidate = payload["correlation_id"]
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    try:
        return candidate if isinstance(candidate, UUID) else UUID(str(candidate))
    except (TypeError, ValueError):
        return uuid4()


def _error_response(
    *,
    status_code: int,
    code: str,
    correlation_id: UUID,
    details: object = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    envelope = HumanReviewErrorResponse(
        code=code,
        message=_ERROR_MESSAGE_BY_CODE[code],
        correlation_id=correlation_id,
        details=details,
    )
    response_headers = {
        "Cache-Control": "private, no-store, max-age=0",
        "X-Correlation-ID": str(correlation_id),
    }
    if headers:
        response_headers.update(headers)
    return JSONResponse(
        status_code=status_code,
        content=envelope.model_dump(mode="json", exclude_none=True),
        headers=response_headers,
    )


class HumanReviewRoute(APIRoute):
    """Keep the approved error envelope local to the Task 8 HTTP surface."""

    def get_route_handler(self):
        original_handler = super().get_route_handler()

        async def handler(request: Request) -> Response:
            request.scope["_human_review_body"] = await request.body()
            correlation_id = _correlation_from_request(request)
            request.state.correlation_id = correlation_id
            try:
                response = await original_handler(request)
            except RequestValidationError:
                is_read = request.method in {"GET", "HEAD"}
                route_extra = (
                    getattr(request.scope.get("route"), "openapi_extra", None) or {}
                )
                read_validation_status = route_extra.get(
                    _READ_VALIDATION_STATUS_OPENAPI_KEY
                )
                has_explicit_read_validation = (
                    is_read
                    and read_validation_status == status.HTTP_422_UNPROCESSABLE_ENTITY
                )
                return _error_response(
                    status_code=(
                        status.HTTP_422_UNPROCESSABLE_ENTITY
                        if has_explicit_read_validation
                        else status.HTTP_400_BAD_REQUEST
                        if is_read
                        else status.HTTP_422_UNPROCESSABLE_ENTITY
                    ),
                    code=(
                        "HUMAN_REVIEW_VALIDATION"
                        if is_read
                        else "INVALID_COMMAND_PAYLOAD"
                    ),
                    correlation_id=correlation_id,
                )
            except ValidationError:
                return _error_response(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    code="HUMAN_REVIEW_VALIDATION",
                    correlation_id=correlation_id,
                )
            except HumanReviewDomainError as error:
                code = error.code if error.code in _ERROR_STATUS_BY_CODE else "HUMAN_REVIEW_INTERNAL_ERROR"
                return _error_response(
                    status_code=_ERROR_STATUS_BY_CODE[code],
                    code=code,
                    correlation_id=error.correlation_id or correlation_id,
                    details=error.details,
                )
            except HTTPException as error:
                detail = error.detail if isinstance(error.detail, dict) else {}
                candidate_code = detail.get("code")
                if not isinstance(candidate_code, str) or candidate_code not in _ERROR_STATUS_BY_CODE:
                    candidate_code = {
                        401: "AUTHENTICATION_REQUIRED",
                        403: "B2B_CAPABILITY_REQUIRED",
                        404: "REVIEW_CASE_NOT_FOUND",
                        409: "INCOMPATIBLE_DECISION",
                        422: "INVALID_COMMAND_PAYLOAD",
                        503: "EVIDENCE_UNAVAILABLE",
                    }.get(error.status_code, "HUMAN_REVIEW_INTERNAL_ERROR")
                safe_headers = None
                if error.status_code == status.HTTP_401_UNAUTHORIZED and error.headers:
                    challenge = error.headers.get("WWW-Authenticate")
                    if challenge:
                        safe_headers = {"WWW-Authenticate": challenge}
                return _error_response(
                    status_code=_ERROR_STATUS_BY_CODE[candidate_code],
                    code=candidate_code,
                    correlation_id=correlation_id,
                    details=detail.get("details"),
                    headers=safe_headers,
                )
            except Exception:
                return _error_response(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    code="HUMAN_REVIEW_INTERNAL_ERROR",
                    correlation_id=correlation_id,
                )
            response.headers.setdefault("X-Correlation-ID", str(correlation_id))
            return response

        return handler


router = APIRouter(route_class=HumanReviewRoute)


def _request_correlation_id(request: Request) -> UUID:
    candidate = getattr(request.state, "correlation_id", None)
    try:
        return candidate if isinstance(candidate, UUID) else UUID(str(candidate))
    except (TypeError, ValueError):
        return uuid4()


@router.get(
    "/cases",
    response_model=ReviewQueueResponse,
    responses=_READ_ERROR_RESPONSES,
)
def list_review_cases(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    statuses: list[ReviewCaseStatus] | None = Query(default=None, alias="status"),
    case_types: list[ReviewCaseType] | None = Query(default=None, alias="case_type"),
    period_id: int | None = Query(default=None, gt=0),
    document_id: int | None = Query(default=None, gt=0),
    source_revision: str | None = Query(default=None, min_length=1, max_length=120),
    created_from: datetime | None = Query(default=None),
    created_to: datetime | None = Query(default=None),
    q: str | None = Query(default=None, min_length=3, max_length=128),
    sort: Literal["priority_oldest"] = Query(default="priority_oldest"),
    db: Session = Depends(get_db),
    actor: User = Depends(require_b2b_action(B2BAction.VIEW_FOUNDATIONS)),
) -> ReviewQueueResponse:
    unsupported = set(request.query_params) - _APPROVED_QUEUE_PARAMETERS
    if unsupported:
        raise HumanReviewDomainError(
            "Unsupported human-review filter",
            correlation_id=_request_correlation_id(request),
        )
    query = ReviewQueueQuery(
        page=page,
        page_size=page_size,
        statuses=tuple(statuses or ()),
        case_types=tuple(case_types or ()),
        period_id=period_id,
        document_id=document_id,
        source_revision=source_revision,
        created_from=created_from,
        created_to=created_to,
        q=q,
        sort=sort,
    )
    return HumanReviewQueryService(
        db,
        actor=actor,
        correlation_id=_request_correlation_id(request),
    ).list_cases(query)


@router.get(
    "/me",
    response_model=EffectiveCapabilitiesResponse,
    responses={code: value for code, value in _READ_ERROR_RESPONSES.items() if code in {401, 422, 500}},
)
def get_effective_capabilities(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EffectiveCapabilitiesResponse:
    capability, actions = effective_b2b_access(db, user.id)
    return EffectiveCapabilitiesResponse(capability=capability, actions=actions)


@router.get(
    "/effective-data-revision",
    response_model=EffectiveDataRevisionResponse,
    responses={401: _READ_ERROR_RESPONSES[401], 500: _READ_ERROR_RESPONSES[500]},
)
def effective_data_revision(
    response: Response,
    db: Session = Depends(get_db),
    actor: User = Depends(require_b2b_action(B2BAction.VIEW_FOUNDATIONS)),
) -> EffectiveDataRevisionResponse:
    revision = db.scalar(
        select(func.count())
        .select_from(ReviewDecision)
        .join(ReviewItem, ReviewItem.id == ReviewDecision.review_item_id)
        .where(
            ReviewDecision.decision_lifecycle == "approved",
            ReviewDecision.locks_projection.is_(True),
            review_scope_predicate(actor),
        )
    ) or 0
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    return EffectiveDataRevisionResponse(revision=int(revision))


@router.get(
    "/cases/{review_item_id}",
    response_model=ReviewCaseDetail,
    responses=_READ_ERROR_RESPONSES,
)
def get_review_case(
    review_item_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_b2b_action(B2BAction.VIEW_FOUNDATIONS)),
) -> ReviewCaseDetail:
    return HumanReviewQueryService(
        db,
        actor=actor,
        correlation_id=_request_correlation_id(request),
    ).get_case(review_item_id)


@router.get(
    "/cases/{review_item_id}/related",
    response_model=RelatedReviewResponse,
    responses=_RELATED_ERROR_RESPONSES,
    openapi_extra={
        _READ_VALIDATION_STATUS_OPENAPI_KEY: status.HTTP_422_UNPROCESSABLE_ENTITY,
    },
)
def get_related_review_cases(
    review_item_id: UUID,
    request: Request,
    response: Response,
    limit: int = Query(default=50, ge=1, le=50),
    db: Session = Depends(get_db),
    actor: User = Depends(require_b2b_action(B2BAction.VIEW_FOUNDATIONS)),
) -> RelatedReviewResponse | Response:
    correlation_id = _request_correlation_id(request)
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["X-Correlation-ID"] = str(correlation_id)
    try:
        return HumanReviewQueryService(
            db,
            actor=actor,
            correlation_id=correlation_id,
        ).get_related(review_item_id, limit=limit)
    except HumanReviewQueryInternalError:
        return _error_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="HUMAN_REVIEW_INTERNAL_ERROR",
            correlation_id=correlation_id,
        )


@router.get(
    "/cases/{review_item_id}/audit",
    response_model=AuditTimelineResponse,
    responses=_READ_ERROR_RESPONSES,
)
def get_review_case_audit(
    review_item_id: UUID,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
    actor: User = Depends(require_b2b_action(B2BAction.VIEW_AUDIT)),
) -> AuditTimelineResponse:
    return HumanReviewQueryService(
        db,
        actor=actor,
        correlation_id=_request_correlation_id(request),
    ).get_audit(review_item_id, page, page_size)


@router.post(
    "/cases/{review_item_id}/apply",
    response_model=ApplyDecisionResponse,
    responses=_COMMON_ERROR_RESPONSES,
)
def apply_review_decision(
    review_item_id: UUID,
    command: ApplyDecisionRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_b2b_action(B2BAction.APPLY_SCIENTIFIC)),
) -> ApplyDecisionResponse:
    return apply_decision(db, actor, review_item_id, command)


@router.post(
    "/cases/{review_item_id}/discard",
    response_model=ApplyDecisionResponse,
    responses=_COMMON_ERROR_RESPONSES,
)
def discard_review_case(
    review_item_id: UUID,
    command: DiscardRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_b2b_action(B2BAction.APPLY_SCIENTIFIC)),
) -> ApplyDecisionResponse:
    return discard_case(db, actor, review_item_id, command)


@router.post(
    "/cases/{review_item_id}/revert",
    response_model=ApplyDecisionResponse,
    responses=_COMMON_ERROR_RESPONSES,
)
def revert_review_decision(
    review_item_id: UUID,
    command: RevertRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_b2b_action(B2BAction.REVERT_SCIENTIFIC)),
) -> ApplyDecisionResponse:
    return revert_case(db, actor, review_item_id, command)


def _safe_error(
    *,
    status_code: int,
    code: str,
    correlation_id: UUID,
) -> HTTPException:
    envelope = HumanReviewErrorResponse(
        code=code,
        message=code,
        correlation_id=correlation_id,
    )
    return HTTPException(
        status_code=status_code,
        detail=envelope.model_dump(mode="json"),
        headers={
            "Cache-Control": "private, no-store, max-age=0",
            "X-Correlation-ID": str(correlation_id),
        },
    )


def _public_pdf_filename(value: str | None) -> str:
    if not value or value == "[redacted]":
        return "evidence.pdf"
    basename = PurePosixPath(value.replace("\\", "/")).name
    normalized = unicodedata.normalize("NFKD", basename).encode(
        "ascii", "ignore"
    ).decode("ascii")
    sanitized = re.sub(r"[^A-Za-z0-9._ -]+", "_", normalized)
    sanitized = re.sub(r"\s+", " ", sanitized).strip(" ._-")
    if not sanitized or sanitized in {".", ".."}:
        return "evidence.pdf"
    stem = sanitized[:-4] if sanitized.casefold().endswith(".pdf") else sanitized
    stem = stem[:251].rstrip(" ._-")
    return f"{stem}.pdf" if stem else "evidence.pdf"


def _dropbox_content_hash(content: bytes) -> str:
    block_hashes = b"".join(
        sha256(content[offset:offset + 4 * 1024 * 1024]).digest()
        for offset in range(0, len(content), 4 * 1024 * 1024)
    )
    return sha256(block_hashes).hexdigest()


async def _download_evidence_pdf(
    db: Session,
    *,
    source_path: str,
    expected_size: int,
) -> bytes:
    """Read Dropbox incrementally and stop before crossing the approved bound."""

    access_token = await ImportService(db)._get_dropbox_access_token()
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Dropbox-API-Arg": json.dumps({"path": source_path}),
    }
    chunks: list[bytes] = []
    received = 0
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream(
            "POST",
            "https://content.dropboxapi.com/2/files/download",
            headers=headers,
        ) as response:
            if response.status_code >= 400:
                raise ValueError("evidence backend unavailable")
            declared = response.headers.get("content-length")
            if declared is not None:
                try:
                    declared_size = int(declared)
                except ValueError as error:
                    raise ValueError("invalid evidence size") from error
                if declared_size != expected_size:
                    raise ValueError("evidence size mismatch")
            async for chunk in response.aiter_bytes():
                received += len(chunk)
                if received > expected_size or received > MAX_EVIDENCE_BYTES:
                    raise ValueError("evidence size limit exceeded")
                chunks.append(chunk)
    if received != expected_size:
        raise ValueError("evidence size mismatch")
    return b"".join(chunks)


def _validated_pdf(content: object, *, expected_size: int | None, expected_pages: int | None) -> bytes:
    if (
        not isinstance(content, bytes)
        or not content
        or len(content) > MAX_EVIDENCE_BYTES
        or (expected_size is not None and len(content) != expected_size)
        or not content.lstrip().startswith(b"%PDF-")
    ):
        raise ValueError("invalid evidence payload")
    try:
        with fitz.open(stream=content, filetype="pdf") as document:
            if (
                document.needs_pass
                or document.page_count < 1
                or (
                    expected_pages is not None
                    and document.page_count != expected_pages
                )
            ):
                raise ValueError("invalid evidence document")
    except (RuntimeError, ValueError) as error:
        raise ValueError("invalid evidence document") from error
    return content


@router.get(
    "/cases/{review_item_id}/evidence",
    response_class=StreamingResponse,
    responses={
        200: {
            "model": EvidenceResponse,
            "content": {
                "application/pdf": {"schema": {"type": "string", "format": "binary"}},
            }
        },
        **_EVIDENCE_ERROR_RESPONSES,
    },
)
async def stream_case_evidence(
    review_item_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_b2b_action(B2BAction.VIEW_FOUNDATIONS)),
) -> Response:
    """Stream case-bound evidence without exposing its storage locator."""

    correlation_id = _request_correlation_id(request)
    try:
        evidence = HumanReviewQueryService(
            db,
            actor=actor,
            correlation_id=correlation_id,
        ).get_evidence(review_item_id)
    except ReviewCaseNotFoundError:
        raise _safe_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code="REVIEW_CASE_NOT_FOUND",
            correlation_id=correlation_id,
        ) from None
    except (EvidenceUnavailableError, HumanReviewQueryInternalError):
        raise _safe_error(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="EVIDENCE_UNAVAILABLE",
            correlation_id=correlation_id,
        ) from None

    cache_headers = {
        "Cache-Control": "private, no-store, max-age=0",
        "Pragma": "no-cache",
        "X-Correlation-ID": str(correlation_id),
    }
    try:
        if (
            not isinstance(evidence.expected_size, int)
            or isinstance(evidence.expected_size, bool)
            or not 0 < evidence.expected_size <= MAX_EVIDENCE_BYTES
            or not isinstance(evidence.expected_content_hash, str)
        ):
            raise ValueError("invalid evidence bounds")
        downloaded = (
            await asyncio.to_thread(load_demo_evidence_pdf)
            if settings.demo_mode and evidence.source_path == DEMO_EVIDENCE_SOURCE
            else await _download_evidence_pdf(
                db,
                source_path=evidence.source_path,
                expected_size=evidence.expected_size,
            )
        )
        actual_hash = (
            sha256(downloaded).hexdigest()
            if evidence.source_path == DEMO_EVIDENCE_SOURCE
            else _dropbox_content_hash(downloaded)
        )
        if actual_hash != evidence.expected_content_hash:
            raise ValueError("evidence content mismatch")
        content = await asyncio.to_thread(
            _validated_pdf,
            downloaded,
            expected_size=evidence.expected_size,
            expected_pages=evidence.expected_page_count,
        )
    except Exception:
        raise _safe_error(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="EVIDENCE_UNAVAILABLE",
            correlation_id=correlation_id,
        ) from None

    if "application/json" in request.headers.get("accept", "").casefold():
        return JSONResponse(
            evidence.public.model_dump(mode="json"),
            headers=cache_headers,
        )

    filename = _public_pdf_filename(evidence.public.document_name)
    return StreamingResponse(
        BytesIO(content),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Content-Length": str(len(content)),
            "X-Content-Type-Options": "nosniff",
            **cache_headers,
        },
    )
