from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine


VERSION = "20260713_0017_b2b_capabilities"
revision = VERSION
down_revision = "20260712_0016_canonical_identity_fields"

_TABLE = "user_b2b_capabilities"
_CAPABILITY_FUNCTION = "b2b_reject_career_capability"
_CAPABILITY_TRIGGER = "trg_user_b2b_capabilities_reject_career"
_USER_ROLE_FUNCTION = "b2b_reject_career_role_with_capability"
_USER_ROLE_TRIGGER = "trg_users_reject_career_with_b2b_capability"

_EXPECTED_COLUMNS = (
    ("id", "uuid", "uuid", None, "NO", None),
    ("user_id", "integer", "int4", None, "NO", None),
    ("capability", "character varying", "varchar", 40, "NO", None),
    ("is_active", "boolean", "bool", None, "NO", "true"),
    ("approval_reference", "character varying", "varchar", 240, "NO", None),
    ("approved_input_sha256", "character", "bpchar", 64, "NO", None),
    ("assigned_by_identifier", "character varying", "varchar", 180, "NO", None),
    (
        "assigned_at",
        "timestamp with time zone",
        "timestamptz",
        None,
        "NO",
        "CURRENT_TIMESTAMP",
    ),
    ("revoked_at", "timestamp with time zone", "timestamptz", None, "YES", None),
    ("revocation_reason", "text", "text", None, "YES", None),
    ("version", "integer", "int4", None, "NO", "1"),
)

_EXPECTED_CONSTRAINTS = {
    "ck_user_b2b_capabilities_active_not_revoked": (
        "c",
        "CHECK (NOT is_active OR revoked_at IS NULL)",
        ("is_active", "revoked_at"),
        None,
        None,
        (),
        None,
        None,
        None,
        False,
        False,
        True,
    ),
    "ck_user_b2b_capabilities_capability": (
        "c",
        "CHECK (capability::text = ANY (ARRAY['RESEARCH_MANAGER'::character varying, "
        "'SYSTEM_ADMIN'::character varying]::text[]))",
        ("capability",),
        None,
        None,
        (),
        None,
        None,
        None,
        False,
        False,
        True,
    ),
    "ck_user_b2b_capabilities_hash": (
        "c",
        "CHECK (approved_input_sha256 ~ '^[0-9a-f]{64}$'::text)",
        ("approved_input_sha256",),
        None,
        None,
        (),
        None,
        None,
        None,
        False,
        False,
        True,
    ),
    "ck_user_b2b_capabilities_revoked_complete": (
        "c",
        "CHECK (is_active OR revoked_at IS NOT NULL AND "
        "NULLIF(btrim(revocation_reason), ''::text) IS NOT NULL)",
        ("is_active", "revoked_at", "revocation_reason"),
        None,
        None,
        (),
        None,
        None,
        None,
        False,
        False,
        True,
    ),
    "fk_user_b2b_capabilities_user_id_users": (
        "f",
        "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT",
        ("user_id",),
        True,
        "users",
        ("id",),
        "r",
        "a",
        "s",
        False,
        False,
        True,
    ),
    "pk_user_b2b_capabilities": (
        "p",
        "PRIMARY KEY (id)",
        ("id",),
        None,
        None,
        (),
        None,
        None,
        None,
        False,
        False,
        True,
    ),
}

_EXPECTED_INDEXES = {
    "ix_user_b2b_capabilities_capability_active": (
        "btree",
        False,
        False,
        True,
        True,
        2,
        2,
        ("capability", "is_active"),
        None,
        True,
    ),
    "pk_user_b2b_capabilities": (
        "btree",
        True,
        True,
        True,
        True,
        1,
        1,
        ("id",),
        None,
        True,
    ),
    "uq_user_b2b_capabilities_active_user": (
        "btree",
        True,
        False,
        True,
        True,
        1,
        1,
        ("user_id",),
        "is_active",
        True,
    ),
}

_CAPABILITY_FUNCTION_BODY = """
DECLARE
    referenced_role text;
BEGIN
    SELECT users.role::text
    INTO referenced_role
    FROM users
    WHERE users.id = NEW.user_id
    FOR UPDATE;
    IF referenced_role = 'CAREER_MANAGER' AND NEW.is_active
    THEN
        RAISE EXCEPTION
            'CAREER_MANAGER users cannot receive active B2B capabilities'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
"""

_USER_ROLE_FUNCTION_BODY = """
BEGIN
    IF NEW.role::text = 'CAREER_MANAGER'
       AND OLD.role::text IS DISTINCT FROM 'CAREER_MANAGER'
       AND EXISTS (
           SELECT 1
           FROM user_b2b_capabilities
           WHERE user_b2b_capabilities.user_id = NEW.id
             AND user_b2b_capabilities.is_active
       )
    THEN
        RAISE EXCEPTION
            'users with active B2B capability cannot become CAREER_MANAGER'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
"""


