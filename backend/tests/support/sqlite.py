from __future__ import annotations

from sqlalchemy.engine import Engine

from app.core.database import Base


POSTGRESQL_ONLY_B2B1_TABLES = frozenset(
    {
        "audit_events",
        "canonical_identities",
        "field_overrides",
        "person_aliases",
        "review_decisions",
        "review_items",
        "user_b2b_capabilities",
    }
)


def create_sqlite_compatible_schema(engine: Engine) -> None:
    """Create the legacy application tables used by SQLite unit tests.

    B2B.1 persistence is PostgreSQL-only and is exercised by the real
    PostgreSQL test suites.  Legacy SQLite tests do not consume those tables.
    """

    tables = [
        table
        for name, table in Base.metadata.tables.items()
        if name not in POSTGRESQL_ONLY_B2B1_TABLES
    ]
    Base.metadata.create_all(engine, tables=tables)
