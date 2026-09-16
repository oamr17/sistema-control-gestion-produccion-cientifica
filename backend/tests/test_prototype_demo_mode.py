from __future__ import annotations

import ast
import hashlib
import importlib
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import types
import unittest
from urllib.parse import unquote, urlsplit
from unittest.mock import MagicMock, patch

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from app.api import dependencies
from app.api.v1.endpoints import admin
from app.core.config import Settings
from app.core.database import Base
from app.core import migrations
from app.models.enums import UserRole
from scripts import init_db


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
DESTRUCTIVE_ENDPOINTS = (
    ("/admin/reset-imported-data", {"confirm": "RESET_IMPORTED_DATA"}),
    (
        "/admin/clean-invalid-imported-records",
        {"confirm": "CLEAN_INVALID_IMPORTED_RECORDS"},
    ),
)
PRESERVED_SEED_DML_SHA256 = (
    "4af20192eff12d0f397c672c1cfdc4719b769df50c367c83c2bb30af6d9e89c6"
)


class DemoModeSettingsTests(unittest.TestCase):
    def test_demo_mode_is_false_when_environment_variable_is_missing(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            configured = Settings(_env_file=None)

        self.assertIs(getattr(configured, "demo_mode", None), False)

    def test_demo_mode_parses_explicit_false_as_false(self) -> None:
        with patch.dict(os.environ, {"DEMO_MODE": "false"}, clear=True):
            configured = Settings(_env_file=None)

        self.assertIs(getattr(configured, "demo_mode", None), False)

    def test_demo_mode_parses_explicit_true_as_true(self) -> None:
        with patch.dict(os.environ, {"DEMO_MODE": "true"}, clear=True):
            configured = Settings(_env_file=None)

        self.assertIs(getattr(configured, "demo_mode", None), True)

    def test_demo_mode_rejects_non_literal_truthy_environment_values(self) -> None:
        for value in ("1", "yes", "on"):
            with self.subTest(value=value), patch.dict(
                os.environ, {"DEMO_MODE": value}, clear=True
            ):
                configured = Settings(_env_file=None)

                self.assertIs(configured.demo_mode, False)

    def test_demo_mode_rejects_non_exact_true_strings(self) -> None:
        for value in ("TRUE", "True", " true "):
            with self.subTest(value=value), patch.dict(
                os.environ, {"DEMO_MODE": value}, clear=True
            ):
                configured = Settings(_env_file=None)

                self.assertIs(configured.demo_mode, False)


class DestructiveAdminDemoGateTests(unittest.TestCase):
    def _client_for(self, role: UserRole) -> tuple[TestClient, MagicMock]:
        test_app = FastAPI()
        test_app.include_router(admin.router, prefix="/admin")
        database = MagicMock()
        database.query.return_value.delete.return_value = 0
        database.query.return_value.all.return_value = []
        test_app.dependency_overrides[dependencies.get_db] = lambda: database
        test_app.dependency_overrides[dependencies.get_current_user] = lambda: SimpleNamespace(
            role=role
        )
        return TestClient(test_app, raise_server_exceptions=False), database

    def test_inventory_confirms_the_two_named_destructive_admin_functions(self) -> None:
        confirmed_routes = {
            route.endpoint.__name__: (route.path, frozenset(route.methods))
            for route in admin.router.routes
            if route.endpoint
            in {admin.reset_imported_data, admin.clean_invalid_imported_records}
        }
        self.assertEqual(
            confirmed_routes,
            {
                "reset_imported_data": (
                    "/reset-imported-data",
                    frozenset({"POST"}),
                ),
                "clean_invalid_imported_records": (
                    "/clean-invalid-imported-records",
                    frozenset({"POST"}),
                ),
            },
        )

    def test_authorized_faculty_admin_is_denied_when_demo_mode_is_false(self) -> None:
        for path, payload in DESTRUCTIVE_ENDPOINTS:
            with self.subTest(path=path):
                client, database = self._client_for(UserRole.FACULTY_ADMIN)
                with patch.object(
                    admin,
                    "settings",
                    SimpleNamespace(app_env="development", demo_mode=False),
                ):
                    response = client.post(path, json=payload)
                client.close()

                self.assertEqual(response.status_code, 403, response.text)
                database.query.assert_not_called()

    def test_unauthorized_role_is_denied_even_when_demo_mode_is_true(self) -> None:
        for path, payload in DESTRUCTIVE_ENDPOINTS:
            with self.subTest(path=path):
                client, database = self._client_for(UserRole.CAREER_MANAGER)
                with patch.object(
                    admin,
                    "settings",
                    SimpleNamespace(app_env="development", demo_mode=True),
                ):
                    response = client.post(path, json=payload)
                client.close()

                self.assertEqual(response.status_code, 403, response.text)
                database.query.assert_not_called()

    def test_authorized_faculty_admin_can_run_each_action_in_demo_mode(self) -> None:
        for path, payload in DESTRUCTIVE_ENDPOINTS:
            with self.subTest(path=path):
                client, database = self._client_for(UserRole.FACULTY_ADMIN)
                with patch.object(
                    admin,
                    "settings",
                    SimpleNamespace(app_env="development", demo_mode=True),
                ):
                    response = client.post(path, json=payload)
                client.close()

                self.assertEqual(response.status_code, 200, response.text)
                database.commit.assert_called_once_with()

    def test_production_guard_still_denies_each_action_in_demo_mode(self) -> None:
        for path, payload in DESTRUCTIVE_ENDPOINTS:
            with self.subTest(path=path):
                client, database = self._client_for(UserRole.FACULTY_ADMIN)
                with patch.object(
                    admin,
                    "settings",
                    SimpleNamespace(app_env="production", demo_mode=True),
                ):
                    response = client.post(path, json=payload)
                client.close()

                self.assertEqual(response.status_code, 403, response.text)
                database.query.assert_not_called()


class PrototypeServingBoundaryTests(unittest.TestCase):
    def test_docker_serving_command_is_uvicorn_only(self) -> None:
        dockerfile = (BACKEND_ROOT / "Dockerfile").read_text(encoding="utf-8")
        command = next(
            line.strip() for line in dockerfile.splitlines() if line.startswith("CMD ")
        )

        self.assertIn('CMD ["uvicorn",', command)
        for forbidden in ("init_db.py", "prototype_bootstrap", "create_all", "seed"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, command)

    def test_compose_separates_serving_from_explicit_one_shot_bootstrap(self) -> None:
        compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("  prototype-bootstrap:\n", compose)
        backend_block = compose.split("  backend:\n", 1)[1].split(
            "\n  prototype-bootstrap:\n", 1
        )[0]
        bootstrap_block = compose.split("  prototype-bootstrap:\n", 1)[1].split(
            "\n  frontend:\n", 1
        )[0]

        self.assertIn(
            "DATABASE_URL: ${COMPOSE_APP_DATABASE_URL:?COMPOSE_APP_DATABASE_URL is required}",
            backend_block,
        )
        self.assertIn("DEMO_MODE:", backend_block)
        self.assertNotIn("MIGRATION_DATABASE_URL", backend_block)
        self.assertNotIn("prototype-bootstrap", backend_block)

        self.assertIn('profiles: ["prototype-bootstrap"]', bootstrap_block)
        self.assertIn("python", bootstrap_block)
        self.assertIn("scripts/init_db.py", bootstrap_block)
        self.assertIn(
            "DATABASE_URL: ${COMPOSE_APP_DATABASE_URL:?COMPOSE_APP_DATABASE_URL is required}",
            bootstrap_block,
        )
        self.assertIn(
            "MIGRATION_DATABASE_URL: "
            "${COMPOSE_MIGRATION_DATABASE_URL:?COMPOSE_MIGRATION_DATABASE_URL is required}",
            bootstrap_block,
        )
        self.assertIn("DEMO_MODE:", bootstrap_block)

    def test_main_defines_no_schema_migration_or_seed_startup_side_effect(self) -> None:
        main_path = BACKEND_ROOT / "app/main.py"
        source = main_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        referenced_names = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        }

        self.assertTrue(
            referenced_names.isdisjoint(
                {"create_all", "run_migrations", "seed", "prototype_bootstrap"}
            )
        )
        startup_decorators = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "on_event"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "startup"
        ]
        self.assertEqual(startup_decorators, [])

    def test_import_and_startup_make_no_bootstrap_calls(self) -> None:
        router_stub = types.ModuleType("app.api.v1.router")
        router_stub.api_router = APIRouter()
        previous_main = sys.modules.pop("app.main", None)
        try:
            with (
                patch.dict(sys.modules, {"app.api.v1.router": router_stub}),
                patch.object(Base.metadata, "create_all") as create_all,
                patch.object(migrations, "run_migrations") as run_migrations,
                patch.object(init_db, "seed") as seed,
                patch.object(init_db, "prototype_bootstrap") as prototype_bootstrap,
            ):
                imported_main = importlib.import_module("app.main")
                with TestClient(imported_main.app):
                    pass

            create_all.assert_not_called()
            run_migrations.assert_not_called()
            seed.assert_not_called()
            prototype_bootstrap.assert_not_called()
        finally:
            sys.modules.pop("app.main", None)
            if previous_main is not None:
                sys.modules["app.main"] = previous_main

    def test_demo_seed_dml_and_approved_credentials_remain_exact(self) -> None:
        source = (BACKEND_ROOT / "scripts/init_db.py").read_text(encoding="utf-8")
        seed_body = source[
            source.index("    db = BootstrapSessionLocal()") : source.index(
                '\n\n\nif __name__ == "__main__":'
            )
        ]

        self.assertEqual(
            hashlib.sha256(seed_body.encode()).hexdigest(),
            PRESERVED_SEED_DML_SHA256,
        )
        for exact_value in (
            'email="admin@university.edu"',
            'hash_password("Admin123*")',
            "role=UserRole.FACULTY_ADMIN",
            'email="adm.manager@university.edu"',
            'hash_password("Manager123*")',
            "role=UserRole.CAREER_MANAGER",
        ):
            with self.subTest(exact_value=exact_value):
                self.assertIn(exact_value, seed_body)


