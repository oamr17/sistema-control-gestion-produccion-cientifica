from __future__ import annotations

import importlib
import unittest


class BackendImportGraphTests(unittest.TestCase):
    def test_app_main_imports_without_missing_runtime_modules(self) -> None:
        try:
            imported_main = importlib.import_module("app.main")
        except ModuleNotFoundError as exc:
            self.fail(f"backend runtime import graph is incomplete: {exc.name}")

        self.assertIsNotNone(imported_main.app)


if __name__ == "__main__":
    unittest.main()
