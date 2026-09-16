from __future__ import annotations

import inspect
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from app.core.database import engine, migration_engine
from app.core.prototype_baseline import BaselineRefused, BaselineReport
from scripts import init_db


BACKEND_ROOT = Path(__file__).resolve().parents[1]


class _SeedDmlReached(RuntimeError):
    pass


class InitDbEngineSeparationTests(unittest.TestCase):
    def test_prototype_bootstrap_establishes_verified_baseline_then_demo_seed(self) -> None:
        self.assertTrue(hasattr(init_db, "prototype_bootstrap"))
        ordered = Mock()
        with (
            patch.object(
                init_db,
                "establish_prototype_verified_schema_baseline",
                return_value=BaselineReport((), (), "prototype_app"),
                create=True,
            ) as establish_baseline,
            patch.object(init_db, "seed") as seed,
            patch.object(
                init_db,
                "settings",
                SimpleNamespace(
                    demo_mode=True,
                    database_url=(
                        "postgresql+psycopg://prototype_app:local@postgres:5432/prototype"
                    ),
                ),
            ),
        ):
            ordered.attach_mock(establish_baseline, "establish_baseline")
            ordered.attach_mock(seed, "seed")
            init_db.prototype_bootstrap()

        self.assertEqual(
            ordered.mock_calls,
            [
                call.establish_baseline(
                    migration_engine,
                    "prototype_app",
                    BACKEND_ROOT.parent,
                ),
                call.seed(),
            ],
        )
        bootstrap_source = inspect.getsource(init_db.prototype_bootstrap)
        self.assertNotIn("create_all", bootstrap_source)
        self.assertNotIn("run_migrations", bootstrap_source)

    def test_prototype_bootstrap_without_demo_establishes_baseline_without_seed(self) -> None:
        self.assertTrue(hasattr(init_db, "prototype_bootstrap"))
        ordered = Mock()
        with (
            patch.object(
                init_db,
                "establish_prototype_verified_schema_baseline",
                return_value=BaselineReport((), (), "prototype_app"),
                create=True,
            ) as establish_baseline,
            patch.object(init_db, "seed") as seed,
            patch.object(
                init_db,
                "settings",
                SimpleNamespace(
                    demo_mode=False,
                    database_url=(
                        "postgresql+psycopg://prototype_app:local@postgres:5432/prototype"
                    ),
                ),
            ),
        ):
            ordered.attach_mock(establish_baseline, "establish_baseline")
            ordered.attach_mock(seed, "seed")
            init_db.prototype_bootstrap()

        self.assertEqual(
            ordered.mock_calls,
            [
                call.establish_baseline(
                    migration_engine,
                    "prototype_app",
                    BACKEND_ROOT.parent,
                ),
            ],
        )
        seed.assert_not_called()

    def test_baseline_refusal_prevents_demo_seed(self) -> None:
        with (
            patch.object(
                init_db,
                "establish_prototype_verified_schema_baseline",
                side_effect=BaselineRefused("fingerprint mismatch"),
                create=True,
            ),
            patch.object(init_db, "seed") as seed,
            patch.object(
                init_db,
                "settings",
                SimpleNamespace(
                    demo_mode=True,
                    database_url=(
                        "postgresql+psycopg://prototype_app:local@postgres:5432/prototype"
                    ),
                ),
            ),
        ):
            with self.assertRaisesRegex(BaselineRefused, "fingerprint mismatch"):
                init_db.prototype_bootstrap()
        seed.assert_not_called()

    def test_seed_opens_only_the_bootstrap_owner_session(self) -> None:
        with patch.object(
            init_db, "BootstrapSessionLocal", side_effect=_SeedDmlReached
        ) as session_factory:
            with self.assertRaises(_SeedDmlReached):
                init_db.seed()

        session_factory.assert_called_once_with()
        self.assertIs(init_db.BootstrapSessionLocal.kw["bind"], migration_engine)
        self.assertIsNot(init_db.BootstrapSessionLocal.kw["bind"], engine)

    def test_script_cli_invokes_only_prototype_bootstrap(self) -> None:
        source = (BACKEND_ROOT / "scripts/init_db.py").read_text(encoding="utf-8")
        cli_block = source.split('if __name__ == "__main__":', 1)[1]

        self.assertIn("prototype_bootstrap()", cli_block)
        self.assertNotIn("\n    seed()", cli_block)


if __name__ == "__main__":
    unittest.main()
