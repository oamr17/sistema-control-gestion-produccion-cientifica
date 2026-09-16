from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


VERSION = "20260628_0006_import_job_error_details"


def _add_column_if_missing(engine: Engine, table: str, column: str, ddl: str) -> None:
    inspector = inspect(engine)
    columns = {item["name"] for item in inspector.get_columns(table)}
    if column not in columns:
        with engine.begin() as connection:
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))


def upgrade(engine: Engine) -> None:
    _add_column_if_missing(engine, "import_jobs", "error_type", "error_type VARCHAR(40)")
    _add_column_if_missing(engine, "import_jobs", "error_message", "error_message TEXT")
    _add_column_if_missing(engine, "import_jobs", "error_traceback", "error_traceback TEXT")

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE import_jobs
                SET
                    error_type = COALESCE(error_type, 'system_error'),
                    error_message = COALESCE(error_message, error_reason)
                WHERE status = 'ERROR'
                  AND error_reason IS NOT NULL
                """
            )
        )
