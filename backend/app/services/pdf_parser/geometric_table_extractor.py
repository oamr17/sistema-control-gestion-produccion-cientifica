from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Sequence


BBox = tuple[float, float, float, float]
Word = tuple[float, float, float, float, str]


@dataclass(frozen=True)
class GeometricCell:
    text: str
    page: int
    column: str
    bbox: BBox


@dataclass(frozen=True)
class GeometricProductionRow:
    cells: tuple[GeometricCell, ...]
    method: str = "geometry_words"
    warnings: tuple[str, ...] = ()

    def cell(self, column: str) -> GeometricCell:
        for item in self.cells:
            if item.column == column:
                return item
        raise KeyError(column)

    @property
    def title_cell(self) -> GeometricCell:
        return self.cell("title")

    @property
    def author_cells(self) -> list[str]:
        authors = [item for item in self.cells if item.column.startswith("author_")]
        authors.sort(key=lambda item: int(item.column.removeprefix("author_")))
        return [item.text for item in authors]


@dataclass(frozen=True)
class _Header:
    column: str
    bbox: BBox

    @property
    def center(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2.0


@dataclass
class _Line:
    words: list[Word]

    @property
    def bbox(self) -> BBox:
        return _union_bbox(self.words)


_SECTION_NUMBER = re.compile(r"^\d+\.$")
_SHORT_STANDALONE_WORDS = {
    "A",
    "AL",
    "AND",
    "DE",
    "DEL",
    "EL",
    "EN",
    "FOR",
    "IN",
    "LA",
    "LAS",
    "LOS",
    "OF",
    "ON",
    "OR",
    "PARA",
    "POR",
    "THE",
    "TO",
    "UN",
    "UNA",
    "Y",
}


def _key(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(character for character in decomposed if not unicodedata.combining(character)).upper()


def _union_bbox(words: Iterable[Word]) -> BBox:
    materialized = list(words)
    return (
        min(word[0] for word in materialized),
        min(word[1] for word in materialized),
        max(word[2] for word in materialized),
        max(word[3] for word in materialized),
    )


def _vertical_overlap(left: Word, right: Word) -> float:
    return min(left[3], right[3]) - max(left[1], right[1])


def _group_by_y_overlap(words: Iterable[Word]) -> list[_Line]:
    lines: list[_Line] = []
    for word in sorted(words, key=lambda item: (item[1], item[0])):
        best_line = None
        best_overlap = 0.0
        for line in lines:
            overlap = max(_vertical_overlap(word, existing) for existing in line.words)
            minimum_height = min(word[3] - word[1], line.bbox[3] - line.bbox[1])
            if overlap > best_overlap and overlap >= minimum_height * 0.35:
                best_line = line
                best_overlap = overlap
        if best_line is None:
            lines.append(_Line([word]))
        else:
            best_line.words.append(word)
            best_line.words.sort(key=lambda item: item[0])
    return sorted(lines, key=lambda line: (line.bbox[1], line.bbox[0]))


def _validated_words(raw_words: Any) -> tuple[list[Word], str | None]:
    if not raw_words:
        return [], "page_words_missing"
    words: list[Word] = []
    for raw_word in raw_words:
        if not isinstance(raw_word, Sequence) or len(raw_word) < 5:
            return [], "malformed_word_geometry"
        try:
            x0, y0, x1, y1 = (float(raw_word[index]) for index in range(4))
        except (TypeError, ValueError):
            return [], "malformed_word_geometry"
        if not all(math.isfinite(value) for value in (x0, y0, x1, y1)) or x0 >= x1 or y0 >= y1:
            return [], "malformed_word_geometry"
        text = str(raw_word[4]).strip()
        if text:
            words.append((x0, y0, x1, y1, text))
    if not words:
        return [], "page_words_missing"
    return words, None


def _same_header_line(word: Word, title: Word) -> bool:
    overlap = _vertical_overlap(word, title)
    minimum_height = min(title[3] - title[1], word[3] - word[1])
    return overlap >= minimum_height * 0.35


def _near_header_band(word: Word, title: Word) -> bool:
    height = max(title[3] - title[1], word[3] - word[1])
    title_center = (title[1] + title[3]) / 2.0
    word_center = (word[1] + word[3]) / 2.0
    return abs(word_center - title_center) <= height * 2.1


def _find_headers(words: list[Word]) -> tuple[list[_Header], str | None]:
    title_candidates = [word for word in words if _key(word[4]) == "TITULO"]
    for title in title_candidates:
        line = [word for word in words if _same_header_line(word, title)]
        nearby = [word for word in words if _near_header_band(word, title)]
        headers: list[_Header] = [_Header("title", title[:4])]

        for word in line:
            normalized = _key(word[4])
            if normalized == "ESTADO":
                headers.append(_Header("status", word[:4]))

        for word in nearby:
            normalized = _key(word[4])
            if normalized == "ESTADO":
                continue
            elif normalized in {"IMPACT", "IMPACTO"}:
                headers.append(_Header("impact", word[:4]))
            elif normalized == "LINK":
                headers.append(_Header("link", word[:4]))

        author_words = [word for word in line if _key(word[4]) == "AUTOR"]
        number_words = [word for word in line if _key(word[4]).isdigit()]
        for author in author_words:
            number = min(
                (
                    candidate
                    for candidate in number_words
                    if 0.0 <= candidate[0] - author[2] <= 8.0 and _vertical_overlap(author, candidate) > 0.0
                ),
                key=lambda candidate: candidate[0],
                default=None,
            )
            suffix = _key(number[4]) if number is not None else "1"
            bbox = _union_bbox([author, number]) if number is not None else author[:4]
            headers.append(_Header(f"author_{suffix}", bbox))

        columns = {header.column for header in headers}
        if "status" not in columns or not any(column.startswith("author_") for column in columns):
            continue

        deduplicated = {header.column: header for header in headers}
        ordered = sorted(deduplicated.values(), key=lambda header: header.center)
        expected_order = ["title"]
        expected_order.extend(sorted((item for item in columns if item.startswith("author_")), key=lambda item: int(item[7:])))
        expected_order.append("status")
        expected_order.extend(item for item in ("impact", "link") if item in columns)
        if [header.column for header in ordered] != expected_order:
            return [], "production_table_header_columns_overlap"
        if any(left.bbox[2] >= right.bbox[0] for left, right in zip(ordered, ordered[1:])):
            return [], "production_table_header_columns_overlap"
        return ordered, None
    return [], "production_table_header_not_found"


def _column_intervals(headers: list[_Header]) -> dict[str, tuple[float, float]]:
    centers = [header.center for header in headers]
    boundaries = [(left + right) / 2.0 for left, right in zip(centers, centers[1:])]
    first_width = boundaries[0] - centers[0]
    last_width = centers[-1] - boundaries[-1]
    edges = [centers[0] - first_width, *boundaries, centers[-1] + last_width]
    return {header.column: (edges[index], edges[index + 1]) for index, header in enumerate(headers)}


def _table_end(words: list[Word], header_bottom: float) -> float:
    for line in _group_by_y_overlap(word for word in words if word[1] > header_bottom):
        line_words = sorted(line.words, key=lambda word: word[0])
        if _SECTION_NUMBER.match(line_words[0][4]) and len(line_words) >= 2:
            return line.bbox[1]
    return max(word[3] for word in words) + 1.0


def _words_in_interval(words: Iterable[Word], interval: tuple[float, float]) -> list[Word]:
    left, right = interval
    return [word for word in words if left <= (word[0] + word[2]) / 2.0 < right]


def _split_lines_by_whitespace(lines: list[_Line]) -> list[list[_Line]]:
    if not lines:
        return []
    line_heights = sorted(line.bbox[3] - line.bbox[1] for line in lines)
    typical_height = line_heights[len(line_heights) // 2]
    groups: list[list[_Line]] = [[lines[0]]]
    for previous, current in zip(lines, lines[1:]):
        whitespace = current.bbox[1] - previous.bbox[3]
        if whitespace >= typical_height * 0.45:
            groups.append([])
        groups[-1].append(current)
    return groups


def _line_group_bbox(group: list[_Line]) -> BBox:
    return _union_bbox(word for line in group for word in line.words)


def _title_chunks(title_words: list[Word], status_words: list[Word]) -> list[list[_Line]]:
    title_lines = _group_by_y_overlap(title_words)
    if not title_lines:
        return []

    title_top = title_lines[0].bbox[1]
    title_bottom = title_lines[-1].bbox[3]
    status_lines = [
        line
        for line in _group_by_y_overlap(status_words)
        if title_top <= (line.bbox[1] + line.bbox[3]) / 2.0 <= title_bottom
    ]
    title_groups = _split_lines_by_whitespace(title_lines)
    status_groups = _split_lines_by_whitespace(status_lines)
    if not status_groups or len(title_groups) != len(status_groups):
        return []

    for title_group, status_group in zip(title_groups, status_groups):
        title_box = _line_group_bbox(title_group)
        status_box = _line_group_bbox(status_group)
        if min(title_box[3], status_box[3]) <= max(title_box[1], status_box[1]):
            return []
    return title_groups


def _reconstruct_cell(words: list[Word], interval: tuple[float, float]) -> str:
    lines = _group_by_y_overlap(words)
    if not lines:
        return ""
    tokens_by_line = [[word[4] for word in line.words] for line in lines]
    right_edge = interval[1]
    join_margin = max(6.0, (interval[1] - interval[0]) * 0.14)
    output = list(tokens_by_line[0])
    for previous_line, line_tokens, line in zip(lines, tokens_by_line[1:], lines[1:]):
        first_token = line_tokens[0]
        previous_last = previous_line.words[-1]
        joins_fragment = (
            right_edge - previous_last[2] <= join_margin
            and len(previous_line.words) == 1
            and len(_key(first_token)) <= 5
            and _key(first_token).strip(".,:;()") not in _SHORT_STANDALONE_WORDS
            and len(_key(output[-1])) >= 5
            and first_token[0].isalnum()
        )
        if joins_fragment:
            output[-1] += first_token
            output.extend(line_tokens[1:])
        else:
            output.extend(line_tokens)
    return " ".join(output)


def _page_number(page: Any) -> int:
    number = getattr(page, "number", 0)
    return int(number) + 1 if isinstance(number, int) else 1


def extract_production_table_words(page: Any) -> tuple[list[GeometricProductionRow], str | None]:
    words, malformed_reason = _validated_words(page.get_text("words"))
    if malformed_reason:
        return [], malformed_reason

    headers, header_reason = _find_headers(words)
    if header_reason:
        return [], header_reason

    intervals = _column_intervals(headers)
    header_bottom = max(header.bbox[3] for header in headers)
    table_end = _table_end(words, header_bottom)
    data_words = [word for word in words if word[1] > header_bottom and word[1] < table_end]
    title_chunks = _title_chunks(
        _words_in_interval(data_words, intervals["title"]),
        _words_in_interval(data_words, intervals["status"]),
    )
    if not title_chunks:
        return [], "production_table_required_cells_missing"

    chunk_boxes = [_union_bbox(word for line in chunk for word in line.words) for chunk in title_chunks]
    row_boundaries = [header_bottom]
    row_boundaries.extend(
        (left[3] + right[1]) / 2.0 for left, right in zip(chunk_boxes, chunk_boxes[1:])
    )
    row_boundaries.append(table_end)

    extractable_columns = [header.column for header in headers if header.column == "title" or header.column == "status" or header.column.startswith("author_")]
    rows: list[GeometricProductionRow] = []
    for index, _chunk in enumerate(title_chunks):
        top, bottom = row_boundaries[index], row_boundaries[index + 1]
        row_words = [word for word in data_words if word[1] < bottom and word[3] > top]
        cells: list[GeometricCell] = []
        for column in extractable_columns:
            cell_words = _words_in_interval(row_words, intervals[column])
            if not cell_words:
                continue
            cells.append(
                GeometricCell(
                    text=_reconstruct_cell(cell_words, intervals[column]),
                    page=_page_number(page),
                    column=column,
                    bbox=_union_bbox(cell_words),
                )
            )
        columns = {cell.column for cell in cells if cell.text}
        has_author = any(column.startswith("author_") for column in columns)
        if "title" not in columns or "status" not in columns or not has_author:
            return [], "production_table_required_cells_missing"
        rows.append(GeometricProductionRow(cells=tuple(cells)))

    if not rows:
        return [], "production_table_rows_not_found"
    return rows, None
