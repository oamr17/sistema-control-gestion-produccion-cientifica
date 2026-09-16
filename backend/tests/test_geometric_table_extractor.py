import re
import sys
import unittest
import unicodedata
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.pdf_parser.geometric_table_extractor import (  # noqa: E402
    extract_production_table_words,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures"
ZAMBRANO_PDF = FIXTURES / "zambrano_fci021_2025.pdf"


def normalized(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return re.sub(r"[^A-Z0-9]+", "", "".join(c for c in decomposed if not unicodedata.combining(c)).upper())


def extract_pdf_rows(source: Path | bytes):
    rows = []
    document = (
        fitz.open(stream=source, filetype="pdf")
        if isinstance(source, bytes)
        else fitz.open(source)
    )
    with document:
        for page in document:
            page_rows, _fallback_reason = extract_production_table_words(page)
            rows.extend(page_rows)
    return rows


def synthetic_geometric_pdf(
    *,
    title: str,
    author_lines: tuple[tuple[str, ...], ...],
) -> bytes:
    if len(author_lines) != 4:
        raise ValueError("synthetic geometric PDF requires exactly four author columns")

    document = fitz.open()
    page = document.new_page(width=612, height=792)
    header_tokens = (
        (30, "TITULO"),
        (210, "AUTOR"),
        (240, "1"),
        (300, "AUTOR"),
        (330, "2"),
        (390, "AUTOR"),
        (420, "3"),
        (480, "AUTOR"),
        (510, "4"),
        (560, "ESTADO"),
    )
    for x, text in header_tokens:
        page.insert_text((x, 60), text, fontsize=8, fontname="helv")

    page.insert_text((30, 100), title, fontsize=8, fontname="helv")
    for x, lines in zip((180, 285, 375, 465), author_lines):
        for line_index, text in enumerate(lines):
            page.insert_text(
                (x, 100 + line_index * 13),
                text,
                fontsize=8,
                fontname="helv",
            )
    page.insert_text((560, 100), "PUBLICADO", fontsize=8, fontname="helv")

    artifact = document.tobytes(
        garbage=4,
        clean=True,
        deflate=True,
        no_new_id=True,
    )
    document.close()
    return artifact


def synthetic_distinct_columns_pdf() -> bytes:
    return synthetic_geometric_pdf(
        title="CASO SINTETICO COLUMNAS",
        author_lines=(
            ("AUTOR BASE",),
            ("ALFA UNO",),
            ("BETA DOS",),
            ("GAMMA TRES",),
        ),
    )


def synthetic_wrapped_author_pdf() -> bytes:
    return synthetic_geometric_pdf(
        title="CASO SINTETICO ENVUELTO",
        author_lines=(
            ("ALFA UNO",),
            ("INVESTIGADOR", "SINTETICO A"),
            ("AUTOR B",),
            ("GAMMA TRES",),
        ),
    )


def find_row(rows, title_fragment: str):
    needle = normalized(title_fragment)
    return next(row for row in rows if needle in normalized(row.title_cell.text))


class FakePage:
    def __init__(self, words, number=0):
        self.words = words
        self.number = number
        self.requested_modes = []

    def get_text(self, mode):
        self.requested_modes.append(mode)
        return self.words


def synthetic_header(y0=10.0, author_y0=None):
    author_y0 = y0 if author_y0 is None else author_y0
    return [
        (10.0, y0, 50.0, y0 + 10.0, "TITULO", 0, 0, 0),
        (100.0, author_y0, 135.0, author_y0 + 10.0, "AUTOR", 0, 1, 0),
        (138.0, author_y0, 144.0, author_y0 + 10.0, "1", 0, 1, 1),
        (200.0, author_y0, 240.0, author_y0 + 10.0, "ESTADO", 0, 2, 0),
    ]


class GeometricTableExtractorTest(unittest.TestCase):
    def test_geometric_pdf_keeps_wrapped_author_column_separate(self):
        rows = extract_pdf_rows(synthetic_wrapped_author_pdf())

        row = find_row(rows, "CASO SINTETICO ENVUELTO")

        self.assertEqual(
            row.author_cells,
            [
                "ALFA UNO",
                "INVESTIGADOR SINTETICO A",
                "AUTOR B",
                "GAMMA TRES",
            ],
        )
        self.assertGreater(
            row.cell("author_2").bbox[3],
            row.cell("author_3").bbox[3],
        )
        self.assertEqual(row.method, "geometry_words")

    def test_geometric_pdf_keeps_distinct_author_columns_separate(self):
        rows = extract_pdf_rows(synthetic_distinct_columns_pdf())

        row = find_row(rows, "CASO SINTETICO COLUMNAS")

        self.assertEqual(row.cell("author_2").text, "ALFA UNO")
        self.assertEqual(row.cell("author_3").text, "BETA DOS")
        self.assertNotEqual(row.cell("author_2").bbox, row.cell("author_3").bbox)
        self.assertLess(row.cell("author_2").bbox[2], row.cell("author_3").bbox[0])
        self.assertEqual(row.title_cell.text, "CASO SINTETICO COLUMNAS")

    @unittest.skipUnless(
        ZAMBRANO_PDF.is_file(),
        "requires independently managed historical PDF fixture",
    )
    def test_zambrano_pdf_preserves_author_column_numbers_and_provenance(self):
        rows = extract_pdf_rows(ZAMBRANO_PDF)

        row = find_row(rows, "INCIDENCIA DEL CAPITAL DE TRABAJO")

        self.assertEqual(row.cell("author_1").text, "María Estefanía Sánchez Pacheco")
        self.assertEqual(row.cell("author_2").text, "María del Carmen Valls Martínez")
        self.assertEqual(row.cell("author_3").text, "Fernando Zambrano Farías")
        self.assertEqual(
            row.title_cell.text,
            "Incidencia del capital de trabajo en la rentabilidad de las pymes en Ecuador",
        )
        self.assertEqual(row.title_cell.page, 7)
        self.assertEqual(len(row.title_cell.bbox), 4)
        self.assertLess(row.title_cell.bbox[0], row.title_cell.bbox[2])
        self.assertLess(row.title_cell.bbox[1], row.title_cell.bbox[3])

    def test_all_synthetic_pdf_cells_include_complete_provenance(self):
        cases = {
            "distinct-columns": synthetic_distinct_columns_pdf(),
            "wrapped-author": synthetic_wrapped_author_pdf(),
        }
        for case, artifact in cases.items():
            with self.subTest(case=case):
                rows = extract_pdf_rows(artifact)
                self.assertTrue(rows)
                for row in rows:
                    self.assertEqual(row.method, "geometry_words")
                    for cell in row.cells:
                        self.assertGreaterEqual(cell.page, 1)
                        self.assertIn(cell.column, {"title", "author_1", "author_2", "author_3", "author_4", "author_5", "status"})
                        self.assertEqual(len(cell.bbox), 4)
                        self.assertTrue(all(isinstance(value, float) for value in cell.bbox))
                        self.assertLess(cell.bbox[0], cell.bbox[2])
                        self.assertLess(cell.bbox[1], cell.bbox[3])

    def test_synthetic_pdf_factory_is_byte_deterministic(self):
        self.assertEqual(
            synthetic_distinct_columns_pdf(),
            synthetic_distinct_columns_pdf(),
        )
        self.assertEqual(
            synthetic_wrapped_author_pdf(),
            synthetic_wrapped_author_pdf(),
        )

    def test_evenly_spaced_single_line_products_use_status_geometry_as_row_boundaries(self):
        page = FakePage(
            synthetic_header()
            + [
                (15.0, 35.0, 45.0, 45.0, "First", 1, 0, 0),
                (105.0, 35.0, 130.0, 45.0, "Alice", 1, 1, 0),
                (205.0, 35.0, 235.0, 45.0, "PUBLICADO", 1, 2, 0),
                (15.0, 50.0, 48.0, 60.0, "Second", 2, 0, 0),
                (105.0, 50.0, 128.0, 60.0, "Bruno", 2, 1, 0),
                (205.0, 50.0, 235.0, 60.0, "ACEPTADO", 2, 2, 0),
            ]
        )

        rows, reason = extract_production_table_words(page)

        self.assertIsNone(reason)
        self.assertEqual([row.title_cell.text for row in rows], ["First", "Second"])
        self.assertEqual([row.author_cells for row in rows], [["Alice"], ["Bruno"]])

    def test_arbitrary_status_text_uses_geometry_only_for_row_boundaries(self):
        page = FakePage(
            synthetic_header()
            + [
                (15.0, 35.0, 45.0, 45.0, "First", 1, 0, 0),
                (105.0, 35.0, 130.0, 45.0, "Alice", 1, 1, 0),
                (205.0, 35.0, 238.0, 45.0, "RECHAZADO", 1, 2, 0),
                (15.0, 50.0, 48.0, 60.0, "Second", 2, 0, 0),
                (105.0, 50.0, 128.0, 60.0, "Bruno", 2, 1, 0),
                (205.0, 50.0, 214.0, 60.0, "NO", 2, 2, 0),
                (216.0, 50.0, 239.0, 60.0, "APLICA", 2, 2, 1),
            ]
        )

        rows, reason = extract_production_table_words(page)

        self.assertIsNone(reason)
        self.assertEqual([row.title_cell.text for row in rows], ["First", "Second"])
        self.assertEqual([row.cell("status").text for row in rows], ["RECHAZADO", "NO APLICA"])

    def test_title_row_without_author_returns_page_fallback(self):
        page = FakePage(
            synthetic_header()
            + [
                (15.0, 35.0, 45.0, 45.0, "Product", 1, 0, 0),
                (205.0, 35.0, 235.0, 45.0, "PUBLICADO", 1, 2, 0),
            ]
        )

        rows, reason = extract_production_table_words(page)

        self.assertEqual(rows, [])
        self.assertEqual(reason, "production_table_required_cells_missing")

    def test_title_row_without_status_returns_page_fallback(self):
        page = FakePage(
            synthetic_header()
            + [
                (15.0, 35.0, 45.0, 45.0, "Product", 1, 0, 0),
                (105.0, 35.0, 130.0, 45.0, "Alice", 1, 1, 0),
            ]
        )

        rows, reason = extract_production_table_words(page)

        self.assertEqual(rows, [])
        self.assertEqual(reason, "production_table_required_cells_missing")

    def test_one_missing_status_invalidates_other_rows_on_the_page(self):
        page = FakePage(
            synthetic_header()
            + [
                (15.0, 35.0, 45.0, 45.0, "First", 1, 0, 0),
                (105.0, 35.0, 130.0, 45.0, "Alice", 1, 1, 0),
                (205.0, 35.0, 235.0, 45.0, "PUBLICADO", 1, 2, 0),
                (15.0, 50.0, 48.0, 60.0, "Second", 2, 0, 0),
                (105.0, 50.0, 128.0, 60.0, "Bruno", 2, 1, 0),
            ]
        )

        rows, reason = extract_production_table_words(page)

        self.assertEqual(rows, [])
        self.assertEqual(reason, "production_table_required_cells_missing")

    def test_neighboring_lines_do_not_form_a_false_header_band(self):
        page = FakePage(
            synthetic_header(y0=10.0, author_y0=25.0)
            + [
                (15.0, 45.0, 45.0, 55.0, "Product", 1, 0, 0),
                (105.0, 45.0, 130.0, 55.0, "Alice", 1, 1, 0),
                (205.0, 45.0, 235.0, 55.0, "PUBLICADO", 1, 2, 0),
            ]
        )

        rows, reason = extract_production_table_words(page)

        self.assertEqual(rows, [])
        self.assertEqual(reason, "production_table_header_not_found")

    @unittest.skipUnless(
        ZAMBRANO_PDF.is_file(),
        "requires independently managed historical PDF fixture",
    )
    def test_page_without_production_header_returns_fallback(self):
        with fitz.open(ZAMBRANO_PDF) as document:
            rows, reason = extract_production_table_words(document[0])

        self.assertEqual(rows, [])
        self.assertEqual(reason, "production_table_header_not_found")

    def test_missing_words_return_fallback_and_only_words_mode_is_requested(self):
        page = FakePage([])

        rows, reason = extract_production_table_words(page)

        self.assertEqual(rows, [])
        self.assertEqual(reason, "page_words_missing")
        self.assertEqual(page.requested_modes, ["words"])

    def test_malformed_word_geometry_returns_fallback(self):
        page = FakePage([(40.0, 10.0, 20.0, 20.0, "TÍTULO", 0, 0, 0)])

        rows, reason = extract_production_table_words(page)

        self.assertEqual(rows, [])
        self.assertEqual(reason, "malformed_word_geometry")

    def test_overlapping_header_geometry_returns_fallback(self):
        page = FakePage(
            [
                (10.0, 10.0, 50.0, 20.0, "TÍTULO", 0, 0, 0),
                (45.0, 10.0, 75.0, 20.0, "AUTOR", 0, 1, 0),
                (66.0, 10.0, 72.0, 20.0, "1", 0, 1, 1),
                (90.0, 10.0, 130.0, 20.0, "ESTADO", 0, 2, 0),
            ]
        )

        rows, reason = extract_production_table_words(page)

        self.assertEqual(rows, [])
        self.assertEqual(reason, "production_table_header_columns_overlap")


if __name__ == "__main__":
    unittest.main()
