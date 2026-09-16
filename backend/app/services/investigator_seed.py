from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path, PurePosixPath
from typing import Any
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile


DEFAULT_SEED_PATH = Path(__file__).resolve().parents[2] / "data" / "reference" / "base_investigadores_fca.xlsx"

HEADER_MAP = {
    "FACULTAD": "faculty",
    "CEDULA": "identity_number",
    "APELLIDOS Y NOMBRES": "raw_name",
    "GENERO": "gender",
    "NOMBRE DEL PROYECTO": "project_name",
    "CODIGO": "project_code",
    "ANO": "year",
    "ROL": "role",
    "DEDICACION": "dedication",
    "OBSERVACION": "observation",
}

NAME_PARTICLES = {"DE", "DEL", "LA", "LAS", "LOS", "Y"}
FUNCTIONAL_ROLE_HINTS = {
    "director_proyecto": "DIRECTOR",
    "director": "DIRECTOR",
    "coordinador_grupo": "COORDINADOR",
    "coordinador": "COORDINADOR",
    "tutor_semillero": "TUTOR",
    "tutor": "TUTOR",
    "integrante_interno": "INVESTIGADOR",
    "investigador_externo": "INVESTIGADOR",
    "autor_producto": "AUTOR",
}


@dataclass(frozen=True)
class InvestigatorSeedRecord:
    row_number: int
    faculty: str | None
    identity_number: str | None
    raw_name: str
    official_name: str
    normalized_name: str
    inverted_name: str
    project_name: str | None
    project_code: str | None
    year: str | None
    role: str | None
    dedication: str | None
    observation: str | None
    aliases: tuple[str, ...] = field(default_factory=tuple)
    alias_keys: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class InvestigatorSeedMatch:
    record: InvestigatorSeedRecord | None
    match_type: str
    confidence: float
    decision: str
    reason: str
    candidate: str
    matched_alias: str | None = None
    project_code_match: bool = False
    role_match: bool = False

    @property
    def is_identity_validation(self) -> bool:
        return self.decision == "identity_validated"


def normalize_key(value: Any) -> str:
    text = str(value or "").strip().upper()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    return " ".join(text.split())


def normalize_name_key(value: Any) -> str:
    return " ".join(token for token in normalize_key(value).split() if token not in NAME_PARTICLES)


def title_case_name(value: str) -> str:
    return " ".join(part.capitalize() for part in normalize_key(value).lower().split())


def official_name_from_inverted(raw_name: str) -> str:
    tokens = normalize_key(raw_name).split()
    if len(tokens) <= 2:
        return title_case_name(raw_name)
    surname_tokens = tokens[:2]
    given_tokens = tokens[2:]
    return title_case_name(" ".join([*given_tokens, *surname_tokens]))


def generate_name_aliases(raw_name: str) -> tuple[str, ...]:
    tokens = normalize_key(raw_name).split()
    if len(tokens) <= 2:
        return (title_case_name(raw_name),)

    surname_tokens = tokens[:2]
    given_tokens = tokens[2:]
    aliases: list[str] = []

    def add(parts: list[str]) -> None:
        value = title_case_name(" ".join(parts))
        if value and normalize_name_key(value) not in {normalize_name_key(alias) for alias in aliases}:
            aliases.append(value)

    add([*given_tokens, *surname_tokens])
    add([given_tokens[0], *surname_tokens])
    add([*given_tokens, surname_tokens[0]])
    add([given_tokens[0], surname_tokens[0]])
    add([surname_tokens[0], surname_tokens[1], *given_tokens])
    add([surname_tokens[0], *given_tokens])
    if len(given_tokens) >= 2:
        add([given_tokens[0], given_tokens[1], surname_tokens[0]])
        add([given_tokens[0], given_tokens[1], *surname_tokens])
    return tuple(aliases)


