import importlib
import importlib.util
import os
import unittest
import uuid

from sqlalchemy import Boolean, DateTime, Float, String, Text, create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import class_mapper

from app.core import migrations as migration_registry
from app.core.migrations import run_migrations
from app.models.entities import PersonRole, ScientificProductionAuthor


MIGRATION_MODULE = "app.migrations.versions.20260712_0016_canonical_identity_fields"
VERSION = "20260712_0016_canonical_identity_fields"
PREVIOUS_VERSION = "20260711_0015_dropbox_revision_uniqueness"
CANONICAL_FIELDS = {
    "canonical_identity_key": {"type": String, "length": 320, "nullable": True},
    "canonical_name": {"type": String, "length": 220, "nullable": True},
    "identity_source": {"type": String, "length": 40, "nullable": True},
    "identity_confidence": {"type": Float, "nullable": True},
    "identity_reason": {"type": Text, "nullable": True},
    "identity_locked": {"type": Boolean, "nullable": False, "default": False},
    "identity_decided_by": {"type": String, "length": 180, "nullable": True},
    "identity_decided_at": {"type": DateTime, "nullable": True, "timezone": True},
}
TARGET_TABLES = {
    "person_roles": PersonRole,
    "scientific_production_authors": ScientificProductionAuthor,
}


class CanonicalIdentityMigrationContractTests(unittest.TestCase):
    def require_migration_module(self):
        if importlib.util.find_spec(MIGRATION_MODULE) is None:
            self.fail(f"Missing migration module {MIGRATION_MODULE}")
        return importlib.import_module(MIGRATION_MODULE)

    def test_revision_metadata_and_registry_order_are_defined(self):
        module = self.require_migration_module()

        self.assertEqual(module.VERSION, VERSION)
        self.assertEqual(module.revision, VERSION)
        self.assertEqual(module.down_revision, PREVIOUS_VERSION)

        versions = [version for version, _ in migration_registry.MIGRATIONS]
        self.assertIn(VERSION, versions)
        self.assertEqual(versions[versions.index(PREVIOUS_VERSION) + 1], VERSION)

    def test_orm_models_expose_canonical_identity_fields_with_expected_types(self):
        for table_name, model in TARGET_TABLES.items():
            with self.subTest(table=table_name):
                columns = class_mapper(model).columns
                for field_name, expected in CANONICAL_FIELDS.items():
                    self.assertIn(field_name, columns)
                    column = columns[field_name]
                    self.assertIsInstance(column.type, expected["type"])
                    self.assertEqual(column.nullable, expected["nullable"])

                    if "length" in expected:
                        self.assertEqual(column.type.length, expected["length"])
                    if "timezone" in expected:
                        self.assertEqual(column.type.timezone, expected["timezone"])
                    if "default" in expected:
                        self.assertIsNotNone(column.default)
                        self.assertEqual(column.default.arg, expected["default"])
                        self.assertIsNotNone(column.server_default)
                        self.assertEqual(str(column.server_default.arg).strip().lower(), "false")

    def test_upgrade_requires_both_target_tables_before_any_change(self):
        module = self.require_migration_module()
        engine = create_engine("sqlite+pysqlite:///:memory:")
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        CREATE TABLE person_roles (
                            id INTEGER PRIMARY KEY,
                            normalized_name VARCHAR(220) NULL,
                            validation_status VARCHAR(40) NOT NULL DEFAULT 'pending_review'
                        )
                        """
                    )
                )

            baseline = self._schema_snapshot(engine, tables=("person_roles",))

            with self.assertRaisesRegex(RuntimeError, "scientific_production_authors"):
                module.upgrade(engine)

            self.assertEqual(self._schema_snapshot(engine, tables=("person_roles",)), baseline)
        finally:
            engine.dispose()

    def test_downgrade_requires_both_target_tables(self):
        module = self.require_migration_module()
        engine = create_engine("sqlite+pysqlite:///:memory:")
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        CREATE TABLE person_roles (
                            id INTEGER PRIMARY KEY,
                            canonical_identity_key VARCHAR(320) NULL
                        )
                        """
                    )
                )

            with self.assertRaisesRegex(RuntimeError, "scientific_production_authors"):
                module.downgrade(engine)
        finally:
            engine.dispose()

    def test_upgrade_adds_expected_columns_and_indexes_idempotently(self):
        module = self.require_migration_module()
        engine = self._pre_migration_engine()
        try:
            module.upgrade(engine)
            module.upgrade(engine)

            inspector = inspect(engine)
            for table_name in TARGET_TABLES:
                with self.subTest(table=table_name):
                    column_names = {column["name"] for column in inspector.get_columns(table_name)}
                    self.assertTrue(set(CANONICAL_FIELDS).issubset(column_names))
                    index_names = {index["name"] for index in inspector.get_indexes(table_name)}
                    self.assertIn(f"ix_{table_name}_canonical_identity_key", index_names)
        finally:
            engine.dispose()

    def test_downgrade_removes_only_canonical_identity_columns_and_indexes(self):
        module = self.require_migration_module()
        engine = self._pre_migration_engine()
        try:
            baseline = self._schema_snapshot(engine)

            module.upgrade(engine)
            module.downgrade(engine)
            module.downgrade(engine)

            self.assertEqual(self._schema_snapshot(engine), baseline)
        finally:
            engine.dispose()

    def test_database_default_probe_supports_repeated_calls_without_reusing_primary_keys(self):
        probe = CanonicalIdentityMigrationPostgresIntegrationTests(
            methodName="test_runner_records_schema_migrations_and_reupgrade_restores_fields"
        )
        probe.engine = create_engine("sqlite+pysqlite:///:memory:")
        try:
            with probe.engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        CREATE TABLE person_roles (
                            id INTEGER PRIMARY KEY,
                            identity_locked BOOLEAN NOT NULL DEFAULT FALSE
                        )
                        """
                    )
                )
                connection.execute(
                    text(
                        """
                        CREATE TABLE scientific_production_authors (
                            id INTEGER PRIMARY KEY,
                            identity_locked BOOLEAN NOT NULL DEFAULT FALSE
                        )
                        """
                    )
                )

            probe._assert_identity_locked_defaults_on_database()
            probe._assert_identity_locked_defaults_on_database()
        finally:
            probe.engine.dispose()

    def _pre_migration_engine(self) -> Engine:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE person_roles (
                        id INTEGER PRIMARY KEY,
                        normalized_name VARCHAR(220) NULL,
                        validation_status VARCHAR(40) NOT NULL DEFAULT 'pending_review'
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE TABLE scientific_production_authors (
                        id INTEGER PRIMARY KEY,
                        normalized_author_name VARCHAR(220) NULL,
                        validation_status VARCHAR(40) NOT NULL DEFAULT 'pending_author_resolution'
                    )
                    """
                )
            )
        return engine

    def _schema_snapshot(
        self,
        engine: Engine,
        *,
        tables: tuple[str, ...] | None = None,
    ) -> dict[str, dict[str, set[str]]]:
        inspector = inspect(engine)
        target_tables = tables or tuple(TARGET_TABLES)
        return {
            table_name: {
                "columns": {column["name"] for column in inspector.get_columns(table_name)},
                "indexes": {index["name"] for index in inspector.get_indexes(table_name)},
            }
            for table_name in target_tables
        }


class CanonicalIdentityMigrationPostgresIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_url = os.environ.get("CANONICAL_IDENTITY_TEST_DATABASE_URL")
        if not cls.database_url:
            raise unittest.SkipTest("CANONICAL_IDENTITY_TEST_DATABASE_URL is not set")
        cls.schema = f"canonical_identity_{uuid.uuid4().hex}"
        cls.admin_engine = create_engine(cls.database_url)
        with cls.admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        cls.engine = create_engine(
            cls.database_url,
            connect_args={"options": f"-csearch_path={cls.schema}"},
        )
        cls.module = importlib.import_module(MIGRATION_MODULE)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "engine"):
            cls.engine.dispose()
        if hasattr(cls, "admin_engine"):
            with cls.admin_engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{cls.schema}" CASCADE'))
            cls.admin_engine.dispose()

    def setUp(self):
        self._reset_schema()

    def test_runner_records_schema_migrations_and_reupgrade_restores_fields(self):
        self._create_target_tables()
        self._seed_predecessor_versions()

        executed = run_migrations(self.engine)

        self.assertEqual(executed, [VERSION])
        self.assertEqual(self._applied_versions()[-1], VERSION)
        self._assert_target_tables_have_canonical_fields()
        self._assert_identity_locked_defaults_on_database()

        self.module.downgrade(self.engine)
        self._delete_applied_version(VERSION)
        self._assert_target_tables_do_not_have_canonical_fields()

        executed_again = run_migrations(self.engine)

        self.assertEqual(executed_again, [VERSION])
        self._assert_target_tables_have_canonical_fields()
        self._assert_identity_locked_defaults_on_database()

    def test_runner_does_not_record_version_when_target_table_is_missing(self):
        self._create_target_tables(include_authors=False)
        self._seed_predecessor_versions()

        with self.assertRaisesRegex(RuntimeError, "scientific_production_authors"):
            run_migrations(self.engine)

        self.assertNotIn(VERSION, self._applied_versions())
        inspector = inspect(self.engine)
        self.assertNotIn("canonical_identity_key", {column["name"] for column in inspector.get_columns("person_roles")})

    def _reset_schema(self) -> None:
        with self.engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS schema_migrations CASCADE"))
            connection.execute(text("DROP TABLE IF EXISTS scientific_production_authors CASCADE"))
            connection.execute(text("DROP TABLE IF EXISTS person_roles CASCADE"))

    def _create_target_tables(self, *, include_authors: bool = True) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE person_roles (
                        id INTEGER PRIMARY KEY,
                        normalized_name VARCHAR(220) NULL,
                        validation_status VARCHAR(40) NOT NULL DEFAULT 'pending_review'
                    )
                    """
                )
            )
            if include_authors:
                connection.execute(
                    text(
                        """
                        CREATE TABLE scientific_production_authors (
                            id INTEGER PRIMARY KEY,
                            normalized_author_name VARCHAR(220) NULL,
                            validation_status VARCHAR(40) NOT NULL DEFAULT 'pending_author_resolution'
                        )
                        """
                    )
                )

    def _seed_predecessor_versions(self) -> None:
        previous_versions = [
            version
            for version, _migration in migration_registry.MIGRATIONS
            if version != VERSION
        ]
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version VARCHAR(120) PRIMARY KEY,
                        applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )
            for version in previous_versions:
                connection.execute(
                    text(
                        "INSERT INTO schema_migrations (version) VALUES (:version) "
                        "ON CONFLICT (version) DO NOTHING"
                    ),
                    {"version": version},
                )

    def _applied_versions(self) -> list[str]:
        with self.engine.begin() as connection:
            return [
                row[0]
                for row in connection.execute(
                    text("SELECT version FROM schema_migrations ORDER BY applied_at, version")
                ).fetchall()
            ]

    def _delete_applied_version(self, version: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text("DELETE FROM schema_migrations WHERE version = :version"),
                {"version": version},
            )

    def _assert_target_tables_have_canonical_fields(self) -> None:
        inspector = inspect(self.engine)
        for table_name in TARGET_TABLES:
            with self.subTest(table=table_name):
                column_names = {column["name"] for column in inspector.get_columns(table_name)}
                self.assertTrue(set(CANONICAL_FIELDS).issubset(column_names))
                index_names = {index["name"] for index in inspector.get_indexes(table_name)}
                self.assertIn(f"ix_{table_name}_canonical_identity_key", index_names)

    def _assert_target_tables_do_not_have_canonical_fields(self) -> None:
        inspector = inspect(self.engine)
        for table_name in TARGET_TABLES:
            with self.subTest(table=table_name):
                column_names = {column["name"] for column in inspector.get_columns(table_name)}
                self.assertTrue(set(CANONICAL_FIELDS).isdisjoint(column_names))

    def _assert_identity_locked_defaults_on_database(self) -> None:
        with self.engine.begin() as connection:
            role_id = connection.execute(
                text("SELECT COALESCE(MAX(id), 0) + 1 FROM person_roles")
            ).scalar_one()
            author_id = connection.execute(
                text("SELECT COALESCE(MAX(id), 0) + 1 FROM scientific_production_authors")
            ).scalar_one()
            role_value = connection.execute(
                text("INSERT INTO person_roles (id) VALUES (:id) RETURNING identity_locked"),
                {"id": role_id},
            ).scalar_one()
            author_value = connection.execute(
                text(
                    "INSERT INTO scientific_production_authors (id) VALUES (:id) "
                    "RETURNING identity_locked"
                ),
                {"id": author_id},
            ).scalar_one()
        self.assertFalse(role_value)
        self.assertFalse(author_value)


if __name__ == "__main__":
    unittest.main()
