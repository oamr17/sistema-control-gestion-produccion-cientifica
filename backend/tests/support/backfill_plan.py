from __future__ import annotations

import json

from app.schemas.human_review_operations import B2B1BackfillPlanV1


def synthetic_backfill_plan() -> B2B1BackfillPlanV1:
    """Build the minimal deterministic plan shared by backfill contract tests."""
    return B2B1BackfillPlanV1.model_validate(
        {
            "schema_version": 1,
            "captured_at": "2035-01-02T03:04:05+00:00",
            "invariant_snapshot_sha256": "1" * 64,
            "source_counts": [
                {
                    "population": "canonical_pending",
                    "row_count": 1,
                    "stable_target_count": 1,
                }
            ],
            "overlaps": [],
            "union_stable_target_count": 1,
            "candidates": [
                {
                    "case_type": "person_identity",
                    "stable_target": {
                        "schema_version": 1,
                        "case_type": "person_identity",
                        "target_table": "person_roles",
                        "target_pk": 900_001,
                        "document_key": "test-document:synthetic-backfill",
                        "source_revision": "test-revision-1",
                        "source_page": 1,
                        "source_section": "synthetic-participants",
                        "row_or_block_id": "test-row:900001",
                        "field_path": "case",
                        "raw_value_sha256": "2" * 64,
                        "period_id": 900_010,
                        "relationship_key": None,
                    },
                    "source_table": "person_roles",
                    "source_id": 900_001,
                    "memberships": ["canonical_pending"],
                    "case_status": "pending",
                    "scientific_status": "pending",
                    "possible_kpi_impact": False,
                }
            ],
            "locked_decisions": [],
            "blockers": [
                {
                    "source_table": "person_roles",
                    "source_id": 900_002,
                    "code": "missing_stable_locator",
                    "message": "Synthetic test row has no stable locator",
                }
            ],
        }
    )


def synthetic_legacy_backfill_plan_json() -> bytes:
    """Serialize a pre-additive-fields plan for legacy parsing coverage."""
    material = synthetic_backfill_plan().model_dump(mode="json")
    for field in ("deferred_records", "excluded_records", "hard_blockers"):
        material.pop(field)
    return (
        json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
