from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine


VERSION = "20260713_0019_human_review_projection"
revision = VERSION
down_revision = "20260713_0018_human_review_core"

_CANONICAL_IDENTITIES = "canonical_identities"
_PERSON_ALIASES = "person_aliases"
_FIELD_OVERRIDES = "field_overrides"
_LATER_VERSION = "20260713_0020_human_review_audit"
_DISPOSABLE_APPLICATION_NAME = "b2b1_disposable_migration_test"

OVERRIDE_FIELDS = (
    "canonical_identity_key",
    "canonical_name",
    "product_title",
    "author_identity_key",
    "project_director_identity_key",
    "project_director_relationship_status",
    "external_identity_key",
    "external_institution",
    "scientific_status",
)

_EXPECTED_COLUMNS = {
    _CANONICAL_IDENTITIES: (
        ("id", "uuid", "uuid", None, "NO", None),
        ("canonical_identity_key", "character varying", "varchar", 320, "NO", None),
        ("identity_type", "character varying", "varchar", 40, "NO", None),
        ("display_name", "character varying", "varchar", 220, "NO", None),
        ("status", "character varying", "varchar", 30, "NO", "'active'::character varying"),
        ("origin", "character varying", "varchar", 30, "NO", None),
        ("created_by_decision_id", "uuid", "uuid", None, "YES", None),
        ("superseded_by_id", "uuid", "uuid", None, "YES", None),
        ("version", "integer", "int4", None, "NO", "1"),
        ("created_at", "timestamp with time zone", "timestamptz", None, "NO", "CURRENT_TIMESTAMP"),
        ("updated_at", "timestamp with time zone", "timestamptz", None, "NO", "CURRENT_TIMESTAMP"),
    ),
    _PERSON_ALIASES: (
        ("id", "uuid", "uuid", None, "NO", None),
        ("alias_original", "character varying", "varchar", 320, "NO", None),
        ("alias_normalized", "character varying", "varchar", 320, "NO", None),
        ("alias_class", "character varying", "varchar", 40, "NO", "'person_name'::character varying"),
        ("canonical_identity_id", "uuid", "uuid", None, "NO", None),
        ("decision_id", "uuid", "uuid", None, "NO", None),
        ("scope", "character varying", "varchar", 30, "NO", "'global_identity'::character varying"),
        ("status", "character varying", "varchar", 30, "NO", "'active'::character varying"),
        ("superseded_by_id", "uuid", "uuid", None, "YES", None),
        ("version", "integer", "int4", None, "NO", "1"),
        ("created_at", "timestamp with time zone", "timestamptz", None, "NO", "CURRENT_TIMESTAMP"),
        ("updated_at", "timestamp with time zone", "timestamptz", None, "NO", "CURRENT_TIMESTAMP"),
    ),
    _FIELD_OVERRIDES: (
        ("id", "uuid", "uuid", None, "NO", None),
        ("review_item_id", "uuid", "uuid", None, "NO", None),
        ("decision_id", "uuid", "uuid", None, "NO", None),
        ("stable_target_key", "character varying", "varchar", 128, "NO", None),
        ("target_table", "character varying", "varchar", 80, "NO", None),
        ("target_pk", "bigint", "int8", None, "YES", None),
        ("field_path", "character varying", "varchar", 120, "NO", None),
        ("value_schema", "character varying", "varchar", 80, "NO", None),
        ("value_version", "smallint", "int2", None, "NO", "1"),
        ("projected_value", "jsonb", "jsonb", None, "NO", None),
        ("scope", "character varying", "varchar", 30, "NO", None),
        ("document_key", "character varying", "varchar", 900, "YES", None),
        ("period_id", "integer", "int4", None, "YES", None),
        ("relationship_key", "character varying", "varchar", 320, "YES", None),
        ("locked", "boolean", "bool", None, "NO", "true"),
        ("is_active", "boolean", "bool", None, "NO", "true"),
        ("valid_from", "timestamp with time zone", "timestamptz", None, "NO", "CURRENT_TIMESTAMP"),
        ("superseded_by_id", "uuid", "uuid", None, "YES", None),
        ("version", "integer", "int4", None, "NO", "1"),
        ("created_at", "timestamp with time zone", "timestamptz", None, "NO", "CURRENT_TIMESTAMP"),
        ("updated_at", "timestamp with time zone", "timestamptz", None, "NO", "CURRENT_TIMESTAMP"),
    ),
}

