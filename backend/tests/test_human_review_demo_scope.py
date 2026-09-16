from __future__ import annotations

from contextlib import nullcontext
from hashlib import sha256
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from app.services.human_review_demo_evidence import (
    DEMO_EVIDENCE_FILENAME,
    DEMO_EVIDENCE_PAGES,
    DEMO_EVIDENCE_SHA256,
    DEMO_EVIDENCE_SIZE,
    DEMO_EVIDENCE_SOURCE,
    load_demo_evidence_pdf,
)
from app.services.human_review_queries import (
    HumanReviewQueryService,
    _is_allowed_evidence_source,
)


class HumanReviewDemoScopeTests(unittest.TestCase):
    def test_synthetic_pdf_matches_fixed_contract(self):
        content = load_demo_evidence_pdf()
        self.assertEqual(len(content), DEMO_EVIDENCE_SIZE)
        self.assertEqual(sha256(content).hexdigest(), DEMO_EVIDENCE_SHA256)
        self.assertTrue(content.startswith(b"%PDF-"))
        self.assertEqual(DEMO_EVIDENCE_PAGES, 2)

    def test_demo_trace_is_accepted_only_when_demo_mode_is_explicit(self):
        document = {
            "source": DEMO_EVIDENCE_SOURCE,
            "name": DEMO_EVIDENCE_FILENAME,
            "size": DEMO_EVIDENCE_SIZE,
            "content_hash": DEMO_EVIDENCE_SHA256,
            "page_count": DEMO_EVIDENCE_PAGES,
        }
        trace = SimpleNamespace(
            import_job_id=7,
            source_path=DEMO_EVIDENCE_SOURCE,
            source_filename=DEMO_EVIDENCE_FILENAME,
            parsed_payload={"document": document},
        )
        job = SimpleNamespace(id=7, source_type=DEMO_EVIDENCE_SOURCE)
        service = HumanReviewQueryService(object())
        with patch("app.services.human_review_queries.settings.demo_mode", False):
            self.assertIsNone(service._validated_trace_source(trace, job))
        with patch("app.services.human_review_queries.settings.demo_mode", True):
            self.assertEqual(
                service._validated_trace_source(trace, job),
                (
                    DEMO_EVIDENCE_SOURCE,
                    DEMO_EVIDENCE_FILENAME,
                    DEMO_EVIDENCE_SIZE,
                    DEMO_EVIDENCE_SHA256,
                ),
            )

    def test_demo_source_is_allowed_for_direct_and_related_evidence_only_in_demo_mode(self):
        with patch("app.services.human_review_queries.settings.demo_mode", False):
            self.assertFalse(_is_allowed_evidence_source(DEMO_EVIDENCE_SOURCE))
            self.assertTrue(_is_allowed_evidence_source("PROGRESS_PDF"))
        with patch("app.services.human_review_queries.settings.demo_mode", True):
            self.assertTrue(_is_allowed_evidence_source(DEMO_EVIDENCE_SOURCE))

    def test_locked_human_identity_is_reused_for_teacher_surfaces(self):
        teacher = SimpleNamespace(id=11, full_name="Ana Torres")
        role = SimpleNamespace(teacher_id=11)
        projection = SimpleNamespace(
            scientific_status="validated",
            canonical_identity_key="human:identity:demo",
            canonical_name="Ana Torres Unificada",
        )
        db = MagicMock()
        db.no_autoflush = nullcontext()
        db.query.return_value.filter.return_value.all.return_value = [role]

        from app.services.validated_read_service import ValidatedReadService

        reader = ValidatedReadService(db)
        reader.human_projection.read_scope = MagicMock(return_value=nullcontext())
        with (
            patch.object(reader, "_prefetch_human_projections"),
            patch.object(reader, "_identity_projection_for", return_value=projection),
        ):
            self.assertEqual(
                reader.effective_teacher_names([teacher]),
                {11: "Ana Torres Unificada"},
            )


if __name__ == "__main__":
    unittest.main()
