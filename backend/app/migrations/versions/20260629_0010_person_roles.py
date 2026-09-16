from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


VERSION = "20260629_0010_person_roles"


def _create_index(connection, name: str, table: str, column: str) -> None:
    connection.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})"))


def upgrade(engine: Engine) -> None:
    with engine.begin() as connection:
        if not inspect(engine).has_table("person_roles"):
            connection.execute(
                text(
                    """
                    CREATE TABLE person_roles (
                        id SERIAL PRIMARY KEY,
                        period_id INTEGER NULL REFERENCES academic_periods(id),
                        import_batch_id INTEGER NULL REFERENCES import_batches(id),
                        import_job_id INTEGER NULL REFERENCES import_jobs(id),
                        teacher_id INTEGER NULL REFERENCES teachers(id),
                        external_researcher_id INTEGER NULL REFERENCES external_researchers(id),
                        scientific_production_id INTEGER NULL REFERENCES scientific_productions(id),
                        research_project_id INTEGER NULL REFERENCES research_projects(id),
                        role_type VARCHAR(50) NOT NULL,
                        person_type VARCHAR(50) NOT NULL,
                        person_key VARCHAR(260) NULL,
                        raw_name TEXT NULL,
                        normalized_name VARCHAR(220) NULL,
                        source_file VARCHAR(255) NULL,
                        source_page INTEGER NULL,
                        source_section VARCHAR(120) NULL,
                        raw_value TEXT NULL,
                        normalized_value TEXT NULL,
                        confidence_score FLOAT NULL,
                        reason TEXT NULL,
                        parser_version VARCHAR(80) NULL,
                        metadata_json JSON NULL,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )

        _create_index(connection, "ix_person_roles_period_id", "person_roles", "period_id")
        _create_index(connection, "ix_person_roles_import_batch_id", "person_roles", "import_batch_id")
        _create_index(connection, "ix_person_roles_import_job_id", "person_roles", "import_job_id")
        _create_index(connection, "ix_person_roles_teacher_id", "person_roles", "teacher_id")
        _create_index(connection, "ix_person_roles_external_researcher_id", "person_roles", "external_researcher_id")
        _create_index(connection, "ix_person_roles_scientific_production_id", "person_roles", "scientific_production_id")
        _create_index(connection, "ix_person_roles_research_project_id", "person_roles", "research_project_id")
        _create_index(connection, "ix_person_roles_role_type", "person_roles", "role_type")
        _create_index(connection, "ix_person_roles_person_type", "person_roles", "person_type")
        _create_index(connection, "ix_person_roles_person_key", "person_roles", "person_key")
        _create_index(connection, "ix_person_roles_normalized_name", "person_roles", "normalized_name")
        _create_index(connection, "ix_person_roles_source_section", "person_roles", "source_section")
        _create_index(connection, "ix_person_roles_parser_version", "person_roles", "parser_version")
