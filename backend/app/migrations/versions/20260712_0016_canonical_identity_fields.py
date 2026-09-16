from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


VERSION = "20260712_0016_canonical_identity_fields"
revision = VERSION
down_revision = "20260711_0015_dropbox_revision_uniqueness"

TABLES = (
    "person_roles",
    "scientific_production_authors",
)
COLUMNS = (
    ("canonical_identity_key", "VARCHAR(320) NULL"),
    ("canonical_name", "VARCHAR(220) NULL"),
    ("identity_source", "VARCHAR(40) NULL"),
    ("identity_confidence", "DOUBLE PRECISION NULL"),
    ("identity_reason", "TEXT NULL"),
    ("identity_locked", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("identity_decided_by", "VARCHAR(180) NULL"),
    ("identity_decided_at", "TIMESTAMPTZ NULL"),
)


def _columns(engine: Engine, table: str) -> set[str]:
    if not inspect(engine).has_table(table):
        return set()
    return {column["name"] for column in inspect(engine).get_columns(table)}


def _indexes(engine: Engine, table: str) -> set[str]:
    if not inspect(engine).has_table(table):
        return set()
    return {index["name"] for index in inspect(engine).get_indexes(table)}


def _add_column(connection, table: str, existing: set[str], name: str, definition: str) -> None:
    if name in existing:
        return
    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
    existing.add(name)


def _drop_column(connection, table: str, existing: set[str], name: str) -> None:
    if name not in existing:
        return
    connection.execute(text(f"ALTER TABLE {table} DROP COLUMN {name}"))
    existing.remove(name)


def _require_target_tables(engine: Engine) -> dict[str, set[str]]:
    columns_by_table = {table: _columns(engine, table) for table in TABLES}
    missing = [table for table, columns in columns_by_table.items() if not columns]
    if missing:
        raise RuntimeError(
            "Missing required tables for canonical identity migration: "
            + ", ".join(missing)
        )
    return columns_by_table


def upgrade(engine: Engine) -> None:
    columns_by_table = _require_target_tables(engine)
    with engine.begin() as connection:
        for table in TABLES:
            existing = columns_by_table[table]
            for name, definition in COLUMNS:
                _add_column(connection, table, existing, name, definition)

            connection.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS ix_{table}_canonical_identity_key "
                    f"ON {table} (canonical_identity_key)"
                )
            )


def downgrade(engine: Engine) -> None:
    columns_by_table = _require_target_tables(engine)
    with engine.begin() as connection:
        for table in TABLES:
            existing = columns_by_table[table]

            index_name = f"ix_{table}_canonical_identity_key"
            if index_name in _indexes(engine, table):
                connection.execute(text(f"DROP INDEX IF EXISTS {index_name}"))

            for name, _definition in reversed(COLUMNS):
                _drop_column(connection, table, existing, name)
