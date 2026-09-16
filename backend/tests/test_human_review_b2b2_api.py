from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import dependencies
from app.api.v1.router import api_router
from app.models.human_review_enums import B2BAction, B2BCapability
from app.schemas.human_review_api import (
    ApplyDecisionResponse,
    AuditTimelineResponse,
    KpiEffect,
    RelatedReviewEntity,
    RelatedReviewItem,
    RelatedReviewResponse,
    ReviewCaseDetail,
    ReviewQueueResponse,
)
from app.services.human_review_authorization import B2BAccessDenied
from app.services.human_review_commands import (
    HumanReviewCommandInternalError,
    InvalidCommandPayloadError,
    ReviewCaseVersionConflictError,
)
from app.services.human_review_queries import (
    HumanReviewQueryService,
    HumanReviewQueryInternalError,
    ReviewCaseNotFoundError,
)
from app.services.human_review_state import IncompatibleDecisionError


CASE_ID = UUID("80000000-0000-0000-0000-000000000001")
DECISION_ID = UUID("80000000-0000-0000-0000-000000000002")
CORRELATION_ID = UUID("80000000-0000-0000-0000-000000000003")
NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


def _case_detail() -> ReviewCaseDetail:
    return ReviewCaseDetail(
        id=CASE_ID,
        case_type="person_identity",
        case_status="pending",
        scientific_status="pending",
        document_id=41,
        document_name="paper.pdf",
        source_revision="rev-1",
        source_page=2,
        source_section="Authors",
        automatic_priority=9,
        manual_priority=None,
        possible_kpi_impact=True,
        version=1,
        created_at=NOW,
        target_table="person_roles",
        target_pk=7,
        field_path="canonical_name",
        detected_value="Ada Lovelace",
        normalized_value="ada lovelace",
        canonical_value=None,
        current_decision_id=None,
        overrides=(),
        effective_memberships=("canonical_pending",),
        evidence_summary={
            "available": False,
            "count": 0,
            "document_name": "paper.pdf",
            "page": 2,
            "section": "Authors",
            "fragment": None,
            "stream_path": None,
        },
    )


def _queue_response() -> ReviewQueueResponse:
    detail = _case_detail()
    return ReviewQueueResponse(
        items=(detail,),
        total=1,
        page=2,
        page_size=10,
        facets={
            "statuses": {"pending": 1},
            "case_types": {"person_identity": 1},
        },
        correlation_id=CORRELATION_ID,
    )


def _related_response(*, correlation_id: UUID = CORRELATION_ID) -> RelatedReviewResponse:
    return RelatedReviewResponse(
        entity=RelatedReviewEntity(
            public_type="person",
            public_id="person-41",
            display_name="Ada Lovelace",
        ),
        items=(
            RelatedReviewItem(
                case_id=CASE_ID,
                case_type="person_identity",
                case_status="pending",
                scientific_status="pending",
                version=1,
                current_decision_id=None,
                detected_value="Ada Lovelace",
                normalized_value="ada lovelace",
                canonical_value=None,
                possible_kpi_impact=True,
                allowed_actions=("approve", "correct", "link"),
                evidence_summary={
                    "available": False,
                    "count": 0,
                    "document_name": "paper.pdf",
                    "page": 2,
                    "section": "Authors",
                    "fragment": None,
                    "stream_path": None,
                },
            ),
        ),
        total_pending=1,
        truncated=False,
        correlation_id=correlation_id,
    )


def _command_response() -> ApplyDecisionResponse:
    return ApplyDecisionResponse(
        case=_case_detail(),
        decision_id=DECISION_ID,
        kpi_effect=KpiEffect(affected=()),
        correlation_id=CORRELATION_ID,
    )


def _apply_payload() -> dict[str, object]:
    return {
        "expected_version": 1,
        "expected_current_decision_id": None,
        "action": "approve",
        "scope": "record",
        "payload": {
            "case_type": "person_identity",
            "canonical_identity_key": "person:ada",
            "canonical_name": "Ada Lovelace",
            "aliases": [],
            "scientific_status": "validated",
            "resolution": None,
        },
        "reason": "Evidence verified",
        "correlation_id": str(CORRELATION_ID),
    }


