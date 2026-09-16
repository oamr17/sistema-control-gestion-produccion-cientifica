from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


VERSION = "20260628_0005_import_job_diagnostics"


def _add_column_if_missing(engine: Engine, table: str, column: str, ddl: str) -> None:
    inspector = inspect(engine)
    columns = {item["name"] for item in inspector.get_columns(table)}
    if column not in columns:
        with engine.begin() as connection:
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))


def upgrade(engine: Engine) -> None:
    _add_column_if_missing(engine, "import_jobs", "current_step", "current_step VARCHAR(80)")
    _add_column_if_missing(engine, "import_jobs", "started_at", "started_at TIMESTAMP")
    _add_column_if_missing(engine, "import_jobs", "finished_at", "finished_at TIMESTAMP")
    _add_column_if_missing(engine, "import_jobs", "duration_ms", "duration_ms INTEGER")
    _add_column_if_missing(engine, "import_jobs", "queue_ms", "queue_ms INTEGER")
    _add_column_if_missing(engine, "import_jobs", "download_ms", "download_ms INTEGER")
    _add_column_if_missing(engine, "import_jobs", "text_extraction_ms", "text_extraction_ms INTEGER")
    _add_column_if_missing(engine, "import_jobs", "ocr_ms", "ocr_ms INTEGER")
    _add_column_if_missing(engine, "import_jobs", "parser_ms", "parser_ms INTEGER")
    _add_column_if_missing(engine, "import_jobs", "persistence_ms", "persistence_ms INTEGER")
    _add_column_if_missing(engine, "import_jobs", "page_count", "page_count INTEGER")
    _add_column_if_missing(engine, "import_jobs", "used_ocr", "used_ocr BOOLEAN DEFAULT FALSE")
    _add_column_if_missing(engine, "import_jobs", "extraction_method", "extraction_method VARCHAR(40)")

    with engine.begin() as connection:
        connection.execute(text("UPDATE import_jobs SET used_ocr = FALSE WHERE used_ocr IS NULL"))
