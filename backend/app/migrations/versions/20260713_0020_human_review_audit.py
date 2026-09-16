from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine


VERSION = "20260713_0020_human_review_audit"
revision = VERSION
down_revision = "20260713_0019_human_review_projection"

_AUDIT_EVENTS = "audit_events"
_APPEND_ONLY_FUNCTION = "b2b_reject_append_only_mutation"
_APPEND_ONLY_TRIGGER = "trg_audit_events_append_only"

AUDIT_EVENT_TYPES = (
    "case_backfilled",
    "locked_decision_imported",
    "identity_created",
    "alias_created",
    "override_created",
    "capability_assigned",
    "capability_revoked",
    "audit_corrected",
    "functional_reversion",
)

_EXPECTED_COLUMNS = (
    ("id", "uuid", "uuid", None, "NO", None),
    ("event_type", "character varying", "varchar", 60, "NO", None),
    ("aggregate_type", "character varying", "varchar", 60, "NO", None),
    ("aggregate_key", "character varying", "varchar", 320, "NO", None),
    ("review_item_id", "uuid", "uuid", None, "YES", None),
    ("actor_user_id", "integer", "int4", None, "YES", None),
    ("actor_identifier", "character varying", "varchar", 180, "NO", None),
    ("actor_capability", "character varying", "varchar", 40, "YES", None),
    ("occurred_at", "timestamp with time zone", "timestamptz", None, "NO", "CURRENT_TIMESTAMP"),
    ("payload_schema", "character varying", "varchar", 80, "NO", None),
    ("payload_version", "smallint", "int2", None, "NO", "1"),
    ("payload", "jsonb", "jsonb", None, "NO", None),
    ("correlation_id", "uuid", "uuid", None, "NO", None),
    ("request_id", "uuid", "uuid", None, "YES", None),
    ("previous_event_id", "uuid", "uuid", None, "YES", None),
    ("corrects_event_id", "uuid", "uuid", None, "YES", None),
    ("previous_event_hash", "character", "bpchar", 64, "YES", None),
    ("event_hash", "character", "bpchar", 64, "NO", None),
    ("created_at", "timestamp with time zone", "timestamptz", None, "NO", "CURRENT_TIMESTAMP"),
)

_EXPECTED_CONSTRAINT_TYPES = {
    "pk_audit_events": "p",
    "uq_audit_events_event_hash": "u",
    "fk_audit_events_review_item_id": "f",
    "fk_audit_events_actor_user_id": "f",
    "fk_audit_events_previous_event_id": "f",
    "fk_audit_events_corrects_event_id": "f",
    "ck_audit_events_event_type": "c",
    "ck_audit_events_actor_capability": "c",
    "ck_audit_events_payload_object": "c",
    "ck_audit_events_payload_version": "c",
    "ck_audit_events_hashes": "c",
    "ck_audit_events_previous_not_self": "c",
    "ck_audit_events_corrects_not_self": "c",
    "ck_audit_events_correction_target": "c",
}

_EXPECTED_FOREIGN_KEYS = {
    "fk_audit_events_review_item_id":
        (("review_item_id",), "review_items", ("id",), "r", False, False),
    "fk_audit_events_actor_user_id":
        (("actor_user_id",), "users", ("id",), "r", False, False),
    "fk_audit_events_previous_event_id":
        (("previous_event_id",), _AUDIT_EVENTS, ("id",), "r", False, False),
    "fk_audit_events_corrects_event_id":
        (("corrects_event_id",), _AUDIT_EVENTS, ("id",), "r", False, False),
}

_EXPECTED_CHECK_LITERALS = {
    "ck_audit_events_event_type": set(AUDIT_EVENT_TYPES),
    "ck_audit_events_actor_capability": {"RESEARCH_MANAGER", "SYSTEM_ADMIN"},
    "ck_audit_events_payload_object": {"object"},
    "ck_audit_events_payload_version": set(),
    "ck_audit_events_hashes": {"^[0-9a-f]{64}$"},
    "ck_audit_events_previous_not_self": set(),
    "ck_audit_events_corrects_not_self": set(),
    "ck_audit_events_correction_target": {"audit_corrected"},
}

