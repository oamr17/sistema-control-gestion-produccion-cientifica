from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


VERSION = "20260629_0011_research_entities_and_pending_products"


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
    connection.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})"))


def _drop_not_null(connection, table: str, column: str) -> None:
    connection.execute(text(f"ALTER TABLE {table} ALTER COLUMN {column} DROP NOT NULL"))


def upgrade(engine: Engine) -> None:
    with engine.begin() as connection:
        if not inspect(engine).has_table("research_entities"):
            connection.execute(
                text(
                    """
                    CREATE TABLE research_entities (
                        id SERIAL PRIMARY KEY,
                        period_id INTEGER NULL REFERENCES academic_periods(id),
                        type VARCHAR(50) NOT NULL,
                        code VARCHAR(120) NULL,
                        normalized_code VARCHAR(140) NULL,
                        name VARCHAR(350) NULL,
                        normalized_name VARCHAR(380) NULL,
                        director_name VARCHAR(220) NULL,
                        normalized_director_name VARCHAR(260) NULL,
                        year INTEGER NULL,
                        cycle INTEGER NULL,
                        academic_unit VARCHAR(220) NULL,
                        career_name VARCHAR(220) NULL,
                        progress_percentage FLOAT NULL,
                        status VARCHAR(80) NULL,
                        validation_status VARCHAR(40) NOT NULL DEFAULT 'pending_review',
                        import_batch_id INTEGER NULL REFERENCES import_batches(id),
                        import_job_id INTEGER NULL REFERENCES import_jobs(id),
                        source_file VARCHAR(255) NULL,
                        source_page INTEGER NULL,
                        source_section VARCHAR(120) NULL,
                        raw_value TEXT NULL,
                        normalized_value TEXT NULL,
                        confidence_score FLOAT NULL,
                        parser_version VARCHAR(80) NULL,
                        reason TEXT NULL,
                        metadata_json JSON NULL,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )

        _create_index(connection, "ix_research_entities_period_id", "research_entities", "period_id")
        _create_index(connection, "ix_research_entities_type", "research_entities", "type")
        _create_index(connection, "ix_research_entities_normalized_code", "research_entities", "normalized_code")
        _create_index(connection, "ix_research_entities_normalized_name", "research_entities", "normalized_name")
        _create_index(connection, "ix_research_entities_validation_status", "research_entities", "validation_status")
        _create_index(connection, "ix_research_entities_import_batch_id", "research_entities", "import_batch_id")
        _create_index(connection, "ix_research_entities_import_job_id", "research_entities", "import_job_id")

        production_columns = _columns(engine, "scientific_productions")
        _drop_not_null(connection, "scientific_productions", "teacher_id")
        _add_column(
            connection,
            "scientific_productions",
            production_columns,
            "research_entity_id",
            "INTEGER NULL REFERENCES research_entities(id)",
        )
        _add_column(
            connection,
            "scientific_productions",
            production_columns,
            "validation_status",
            "VARCHAR(40) NOT NULL DEFAULT 'validated'",
        )
        _add_column(connection, "scientific_productions", production_columns, "review_reason", "TEXT NULL")
        _create_index(connection, "ix_scientific_productions_research_entity_id", "scientific_productions", "research_entity_id")
        _create_index(connection, "ix_scientific_productions_validation_status", "scientific_productions", "validation_status")

        author_columns = _columns(engine, "scientific_production_authors")
        _add_column(
            connection,
            "scientific_production_authors",
            author_columns,
            "research_entity_id",
            "INTEGER NULL REFERENCES research_entities(id)",
        )
        _create_index(
            connection,
            "ix_scientific_production_authors_research_entity_id",
            "scientific_production_authors",
            "research_entity_id",
        )

        role_columns = _columns(engine, "person_roles")
        _add_column(
            connection,
            "person_roles",
            role_columns,
            "research_entity_id",
            "INTEGER NULL REFERENCES research_entities(id)",
        )
        _create_index(connection, "ix_person_roles_research_entity_id", "person_roles", "research_entity_id")
