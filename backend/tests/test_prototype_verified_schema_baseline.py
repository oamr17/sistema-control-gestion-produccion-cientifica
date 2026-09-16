from __future__ import annotations

import ast
import hashlib
import importlib.util
import os
import shutil
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from sqlalchemy import create_engine, text

from app.core import prototype_baseline
from app.core import migrations as migration_registry
from app.core.migrations import MIGRATIONS


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _fresh_state(**overrides: object) -> dict[str, object]:
    state: dict[str, object] = {
        "current_user": "prototype_owner",
        "application_role_exists": True,
        "application_role_is_superuser": False,
        "application_role_can_create_db": False,
        "application_role_can_create_role": False,
        "application_role_can_replicate": False,
        "application_role_bypasses_rls": False,
        "schema_name": "public",
        "schema_owner": "pg_database_owner",
        "owner_controls_schema": True,
        "schemas": ("information_schema", "pg_catalog", "pg_toast", "public"),
        "extensions": ("plpgsql",),
        "relations": (),
        "functions": (),
        "triggers": (),
        "scientific_or_demo_rows": 0,
    }
    state.update(overrides)
    return state


class _RecordingConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: object, parameters: object = None) -> SimpleNamespace:
        self.statements.append(str(statement))
        return SimpleNamespace()


class _RecordingEngine:
    def __init__(self) -> None:
        self.connection = _RecordingConnection()

    def begin(self) -> nullcontext[_RecordingConnection]:
        return nullcontext(self.connection)


class PrototypeVerifiedSchemaBaselineModuleTests(unittest.TestCase):
    def test_verified_baseline_module_exists(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec("app.core.prototype_baseline"))

    def test_verified_baseline_exposes_only_the_approved_boundary(self) -> None:
        expected = {
            "MigrationHash",
            "BaselineReport",
            "BaselineRefused",
            "prove_fresh_prototype_database",
            "install_runtime_object_manifest",
            "validate_runtime_fingerprint",
            "verify_migration_hash_manifest",
            "register_verified_baseline",
            "establish_prototype_verified_schema_baseline",
        }

        self.assertEqual(
            {name for name in expected if hasattr(prototype_baseline, name)},
            expected,
        )


class FreshDatabaseProofTests(unittest.TestCase):
    def test_fresh_database_is_accepted(self) -> None:
        with patch.object(
            prototype_baseline,
            "_read_fresh_database_state",
            return_value=_fresh_state(),
            create=True,
        ):
            prototype_baseline.prove_fresh_prototype_database(
                Mock(), "prototype_app"
            )

    def test_each_unapproved_precondition_is_refused_with_its_reason(self) -> None:
        cases = (
            ("application table", {"relations": (("public", "users", "table"),)}),
            ("application sequence", {"relations": (("public", "users_id_seq", "sequence"),)}),
            ("user view", {"relations": (("public", "report", "view"),)}),
            ("application function", {"functions": (("public", "unsafe_fn"),)}),
            ("application trigger", {"triggers": (("public", "unsafe_trigger"),)}),
            ("schema_migrations", {"relations": (("public", "schema_migrations", "table"),)}),
            ("scientific/demo rows", {"scientific_or_demo_rows": 1}),
            ("unexpected schema", {"schemas": ("information_schema", "pg_catalog", "pg_toast", "public", "shadow")}),
            ("unexpected extension", {"extensions": ("plpgsql", "pgcrypto")}),
            ("distinct runtime role", {"current_user": "prototype_app"}),
            ("runtime role ownership", {"schema_owner": "prototype_app"}),
            ("runtime role escalation", {"application_role_is_superuser": True}),
        )
        for reason, overrides in cases:
            with self.subTest(reason=reason), patch.object(
                prototype_baseline,
                "_read_fresh_database_state",
                return_value=_fresh_state(**overrides),
                create=True,
            ):
                with self.assertRaisesRegex(prototype_baseline.BaselineRefused, reason):
                    prototype_baseline.prove_fresh_prototype_database(
                        Mock(), "prototype_app"
                    )