def _normalized_sql(value: str) -> str:
    return " ".join(value.split())


def _normalized_body(value: str) -> str:
    return " ".join(value.split())


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(f"0017 schema assertion failed: {message}")


def assert_schema(connection: Connection) -> None:
    columns = tuple(
        tuple(row)
        for row in connection.execute(
            text(
                """
                SELECT column_name, data_type, udt_name,
                       character_maximum_length, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'user_b2b_capabilities'
                ORDER BY ordinal_position
                """
            )
        )
    )
    _require(columns == _EXPECTED_COLUMNS, "columns/types/nullability/defaults differ")

    constraints = {}
    for row in connection.execute(
        text(
            """
            SELECT constraint_row.conname,
                   constraint_row.contype,
                   pg_get_constraintdef(constraint_row.oid, true) AS definition,
                   ARRAY(
                       SELECT attribute_row.attname
                       FROM unnest(constraint_row.conkey) WITH ORDINALITY
                            AS key_row(attnum, position)
                       JOIN pg_attribute AS attribute_row
                         ON attribute_row.attrelid = constraint_row.conrelid
                        AND attribute_row.attnum = key_row.attnum
                       ORDER BY key_row.position
                   ) AS local_columns,
                   target_namespace.nspname = current_schema() AS target_in_current_schema,
                   target_table.relname AS target_table,
                   ARRAY(
                       SELECT attribute_row.attname
                       FROM unnest(constraint_row.confkey) WITH ORDINALITY
                            AS key_row(attnum, position)
                       JOIN pg_attribute AS attribute_row
                         ON attribute_row.attrelid = constraint_row.confrelid
                        AND attribute_row.attnum = key_row.attnum
                       ORDER BY key_row.position
                   ) AS target_columns,
                   CASE WHEN constraint_row.contype = 'f'
                        THEN constraint_row.confdeltype END AS delete_action,
                   CASE WHEN constraint_row.contype = 'f'
                        THEN constraint_row.confupdtype END AS update_action,
                   CASE WHEN constraint_row.contype = 'f'
                        THEN constraint_row.confmatchtype END AS match_type,
                   constraint_row.condeferrable,
                   constraint_row.condeferred,
                   constraint_row.convalidated
            FROM pg_constraint AS constraint_row
            JOIN pg_class AS table_row
              ON table_row.oid = constraint_row.conrelid
            JOIN pg_namespace AS namespace_row
              ON namespace_row.oid = table_row.relnamespace
            LEFT JOIN pg_class AS target_table
              ON target_table.oid = constraint_row.confrelid
            LEFT JOIN pg_namespace AS target_namespace
              ON target_namespace.oid = target_table.relnamespace
            WHERE namespace_row.nspname = current_schema()
              AND table_row.relname = 'user_b2b_capabilities'
            """
        )
    ):
        constraints[row.conname] = (
            row.contype,
            _normalized_sql(row.definition),
            tuple(row.local_columns),
            row.target_in_current_schema,
            row.target_table,
            tuple(row.target_columns),
            row.delete_action,
            row.update_action,
            row.match_type,
            row.condeferrable,
            row.condeferred,
            row.convalidated,
        )
    _require(constraints == _EXPECTED_CONSTRAINTS, "constraint catalog differs")

    indexes = {}
    for row in connection.execute(
        text(
            """
            SELECT index_table.relname AS index_name,
                   access_method.amname AS access_method,
                   index_row.indisunique,
                   index_row.indisprimary,
                   index_row.indisvalid,
                   index_row.indisready,
                   index_row.indnkeyatts,
                   index_row.indnatts,
                   ARRAY(
                       SELECT pg_get_indexdef(
                           index_row.indexrelid, position_row.position, true
                       )
                       FROM generate_series(
                           1, index_row.indnatts::integer
                       ) AS position_row(position)
                       ORDER BY position_row.position
                   ) AS attributes,
                   pg_get_expr(
                       index_row.indpred, index_row.indrelid, true
                   ) AS predicate,
                   index_row.indexprs IS NULL AS has_no_expressions
            FROM pg_index AS index_row
            JOIN pg_class AS table_row ON table_row.oid = index_row.indrelid
            JOIN pg_namespace AS namespace_row
              ON namespace_row.oid = table_row.relnamespace
            JOIN pg_class AS index_table ON index_table.oid = index_row.indexrelid
            JOIN pg_am AS access_method ON access_method.oid = index_table.relam
            WHERE namespace_row.nspname = current_schema()
              AND table_row.relname = 'user_b2b_capabilities'
            """
        )
    ):
        indexes[row.index_name] = (
            row.access_method,
            row.indisunique,
            row.indisprimary,
            row.indisvalid,
            row.indisready,
            row.indnkeyatts,
            row.indnatts,
            tuple(_normalized_sql(value) for value in row.attributes),
            _normalized_sql(row.predicate) if row.predicate is not None else None,
            row.has_no_expressions,
        )
    _require(indexes == _EXPECTED_INDEXES, "index catalog differs")

    functions = {}
    for row in connection.execute(
        text(
            """
            SELECT procedure_row.proname,
                   language_row.lanname AS language_name,
                   pg_get_function_result(procedure_row.oid) AS result_type,
                   pg_get_function_identity_arguments(procedure_row.oid) AS identity_arguments,
                   procedure_row.pronargs,
                   procedure_row.pronargdefaults,
                   procedure_row.provolatile,
                   procedure_row.prosecdef,
                   procedure_row.proleakproof,
                   procedure_row.proisstrict,
                   procedure_row.proparallel,
                   procedure_row.prokind,
                   procedure_row.proconfig,
                   procedure_row.prosrc
            FROM pg_proc AS procedure_row
            JOIN pg_namespace AS namespace_row
              ON namespace_row.oid = procedure_row.pronamespace
            JOIN pg_language AS language_row
              ON language_row.oid = procedure_row.prolang
            WHERE namespace_row.nspname = current_schema()
              AND procedure_row.proname IN (:capability_function, :user_role_function)
            """
        ),
        {
            "capability_function": _CAPABILITY_FUNCTION,
            "user_role_function": _USER_ROLE_FUNCTION,
        },
    ):
        functions[(row.proname, row.identity_arguments)] = (
            row.language_name,
            row.result_type,
            row.identity_arguments,
            row.pronargs,
            row.pronargdefaults,
            row.provolatile,
            row.prosecdef,
            row.proleakproof,
            row.proisstrict,
            row.proparallel,
            row.prokind,
            tuple(row.proconfig) if row.proconfig is not None else None,
            _normalized_body(row.prosrc),
        )
    common_function_properties = (
        "plpgsql",
        "trigger",
        "",
        0,
        0,
        "v",
        False,
        False,
        False,
        "u",
        "f",
        None,
    )
    _require(
        functions
        == {
            (_CAPABILITY_FUNCTION, ""): common_function_properties
            + (_normalized_body(_CAPABILITY_FUNCTION_BODY),),
            (_USER_ROLE_FUNCTION, ""): common_function_properties
            + (_normalized_body(_USER_ROLE_FUNCTION_BODY),),
        },
        "Career guard function catalog/body differs",
    )

    triggers = {}
    for row in connection.execute(
        text(
            """
            SELECT trigger_row.tgname AS trigger_name,
                   table_row.relname AS table_name,
                   trigger_row.tgenabled,
                   trigger_row.tgtype,
                   procedure_row.proname AS function_name,
                   procedure_namespace.nspname = current_schema()
                       AS function_in_current_schema,
                   trigger_row.tgnargs,
                   octet_length(trigger_row.tgargs) AS argument_bytes,
                   trigger_row.tgqual IS NULL AS has_no_when_clause,
                   trigger_row.tgconstraint,
                   trigger_row.tgdeferrable,
                   trigger_row.tginitdeferred,
                   ARRAY(
                       SELECT attribute_row.attname
                       FROM unnest(trigger_row.tgattr::smallint[]) WITH ORDINALITY
                            AS trigger_attribute(attnum, position)
                       JOIN pg_attribute AS attribute_row
                         ON attribute_row.attrelid = trigger_row.tgrelid
                        AND attribute_row.attnum = trigger_attribute.attnum
                       ORDER BY trigger_attribute.position
                   ) AS update_columns
            FROM pg_trigger AS trigger_row
            JOIN pg_class AS table_row ON table_row.oid = trigger_row.tgrelid
            JOIN pg_namespace AS namespace_row
              ON namespace_row.oid = table_row.relnamespace
            JOIN pg_proc AS procedure_row ON procedure_row.oid = trigger_row.tgfoid
            JOIN pg_namespace AS procedure_namespace
              ON procedure_namespace.oid = procedure_row.pronamespace
            WHERE namespace_row.nspname = current_schema()
              AND NOT trigger_row.tgisinternal
              AND trigger_row.tgname IN (:capability_trigger, :user_role_trigger)
            """
        ),
        {
            "capability_trigger": _CAPABILITY_TRIGGER,
            "user_role_trigger": _USER_ROLE_TRIGGER,
        },
    ):
        triggers[(row.trigger_name, row.table_name)] = (
            row.table_name,
            row.tgenabled,
            row.tgtype,
            row.function_name,
            row.function_in_current_schema,
            row.tgnargs,
            row.argument_bytes,
            row.has_no_when_clause,
            row.tgconstraint,
            row.tgdeferrable,
            row.tginitdeferred,
            tuple(row.update_columns),
        )
    _require(
        triggers
        == {
            (_CAPABILITY_TRIGGER, _TABLE): (
                _TABLE,
                "O",
                23,
                _CAPABILITY_FUNCTION,
                True,
                0,
                0,
                True,
                0,
                False,
                False,
                (),
            ),
            (_USER_ROLE_TRIGGER, "users"): (
                "users",
                "O",
                19,
                _USER_ROLE_FUNCTION,
                True,
                0,
                0,
                True,
                0,
                False,
                False,
                ("role",),
            ),
        },
        "Career guard trigger catalog differs",
    )


