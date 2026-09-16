from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


VERSION = "20260710_0013_teacher_person_role_validation"


def _ensure_status_column(engine: Engine, table: str, default: str) -> None:
    columns = {column["name"] for column in inspect(engine).get_columns(table)}
    with engine.begin() as connection:
        if "validation_status" not in columns:
            connection.execute(
                text(
                    f"ALTER TABLE {table} ADD COLUMN validation_status "
                    f"VARCHAR(40) NOT NULL DEFAULT '{default}'"
                )
            )
        connection.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS ix_{table}_validation_status "
                f"ON {table} (validation_status)"
            )
        )


def upgrade(engine: Engine) -> None:
    _ensure_status_column(engine, "teachers", "validated")
    _ensure_status_column(engine, "person_roles", "pending_review")
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE teachers SET validation_status = 'validated' "
                "WHERE validation_status IS NULL OR validation_status <> 'validated'"
            )
        )
        connection.execute(
            text(
                """
                UPDATE person_roles
                SET validation_status = CASE
                    WHEN person_type = 'teacher'
                         AND teacher_id IS NOT NULL
                         AND COALESCE(confidence_score, 0) >= 0.90
                        THEN 'validated'
                    WHEN person_type = 'external_researcher'
                         AND external_researcher_id IS NOT NULL
                         AND COALESCE(confidence_score, 0) >= 0.90
                        THEN 'validated'
                    ELSE 'pending_review'
                END
                """
            )
        )
