from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


VERSION = "20260628_0008_normalization_traceability"


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


def _add_common_trace_columns(engine: Engine, connection, table: str) -> set[str]:
    existing = _columns(engine, table)
    _add_column(connection, table, existing, "import_batch_id", "INTEGER NULL REFERENCES import_batches(id)")
    _add_column(connection, table, existing, "import_job_id", "INTEGER NULL REFERENCES import_jobs(id)")
    _add_column(connection, table, existing, "source_file", "VARCHAR(255) NULL")
    _add_column(connection, table, existing, "source_page", "INTEGER NULL")
    _add_column(connection, table, existing, "source_section", "VARCHAR(120) NULL")
    _add_column(connection, table, existing, "raw_value", "TEXT NULL")
    _add_column(connection, table, existing, "normalized_value", "TEXT NULL")
    _add_column(connection, table, existing, "confidence_score", "FLOAT NULL")
    _add_column(connection, table, existing, "parser_version", "VARCHAR(80) NULL")
    _add_column(connection, table, existing, "normalization_action", "VARCHAR(40) NULL")
    _add_column(connection, table, existing, "normalization_reason", "TEXT NULL")
    _create_index(connection, f"ix_{table}_import_batch_id", table, "import_batch_id")
    _create_index(connection, f"ix_{table}_import_job_id", table, "import_job_id")
    _create_index(connection, f"ix_{table}_parser_version", table, "parser_version")
    _create_index(connection, f"ix_{table}_normalization_action", table, "normalization_action")
    return existing


def upgrade(engine: Engine) -> None:
    with engine.begin() as connection:
        teachers = _add_common_trace_columns(engine, connection, "teachers")
        _add_column(connection, "teachers", teachers, "raw_name", "TEXT NULL")
        _add_column(connection, "teachers", teachers, "normalized_name", "VARCHAR(220) NULL")
        _add_column(connection, "teachers", teachers, "raw_faculty", "TEXT NULL")
        _add_column(connection, "teachers", teachers, "normalized_faculty", "VARCHAR(220) NULL")
        _add_column(connection, "teachers", teachers, "raw_career", "TEXT NULL")
        _add_column(connection, "teachers", teachers, "normalized_career", "VARCHAR(220) NULL")
        _create_index(connection, "ix_teachers_normalized_name", "teachers", "normalized_name")

        external = _add_common_trace_columns(engine, connection, "external_researchers")
        _add_column(connection, "external_researchers", external, "raw_name", "TEXT NULL")
        _add_column(connection, "external_researchers", external, "raw_institution", "TEXT NULL")

        productions = _add_common_trace_columns(engine, connection, "scientific_productions")
        _add_column(connection, "scientific_productions", productions, "raw_title", "TEXT NULL")
        _add_column(connection, "scientific_productions", productions, "normalized_title", "VARCHAR(300) NULL")
        _add_column(connection, "scientific_productions", productions, "raw_authors", "TEXT NULL")
        _add_column(connection, "scientific_productions", productions, "normalized_authors", "TEXT NULL")
        _add_column(connection, "scientific_productions", productions, "raw_status", "TEXT NULL")
        _add_column(connection, "scientific_productions", productions, "normalized_status", "VARCHAR(80) NULL")
        _add_column(connection, "scientific_productions", productions, "raw_impact", "TEXT NULL")
        _add_column(connection, "scientific_productions", productions, "normalized_impact", "VARCHAR(180) NULL")
        _create_index(connection, "ix_scientific_productions_normalized_title", "scientific_productions", "normalized_title")

        projects = _add_common_trace_columns(engine, connection, "research_projects")
        _add_column(connection, "research_projects", projects, "raw_project_name", "TEXT NULL")
        _add_column(connection, "research_projects", projects, "normalized_project_name", "VARCHAR(300) NULL")
        _add_column(connection, "research_projects", projects, "raw_code", "TEXT NULL")
        _add_column(connection, "research_projects", projects, "normalized_code", "VARCHAR(120) NULL")
        _add_column(connection, "research_projects", projects, "raw_status", "TEXT NULL")
        _add_column(connection, "research_projects", projects, "normalized_status", "VARCHAR(80) NULL")
        _add_column(connection, "research_projects", projects, "raw_progress", "TEXT NULL")
        _add_column(connection, "research_projects", projects, "normalized_progress", "FLOAT NULL")
        _create_index(connection, "ix_research_projects_normalized_project_name", "research_projects", "normalized_project_name")
        _create_index(connection, "ix_research_projects_normalized_code", "research_projects", "normalized_code")

        if not inspect(engine).has_table("import_normalization_audits"):
            connection.execute(
                text(
                    """
                    CREATE TABLE import_normalization_audits (
                        id SERIAL PRIMARY KEY,
                        import_batch_id INTEGER NULL REFERENCES import_batches(id),
                        import_job_id INTEGER NOT NULL REFERENCES import_jobs(id),
                        normalized_record_id INTEGER NULL,
                        entity_type VARCHAR(80) NOT NULL,
                        source_file VARCHAR(255) NULL,
                        source_page INTEGER NULL,
                        source_section VARCHAR(120) NULL,
                        raw_value TEXT NULL,
                        normalized_value TEXT NULL,
                        confidence_score FLOAT NULL,
                        parser_version VARCHAR(80) NULL,
                        action VARCHAR(40) NOT NULL,
                        reason TEXT NULL,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )
        _create_index(connection, "ix_import_normalization_audits_import_batch_id", "import_normalization_audits", "import_batch_id")
        _create_index(connection, "ix_import_normalization_audits_import_job_id", "import_normalization_audits", "import_job_id")
        _create_index(connection, "ix_import_normalization_audits_entity_type", "import_normalization_audits", "entity_type")
        _create_index(connection, "ix_import_normalization_audits_source_section", "import_normalization_audits", "source_section")
        _create_index(connection, "ix_import_normalization_audits_parser_version", "import_normalization_audits", "parser_version")
        _create_index(connection, "ix_import_normalization_audits_action", "import_normalization_audits", "action")