def _duplicate_apply_payload() -> dict[str, object]:
    return {
        "expected_version": 1,
        "expected_current_decision_id": None,
        "action": "link",
        "scope": "record",
        "payload": {
            "case_type": "possible_duplicate",
            "counterpart_ref": {
                "target_type": "scientific_production_authors",
                "target_id": 19,
            },
            "resolution": "merged",
            "scientific_status": "validated",
        },
        "reason": "Verified duplicate evidence",
        "correlation_id": str(CORRELATION_ID),
    }


def _discard_payload() -> dict[str, object]:
    return {
        "expected_version": 1,
        "expected_current_decision_id": None,
        "reason": "Not a scientific record",
        "correlation_id": str(CORRELATION_ID),
    }


def _revert_payload() -> dict[str, object]:
    return {
        "expected_version": 2,
        "expected_current_decision_id": str(DECISION_ID),
        "decision_id_to_revert": str(DECISION_ID),
        "reason": "Decision superseded by verified evidence",
        "correlation_id": str(CORRELATION_ID),
    }


class HumanReviewApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = MagicMock(name="db")
        self.user = SimpleNamespace(
            id=17,
            is_active=True,
            role="FACULTY_ADMIN",
            faculty_id=1,
            career_id=None,
        )
        app = FastAPI()
        app.include_router(api_router, prefix="/api/v1")
        app.dependency_overrides[dependencies.get_db] = lambda: self.db
        app.dependency_overrides[dependencies.get_current_user] = lambda: self.user
        self.app = app
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self) -> None:
        self.client.close()

    def _authorized(self):
        return patch.object(
            dependencies,
            "authorize_b2b_action",
            return_value=B2BCapability.RESEARCH_MANAGER,
        )

    def test_openapi_registers_exact_human_review_surface_and_error_statuses(self) -> None:
        schema = self.app.openapi()
        paths = {
            path: set(operations)
            for path, operations in schema["paths"].items()
            if path.startswith("/api/v1/human-review")
        }
        expected = {
            "/api/v1/human-review/effective-data-revision": {"get"},
            "/api/v1/human-review/cases": {"get"},
            "/api/v1/human-review/cases/{review_item_id}": {"get"},
            "/api/v1/human-review/cases/{review_item_id}/related": {"get"},
            "/api/v1/human-review/cases/{review_item_id}/audit": {"get"},
            "/api/v1/human-review/cases/{review_item_id}/evidence": {"get"},
            "/api/v1/human-review/me": {"get"},
            "/api/v1/human-review/cases/{review_item_id}/apply": {"post"},
            "/api/v1/human-review/cases/{review_item_id}/discard": {"post"},
            "/api/v1/human-review/cases/{review_item_id}/revert": {"post"},
        }
        self.assertEqual(paths, expected)
        self.assertFalse(any("proposal" in path for path in schema["paths"]))
        apply_responses = schema["paths"][
            "/api/v1/human-review/cases/{review_item_id}/apply"
        ]["post"]["responses"]
        self.assertTrue({"200", "400", "401", "403", "404", "409", "422", "500"}.issubset(apply_responses))
        evidence_responses = schema["paths"][
            "/api/v1/human-review/cases/{review_item_id}/evidence"
        ]["get"]["responses"]
        self.assertIn("503", evidence_responses)
        queue_parameters = {
            parameter["name"]
            for parameter in schema["paths"]["/api/v1/human-review/cases"]["get"]["parameters"]
        }
        self.assertIn("document_id", queue_parameters)
        self.assertNotIn("document_key", queue_parameters)
        serialized = json.dumps(schema, sort_keys=True)
        self.assertIn("counterpart_ref", serialized)
        self.assertNotIn("counterpart_stable_target_key", serialized)

    def test_effective_data_revision_is_authenticated_and_returns_only_committed_count(self) -> None:
        self.db.scalar.return_value = 17

        response = self.client.get("/api/v1/human-review/effective-data-revision")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"revision": 17})
        self.assertEqual(response.headers["cache-control"], "private, no-store, max-age=0")
        self.db.scalar.assert_called_once()
        self.db.add.assert_not_called()
        self.db.flush.assert_not_called()
        self.db.commit.assert_not_called()

    def test_related_openapi_is_one_get_with_typed_sanitized_response(self) -> None:
        schema = self.app.openapi()
        operation = schema["paths"][
            "/api/v1/human-review/cases/{review_item_id}/related"
        ]
        self.assertEqual(set(operation), {"get"})
        get = operation["get"]
        self.assertEqual(
            get["responses"]["200"]["content"]["application/json"]["schema"]["$ref"],
            "#/components/schemas/RelatedReviewResponse",
        )
        self.assertEqual(
            {parameter["name"] for parameter in get["parameters"]},
            {"review_item_id", "limit"},
        )
        limit_parameter = next(
            parameter for parameter in get["parameters"] if parameter["name"] == "limit"
        )
        self.assertEqual(limit_parameter["schema"]["default"], 50)
        self.assertEqual(limit_parameter["schema"]["minimum"], 1)
        self.assertEqual(limit_parameter["schema"]["maximum"], 50)
        self.assertEqual(get["x-human-review-read-validation-status"], 422)
        self.assertTrue(
            {"401", "403", "404", "422", "503"}.issubset(get["responses"])
        )
        component_names = (
            "RelatedReviewResponse",
            "RelatedReviewEntity",
            "RelatedReviewItem",
        )
        serialized = json.dumps(
            {name: schema["components"]["schemas"][name] for name in component_names},
            sort_keys=True,
        ).casefold()
        for forbidden in (
            "document_key",
            "stable_target_key",
            "dropbox_path",
            "bucket",
            "credential",
            "storage_path",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_related_returns_typed_body_correlation_cache_and_is_read_only(self) -> None:
        self.db.add.reset_mock()
        self.db.commit.reset_mock()
        self.db.flush.reset_mock()
        with self._authorized(), patch(
            "app.api.v1.endpoints.human_review.HumanReviewQueryService.get_related",
            return_value=_related_response(),
        ) as get_related:
            response = self.client.get(
                f"/api/v1/human-review/cases/{CASE_ID}/related",
                headers={"X-Correlation-ID": str(CORRELATION_ID)},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["entity"]["public_id"], "person-41")
        self.assertEqual(response.json()["correlation_id"], str(CORRELATION_ID))
        self.assertEqual(response.headers["x-correlation-id"], str(CORRELATION_ID))
        self.assertEqual(response.headers["cache-control"], "private, no-store, max-age=0")
        serialized = response.text.casefold()
        for forbidden in (
            "document_key",
            "stable_target_key",
            "dropbox_path",
            "credential",
            "bucket",
            "storage_path",
        ):
            self.assertNotIn(forbidden, serialized)
        get_related.assert_called_once_with(CASE_ID, limit=50)
        self.db.add.assert_not_called()
        self.db.flush.assert_not_called()
        self.db.commit.assert_not_called()

    def test_related_accepts_exact_limit_boundaries(self) -> None:
        for limit in (1, 50):
            with self.subTest(limit=limit), self._authorized(), patch(
                "app.api.v1.endpoints.human_review.HumanReviewQueryService.get_related",
                return_value=_related_response(),
            ) as get_related:
                response = self.client.get(
                    f"/api/v1/human-review/cases/{CASE_ID}/related?limit={limit}",
                    headers={"X-Correlation-ID": str(CORRELATION_ID)},
                )
            self.assertEqual(response.status_code, 200)
            get_related.assert_called_once_with(CASE_ID, limit=limit)

    def test_related_requires_authentication_and_foundation_view(self) -> None:
        override = self.app.dependency_overrides.pop(dependencies.get_current_user)
        try:
            unauthenticated = self.client.get(
                f"/api/v1/human-review/cases/{CASE_ID}/related",
                headers={"X-Correlation-ID": str(CORRELATION_ID)},
            )
        finally:
            self.app.dependency_overrides[dependencies.get_current_user] = override
        with patch.object(
            dependencies,
            "authorize_b2b_action",
            side_effect=B2BAccessDenied("dropbox_path:/private/secret.pdf"),
        ):
            forbidden = self.client.get(
                f"/api/v1/human-review/cases/{CASE_ID}/related",
                headers={"X-Correlation-ID": str(CORRELATION_ID)},
            )

        self.assertEqual(unauthenticated.status_code, 401)
        self.assertEqual(unauthenticated.json()["code"], "AUTHENTICATION_REQUIRED")
        self.assertEqual(
            unauthenticated.headers["cache-control"],
            "private, no-store, max-age=0",
        )
        self.assertEqual(
            unauthenticated.json()["correlation_id"],
            unauthenticated.headers["x-correlation-id"],
        )
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(forbidden.json()["code"], "B2B_CAPABILITY_REQUIRED")
        self.assertEqual(
            forbidden.headers["cache-control"],
            "private, no-store, max-age=0",
        )
        self.assertEqual(
            forbidden.json()["correlation_id"],
            forbidden.headers["x-correlation-id"],
        )
        self.assertNotIn("dropbox_path", forbidden.text.casefold())
        self.assertNotIn("private", forbidden.text.casefold())

        observed_actions: list[B2BAction] = []

        def authorize_foundations_only(_db, _user, action):
            observed_actions.append(action)
            if action is B2BAction.VIEW_FOUNDATIONS:
                return B2BCapability.RESEARCH_MANAGER
            raise B2BAccessDenied("unexpected action")

        with patch.object(
            dependencies,
            "authorize_b2b_action",
            side_effect=authorize_foundations_only,
        ), patch(
            "app.api.v1.endpoints.human_review.HumanReviewQueryService.get_related",
            return_value=_related_response(),
        ):
            authorized = self.client.get(
                f"/api/v1/human-review/cases/{CASE_ID}/related",
                headers={"X-Correlation-ID": str(CORRELATION_ID)},
            )

        self.assertEqual(authorized.status_code, 200)
        self.assertEqual(observed_actions, [B2BAction.VIEW_FOUNDATIONS])

    def test_related_missing_and_out_of_scope_anchors_are_indistinguishable(self) -> None:
        responses = []
        for item_id in (
            UUID("80000000-0000-0000-0000-000000000099"),
            UUID("80000000-0000-0000-0000-000000000098"),
        ):
            with self._authorized(), patch(
                "app.api.v1.endpoints.human_review.HumanReviewQueryService.get_related",
                side_effect=ReviewCaseNotFoundError(CORRELATION_ID),
            ):
                responses.append(self.client.get(
                    f"/api/v1/human-review/cases/{item_id}/related",
                    headers={"X-Correlation-ID": str(CORRELATION_ID)},
                ))

        self.assertEqual([response.status_code for response in responses], [404, 404])
        self.assertEqual(responses[0].json(), responses[1].json())
        self.assertEqual(responses[0].json()["code"], "REVIEW_CASE_NOT_FOUND")

    def test_related_rejects_invalid_uuid_and_limits_as_422(self) -> None:
        with self._authorized():
            responses = (
                self.client.get(
                    "/api/v1/human-review/cases/not-a-uuid/related",
                    headers={"X-Correlation-ID": str(CORRELATION_ID)},
                ),
                self.client.get(
                    f"/api/v1/human-review/cases/{CASE_ID}/related?limit=0",
                    headers={"X-Correlation-ID": str(CORRELATION_ID)},
                ),
                self.client.get(
                    f"/api/v1/human-review/cases/{CASE_ID}/related?limit=51",
                    headers={"X-Correlation-ID": str(CORRELATION_ID)},
                ),
            )
        for response in responses:
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["code"], "HUMAN_REVIEW_VALIDATION")
            self.assertEqual(
                response.headers["cache-control"],
                "private, no-store, max-age=0",
            )
            self.assertEqual(
                response.json()["correlation_id"],
                response.headers["x-correlation-id"],
            )

    def test_related_internal_failure_is_sanitized_503(self) -> None:
        with self._authorized(), patch(
            "app.api.v1.endpoints.human_review.HumanReviewQueryService.get_related",
            side_effect=HumanReviewQueryInternalError(CORRELATION_ID),
        ):
            response = self.client.get(
                f"/api/v1/human-review/cases/{CASE_ID}/related",
                headers={"X-Correlation-ID": str(CORRELATION_ID)},
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "HUMAN_REVIEW_INTERNAL_ERROR")
        self.assertEqual(response.json()["correlation_id"], str(CORRELATION_ID))
        self.assertEqual(response.headers["x-correlation-id"], str(CORRELATION_ID))
        self.assertEqual(
            response.headers["cache-control"],
            "private, no-store, max-age=0",
        )
        self.assertNotIn("traceback", response.text.casefold())
        self.assertNotIn("private", response.text.casefold())

    def test_related_unexpected_resolver_failure_is_sanitized_503(self) -> None:
        scoped_anchor = SimpleNamespace(scope_faculty_id=1, scope_career_id=10)
        with self._authorized(), patch.object(
            HumanReviewQueryService,
            "_get_item_or_raise",
            return_value=scoped_anchor,
        ), patch(
            "app.services.human_review_queries.resolve_related_entity",
            side_effect=RuntimeError(
                r"unexpected resolver failure C:\private\anchor token=secret"
            ),
        ):
            response = self.client.get(
                f"/api/v1/human-review/cases/{CASE_ID}/related",
                headers={"X-Correlation-ID": str(CORRELATION_ID)},
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "HUMAN_REVIEW_INTERNAL_ERROR")
        self.assertEqual(response.headers["x-correlation-id"], str(CORRELATION_ID))
        self.assertEqual(
            response.headers["cache-control"],
            "private, no-store, max-age=0",
        )
        self.assertEqual(
            response.json()["correlation_id"],
            response.headers["x-correlation-id"],
        )
        serialized = response.text.casefold()
        for forbidden in ("unexpected resolver", "private", "token=secret", "traceback"):
            self.assertNotIn(forbidden, serialized)

    def test_related_literal_suffix_is_not_consumed_by_case_detail_route(self) -> None:
        with self._authorized(), patch(
            "app.api.v1.endpoints.human_review.HumanReviewQueryService.get_related",
            return_value=_related_response(),
        ) as get_related, patch(
            "app.api.v1.endpoints.human_review.HumanReviewQueryService.get_case",
        ) as get_case:
            response = self.client.get(
                f"/api/v1/human-review/cases/{CASE_ID}/related",
                headers={"X-Correlation-ID": str(CORRELATION_ID)},
            )

        self.assertEqual(response.status_code, 200)
        get_related.assert_called_once_with(CASE_ID, limit=50)
        get_case.assert_not_called()

    def test_possible_duplicate_accepts_only_the_public_counterpart_reference(self) -> None:
        with self._authorized(), patch(
            "app.api.v1.endpoints.human_review.apply_decision",
            return_value=_command_response(),
        ) as apply:
            accepted = self.client.post(
                f"/api/v1/human-review/cases/{CASE_ID}/apply",
                json=_duplicate_apply_payload(),
            )
            rejected = self.client.post(
                f"/api/v1/human-review/cases/{CASE_ID}/apply",
                json={
                    **_duplicate_apply_payload(),
                    "payload": {
                        "case_type": "possible_duplicate",
                        "counterpart_stable_target_key": "b2b:v1:person_identity:" + "a" * 64,
                        "resolution": "merged",
                        "scientific_status": "validated",
                    },
                },
            )

        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(
            apply.call_args.args[3].payload.counterpart_ref.target_type.value,
            "scientific_production_authors",
        )
        self.assertEqual(apply.call_args.args[3].payload.counterpart_ref.target_id, 19)
        self.assertEqual(rejected.status_code, 422)
        self.assertNotIn("counterpart_stable_target_key", rejected.text)

    def test_list_maps_only_approved_filters_and_returns_typed_page(self) -> None:
        with (
            self._authorized(),
            patch(
                "app.api.v1.endpoints.human_review.HumanReviewQueryService.list_cases",
                return_value=_queue_response(),
            ) as list_cases,
        ):
            response = self.client.get(
                "/api/v1/human-review/cases",
                params=[
                    ("page", "2"),
                    ("page_size", "10"),
                    ("status", "pending"),
                    ("case_type", "person_identity"),
                    ("period_id", "5"),
                    ("document_id", "41"),
                    ("source_revision", "rev-1"),
                    ("created_from", "2026-01-01T00:00:00Z"),
                    ("created_to", "2026-12-31T23:59:59Z"),
                    ("q", "ada"),
                    ("sort", "priority_oldest"),
                ],
                headers={"X-Correlation-ID": str(CORRELATION_ID)},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 1)
        self.assertEqual(response.headers["x-correlation-id"], str(CORRELATION_ID))
        query = list_cases.call_args.args[0]
        self.assertEqual(query.page, 2)
        self.assertEqual(query.page_size, 10)
        self.assertEqual([item.value for item in query.statuses], ["pending"])
        self.assertEqual([item.value for item in query.case_types], ["person_identity"])
        self.assertEqual(query.period_id, 5)
        self.assertEqual(query.document_id, 41)
        self.assertEqual(query.source_revision, "rev-1")
        self.assertEqual(query.q, "ada")

    def test_list_serializes_human_context_without_internal_storage_or_tracebacks(self) -> None:
        author = _case_detail().model_copy(update={
            "case_type": "author_identity",
            "detected_value": "Ada Lovelace",
        })
        product = _case_detail().model_copy(update={
            "id": UUID("80000000-0000-0000-0000-000000000004"),
            "case_type": "product",
            "source_page": 7,
            "source_section": "Producción científica",
            "detected_value": "Artículo de investigación",
        })
        page = ReviewQueueResponse(
            items=(author, product),
            total=2,
            page=1,
            page_size=25,
            facets={"statuses": {"pending": 2}, "case_types": {"author_identity": 1, "product": 1}},
            correlation_id=CORRELATION_ID,
        )
        with self._authorized(), patch(
            "app.api.v1.endpoints.human_review.HumanReviewQueryService.list_cases",
            return_value=page,
        ):
            response = self.client.get("/api/v1/human-review/cases")

        self.assertEqual(response.status_code, 200)
        items = response.json()["items"]
        self.assertEqual(
            [(item["case_type"], item["document_name"], item["source_page"], item["source_section"], item["detected_value"], item["case_status"]) for item in items],
            [
                ("author_identity", "paper.pdf", 2, "Authors", "Ada Lovelace", "pending"),
                ("product", "paper.pdf", 7, "Producción científica", "Artículo de investigación", "pending"),
            ],
        )
        serialized = response.text.casefold()
        for forbidden in ("document_key", "stable_target_key", "dropbox_path", "source_path", "object_key", "traceback"):
            self.assertNotIn(forbidden, serialized)

    def test_detail_and_audit_delegate_to_read_only_query_service(self) -> None:
        audit = AuditTimelineResponse(
            items=(), page=3, page_size=5, total=0, correlation_id=CORRELATION_ID
        )
        with (
            self._authorized(),
            patch(
                "app.api.v1.endpoints.human_review.HumanReviewQueryService.get_case",
                return_value=_case_detail(),
            ) as get_case,
            patch(
                "app.api.v1.endpoints.human_review.HumanReviewQueryService.get_audit",
                return_value=audit,
            ) as get_audit,
        ):
            detail = self.client.get(
                f"/api/v1/human-review/cases/{CASE_ID}",
                headers={"X-Correlation-ID": str(CORRELATION_ID)},
            )
            timeline = self.client.get(
                f"/api/v1/human-review/cases/{CASE_ID}/audit?page=3&page_size=5",
                headers={"X-Correlation-ID": str(CORRELATION_ID)},
            )

        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["id"], str(CASE_ID))
        get_case.assert_called_once_with(CASE_ID)
        self.assertEqual(timeline.status_code, 200)
        get_audit.assert_called_once_with(CASE_ID, 3, 5)

    def test_commands_delegate_typed_requests_without_committing_in_http_layer(self) -> None:
        self.db.commit.reset_mock()
        with (
            self._authorized(),
            patch(
                "app.api.v1.endpoints.human_review.apply_decision",
                return_value=_command_response(),
                create=True,
            ) as apply,
            patch(
                "app.api.v1.endpoints.human_review.discard_case",
                return_value=_command_response(),
                create=True,
            ) as discard,
            patch(
                "app.api.v1.endpoints.human_review.revert_case",
                return_value=_command_response(),
                create=True,
            ) as revert,
        ):
            responses = (
                self.client.post(f"/api/v1/human-review/cases/{CASE_ID}/apply", json=_apply_payload()),
                self.client.post(f"/api/v1/human-review/cases/{CASE_ID}/discard", json=_discard_payload()),
                self.client.post(f"/api/v1/human-review/cases/{CASE_ID}/revert", json=_revert_payload()),
            )

        self.assertEqual([response.status_code for response in responses], [200, 200, 200])
        self.assertIs(apply.call_args.args[0], self.db)
        self.assertIs(apply.call_args.args[1], self.user)
        self.assertEqual(apply.call_args.args[2], CASE_ID)
        self.assertEqual(apply.call_args.args[3].correlation_id, CORRELATION_ID)
        self.assertEqual(discard.call_args.args[3].reason, "Not a scientific record")
        self.assertEqual(revert.call_args.args[3].decision_id_to_revert, DECISION_ID)
        self.db.commit.assert_not_called()

    def test_me_exposes_effective_capability_actions_without_granting_access(self) -> None:
        self.db.get.return_value = self.user
        with patch(
            "app.services.human_review_authorization.resolve_b2b_capability",
            return_value=B2BCapability.RESEARCH_MANAGER,
        ):
            response = self.client.get("/api/v1/human-review/me")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["capability"], "RESEARCH_MANAGER")
        self.assertIn("apply_scientific", response.json()["actions"])
        self.assertNotIn("manage_technical_access", response.json()["actions"])

    def test_missing_authentication_is_closed_401_with_correlation_id(self) -> None:
        override = self.app.dependency_overrides.pop(dependencies.get_current_user)
        try:
            response = self.client.get(
                "/api/v1/human-review/cases",
                headers={"X-Correlation-ID": str(CORRELATION_ID)},
            )
        finally:
            self.app.dependency_overrides[dependencies.get_current_user] = override

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "AUTHENTICATION_REQUIRED")
        self.assertEqual(response.json()["correlation_id"], str(CORRELATION_ID))

    def test_capability_matrix_allows_admin_reads_but_denies_scientific_commands(self) -> None:
        def authorize(_db, _user, action):
            if action in {B2BAction.VIEW_FOUNDATIONS, B2BAction.VIEW_AUDIT}:
                return B2BCapability.SYSTEM_ADMIN
            raise B2BAccessDenied("denied")

        with (
            patch.object(dependencies, "authorize_b2b_action", side_effect=authorize),
            patch(
                "app.api.v1.endpoints.human_review.HumanReviewQueryService.get_case",
                return_value=_case_detail(),
            ),
        ):
            readable = self.client.get(f"/api/v1/human-review/cases/{CASE_ID}")
            denied = self.client.post(
                f"/api/v1/human-review/cases/{CASE_ID}/discard",
                json=_discard_payload(),
            )

        self.assertEqual(readable.status_code, 200)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()["code"], "B2B_CAPABILITY_REQUIRED")

    def test_unassigned_or_career_manager_is_denied_without_sensitive_detail(self) -> None:
        with patch.object(
            dependencies,
            "authorize_b2b_action",
            side_effect=B2BAccessDenied("dropbox_path:/private/report.pdf"),
        ):
            response = self.client.get(f"/api/v1/human-review/cases/{CASE_ID}")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "B2B_CAPABILITY_REQUIRED")
        self.assertNotIn("dropbox_path", response.text)
        self.assertNotIn("private", response.text)

    def test_domain_failures_map_to_closed_http_errors(self) -> None:
        cases = (
            (ReviewCaseNotFoundError(CORRELATION_ID), 404, "REVIEW_CASE_NOT_FOUND"),
            (ReviewCaseVersionConflictError(CORRELATION_ID), 409, "REVIEW_CASE_VERSION_CONFLICT"),
            (
                IncompatibleDecisionError(
                    "unsafe /private/db",
                    correlation_id=CORRELATION_ID,
                    details={"case_type": "person_identity", "action": "link"},
                ),
                409,
                "INCOMPATIBLE_DECISION",
            ),
            (InvalidCommandPayloadError(CORRELATION_ID), 422, "INVALID_COMMAND_PAYLOAD"),
            (HumanReviewCommandInternalError(CORRELATION_ID), 500, "HUMAN_REVIEW_INTERNAL_ERROR"),
        )
        for error, status_code, code in cases:
            with self.subTest(code=code), self._authorized(), patch(
                "app.api.v1.endpoints.human_review.apply_decision",
                side_effect=error,
                create=True,
            ):
                response = self.client.post(
                    f"/api/v1/human-review/cases/{CASE_ID}/apply",
                    json=_apply_payload(),
                )
            self.assertEqual(response.status_code, status_code)
            self.assertEqual(response.json()["code"], code)
            self.assertEqual(response.json()["correlation_id"], str(CORRELATION_ID))
            self.assertNotIn("/private", response.text)

    def test_body_and_path_validation_are_closed_422_and_never_500(self) -> None:
        with self._authorized():
            malformed = self.client.post(
                f"/api/v1/human-review/cases/{CASE_ID}/apply",
                json={**_apply_payload(), "expected_version": "1", "unexpected": "secret"},
            )
            invalid_id = self.client.post(
                "/api/v1/human-review/cases/not-a-uuid/discard",
                json=_discard_payload(),
            )

        for response in (malformed, invalid_id):
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["code"], "INVALID_COMMAND_PAYLOAD")
            self.assertEqual(response.json()["correlation_id"], str(CORRELATION_ID))

    def test_unapproved_query_filter_is_closed_400(self) -> None:
        with self._authorized():
            response = self.client.get(
                "/api/v1/human-review/cases?document_key=dropbox_path%3A%2Fprivate.pdf",
                headers={"X-Correlation-ID": str(CORRELATION_ID)},
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "HUMAN_REVIEW_VALIDATION")
        self.assertEqual(response.json()["correlation_id"], str(CORRELATION_ID))

    def test_invalid_read_parameters_are_validation_400_not_command_422(self) -> None:
        with self._authorized():
            responses = (
                self.client.get(
                    "/api/v1/human-review/cases?page=0",
                    headers={"X-Correlation-ID": str(CORRELATION_ID)},
                ),
                self.client.get(
                    "/api/v1/human-review/cases?q=ab",
                    headers={"X-Correlation-ID": str(CORRELATION_ID)},
                ),
                self.client.get(
                    f"/api/v1/human-review/cases/{CASE_ID}/audit?page_size=101",
                    headers={"X-Correlation-ID": str(CORRELATION_ID)},
                ),
            )

        for response in responses:
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["code"], "HUMAN_REVIEW_VALIDATION")
            self.assertEqual(response.json()["correlation_id"], str(CORRELATION_ID))


if __name__ == "__main__":
    unittest.main()
