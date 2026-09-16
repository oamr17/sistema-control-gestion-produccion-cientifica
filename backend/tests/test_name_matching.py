import unittest
from types import SimpleNamespace

from app.services.import_service import ImportService, _person_match_tokens


class PersistedNameMatchingTests(unittest.TestCase):
    def test_author_is_not_linked_by_given_names_and_one_shared_surname(self):
        teacher = SimpleNamespace(full_name="Ana Maria Paredes Ruiz")

        matched, confidence = ImportService._match_teacher_by_tokens(
            [teacher],
            _person_match_tokens("Ana Maria Paredes Rios"),
        )

        self.assertIsNone(matched)
        self.assertEqual(confidence, 0.0)


if __name__ == "__main__":
    unittest.main()
