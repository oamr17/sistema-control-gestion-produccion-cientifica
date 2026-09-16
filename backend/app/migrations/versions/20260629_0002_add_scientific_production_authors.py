from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


VERSION = "20260629_0002_add_scientific_production_authors"


def upgrade(engine: Engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "scientific_production_authors" not in tables:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE scientific_production_authors (
                        id SERIAL PRIMARY KEY,
                        production_id INTEGER NOT NULL REFERENCES scientific_productions(id) ON DELETE CASCADE,
                        author_order INTEGER NOT NULL DEFAULT 1,
                        raw_author_name TEXT NULL,
                        normalized_author_name VARCHAR(220) NULL,
                        teacher_id INTEGER NULL REFERENCES teachers(id) ON DELETE SET NULL,
                        author_type VARCHAR(30) NOT NULL DEFAULT 'unclassified',
                        person_type VARCHAR(60) NOT NULL DEFAULT 'pendiente_clasificacion',
                        production_role VARCHAR(60) NOT NULL DEFAULT 'autor_producto',
                        validation_status VARCHAR(60) NOT NULL DEFAULT 'pendiente_validacion',
                        confidence_score DOUBLE PRECISION NULL,
                        reason TEXT NULL,
                        source_file VARCHAR(255) NULL,
                        source_page INTEGER NULL,
                        source_section VARCHAR(120) NULL,
                        import_batch_id INTEGER NULL,
                        import_job_id INTEGER NULL REFERENCES import_jobs(id) ON DELETE SET NULL,
                        parser_version VARCHAR(80) NULL,
                        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        CONSTRAINT uq_scientific_production_author_order UNIQUE (production_id, author_order)
                    )
                    """
                )
            )
    else:
        _ensure_columns(engine)

    _ensure_indexes(engine)
    _backfill_teacher_authors(engine)


def _ensure_columns(engine: Engine) -> None:
    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("scientific_production_authors")}
    column_statements = {
        "author_order": "ALTER TABLE scientific_production_authors ADD COLUMN author_order INTEGER NOT NULL DEFAULT 1",
        "raw_author_name": "ALTER TABLE scientific_production_authors ADD COLUMN raw_author_name TEXT",
        "normalized_author_name": "ALTER TABLE scientific_production_authors ADD COLUMN normalized_author_name VARCHAR(220)",
        "author_type": "ALTER TABLE scientific_production_authors ADD COLUMN author_type VARCHAR(30) NOT NULL DEFAULT 'unclassified'",
        "person_type": "ALTER TABLE scientific_production_authors ADD COLUMN person_type VARCHAR(60) NOT NULL DEFAULT 'pendiente_clasificacion'",
        "production_role": "ALTER TABLE scientific_production_authors ADD COLUMN production_role VARCHAR(60) NOT NULL DEFAULT 'autor_producto'",
        "validation_status": "ALTER TABLE scientific_production_authors ADD COLUMN validation_status VARCHAR(60) NOT NULL DEFAULT 'pendiente_validacion'",
        "confidence_score": "ALTER TABLE scientific_production_authors ADD COLUMN confidence_score DOUBLE PRECISION",
        "reason": "ALTER TABLE scientific_production_authors ADD COLUMN reason TEXT",
        "source_file": "ALTER TABLE scientific_production_authors ADD COLUMN source_file VARCHAR(255)",
        "source_page": "ALTER TABLE scientific_production_authors ADD COLUMN source_page INTEGER",
        "source_section": "ALTER TABLE scientific_production_authors ADD COLUMN source_section VARCHAR(120)",
        "import_batch_id": "ALTER TABLE scientific_production_authors ADD COLUMN import_batch_id INTEGER",
        "import_job_id": "ALTER TABLE scientific_production_authors ADD COLUMN import_job_id INTEGER",
        "parser_version": "ALTER TABLE scientific_production_authors ADD COLUMN parser_version VARCHAR(80)",
        "created_at": "ALTER TABLE scientific_production_authors ADD COLUMN created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP",
    }
    for column_name, statement in column_statements.items():
        if column_name not in columns:
            with engine.begin() as connection:
                connection.execute(text(statement))


def _ensure_indexes(engine: Engine) -> None:
    inspector = inspect(engine)
    indexes = {index["name"] for index in inspector.get_indexes("scientific_production_authors")}
    index_statements = {
        "ix_scientific_production_authors_production_id": (
            "CREATE INDEX ix_scientific_production_authors_production_id "
            "ON scientific_production_authors (production_id)"
        ),
        "ix_scientific_production_authors_teacher_id": (
            "CREATE INDEX ix_scientific_production_authors_teacher_id "
            "ON scientific_production_authors (teacher_id)"
        ),
        "ix_scientific_production_authors_normalized_author_name": (
            "CREATE INDEX ix_scientific_production_authors_normalized_author_name "
            "ON scientific_production_authors (normalized_author_name)"
        ),
        "ix_scientific_production_authors_author_type": (
            "CREATE INDEX ix_scientific_production_authors_author_type "
            "ON scientific_production_authors (author_type)"
        ),
        "ix_scientific_production_authors_person_type": (
            "CREATE INDEX ix_scientific_production_authors_person_type "
            "ON scientific_production_authors (person_type)"
        ),
        "ix_scientific_production_authors_validation_status": (
            "CREATE INDEX ix_scientific_production_authors_validation_status "
            "ON scientific_production_authors (validation_status)"
        ),
        "ix_scientific_production_authors_import_job_id": (
            "CREATE INDEX ix_scientific_production_authors_import_job_id "
            "ON scientific_production_authors (import_job_id)"
        ),
        "ix_scientific_production_authors_parser_version": (
            "CREATE INDEX ix_scientific_production_authors_parser_version "
            "ON scientific_production_authors (parser_version)"
        ),
        "ix_scientific_production_authors_created_at": (
            "CREATE INDEX ix_scientific_production_authors_created_at "
            "ON scientific_production_authors (created_at)"
        ),
    }
    for index_name, statement in index_statements.items():
        if index_name not in indexes:
            with engine.begin() as connection:
                connection.execute(text(statement))


def _backfill_teacher_authors(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO scientific_production_authors (
                    production_id,
                    author_order,
                    raw_author_name,
                    normalized_author_name,
                    teacher_id,
                    author_type,
                    person_type,
                    production_role,
                    validation_status,
                    confidence_score,
                    reason,
                    parser_version
                )
                SELECT
                    production.id,
                    COALESCE((
                        SELECT MAX(existing.author_order) + 1
                        FROM scientific_production_authors AS existing
                        WHERE existing.production_id = production.id
                    ), 1),
                    teacher.full_name,
                    teacher.full_name,
                    production.teacher_id,
                    'internal_teacher',
                    'docente_interno',
                    'autor_producto',
                    'validado',
                    1.0,
                    'Autor principal creado desde teacher_id historico de la produccion.',
                    '20260629_0002'
                FROM scientific_productions AS production
                JOIN teachers AS teacher ON teacher.id = production.teacher_id
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM scientific_production_authors AS author
                    WHERE author.production_id = production.id
                    AND (
                        author.teacher_id = production.teacher_id
                        OR author.normalized_author_name = teacher.full_name
                        OR author.raw_author_name = teacher.full_name
                    )
                )
                """
            )
        )