class InvestigatorSeedService:
    def __init__(self, path: str | Path | None = None):
        self._uses_default_reference = path is None
        self.path = DEFAULT_SEED_PATH if self._uses_default_reference else Path(path)
        self._records: list[InvestigatorSeedRecord] | None = None

    def load_records(self) -> list[InvestigatorSeedRecord]:
        if self._records is None:
            try:
                self._records = self._load_records()
            except FileNotFoundError as exc:
                if self._uses_default_reference:
                    self._records = []
                else:
                    raise FileNotFoundError(
                        f"explicit investigator reference could not be loaded: {self.path}"
                    ) from exc
            except (zipfile.BadZipFile, ET.ParseError, KeyError, ValueError) as exc:
                source = "default" if self._uses_default_reference else "explicit"
                raise ValueError(
                    f"{source} investigator reference is corrupt or invalid: {self.path}"
                ) from exc
        return self._records

    def stats(self) -> dict[str, int]:
        records = self.load_records()
        unique_keys = {
            record.identity_number or record.normalized_name
            for record in records
            if record.identity_number or record.normalized_name
        }
        return {
            "records": len(records),
            "unique_investigators": len(unique_keys),
            "with_identity_number": sum(1 for record in records if record.identity_number),
        }

    def match_name(
        self,
        candidate: Any,
        *,
        identity_number: Any = None,
        project_codes: list[Any] | None = None,
        role_keys: list[Any] | None = None,
    ) -> InvestigatorSeedMatch:
        candidate_text = str(candidate or "").strip()
        candidate_key = normalize_name_key(candidate_text)
        candidate_identity = _identity_key(identity_number)
        project_key_set = {_project_code_key(code) for code in project_codes or [] if _project_code_key(code)}
        role_key_set = {normalize_key(role) for role in role_keys or [] if normalize_key(role)}

        if not candidate_key and not candidate_identity:
            return InvestigatorSeedMatch(
                record=None,
                match_type="no_candidate",
                confidence=0.0,
                decision="no_match",
                reason="No candidate name or identity number was provided.",
                candidate=candidate_text,
            )

        best: InvestigatorSeedMatch | None = None
        for match in self._all_matches(
            candidate_text=candidate_text,
            candidate_key=candidate_key,
            candidate_identity=candidate_identity,
            project_key_set=project_key_set,
            role_key_set=role_key_set,
        ):
            if best is None or match.confidence > best.confidence:
                best = match

        return best or InvestigatorSeedMatch(
            record=None,
            match_type="no_match",
            confidence=0.0,
            decision="no_match",
            reason="No seed record matched the candidate.",
            candidate=candidate_text,
        )

    def match_candidates(
        self,
        candidate: Any,
        *,
        identity_number: Any = None,
        project_codes: list[Any] | None = None,
        role_keys: list[Any] | None = None,
    ) -> tuple[InvestigatorSeedMatch, ...]:
        candidate_text = str(candidate or "").strip()
        candidate_key = normalize_name_key(candidate_text)
        candidate_identity = _identity_key(identity_number)
        if not candidate_key and not candidate_identity:
            return ()
        matches = self._all_matches(
            candidate_text=candidate_text,
            candidate_key=candidate_key,
            candidate_identity=candidate_identity,
            project_key_set={_project_code_key(code) for code in project_codes or [] if _project_code_key(code)},
            role_key_set={normalize_key(role) for role in role_keys or [] if normalize_key(role)},
        )
        compatible = [match for match in matches if match.decision != "no_match"]
        unique: dict[str, InvestigatorSeedMatch] = {}
        for match in sorted(compatible, key=lambda item: item.confidence, reverse=True):
            assert match.record is not None
            identity_key = match.record.identity_number or match.record.normalized_name
            unique.setdefault(identity_key, match)
        return tuple(unique.values())

    def _all_matches(
        self,
        *,
        candidate_text: str,
        candidate_key: str,
        candidate_identity: str | None,
        project_key_set: set[str],
        role_key_set: set[str],
    ) -> list[InvestigatorSeedMatch]:
        return [
            self._score_record(
                record=record,
                candidate_text=candidate_text,
                candidate_key=candidate_key,
                candidate_identity=candidate_identity,
                project_key_set=project_key_set,
                role_key_set=role_key_set,
            )
            for record in self.load_records()
        ]

    def _score_record(
        self,
        *,
        record: InvestigatorSeedRecord,
        candidate_text: str,
        candidate_key: str,
        candidate_identity: str | None,
        project_key_set: set[str],
        role_key_set: set[str],
    ) -> InvestigatorSeedMatch:
        project_code_match = bool(record.project_code and _project_code_key(record.project_code) in project_key_set)
        role_match = _role_matches(record.role, role_key_set)

        if candidate_identity and record.identity_number and candidate_identity == record.identity_number:
            return InvestigatorSeedMatch(
                record=record,
                match_type="identity_number",
                confidence=1.0,
                decision="identity_validated",
                reason="Cedula exacta encontrada en la base semilla.",
                candidate=candidate_text,
                project_code_match=project_code_match,
                role_match=role_match,
            )

        for alias, alias_key in zip(record.aliases, record.alias_keys, strict=False):
            if candidate_key and candidate_key == alias_key:
                match_type = "exact_normalized_name"
                if alias_key == normalize_name_key(record.inverted_name):
                    match_type = "inverted_name"
                return InvestigatorSeedMatch(
                    record=record,
                    match_type=match_type,
                    confidence=0.98,
                    decision="identity_validated",
                    reason="Nombre normalizado coincide con una variante oficial de la base semilla.",
                    candidate=candidate_text,
                    matched_alias=alias,
                    project_code_match=project_code_match,
                    role_match=role_match,
                )

        candidate_tokens = candidate_key.split()
        strict_prefix_alias = next(
            (
                alias
                for alias, alias_key in zip(record.aliases, record.alias_keys, strict=False)
                if len(candidate_tokens) >= 2 and alias_key != candidate_key and alias_key.startswith(candidate_key)
            ),
            None,
        )
        if strict_prefix_alias:
            return InvestigatorSeedMatch(
                record=record,
                match_type="strict_name_prefix",
                confidence=0.97,
                decision="identity_validated",
                reason="El candidato es prefijo normalizado estricto de una variante oficial de la base semilla.",
                candidate=candidate_text,
                matched_alias=strict_prefix_alias,
                project_code_match=project_code_match,
                role_match=role_match,
            )

        candidate_token_set = set(candidate_tokens)
        subset_alias = next(
            (
                alias
                for alias, alias_key in zip(record.aliases, record.alias_keys, strict=False)
                if len(candidate_tokens) >= 2
                and candidate_token_set < set(alias_key.split())
            ),
            None,
        )
        if subset_alias:
            return InvestigatorSeedMatch(
                record=record,
                match_type="name_token_subset",
                confidence=0.96,
                decision="identity_validated",
                reason="Todos los tokens del candidato forman un subconjunto estricto de una variante oficial.",
                candidate=candidate_text,
                matched_alias=subset_alias,
                project_code_match=project_code_match,
                role_match=role_match,
            )

        record_tokens = record.normalized_name.split()
        token_score = _token_alias_score(candidate_tokens, record_tokens)
        sequence_score = SequenceMatcher(None, candidate_key, record.normalized_name).ratio() if candidate_key else 0.0
        confidence = max(token_score, sequence_score)

        if len(candidate_tokens) >= 2 and token_score >= 0.98:
            return InvestigatorSeedMatch(
                record=record,
                match_type="strong_name_similarity",
                confidence=0.93,
                decision="identity_validated",
                reason="Todos los tokens detectados coinciden con el nombre oficial o sus variantes OCR.",
                candidate=candidate_text,
                project_code_match=project_code_match,
                role_match=role_match,
            )

        if project_code_match and role_match and token_score >= 0.50:
            return InvestigatorSeedMatch(
                record=record,
                match_type="project_role_name_context",
                confidence=max(0.86, confidence),
                decision="identity_suggested",
                reason="Codigo de proyecto y rol coinciden; el nombre detectado es parcial y requiere revision.",
                candidate=candidate_text,
                project_code_match=project_code_match,
                role_match=role_match,
            )

        if project_code_match and token_score >= 0.50:
            return InvestigatorSeedMatch(
                record=record,
                match_type="project_name_context",
                confidence=max(0.82, confidence),
                decision="identity_suggested",
                reason="Codigo de proyecto coincide; el nombre detectado es parcial o truncado.",
                candidate=candidate_text,
                project_code_match=True,
                role_match=role_match,
            )

        if len(candidate_tokens) >= 2 and confidence >= 0.72:
            return InvestigatorSeedMatch(
                record=record,
                match_type="weak_name_similarity",
                confidence=round(confidence, 3),
                decision="identity_suggested",
                reason="Similitud nominal parcial; no valida identidad sin evidencia adicional.",
                candidate=candidate_text,
                project_code_match=project_code_match,
                role_match=role_match,
            )

        if len(candidate_tokens) == 1 and token_score >= 0.99:
            return InvestigatorSeedMatch(
                record=record,
                match_type="single_token_name",
                confidence=0.66,
                decision="identity_suggested",
                reason="Solo coincide un apellido o token; se mantiene como sugerencia.",
                candidate=candidate_text,
                project_code_match=project_code_match,
                role_match=role_match,
            )

        return InvestigatorSeedMatch(
            record=record,
            match_type="no_match",
            confidence=round(confidence, 3),
            decision="no_match",
            reason="La similitud no alcanza el umbral de sugerencia.",
            candidate=candidate_text,
            project_code_match=project_code_match,
            role_match=role_match,
        )

    def _load_records(self) -> list[InvestigatorSeedRecord]:
        rows = _read_xlsx_sheet(self.path, "base")
        if not rows:
            return []
        headers = [_header_key(value) for value in rows[0]]
        column_index = {HEADER_MAP[header]: index for index, header in enumerate(headers) if header in HEADER_MAP}
        records: list[InvestigatorSeedRecord] = []
        for row_number, row in enumerate(rows[1:], start=2):
            raw_name = _cell(row, column_index.get("raw_name"))
            if not raw_name:
                continue
            identity_number = _identity_key(_cell(row, column_index.get("identity_number")))
            official_name = official_name_from_inverted(raw_name)
            aliases = generate_name_aliases(raw_name)
            alias_values = tuple(dict.fromkeys([official_name, title_case_name(raw_name), *aliases]))
            records.append(
                InvestigatorSeedRecord(
                    row_number=row_number,
                    faculty=_cell(row, column_index.get("faculty")),
                    identity_number=identity_number,
                    raw_name=raw_name,
                    official_name=official_name,
                    normalized_name=normalize_name_key(official_name),
                    inverted_name=title_case_name(raw_name),
                    project_name=_cell(row, column_index.get("project_name")),
                    project_code=_cell(row, column_index.get("project_code")),
                    year=_cell(row, column_index.get("year")),
                    role=_cell(row, column_index.get("role")),
                    dedication=_cell(row, column_index.get("dedication")),
                    observation=_cell(row, column_index.get("observation")),
                    aliases=alias_values,
                    alias_keys=tuple(normalize_name_key(alias) for alias in alias_values),
                )
            )
        return records


