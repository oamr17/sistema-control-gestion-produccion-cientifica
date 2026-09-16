from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


VERSION = "20260628_0007_external_researchers_and_review_items"


def _table_exists(engine: Engine, table: str) -> bool:
    return inspect(engine).has_table(table)


def upgrade(engine: Engine) -> None:
    with engine.begin() as connection:
        if not _table_exists(engine, "external_researchers"):
            connection.execute(
                text(
                    """
                    CREATE TABLE external_researchers (
                        id SERIAL PRIMARY KEY,
                        period_id INTEGER NOT NULL REFERENCES academic_periods(id),
                        import_job_id INTEGER NULL REFERENCES import_jobs(id),
                        full_name VARCHAR(180) NOT NULL,
                        normalized_name VARCHAR(220) NOT NULL,
                        institution VARCHAR(220) NOT NULL,
                        normalized_institution VARCHAR(260) NOT NULL,
                        source_section VARCHAR(80) NOT NULL DEFAULT 'integrantes_externos',
                        confidence_score FLOAT NULL,
                        requires_review BOOLEAN NOT NULL DEFAULT FALSE,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        CONSTRAINT uq_external_researcher_period UNIQUE (period_id, normalized_name, normalized_institution)
                    )
                    """
                )
            )
            connection.execute(text("CREATE INDEX ix_external_researchers_period_id ON external_researchers(period_id)"))
            connection.execute(text("CREATE INDEX ix_external_researchers_import_job_id ON external_researchers(import_job_id)"))
            connection.execute(text("CREATE INDEX ix_external_researchers_normalized_institution ON external_researchers(normalized_institution)"))

        if not _table_exists(engine, "import_review_items"):
            connection.execute(
                text(
                    """
                    CREATE TABLE import_review_items (
                        id SERIAL PRIMARY KEY,
                        import_job_id INTEGER NOT NULL REFERENCES import_jobs(id),
                        trace_id INTEGER NULL REFERENCES imported_ocr_traces(id),
                        field VARCHAR(120) NOT NULL,
                        source_page INTEGER NULL,
                        source_section VARCHAR(120) NULL,
                        raw_value TEXT NULL,
                        normalized_value TEXT NULL,
                        confidence_score FLOAT NULL,
                        reason TEXT NOT NULL,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )
            connection.execute(text("CREATE INDEX ix_import_review_items_import_job_id ON import_review_items(import_job_id)"))
            connection.execute(text("CREATE INDEX ix_import_review_items_trace_id ON import_review_items(trace_id)"))
            connection.execute(text("CREATE INDEX ix_import_review_items_field ON import_review_items(field)"))
