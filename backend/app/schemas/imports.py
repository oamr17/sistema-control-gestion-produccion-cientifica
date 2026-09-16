from datetime import datetime
from pathlib import PurePosixPath
import re
import unicodedata
from typing import Any

from pydantic import BaseModel, Field, field_serializer


def _public_pdf_filename(value: str | None) -> str:
    basename = PurePosixPath((value or "").replace("\\", "/")).name
    normalized = unicodedata.normalize("NFKD", basename).encode(
        "ascii", "ignore"
    ).decode("ascii")
    sanitized = re.sub(r"[^A-Za-z0-9._ -]+", "_", normalized)
    sanitized = re.sub(r"\s+", " ", sanitized).strip(" ._-")
    if not sanitized or sanitized in {".", ".."}:
        return "documento.pdf"
    stem = sanitized[:-4] if sanitized.casefold().endswith(".pdf") else sanitized
    stem = stem[:251].rstrip(" ._-")
    return f"{stem}.pdf" if stem else "documento.pdf"


def _public_import_error(error_type: str | None) -> str:
    messages = {
        "validation_error": "El archivo no cumple los requisitos de importación.",
        "download_error": "No se pudo descargar el archivo de origen.",
        "parser_error": "No se pudo procesar el contenido del PDF.",
        "persistence_error": "No se pudo guardar el resultado de la importación.",
    }
    return messages.get(error_type or "", "No se pudo completar la importación.")


_PRIVATE_IMPORT_KEYS = {
    "accesstoken",
    "bucket",
    "bucketname",
    "connectionstring",
    "credentials",
    "databaseurl",
    "documentkey",
    "dropboxmetadata",
    "dropboxpath",
    "dsn",
    "endpoint",
    "errortraceback",
    "filesystempath",
    "internalpath",
    "minioendpoint",
    "objectkey",
    "objectpath",
    "password",
    "pathdisplay",
    "pathlower",
    "refreshtoken",
    "secret",
    "sourceidentifier",
    "sourcepath",
    "storagelocator",
    "token",
    "traceback",
}
_PRIVATE_IMPORT_VALUE = re.compile(
    r"traceback \(most recent call last\)|(?:postgres(?:ql)?|mysql|mariadb|mssql|oracle|redis)://|"
    r"(?:s3|minio)://|https?://(?:localhost|127\.0\.0\.1|minio)(?=[:/])|"
    r"(?:^|\s)[A-Za-z]:[\\/]|(?:^|\s)\\\\[^\\]+\\|(?:^|\s)/(?:app|private|secret|var|srv)/|"
    r"(?:access[_-]?token|bearer|jwt|token)\s*[=:]",
    re.IGNORECASE,
)
_PUBLIC_ERROR_KEYS = {"error", "errormessage", "errorreason"}
_PUBLIC_FILENAME_KEYS = {"filename", "pdf", "sourcefile", "sourcefilename"}


def _normalized_public_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _is_private_import_key(value: str) -> bool:
    return value in _PRIVATE_IMPORT_KEYS or value.endswith(
        ("connectionstring", "databaseurl", "password", "path", "secret", "token")
    )