class MigrationManifestTests(unittest.TestCase):
    def test_control_table_helper_preserves_the_existing_two_column_contract(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        try:
            with engine.begin() as connection:
                migration_registry.ensure_schema_migrations_table(connection)
                columns = tuple(
                    row[1]
                    for row in connection.execute(
                        text("PRAGMA table_info(schema_migrations)")
                    ).fetchall()
                )
            self.assertEqual(columns, ("version", "applied_at"))
        finally:
            engine.dispose()

    def test_repository_migration_manifest_matches_all_22_files(self) -> None:
        versions = prototype_baseline.verify_migration_hash_manifest(PROJECT_ROOT)

        self.assertEqual(tuple(versions), tuple(version for version, _ in MIGRATIONS))
        self.assertEqual(len(versions), 22)
        self.assertEqual(
            versions[-1],
            "20260827_0022_scoped_human_review_authorization",
        )

    def test_changed_migration_hash_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(
                PROJECT_ROOT / "backend/app/migrations/versions",
                root / "backend/app/migrations/versions",
            )
            target = root / prototype_baseline.MIGRATION_SHA256_MANIFEST[0].relative_path
            target.write_bytes(target.read_bytes() + b"\n# changed\n")

            with self.assertRaisesRegex(
                prototype_baseline.BaselineRefused, "SHA-256 mismatch"
            ):
                prototype_baseline.verify_migration_hash_manifest(root)

    def test_0022_presence_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(
                PROJECT_ROOT / "backend/app/migrations/versions",
                root / "backend/app/migrations/versions",
            )
            (root / "backend/app/migrations/versions/20260825_0022_forbidden.py").write_text(
                "VERSION = '20260825_0022_forbidden'\n", encoding="utf-8"
            )

            with self.assertRaisesRegex(prototype_baseline.BaselineRefused, "0022"):
                prototype_baseline.verify_migration_hash_manifest(root)

    def test_orphan_is_explicitly_excluded(self) -> None:
        versions = tuple(item.version for item in prototype_baseline.MIGRATION_SHA256_MANIFEST)
        self.assertNotIn(
            "20260629_0002_add_scientific_production_authors",
            versions,
        )
        self.assertEqual(
            prototype_baseline.EXCLUDED_ORPHAN_MIGRATION,
            "backend/app/migrations/versions/20260629_0002_add_scientific_production_authors.py",
        )


class RuntimeObjectManifestTests(unittest.TestCase):
    def test_manifest_contains_only_the_eight_approved_objects(self) -> None:
        expected = {
            "uq_import_jobs_current_document",
            "b2b_reject_career_capability",
            "b2b_reject_career_role_with_capability",
            "b2b_reject_append_only_mutation",
            "trg_user_b2b_capabilities_reject_career",
            "trg_users_reject_career_with_b2b_capability",
            "trg_review_decisions_append_only",
            "trg_audit_events_append_only",
        }
        manifest = prototype_baseline.RUNTIME_OBJECT_MANIFEST

        self.assertEqual({item.name for item in manifest}, expected)
        self.assertEqual(len(manifest), 8)
        self.assertTrue(all("IF NOT EXISTS" not in item.ddl.upper() for item in manifest))

    def test_manifest_definitions_are_derived_from_the_audited_sources(self) -> None:
        for item in prototype_baseline.RUNTIME_OBJECT_MANIFEST:
            with self.subTest(name=item.name):
                source = (PROJECT_ROOT / item.source_path).read_text(encoding="utf-8")
                source_literals = {
                    " ".join(node.value.replace("IF NOT EXISTS", "").split()).lower()
                    for node in ast.walk(ast.parse(source))
                    if isinstance(node, ast.Constant) and isinstance(node.value, str)
                }
                canonical_ddl = " ".join(item.ddl.split()).lower()
                self.assertIn(canonical_ddl, source_literals)

    def test_installer_executes_each_manifest_definition_once_in_order(self) -> None:
        engine = _RecordingEngine()

        names = prototype_baseline.install_runtime_object_manifest(engine)

        self.assertEqual(
            tuple(names), tuple(item.name for item in prototype_baseline.RUNTIME_OBJECT_MANIFEST)
        )
        self.assertEqual(
            engine.connection.statements,
            [item.ddl for item in prototype_baseline.RUNTIME_OBJECT_MANIFEST],
        )


class RuntimeFingerprintTests(unittest.TestCase):
    def test_postgresql_check_rewrite_normalizes_to_metadata_semantics(self) -> None:
        metadata_form = (
            "actor_capability IS NULL OR actor_capability "
            "IN ('RESEARCH_MANAGER', 'SYSTEM_ADMIN')"
        )
        catalog_form = (
            "actor_capability IS NULL OR "
            "(actor_capability = ANY (ARRAY['RESEARCH_MANAGER'::text, "
            "'SYSTEM_ADMIN'::text]))"
        )
        self.assertEqual(
            prototype_baseline._canonical_check_sql(metadata_form),
            prototype_baseline._canonical_check_sql(catalog_form),
        )

    def test_fingerprint_normalization_preserves_semantic_tuple_field_order(self) -> None:
        left = (("table", "constraint", "definition"),)
        reordered_fields = (("definition", "constraint", "table"),)

        self.assertNotEqual(
            prototype_baseline._normalize_fingerprint(left),
            prototype_baseline._normalize_fingerprint(reordered_fields),
        )

    def test_postgresql_float_metadata_matches_reflected_double_precision(self) -> None:
        from sqlalchemy import Float
        from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION

        self.assertEqual(
            prototype_baseline._canonical_type(Float()),
            prototype_baseline._canonical_type(DOUBLE_PRECISION()),
        )

    def test_postgresql_varchar_spelling_matches_character_varying(self) -> None:
        from sqlalchemy import String
        from sqlalchemy.dialects.postgresql import VARCHAR

        self.assertEqual(
            prototype_baseline._canonical_type(String(120)),
            prototype_baseline._canonical_type(VARCHAR(120)),
        )
        self.assertEqual(
            prototype_baseline._canonical_type(String(120)),
            "character varying(120)",
        )

    def test_default_btree_index_spelling_is_semantically_ignored(self) -> None:
        metadata_form = "CREATE INDEX ix_users_email ON users (email)"
        catalog_form = "CREATE INDEX ix_users_email ON public.users USING btree (email)"

        self.assertEqual(
            prototype_baseline._canonical_index_sql(metadata_form),
            prototype_baseline._canonical_index_sql(catalog_form),
        )

    def test_postgresql_partial_index_predicate_rewrite_is_normalized(self) -> None:
        metadata_form = (
            "CREATE UNIQUE INDEX uq_review ON review_items (case_status) "
            "WHERE case_status IN ('pending', 'reopened')"
        )
        catalog_form = (
            "CREATE UNIQUE INDEX uq_review ON public.review_items USING btree "
            "(case_status) WHERE ((case_status) = ANY "
            "((ARRAY['pending'::text, 'reopened'::text])))"
        )

        self.assertEqual(
            prototype_baseline._canonical_index_sql(metadata_form),
            prototype_baseline._canonical_index_sql(catalog_form),
        )

    def test_trigger_event_order_is_compared_as_a_semantic_set(self) -> None:
        source_form = (
            "CREATE TRIGGER guard BEFORE UPDATE OR DELETE OR TRUNCATE ON audit_events "
            "FOR EACH STATEMENT EXECUTE FUNCTION reject_mutation()"
        )
        catalog_form = (
            "CREATE TRIGGER guard BEFORE DELETE OR UPDATE OR TRUNCATE ON public.audit_events "
            "FOR EACH STATEMENT EXECUTE FUNCTION reject_mutation()"
        )

        self.assertEqual(
            prototype_baseline._canonical_trigger_sql(source_form),
            prototype_baseline._canonical_trigger_sql(catalog_form),
        )

    def test_pg_get_functiondef_spelling_matches_audited_source_semantics(self) -> None:
        source_form = (
            "CREATE FUNCTION reject_mutation() RETURNS TRIGGER LANGUAGE plpgsql "
            "AS $$ BEGIN RAISE EXCEPTION 'blocked'; END; $$"
        )
        catalog_form = (
            "CREATE OR REPLACE FUNCTION public.reject_mutation()\n"
            "RETURNS trigger\nLANGUAGE plpgsql\n"
            "AS $function$ BEGIN RAISE EXCEPTION 'blocked'; END; $function$"
        )

        self.assertEqual(
            prototype_baseline._canonical_function_sql(source_form),
            prototype_baseline._canonical_function_sql(catalog_form),
        )

    def test_expected_fingerprint_covers_current_metadata_and_final_0022_state(self) -> None:
        builder = getattr(prototype_baseline, "_expected_runtime_fingerprint", None)
        self.assertIsNotNone(builder)
        pre_registration = builder("pre_registration")
        final = builder("final")

        self.assertEqual(len(pre_registration["tables"]), 28)
        self.assertEqual(pre_registration["migration_control"], "absent")
        self.assertEqual(
            final["migration_control"],
            tuple(item.version for item in prototype_baseline.MIGRATION_SHA256_MANIFEST),
        )
        self.assertEqual(
            {name for name, _definition in final["functions"]},
            {
                "b2b_reject_career_capability",
                "b2b_reject_career_role_with_capability",
                "b2b_reject_append_only_mutation",
            },
        )
        self.assertEqual(
            {name for name, _table, _definition in final["triggers"]},
            {
                "trg_user_b2b_capabilities_reject_career",
                "trg_users_reject_career_with_b2b_capability",
                "trg_review_decisions_append_only",
                "trg_audit_events_append_only",
            },
        )
        event_type_checks = tuple(
            definition
            for table, name, definition in final["checks"]
            if table == "audit_events" and name == "ck_audit_events_event_type"
        )
        self.assertEqual(len(event_type_checks), 1)
        self.assertIn("scientific_decision_applied", event_type_checks[0])

        primary_keys = {
            table: (name, columns)
            for table, name, columns in final["primary_keys"]
        }
        self.assertEqual(
            primary_keys["review_items"],
            ("review_items_pkey", ("id",)),
        )
        self.assertEqual(
            prototype_baseline._HISTORICAL_PK_NAME_DELTAS[
                "pk_review_items"
            ],
            "review_items_pkey",
        )

    def test_approved_pre_registration_fingerprint_passes(self) -> None:
        approved = {"tables": ("users",), "migration_control": "absent"}
        with (
            patch.object(
                prototype_baseline,
                "_expected_runtime_fingerprint",
                return_value=approved,
                create=True,
            ),
            patch.object(
                prototype_baseline,
                "_read_runtime_fingerprint",
                return_value=approved,
                create=True,
            ),
        ):
            prototype_baseline.validate_runtime_fingerprint(
                Mock(), phase="pre_registration"
            )

    def test_each_semantic_fingerprint_mismatch_is_refused(self) -> None:
        approved = {
            "tables": ("users",),
            "columns": (("users", "id", "INTEGER", False, None),),
            "primary_keys": (("users", ("id",)),),
            "foreign_keys": (),
            "unique_constraints": (),
            "checks": (),
            "indexes": (),
            "functions": (("guard", "body"),),
            "triggers": (("trigger", "definition"),),
            "schemas": ("public",),
            "extensions": ("plpgsql",),
            "migration_control": "absent",
            "privileges": "pre_registration",
        }
        for key in approved:
            drifted = dict(approved)
            drifted[key] = ("unexpected",)
            with self.subTest(key=key), patch.object(
                prototype_baseline,
                "_expected_runtime_fingerprint",
                return_value=approved,
                create=True,
            ), patch.object(
                prototype_baseline,
                "_read_runtime_fingerprint",
                return_value=drifted,
                create=True,
            ):
                with self.assertRaisesRegex(
                    prototype_baseline.BaselineRefused, key
                ):
                    prototype_baseline.validate_runtime_fingerprint(
                        Mock(), phase="pre_registration"
                    )

    def test_unknown_fingerprint_phase_is_refused(self) -> None:
        with self.assertRaisesRegex(prototype_baseline.BaselineRefused, "phase"):
            prototype_baseline.validate_runtime_fingerprint(
                Mock(), phase="unknown"  # type: ignore[arg-type]
            )


class KnownCatalogDeltaTests(unittest.TestCase):
    def test_current_fk_defaults_and_pk_name_deltas_are_explicitly_accepted(self) -> None:
        report = prototype_baseline.verify_known_catalog_deltas(PROJECT_ROOT)

        self.assertIn("NO ACTION", report.author_fk)
        self.assertIn("client defaults", report.historical_defaults)
        self.assertIn("PK/FK semantics", report.historical_pk_names)

    def test_supported_delete_path_requiring_cascade_is_refused(self) -> None:
        with patch.object(
            prototype_baseline,
            "_functions_with_production_deletes",
            return_value=("admin.py:unsafe_delete",),
        ):
            with self.assertRaisesRegex(
                prototype_baseline.BaselineRefused, "CASCADE"
            ):
                prototype_baseline.verify_known_catalog_deltas(PROJECT_ROOT)

    def test_missing_call_site_evidence_is_a_baseline_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(
                prototype_baseline.BaselineRefused, "evidence source"
            ):
                prototype_baseline.verify_known_catalog_deltas(root)

    def test_required_server_default_without_client_fallback_is_refused(self) -> None:
        with patch.object(
            prototype_baseline,
            "_HISTORICAL_DEFAULT_CLIENT_FALLBACKS",
            (("users", "email"),),
        ):
            with self.assertRaisesRegex(
                prototype_baseline.BaselineRefused, "server default"
            ):
                prototype_baseline.verify_known_catalog_deltas(PROJECT_ROOT)

    def test_runtime_dependency_on_historical_pk_name_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime_file = root / "backend/app/operational.py"
            runtime_file.parent.mkdir(parents=True)
            runtime_file.write_text(
                "EXPECTED_CONSTRAINT = 'pk_review_items'\n", encoding="utf-8"
            )
            (root / "backend/scripts").mkdir(parents=True)
            with patch.object(
                prototype_baseline,
                "_functions_with_production_deletes",
                return_value=(),
            ):
                with self.assertRaisesRegex(
                    prototype_baseline.BaselineRefused, "PK name dependency"
                ):
                    prototype_baseline.verify_known_catalog_deltas(root)


class BaselineRegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        with self.engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE schema_migrations ("
                "version VARCHAR(120) PRIMARY KEY, "
                "applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            ))
        self.versions = tuple(item.version for item in prototype_baseline.MIGRATION_SHA256_MANIFEST)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_registration_is_refused_outside_verified_bootstrap(self) -> None:
        with self.assertRaisesRegex(prototype_baseline.BaselineRefused, "verified bootstrap"):
            prototype_baseline.register_verified_baseline(self.engine, self.versions)

    def test_registration_writes_all_22_versions_atomically_when_authorized(self) -> None:
        with prototype_baseline._allow_baseline_registration():
            prototype_baseline.register_verified_baseline(self.engine, self.versions)

        with self.engine.connect() as connection:
            rows = tuple(connection.execute(text(
                "SELECT version FROM schema_migrations ORDER BY applied_at, version"
            )).scalars())
        self.assertEqual(set(rows), set(self.versions))
        self.assertEqual(len(rows), 22)

    def test_preexisting_control_state_is_refused_without_partial_records(self) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text("INSERT INTO schema_migrations (version) VALUES ('unexpected')")
            )
        with self.assertRaisesRegex(prototype_baseline.BaselineRefused, "empty"):
            with prototype_baseline._allow_baseline_registration():
                prototype_baseline.register_verified_baseline(self.engine, self.versions)

        with self.engine.connect() as connection:
            rows = tuple(connection.execute(text(
                "SELECT version FROM schema_migrations"
            )).scalars())
        self.assertEqual(rows, ("unexpected",))


class BaselineOrchestrationTests(unittest.TestCase):
    def test_valid_fresh_path_completes_every_gate_before_registration(self) -> None:
        ordered = Mock()
        versions = tuple(item.version for item in prototype_baseline.MIGRATION_SHA256_MANIFEST)
        runtime_objects = tuple(item.name for item in prototype_baseline.RUNTIME_OBJECT_MANIFEST)
        with (
            patch.object(prototype_baseline, "prove_fresh_prototype_database") as fresh,
            patch.object(prototype_baseline.Base.metadata, "create_all") as create_all,
            patch.object(prototype_baseline, "install_runtime_object_manifest", return_value=runtime_objects) as install,
            patch.object(prototype_baseline, "validate_runtime_fingerprint") as fingerprint,
            patch.object(prototype_baseline, "verify_migration_hash_manifest", return_value=versions) as hashes,
            patch.object(prototype_baseline, "ensure_schema_migrations_table") as ensure_table,
            patch.object(prototype_baseline, "verify_known_catalog_deltas") as deltas,
            patch.object(prototype_baseline, "configure_human_review_privileges", return_value=SimpleNamespace()) as acl,
            patch.object(prototype_baseline, "register_verified_baseline") as register,
        ):
            for name, mocked in (
                ("fresh", fresh),
                ("create_all", create_all),
                ("install", install),
                ("fingerprint", fingerprint),
                ("hashes", hashes),
                ("ensure_table", ensure_table),
                ("deltas", deltas),
                ("acl", acl),
                ("register", register),
            ):
                ordered.attach_mock(mocked, name)

            report = prototype_baseline.establish_prototype_verified_schema_baseline(
                Mock(), "prototype_app", PROJECT_ROOT
            )

        self.assertEqual(report.versions, versions)
        self.assertEqual(report.runtime_objects, runtime_objects)
        self.assertEqual(report.application_role, "prototype_app")
        self.assertEqual(
            ordered.mock_calls,
            [
                call.fresh(unittest.mock.ANY, "prototype_app"),
                call.create_all(bind=unittest.mock.ANY),
                call.install(unittest.mock.ANY),
                call.fingerprint(unittest.mock.ANY, phase="pre_registration"),
                call.hashes(PROJECT_ROOT),
                call.ensure_table(unittest.mock.ANY),
                call.deltas(PROJECT_ROOT),
                call.acl(unittest.mock.ANY, "prototype_app"),
                call.register(unittest.mock.ANY, versions),
                call.fingerprint(unittest.mock.ANY, phase="final"),
            ],
        )

    def test_failed_gate_prevents_registration(self) -> None:
        with (
            patch.object(prototype_baseline, "prove_fresh_prototype_database"),
            patch.object(prototype_baseline.Base.metadata, "create_all"),
            patch.object(prototype_baseline, "install_runtime_object_manifest"),
            patch.object(
                prototype_baseline,
                "validate_runtime_fingerprint",
                side_effect=prototype_baseline.BaselineRefused("fingerprint mismatch"),
            ),
            patch.object(prototype_baseline, "register_verified_baseline") as register,
        ):
            with self.assertRaisesRegex(
                prototype_baseline.BaselineRefused, "fingerprint mismatch"
            ):
                prototype_baseline.establish_prototype_verified_schema_baseline(
                    Mock(), "prototype_app", PROJECT_ROOT
                )
        register.assert_not_called()


@unittest.skipUnless(
    os.environ.get("TASK7_POSTGRES_URL"),
    "TASK7_POSTGRES_URL is required for the disposable PostgreSQL 16 catalog proof",
)
class DisposablePostgresCatalogProofTests(unittest.TestCase):
    def test_fresh_verified_baseline_matches_catalog_and_acl(self) -> None:
        engine = create_engine(os.environ["TASK7_POSTGRES_URL"], pool_pre_ping=True)
        try:
            report = prototype_baseline.establish_prototype_verified_schema_baseline(
                engine,
                "prototype_task7_app",
                PROJECT_ROOT,
            )
            with engine.connect() as connection:
                versions = tuple(connection.execute(text(
                    "SELECT version FROM schema_migrations ORDER BY applied_at, version"
                )).scalars())
                author_fk_action = connection.execute(text(
                    """
                    SELECT constraint_row.confdeltype
                    FROM pg_constraint AS constraint_row
                    JOIN pg_class AS table_row ON table_row.oid=constraint_row.conrelid
                    WHERE table_row.relname='scientific_production_authors'
                      AND constraint_row.contype='f'
                      AND pg_get_constraintdef(constraint_row.oid)
                          LIKE '%FOREIGN KEY (production_id)%'
                    """
                )).scalar_one()
                functions = set(connection.execute(text(
                    """
                    SELECT function_row.proname
                    FROM pg_proc AS function_row
                    JOIN pg_namespace AS namespace
                      ON namespace.oid=function_row.pronamespace
                    WHERE namespace.nspname=current_schema()
                    """
                )).scalars())
                triggers = set(connection.execute(text(
                    "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"
                )).scalars())
                can_read_control = connection.execute(text(
                    "SELECT has_table_privilege("
                    "'prototype_task7_app', 'public.schema_migrations', 'SELECT')"
                )).scalar_one()
                can_create = connection.execute(text(
                    "SELECT has_schema_privilege("
                    "'prototype_task7_app', 'public', 'CREATE')"
                )).scalar_one()
        finally:
            engine.dispose()

        self.assertEqual(tuple(report.versions), versions)
        self.assertEqual(len(versions), 22)
        self.assertEqual(author_fk_action, "a")
        self.assertEqual(
            functions,
            {
                "b2b_reject_career_capability",
                "b2b_reject_career_role_with_capability",
                "b2b_reject_append_only_mutation",
            },
        )
        self.assertEqual(
            triggers,
            {
                "trg_user_b2b_capabilities_reject_career",
                "trg_users_reject_career_with_b2b_capability",
                "trg_review_decisions_append_only",
                "trg_audit_events_append_only",
            },
        )
        self.assertFalse(can_read_control)
        self.assertFalse(can_create)


if __name__ == "__main__":
    unittest.main()
