from __future__ import annotations

from importlib import import_module
import re

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine


VERSION = "20260718_0021_human_review_scientific_decision_audit"
revision = VERSION
DOWN_REVISION = "20260713_0020_human_review_audit"
down_revision = DOWN_REVISION

_PREDECESSOR = import_module(
    "app.migrations.versions.20260713_0020_human_review_audit"
)
AUDIT_EVENT_TYPES = _PREDECESSOR.AUDIT_EVENT_TYPES + (
    "scientific_decision_applied",
)
_EXPECTED_CONSTRAINT_DEFINITION = (
    "CHECK (event_type::text = ANY (ARRAY["
    + ", ".join(
        f"'{event_type}'::character varying"
        for event_type in AUDIT_EVENT_TYPES
    )
    + "]::text[]))"
)


def assert_schema(connection: Connection) -> None:
    rows = tuple(connection.execute(text(
        """
        SELECT pg_get_constraintdef(constraint_row.oid, true)
        FROM pg_constraint AS constraint_row
        JOIN pg_class AS table_row ON table_row.oid = constraint_row.conrelid
        JOIN pg_namespace AS namespace_row
          ON namespace_row.oid = table_row.relnamespace
        WHERE namespace_row.nspname = current_schema()
          AND table_row.relname = 'audit_events'
          AND constraint_row.conname = 'ck_audit_events_event_type'
          AND constraint_row.contype = 'c'
        """
    )).scalars())
    if len(rows) != 1:
        raise RuntimeError(
            "0021 schema assertion failed: event type constraint multiplicity differs"
        )
    literals = tuple(re.findall(r"'([^']*)'", rows[0]))
    if literals != AUDIT_EVENT_TYPES:
        raise RuntimeError(
            "0021 schema assertion failed: event type literals differ"
        )
    if rows[0] != _EXPECTED_CONSTRAINT_DEFINITION:
        raise RuntimeError(
            "0021 schema assertion failed: constraint definition differs"
        )


def upgrade(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text(
            "ALTER TABLE audit_events "
            "DROP CONSTRAINT ck_audit_events_event_type"
        ))
        connection.execute(text(
            """
            ALTER TABLE audit_events
            ADD CONSTRAINT ck_audit_events_event_type CHECK (
                event_type IN (
                    'case_backfilled', 'locked_decision_imported',
                    'identity_created', 'alias_created', 'override_created',
                    'capability_assigned', 'capability_revoked',
                    'audit_corrected', 'functional_reversion',
                    'scientific_decision_applied'
                )
            )
            """
        ))
        assert_schema(connection)


def downgrade(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text(
            "ALTER TABLE audit_events "
            "DROP CONSTRAINT ck_audit_events_event_type"
        ))
        connection.execute(text(
            """
            ALTER TABLE audit_events
            ADD CONSTRAINT ck_audit_events_event_type CHECK (
                event_type IN (
                    'case_backfilled', 'locked_decision_imported',
                    'identity_created', 'alias_created', 'override_created',
                    'capability_assigned', 'capability_revoked',
                    'audit_corrected', 'functional_reversion'
                )
            )
            """
        ))
        _PREDECESSOR.assert_schema(connection)
