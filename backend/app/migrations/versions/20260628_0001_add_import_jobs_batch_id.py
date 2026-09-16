from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


VERSION = "20260628_0001_add_import_jobs_batch_id"


def upgrade(engine: Engine) -> None:
    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("import_jobs")}
    if "batch_id" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE import_jobs ADD COLUMN batch_id INTEGER"))

    inspector = inspect(engine)
    indexes = {index["name"] for index in inspector.get_indexes("import_jobs")}
    if "ix_import_jobs_batch_id" not in indexes:
        with engine.begin() as connection:
            connection.execute(text("CREATE INDEX ix_import_jobs_batch_id ON import_jobs (batch_id)"))