def upgrade(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE user_b2b_capabilities (
                    id UUID NOT NULL,
                    user_id INTEGER NOT NULL,
                    capability VARCHAR(40) NOT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    approval_reference VARCHAR(240) NOT NULL,
                    approved_input_sha256 CHAR(64) NOT NULL,
                    assigned_by_identifier VARCHAR(180) NOT NULL,
                    assigned_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    revoked_at TIMESTAMPTZ NULL,
                    revocation_reason TEXT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    CONSTRAINT pk_user_b2b_capabilities PRIMARY KEY (id),
                    CONSTRAINT fk_user_b2b_capabilities_user_id_users
                        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE RESTRICT,
                    CONSTRAINT ck_user_b2b_capabilities_capability
                        CHECK (capability IN ('RESEARCH_MANAGER', 'SYSTEM_ADMIN')),
                    CONSTRAINT ck_user_b2b_capabilities_hash
                        CHECK (approved_input_sha256 ~ '^[0-9a-f]{64}$'),
                    CONSTRAINT ck_user_b2b_capabilities_active_not_revoked
                        CHECK (NOT is_active OR revoked_at IS NULL),
                    CONSTRAINT ck_user_b2b_capabilities_revoked_complete
                        CHECK (
                            is_active
                            OR (
                                revoked_at IS NOT NULL
                                AND NULLIF(BTRIM(revocation_reason), '') IS NOT NULL
                            )
                        )
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE UNIQUE INDEX uq_user_b2b_capabilities_active_user
                    ON user_b2b_capabilities (user_id)
                    WHERE is_active
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE INDEX ix_user_b2b_capabilities_capability_active
                    ON user_b2b_capabilities (capability, is_active)
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE FUNCTION b2b_reject_career_capability()
                RETURNS TRIGGER
                LANGUAGE plpgsql
                AS $$
                DECLARE
                    referenced_role text;
                BEGIN
                    SELECT users.role::text
                    INTO referenced_role
                    FROM users
                    WHERE users.id = NEW.user_id
                    FOR UPDATE;
                    IF referenced_role = 'CAREER_MANAGER' AND NEW.is_active
                    THEN
                        RAISE EXCEPTION
                            'CAREER_MANAGER users cannot receive active B2B capabilities'
                            USING ERRCODE = '23514';
                    END IF;
                    RETURN NEW;
                END;
                $$
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TRIGGER trg_user_b2b_capabilities_reject_career
                BEFORE INSERT OR UPDATE ON user_b2b_capabilities
                FOR EACH ROW
                EXECUTE FUNCTION b2b_reject_career_capability()
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE FUNCTION b2b_reject_career_role_with_capability()
                RETURNS TRIGGER
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    IF NEW.role::text = 'CAREER_MANAGER'
                       AND OLD.role::text IS DISTINCT FROM 'CAREER_MANAGER'
                       AND EXISTS (
                           SELECT 1
                           FROM user_b2b_capabilities
                           WHERE user_b2b_capabilities.user_id = NEW.id
                             AND user_b2b_capabilities.is_active
                       )
                    THEN
                        RAISE EXCEPTION
                            'users with active B2B capability cannot become CAREER_MANAGER'
                            USING ERRCODE = '23514';
                    END IF;
                    RETURN NEW;
                END;
                $$
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TRIGGER trg_users_reject_career_with_b2b_capability
                BEFORE UPDATE OF role ON users
                FOR EACH ROW
                EXECUTE FUNCTION b2b_reject_career_role_with_capability()
                """
            )
        )
        assert_schema(connection)


def downgrade(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "DROP TRIGGER IF EXISTS trg_user_b2b_capabilities_reject_career "
                "ON user_b2b_capabilities"
            )
        )
        connection.execute(
            text(
                "DROP TRIGGER IF EXISTS trg_users_reject_career_with_b2b_capability "
                "ON users"
            )
        )
        connection.execute(text("DROP FUNCTION IF EXISTS b2b_reject_career_capability()"))
        connection.execute(
            text("DROP FUNCTION IF EXISTS b2b_reject_career_role_with_capability()")
        )
        connection.execute(text("DROP TABLE user_b2b_capabilities"))
