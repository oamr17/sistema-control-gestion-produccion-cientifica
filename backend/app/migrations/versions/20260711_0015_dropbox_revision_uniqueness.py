from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


VERSION = "20260711_0015_dropbox_revision_uniqueness"
CONSTRAINT = "uq_import_jobs_document_revision"


def upgrade(engine: Engine) -> None:
    constraints = {item["name"] for item in inspect(engine).get_unique_constraints("import_jobs")}
    if CONSTRAINT in constraints:
        return

    with engine.begin() as connection:
        duplicate = connection.execute(
            text(
                """
                SELECT document_key, source_rev, COUNT(*) AS copies
                FROM import_jobs
                WHERE document_key IS NOT NULL AND source_rev IS NOT NULL
                GROUP BY document_key, source_rev
                HAVING COUNT(*) > 1
                LIMIT 1
                """
            )
        ).first()
        if duplicate:
            raise RuntimeError(
                "No se puede crear uq_import_jobs_document_revision: "
                f"{duplicate.document_key} / {duplicate.source_rev} tiene {duplicate.copies} jobs."
            )
        connection.execute(
            text(
                "ALTER TABLE import_jobs ADD CONSTRAINT "
                f"{CONSTRAINT} UNIQUE (document_key, source_rev)"
            )
        )


def downgrade(engine: Engine) -> None:
    constraints = {item["name"] for item in inspect(engine).get_unique_constraints("import_jobs")}
    if CONSTRAINT not in constraints:
        return
    with engine.begin() as connection:
        connection.execute(text(f"ALTER TABLE import_jobs DROP CONSTRAINT {CONSTRAINT}"))
