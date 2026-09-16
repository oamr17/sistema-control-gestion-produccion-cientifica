from __future__ import annotations

import importlib
import json
import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core import migrations as migration_registry
from app.core.migrations import run_migrations
from tests.support.postgres import isolated_postgres_schema, require_b2b1_test_database_url


MIGRATION_MODULE = "app.migrations.versions.20260713_0018_human_review_core"
VERSION = "20260713_0018_human_review_core"
DOWN_REVISION = "20260713_0017_b2b_capabilities"
LATER_VERSIONS = (
    "20260713_0019_human_review_projection",
    "20260713_0020_human_review_audit",
)
PREDECESSOR_VERSIONS = (
    "20260628_0001_add_import_jobs_batch_id",
    "20260628_0002_import_batches_and_job_state",
    "20260628_0003_fix_import_batches_sequence",
    "20260628_0004_normalized_dashboard_status_fields",
    "20260628_0005_import_job_diagnostics",
    "20260628_0006_import_job_error_details",
    "20260628_0007_external_researchers_and_review_items",
    "20260628_0008_normalization_traceability",
    "20260629_0009_scientific_production_authors",
    "20260629_0010_person_roles",
    "20260629_0011_research_entities_and_pending_products",
    "20260629_0012_author_traceability",
    "20260710_0013_teacher_person_role_validation",
    "20260711_0014_dropbox_document_versioning",
    "20260711_0015_dropbox_revision_uniqueness",
    "20260712_0016_canonical_identity_fields",
    DOWN_REVISION,
)

REVIEW_ITEM_COLUMNS = (
    ("id", "uuid", "uuid", None, "NO", None),
    ("case_type", "character varying", "varchar", 60, "NO", None),
    ("stable_target_key", "character varying", "varchar", 128, "NO", None),
    ("target_table", "character varying", "varchar", 80, "NO", None),
    ("target_pk", "bigint", "int8", None, "YES", None),
    ("document_key", "character varying", "varchar", 900, "NO", None),
    ("source_revision", "character varying", "varchar", 120, "YES", None),
    ("source_page", "integer", "int4", None, "YES", None),
    ("source_section", "character varying", "varchar", 120, "NO", None),
    ("row_or_block_id", "text", "text", None, "NO", None),
    ("field_path", "character varying", "varchar", 120, "NO", None),
    ("raw_value_sha256", "character", "bpchar", 64, "NO", None),
    ("period_id", "integer", "int4", None, "YES", None),
    ("relationship_key", "character varying", "varchar", 320, "YES", None),
    ("case_status", "character varying", "varchar", 40, "NO", "'pending'::character varying"),
    ("scientific_status", "character varying", "varchar", 40, "NO", "'pending'::character varying"),
    ("automatic_priority", "smallint", "int2", None, "NO", "0"),
    ("manual_priority", "smallint", "int2", None, "YES", None),
    ("possible_kpi_impact", "boolean", "bool", None, "NO", "false"),
    ("current_decision_id", "uuid", "uuid", None, "YES", None),
    ("version", "integer", "int4", None, "NO", "1"),
    ("created_at", "timestamp with time zone", "timestamptz", None, "NO", "CURRENT_TIMESTAMP"),
    ("updated_at", "timestamp with time zone", "timestamptz", None, "NO", "CURRENT_TIMESTAMP"),
)

REVIEW_DECISION_COLUMNS = (
    ("id", "uuid", "uuid", None, "NO", None),
    ("review_item_id", "uuid", "uuid", None, "NO", None),
    ("sequence", "integer", "int4", None, "NO", None),
    ("decision_type", "character varying", "varchar", 40, "NO", None),
    ("decision_lifecycle", "character varying", "varchar", 30, "NO", None),
    ("scope", "character varying", "varchar", 30, "NO", None),
    ("payload_schema", "character varying", "varchar", 80, "NO", None),
    ("payload_version", "smallint", "int2", None, "NO", "1"),
    ("payload", "jsonb", "jsonb", None, "NO", None),
    ("reason", "text", "text", None, "YES", None),
    ("actor_type", "character varying", "varchar", 30, "NO", None),
    ("actor_user_id", "integer", "int4", None, "YES", None),
    ("actor_identifier", "character varying", "varchar", 180, "NO", None),
    ("actor_capability", "character varying", "varchar", 40, "YES", None),
    ("decided_at", "timestamp with time zone", "timestamptz", None, "NO", "CURRENT_TIMESTAMP"),
    ("expected_case_version", "integer", "int4", None, "NO", None),
    ("previous_decision_id", "uuid", "uuid", None, "YES", None),
    ("corrects_decision_id", "uuid", "uuid", None, "YES", None),
    ("locks_projection", "boolean", "bool", None, "NO", "false"),
    ("created_at", "timestamp with time zone", "timestamptz", None, "NO", "CURRENT_TIMESTAMP"),
)

EXPECTED_CONSTRAINT_KEYS = {
    ("review_items", "pk_review_items", "p"),
    ("review_items", "ck_review_items_case_type", "c"),
    ("review_items", "ck_review_items_target_table", "c"),
    ("review_items", "ck_review_items_case_status", "c"),
    ("review_items", "ck_review_items_scientific_status", "c"),
    ("review_items", "ck_review_items_raw_hash", "c"),
    ("review_items", "fk_review_items_current_decision_id_review_decisions", "f"),
    ("review_decisions", "pk_review_decisions", "p"),
    ("review_decisions", "fk_review_decisions_review_item_id_review_items", "f"),
    ("review_decisions", "fk_review_decisions_actor_user_id_users", "f"),
    ("review_decisions", "fk_review_decisions_previous_decision_id", "f"),
    ("review_decisions", "fk_review_decisions_corrects_decision_id", "f"),
    ("review_decisions", "uq_review_decisions_item_sequence", "u"),
    ("review_decisions", "ck_review_decisions_decision_type", "c"),
    ("review_decisions", "ck_review_decisions_lifecycle", "c"),
    ("review_decisions", "ck_review_decisions_scope", "c"),
    ("review_decisions", "ck_review_decisions_payload_object", "c"),
    ("review_decisions", "ck_review_decisions_payload_version", "c"),
    ("review_decisions", "ck_review_decisions_positive_sequence", "c"),
    ("review_decisions", "ck_review_decisions_actor_shape", "c"),
    ("review_decisions", "ck_review_decisions_approved_locks_projection", "c"),
    ("review_decisions", "ck_review_decisions_previous_not_self", "c"),
    ("review_decisions", "ck_review_decisions_corrects_not_self", "c"),
}