def _read_xlsx_sheet(path: Path, sheet_name: str) -> list[list[str | None]]:
    with zipfile.ZipFile(path) as zf:
        shared_strings = _shared_strings(zf)
        sheet_path = _sheet_path(zf, sheet_name)
        root = ET.fromstring(zf.read(sheet_path))

    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows: list[list[str | None]] = []
    for row in root.findall(".//a:sheetData/a:row", ns):
        values_by_column: dict[int, str | None] = {}
        for cell in row.findall("a:c", ns):
            ref = cell.attrib.get("r", "")
            column_index = _column_index(ref)
            values_by_column[column_index] = _cell_value(cell, shared_strings, ns)
        if values_by_column:
            max_column = max(values_by_column)
            rows.append([values_by_column.get(index) for index in range(max_column + 1)])
    return rows


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    values: list[str] = []
    for item in root.findall("a:si", ns):
        texts = [text.text or "" for text in item.findall(".//a:t", ns)]
        values.append("".join(texts).strip())
    return values


def _sheet_path(zf: zipfile.ZipFile, sheet_name: str) -> str:
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    main_ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    package_rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    rel_targets = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in rels.findall(f"{{{package_rel_ns}}}Relationship")
    }
    for sheet in workbook.findall(f".//{{{main_ns}}}sheet"):
        if normalize_key(sheet.attrib.get("name")) != normalize_key(sheet_name):
            continue
        rel_id = sheet.attrib.get(f"{{{rel_ns}}}id")
        target = rel_targets.get(str(rel_id))
        if not target:
            break
        if target.startswith("/"):
            return target.lstrip("/")
        return str(PurePosixPath("xl") / target)
    raise ValueError(f"Sheet not found: {sheet_name}")


