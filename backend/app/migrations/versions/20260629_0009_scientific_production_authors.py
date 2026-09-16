from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


VERSION = "20260629_0009_scientific_production_authors"


def _columns(engine: Engine, table: str) -> set[str]:
    if not inspect(engine).has_table(table):
        return set()
    return {column["name"] for column in inspect(engine).get_columns(table)}


def _add_column(connection, table: str, existing: set[str], name: str, definition: str) -> None:
    if name in existing:
        return
    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
    existing.add(name)


def _create_index(connection, name: str, table: str, column: str) -> None:
    connection.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {table}({column})"))


def upgrade(engine: Engine) -> None:
    with engine.begin() as connection:
        if not inspect(engine).has_table("scientific_production_authors"):
            connection.execute(
                text(
                    """
                    CREATE TABLE scientific_production_authors (
                        id SERIAL PRIMARY KEY,
                        production_id INTEGER NOT NULL REFERENCES scientific_productions(id) ON DELETE CASCADE,
                        author_order INTEGER NOT NULL,
                        raw_author_name TEXT NULL,
                        normalized_author_name VARCHAR(220) NULL,
                        teacher_id INTEGER NULL REFERENCES teachers(id),
                        external_researcher_id INTEGER NULL REFERENCES external_researchers(id),
                        author_type VARCHAR(30) NOT NULL,
                        confidence_score FLOAT NULL,
                        reason TEXT NULL,
                        source_file VARCHAR(255) NULL,
                        source_page INTEGER NULL,
                        source_section VARCHAR(120) NULL,
                        import_batch_id INTEGER NULL REFERENCES import_batches(id),
                        import_job_id INTEGER NULL REFERENCES import_jobs(id),
                        parser_version VARCHAR(80) NULL,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        CONSTRAINT uq_scientific_production_author_order UNIQUE (production_id, author_order)
                    )
                    """
                )
            )
        _create_index(connection, "ix_scientific_production_authors_production_id", "scientific_production_authors", "production_id")
        _create_index(connection, "ix_scientific_production_authors_normalized_author_name", "scientific_production_authors", "normalized_author_name")
        _create_index(connection, "ix_scientific_production_authors_teacher_id", "scientific_production_authors", "teacher_id")
        _create_index(connection, "ix_scientific_production_authors_external_researcher_id", "scientific_production_authors", "external_researcher_id")
        _create_index(connection, "ix_scientific_production_authors_author_type", "scientific_production_authors", "author_type")
        _create_index(connection, "ix_scientific_production_authors_import_batch_id", "scientific_production_authors", "import_batch_id")
        _create_index(connection, "ix_scientific_production_authors_import_job_id", "scientific_production_authors", "import_job_id")
        _create_index(connection, "ix_scientific_production_authors_parser_version", "scientific_production_authors", "parser_version")

        audit_columns = _columns(engine, "import_normalization_audits")
        _add_column(connection, "import_normalization_audits", audit_columns, "metadata_json", "JSON NULL")
