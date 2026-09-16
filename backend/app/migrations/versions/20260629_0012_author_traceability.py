from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


VERSION = "20260629_0012_author_traceability"


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


def upgrade(engine: Engine) -> None:
    with engine.begin() as connection:
        author_columns = _columns(engine, "scientific_production_authors")
        if not author_columns:
            return
        _add_column(connection, "scientific_production_authors", author_columns, "source_field", "VARCHAR(120) NULL")
        _add_column(connection, "scientific_production_authors", author_columns, "row_or_block_id", "VARCHAR(120) NULL")
        _add_column(
            connection,
            "scientific_production_authors",
            author_columns,
            "validation_status",
            "VARCHAR(40) NOT NULL DEFAULT 'pending_review'",
        )
        _add_column(connection, "scientific_production_authors", author_columns, "metadata_json", "JSON NULL")
        _create_index(
            connection,
            "ix_scientific_production_authors_row_or_block_id",
            "scientific_production_authors",
            "row_or_block_id",
        )
        _create_index(
            connection,
            "ix_scientific_production_authors_validation_status",
            "scientific_production_authors",
            "validation_status",
        )