def sanitize_public_import_payload(value: Any) -> Any:
    if isinstance(value, list):
        return [sanitize_public_import_payload(item) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            normalized_key = _normalized_public_key(key)
            if _is_private_import_key(normalized_key):
                result[key] = None
            elif normalized_key in _PUBLIC_ERROR_KEYS and item:
                result[key] = _public_import_error(None)
            elif normalized_key in _PUBLIC_FILENAME_KEYS and isinstance(item, str):
                result[key] = _public_pdf_filename(item)
            else:
                result[key] = sanitize_public_import_payload(item)
        return result
    if isinstance(value, str) and _PRIVATE_IMPORT_VALUE.search(value):
        return "Información técnica disponible únicamente en los registros del servidor."
    return value


class ImportJobRead(BaseModel):
    id: int
    batch_id: int | None = None
    source_type: str
    filename: str
    status: str
    imported_by: str | None = None
    summary: str | None = None
    source_identifier: str | None = None
    source_rev: str | None = None
    document_key: str | None = None
    is_current: bool = False
    supersedes_id: int | None = None
    error_reason: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    error_traceback: str | None = None
    retry_count: int = 0
    max_retries: int = 0
    current_step: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    queue_ms: int | None = None
    download_ms: int | None = None
    text_extraction_ms: int | None = None
    ocr_ms: int | None = None
    parser_ms: int | None = None
    persistence_ms: int | None = None
    page_count: int | None = None
    used_ocr: bool = False
    extraction_method: str | None = None
    created_at: datetime
    processed_at: datetime | None = None

    model_config = {"from_attributes": True}

    @field_serializer("filename")
    def serialize_filename(self, value: str) -> str:
        return _public_pdf_filename(value)

    @field_serializer("source_identifier", "document_key")
    def serialize_storage_locator(self, _value: str | None) -> None:
        return None

    @field_serializer("error_traceback")
    def serialize_traceback(self, _value: str | None) -> None:
        return None

    @field_serializer("summary", "error_reason", "error_message")
    def serialize_error_text(self, value: str | None) -> str | None:
        if value and (self.status == "ERROR" or _PRIVATE_IMPORT_VALUE.search(value)):
            return _public_import_error(self.error_type)
        return value


class ImportBatchRead(BaseModel):
    id: int
    source_type: str
    status: str
    total_files: int
    created_by: str | None = None
    summary: str | None = None
    created_at: datetime
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


class ImportResult(BaseModel):
    job_id: int
    batch_id: int | None = None
    source_type: str
    filename: str
    status: str
    imported_rows: int
    summary: str

    @field_serializer("filename")
    def serialize_filename(self, value: str) -> str:
        return _public_pdf_filename(value)

    @field_serializer("summary")
    def serialize_summary(self, value: str) -> str:
        if self.status == "ERROR" or _PRIVATE_IMPORT_VALUE.search(value):
            return _public_import_error(None)
        return value


class ImportBatchAcceptedRead(BaseModel):
    ok: bool
    batch_id: int | None = None
    total: int = 0
    queued: int = 0
    skipped: int = 0
    message: str


class ImportJobDiagnosticsRead(BaseModel):
    id: int
    filename: str
    status: str
    queue_time: int | None = None
    queue_ms: int | None = None
    duration: int | None = None
    duration_ms: int | None = None
    retry_count: int = 0
    current_step: str | None = None
    error: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    traceback: str | None = None
    used_ocr: bool = False
    page_count: int | None = None
    extraction_method: str | None = None
    extraction_counts: dict[str, int]
    persisted_counts: dict[str, int]

    @field_serializer("filename")
    def serialize_filename(self, value: str) -> str:
        return _public_pdf_filename(value)

    @field_serializer("traceback")
    def serialize_traceback(self, _value: str | None) -> None:
        return None

    @field_serializer("error", "error_message")
    def serialize_error_text(self, value: str | None) -> str | None:
        return _public_import_error(self.error_type) if value else None


class ImportBatchDiagnosticsRead(BaseModel):
    batch_id: int
    total: int
    jobs: list[ImportJobDiagnosticsRead]


class ImportStatusRead(BaseModel):
    active: bool
    batch_id: int | None = None
    total: int
    queued: int = 0
    processing: int = 0
    processed: int
    failed: int
    ignored: int
    requires_review: int = 0


class ProgressImportRecord(BaseModel):
    career_name: str
    year_label: str
    cycle: int = Field(ge=1, le=2)
    teacher_name: str
    teacher_identifier: str | None = None
    articles: int = 0
    books: int = 0
    book_chapters: int = 0
    presentations: int = 0
    projects: int = 0
    notes: str | None = None


class ImportedProgressReportRead(BaseModel):
    id: int
    import_job_id: int
    career_name: str | None = None
    year_label: str
    cycle: int
    teacher_identifier: str | None = None
    teacher_name: str
    articles: int
    books: int
    book_chapters: int
    presentations: int
    unclassified_products: int = 0
    projects: int
    notes: str | None = None
    research_topic: str | None = None
    source_filename: str | None = None
    source_path: str | None = None
    has_source_file: bool = False
    group_projects: list[dict] = []
    research_entities: list[dict] = []
    group_members: list[dict] = []
    external_researchers: list[dict] = []
    scientific_products: list[dict] = []
    project_directors: list[str] = []
    associated_teachers: list[str] = []
    normalized_participants: list[dict] = []
    participants_summary: dict = {}
    person_aliases: list[dict] = []
    possible_merge_review: list[dict] = []

    model_config = {"from_attributes": True}

    @field_serializer("source_filename")
    def serialize_source_filename(self, value: str | None) -> str | None:
        return _public_pdf_filename(value) if value else None

    @field_serializer("source_path")
    def serialize_source_path(self, _value: str | None) -> None:
        return None

    @field_serializer(
        "group_projects",
        "research_entities",
        "group_members",
        "external_researchers",
        "scientific_products",
        "normalized_participants",
        "participants_summary",
        "person_aliases",
        "possible_merge_review",
    )
    def serialize_public_payload(self, value):
        return sanitize_public_import_payload(value)


class ProgressJsonImportRequest(BaseModel):
    source_filename: str
    source_path: str | None = None
    ocr_provider: str | None = None
    extracted_text: str | None = None
    confidence_score: float | None = Field(default=None, ge=0, le=1)
    records: list[ProgressImportRecord]


class DropboxProgressPdfItem(BaseModel):
    id: str | None = None
    rev: str | None = None
    path_lower: str
    name: str | None = None
    path_display: str | None = None
    client_modified: str | None = None
    server_modified: str | None = None
    size: int | None = None
    content_hash: str | None = None

    model_config = {"extra": "ignore"}


class DropboxProgressPdfBatchRequest(BaseModel):
    files: list[DropboxProgressPdfItem]


class OcrTraceRead(BaseModel):
    id: int
    import_job_id: int
    progress_report_id: int | None = None
    source_filename: str
    source_path: str | None = None
    ocr_provider: str | None = None
    extracted_text: str | None = None
    parsed_payload: dict | None = None
    confidence_score: float | None = None
    review_status: str
    reviewed_by: str | None = None
    review_notes: str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("source_filename")
    def serialize_source_filename(self, value: str) -> str:
        return _public_pdf_filename(value)

    @field_serializer("source_path")
    def serialize_source_path(self, _value: str | None) -> None:
        return None

    @field_serializer("parsed_payload")
    def serialize_parsed_payload(self, value: dict | None) -> dict | None:
        return sanitize_public_import_payload(value)


class OcrTraceReviewUpdate(BaseModel):
    review_status: str
    review_notes: str | None = None