_EXPECTED_INDEXES = {
    "pk_audit_events": (True, True, ("id",)),
    "uq_audit_events_event_hash": (True, False, ("event_hash",)),
    "ix_audit_events_occurred_at": (False, False, ("occurred_at",)),
    "ix_audit_events_review_item": (False, False, ("review_item_id",)),
    "ix_audit_events_actor": (False, False, ("actor_user_id", "occurred_at")),
    "ix_audit_events_event_type": (False, False, ("event_type", "occurred_at")),
    "ix_audit_events_aggregate": (False, False, ("aggregate_type", "aggregate_key")),
    "ix_audit_events_correlation": (False, False, ("correlation_id",)),
    "ix_audit_events_corrects": (False, False, ("corrects_event_id",)),
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(f"0020 schema assertion failed: {message}")


def assert_schema(connection: Connection) -> None:
    columns = tuple(
        tuple(row)
        for row in connection.execute(text(
            """
            SELECT column_name, data_type, udt_name,
                   character_maximum_length, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'audit_events'
            ORDER BY ordinal_position
            """
        ))
    )
    _require(columns == _EXPECTED_COLUMNS, "columns/types/nullability/defaults differ")

    constraints = {
        row.conname: row.contype
        for row in connection.execute(text(
            """
            SELECT constraint_row.conname, constraint_row.contype
            FROM pg_constraint AS constraint_row
            JOIN pg_class AS table_row ON table_row.oid = constraint_row.conrelid
            JOIN pg_namespace AS namespace_row ON namespace_row.oid = table_row.relnamespace
            WHERE namespace_row.nspname = current_schema()
              AND table_row.relname = 'audit_events'
            """
        ))
    }
    _require(constraints == _EXPECTED_CONSTRAINT_TYPES, "constraint names/types differ")

    foreign_keys = {}
    for row in connection.execute(text(
        """
        SELECT constraint_row.conname,
               ARRAY(
                   SELECT source_attribute.attname
                   FROM unnest(constraint_row.conkey) WITH ORDINALITY
                        AS source_key(attnum, position)
                   JOIN pg_attribute AS source_attribute
                     ON source_attribute.attrelid = constraint_row.conrelid
                    AND source_attribute.attnum = source_key.attnum
                   ORDER BY source_key.position
               ) AS source_columns,
               target_table.relname AS target_table,
               ARRAY(
                   SELECT target_attribute.attname
                   FROM unnest(constraint_row.confkey) WITH ORDINALITY
                        AS target_key(attnum, position)
                   JOIN pg_attribute AS target_attribute
                     ON target_attribute.attrelid = constraint_row.confrelid
                    AND target_attribute.attnum = target_key.attnum
                   ORDER BY target_key.position
               ) AS target_columns,
               constraint_row.confdeltype,
               constraint_row.condeferrable,
               constraint_row.condeferred
        FROM pg_constraint AS constraint_row
        JOIN pg_class AS source_table ON source_table.oid = constraint_row.conrelid
        JOIN pg_namespace AS source_namespace ON source_namespace.oid = source_table.relnamespace
        JOIN pg_class AS target_table ON target_table.oid = constraint_row.confrelid
        JOIN pg_namespace AS target_namespace ON target_namespace.oid = target_table.relnamespace
        WHERE source_namespace.nspname = current_schema()
          AND target_namespace.nspname = current_schema()
          AND source_table.relname = 'audit_events'
          AND constraint_row.contype = 'f'
        """
    )):
        foreign_keys[row.conname] = (
            tuple(row.source_columns), row.target_table, tuple(row.target_columns),
            row.confdeltype, row.condeferrable, row.condeferred,
        )
    _require(foreign_keys == _EXPECTED_FOREIGN_KEYS, "foreign key catalog differs")

    check_definitions = {
        row.conname: row.definition
        for row in connection.execute(text(
            """
            SELECT constraint_row.conname,
                   pg_get_constraintdef(constraint_row.oid, true) AS definition
            FROM pg_constraint AS constraint_row
            JOIN pg_class AS table_row ON table_row.oid = constraint_row.conrelid
            JOIN pg_namespace AS namespace_row ON namespace_row.oid = table_row.relnamespace
            WHERE namespace_row.nspname = current_schema()
              AND table_row.relname = 'audit_events'
              AND constraint_row.contype = 'c'
            """
        ))
    }
    _require(
        set(check_definitions) == set(_EXPECTED_CHECK_LITERALS),
        "check constraint definitions are incomplete",
    )
    for name, expected_literals in _EXPECTED_CHECK_LITERALS.items():
        actual_literals = set(re.findall(r"'([^']*)'", check_definitions[name]))
        _require(actual_literals == expected_literals, f"closed literals differ for {name}")
    _require(
        "payload_version = 1" in check_definitions["ck_audit_events_payload_version"],
        "payload version check differs",
    )
    _require(
        "previous_event_id <> id" in check_definitions["ck_audit_events_previous_not_self"],
        "previous event self-reference check differs",
    )
    _require(
        "corrects_event_id <> id" in check_definitions["ck_audit_events_corrects_not_self"],
        "correction self-reference check differs",
    )

    indexes = {}
    for row in connection.execute(text(
        """
        SELECT index_row.relname AS index_name,
               index_catalog.indisunique,
               index_catalog.indisprimary,
               index_catalog.indpred IS NULL AS has_no_predicate,
               ARRAY(
                   SELECT pg_get_indexdef(index_row.oid, position, true)
                   FROM generate_series(1, index_catalog.indnkeyatts) AS position
                   ORDER BY position
               ) AS key_columns
        FROM pg_index AS index_catalog
        JOIN pg_class AS table_row ON table_row.oid = index_catalog.indrelid
        JOIN pg_class AS index_row ON index_row.oid = index_catalog.indexrelid
        JOIN pg_namespace AS namespace_row ON namespace_row.oid = table_row.relnamespace
        WHERE namespace_row.nspname = current_schema()
          AND table_row.relname = 'audit_events'
        """
    )):
        _require(row.has_no_predicate, f"unexpected predicate on {row.index_name}")
        indexes[row.index_name] = (
            row.indisunique,
            row.indisprimary,
            tuple(row.key_columns),
        )
    _require(indexes == _EXPECTED_INDEXES, "index names/flags/columns differ")

    triggers = tuple(connection.execute(text(
        """
        SELECT trigger_row.tgname,
               trigger_row.tgenabled,
               trigger_row.tgtype,
               function_row.proname,
               pg_get_function_identity_arguments(function_row.oid) AS function_arguments
        FROM pg_trigger AS trigger_row
        JOIN pg_class AS table_row ON table_row.oid = trigger_row.tgrelid
        JOIN pg_namespace AS namespace_row ON namespace_row.oid = table_row.relnamespace
        JOIN pg_proc AS function_row ON function_row.oid = trigger_row.tgfoid
        WHERE namespace_row.nspname = current_schema()
          AND table_row.relname = 'audit_events'
          AND NOT trigger_row.tgisinternal
        ORDER BY trigger_row.tgname
        """
    )))
    _require(len(triggers) == 1, "append-only trigger multiplicity differs")
    trigger = triggers[0]
    _require(
        tuple(trigger) == (
            _APPEND_ONLY_TRIGGER,
            "O",
            58,
            _APPEND_ONLY_FUNCTION,
            "",
        ),
        "append-only trigger definition or enabled state differs",
    )


def upgrade(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text(
            """
            CREATE TABLE audit_events (
                id UUID NOT NULL,
                event_type VARCHAR(60) NOT NULL,
                aggregate_type VARCHAR(60) NOT NULL,
                aggregate_key VARCHAR(320) NOT NULL,
                review_item_id UUID NULL,
                actor_user_id INTEGER NULL,
                actor_identifier VARCHAR(180) NOT NULL,
                actor_capability VARCHAR(40) NULL,
                occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                payload_schema VARCHAR(80) NOT NULL,
                payload_version SMALLINT NOT NULL DEFAULT 1,
                payload JSONB NOT NULL,
                correlation_id UUID NOT NULL,
                request_id UUID NULL,
                previous_event_id UUID NULL,
                corrects_event_id UUID NULL,
                previous_event_hash CHAR(64) NULL,
                event_hash CHAR(64) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT pk_audit_events PRIMARY KEY (id),
                CONSTRAINT uq_audit_events_event_hash UNIQUE (event_hash),
                CONSTRAINT fk_audit_events_review_item_id
                    FOREIGN KEY (review_item_id) REFERENCES review_items (id)
                    ON DELETE RESTRICT,
                CONSTRAINT fk_audit_events_actor_user_id
                    FOREIGN KEY (actor_user_id) REFERENCES users (id)
                    ON DELETE RESTRICT,
                CONSTRAINT fk_audit_events_previous_event_id
                    FOREIGN KEY (previous_event_id) REFERENCES audit_events (id)
                    ON DELETE RESTRICT,
                CONSTRAINT fk_audit_events_corrects_event_id
                    FOREIGN KEY (corrects_event_id) REFERENCES audit_events (id)
                    ON DELETE RESTRICT,
                CONSTRAINT ck_audit_events_event_type CHECK (
                    event_type IN (
                        'case_backfilled', 'locked_decision_imported',
                        'identity_created', 'alias_created', 'override_created',
                        'capability_assigned', 'capability_revoked',
                        'audit_corrected', 'functional_reversion'
                    )
                ),
                CONSTRAINT ck_audit_events_actor_capability CHECK (
                    actor_capability IS NULL
                    OR actor_capability IN ('RESEARCH_MANAGER', 'SYSTEM_ADMIN')
                ),
                CONSTRAINT ck_audit_events_payload_object CHECK (
                    jsonb_typeof(payload) = 'object'
                ),
                CONSTRAINT ck_audit_events_payload_version CHECK (payload_version = 1),
                CONSTRAINT ck_audit_events_hashes CHECK (
                    event_hash ~ '^[0-9a-f]{64}$'
                    AND (
                        previous_event_hash IS NULL
                        OR previous_event_hash ~ '^[0-9a-f]{64}$'
                    )
                ),
                CONSTRAINT ck_audit_events_previous_not_self CHECK (
                    previous_event_id IS NULL OR previous_event_id <> id
                ),
                CONSTRAINT ck_audit_events_corrects_not_self CHECK (
                    corrects_event_id IS NULL OR corrects_event_id <> id
                ),
                CONSTRAINT ck_audit_events_correction_target CHECK (
                    event_type <> 'audit_corrected' OR corrects_event_id IS NOT NULL
                )
            )
            """
        ))
        connection.execute(text(
            "CREATE INDEX ix_audit_events_occurred_at ON audit_events (occurred_at)"
        ))
        connection.execute(text(
            "CREATE INDEX ix_audit_events_review_item ON audit_events (review_item_id)"
        ))
        connection.execute(text(
            "CREATE INDEX ix_audit_events_actor ON audit_events (actor_user_id, occurred_at)"
        ))
        connection.execute(text(
            "CREATE INDEX ix_audit_events_event_type ON audit_events (event_type, occurred_at)"
        ))
        connection.execute(text(
            "CREATE INDEX ix_audit_events_aggregate ON audit_events (aggregate_type, aggregate_key)"
        ))
        connection.execute(text(
            "CREATE INDEX ix_audit_events_correlation ON audit_events (correlation_id)"
        ))
        connection.execute(text(
            "CREATE INDEX ix_audit_events_corrects ON audit_events (corrects_event_id)"
        ))
        connection.execute(text(
            """
            CREATE TRIGGER trg_audit_events_append_only
            BEFORE UPDATE OR DELETE OR TRUNCATE ON audit_events
            FOR EACH STATEMENT
            EXECUTE FUNCTION b2b_reject_append_only_mutation()
            """
        ))
        assert_schema(connection)


def downgrade(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text(
            "DROP TRIGGER trg_audit_events_append_only ON audit_events"
        ))
        connection.execute(text("DROP TABLE audit_events"))