_EXPECTED_CONSTRAINT_TYPES = {
    (_CANONICAL_IDENTITIES, "pk_canonical_identities"): "p",
    (_CANONICAL_IDENTITIES, "uq_canonical_identities_key"): "u",
    (_CANONICAL_IDENTITIES, "ck_canonical_identities_identity_type"): "c",
    (_CANONICAL_IDENTITIES, "ck_canonical_identities_status"): "c",
    (_CANONICAL_IDENTITIES, "ck_canonical_identities_origin"): "c",
    (_CANONICAL_IDENTITIES, "fk_canonical_identities_created_by_decision_id"): "f",
    (_CANONICAL_IDENTITIES, "fk_canonical_identities_superseded_by_id"): "f",
    (_PERSON_ALIASES, "pk_person_aliases"): "p",
    (_PERSON_ALIASES, "ck_person_aliases_alias_class"): "c",
    (_PERSON_ALIASES, "ck_person_aliases_scope"): "c",
    (_PERSON_ALIASES, "ck_person_aliases_status"): "c",
    (_PERSON_ALIASES, "fk_person_aliases_canonical_identity_id"): "f",
    (_PERSON_ALIASES, "fk_person_aliases_decision_id"): "f",
    (_PERSON_ALIASES, "fk_person_aliases_superseded_by_id"): "f",
    (_FIELD_OVERRIDES, "pk_field_overrides"): "p",
    (_FIELD_OVERRIDES, "ck_field_overrides_target_table"): "c",
    (_FIELD_OVERRIDES, "ck_field_overrides_field_path"): "c",
    (_FIELD_OVERRIDES, "ck_field_overrides_scope"): "c",
    (_FIELD_OVERRIDES, "ck_field_overrides_scope_context"): "c",
    (_FIELD_OVERRIDES, "ck_field_overrides_value_object"): "c",
    (_FIELD_OVERRIDES, "ck_field_overrides_value_version"): "c",
    (_FIELD_OVERRIDES, "fk_field_overrides_review_item_id"): "f",
    (_FIELD_OVERRIDES, "fk_field_overrides_decision_id"): "f",
    (_FIELD_OVERRIDES, "fk_field_overrides_superseded_by_id"): "f",
}

_EXPECTED_FOREIGN_KEYS = {
    (_CANONICAL_IDENTITIES, "fk_canonical_identities_created_by_decision_id"):
        (("created_by_decision_id",), "review_decisions", ("id",), "r", False, False),
    (_CANONICAL_IDENTITIES, "fk_canonical_identities_superseded_by_id"):
        (("superseded_by_id",), _CANONICAL_IDENTITIES, ("id",), "r", False, False),
    (_PERSON_ALIASES, "fk_person_aliases_canonical_identity_id"):
        (("canonical_identity_id",), _CANONICAL_IDENTITIES, ("id",), "r", False, False),
    (_PERSON_ALIASES, "fk_person_aliases_decision_id"):
        (("decision_id",), "review_decisions", ("id",), "r", False, False),
    (_PERSON_ALIASES, "fk_person_aliases_superseded_by_id"):
        (("superseded_by_id",), _PERSON_ALIASES, ("id",), "r", False, False),
    (_FIELD_OVERRIDES, "fk_field_overrides_review_item_id"):
        (("review_item_id",), "review_items", ("id",), "r", False, False),
    (_FIELD_OVERRIDES, "fk_field_overrides_decision_id"):
        (("decision_id",), "review_decisions", ("id",), "r", False, False),
    (_FIELD_OVERRIDES, "fk_field_overrides_superseded_by_id"):
        (("superseded_by_id",), _FIELD_OVERRIDES, ("id",), "r", False, False),
}

