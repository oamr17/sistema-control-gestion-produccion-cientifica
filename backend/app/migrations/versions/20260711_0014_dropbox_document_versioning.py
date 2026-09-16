from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


VERSION = "20260711_0014_dropbox_document_versioning"


def upgrade(engine: Engine) -> None:
    columns = {column["name"] for column in inspect(engine).get_columns("import_jobs")}
    with engine.begin() as connection:
        if "document_key" not in columns:
            connection.execute(text("ALTER TABLE import_jobs ADD COLUMN document_key VARCHAR(900)"))
        if "is_current" not in columns:
            connection.execute(text("ALTER TABLE import_jobs ADD COLUMN is_current BOOLEAN NOT NULL DEFAULT FALSE"))
        if "supersedes_id" not in columns:
            connection.execute(
                text(
                    "ALTER TABLE import_jobs ADD COLUMN supersedes_id INTEGER "
                    "REFERENCES import_jobs(id)"
                )
            )

        connection.execute(
            text(
                """
                UPDATE import_jobs
                SET document_key = CASE
                    WHEN source_identifier LIKE 'id:%' THEN 'dropbox:' || source_identifier
                    WHEN source_identifier IS NOT NULL AND BTRIM(source_identifier) <> ''
                        THEN 'dropbox_path:/' || LOWER(
                            REGEXP_REPLACE(
                                REPLACE(BTRIM(source_identifier), CHR(92), '/'),
                                '^/+|/+$',
                                '',
                                'g'
                            )
                        )
                    ELSE NULL
                END
                WHERE document_key IS NULL
                """
            )
        )
        connection.execute(text("UPDATE import_jobs SET is_current = FALSE WHERE document_key IS NOT NULL"))
        connection.execute(
            text(
                """
                WITH ranked AS (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY document_key
                               ORDER BY id DESC
                           ) AS position
                    FROM import_jobs
                    WHERE document_key IS NOT NULL
                      AND status NOT IN ('ERROR', 'QUEUED', 'PROCESSING')
                )
                UPDATE import_jobs
                SET is_current = TRUE
                FROM ranked
                WHERE import_jobs.id = ranked.id
                  AND ranked.position = 1
                """
            )
        )
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_import_jobs_document_key ON import_jobs (document_key)")
        )
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_import_jobs_is_current ON import_jobs (is_current)")
        )
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_import_jobs_supersedes_id ON import_jobs (supersedes_id)")
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_import_jobs_current_document "
                "ON import_jobs (document_key) WHERE is_current = TRUE AND document_key IS NOT NULL"
            )
        )
