import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.entities import ImportJob
from app.services import import_batching
from tests.support.sqlite import create_sqlite_compatible_schema


class ImportJobVersioningTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        create_sqlite_compatible_schema(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_new_revision_supersedes_current_job_for_same_document(self):
        promote = getattr(import_batching, "promote_document_version", None)
        self.assertIsNotNone(promote)

        previous = ImportJob(
            source_type="PROGRESS_PDF",
            filename="zambrano.pdf",
            status="SUCCESS",
            source_identifier="id:zambrano",
            source_rev="rev-1",
            document_key="dropbox:id:zambrano",
            is_current=True,
        )
        current = ImportJob(
            source_type="PROGRESS_PDF",
            filename="zambrano.pdf",
            status="PROCESSING",
            source_identifier="id:zambrano",
            source_rev="rev-2",
            document_key="dropbox:id:zambrano",
            is_current=False,
        )
        self.db.add_all([previous, current])
        self.db.flush()

        promote(self.db, current)
        self.db.flush()

        self.assertFalse(previous.is_current)
        self.assertTrue(current.is_current)
        self.assertEqual(current.supersedes_id, previous.id)

    def test_first_revision_becomes_current_without_supersedes(self):
        promote = getattr(import_batching, "promote_document_version", None)
        self.assertIsNotNone(promote)

        current = ImportJob(
            source_type="PROGRESS_PDF",
            filename="zambrano.pdf",
            status="PROCESSING",
            source_identifier="id:zambrano",
            source_rev="rev-1",
            document_key="dropbox:id:zambrano",
            is_current=False,
        )
        self.db.add(current)
        self.db.flush()

        promote(self.db, current)
        self.db.flush()

        self.assertTrue(current.is_current)
        self.assertIsNone(current.supersedes_id)


if __name__ == "__main__":
    unittest.main()
