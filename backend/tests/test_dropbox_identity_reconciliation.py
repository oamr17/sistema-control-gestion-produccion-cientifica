import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.entities import ImportBatch, ImportJob, ImportedOcrTrace
from app.services.dropbox_identity_reconciliation import (
    ReconciliationConflict,
    find_exact_pairs,
    reconcile_exact_pairs,
)
from tests.support.sqlite import create_sqlite_compatible_schema


class DropboxIdentityReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        create_sqlite_compatible_schema(self.engine)
        self.db = Session(self.engine)
        self.historical_batch = ImportBatch(source_type="PROGRESS_PDF", status="COMPLETED", total_files=1)
        self.current_batch = ImportBatch(source_type="PROGRESS_PDF", status="COMPLETED", total_files=1)
        self.db.add_all([self.historical_batch, self.current_batch])
        self.db.flush()

    def tearDown(self):
        self.db.rollback()
        self.db.close()
        self.engine.dispose()

    def _add_pair(self, *, filename="report.pdf", path="/reports/report.pdf"):
        canonical_token = filename.casefold().replace(".pdf", "").replace(" ", "-")
        historical = ImportJob(
            batch_id=self.historical_batch.id,
            source_type="PROGRESS_PDF",
            filename=filename,
            status="SUCCESS",
            source_identifier=path,
            document_key=f"dropbox_path:{path}",
            is_current=True,
        )
        current = ImportJob(
            batch_id=self.current_batch.id,
            source_type="PROGRESS_PDF",
            filename=filename,
            status="SUCCESS",
            source_identifier=f"id:{canonical_token}",
            source_rev="rev-2",
            document_key=f"dropbox:id:{canonical_token}",
            is_current=True,
        )
        self.db.add_all([historical, current])
        self.db.flush()
        self.db.add(
            ImportedOcrTrace(
                import_job_id=current.id,
                source_filename=filename,
                source_path=path,
                parsed_payload={"document": {"path_lower": path, "name": filename}},
                review_status="VALIDADO_AUTOMATICO",
            )
        )
        self.db.flush()
        return historical, current

    def test_find_exact_pairs_requires_exact_path_and_filename(self):
        historical, current = self._add_pair()
        unmatched = ImportJob(
            batch_id=self.historical_batch.id,
            source_type="PROGRESS_PDF",
            filename="similar-report.pdf",
            status="SUCCESS",
            source_identifier="/reports/other.pdf",
            document_key="dropbox_path:/reports/other.pdf",
            is_current=True,
        )
        self.db.add(unmatched)
        self.db.flush()

        result = find_exact_pairs(self.db, self.historical_batch.id, self.current_batch.id)

        self.assertEqual([(pair.historical_job_id, pair.current_job_id) for pair in result.pairs], [(historical.id, current.id)])
        self.assertEqual(len(result.unmatched_historical), 1)

    def test_reconcile_changes_only_identity_state_and_is_idempotent(self):
        historical, current = self._add_pair()
        trace_id = self.db.query(ImportedOcrTrace.id).filter_by(import_job_id=current.id).scalar()
        original_source_identifier = historical.source_identifier
        pairs = find_exact_pairs(self.db, self.historical_batch.id, self.current_batch.id).pairs

        first = reconcile_exact_pairs(self.db, pairs)
        self.db.flush()

        self.assertEqual(first.modified_pairs, 1)
        self.assertFalse(historical.is_current)
        self.assertTrue(current.is_current)
        self.assertEqual(historical.document_key, current.document_key)
        self.assertEqual(historical.source_identifier, original_source_identifier)
        self.assertEqual(current.supersedes_id, historical.id)
        self.assertEqual(self.db.get(ImportedOcrTrace, trace_id).import_job_id, current.id)

        second_pairs = find_exact_pairs(self.db, self.historical_batch.id, self.current_batch.id).pairs
        second = reconcile_exact_pairs(self.db, second_pairs)
        self.db.flush()

        self.assertEqual(second.modified_pairs, 0)
        self.assertEqual(second.already_reconciled_pairs, 1)
        self.assertEqual(current.supersedes_id, historical.id)

    def test_conflicting_supersedes_chain_aborts_without_changes(self):
        historical, current = self._add_pair()
        unrelated = ImportJob(
            source_type="PROGRESS_PDF",
            filename="unrelated.pdf",
            status="SUCCESS",
            document_key="dropbox:id:unrelated",
            is_current=False,
        )
        self.db.add(unrelated)
        self.db.flush()
        current.supersedes_id = unrelated.id
        self.db.flush()
        pairs = find_exact_pairs(self.db, self.historical_batch.id, self.current_batch.id).pairs

        with self.assertRaises(ReconciliationConflict):
            reconcile_exact_pairs(self.db, pairs)

        self.assertTrue(historical.is_current)
        self.assertEqual(historical.document_key, "dropbox_path:/reports/report.pdf")
        self.assertTrue(current.is_current)
        self.assertEqual(current.supersedes_id, unrelated.id)

    def test_ambiguous_exact_match_is_not_reconciled(self):
        historical, _ = self._add_pair()
        duplicate_current = ImportJob(
            batch_id=self.current_batch.id,
            source_type="PROGRESS_PDF",
            filename=historical.filename,
            status="SUCCESS",
            source_identifier="id:other",
            source_rev="rev-other",
            document_key="dropbox:id:other",
            is_current=True,
        )
        self.db.add(duplicate_current)
        self.db.flush()
        self.db.add(
            ImportedOcrTrace(
                import_job_id=duplicate_current.id,
                source_filename=historical.filename,
                source_path=historical.source_identifier,
                parsed_payload={"document": {"path_lower": historical.source_identifier}},
                review_status="VALIDADO_AUTOMATICO",
            )
        )
        self.db.flush()

        result = find_exact_pairs(self.db, self.historical_batch.id, self.current_batch.id)

        self.assertEqual(result.pairs, [])
        self.assertEqual(result.ambiguous_historical, [historical.id])


if __name__ == "__main__":
    unittest.main()