EXPECTED_INDEX_KEYS = {
    ("review_items", "pk_review_items"),
    ("review_items", "uq_review_items_active_case_target"),
    ("review_items", "ix_review_items_queue"),
    ("review_items", "ix_review_items_target"),
    ("review_items", "ix_review_items_document"),
    ("review_items", "ix_review_items_period"),
    ("review_items", "ix_review_items_current_decision"),
    ("review_decisions", "pk_review_decisions"),
    ("review_decisions", "uq_review_decisions_item_sequence"),
    ("review_decisions", "ix_review_decisions_case"),
    ("review_decisions", "ix_review_decisions_lifecycle"),
    ("review_decisions", "ix_review_decisions_actor"),
    ("review_decisions", "ix_review_decisions_previous"),
    ("review_decisions", "ix_review_decisions_corrects"),
}

CASE_TYPES = (
    "person_identity", "author_identity", "product", "project_director_relation",
    "external_identity", "possible_duplicate", "invalid_text", "new_evidence_conflict",
)
CASE_STATUSES = (
    "pending", "in_review", "awaiting_gestor_approval", "resolved",
    "reopened", "conflicted", "superseded",
)
ACTIVE_CASE_STATUSES = (
    "pending", "in_review", "awaiting_gestor_approval", "reopened", "conflicted",
)
SCIENTIFIC_STATUSES = ("pending", "validated", "rejected", "discarded")
TARGET_TABLES = (
    "person_roles", "scientific_production_authors", "scientific_productions",
    "research_entities", "external_researchers",
)
DECISION_TYPES = (
    "validated", "corrected", "linked", "merged", "maintained_separate",
    "separated", "rejected", "discarded", "maintained", "reverted",
)
DECISION_LIFECYCLES = ("proposed", "approved", "declined", "superseded")
DECISION_SCOPES = ("global_identity", "record", "document", "relationship", "period")


