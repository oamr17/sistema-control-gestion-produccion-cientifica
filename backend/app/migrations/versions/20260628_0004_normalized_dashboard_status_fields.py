from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


VERSION = "20260628_0004_normalized_dashboard_status_fields"


def _add_column_if_missing(engine: Engine, table: str, column: str, ddl: str) -> None:
    inspector = inspect(engine)
    columns = {item["name"] for item in inspector.get_columns(table)}
    if column not in columns:
        with engine.begin() as connection:
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))


def _create_index_if_missing(engine: Engine, table: str, index_name: str, ddl: str) -> None:
    inspector = inspect(engine)
    indexes = {item["name"] for item in inspector.get_indexes(table)}
    if index_name not in indexes:
        with engine.begin() as connection:
            connection.execute(text(ddl))


def upgrade(engine: Engine) -> None:
    _add_column_if_missing(engine, "scientific_productions", "status", "status VARCHAR(40) DEFAULT 'published'")
    _add_column_if_missing(engine, "research_projects", "status", "status VARCHAR(40) DEFAULT 'vigente'")
    _add_column_if_missing(engine, "research_projects", "progress_percentage", "progress_percentage FLOAT DEFAULT 0")

    with engine.begin() as connection:
        connection.execute(text("UPDATE scientific_productions SET status = 'published' WHERE status IS NULL"))
        connection.execute(text("UPDATE research_projects SET status = 'vigente' WHERE status IS NULL"))
        connection.execute(text("UPDATE research_projects SET progress_percentage = 0 WHERE progress_percentage IS NULL"))

    _create_index_if_missing(
        engine,
        "scientific_productions",
        "ix_scientific_productions_status",
        "CREATE INDEX ix_scientific_productions_status ON scientific_productions (status)",
    )
    _create_index_if_missing(
        engine,
        "research_projects",
        "ix_research_projects_status",
        "CREATE INDEX ix_research_projects_status ON research_projects (status)",
    )
