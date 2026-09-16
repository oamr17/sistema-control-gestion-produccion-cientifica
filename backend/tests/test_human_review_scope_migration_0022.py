from __future__ import annotations

import hashlib
import importlib
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core import migrations as migration_registry
from app.core.migrations import run_migrations
from tests.support.postgres import isolated_postgres_schema, require_b2b1_test_database_url


VERSION = "20260827_0022_scoped_human_review_authorization"
DOWN_REVISION = "20260718_0021_human_review_scientific_decision_audit"
MIGRATION_MODULE = f"app.migrations.versions.{VERSION}"
MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "migrations"
    / "versions"
    / f"{VERSION}.py"
)
MIGRATION_0021_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "migrations"
    / "versions"
    / f"{DOWN_REVISION}.py"
)
MIGRATION_0021_SHA256 = (
    "54704858458CE6F27B12BF8B2BE386B91C3A32F27D1D210CEC50DAD16E423B2A"
)


class HumanReviewScopeMigration0022PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_url = require_b2b1_test_database_url()

    def test_contract_is_exact_and_is_the_sole_activated_entry_22(self) -> None:
        module = self._migration_module()
        self.assertEqual(
            (module.VERSION, module.revision, module.DOWN_REVISION, module.down_revision),
            (VERSION, VERSION, DOWN_REVISION, DOWN_REVISION),
        )
        self.assertEqual(
            module.UNRESOLVED_REASONS,
            (
                "unresolved_no_persisted_scope",
                "unresolved_cross_faculty",
                "unresolved_missing_target",
            ),
        )
        versions = tuple(version for version, _upgrade in migration_registry.MIGRATIONS)
        self.assertEqual(len(versions), 22)
        self.assertEqual(versions[-2:], (DOWN_REVISION, VERSION))

        from app.core.prototype_baseline import MIGRATION_SHA256_MANIFEST

        manifest_versions = tuple(item.version for item in MIGRATION_SHA256_MANIFEST)
        self.assertEqual(len(manifest_versions), 22)
        self.assertEqual(manifest_versions[-2:], (DOWN_REVISION, VERSION))
        self.assertEqual(
            MIGRATION_SHA256_MANIFEST[-1].sha256,
            "2F6B9ACE942723F5A2095D1673E3F8051C5CB81E58E45141C4B4A31F4C4DDFCF",
        )

    def test_upgrade_creates_the_exact_catalog(self) -> None:
        module = self._migration_module()
        with self._prepared_0021("scope0022_catalog") as engine:
            module.upgrade(engine)
            with engine.connect() as connection:
                module.assert_schema(connection)

                columns = {
                    (row.table_name, row.column_name): (
                        row.data_type,
                        row.character_maximum_length,
                        row.is_nullable,
                    )
                    for row in connection.execute(text("""
                        SELECT table_name, column_name, data_type,
                               character_maximum_length, is_nullable
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND (
                            (table_name = 'users' AND column_name = 'faculty_id')
                            OR (table_name = 'review_items' AND column_name LIKE 'scope_%')
                          )
                    """))
                }
                self.assertEqual(
                    columns,
                    {
                        ("users", "faculty_id"): ("integer", None, "YES"),
                        ("review_items", "scope_faculty_id"): ("integer", None, "YES"),
                        ("review_items", "scope_career_id"): ("integer", None, "YES"),
                        ("review_items", "scope_resolution_reason"): (
                            "character varying",
                            80,
                            "YES",
                        ),
                    },
                )

                constraints = {
                    row.name: (row.kind, row.definition, row.delete_action)
                    for row in connection.execute(text("""
                        SELECT constraint_row.conname AS name,
                               constraint_row.contype AS kind,
                               pg_get_constraintdef(constraint_row.oid, true) AS definition,
                               constraint_row.confdeltype AS delete_action
                        FROM pg_constraint AS constraint_row
                        JOIN pg_class AS table_row
                          ON table_row.oid = constraint_row.conrelid
                        JOIN pg_namespace AS namespace_row
                          ON namespace_row.oid = table_row.relnamespace
                        WHERE namespace_row.nspname = current_schema()
                          AND constraint_row.conname IN (
                            'fk_users_faculty_id_faculties',
                            'uq_careers_id_faculty_id',
                            'fk_review_items_scope_faculty_id_faculties',
                            'fk_review_items_scope_career_faculty_careers',
                            'ck_review_items_scope_hierarchy',
                            'ck_review_items_scope_resolution'
                          )
                    """))
                }
                self.assertEqual(set(constraints), {
                    "fk_users_faculty_id_faculties",
                    "uq_careers_id_faculty_id",
                    "fk_review_items_scope_faculty_id_faculties",
                    "fk_review_items_scope_career_faculty_careers",
                    "ck_review_items_scope_hierarchy",
                    "ck_review_items_scope_resolution",
                })
                self.assertEqual(
                    constraints["fk_users_faculty_id_faculties"],
                    (
                        "f",
                        "FOREIGN KEY (faculty_id) REFERENCES faculties(id) ON DELETE RESTRICT",
                        "r",
                    ),
                )
                self.assertEqual(
                    constraints["uq_careers_id_faculty_id"],
                    ("u", "UNIQUE (id, faculty_id)", " "),
                )
                self.assertEqual(
                    constraints["fk_review_items_scope_faculty_id_faculties"],
                    (
                        "f",
                        "FOREIGN KEY (scope_faculty_id) REFERENCES faculties(id) ON DELETE RESTRICT",
                        "r",
                    ),
                )
                self.assertEqual(
                    constraints["fk_review_items_scope_career_faculty_careers"],
                    (
                        "f",
                        "FOREIGN KEY (scope_career_id, scope_faculty_id) "
                        "REFERENCES careers(id, faculty_id) ON DELETE RESTRICT",
                        "r",
                    ),
                )
                self.assertEqual(
                    constraints["ck_review_items_scope_hierarchy"][1],
                    "CHECK (scope_career_id IS NULL OR scope_faculty_id IS NOT NULL)",
                )
                self.assertEqual(
                    constraints["ck_review_items_scope_resolution"][1],
                    "CHECK (scope_faculty_id IS NULL AND scope_career_id IS NULL "
                    "AND scope_resolution_reason IS NOT NULL AND "
                    "(scope_resolution_reason::text = ANY (ARRAY["
                    "'unresolved_no_persisted_scope'::character varying, "
                    "'unresolved_cross_faculty'::character varying, "
                    "'unresolved_missing_target'::character varying]::text[])) "
                    "OR scope_faculty_id IS NOT NULL AND scope_resolution_reason IS NULL)",
                )

                indexes = {
                    row.name: tuple(row.columns)
                    for row in connection.execute(text("""
                        SELECT index_row.relname AS name,
                               ARRAY_AGG(attribute_row.attname ORDER BY key_row.ordinality) AS columns
                        FROM pg_index AS index_catalog
                        JOIN pg_class AS index_row
                          ON index_row.oid = index_catalog.indexrelid
                        JOIN pg_class AS table_row
                          ON table_row.oid = index_catalog.indrelid
                        JOIN pg_namespace AS namespace_row
                          ON namespace_row.oid = table_row.relnamespace
                        JOIN LATERAL UNNEST(index_catalog.indkey)
                          WITH ORDINALITY AS key_row(attribute_number, ordinality) ON TRUE
                        JOIN pg_attribute AS attribute_row
                          ON attribute_row.attrelid = table_row.oid
                         AND attribute_row.attnum = key_row.attribute_number
                        WHERE namespace_row.nspname = current_schema()
                          AND index_row.relname IN (
                            'ix_users_faculty_id',
                            'ix_review_items_scope_faculty_queue',
                            'ix_review_items_scope_career_queue'
                          )
                        GROUP BY index_row.relname
                    """))
                }
                self.assertEqual(indexes, {
                    "ix_users_faculty_id": ("faculty_id",),
                    "ix_review_items_scope_faculty_queue": (
                        "scope_faculty_id",
                        "case_status",
                        "automatic_priority",
                        "created_at",
                        "id",
                    ),
                    "ix_review_items_scope_career_queue": (
                        "scope_career_id",
                        "case_status",
                        "automatic_priority",
                        "created_at",
                        "id",
                    ),
                })

    def test_normal_runner_applies_only_pending_0022_to_initialized_0021(self) -> None:
        module = self._migration_module()
        with self._prepared_0021("scope0022_runner") as engine:
            before = self._protected_snapshot(engine)
            self.assertEqual(run_migrations(engine), [VERSION])
            self.assertEqual(self._protected_snapshot(engine), before)
            with engine.connect() as connection:
                module.assert_schema(connection)
                versions = tuple(connection.execute(text(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )).scalars())
            self.assertEqual(len(versions), 22)
            self.assertEqual(versions[-1], VERSION)

    def test_backfill_uses_only_persisted_relationships_and_fails_closed(self) -> None:
        module = self._migration_module()
        with self._prepared_0021("scope0022_backfill") as engine:
            module.upgrade(engine)
            with engine.connect() as connection:
                scopes = {
                    row.stable_target_key: (
                        row.scope_faculty_id,
                        row.scope_career_id,
                        row.scope_resolution_reason,
                    )
                    for row in connection.execute(text("""
                        SELECT stable_target_key, scope_faculty_id, scope_career_id,
                               scope_resolution_reason
                        FROM review_items
                        ORDER BY stable_target_key
                    """))
                }

            self.assertEqual(scopes["person:single"], (1, 10, None))
            self.assertEqual(scopes["person:production"], (1, 11, None))
            self.assertEqual(scopes["person:project-same-faculty"], (1, None, None))
            self.assertEqual(
                scopes["person:project-cross-faculty"],
                (None, None, "unresolved_cross_faculty"),
            )
            self.assertEqual(
                scopes["person:external-only"],
                (None, None, "unresolved_no_persisted_scope"),
            )
            self.assertEqual(scopes["production:single"], (1, 10, None))
            self.assertEqual(scopes["production:same-faculty"], (1, None, None))
            self.assertEqual(
                scopes["production:cross-faculty"],
                (None, None, "unresolved_cross_faculty"),
            )
            self.assertEqual(scopes["author:single"], (1, 10, None))
            self.assertEqual(scopes["author:same-faculty"], (1, None, None))
            self.assertEqual(
                scopes["author:cross-faculty"],
                (None, None, "unresolved_cross_faculty"),
            )
            self.assertEqual(
                scopes["entity:strings-must-not-resolve"],
                (None, None, "unresolved_no_persisted_scope"),
            )
            self.assertEqual(
                scopes["external:email-must-not-resolve"],
                (None, None, "unresolved_no_persisted_scope"),
            )
            self.assertEqual(
                scopes["missing:target"],
                (None, None, "unresolved_missing_target"),
            )
            self.assertEqual(
                scopes["missing:null-pk"],
                (None, None, "unresolved_missing_target"),
            )

    def test_backfill_processes_more_than_one_bounded_batch(self) -> None:
        module = self._migration_module()
        with self._prepared_0021("scope0022_batches") as engine:
            rows = [
                {
                    "id": UUID(int=10_000 + offset),
                    "key": f"batch:external:{offset}",
                }
                for offset in range(501)
            ]
            with engine.begin() as connection:
                connection.execute(text("""
                    INSERT INTO review_items (
                        id, stable_target_key, target_table, target_pk,
                        case_status, automatic_priority
                    ) VALUES (
                        :id, :key, 'external_researchers', 7000,
                        'pending', 0
                    )
                """), rows)

            module.upgrade(engine)
            with engine.connect() as connection:
                result = connection.execute(text("""
                    SELECT COUNT(*),
                           COUNT(*) FILTER (
                               WHERE scope_faculty_id IS NULL
                                 AND scope_career_id IS NULL
                                 AND scope_resolution_reason = 'unresolved_no_persisted_scope'
                           )
                    FROM review_items
                    WHERE stable_target_key LIKE 'batch:external:%'
                """
                )).one()
            self.assertEqual(tuple(result), (501, 501))

    def test_failed_later_batch_is_resumable_without_rewriting_completed_rows(self) -> None:
        module = self._migration_module()
        with self._prepared_0021("scope0022_resume") as engine:
            rows = [
                {
                    "id": UUID(int=20_000 + offset),
                    "key": f"resume:external:{offset}",
                }
                for offset in range(501)
            ]
            with engine.begin() as connection:
                connection.execute(text("""
                    INSERT INTO review_items (
                        id, stable_target_key, target_table, target_pk,
                        case_status, automatic_priority
                    ) VALUES (
                        :id, :key, 'external_researchers', 7000,
                        'pending', 0
                    )
                """), rows)

            original_backfill_batch = module._backfill_batch
            calls = 0

            def fail_on_second_batch(connection, batch_ids):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError("simulated second-batch failure")
                return original_backfill_batch(connection, batch_ids)

            with patch.object(module, "_backfill_batch", side_effect=fail_on_second_batch):
                with self.assertRaisesRegex(RuntimeError, "simulated second-batch failure"):
                    module.upgrade(engine)

            with engine.connect() as connection:
                lock_was_released = connection.execute(
                    text("SELECT pg_try_advisory_lock(:lock_key)"),
                    {"lock_key": module._MIGRATION_LOCK_KEY},
                ).scalar_one()
                self.assertTrue(lock_was_released)
                connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_key)"),
                    {"lock_key": module._MIGRATION_LOCK_KEY},
                )
                connection.commit()

            with engine.connect() as connection:
                partial = connection.execute(text("""
                    SELECT COUNT(*) FILTER (
                               WHERE scope_faculty_id IS NOT NULL
                                  OR scope_career_id IS NOT NULL
                                  OR scope_resolution_reason IS NOT NULL
                           ) AS processed,
                           COUNT(*) FILTER (
                               WHERE scope_faculty_id IS NULL
                                 AND scope_career_id IS NULL
                                 AND scope_resolution_reason IS NULL
                           ) AS pending
                    FROM review_items
                """
                )).one()
            self.assertEqual(tuple(partial), (500, 16))

            module.upgrade(engine)
            with engine.connect() as connection:
                module.assert_schema(connection)
                completed = connection.execute(text("""
                    SELECT COUNT(*) FILTER (
                               WHERE scope_faculty_id IS NOT NULL
                                  OR scope_career_id IS NOT NULL
                                  OR scope_resolution_reason IS NOT NULL
                           ) AS processed,
                           COUNT(*) FILTER (
                               WHERE scope_faculty_id IS NULL
                                 AND scope_career_id IS NULL
                                 AND scope_resolution_reason IS NULL
                           ) AS pending
                    FROM review_items
                """
                )).one()
            self.assertEqual(tuple(completed), (516, 0))

    def test_advisory_lock_refuses_concurrent_upgrade(self) -> None:
        module = self._migration_module()
        with self._prepared_0021("scope0022_lock") as engine:
            with engine.connect() as blocking_connection:
                blocking_connection.execute(
                    text("SELECT pg_advisory_lock(:lock_key)"),
                    {"lock_key": module._MIGRATION_LOCK_KEY},
                )
                blocking_connection.commit()
                try:
                    with self.assertRaisesRegex(RuntimeError, "holds the advisory lock"):
                        module.upgrade(engine)
                finally:
                    blocking_connection.execute(
                        text("SELECT pg_advisory_unlock(:lock_key)"),
                        {"lock_key": module._MIGRATION_LOCK_KEY},
                    )
                    blocking_connection.commit()

            module.upgrade(engine)

    def test_resumable_catalog_recomputes_scope_and_rejects_historical_faculty_assignment(self) -> None:
        module = self._migration_module()
        with self._prepared_0021("scope0022_tampered") as engine:
            with engine.begin() as connection:
                module._add_columns(connection)
                connection.execute(text(
                    "UPDATE users SET faculty_id = 1 WHERE id = 1"
                ))
                connection.execute(text("""
                    UPDATE review_items
                    SET scope_faculty_id = 2,
                        scope_career_id = 20,
                        scope_resolution_reason = NULL
                    WHERE stable_target_key = 'person:single'
                """))

            with self.assertRaisesRegex(RuntimeError, "historical users.*faculty_id"):
                module.upgrade(engine)

            with engine.begin() as connection:
                connection.execute(text("UPDATE users SET faculty_id = NULL WHERE id = 1"))
            module.upgrade(engine)
            with engine.connect() as connection:
                corrected = connection.execute(text("""
                    SELECT scope_faculty_id, scope_career_id, scope_resolution_reason
                    FROM review_items
                    WHERE stable_target_key = 'person:single'
                """)).one()
            self.assertEqual(tuple(corrected), (1, 10, None))

    def test_graph_change_between_batches_is_refused_then_cleanly_recomputed(self) -> None:
        module = self._migration_module()
        with self._prepared_0021("scope0022_graph") as engine:
            original_next = module._next_backfill_ids
            graph_changed = False

            def change_graph_after_last_batch(connection, lower_bound):
                nonlocal graph_changed
                batch_ids = original_next(connection, lower_bound)
                if not batch_ids and not graph_changed:
                    graph_changed = True
                    with engine.begin() as other_connection:
                        other_connection.execute(text(
                            "UPDATE project_teachers SET teacher_id = 200 WHERE id = 2"
                        ))
                return batch_ids

            with patch.object(
                module,
                "_next_backfill_ids",
                side_effect=change_graph_after_last_batch,
            ):
                with self.assertRaisesRegex(RuntimeError, "input graph changed"):
                    module.upgrade(engine)

            module.upgrade(engine)
            with engine.connect() as connection:
                scope = connection.execute(text("""
                    SELECT scope_faculty_id, scope_career_id, scope_resolution_reason
                    FROM review_items
                    WHERE stable_target_key = 'person:project-same-faculty'
                """)).one()
            self.assertEqual(tuple(scope), (None, None, "unresolved_cross_faculty"))

    def test_aba_graph_change_is_detected_by_final_scope_validation(self) -> None:
        module = self._migration_module()
        with self._prepared_0021("scope0022_aba") as engine:
            original_backfill_batch = module._backfill_batch
            aba_completed = False

            def resolve_during_temporary_graph(connection, batch_ids):
                nonlocal aba_completed
                if aba_completed:
                    return original_backfill_batch(connection, batch_ids)
                with engine.begin() as other_connection:
                    other_connection.execute(text(
                        "UPDATE project_teachers SET teacher_id = 200 WHERE id = 2"
                    ))
                result = original_backfill_batch(connection, batch_ids)
                with engine.begin() as other_connection:
                    other_connection.execute(text(
                        "UPDATE project_teachers SET teacher_id = 110 WHERE id = 2"
                    ))
                aba_completed = True
                return result

            with patch.object(
                module,
                "_backfill_batch",
                side_effect=resolve_during_temporary_graph,
            ):
                with self.assertRaisesRegex(RuntimeError, "scope diverged"):
                    module.upgrade(engine)

            module.upgrade(engine)
            with engine.connect() as connection:
                scope = connection.execute(text("""
                    SELECT scope_faculty_id, scope_career_id, scope_resolution_reason
                    FROM review_items
                    WHERE stable_target_key = 'person:project-same-faculty'
                """)).one()
            self.assertEqual(tuple(scope), (1, None, None))

    def test_concurrent_insert_cannot_expand_a_selected_500_row_transaction(self) -> None:
        module = self._migration_module()
        with self._prepared_0021("scope0022_exactbatch") as engine:
            rows = [
                {
                    "id": UUID(int=30_000 + offset),
                    "key": f"exact-batch:external:{offset}",
                }
                for offset in range(501)
            ]
            with engine.begin() as connection:
                connection.execute(text("""
                    INSERT INTO review_items (
                        id, stable_target_key, target_table, target_pk,
                        case_status, automatic_priority
                    ) VALUES (
                        :id, :key, 'external_researchers', 7000,
                        'pending', 0
                    )
                """), rows)

            original_backfill_batch = module._backfill_batch
            inserted = False
            updated_counts: list[int] = []

            def insert_inside_selected_range(connection, batch_ids):
                nonlocal inserted
                self.assertLessEqual(len(batch_ids), 500)
                if not inserted:
                    inserted = True
                    with engine.begin() as other_connection:
                        other_connection.execute(text("""
                            INSERT INTO review_items (
                                id, stable_target_key, target_table, target_pk,
                                case_status, automatic_priority
                            ) VALUES (
                                :id, 'exact-batch:concurrent',
                                'external_researchers', 7000, 'pending', 0
                            )
                        """), {"id": UUID(int=25_000)})
                updated = original_backfill_batch(connection, batch_ids)
                updated_counts.append(updated)
                return updated

            with patch.object(
                module,
                "_backfill_batch",
                side_effect=insert_inside_selected_range,
            ):
                with self.assertRaisesRegex(RuntimeError, "input graph changed"):
                    module.upgrade(engine)
            self.assertTrue(updated_counts)
            self.assertLessEqual(max(updated_counts), 500)

            module.upgrade(engine)
            with engine.connect() as connection:
                pending = connection.execute(text("""
                    SELECT COUNT(*)
                    FROM review_items
                    WHERE scope_faculty_id IS NULL
                      AND scope_career_id IS NULL
                      AND scope_resolution_reason IS NULL
                """)).scalar_one()
            self.assertEqual(pending, 0)

    def test_upgrade_preserves_historical_and_scientific_rows(self) -> None:
        module = self._migration_module()
        with self._prepared_0021("scope0022_immutable") as engine:
            before = self._protected_snapshot(engine)
            module.upgrade(engine)
            after = self._protected_snapshot(engine)
            self.assertEqual(after, before)

            with engine.connect() as connection:
                users = tuple(connection.execute(text(
                    "SELECT id, career_id, faculty_id FROM users ORDER BY id"
                )))
            self.assertEqual(users, ((1, None, None), (2, 10, None)))

    def test_diagnostics_are_aggregate_and_hash_the_full_fk_input_graph(self) -> None:
        module = self._migration_module()
        with self._prepared_0021("scope0022_diagnostics") as engine:
            with engine.connect() as connection:
                before_hash = module._scope_input_hash(connection)
            with engine.begin() as connection:
                connection.execute(text(
                    "UPDATE project_teachers SET teacher_id = 200 WHERE id = 2"
                ))
            with engine.connect() as connection:
                changed_hash = module._scope_input_hash(connection)
            self.assertNotEqual(changed_hash, before_hash)

            with self.assertLogs(module.__name__, level="INFO") as captured:
                module.upgrade(engine)
            log_text = "\n".join(captured.output)
            for expected in (
                "total=15",
                "career_scoped=4",
                "faculty_scoped=2",
                "unresolved=9",
                "unresolved_cross_faculty",
                "unresolved_no_persisted_scope",
                "unresolved_missing_target",
                "input_sha256=",
                "output_sha256=",
            ):
                self.assertIn(expected, log_text)
            for prohibited in (
                "faculty@example.test",
                "career@example.test",
                "Teacher A",
                "person:single",
                "00000000-0000-0000-0000-000000000001",
            ):
                self.assertNotIn(prohibited, log_text)

    def test_constraints_reject_invalid_hierarchy_reason_and_composite_scope(self) -> None:
        module = self._migration_module()
        with self._prepared_0021("scope0022_checks") as engine:
            module.upgrade(engine)
            for statement, message in (
                (
                    "UPDATE review_items SET scope_faculty_id = NULL, "
                    "scope_career_id = 10, scope_resolution_reason = NULL "
                    "WHERE stable_target_key = 'person:single'",
                    "career without faculty",
                ),
                (
                    "UPDATE review_items SET scope_faculty_id = NULL, "
                    "scope_career_id = NULL, scope_resolution_reason = NULL "
                    "WHERE stable_target_key = 'person:single'",
                    "unresolved without reason",
                ),
                (
                    "UPDATE review_items SET scope_faculty_id = NULL, "
                    "scope_career_id = NULL, scope_resolution_reason = 'guessed_by_name' "
                    "WHERE stable_target_key = 'person:single'",
                    "unapproved reason",
                ),
                (
                    "UPDATE review_items SET scope_faculty_id = 2, "
                    "scope_career_id = 10, scope_resolution_reason = NULL "
                    "WHERE stable_target_key = 'person:single'",
                    "career/faculty mismatch",
                ),
                (
                    "UPDATE review_items SET scope_faculty_id = 1, "
                    "scope_career_id = NULL, "
                    "scope_resolution_reason = 'unresolved_no_persisted_scope' "
                    "WHERE stable_target_key = 'person:single'",
                    "resolved row with unresolved reason",
                ),
            ):
                with self.subTest(message=message):
                    with self.assertRaises(DBAPIError):
                        with engine.begin() as connection:
                            connection.execute(text(statement))

    def test_downgrade_removes_only_0022_owned_objects(self) -> None:
        module = self._migration_module()
        with self._prepared_0021("scope0022_down") as engine:
            before_catalog = self._catalog_snapshot(engine)
            before_rows = self._protected_snapshot(engine)
            module.upgrade(engine)
            with self.assertRaisesRegex(RuntimeError, "must be recorded"):
                module.downgrade(engine)
            with engine.begin() as connection:
                connection.execute(
                    text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                    {"version": VERSION},
                )
            module.downgrade(engine)
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM schema_migrations WHERE version = :version"),
                    {"version": VERSION},
                )
            self.assertEqual(self._catalog_snapshot(engine), before_catalog)
            self.assertEqual(self._protected_snapshot(engine), before_rows)

    def test_upgrade_and_downgrade_match_the_full_current_0021_metadata_catalog(self) -> None:
        module = self._migration_module()
        with isolated_postgres_schema(self.database_url, "scope0022_full") as engine:
            import app.models  # noqa: F401 -- registers every mapped table with Base.metadata
            from app.core.database import Base

            Base.metadata.create_all(engine)
            with engine.begin() as connection:
                connection.execute(text(
                    "CREATE TABLE schema_migrations ("
                    "version VARCHAR(120) PRIMARY KEY, "
                    "applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
                ))
                for version, _upgrade in migration_registry.MIGRATIONS[:-1]:
                    connection.execute(
                        text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                        {"version": version},
                    )
                connection.execute(
                    text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                    {"version": VERSION},
                )

            module.downgrade(engine)
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM schema_migrations WHERE version = :version"),
                    {"version": VERSION},
                )
            before_catalog = self._catalog_snapshot(engine)
            module.upgrade(engine)
            with engine.connect() as connection:
                module.assert_schema(connection)
            with engine.begin() as connection:
                connection.execute(
                    text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                    {"version": VERSION},
                )
            module.downgrade(engine)
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM schema_migrations WHERE version = :version"),
                    {"version": VERSION},
                )
            self.assertEqual(self._catalog_snapshot(engine), before_catalog)

    def test_complete_unrecorded_upgrade_recovers_and_runner_skips_after_recording(self) -> None:
        module = self._migration_module()
        with self._prepared_0021("scope0022_second") as engine:
            module.upgrade(engine)
            catalog_after_upgrade = self._catalog_snapshot(engine)
            rows_after_upgrade = self._protected_snapshot(engine)
            module.upgrade(engine)
            self.assertEqual(self._catalog_snapshot(engine), catalog_after_upgrade)
            self.assertEqual(self._protected_snapshot(engine), rows_after_upgrade)

            with engine.begin() as connection:
                connection.execute(
                    text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                    {"version": VERSION},
                )

            original = migration_registry.MIGRATIONS
            try:
                migration_registry.MIGRATIONS = [*original, (VERSION, module.upgrade)]
                self.assertEqual(run_migrations(engine), [])
            finally:
                migration_registry.MIGRATIONS = original

    def test_0021_is_byte_identical(self) -> None:
        digest = hashlib.sha256(MIGRATION_0021_PATH.read_bytes()).hexdigest().upper()
        self.assertEqual(digest, MIGRATION_0021_SHA256)

    @staticmethod
    def _migration_module():
        return importlib.import_module(MIGRATION_MODULE)

    @contextmanager
    def _prepared_0021(self, prefix: str):
        with isolated_postgres_schema(self.database_url, prefix) as engine:
            with engine.begin() as connection:
                self._create_0021_compatible_catalog(connection)
                self._seed_scope_cases(connection)
            yield engine

    @staticmethod
    def _create_0021_compatible_catalog(connection) -> None:
        statements = (
            "CREATE TABLE faculties (id INTEGER PRIMARY KEY, name VARCHAR(180) NOT NULL UNIQUE)",
            "CREATE TABLE careers (id INTEGER PRIMARY KEY, faculty_id INTEGER NOT NULL "
            "REFERENCES faculties(id) ON DELETE RESTRICT, name VARCHAR(180) NOT NULL UNIQUE, "
            "code VARCHAR(20) NOT NULL UNIQUE)",
            "CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(180) NOT NULL UNIQUE, "
            "full_name VARCHAR(180) NOT NULL, hashed_password VARCHAR(255) NOT NULL, "
            "role VARCHAR(40) NOT NULL, career_id INTEGER NULL REFERENCES careers(id), "
            "is_active BOOLEAN NOT NULL DEFAULT TRUE)",
            "CREATE TABLE teachers (id INTEGER PRIMARY KEY, career_id INTEGER NOT NULL "
            "REFERENCES careers(id) ON DELETE RESTRICT, full_name VARCHAR(180) NOT NULL)",
            "CREATE TABLE research_projects (id INTEGER PRIMARY KEY, name VARCHAR(250) NOT NULL)",
            "CREATE TABLE project_teachers (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL "
            "REFERENCES research_projects(id), teacher_id INTEGER NOT NULL REFERENCES teachers(id), "
            "CONSTRAINT uq_project_teacher UNIQUE (project_id, teacher_id))",
            "CREATE TABLE research_entities (id INTEGER PRIMARY KEY, academic_unit VARCHAR(220), "
            "career_name VARCHAR(220), normalized_name VARCHAR(380))",
            "CREATE TABLE external_researchers (id INTEGER PRIMARY KEY, full_name VARCHAR(180), "
            "normalized_name VARCHAR(220), institution VARCHAR(220))",
            "CREATE TABLE scientific_productions (id INTEGER PRIMARY KEY, teacher_id INTEGER NULL "
            "REFERENCES teachers(id), research_entity_id INTEGER NULL REFERENCES research_entities(id), "
            "title VARCHAR(250) NOT NULL)",
            "CREATE TABLE scientific_production_authors (id INTEGER PRIMARY KEY, "
            "production_id INTEGER NOT NULL REFERENCES scientific_productions(id), "
            "teacher_id INTEGER NULL REFERENCES teachers(id), external_researcher_id INTEGER NULL "
            "REFERENCES external_researchers(id), research_entity_id INTEGER NULL "
            "REFERENCES research_entities(id), normalized_author_name VARCHAR(220))",
            "CREATE TABLE person_roles (id INTEGER PRIMARY KEY, teacher_id INTEGER NULL "
            "REFERENCES teachers(id), external_researcher_id INTEGER NULL "
            "REFERENCES external_researchers(id), scientific_production_id INTEGER NULL "
            "REFERENCES scientific_productions(id), research_project_id INTEGER NULL "
            "REFERENCES research_projects(id), research_entity_id INTEGER NULL "
            "REFERENCES research_entities(id), normalized_name VARCHAR(220), raw_value TEXT)",
            "CREATE TABLE review_items (id UUID PRIMARY KEY, stable_target_key VARCHAR(128) NOT NULL, "
            "target_table VARCHAR(80) NOT NULL, target_pk BIGINT NULL, case_status VARCHAR(40) NOT NULL "
            "DEFAULT 'pending', automatic_priority SMALLINT NOT NULL DEFAULT 0, "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE review_decisions (id UUID PRIMARY KEY, review_item_id UUID NOT NULL "
            "REFERENCES review_items(id), payload JSONB NOT NULL)",
            "CREATE TABLE canonical_identities (id UUID PRIMARY KEY, canonical_name VARCHAR(220))",
            "CREATE TABLE audit_events (id UUID PRIMARY KEY, payload JSONB NOT NULL)",
            "CREATE TABLE schema_migrations (version VARCHAR(120) PRIMARY KEY, "
            "applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        )
        for statement in statements:
            connection.execute(text(statement))
        for version, _upgrade in migration_registry.MIGRATIONS[:-1]:
            connection.execute(
                text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                {"version": version},
            )

    @staticmethod
    def _seed_scope_cases(connection) -> None:
        statements = (
            "INSERT INTO faculties VALUES (1, 'Faculty One'), (2, 'Faculty Two')",
            "INSERT INTO careers VALUES (10, 1, 'Career Alpha', 'A'), "
            "(11, 1, 'Career Beta', 'B'), (20, 2, 'Career Gamma', 'C')",
            "INSERT INTO users (id, email, full_name, hashed_password, role, career_id) VALUES "
            "(1, 'faculty@example.test', 'Career Alpha', 'demo', 'FACULTY_ADMIN', NULL), "
            "(2, 'career@example.test', 'Faculty One', 'demo', 'CAREER_MANAGER', 10)",
            "INSERT INTO teachers VALUES (100, 10, 'Teacher A'), (110, 11, 'Teacher B'), "
            "(200, 20, 'Teacher C')",
            "INSERT INTO research_projects VALUES (1000, 'Same faculty'), (2000, 'Cross faculty')",
            "INSERT INTO project_teachers VALUES (1, 1000, 100), (2, 1000, 110), "
            "(3, 2000, 100), (4, 2000, 200)",
            "INSERT INTO research_entities VALUES "
            "(6000, 'Faculty One', 'Career Alpha', 'Career Alpha')",
            "INSERT INTO external_researchers VALUES "
            "(7000, 'faculty@example.test', 'Career Alpha', 'Faculty One')",
            "INSERT INTO scientific_productions VALUES "
            "(3000, 100, NULL, 'Single'), (3001, 110, NULL, 'Second career'), "
            "(3002, NULL, 6000, 'String-only scope'), (3003, 100, NULL, 'Same faculty authors'), "
            "(3004, 100, NULL, 'Cross faculty authors'), "
            "(3005, 100, NULL, 'Single career product')",
            "INSERT INTO scientific_production_authors VALUES "
            "(4000, 3000, 100, NULL, NULL, 'Teacher A'), "
            "(4001, 3000, 110, NULL, NULL, 'Teacher B'), "
            "(4002, 3000, 200, NULL, NULL, 'Teacher C'), "
            "(4003, 3002, NULL, 7000, NULL, 'faculty@example.test'), "
            "(4004, 3003, 110, NULL, NULL, 'Teacher B'), "
            "(4005, 3004, 200, NULL, NULL, 'Teacher C')",
            "INSERT INTO person_roles VALUES "
            "(5000, 100, NULL, NULL, NULL, NULL, 'Teacher A', 'Faculty One'), "
            "(5001, NULL, NULL, 3001, NULL, NULL, 'Teacher B', 'Career Beta'), "
            "(5002, NULL, NULL, NULL, 1000, NULL, 'Project', 'Faculty One'), "
            "(5003, NULL, NULL, NULL, 2000, NULL, 'Project', 'Faculty One'), "
            "(5004, NULL, 7000, NULL, NULL, NULL, 'faculty@example.test', 'Career Alpha')",
        )
        for statement in statements:
            connection.execute(text(statement))

        review_rows = (
            ("00000000-0000-0000-0000-000000000001", "person:single", "person_roles", 5000),
            ("00000000-0000-0000-0000-000000000002", "person:production", "person_roles", 5001),
            ("00000000-0000-0000-0000-000000000003", "person:project-same-faculty", "person_roles", 5002),
            ("00000000-0000-0000-0000-000000000004", "person:project-cross-faculty", "person_roles", 5003),
            ("00000000-0000-0000-0000-000000000005", "person:external-only", "person_roles", 5004),
            ("00000000-0000-0000-0000-000000000006", "production:single", "scientific_productions", 3005),
            ("00000000-0000-0000-0000-000000000007", "production:same-faculty", "scientific_productions", 3003),
            ("00000000-0000-0000-0000-000000000008", "production:cross-faculty", "scientific_productions", 3004),
            ("00000000-0000-0000-0000-000000000009", "author:single", "scientific_production_authors", 4000),
            ("00000000-0000-0000-0000-000000000010", "author:same-faculty", "scientific_production_authors", 4001),
            ("00000000-0000-0000-0000-000000000011", "author:cross-faculty", "scientific_production_authors", 4002),
            ("00000000-0000-0000-0000-000000000012", "entity:strings-must-not-resolve", "research_entities", 6000),
            ("00000000-0000-0000-0000-000000000013", "external:email-must-not-resolve", "external_researchers", 7000),
            ("00000000-0000-0000-0000-000000000014", "missing:target", "person_roles", 9999),
            ("00000000-0000-0000-0000-000000000015", "missing:null-pk", "person_roles", None),
        )
        for row_id, key, target_table, target_pk in review_rows:
            connection.execute(text("""
                INSERT INTO review_items (
                    id, stable_target_key, target_table, target_pk,
                    case_status, automatic_priority
                ) VALUES (
                    CAST(:id AS UUID), :key, :target_table, :target_pk,
                    'pending', 5
                )
            """), {
                "id": row_id,
                "key": key,
                "target_table": target_table,
                "target_pk": target_pk,
            })

        connection.execute(text(
            "INSERT INTO review_decisions VALUES "
            "('10000000-0000-0000-0000-000000000001', "
            "'00000000-0000-0000-0000-000000000001', '{\"decision\": \"keep\"}')"
        ))
        connection.execute(text(
            "INSERT INTO canonical_identities VALUES "
            "('20000000-0000-0000-0000-000000000001', 'Immutable Identity')"
        ))
        connection.execute(text(
            "INSERT INTO audit_events VALUES "
            "('30000000-0000-0000-0000-000000000001', '{\"event\": \"keep\"}')"
        ))

    @staticmethod
    def _protected_snapshot(engine) -> tuple[tuple[str, tuple[tuple[object, ...], ...]], ...]:
        queries = {
            "users": "SELECT id, email, full_name, role, career_id, is_active FROM users ORDER BY id",
            "teachers": "SELECT id, career_id, full_name FROM teachers ORDER BY id",
            "projects": "SELECT id, name FROM research_projects ORDER BY id",
            "project_teachers": "SELECT id, project_id, teacher_id FROM project_teachers ORDER BY id",
            "entities": "SELECT id, academic_unit, career_name, normalized_name FROM research_entities ORDER BY id",
            "externals": "SELECT id, full_name, normalized_name, institution FROM external_researchers ORDER BY id",
            "productions": "SELECT id, teacher_id, research_entity_id, title FROM scientific_productions ORDER BY id",
            "authors": "SELECT id, production_id, teacher_id, external_researcher_id, "
            "research_entity_id, normalized_author_name FROM scientific_production_authors ORDER BY id",
            "person_roles": "SELECT id, teacher_id, external_researcher_id, scientific_production_id, "
            "research_project_id, research_entity_id, normalized_name, raw_value FROM person_roles ORDER BY id",
            "review_items": "SELECT id, stable_target_key, target_table, target_pk, case_status, "
            "automatic_priority, created_at FROM review_items ORDER BY id",
            "decisions": "SELECT id, review_item_id, payload::text FROM review_decisions ORDER BY id",
            "identities": "SELECT id, canonical_name FROM canonical_identities ORDER BY id",
            "audit": "SELECT id, payload::text FROM audit_events ORDER BY id",
        }
        with engine.connect() as connection:
            return tuple(
                (name, tuple(tuple(row) for row in connection.execute(text(query))))
                for name, query in queries.items()
            )

    @staticmethod
    def _catalog_snapshot(engine) -> tuple[tuple[object, ...], ...]:
        with engine.connect() as connection:
            return tuple(tuple(row) for row in connection.execute(text("""
                SELECT object_type, object_name, definition
                FROM (
                    SELECT 'column' AS object_type,
                           table_name || '.' || column_name AS object_name,
                           data_type || ':' || is_nullable || ':' ||
                           COALESCE(character_maximum_length::text, '') AS definition
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                    UNION ALL
                    SELECT 'constraint', constraint_row.conname,
                           pg_get_constraintdef(constraint_row.oid, true)
                    FROM pg_constraint AS constraint_row
                    JOIN pg_class AS table_row ON table_row.oid = constraint_row.conrelid
                    JOIN pg_namespace AS namespace_row ON namespace_row.oid = table_row.relnamespace
                    WHERE namespace_row.nspname = current_schema()
                    UNION ALL
                    SELECT 'index', index_row.relname, pg_get_indexdef(index_row.oid)
                    FROM pg_index AS index_catalog
                    JOIN pg_class AS index_row ON index_row.oid = index_catalog.indexrelid
                    JOIN pg_class AS table_row ON table_row.oid = index_catalog.indrelid
                    JOIN pg_namespace AS namespace_row ON namespace_row.oid = table_row.relnamespace
                    WHERE namespace_row.nspname = current_schema()
                ) AS catalog
                ORDER BY object_type, object_name, definition
            """)))


if __name__ == "__main__":
    unittest.main()
