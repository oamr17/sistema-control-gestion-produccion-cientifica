import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models.entities import ImportJob
from app.services.import_batching import promote_document_version


TEST_DATABASE_URL = os.getenv("DROPBOX_VERSIONING_TEST_DATABASE_URL")


@unittest.skipUnless(TEST_DATABASE_URL, "requiere DROPBOX_VERSIONING_TEST_DATABASE_URL")
class DropboxVersioningPostgresIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
        cls.Session = sessionmaker(bind=cls.engine, expire_on_commit=False)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def test_database_rejects_second_job_for_same_document_and_revision(self):
        token = uuid4().hex
        document_key = f"dropbox:id:concurrent-{token}"
        first = ImportJob(
            source_type="PROGRESS_PDF",
            filename="first.pdf",
            status="QUEUED",
            source_identifier=f"id:concurrent-{token}",
            source_rev="rev-1",
            source_fingerprint=f"dropbox:id:concurrent-{token}:rev-1",
            document_key=document_key,
            is_current=False,
        )
        with self.Session() as db:
            db.add(first)
            db.commit()

        with self.Session() as db:
            db.add(
                ImportJob(
                    source_type="PROGRESS_PDF",
                    filename="second.pdf",
                    status="QUEUED",
                    source_identifier=f"id:concurrent-{token}",
                    source_rev="rev-1",
                    source_fingerprint=f"dropbox:id:concurrent-{token}:rev-1",
                    document_key=document_key,
                    is_current=False,
                )
            )
            with self.assertRaises(IntegrityError):
                db.commit()

    def test_failed_revision_does_not_displace_current_version(self):
        token = uuid4().hex
        document_key = f"dropbox:id:failure-{token}"
        with self.Session() as db:
            current = ImportJob(
                source_type="PROGRESS_PDF",
                filename="current.pdf",
                status="SUCCESS",
                source_identifier=f"id:failure-{token}",
                source_rev="rev-1",
                source_fingerprint=f"dropbox:id:failure-{token}:rev-1",
                document_key=document_key,
                is_current=True,
            )
            failed = ImportJob(
                source_type="PROGRESS_PDF",
                filename="failed.pdf",
                status="ERROR",
                source_identifier=f"id:failure-{token}",
                source_rev="rev-2",
                source_fingerprint=f"dropbox:id:failure-{token}:rev-2",
                document_key=document_key,
                is_current=False,
            )
            db.add_all([current, failed])
            db.commit()

            db.refresh(current)
            db.refresh(failed)
            self.assertTrue(current.is_current)
            self.assertFalse(failed.is_current)
            self.assertIsNone(failed.supersedes_id)

    def test_successful_new_revision_displaces_previous_only_on_promotion(self):
        token = uuid4().hex
        document_key = f"dropbox:id:success-{token}"
        with self.Session() as db:
            previous = ImportJob(
                source_type="PROGRESS_PDF",
                filename="previous.pdf",
                status="SUCCESS",
                source_identifier=f"id:success-{token}",
                source_rev="rev-1",
                source_fingerprint=f"dropbox:id:success-{token}:rev-1",
                document_key=document_key,
                is_current=True,
            )
            revised = ImportJob(
                source_type="PROGRESS_PDF",
                filename="revised.pdf",
                status="PROCESSING",
                source_identifier=f"id:success-{token}",
                source_rev="rev-2",
                source_fingerprint=f"dropbox:id:success-{token}:rev-2",
                document_key=document_key,
                is_current=False,
            )
            db.add_all([previous, revised])
            db.commit()

            self.assertTrue(previous.is_current)
            self.assertFalse(revised.is_current)

            promote_document_version(db, revised)
            revised.status = "SUCCESS"
            db.commit()

            self.assertFalse(previous.is_current)
            self.assertTrue(revised.is_current)
            self.assertEqual(revised.supersedes_id, previous.id)

    def test_concurrent_promotions_keep_newest_current_for_either_lock_order(self):
        token = uuid4().hex
        document_key = f"dropbox:id:promotion-{token}"
        with self.Session() as db:
            previous = ImportJob(
                source_type="PROGRESS_PDF",
                filename="previous.pdf",
                status="SUCCESS",
                source_identifier=f"id:promotion-{token}",
                source_rev="rev-1",
                source_fingerprint=f"dropbox:id:promotion-{token}:rev-1",
                document_key=document_key,
                is_current=True,
            )
            first = ImportJob(
                source_type="PROGRESS_PDF",
                filename="first.pdf",
                status="PROCESSING",
                source_identifier=f"id:promotion-{token}",
                source_rev="rev-2",
                source_fingerprint=f"dropbox:id:promotion-{token}:rev-2",
                document_key=document_key,
                is_current=False,
            )
            second = ImportJob(
                source_type="PROGRESS_PDF",
                filename="second.pdf",
                status="PROCESSING",
                source_identifier=f"id:promotion-{token}",
                source_rev="rev-3",
                source_fingerprint=f"dropbox:id:promotion-{token}:rev-3",
                document_key=document_key,
                is_current=False,
            )
            db.add_all([previous, first, second])
            db.commit()
            revision_ids = [first.id, second.id]

        barrier = Barrier(2)

        def promote(job_id: int) -> int:
            with self.Session() as db:
                job = db.get(ImportJob, job_id)
                barrier.wait(timeout=10)
                promote_document_version(db, job)
                job.status = "SUCCESS"
                db.commit()
                return job.id

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(promote, revision_ids))

        self.assertCountEqual(results, revision_ids)
        with self.Session() as db:
            versions = (
                db.query(ImportJob)
                .filter(ImportJob.document_key == document_key)
                .order_by(ImportJob.id.asc())
                .all()
            )
            current = [job for job in versions if job.is_current]
            self.assertEqual(len(current), 1)
            previous, first, second = versions
            self.assertFalse(previous.is_current)
            self.assertFalse(first.is_current)
            self.assertTrue(second.is_current)

            if second.supersedes_id == first.id:
                self.assertEqual(first.supersedes_id, previous.id)
            else:
                self.assertEqual(second.supersedes_id, previous.id)
                self.assertIsNone(first.supersedes_id)

    def test_older_job_finishing_late_does_not_displace_newer_current_job(self):
        token = uuid4().hex
        document_key = f"dropbox:id:out-of-order-{token}"
        with self.Session() as db:
            older = ImportJob(
                source_type="PROGRESS_PDF",
                filename="older.pdf",
                status="PROCESSING",
                source_identifier=f"id:out-of-order-{token}",
                source_rev="rev-1",
                source_fingerprint=f"dropbox:id:out-of-order-{token}:rev-1",
                document_key=document_key,
                is_current=False,
            )
            newer = ImportJob(
                source_type="PROGRESS_PDF",
                filename="newer.pdf",
                status="PROCESSING",
                source_identifier=f"id:out-of-order-{token}",
                source_rev="rev-2",
                source_fingerprint=f"dropbox:id:out-of-order-{token}:rev-2",
                document_key=document_key,
                is_current=False,
            )
            db.add_all([older, newer])
            db.commit()

            promote_document_version(db, newer)
            newer.status = "SUCCESS"
            db.commit()
            promote_document_version(db, older)
            older.status = "SUCCESS"
            db.commit()

            db.refresh(older)
            db.refresh(newer)
            self.assertFalse(older.is_current)
            self.assertTrue(newer.is_current)
            self.assertIsNone(older.supersedes_id)


if __name__ == "__main__":
    unittest.main()