_EXPECTED_CHECK_LITERALS = {
    (_CANONICAL_IDENTITIES, "ck_canonical_identities_identity_type"):
        {"internal_person", "external_person", "unclassified_person"},
    (_CANONICAL_IDENTITIES, "ck_canonical_identities_status"):
        {"active", "merged", "superseded"},
    (_CANONICAL_IDENTITIES, "ck_canonical_identities_origin"): {"b1_locked", "human"},
    (_PERSON_ALIASES, "ck_person_aliases_alias_class"): {"person_name"},
    (_PERSON_ALIASES, "ck_person_aliases_scope"): {"global_identity"},
    (_PERSON_ALIASES, "ck_person_aliases_status"): {"active", "superseded"},
    (_FIELD_OVERRIDES, "ck_field_overrides_target_table"): {
        "person_roles", "scientific_production_authors", "scientific_productions",
        "research_entities", "external_researchers",
    },
    (_FIELD_OVERRIDES, "ck_field_overrides_field_path"): set(OVERRIDE_FIELDS),
    (_FIELD_OVERRIDES, "ck_field_overrides_scope"): {
        "global_identity", "record", "document", "relationship", "period",
    },
    (_FIELD_OVERRIDES, "ck_field_overrides_scope_context"): {
        "record", "document", "period", "relationship", "global_identity",
        "canonical_identity_key", "canonical_name",
    },
    (_FIELD_OVERRIDES, "ck_field_overrides_value_object"): {"object"},
    (_FIELD_OVERRIDES, "ck_field_overrides_value_version"): set(),
}