class HumanReviewMigration0018PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_url = require_b2b1_test_database_url()

    def test_predecessor_setup_applies_real_0017_without_skip(self) -> None:
        with self._prepared_database("b2b1_0018_pre") as engine:
            with engine.connect() as connection:
                versions = tuple(connection.execute(text(
                    "SELECT version FROM schema_migrations ORDER BY applied_at, version"
                )).scalars())
                capability_table = connection.execute(text(
                    "SELECT to_regclass(current_schema() || '.user_b2b_capabilities')::text"
                )).scalar_one()
            self.assertEqual(versions, PREDECESSOR_VERSIONS)
            self.assertEqual(capability_table, "user_b2b_capabilities")

    def test_metadata_and_registry_order_are_exact(self) -> None:
        module = self._migration_module()
        self.assertEqual((module.VERSION, module.revision, module.down_revision),
                         (VERSION, VERSION, DOWN_REVISION))
        self.assertTrue(callable(module.upgrade))
        self.assertTrue(callable(module.downgrade))
        self.assertTrue(callable(module.assert_schema))
        versions = tuple(version for version, _upgrade in migration_registry.MIGRATIONS)
        expected_prefix = PREDECESSOR_VERSIONS + (VERSION,)
        self.assertEqual(versions[:len(expected_prefix)], expected_prefix)
        self.assertEqual(versions.count(VERSION), 1)
        self.assertEqual(len(versions), len(set(versions)))
        self.assertIs(migration_registry.MIGRATIONS[len(expected_prefix) - 1][1], module.upgrade)

    def test_upgrade_creates_exact_columns_constraints_indexes_function_and_trigger(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0018_catalog") as engine:
            module.upgrade(engine)
            with engine.connect() as connection:
                module.assert_schema(connection)
                columns = {
                    table_name: tuple(tuple(row) for row in connection.execute(text(
                        """
                        SELECT column_name, data_type, udt_name,
                               character_maximum_length, is_nullable, column_default
                        FROM information_schema.columns
                        WHERE table_schema = current_schema() AND table_name = :table_name
                        ORDER BY ordinal_position
                        """
                    ), {"table_name": table_name}))
                    for table_name in ("review_items", "review_decisions")
                }
                constraint_keys = {
                    (row.table_name, row.conname, row.contype)
                    for row in connection.execute(text(
                        """
                        SELECT table_row.relname AS table_name,
                               constraint_row.conname, constraint_row.contype
                        FROM pg_constraint AS constraint_row
                        JOIN pg_class AS table_row ON table_row.oid = constraint_row.conrelid
                        JOIN pg_namespace AS namespace_row ON namespace_row.oid = table_row.relnamespace
                        WHERE namespace_row.nspname = current_schema()
                          AND table_row.relname IN ('review_items', 'review_decisions')
                        """
                    ))
                }
                index_keys = {
                    (row.tablename, row.indexname)
                    for row in connection.execute(text(
                        """
                        SELECT tablename, indexname FROM pg_indexes
                        WHERE schemaname = current_schema()
                          AND tablename IN ('review_items', 'review_decisions')
                        """
                    ))
                }
                future_columns = connection.execute(text(
                    """
                    SELECT COUNT(*) FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name IN ('review_items', 'review_decisions')
                      AND column_name IN (
                          'delegated_to', 'reserved_by', 'reservation_expires_at',
                          'draft_payload', 'evidence_asset_id', 'conflict_id',
                          'export_id', 'materialized_kpi', 'batch_id'
                      )
                    """
                )).scalar_one()
                function_rows = tuple(tuple(row) for row in connection.execute(text(
                    """
                    SELECT procedure_row.proname,
                           pg_get_function_identity_arguments(procedure_row.oid),
                           language_row.lanname,
                           pg_get_function_result(procedure_row.oid),
                           procedure_row.pronargs, procedure_row.pronargdefaults,
                           procedure_row.provolatile, procedure_row.prosecdef,
                           procedure_row.proleakproof, procedure_row.proisstrict,
                           procedure_row.proparallel, procedure_row.prokind,
                           procedure_row.proconfig
                    FROM pg_proc AS procedure_row
                    JOIN pg_namespace AS namespace_row ON namespace_row.oid = procedure_row.pronamespace
                    JOIN pg_language AS language_row ON language_row.oid = procedure_row.prolang
                    WHERE namespace_row.nspname = current_schema()
                      AND procedure_row.proname = 'b2b_reject_append_only_mutation'
                    ORDER BY 2
                    """
                )))
                trigger_rows = tuple(tuple(row) for row in connection.execute(text(
                    """
                    SELECT trigger_row.tgname, table_row.relname, trigger_row.tgenabled,
                           trigger_row.tgtype, procedure_row.proname,
                           procedure_namespace.nspname = current_schema(),
                           trigger_row.tgnargs, octet_length(trigger_row.tgargs),
                           trigger_row.tgqual IS NULL, trigger_row.tgconstraint,
                           trigger_row.tgdeferrable, trigger_row.tginitdeferred,
                           cardinality(trigger_row.tgattr::smallint[])
                    FROM pg_trigger AS trigger_row
                    JOIN pg_class AS table_row ON table_row.oid = trigger_row.tgrelid
                    JOIN pg_namespace AS namespace_row ON namespace_row.oid = table_row.relnamespace
                    JOIN pg_proc AS procedure_row ON procedure_row.oid = trigger_row.tgfoid
                    JOIN pg_namespace AS procedure_namespace ON procedure_namespace.oid = procedure_row.pronamespace
                    WHERE namespace_row.nspname = current_schema()
                      AND NOT trigger_row.tgisinternal
                      AND trigger_row.tgname = 'trg_review_decisions_append_only'
                    """
                )))

            self.assertEqual(columns["review_items"], REVIEW_ITEM_COLUMNS)
            self.assertEqual(columns["review_decisions"], REVIEW_DECISION_COLUMNS)
            self.assertEqual(constraint_keys, EXPECTED_CONSTRAINT_KEYS)
            self.assertEqual(index_keys, EXPECTED_INDEX_KEYS)
            self.assertEqual(future_columns, 0)
            self.assertEqual(function_rows, ((
                "b2b_reject_append_only_mutation", "", "plpgsql", "trigger",
                0, 0, "v", False, False, False, "u", "f", None,
            ),))
            self.assertEqual(trigger_rows, ((
                "trg_review_decisions_append_only", "review_decisions", "O", 58,
                "b2b_reject_append_only_mutation", True, 0, 0, True, 0,
                False, False, 0,
            ),))

    def test_stable_target_width_is_128_and_full_untruncated_key_is_accepted(self) -> None:
        module = self._migration_module()
        full_key = "b2b:v1:project_director_relation:" + "a" * 64
        self.assertEqual(len(full_key), 97)
        with self._prepared_database("b2b1_0018_key") as engine:
            module.upgrade(engine)
            item_id = self._insert_item(engine, stable_target_key=full_key)
            second_id = self._insert_item(
                engine, stable_target_key="x" * 128, case_type="invalid_text"
            )
            with engine.connect() as connection:
                stored = tuple(connection.execute(text(
                    "SELECT stable_target_key FROM review_items ORDER BY stable_target_key"
                )).scalars())
            self.assertIn(full_key, stored)
            self.assertIn("x" * 128, stored)
            self.assertIsInstance(item_id, UUID)
            self.assertIsInstance(second_id, UUID)

    def test_row_or_block_id_text_has_no_legacy_limit_and_round_trips(self) -> None:
        module = self._migration_module()
        exact_fixture_prefix = "product:scientific-productions:id=400:"
        locator_248 = exact_fixture_prefix + "x" * (248 - len(exact_fixture_prefix))
        self.assertEqual(len(locator_248), 248)

        with self._prepared_database("b2b1_0018_locator") as engine:
            module.upgrade(engine)
            for index, length in enumerate((179, 180, 181, 248, 512), start=1):
                locator = locator_248 if length == 248 else f"L{index}:" + "x" * (length - len(f"L{index}:"))
                parameters = self._item_parameters(
                    stable_target_key=self._stable_key(500 + index)
                )
                parameters["row_or_block_id"] = locator
                with engine.begin() as connection:
                    connection.execute(self._item_insert_sql(), parameters)
                with engine.connect() as connection:
                    stored = connection.execute(
                        text("SELECT row_or_block_id FROM review_items WHERE id = :id"),
                        {"id": parameters["id"]},
                    ).scalar_one()
                self.assertEqual(len(stored), length)
                self.assertEqual(stored.encode("utf-8"), locator.encode("utf-8"))

    def test_row_or_block_id_248_survives_flush_and_injected_rollback_is_total(self) -> None:
        module = self._migration_module()
        prefix = "product:scientific-productions:id=400:"
        locator = prefix + "z" * (248 - len(prefix))
        stable_key = self._stable_key(600)
        parameters = self._item_parameters(stable_target_key=stable_key)
        parameters["row_or_block_id"] = locator

        with self._prepared_database("b2b1_0018_rollback") as engine:
            module.upgrade(engine)
            with self.assertRaisesRegex(RuntimeError, "injected after locator flush"):
                with engine.begin() as connection:
                    connection.execute(self._item_insert_sql(), parameters)
                    stored = connection.execute(
                        text("SELECT row_or_block_id FROM review_items WHERE id = :id"),
                        {"id": parameters["id"]},
                    ).scalar_one()
                    self.assertEqual(stored.encode("utf-8"), locator.encode("utf-8"))
                    raise RuntimeError("injected after locator flush")
            with engine.connect() as connection:
                self.assertEqual(
                    connection.execute(
                        text("SELECT COUNT(*) FROM review_items WHERE stable_target_key = :key"),
                        {"key": stable_key},
                    ).scalar_one(),
                    0,
                )

    def test_active_case_uniqueness_and_terminal_target_reuse_are_exact(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0018_active") as engine:
            module.upgrade(engine)
            for index, status in enumerate(ACTIVE_CASE_STATUSES):
                key = self._stable_key(index)
                self._insert_item(engine, stable_target_key=key, case_status=status)
                self._assert_rejected(
                    engine,
                    self._item_insert_sql(),
                    self._item_parameters(stable_target_key=key, case_status="pending"),
                )
            for index, status in enumerate(("resolved", "superseded"), start=20):
                key = self._stable_key(index)
                self._insert_item(engine, stable_target_key=key, case_status=status)
                self._insert_item(engine, stable_target_key=key, case_status="pending")

    def test_review_item_vocabularies_and_lowercase_hash_are_closed(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0018_itemck") as engine:
            module.upgrade(engine)
            counter = 0
            for field, valid_values in (
                ("case_type", CASE_TYPES),
                ("case_status", CASE_STATUSES),
                ("scientific_status", SCIENTIFIC_STATUSES),
                ("target_table", TARGET_TABLES),
            ):
                for value in valid_values:
                    counter += 1
                    parameters = self._item_parameters(stable_target_key=self._stable_key(counter))
                    parameters[field] = value
                    with engine.begin() as connection:
                        connection.execute(self._item_insert_sql(), parameters)
                parameters = self._item_parameters(stable_target_key=self._stable_key(counter + 100))
                parameters[field] = "UNKNOWN"
                self._assert_rejected(engine, self._item_insert_sql(), parameters)
            for invalid_hash in ("", "a" * 63, "a" * 65, "A" * 64, "g" * 64):
                parameters = self._item_parameters(stable_target_key=self._stable_key(counter + 200))
                parameters["raw_hash"] = invalid_hash
                self._assert_rejected(engine, self._item_insert_sql(), parameters)

    def test_decision_closed_vocabularies_payload_sequence_actor_lock_and_lineage(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0018_decck") as engine:
            module.upgrade(engine)
            item_id = self._insert_item(engine)
            sequence = 1
            for field, valid_values in (
                ("decision_type", DECISION_TYPES),
                ("decision_lifecycle", DECISION_LIFECYCLES),
                ("scope", DECISION_SCOPES),
            ):
                for value in valid_values:
                    parameters = self._decision_parameters(item_id, sequence=sequence)
                    parameters[field] = value
                    if field == "decision_lifecycle" and value == "approved":
                        parameters["locks_projection"] = True
                    with engine.begin() as connection:
                        connection.execute(self._decision_insert_sql(), parameters)
                    sequence += 1
                invalid = self._decision_parameters(item_id, sequence=sequence)
                invalid[field] = "UNKNOWN"
                self._assert_rejected(engine, self._decision_insert_sql(), invalid)

            invalid_cases = (
                {"sequence": 0},
                {"payload": json.dumps([])},
                {"payload_version": 2},
                {"actor_type": "human", "actor_user_id": None},
                {"actor_type": "human", "actor_capability": "CAREER_MANAGER"},
                {"actor_type": "legacy", "actor_user_id": 1, "actor_capability": None},
                {"actor_type": "legacy", "actor_user_id": None,
                 "actor_capability": None, "actor_identifier": "   "},
                {"decision_lifecycle": "approved", "locks_projection": False},
            )
            for offset, overrides in enumerate(invalid_cases, start=100):
                parameters = self._decision_parameters(item_id, sequence=offset)
                parameters.update(overrides)
                self._assert_rejected(engine, self._decision_insert_sql(), parameters)

            valid_legacy = self._decision_parameters(item_id, sequence=200)
            valid_legacy.update(actor_type="legacy", actor_user_id=None,
                                actor_capability=None, actor_identifier="legacy-import")
            with engine.begin() as connection:
                connection.execute(self._decision_insert_sql(), valid_legacy)

            self_id = uuid4()
            for lineage_field in ("previous_decision_id", "corrects_decision_id"):
                invalid = self._decision_parameters(item_id, sequence=201, decision_id=self_id)
                invalid[lineage_field] = self_id
                self._assert_rejected(engine, self._decision_insert_sql(), invalid)

            duplicate = self._decision_parameters(item_id, sequence=200)
            self._assert_rejected(engine, self._decision_insert_sql(), duplicate)

    def test_current_decision_foreign_key_is_restrictive_and_initially_deferred(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0018_deferred") as engine:
            module.upgrade(engine)
            item_id = uuid4()
            decision_id = uuid4()
            with engine.begin() as connection:
                item = self._item_parameters(item_id=item_id)
                item["current_decision_id"] = decision_id
                connection.execute(self._item_insert_sql(include_current=True), item)
                connection.execute(
                    self._decision_insert_sql(),
                    self._decision_parameters(item_id, sequence=1, decision_id=decision_id),
                )
            with engine.connect() as connection:
                pointer = connection.execute(text(
                    "SELECT current_decision_id FROM review_items WHERE id = :id"
                ), {"id": item_id}).scalar_one()
            self.assertEqual(pointer, decision_id)

            with self.assertRaises(DBAPIError):
                with engine.begin() as connection:
                    invalid = self._item_parameters(stable_target_key=self._stable_key(400))
                    invalid["current_decision_id"] = uuid4()
                    connection.execute(self._item_insert_sql(include_current=True), invalid)

            self._assert_rejected(
                engine, text("DELETE FROM review_items WHERE id = :id"), {"id": item_id}
            )

    def test_append_only_trigger_allows_insert_and_rejects_update_delete_truncate(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0018_append") as engine:
            module.upgrade(engine)
            item_id = self._insert_item(engine)
            decision_id = self._insert_decision(engine, item_id)
            for statement in (
                text("UPDATE review_decisions SET reason = 'changed' WHERE id = :id"),
                text("DELETE FROM review_decisions WHERE id = :id"),
                text("TRUNCATE TABLE review_decisions, review_items"),
            ):
                with self.assertRaisesRegex(DBAPIError, "review_decisions is append-only"):
                    with engine.begin() as connection:
                        connection.execute(statement, {"id": decision_id})
            with engine.connect() as connection:
                count = connection.execute(text("SELECT COUNT(*) FROM review_decisions")).scalar_one()
            self.assertEqual(count, 1)

    def test_assert_schema_rejects_all_required_catalog_mutations(self) -> None:
        module = self._migration_module()
        mutation_plans = (
            (
                "broadened_case_type",
                ("ALTER TABLE review_items DROP CONSTRAINT ck_review_items_case_type",
                 "ALTER TABLE review_items ADD CONSTRAINT ck_review_items_case_type "
                 "CHECK (case_type IN ('person_identity', 'author_identity', 'product', "
                 "'project_director_relation', 'external_identity', 'possible_duplicate', "
                 "'invalid_text', 'new_evidence_conflict', 'future_type'))"),
            ),
            (
                "uppercase_hash_literal",
                ("ALTER TABLE review_items DROP CONSTRAINT ck_review_items_raw_hash",
                 "ALTER TABLE review_items ADD CONSTRAINT ck_review_items_raw_hash "
                 "CHECK (raw_value_sha256 ~ '^[0-9A-F]{64}$')"),
            ),
            (
                "changed_active_predicate",
                ("DROP INDEX uq_review_items_active_case_target",
                 "CREATE UNIQUE INDEX uq_review_items_active_case_target "
                 "ON review_items (case_type, stable_target_key) "
                 "WHERE case_status IN ('pending', 'in_review')"),
            ),
            (
                "noop_function",
                ("CREATE OR REPLACE FUNCTION b2b_reject_append_only_mutation() RETURNS TRIGGER "
                 "LANGUAGE plpgsql AS $$ BEGIN RETURN NULL; END; $$",),
            ),
            (
                "row_trigger",
                ("DROP TRIGGER trg_review_decisions_append_only ON review_decisions",
                 "CREATE TRIGGER trg_review_decisions_append_only BEFORE UPDATE OR DELETE "
                 "ON review_decisions FOR EACH ROW "
                 "EXECUTE FUNCTION b2b_reject_append_only_mutation()"),
            ),
            (
                "function_overload",
                ("CREATE FUNCTION b2b_reject_append_only_mutation(integer) RETURNS integer "
                 "LANGUAGE sql IMMUTABLE AS $$ SELECT $1 $$",),
            ),
            (
                "same_name_cross_table_trigger",
                ("CREATE TRIGGER trg_review_decisions_append_only BEFORE UPDATE ON users "
                 "FOR EACH STATEMENT EXECUTE FUNCTION b2b_reject_append_only_mutation()",),
            ),
        )
        for index, (marker, statements) in enumerate(mutation_plans):
            with self.subTest(marker=marker):
                with self._prepared_database(f"b2b1_0018_mut{index}") as engine:
                    module.upgrade(engine)
                    with engine.begin() as connection:
                        for statement in statements:
                            connection.execute(text(statement))
                    with engine.connect() as connection:
                        with self.assertRaisesRegex(RuntimeError, "0018 schema assertion failed"):
                            module.assert_schema(connection)

    def test_assert_schema_rejects_differently_named_trigger_on_review_decisions(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0018_xtrg") as engine:
            module.upgrade(engine)
            with engine.begin() as connection:
                connection.execute(text(
                    """
                    CREATE TRIGGER hostile_review_decisions_before_insert
                    BEFORE INSERT ON review_decisions
                    FOR EACH STATEMENT
                    EXECUTE FUNCTION b2b_reject_append_only_mutation()
                    """
                ))
            with engine.connect() as connection:
                with self.assertRaisesRegex(RuntimeError, "0018 schema assertion failed"):
                    module.assert_schema(connection)

    def test_assert_schema_rejects_active_index_with_nulls_not_distinct(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0018_nulls") as engine:
            module.upgrade(engine)
            with engine.begin() as connection:
                connection.execute(text("DROP INDEX uq_review_items_active_case_target"))
                connection.execute(text(
                    """
                    CREATE UNIQUE INDEX uq_review_items_active_case_target
                    ON review_items (case_type, stable_target_key) NULLS NOT DISTINCT
                    WHERE case_status IN (
                        'pending', 'in_review', 'awaiting_gestor_approval',
                        'reopened', 'conflicted'
                    )
                    """
                ))
            with engine.connect() as connection:
                with self.assertRaisesRegex(RuntimeError, "0018 schema assertion failed"):
                    module.assert_schema(connection)

    def test_update_through_inheritance_parent_bypasses_child_statement_trigger(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0018_inhproof") as engine:
            module.upgrade(engine)
            item_id = self._insert_item(engine)
            decision_id = self._insert_decision(engine, item_id)
            with engine.begin() as connection:
                connection.execute(text(
                    "CREATE TABLE hostile_decision_parent (id UUID, reason TEXT)"
                ))
                connection.execute(text(
                    "ALTER TABLE review_decisions INHERIT hostile_decision_parent"
                ))
                changed = connection.execute(text(
                    """
                    UPDATE hostile_decision_parent
                    SET reason = 'changed through inheritance parent'
                    WHERE id = :decision_id
                    RETURNING id
                    """
                ), {"decision_id": decision_id}).scalar_one()
            with engine.connect() as connection:
                reason = connection.execute(text(
                    "SELECT reason FROM review_decisions WHERE id = :decision_id"
                ), {"decision_id": decision_id}).scalar_one()
            self.assertEqual(changed, decision_id)
            self.assertEqual(reason, "changed through inheritance parent")

    def test_assert_schema_rejects_review_decisions_inheritance(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0018_inherit") as engine:
            module.upgrade(engine)
            item_id = self._insert_item(engine)
            decision_id = self._insert_decision(engine, item_id)
            with engine.begin() as connection:
                connection.execute(text(
                    "CREATE TABLE hostile_decision_parent (id UUID, reason TEXT)"
                ))
                connection.execute(text(
                    "ALTER TABLE review_decisions INHERIT hostile_decision_parent"
                ))
            with engine.connect() as connection:
                with self.assertRaisesRegex(RuntimeError, "0018 schema assertion failed"):
                    module.assert_schema(connection)
                unchanged_reason = connection.execute(text(
                    "SELECT reason FROM review_decisions WHERE id = :decision_id"
                ), {"decision_id": decision_id}).scalar_one()
            self.assertEqual(unchanged_reason, "test decision")

    def test_assert_schema_rejects_inherited_constraint_state(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0018_inhck") as engine:
            module.upgrade(engine)
            with engine.begin() as connection:
                connection.execute(text(
                    """
                    CREATE TABLE hostile_item_parent (
                        case_type VARCHAR(60) NOT NULL,
                        CONSTRAINT ck_review_items_case_type CHECK (
                            case_type IN (
                                'person_identity', 'author_identity', 'product',
                                'project_director_relation', 'external_identity',
                                'possible_duplicate', 'invalid_text',
                                'new_evidence_conflict'
                            )
                        )
                    )
                    """
                ))
                connection.execute(text(
                    "ALTER TABLE review_items INHERIT hostile_item_parent"
                ))
                inheritance_count = connection.execute(text(
                    """
                    SELECT coninhcount
                    FROM pg_constraint AS constraint_row
                    JOIN pg_class AS table_row ON table_row.oid = constraint_row.conrelid
                    JOIN pg_namespace AS namespace_row ON namespace_row.oid = table_row.relnamespace
                    WHERE namespace_row.nspname = current_schema()
                      AND table_row.relname = 'review_items'
                      AND constraint_row.conname = 'ck_review_items_case_type'
                    """
                )).scalar_one()
            self.assertEqual(inheritance_count, 1)
            with engine.connect() as connection:
                with self.assertRaisesRegex(RuntimeError, "0018 schema assertion failed"):
                    module.assert_schema(connection)

    def test_assert_schema_rejects_clustered_index_state(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0018_cluster") as engine:
            module.upgrade(engine)
            with engine.begin() as connection:
                connection.execute(text(
                    "CLUSTER review_items USING ix_review_items_document"
                ))
            with engine.connect() as connection:
                with self.assertRaisesRegex(RuntimeError, "0018 schema assertion failed"):
                    module.assert_schema(connection)

    def test_assert_schema_rejects_replica_identity_index_state(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0018_replid") as engine:
            module.upgrade(engine)
            with engine.begin() as connection:
                connection.execute(text(
                    "ALTER TABLE review_items REPLICA IDENTITY USING INDEX pk_review_items"
                ))
            with engine.connect() as connection:
                with self.assertRaisesRegex(RuntimeError, "0018 schema assertion failed"):
                    module.assert_schema(connection)

    def test_assert_schema_rejects_index_fillfactor(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0018_ixfill") as engine:
            module.upgrade(engine)
            with engine.begin() as connection:
                connection.execute(text(
                    "ALTER INDEX ix_review_items_document SET (fillfactor=50)"
                ))
            with engine.connect() as connection:
                with self.assertRaisesRegex(RuntimeError, "0018 schema assertion failed"):
                    module.assert_schema(connection)

    def test_assert_schema_rejects_table_fillfactor(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0018_tblfill") as engine:
            module.upgrade(engine)
            with engine.begin() as connection:
                connection.execute(text(
                    "ALTER TABLE review_items SET (fillfactor=50)"
                ))
            with engine.connect() as connection:
                with self.assertRaisesRegex(RuntimeError, "0018 schema assertion failed"):
                    module.assert_schema(connection)

    def test_upgrade_assertion_failure_rolls_back_every_object_and_is_not_registered(self) -> None:
        module = self._migration_module()
        sabotage_statements = (
            "DROP INDEX ix_review_items_document",
            "ALTER TABLE review_decisions DROP CONSTRAINT ck_review_decisions_scope",
        )
        for index, sabotage_statement in enumerate(sabotage_statements):
            with self.subTest(sabotage=sabotage_statement):
                with self._prepared_database(f"b2b1_0018_rb{index}") as engine:
                    original_assert = module.assert_schema

                    def sabotage(connection, *, statement=sabotage_statement):
                        connection.execute(text(statement))
                        original_assert(connection)

                    with patch.object(module, "assert_schema", side_effect=sabotage):
                        with self.assertRaisesRegex(RuntimeError, "0018 schema assertion failed"):
                            run_migrations(engine)
                    self._assert_0018_absent(engine)
                    self.assertEqual(self._applied_versions(engine), PREDECESSOR_VERSIONS)

    def test_runner_records_0018_once_and_preserves_all_predecessors(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0018_runner") as engine:
            with patch.object(
                migration_registry, "MIGRATIONS", migration_registry.MIGRATIONS[:18]
            ):
                self.assertEqual(run_migrations(engine), [VERSION])
                self.assertEqual(run_migrations(engine), [])
            self.assertEqual(self._applied_versions(engine), PREDECESSOR_VERSIONS + (VERSION,))
            with engine.connect() as connection:
                module.assert_schema(connection)
                capability_count = connection.execute(text(
                    "SELECT COUNT(*) FROM user_b2b_capabilities"
                )).scalar_one()
            self.assertEqual(capability_count, 0)

    def test_upgrade_downgrade_reupgrade_is_exact_and_preserves_users_and_0017(self) -> None:
        module = self._migration_module()
        with self._prepared_database("b2b1_0018_cycle") as engine:
            before_users = self._users(engine)
            module.upgrade(engine)
            module.downgrade(engine)
            self._assert_0018_absent(engine)
            self.assertEqual(self._users(engine), before_users)
            self.assertEqual(self._applied_versions(engine), PREDECESSOR_VERSIONS)
            with engine.connect() as connection:
                self.assertEqual(connection.execute(text(
                    "SELECT to_regclass(current_schema() || '.user_b2b_capabilities')::text"
                )).scalar_one(), "user_b2b_capabilities")
            module.upgrade(engine)
            with engine.connect() as connection:
                module.assert_schema(connection)

    def test_downgrade_rejects_each_recorded_later_revision_without_changes(self) -> None:
        module = self._migration_module()
        for index, later_version in enumerate(LATER_VERSIONS):
            with self.subTest(later_version=later_version):
                with self._prepared_database(f"b2b1_0018_later{index}") as engine:
                    module.upgrade(engine)
                    with engine.begin() as connection:
                        connection.execute(text(
                            "INSERT INTO schema_migrations (version) VALUES (:version)"
                        ), {"version": later_version})
                    with self.assertRaisesRegex(RuntimeError, later_version):
                        module.downgrade(engine)
                    with engine.connect() as connection:
                        module.assert_schema(connection)

    def test_order_sensitive_append_only_mutation_rollback_and_cycle_probes_repeat(self) -> None:
        module = self._migration_module()
        for iteration in range(self._repetition_count()):
            with self.subTest(iteration=iteration):
                with self._prepared_database(f"b2b1_0018_rep{iteration}") as engine:
                    module.upgrade(engine)
                    item_id = self._insert_item(engine)
                    decision_id = self._insert_decision(engine, item_id)
                    for statement in (
                        text("UPDATE review_decisions SET reason = 'forbidden' WHERE id = :id"),
                        text("DELETE FROM review_decisions WHERE id = :id"),
                        text("TRUNCATE TABLE review_decisions, review_items"),
                    ):
                        with self.assertRaisesRegex(DBAPIError, "review_decisions is append-only"):
                            with engine.begin() as connection:
                                connection.execute(statement, {"id": decision_id})

                    with engine.begin() as connection:
                        connection.execute(text(
                            """
                            CREATE TRIGGER hostile_review_decisions_before_insert
                            BEFORE INSERT ON review_decisions
                            FOR EACH STATEMENT
                            EXECUTE FUNCTION b2b_reject_append_only_mutation()
                            """
                        ))
                    with engine.connect() as connection:
                        with self.assertRaisesRegex(RuntimeError, "0018 schema assertion failed"):
                            module.assert_schema(connection)
                    with engine.begin() as connection:
                        connection.execute(text(
                            "DROP TRIGGER hostile_review_decisions_before_insert "
                            "ON review_decisions"
                        ))
                    with engine.connect() as connection:
                        module.assert_schema(connection)

                    with engine.begin() as connection:
                        connection.execute(text("DROP INDEX uq_review_items_active_case_target"))
                        connection.execute(text(
                            """
                            CREATE UNIQUE INDEX uq_review_items_active_case_target
                            ON review_items (case_type, stable_target_key) NULLS NOT DISTINCT
                            WHERE case_status IN (
                                'pending', 'in_review', 'awaiting_gestor_approval',
                                'reopened', 'conflicted'
                            )
                            """
                        ))
                    with engine.connect() as connection:
                        with self.assertRaisesRegex(RuntimeError, "0018 schema assertion failed"):
                            module.assert_schema(connection)
                    with engine.begin() as connection:
                        connection.execute(text("DROP INDEX uq_review_items_active_case_target"))
                        connection.execute(text(
                            """
                            CREATE UNIQUE INDEX uq_review_items_active_case_target
                            ON review_items (case_type, stable_target_key)
                            WHERE case_status IN (
                                'pending', 'in_review', 'awaiting_gestor_approval',
                                'reopened', 'conflicted'
                            )
                            """
                        ))
                    with engine.connect() as connection:
                        module.assert_schema(connection)

                    hostile_catalog_steps = (
                        (
                            "review_decisions_inheritance",
                            (
                                "CREATE TABLE hostile_decision_parent (id UUID, reason TEXT)",
                                "ALTER TABLE review_decisions INHERIT hostile_decision_parent",
                            ),
                            (
                                "ALTER TABLE review_decisions NO INHERIT hostile_decision_parent",
                                "DROP TABLE hostile_decision_parent",
                            ),
                        ),
                        (
                            "inherited_constraint_state",
                            (
                                """
                                CREATE TABLE hostile_item_parent (
                                    case_type VARCHAR(60) NOT NULL,
                                    CONSTRAINT ck_review_items_case_type CHECK (
                                        case_type IN (
                                            'person_identity', 'author_identity', 'product',
                                            'project_director_relation', 'external_identity',
                                            'possible_duplicate', 'invalid_text',
                                            'new_evidence_conflict'
                                        )
                                    )
                                )
                                """,
                                "ALTER TABLE review_items INHERIT hostile_item_parent",
                            ),
                            (
                                "ALTER TABLE review_items NO INHERIT hostile_item_parent",
                                "DROP TABLE hostile_item_parent",
                            ),
                        ),
                        (
                            "clustered_index",
                            ("CLUSTER review_items USING ix_review_items_document",),
                            ("ALTER TABLE review_items SET WITHOUT CLUSTER",),
                        ),
                        (
                            "replica_identity_index",
                            (
                                "ALTER TABLE review_items REPLICA IDENTITY "
                                "USING INDEX pk_review_items",
                            ),
                            ("ALTER TABLE review_items REPLICA IDENTITY DEFAULT",),
                        ),
                        (
                            "index_fillfactor",
                            ("ALTER INDEX ix_review_items_document SET (fillfactor=50)",),
                            ("ALTER INDEX ix_review_items_document RESET (fillfactor)",),
                        ),
                        (
                            "table_fillfactor",
                            ("ALTER TABLE review_items SET (fillfactor=50)",),
                            ("ALTER TABLE review_items RESET (fillfactor)",),
                        ),
                    )
                    for marker, attack_statements, restore_statements in hostile_catalog_steps:
                        with self.subTest(iteration=iteration, hostile_catalog=marker):
                            with engine.begin() as connection:
                                for statement in attack_statements:
                                    connection.execute(text(statement))
                            with engine.connect() as connection:
                                with self.assertRaisesRegex(
                                    RuntimeError, "0018 schema assertion failed"
                                ):
                                    module.assert_schema(connection)
                            with engine.begin() as connection:
                                for statement in restore_statements:
                                    connection.execute(text(statement))
                            with engine.connect() as connection:
                                module.assert_schema(connection)

                    with engine.begin() as connection:
                        connection.execute(text("DROP INDEX ix_review_items_period"))
                    with engine.connect() as connection:
                        with self.assertRaises(RuntimeError):
                            module.assert_schema(connection)
                    module.downgrade(engine)
                    self._assert_0018_absent(engine)

                    original_assert = module.assert_schema

                    def sabotage(connection):
                        connection.execute(text("DROP INDEX ix_review_items_document"))
                        original_assert(connection)

                    with patch.object(module, "assert_schema", side_effect=sabotage):
                        with self.assertRaisesRegex(RuntimeError, "0018 schema assertion failed"):
                            module.upgrade(engine)
                    self._assert_0018_absent(engine)

                    module.upgrade(engine)
                    with engine.connect() as connection:
                        module.assert_schema(connection)

    @staticmethod
    def _migration_module():
        return importlib.import_module(MIGRATION_MODULE)

    @staticmethod
    def _repetition_count() -> int:
        value = int(os.environ.get("B2B1_0018_REPETITIONS", "1"))
        if not 1 <= value <= 10:
            raise AssertionError("B2B1_0018_REPETITIONS must be between 1 and 10")
        return value

    @contextmanager
    def _prepared_database(self, prefix: str):
        with isolated_postgres_schema(self.database_url, prefix) as engine:
            with engine.begin() as connection:
                connection.execute(text(
                    """
                    CREATE TABLE users (
                        id INTEGER PRIMARY KEY,
                        role VARCHAR(40) NOT NULL,
                        marker VARCHAR(80) NOT NULL
                    )
                    """
                ))
                connection.execute(text(
                    """
                    INSERT INTO users (id, role, marker) VALUES
                        (1, 'FACULTY_ADMIN', 'preserve-one'),
                        (2, 'CAREER_MANAGER', 'preserve-career')
                    """
                ))
                connection.execute(text(
                    """
                    CREATE TABLE schema_migrations (
                        version VARCHAR(120) PRIMARY KEY,
                        applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                ))
                for version in PREDECESSOR_VERSIONS[:-1]:
                    connection.execute(text(
                        "INSERT INTO schema_migrations (version) VALUES (:version)"
                    ), {"version": version})
            capability_module = importlib.import_module(
                "app.migrations.versions.20260713_0017_b2b_capabilities"
            )
            capability_module.upgrade(engine)
            with engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO schema_migrations (version) VALUES (:version)"
                ), {"version": DOWN_REVISION})
            yield engine

    @staticmethod
    def _stable_key(index: int) -> str:
        return f"b2b:v1:person_identity:{index:064x}"

    @classmethod
    def _item_parameters(
        cls,
        *,
        item_id: UUID | None = None,
        stable_target_key: str | None = None,
        case_type: str = "person_identity",
        case_status: str = "pending",
    ) -> dict[str, object]:
        return {
            "id": item_id or uuid4(),
            "case_type": case_type,
            "stable_target_key": stable_target_key or cls._stable_key(0),
            "target_table": "person_roles",
            "target_pk": 10,
            "document_key": "document:test.pdf",
            "source_revision": "rev-1",
            "source_page": 1,
            "source_section": "faculty",
            "row_or_block_id": "row-1",
            "field_path": "canonical_name",
            "raw_hash": "a" * 64,
            "period_id": 2026,
            "relationship_key": "relationship:1",
            "case_status": case_status,
            "scientific_status": "pending",
            "current_decision_id": None,
        }

    @staticmethod
    def _item_insert_sql(*, include_current: bool = False):
        current_column = ", current_decision_id" if include_current else ""
        current_value = ", :current_decision_id" if include_current else ""
        return text(f"""
            INSERT INTO review_items (
                id, case_type, stable_target_key, target_table, target_pk,
                document_key, source_revision, source_page, source_section,
                row_or_block_id, field_path, raw_value_sha256, period_id,
                relationship_key, case_status, scientific_status{current_column}
            ) VALUES (
                :id, :case_type, :stable_target_key, :target_table, :target_pk,
                :document_key, :source_revision, :source_page, :source_section,
                :row_or_block_id, :field_path, :raw_hash, :period_id,
                :relationship_key, :case_status, :scientific_status{current_value}
            )
        """)

    @classmethod
    def _insert_item(cls, engine, **overrides) -> UUID:
        parameters = cls._item_parameters(**overrides)
        with engine.begin() as connection:
            connection.execute(cls._item_insert_sql(), parameters)
        return parameters["id"]

    @staticmethod
    def _decision_parameters(
        item_id: UUID,
        *,
        sequence: int = 1,
        decision_id: UUID | None = None,
    ) -> dict[str, object]:
        return {
            "id": decision_id or uuid4(),
            "review_item_id": item_id,
            "sequence": sequence,
            "decision_type": "validated",
            "decision_lifecycle": "proposed",
            "scope": "record",
            "payload_schema": "review.decision.v1",
            "payload_version": 1,
            "payload": json.dumps({"value": "accepted"}),
            "reason": "test decision",
            "actor_type": "human",
            "actor_user_id": 1,
            "actor_identifier": "user:1",
            "actor_capability": "RESEARCH_MANAGER",
            "expected_case_version": 1,
            "previous_decision_id": None,
            "corrects_decision_id": None,
            "locks_projection": False,
        }

    @staticmethod
    def _decision_insert_sql():
        return text("""
            INSERT INTO review_decisions (
                id, review_item_id, sequence, decision_type, decision_lifecycle,
                scope, payload_schema, payload_version, payload, reason,
                actor_type, actor_user_id, actor_identifier, actor_capability,
                expected_case_version, previous_decision_id,
                corrects_decision_id, locks_projection
            ) VALUES (
                :id, :review_item_id, :sequence, :decision_type,
                :decision_lifecycle, :scope, :payload_schema, :payload_version,
                CAST(:payload AS JSONB), :reason, :actor_type, :actor_user_id,
                :actor_identifier, :actor_capability, :expected_case_version,
                :previous_decision_id, :corrects_decision_id, :locks_projection
            )
        """)

    @classmethod
    def _insert_decision(cls, engine, item_id: UUID, **overrides) -> UUID:
        parameters = cls._decision_parameters(item_id, **overrides)
        with engine.begin() as connection:
            connection.execute(cls._decision_insert_sql(), parameters)
        return parameters["id"]

    def _assert_rejected(self, engine, statement, parameters=None) -> None:
        with self.assertRaises(DBAPIError):
            with engine.begin() as connection:
                connection.execute(statement, parameters or {})

    @staticmethod
    def _applied_versions(engine) -> tuple[str, ...]:
        with engine.connect() as connection:
            return tuple(connection.execute(text(
                "SELECT version FROM schema_migrations ORDER BY applied_at, version"
            )).scalars())

    @staticmethod
    def _users(engine) -> tuple[tuple[int, str, str], ...]:
        with engine.connect() as connection:
            return tuple(tuple(row) for row in connection.execute(text(
                "SELECT id, role, marker FROM users ORDER BY id"
            )))

    def _assert_0018_absent(self, engine) -> None:
        with engine.connect() as connection:
            tables = tuple(connection.execute(text(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = current_schema()
                  AND table_name IN ('review_items', 'review_decisions')
                ORDER BY table_name
                """
            )).scalars())
            functions = tuple(connection.execute(text(
                """
                SELECT pg_get_function_identity_arguments(procedure_row.oid)
                FROM pg_proc AS procedure_row
                JOIN pg_namespace AS namespace_row ON namespace_row.oid = procedure_row.pronamespace
                WHERE namespace_row.nspname = current_schema()
                  AND procedure_row.proname = 'b2b_reject_append_only_mutation'
                """
            )).scalars())
        self.assertEqual(tables, ())
        self.assertEqual(functions, ())


if __name__ == "__main__":
    unittest.main()