class ReproducibleEnvironmentTests(unittest.TestCase):
    ENVIRONMENT_KEYS = {
        "ACCESS_TOKEN_EXPIRE_MINUTES",
        "APP_DATABASE_URL",
        "APP_ENV",
        "BACKEND_PORT",
        "CORS_ORIGINS",
        "DEMO_MODE",
        "DROPBOX_CLIENT_ID",
        "DROPBOX_CLIENT_SECRET",
        "DROPBOX_REFRESH_TOKEN",
        "EVIDENCE_ALLOWED_EXTENSIONS",
        "EVIDENCE_ALLOWED_MIME_TYPES",
        "EVIDENCE_MAX_BYTES",
        "FRONTEND_PORT",
        "IMPORT_PDF_MAX_CONCURRENCY",
        "IMPORT_PDF_MAX_RETRIES",
        "INGEST_API_KEY",
        "JWT_ALGORITHM",
        "JWT_SECRET",
        "MIGRATION_DATABASE_URL",
        "MINIO_ACCESS_KEY",
        "MINIO_API_PORT",
        "MINIO_BUCKET",
        "MINIO_CONSOLE_PORT",
        "MINIO_ENDPOINT",
        "MINIO_ROOT_PASSWORD",
        "MINIO_ROOT_USER",
        "MINIO_SECRET_KEY",
        "N8N_HOST",
        "N8N_PORT",
        "N8N_PROTOCOL",
        "N8N_PUBLIC_PORT",
        "N8N_WEBHOOK_URL",
        "NEXT_PUBLIC_API_URL",
        "POSTGRES_APP_PASSWORD",
        "POSTGRES_APP_USER",
        "POSTGRES_DB",
        "POSTGRES_OWNER_PASSWORD",
        "POSTGRES_OWNER_USER",
        "POSTGRES_PORT",
        "SEED_DEMO_DATA",
    }
    COPY_COMMAND = "Copy-Item .env.example .env"
    INFRASTRUCTURE_COMMAND = (
        "docker compose --project-name scientific-prototype-demo --env-file .env "
        "up -d --wait postgres minio"
    )
    BOOTSTRAP_COMMAND = (
        "docker compose --project-name scientific-prototype-demo --env-file .env "
        "--profile prototype-bootstrap run --rm --build prototype-bootstrap"
    )
    SERVING_COMMAND = (
        "docker compose --project-name scientific-prototype-demo --env-file .env "
        "up -d --build backend frontend n8n"
    )

    def _environment(self) -> tuple[str, dict[str, str]]:
        path = PROJECT_ROOT / ".env.example"
        self.assertTrue(path.is_file(), ".env.example must exist")
        text = path.read_text(encoding="utf-8")
        values = {
            match.group("key"): match.group("value")
            for match in re.finditer(
                r"^(?P<key>[A-Z][A-Z0-9_]*)=(?P<value>.*)$", text, re.MULTILINE
            )
        }
        return text, values

    def test_env_example_covers_compose_and_prototype_settings(self) -> None:
        compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        compose_substitutions = {
            match.group(1)
            for match in re.finditer(
                r"(?<!\$)\$\{([A-Z][A-Z0-9_]*)[^}]*\}", compose
            )
        }
        _, values = self._environment()

        self.assertEqual(compose_substitutions - values.keys(), set())
        self.assertEqual(self.ENVIRONMENT_KEYS - values.keys(), set())
        self.assertEqual(values["DEMO_MODE"], "true")
        self.assertIn(values["SEED_DEMO_DATA"], {"true", "false"})

    def test_env_example_uses_distinct_owner_and_runtime_database_roles(self) -> None:
        _, values = self._environment()
        application_url = urlsplit(values["APP_DATABASE_URL"])
        migration_url = urlsplit(values["MIGRATION_DATABASE_URL"])

        self.assertEqual(application_url.scheme, "postgresql+psycopg")
        self.assertEqual(migration_url.scheme, "postgresql+psycopg")
        self.assertEqual(unquote(application_url.username or ""), values["POSTGRES_APP_USER"])
        self.assertEqual(
            unquote(application_url.password or ""), values["POSTGRES_APP_PASSWORD"]
        )
        self.assertEqual(
            unquote(migration_url.username or ""), values["POSTGRES_OWNER_USER"]
        )
        self.assertEqual(
            unquote(migration_url.password or ""), values["POSTGRES_OWNER_PASSWORD"]
        )
        self.assertEqual(application_url.hostname, "postgres")
        self.assertEqual(migration_url.hostname, "postgres")
        self.assertEqual(application_url.path, f"/{values['POSTGRES_DB']}")
        self.assertEqual(migration_url.path, f"/{values['POSTGRES_DB']}")
        self.assertNotEqual(values["APP_DATABASE_URL"], values["MIGRATION_DATABASE_URL"])
        self.assertNotEqual(values["POSTGRES_APP_USER"], values["POSTGRES_OWNER_USER"])
        self.assertNotEqual(
            values["POSTGRES_APP_PASSWORD"], values["POSTGRES_OWNER_PASSWORD"]
        )

    def test_compose_database_urls_ignore_stale_host_database_url_overrides(self) -> None:
        compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        _, values = self._environment()

        self.assertEqual(
            values["COMPOSE_APP_DATABASE_URL"], values["APP_DATABASE_URL"]
        )
        self.assertEqual(
            values["COMPOSE_MIGRATION_DATABASE_URL"],
            values["MIGRATION_DATABASE_URL"],
        )
        self.assertIn(
            "DATABASE_URL: ${COMPOSE_APP_DATABASE_URL", compose
        )
        self.assertIn(
            "MIGRATION_DATABASE_URL: ${COMPOSE_MIGRATION_DATABASE_URL", compose
        )
        self.assertNotIn("DATABASE_URL: ${APP_DATABASE_URL", compose)
        self.assertNotIn(
            "MIGRATION_DATABASE_URL: ${MIGRATION_DATABASE_URL", compose
        )

    def test_compose_provisions_isolated_runtime_role_before_bootstrap_acl(self) -> None:
        compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("  postgres-app-role:\n", compose)
        role_block = compose.split("  postgres-app-role:\n", 1)[1].split(
            "\n  backend:\n", 1
        )[0]
        bootstrap_block = compose.split("  prototype-bootstrap:\n", 1)[1].split(
            "\n  frontend:\n", 1
        )[0]

        for required in (
            "NOSUPERUSER",
            "NOCREATEDB",
            "NOCREATEROLE",
            "NOREPLICATION",
            "NOBYPASSRLS",
            "GRANT CONNECT",
            "GRANT USAGE ON SCHEMA public",
        ):
            with self.subTest(required=required):
                self.assertIn(required, role_block)
        for forbidden in (
            "GRANT ALL",
            "GRANT CREATE",
            "ON ALL TABLES",
            "ON ALL SEQUENCES",
            "ALTER DEFAULT PRIVILEGES",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, role_block)
        self.assertIn("postgres-app-role:\n        condition: service_completed_successfully", bootstrap_block)

    def test_backend_image_preserves_repository_layout_for_baseline_manifest(self) -> None:
        compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        dockerfile = (PROJECT_ROOT / "backend/Dockerfile").read_text(encoding="utf-8")
        backend_block = compose.split("  backend:\n", 1)[1].split(
            "\n  prototype-bootstrap:\n", 1
        )[0]
        bootstrap_block = compose.split("  prototype-bootstrap:\n", 1)[1].split(
            "\n  frontend:\n", 1
        )[0]

        for service_block in (backend_block, bootstrap_block):
            self.assertIn("build:\n      context: .\n      dockerfile: backend/Dockerfile", service_block)
        self.assertIn("ENV PYTHONPATH=/workspace/backend", dockerfile)
        self.assertIn("WORKDIR /workspace/backend", dockerfile)
        self.assertIn("COPY backend/requirements.txt .", dockerfile)
        self.assertIn("COPY backend/ .", dockerfile)

    def test_backend_and_bootstrap_receive_only_their_required_database_urls(self) -> None:
        compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        backend_block = compose.split("  backend:\n", 1)[1].split(
            "\n  prototype-bootstrap:\n", 1
        )[0]
        bootstrap_block = compose.split("  prototype-bootstrap:\n", 1)[1].split(
            "\n  frontend:\n", 1
        )[0]

        self.assertIn("DATABASE_URL: ${COMPOSE_APP_DATABASE_URL", backend_block)
        self.assertNotIn("MIGRATION_DATABASE_URL", backend_block)
        self.assertIn('command: ["python", "scripts/init_db.py"]', bootstrap_block)
        self.assertIn("DATABASE_URL: ${COMPOSE_APP_DATABASE_URL", bootstrap_block)
        self.assertIn(
            "MIGRATION_DATABASE_URL: ${COMPOSE_MIGRATION_DATABASE_URL",
            bootstrap_block,
        )

    def test_optional_external_integrations_are_empty_and_documented(self) -> None:
        _, values = self._environment()
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertEqual(values["DROPBOX_CLIENT_ID"], "")
        self.assertEqual(values["DROPBOX_CLIENT_SECRET"], "")
        self.assertEqual(values["DROPBOX_REFRESH_TOKEN"], "")
        self.assertEqual(values["N8N_WEBHOOK_URL"], "")
        self.assertIn("Faltan las credenciales de Dropbox en el backend.", readme)
        self.assertIn("`skipped`", readme)

    def test_readme_documents_exact_env_file_commands_without_shell_secrets(self) -> None:
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

        for command in (
            self.COPY_COMMAND,
            self.INFRASTRUCTURE_COMMAND,
            self.BOOTSTRAP_COMMAND,
            self.SERVING_COMMAND,
        ):
            with self.subTest(command=command):
                self.assertIn(command, readme)
        self.assertNotIn("$env:", readme)
        self.assertNotIn("setx ", readme.lower())
        self.assertIn(
            "docker compose --project-name scientific-prototype-demo --env-file .env down --volumes",
            readme,
        )


if __name__ == "__main__":
    unittest.main()