_EXPECTED_INDEXES = {
    (_CANONICAL_IDENTITIES, "pk_canonical_identities"): (True, True, False),
    (_CANONICAL_IDENTITIES, "uq_canonical_identities_key"): (True, False, False),
    (_PERSON_ALIASES, "pk_person_aliases"): (True, True, False),
    (_PERSON_ALIASES, "uq_person_aliases_active_normalized_class"): (True, False, True),
    (_FIELD_OVERRIDES, "pk_field_overrides"): (True, True, False),
    (_FIELD_OVERRIDES, "ix_field_overrides_target"): (False, False, False),
    (_FIELD_OVERRIDES, "uq_field_overrides_active_target_field_scope_context"):
        (True, False, True),
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(f"0019 schema assertion failed: {message}")


def assert_schema(connection: Connection) -> None:
    columns = {}
    for table_name in (_CANONICAL_IDENTITIES, _PERSON_ALIASES, _FIELD_OVERRIDES):
        columns[table_name] = tuple(
            tuple(row)
            for row in connection.execute(
                text(
                    """
                    SELECT column_name, data_type, udt_name,
                           character_maximum_length, is_nullable, column_default
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = :table_name
                    ORDER BY ordinal_position
                    """
                ),
                {"table_name": table_name},
            )
        )
    _require(columns == _EXPECTED_COLUMNS, "columns/types/nullability/defaults differ")

    constraints = {
        (row.table_name, row.conname): row.contype
        for row in connection.execute(text(
            """
            SELECT table_row.relname AS table_name,
                   constraint_row.conname,
                   constraint_row.contype
            FROM pg_constraint AS constraint_row
            JOIN pg_class AS table_row ON table_row.oid = constraint_row.conrelid
            JOIN pg_namespace AS namespace_row ON namespace_row.oid = table_row.relnamespace
            WHERE namespace_row.nspname = current_schema()
              AND table_row.relname IN (
                  'canonical_identities', 'person_aliases', 'field_overrides'
              )
            """
        ))
    }
    _require(constraints == _EXPECTED_CONSTRAINT_TYPES, "constraint names/types differ")

    foreign_keys = {}
    for row in connection.execute(text(
        """
        SELECT source_table.relname AS table_name,
               constraint_row.conname,
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
          AND source_table.relname IN (
              'canonical_identities', 'person_aliases', 'field_overrides'
          )
          AND constraint_row.contype = 'f'
        """
    )):
        foreign_keys[(row.table_name, row.conname)] = (
            tuple(row.source_columns), row.target_table, tuple(row.target_columns),
            row.confdeltype, row.condeferrable, row.condeferred,
        )
    _require(foreign_keys == _EXPECTED_FOREIGN_KEYS, "foreign key catalog differs")

    check_definitions = {
        (row.table_name, row.conname): row.definition
        for row in connection.execute(text(
            """
            SELECT table_row.relname AS table_name,
                   constraint_row.conname,
                   pg_get_constraintdef(constraint_row.oid, true) AS definition
            FROM pg_constraint AS constraint_row
            JOIN pg_class AS table_row ON table_row.oid = constraint_row.conrelid
            JOIN pg_namespace AS namespace_row ON namespace_row.oid = table_row.relnamespace
            WHERE namespace_row.nspname = current_schema()
              AND table_row.relname IN (
                  'canonical_identities', 'person_aliases', 'field_overrides'
              )
              AND constraint_row.contype = 'c'
            """
        ))
    }
    _require(
        set(check_definitions) == set(_EXPECTED_CHECK_LITERALS),
        "check constraint definitions are incomplete",
    )
    for key, expected_literals in _EXPECTED_CHECK_LITERALS.items():
        actual_literals = {
            literal for literal in re.findall(r"'([^']*)'", check_definitions[key]) if literal
        }
        _require(
            actual_literals == expected_literals,
            f"closed literals differ for {key[1]}",
        )
    _require(
        "value_version = 1" in check_definitions[
            (_FIELD_OVERRIDES, "ck_field_overrides_value_version")
        ],
        "value version check differs",
    )

    indexes = {}
    index_definitions = {}
    for row in connection.execute(text(
        """
        SELECT table_row.relname AS table_name,
               index_row.relname AS index_name,
               index_catalog.indisunique,
               index_catalog.indisprimary,
               index_catalog.indpred IS NOT NULL AS has_predicate,
               pg_get_indexdef(index_row.oid) AS definition,
               pg_get_expr(index_catalog.indpred, index_catalog.indrelid) AS predicate
        FROM pg_index AS index_catalog
        JOIN pg_class AS table_row ON table_row.oid = index_catalog.indrelid
        JOIN pg_class AS index_row ON index_row.oid = index_catalog.indexrelid
        JOIN pg_namespace AS namespace_row ON namespace_row.oid = table_row.relnamespace
        WHERE namespace_row.nspname = current_schema()
          AND table_row.relname IN (
              'canonical_identities', 'person_aliases', 'field_overrides'
          )
        """
    )):
        key = (row.table_name, row.index_name)
        indexes[key] = (row.indisunique, row.indisprimary, row.has_predicate)
        index_definitions[key] = (row.definition, row.predicate)
    _require(indexes == _EXPECTED_INDEXES, "index names/uniqueness/predicates differ")

    alias_definition, alias_predicate = index_definitions[
        (_PERSON_ALIASES, "uq_person_aliases_active_normalized_class")
    ]
    _require(
        "(alias_normalized, alias_class)" in alias_definition
        and alias_predicate is not None
        and "status" in alias_predicate
        and "active" in alias_predicate,
        "active alias unique index differs",
    )
    override_definition, override_predicate = index_definitions[
        (_FIELD_OVERRIDES, "uq_field_overrides_active_target_field_scope_context")
    ]
    for token in (
        "stable_target_key", "field_path", "scope", "COALESCE(document_key",
        "COALESCE(period_id", "COALESCE(relationship_key",
    ):
        _require(token in override_definition, "active override unique index differs")
    _require(
        override_predicate is not None and "is_active" in override_predicate,
        "active override predicate differs",
    )


def upgrade(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text(
            """
            CREATE TABLE canonical_identities (
                id UUID NOT NULL,
                canonical_identity_key VARCHAR(320) NOT NULL,
                identity_type VARCHAR(40) NOT NULL,
                display_name VARCHAR(220) NOT NULL,
                status VARCHAR(30) NOT NULL DEFAULT 'active',
                origin VARCHAR(30) NOT NULL,
                created_by_decision_id UUID NULL,
                superseded_by_id UUID NULL,
                version INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT pk_canonical_identities PRIMARY KEY (id),
                CONSTRAINT uq_canonical_identities_key UNIQUE (canonical_identity_key),
                CONSTRAINT ck_canonical_identities_identity_type CHECK (
                    identity_type IN (
                        'internal_person', 'external_person', 'unclassified_person'
                    )
                ),
                CONSTRAINT ck_canonical_identities_status CHECK (
                    status IN ('active', 'merged', 'superseded')
                ),
                CONSTRAINT ck_canonical_identities_origin CHECK (
                    origin IN ('b1_locked', 'human')
                ),
                CONSTRAINT fk_canonical_identities_created_by_decision_id
                    FOREIGN KEY (created_by_decision_id) REFERENCES review_decisions (id)
                    ON DELETE RESTRICT,
                CONSTRAINT fk_canonical_identities_superseded_by_id
                    FOREIGN KEY (superseded_by_id) REFERENCES canonical_identities (id)
                    ON DELETE RESTRICT
            )
            """
        ))

        connection.execute(text(
            """
            CREATE TABLE person_aliases (
                id UUID NOT NULL,
                alias_original VARCHAR(320) NOT NULL,
                alias_normalized VARCHAR(320) NOT NULL,
                alias_class VARCHAR(40) NOT NULL DEFAULT 'person_name',
                canonical_identity_id UUID NOT NULL,
                decision_id UUID NOT NULL,
                scope VARCHAR(30) NOT NULL DEFAULT 'global_identity',
                status VARCHAR(30) NOT NULL DEFAULT 'active',
                superseded_by_id UUID NULL,
                version INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT pk_person_aliases PRIMARY KEY (id),
                CONSTRAINT ck_person_aliases_alias_class CHECK (
                    alias_class = 'person_name'
                ),
                CONSTRAINT ck_person_aliases_scope CHECK (
                    scope = 'global_identity'
                ),
                CONSTRAINT ck_person_aliases_status CHECK (
                    status IN ('active', 'superseded')
                ),
                CONSTRAINT fk_person_aliases_canonical_identity_id
                    FOREIGN KEY (canonical_identity_id) REFERENCES canonical_identities (id)
                    ON DELETE RESTRICT,
                CONSTRAINT fk_person_aliases_decision_id
                    FOREIGN KEY (decision_id) REFERENCES review_decisions (id)
                    ON DELETE RESTRICT,
                CONSTRAINT fk_person_aliases_superseded_by_id
                    FOREIGN KEY (superseded_by_id) REFERENCES person_aliases (id)
                    ON DELETE RESTRICT
            )
            """
        ))
        connection.execute(text(
            """
            CREATE UNIQUE INDEX uq_person_aliases_active_normalized_class
                ON person_aliases (alias_normalized, alias_class)
                WHERE status = 'active'
            """
        ))

        connection.execute(text(
            """
            CREATE TABLE field_overrides (
                id UUID NOT NULL,
                review_item_id UUID NOT NULL,
                decision_id UUID NOT NULL,
                stable_target_key VARCHAR(128) NOT NULL,
                target_table VARCHAR(80) NOT NULL,
                target_pk BIGINT NULL,
                field_path VARCHAR(120) NOT NULL,
                value_schema VARCHAR(80) NOT NULL,
                value_version SMALLINT NOT NULL DEFAULT 1,
                projected_value JSONB NOT NULL,
                scope VARCHAR(30) NOT NULL,
                document_key VARCHAR(900) NULL,
                period_id INTEGER NULL,
                relationship_key VARCHAR(320) NULL,
                locked BOOLEAN NOT NULL DEFAULT TRUE,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                valid_from TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                superseded_by_id UUID NULL,
                version INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT pk_field_overrides PRIMARY KEY (id),
                CONSTRAINT fk_field_overrides_review_item_id
                    FOREIGN KEY (review_item_id) REFERENCES review_items (id)
                    ON DELETE RESTRICT,
                CONSTRAINT fk_field_overrides_decision_id
                    FOREIGN KEY (decision_id) REFERENCES review_decisions (id)
                    ON DELETE RESTRICT,
                CONSTRAINT fk_field_overrides_superseded_by_id
                    FOREIGN KEY (superseded_by_id) REFERENCES field_overrides (id)
                    ON DELETE RESTRICT,
                CONSTRAINT ck_field_overrides_target_table CHECK (
                    target_table IN (
                        'person_roles', 'scientific_production_authors',
                        'scientific_productions', 'research_entities',
                        'external_researchers'
                    )
                ),
                CONSTRAINT ck_field_overrides_field_path CHECK (
                    field_path IN (
                        'canonical_identity_key', 'canonical_name', 'product_title',
                        'author_identity_key', 'project_director_identity_key',
                        'project_director_relationship_status', 'external_identity_key',
                        'external_institution', 'scientific_status'
                    )
                ),
                CONSTRAINT ck_field_overrides_scope CHECK (
                    scope IN (
                        'global_identity', 'record', 'document', 'relationship', 'period'
                    )
                ),
                CONSTRAINT ck_field_overrides_scope_context CHECK (
                    (
                        scope = 'record'
                        AND document_key IS NULL
                        AND period_id IS NULL
                        AND relationship_key IS NULL
                    )
                    OR (
                        scope = 'document'
                        AND NULLIF(BTRIM(document_key), '') IS NOT NULL
                        AND period_id IS NULL
                        AND relationship_key IS NULL
                    )
                    OR (
                        scope = 'period'
                        AND document_key IS NULL
                        AND period_id IS NOT NULL
                        AND relationship_key IS NULL
                    )
                    OR (
                        scope = 'relationship'
                        AND document_key IS NULL
                        AND period_id IS NULL
                        AND NULLIF(BTRIM(relationship_key), '') IS NOT NULL
                    )
                    OR (
                        scope = 'global_identity'
                        AND document_key IS NULL
                        AND period_id IS NULL
                        AND relationship_key IS NULL
                        AND field_path IN ('canonical_identity_key', 'canonical_name')
                    )
                ),
                CONSTRAINT ck_field_overrides_value_object CHECK (
                    jsonb_typeof(projected_value) = 'object'
                ),
                CONSTRAINT ck_field_overrides_value_version CHECK (value_version = 1)
            )
            """
        ))
        connection.execute(text(
            """
            CREATE INDEX ix_field_overrides_target
                ON field_overrides (stable_target_key, field_path, is_active)
            """
        ))
        connection.execute(text(
            """
            CREATE UNIQUE INDEX uq_field_overrides_active_target_field_scope_context
                ON field_overrides (
                    stable_target_key,
                    field_path,
                    scope,
                    COALESCE(document_key, ''),
                    COALESCE(period_id, -1),
                    COALESCE(relationship_key, '')
                )
                WHERE is_active
            """
        ))
        assert_schema(connection)


def downgrade(engine: Engine) -> None:
    with engine.begin() as connection:
        later_version = connection.execute(
            text(
                """
                SELECT version
                FROM schema_migrations
                WHERE version = :later_version
                """
            ),
            {"later_version": _LATER_VERSION},
        ).scalar_one_or_none()
        if later_version is not None:
            raise RuntimeError(
                f"cannot downgrade 0019 while later revision is recorded: {later_version}"
            )

        application_name = connection.execute(
            text("SELECT current_setting('application_name')")
        ).scalar_one()
        has_human_decisions = connection.execute(text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM review_decisions
                WHERE actor_type = 'human'
            )
            """
        )).scalar_one()
        if has_human_decisions and application_name != _DISPOSABLE_APPLICATION_NAME:
            raise RuntimeError(
                "cannot downgrade 0019 with human review decisions outside the disposable "
                "migration test database"
            )

        connection.execute(text("DROP TABLE field_overrides"))
        connection.execute(text("DROP TABLE person_aliases"))
        connection.execute(text("DROP TABLE canonical_identities"))