def _cell_value(cell: ET.Element, shared_strings: list[str], ns: dict[str, str]) -> str | None:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        texts = [text.text or "" for text in cell.findall(".//a:t", ns)]
        return _clean_cell("".join(texts))
    value = cell.find("a:v", ns)
    if value is None or value.text is None:
        return None
    raw = value.text
    if cell_type == "s":
        try:
            return _clean_cell(shared_strings[int(raw)])
        except (IndexError, ValueError):
            return None
    return _clean_cell(raw)


def _column_index(cell_ref: str) -> int:
    letters = "".join(char for char in cell_ref if char.isalpha())
    index = 0
    for char in letters:
        index = index * 26 + (ord(char.upper()) - ord("A") + 1)
    return max(index - 1, 0)


def _clean_cell(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _cell(row: list[str | None], index: int | None) -> str | None:
    if index is None or index >= len(row):
        return None
    return _clean_cell(row[index])


def _header_key(value: Any) -> str:
    return normalize_key(value).replace("CODIGO", "CODIGO").replace("ANO", "ANO")


def _identity_key(value: Any) -> str | None:
    digits = re.sub(r"\D+", "", str(value or ""))
    return digits or None


def _project_code_key(value: Any) -> str:
    return normalize_key(value).replace(" ", "")


def _role_matches(seed_role: Any, role_keys: set[str]) -> bool:
    seed_key = normalize_key(seed_role)
    if not seed_key or not role_keys:
        return False
    for role in role_keys:
        hint = FUNCTIONAL_ROLE_HINTS.get(role.lower()) or FUNCTIONAL_ROLE_HINTS.get(role)
        if hint and hint in seed_key:
            return True
        if role and role in seed_key:
            return True
    return False


def _token_alias_score(candidate_tokens: list[str], record_tokens: list[str]) -> float:
    candidate = [token for token in candidate_tokens if len(token) > 1]
    record = [token for token in record_tokens if len(token) > 1]
    if not candidate or not record:
        return 0.0
    used: set[int] = set()
    matches = 0
    for token in candidate:
        best_index: int | None = None
        best_score = 0.0
        for index, record_token in enumerate(record):
            if index in used:
                continue
            score = _single_token_score(token, record_token)
            if score > best_score:
                best_index = index
                best_score = score
        if best_index is not None and best_score >= 0.84:
            used.add(best_index)
            matches += 1
    return matches / len(candidate)


def _single_token_score(left: str, right: str) -> float:
    if left == right:
        return 1.0
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    if len(shorter) >= 4 and longer.startswith(shorter):
        return 0.96
    if len(shorter) >= 3 and longer.endswith(shorter):
        return 0.9
    return SequenceMatcher(None, left, right).ratio()
