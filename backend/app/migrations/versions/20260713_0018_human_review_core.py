from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine


VERSION = "20260713_0018_human_review_core"
revision = VERSION
down_revision = "20260713_0017_b2b_capabilities"

_REVIEW_ITEMS = "review_items"
_REVIEW_DECISIONS = "review_decisions"
_APPEND_ONLY_FUNCTION = "b2b_reject_append_only_mutation"
_APPEND_ONLY_TRIGGER = "trg_review_decisions_append_only"
_LATER_VERSIONS = (
    "20260713_0019_human_review_projection",
    "20260713_0020_human_review_audit",
)

_EXPECTED_COLUMNS = {
    _REVIEW_ITEMS: (
        ("id", "uuid", "uuid", None, "NO", None),
        ("case_type", "character varying", "varchar", 60, "NO", None),
        ("stable_target_key", "character varying", "varchar", 128, "NO", None),
        ("target_table", "character varying", "varchar", 80, "NO", None),
        ("target_pk", "bigint", "int8", None, "YES", None),
        ("document_key", "character varying", "varchar", 900, "NO", None),
        ("source_revision", "character varying", "varchar", 120, "YES", None),
        ("source_page", "integer", "int4", None, "YES", None),
        ("source_section", "character varying", "varchar", 120, "NO", None),
        ("row_or_block_id", "text", "text", None, "NO", None),
        ("field_path", "character varying", "varchar", 120, "NO", None),
        ("raw_value_sha256", "character", "bpchar", 64, "NO", None),
        ("period_id", "integer", "int4", None, "YES", None),
        ("relationship_key", "character varying", "varchar", 320, "YES", None),
        (
            "case_status", "character varying", "varchar", 40, "NO",
            "'pending'::character varying",
        ),
        (
            "scientific_status", "character varying", "varchar", 40, "NO",
            "'pending'::character varying",
        ),
        ("automatic_priority", "smallint", "int2", None, "NO", "0"),
        ("manual_priority", "smallint", "int2", None, "YES", None),
        ("possible_kpi_impact", "boolean", "bool", None, "NO", "false"),
        ("current_decision_id", "uuid", "uuid", None, "YES", None),
        ("version", "integer", "int4", None, "NO", "1"),
        (
            "created_at", "timestamp with time zone", "timestamptz", None,
            "NO", "CURRENT_TIMESTAMP",
        ),
        (
            "updated_at", "timestamp with time zone", "timestamptz", None,
            "NO", "CURRENT_TIMESTAMP",
        ),
    ),
    _REVIEW_DECISIONS: (
        ("id", "uuid", "uuid", None, "NO", None),
        ("review_item_id", "uuid", "uuid", None, "NO", None),
        ("sequence", "integer", "int4", None, "NO", None),
        ("decision_type", "character varying", "varchar", 40, "NO", None),
        ("decision_lifecycle", "character varying", "varchar", 30, "NO", None),
        ("scope", "character varying", "varchar", 30, "NO", None),
        ("payload_schema", "character varying", "varchar", 80, "NO", None),
        ("payload_version", "smallint", "int2", None, "NO", "1"),
        ("payload", "jsonb", "jsonb", None, "NO", None),
        ("reason", "text", "text", None, "YES", None),
        ("actor_type", "character varying", "varchar", 30, "NO", None),
        ("actor_user_id", "integer", "int4", None, "YES", None),
        ("actor_identifier", "character varying", "varchar", 180, "NO", None),
        ("actor_capability", "character varying", "varchar", 40, "YES", None),
        (
            "decided_at", "timestamp with time zone", "timestamptz", None,
            "NO", "CURRENT_TIMESTAMP",
        ),
        ("expected_case_version", "integer", "int4", None, "NO", None),
        ("previous_decision_id", "uuid", "uuid", None, "YES", None),
        ("corrects_decision_id", "uuid", "uuid", None, "YES", None),
        ("locks_projection", "boolean", "bool", None, "NO", "false"),
        (
            "created_at", "timestamp with time zone", "timestamptz", None,
            "NO", "CURRENT_TIMESTAMP",
        ),
    ),
}

_EXPECTED_RELATIONS = {
    _REVIEW_ITEMS: (
        "r", "p", "heap", True, False, True, "database_default",
        False, False, False, False, "d", (),
    ),
    _REVIEW_DECISIONS: (
        "r", "p", "heap", True, False, True, "database_default",
        False, False, False, False, "d", (),
    ),
}

_EXPECTED_CONSTRAINTS = {
    (_REVIEW_ITEMS, "pk_review_items"): (
        "p", "PRIMARY KEY (id)", ("id",), None, None, (), None, None,
        None, False, False, True,
    ),
    (_REVIEW_ITEMS, "ck_review_items_case_type"): (
        "c",
        "CHECK (case_type::text = ANY (ARRAY['person_identity'::character varying, "
        "'author_identity'::character varying, 'product'::character varying, "
        "'project_director_relation'::character varying, 'external_identity'::character varying, "
        "'possible_duplicate'::character varying, 'invalid_text'::character varying, "
        "'new_evidence_conflict'::character varying]::text[]))",
        ("case_type",), None, None, (), None, None, None, False, False, True,
    ),
    (_REVIEW_ITEMS, "ck_review_items_target_table"): (
        "c",
        "CHECK (target_table::text = ANY (ARRAY['person_roles'::character varying, "
        "'scientific_production_authors'::character varying, "
        "'scientific_productions'::character varying, 'research_entities'::character varying, "
        "'external_researchers'::character varying]::text[]))",
        ("target_table",), None, None, (), None, None, None, False, False, True,
    ),
    (_REVIEW_ITEMS, "ck_review_items_case_status"): (
        "c",
        "CHECK (case_status::text = ANY (ARRAY['pending'::character varying, "
        "'in_review'::character varying, 'awaiting_gestor_approval'::character varying, "
        "'resolved'::character varying, 'reopened'::character varying, "
        "'conflicted'::character varying, 'superseded'::character varying]::text[]))",
        ("case_status",), None, None, (), None, None, None, False, False, True,
    ),
    (_REVIEW_ITEMS, "ck_review_items_scientific_status"): (
        "c",
        "CHECK (scientific_status::text = ANY (ARRAY['pending'::character varying, "
        "'validated'::character varying, 'rejected'::character varying, "
        "'discarded'::character varying]::text[]))",
        ("scientific_status",), None, None, (), None, None, None, False, False, True,
    ),
    (_REVIEW_ITEMS, "ck_review_items_raw_hash"): (
        "c", "CHECK (raw_value_sha256 ~ '^[0-9a-f]{64}$'::text)",
        ("raw_value_sha256",), None, None, (), None, None, None, False, False, True,
    ),
    (_REVIEW_ITEMS, "fk_review_items_current_decision_id_review_decisions"): (
        "f",
        "FOREIGN KEY (current_decision_id) REFERENCES review_decisions(id) "
        "ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED",
        ("current_decision_id",), True, _REVIEW_DECISIONS, ("id",),
        "r", "a", "s", True, True, True,
    ),
    (_REVIEW_DECISIONS, "pk_review_decisions"): (
        "p", "PRIMARY KEY (id)", ("id",), None, None, (), None, None,
        None, False, False, True,
    ),
    (_REVIEW_DECISIONS, "fk_review_decisions_review_item_id_review_items"): (
        "f", "FOREIGN KEY (review_item_id) REFERENCES review_items(id) ON DELETE RESTRICT",
        ("review_item_id",), True, _REVIEW_ITEMS, ("id",),
        "r", "a", "s", False, False, True,
    ),
    (_REVIEW_DECISIONS, "fk_review_decisions_actor_user_id_users"): (
        "f", "FOREIGN KEY (actor_user_id) REFERENCES users(id) ON DELETE RESTRICT",
        ("actor_user_id",), True, "users", ("id",),
        "r", "a", "s", False, False, True,
    ),
    (_REVIEW_DECISIONS, "fk_review_decisions_previous_decision_id"): (
        "f", "FOREIGN KEY (previous_decision_id) REFERENCES review_decisions(id) ON DELETE RESTRICT",
        ("previous_decision_id",), True, _REVIEW_DECISIONS, ("id",),
        "r", "a", "s", False, False, True,
    ),
    (_REVIEW_DECISIONS, "fk_review_decisions_corrects_decision_id"): (
        "f", "FOREIGN KEY (corrects_decision_id) REFERENCES review_decisions(id) ON DELETE RESTRICT",
        ("corrects_decision_id",), True, _REVIEW_DECISIONS, ("id",),
        "r", "a", "s", False, False, True,
    ),
    (_REVIEW_DECISIONS, "uq_review_decisions_item_sequence"): (
        "u", "UNIQUE (review_item_id, sequence)",
        ("review_item_id", "sequence"), None, None, (), None, None,
        None, False, False, True,
    ),
    (_REVIEW_DECISIONS, "ck_review_decisions_decision_type"): (
        "c",
        "CHECK (decision_type::text = ANY (ARRAY['validated'::character varying, "
        "'corrected'::character varying, 'linked'::character varying, "
        "'merged'::character varying, 'maintained_separate'::character varying, "
        "'separated'::character varying, 'rejected'::character varying, "
        "'discarded'::character varying, 'maintained'::character varying, "
        "'reverted'::character varying]::text[]))",
        ("decision_type",), None, None, (), None, None, None, False, False, True,
    ),
    (_REVIEW_DECISIONS, "ck_review_decisions_lifecycle"): (
        "c",
        "CHECK (decision_lifecycle::text = ANY (ARRAY['proposed'::character varying, "
        "'approved'::character varying, 'declined'::character varying, "
        "'superseded'::character varying]::text[]))",
        ("decision_lifecycle",), None, None, (), None, None, None, False, False, True,
    ),
    (_REVIEW_DECISIONS, "ck_review_decisions_scope"): (
        "c",
        "CHECK (scope::text = ANY (ARRAY['global_identity'::character varying, "
        "'record'::character varying, 'document'::character varying, "
        "'relationship'::character varying, 'period'::character varying]::text[]))",
        ("scope",), None, None, (), None, None, None, False, False, True,
    ),
    (_REVIEW_DECISIONS, "ck_review_decisions_payload_object"): (
        "c", "CHECK (jsonb_typeof(payload) = 'object'::text)",
        ("payload",), None, None, (), None, None, None, False, False, True,
    ),
    (_REVIEW_DECISIONS, "ck_review_decisions_payload_version"): (
        "c", "CHECK (payload_version = 1)",
        ("payload_version",), None, None, (), None, None, None, False, False, True,
    ),
    (_REVIEW_DECISIONS, "ck_review_decisions_positive_sequence"): (
        "c", "CHECK (sequence > 0)",
        ("sequence",), None, None, (), None, None, None, False, False, True,
    ),
    (_REVIEW_DECISIONS, "ck_review_decisions_actor_shape"): (
        "c",
        "CHECK (actor_type::text = 'human'::text AND actor_user_id IS NOT NULL AND "
        "(actor_capability::text = ANY (ARRAY['RESEARCH_MANAGER'::character varying, "
        "'SYSTEM_ADMIN'::character varying]::text[])) OR actor_type::text = 'legacy'::text "
        "AND actor_user_id IS NULL AND actor_capability IS NULL AND "
        "NULLIF(btrim(actor_identifier::text), ''::text) IS NOT NULL)",
        ("actor_type", "actor_user_id", "actor_capability", "actor_identifier"),
        None, None, (), None, None, None, False, False, True,
    ),
    (_REVIEW_DECISIONS, "ck_review_decisions_approved_locks_projection"): (
        "c", "CHECK (decision_lifecycle::text <> 'approved'::text OR locks_projection)",
        ("decision_lifecycle", "locks_projection"),
        None, None, (), None, None, None, False, False, True,
    ),
    (_REVIEW_DECISIONS, "ck_review_decisions_previous_not_self"): (
        "c", "CHECK (previous_decision_id IS NULL OR previous_decision_id <> id)",
        ("previous_decision_id", "id"),
        None, None, (), None, None, None, False, False, True,
    ),
    (_REVIEW_DECISIONS, "ck_review_decisions_corrects_not_self"): (
        "c", "CHECK (corrects_decision_id IS NULL OR corrects_decision_id <> id)",
        ("corrects_decision_id", "id"),
        None, None, (), None, None, None, False, False, True,
    ),
}

_EXPECTED_CONSTRAINTS = {
    key: value + (True, 0, value[0] != "c", True)
    for key, value in _EXPECTED_CONSTRAINTS.items()
}

_EXPECTED_INDEXES = {
    (_REVIEW_ITEMS, "pk_review_items"): (
        "btree", True, True, True, True, False, 1, 1, ("id",), None, True,
    ),
    (_REVIEW_ITEMS, "uq_review_items_active_case_target"): (
        "btree", True, False, True, True, False, 2, 2,
        ("case_type", "stable_target_key"),
        "case_status::text = ANY (ARRAY['pending'::character varying, "
        "'in_review'::character varying, 'awaiting_gestor_approval'::character varying, "
        "'reopened'::character varying, 'conflicted'::character varying]::text[])",
        True,
    ),
    (_REVIEW_ITEMS, "ix_review_items_queue"): (
        "btree", False, False, True, True, False, 4, 4,
        ("case_type", "case_status", "automatic_priority", "created_at"), None, True,
    ),
    (_REVIEW_ITEMS, "ix_review_items_target"): (
        "btree", False, False, True, True, False, 2, 2,
        ("target_table", "target_pk"), None, True,
    ),
    (_REVIEW_ITEMS, "ix_review_items_document"): (
        "btree", False, False, True, True, False, 1, 1, ("document_key",), None, True,
    ),
    (_REVIEW_ITEMS, "ix_review_items_period"): (
        "btree", False, False, True, True, False, 1, 1, ("period_id",), None, True,
    ),
    (_REVIEW_ITEMS, "ix_review_items_current_decision"): (
        "btree", False, False, True, True, False, 1, 1, ("current_decision_id",), None, True,
    ),
    (_REVIEW_DECISIONS, "pk_review_decisions"): (
        "btree", True, True, True, True, False, 1, 1, ("id",), None, True,
    ),
    (_REVIEW_DECISIONS, "uq_review_decisions_item_sequence"): (
        "btree", True, False, True, True, False, 2, 2,
        ("review_item_id", "sequence"), None, True,
    ),
    (_REVIEW_DECISIONS, "ix_review_decisions_case"): (
        "btree", False, False, True, True, False, 2, 2,
        ("review_item_id", "decided_at"), None, True,
    ),
    (_REVIEW_DECISIONS, "ix_review_decisions_lifecycle"): (
        "btree", False, False, True, True, False, 1, 1,
        ("decision_lifecycle",), None, True,
    ),
    (_REVIEW_DECISIONS, "ix_review_decisions_actor"): (
        "btree", False, False, True, True, False, 2, 2,
        ("actor_user_id", "decided_at"), None, True,
    ),
    (_REVIEW_DECISIONS, "ix_review_decisions_previous"): (
        "btree", False, False, True, True, False, 1, 1,
        ("previous_decision_id",), None, True,
    ),
    (_REVIEW_DECISIONS, "ix_review_decisions_corrects"): (
        "btree", False, False, True, True, False, 1, 1,
        ("corrects_decision_id",), None, True,
    ),
}

_EXPECTED_INDEX_DEFINITIONS = {
    (_REVIEW_ITEMS, "pk_review_items"):
        "CREATE UNIQUE INDEX pk_review_items ON review_items USING btree (id)",
    (_REVIEW_ITEMS, "uq_review_items_active_case_target"):
        "CREATE UNIQUE INDEX uq_review_items_active_case_target ON review_items USING btree "
        "(case_type, stable_target_key) WHERE case_status::text = ANY (ARRAY["
        "'pending'::character varying, 'in_review'::character varying, "
        "'awaiting_gestor_approval'::character varying, 'reopened'::character varying, "
        "'conflicted'::character varying]::text[])",
    (_REVIEW_ITEMS, "ix_review_items_queue"):
        "CREATE INDEX ix_review_items_queue ON review_items USING btree "
        "(case_type, case_status, automatic_priority, created_at)",
    (_REVIEW_ITEMS, "ix_review_items_target"):
        "CREATE INDEX ix_review_items_target ON review_items USING btree "
        "(target_table, target_pk)",
    (_REVIEW_ITEMS, "ix_review_items_document"):
        "CREATE INDEX ix_review_items_document ON review_items USING btree (document_key)",
    (_REVIEW_ITEMS, "ix_review_items_period"):
        "CREATE INDEX ix_review_items_period ON review_items USING btree (period_id)",
    (_REVIEW_ITEMS, "ix_review_items_current_decision"):
        "CREATE INDEX ix_review_items_current_decision ON review_items USING btree "
        "(current_decision_id)",
    (_REVIEW_DECISIONS, "pk_review_decisions"):
        "CREATE UNIQUE INDEX pk_review_decisions ON review_decisions USING btree (id)",
    (_REVIEW_DECISIONS, "uq_review_decisions_item_sequence"):
        "CREATE UNIQUE INDEX uq_review_decisions_item_sequence ON review_decisions USING btree "
        "(review_item_id, sequence)",
    (_REVIEW_DECISIONS, "ix_review_decisions_case"):
        "CREATE INDEX ix_review_decisions_case ON review_decisions USING btree "
        "(review_item_id, decided_at)",
    (_REVIEW_DECISIONS, "ix_review_decisions_lifecycle"):
        "CREATE INDEX ix_review_decisions_lifecycle ON review_decisions USING btree "
        "(decision_lifecycle)",
    (_REVIEW_DECISIONS, "ix_review_decisions_actor"):
        "CREATE INDEX ix_review_decisions_actor ON review_decisions USING btree "
        "(actor_user_id, decided_at)",
    (_REVIEW_DECISIONS, "ix_review_decisions_previous"):
        "CREATE INDEX ix_review_decisions_previous ON review_decisions USING btree "
        "(previous_decision_id)",
    (_REVIEW_DECISIONS, "ix_review_decisions_corrects"):
        "CREATE INDEX ix_review_decisions_corrects ON review_decisions USING btree "
        "(corrects_decision_id)",
}

_EXPECTED_INDEXES = {
    key: value + (
        False,
        True,
        False,
        False,
        True,
        False,
        "i",
        "p",
        False,
        False,
        True,
        "database_default",
        (),
        _EXPECTED_INDEX_DEFINITIONS[key],
    )
    for key, value in _EXPECTED_INDEXES.items()
}

_APPEND_ONLY_FUNCTION_BODY = """
BEGIN
    RAISE EXCEPTION
        'review_decisions is append-only'
        USING ERRCODE = '55000';
END;
"""


def _normalized_sql(value: str) -> str:
    return " ".join(value.split())


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(f"0018 schema assertion failed: {message}")


def assert_schema(connection: Connection) -> None:
    columns = {}
    for table_name in (_REVIEW_ITEMS, _REVIEW_DECISIONS):
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

    relations = {}
    for row in connection.execute(
        text(
            """
            SELECT table_row.relname AS table_name,
                   table_row.relkind,
                   table_row.relpersistence,
                   access_method.amname AS access_method,
                   table_row.reloftype = 0 AS is_not_typed_table,
                   table_row.relispartition,
                   table_row.relpartbound IS NULL AS has_no_partition_bound,
                   CASE
                       WHEN table_row.reltablespace = 0 THEN 'database_default'
                       ELSE tablespace_row.spcname
                   END AS tablespace_name,
                   table_row.relhassubclass,
                   table_row.relhasrules,
                   table_row.relrowsecurity,
                   table_row.relforcerowsecurity,
                   table_row.relreplident,
                   ARRAY(
                       SELECT option_row.option
                       FROM unnest(
                           COALESCE(table_row.reloptions, ARRAY[]::text[])
                       ) AS option_row(option)
                       ORDER BY option_row.option
                   ) AS relation_options
            FROM pg_class AS table_row
            JOIN pg_namespace AS namespace_row
              ON namespace_row.oid = table_row.relnamespace
            LEFT JOIN pg_am AS access_method
              ON access_method.oid = table_row.relam
            LEFT JOIN pg_tablespace AS tablespace_row
              ON tablespace_row.oid = table_row.reltablespace
            WHERE namespace_row.nspname = current_schema()
              AND table_row.relname IN (:review_items, :review_decisions)
            """
        ),
        {"review_items": _REVIEW_ITEMS, "review_decisions": _REVIEW_DECISIONS},
    ):
        relations[row.table_name] = (
            row.relkind,
            row.relpersistence,
            row.access_method,
            row.is_not_typed_table,
            row.relispartition,
            row.has_no_partition_bound,
            row.tablespace_name,
            row.relhassubclass,
            row.relhasrules,
            row.relrowsecurity,
            row.relforcerowsecurity,
            row.relreplident,
            tuple(row.relation_options),
        )
    _require(relations == _EXPECTED_RELATIONS, "table relation catalog differs")

    constraints = {}
    for row in connection.execute(
        text(
            """
            SELECT table_row.relname AS table_name,
                   constraint_row.conname,
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
                   constraint_row.convalidated,
                   constraint_row.conislocal,
                   constraint_row.coninhcount,
                   constraint_row.connoinherit,
                   constraint_row.conparentid = 0 AS has_no_parent_constraint
            FROM pg_constraint AS constraint_row
            JOIN pg_class AS table_row ON table_row.oid = constraint_row.conrelid
            JOIN pg_namespace AS namespace_row ON namespace_row.oid = table_row.relnamespace
            LEFT JOIN pg_class AS target_table ON target_table.oid = constraint_row.confrelid
            LEFT JOIN pg_namespace AS target_namespace
              ON target_namespace.oid = target_table.relnamespace
            WHERE namespace_row.nspname = current_schema()
              AND table_row.relname IN (:review_items, :review_decisions)
            """
        ),
        {"review_items": _REVIEW_ITEMS, "review_decisions": _REVIEW_DECISIONS},
    ):
        constraints[(row.table_name, row.conname)] = (
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
            row.conislocal,
            row.coninhcount,
            row.connoinherit,
            row.has_no_parent_constraint,
        )
    _require(constraints == _EXPECTED_CONSTRAINTS, "constraint catalog differs")

    inheritance_edges = tuple(
        tuple(row)
        for row in connection.execute(
            text(
                """
                SELECT child_namespace.nspname = current_schema()
                           AS child_in_current_schema,
                       child_table.relname AS child_table,
                       parent_namespace.nspname = current_schema()
                           AS parent_in_current_schema,
                       parent_table.relname AS parent_table,
                       inheritance_row.inhseqno,
                       inheritance_row.inhdetachpending
                FROM pg_inherits AS inheritance_row
                JOIN pg_class AS child_table
                  ON child_table.oid = inheritance_row.inhrelid
                JOIN pg_namespace AS child_namespace
                  ON child_namespace.oid = child_table.relnamespace
                JOIN pg_class AS parent_table
                  ON parent_table.oid = inheritance_row.inhparent
                JOIN pg_namespace AS parent_namespace
                  ON parent_namespace.oid = parent_table.relnamespace
                WHERE (
                    child_namespace.nspname = current_schema()
                    AND child_table.relname IN (:review_items, :review_decisions)
                ) OR (
                    parent_namespace.nspname = current_schema()
                    AND parent_table.relname IN (:review_items, :review_decisions)
                )
                ORDER BY child_namespace.nspname, child_table.relname,
                         parent_namespace.nspname, parent_table.relname,
                         inheritance_row.inhseqno
                """
            ),
            {"review_items": _REVIEW_ITEMS, "review_decisions": _REVIEW_DECISIONS},
        )
    )
    _require(inheritance_edges == (), "table inheritance catalog differs")

    policies = tuple(
        tuple(row)
        for row in connection.execute(
            text(
                """
                SELECT table_row.relname AS table_name,
                       policy_row.polname,
                       policy_row.polcmd,
                       policy_row.polpermissive,
                       ARRAY(
                           SELECT COALESCE(role_row.rolname, 'PUBLIC')
                           FROM unnest(policy_row.polroles) AS policy_role(role_oid)
                           LEFT JOIN pg_roles AS role_row
                             ON role_row.oid = policy_role.role_oid
                           ORDER BY COALESCE(role_row.rolname, 'PUBLIC')
                       ) AS role_names,
                       pg_get_expr(
                           policy_row.polqual, policy_row.polrelid, true
                       ) AS using_expression,
                       pg_get_expr(
                           policy_row.polwithcheck, policy_row.polrelid, true
                       ) AS check_expression
                FROM pg_policy AS policy_row
                JOIN pg_class AS table_row
                  ON table_row.oid = policy_row.polrelid
                JOIN pg_namespace AS namespace_row
                  ON namespace_row.oid = table_row.relnamespace
                WHERE namespace_row.nspname = current_schema()
                  AND table_row.relname IN (:review_items, :review_decisions)
                ORDER BY table_row.relname, policy_row.polname
                """
            ),
            {"review_items": _REVIEW_ITEMS, "review_decisions": _REVIEW_DECISIONS},
        )
    )
    _require(policies == (), "table policy catalog differs")

    indexes = {}
    for row in connection.execute(
        text(
            """
            SELECT table_row.relname AS table_name,
                   index_table.relname AS index_name,
                   access_method.amname AS access_method,
                   index_row.indisunique,
                   index_row.indisprimary,
                   index_row.indisvalid,
                   index_row.indisready,
                   index_row.indnullsnotdistinct,
                   index_row.indnkeyatts,
                   index_row.indnatts,
                   ARRAY(
                       SELECT pg_get_indexdef(index_row.indexrelid, position_row.position, true)
                       FROM generate_series(1, index_row.indnatts::integer)
                            AS position_row(position)
                       ORDER BY position_row.position
                   ) AS attributes,
                   pg_get_expr(index_row.indpred, index_row.indrelid, true) AS predicate,
                   index_row.indexprs IS NULL AS has_no_expressions,
                   index_row.indisexclusion,
                   index_row.indimmediate,
                   index_row.indisclustered,
                   index_row.indcheckxmin,
                   index_row.indislive,
                   index_row.indisreplident,
                   index_table.relkind AS index_relation_kind,
                   index_table.relpersistence AS index_persistence,
                   index_table.relispartition AS index_is_partition,
                   index_table.relhassubclass AS index_has_subclass,
                   index_table.relpartbound IS NULL
                       AS index_has_no_partition_bound,
                   CASE
                       WHEN index_table.reltablespace = 0 THEN 'database_default'
                       ELSE tablespace_row.spcname
                   END AS index_tablespace,
                   ARRAY(
                       SELECT option_row.option
                       FROM unnest(
                           COALESCE(index_table.reloptions, ARRAY[]::text[])
                       ) AS option_row(option)
                       ORDER BY option_row.option
                   ) AS index_options,
                   pg_get_indexdef(index_row.indexrelid, 0, true)
                       AS full_definition
            FROM pg_index AS index_row
            JOIN pg_class AS table_row ON table_row.oid = index_row.indrelid
            JOIN pg_namespace AS namespace_row ON namespace_row.oid = table_row.relnamespace
            JOIN pg_class AS index_table ON index_table.oid = index_row.indexrelid
            JOIN pg_am AS access_method ON access_method.oid = index_table.relam
            LEFT JOIN pg_tablespace AS tablespace_row
              ON tablespace_row.oid = index_table.reltablespace
            WHERE namespace_row.nspname = current_schema()
              AND table_row.relname IN (:review_items, :review_decisions)
            """
        ),
        {"review_items": _REVIEW_ITEMS, "review_decisions": _REVIEW_DECISIONS},
    ):
        indexes[(row.table_name, row.index_name)] = (
            row.access_method,
            row.indisunique,
            row.indisprimary,
            row.indisvalid,
            row.indisready,
            row.indnullsnotdistinct,
            row.indnkeyatts,
            row.indnatts,
            tuple(_normalized_sql(value) for value in row.attributes),
            _normalized_sql(row.predicate) if row.predicate is not None else None,
            row.has_no_expressions,
            row.indisexclusion,
            row.indimmediate,
            row.indisclustered,
            row.indcheckxmin,
            row.indislive,
            row.indisreplident,
            row.index_relation_kind,
            row.index_persistence,
            row.index_is_partition,
            row.index_has_subclass,
            row.index_has_no_partition_bound,
            row.index_tablespace,
            tuple(row.index_options),
            _normalized_sql(row.full_definition),
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
            JOIN pg_namespace AS namespace_row ON namespace_row.oid = procedure_row.pronamespace
            JOIN pg_language AS language_row ON language_row.oid = procedure_row.prolang
            WHERE namespace_row.nspname = current_schema()
              AND procedure_row.proname = :function_name
            """
        ),
        {"function_name": _APPEND_ONLY_FUNCTION},
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
            _normalized_sql(row.prosrc),
        )
    _require(
        functions
        == {
            (_APPEND_ONLY_FUNCTION, ""): (
                "plpgsql", "trigger", "", 0, 0, "v", False, False, False,
                "u", "f", None, _normalized_sql(_APPEND_ONLY_FUNCTION_BODY),
            )
        },
        "append-only function catalog/body differs",
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
            JOIN pg_namespace AS namespace_row ON namespace_row.oid = table_row.relnamespace
            JOIN pg_proc AS procedure_row ON procedure_row.oid = trigger_row.tgfoid
            JOIN pg_namespace AS procedure_namespace
              ON procedure_namespace.oid = procedure_row.pronamespace
            WHERE namespace_row.nspname = current_schema()
              AND NOT trigger_row.tgisinternal
              AND (
                  table_row.relname IN (:review_items, :review_decisions)
                  OR trigger_row.tgname = :trigger_name
              )
            """
        ),
        {
            "review_items": _REVIEW_ITEMS,
            "review_decisions": _REVIEW_DECISIONS,
            "trigger_name": _APPEND_ONLY_TRIGGER,
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
            (_APPEND_ONLY_TRIGGER, _REVIEW_DECISIONS): (
                _REVIEW_DECISIONS, "O", 58, _APPEND_ONLY_FUNCTION, True,
                0, 0, True, 0, False, False, (),
            )
        },
        "append-only trigger catalog differs",
    )


def upgrade(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text(
            """
            CREATE TABLE review_items (
                id UUID NOT NULL,
                case_type VARCHAR(60) NOT NULL,
                stable_target_key VARCHAR(128) NOT NULL,
                target_table VARCHAR(80) NOT NULL,
                target_pk BIGINT NULL,
                document_key VARCHAR(900) NOT NULL,
                source_revision VARCHAR(120) NULL,
                source_page INTEGER NULL,
                source_section VARCHAR(120) NOT NULL,
                row_or_block_id TEXT NOT NULL,
                field_path VARCHAR(120) NOT NULL,
                raw_value_sha256 CHAR(64) NOT NULL,
                period_id INTEGER NULL,
                relationship_key VARCHAR(320) NULL,
                case_status VARCHAR(40) NOT NULL DEFAULT 'pending',
                scientific_status VARCHAR(40) NOT NULL DEFAULT 'pending',
                automatic_priority SMALLINT NOT NULL DEFAULT 0,
                manual_priority SMALLINT NULL,
                possible_kpi_impact BOOLEAN NOT NULL DEFAULT FALSE,
                current_decision_id UUID NULL,
                version INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT pk_review_items PRIMARY KEY (id),
                CONSTRAINT ck_review_items_case_type CHECK (
                    case_type IN (
                        'person_identity', 'author_identity', 'product',
                        'project_director_relation', 'external_identity',
                        'possible_duplicate', 'invalid_text', 'new_evidence_conflict'
                    )
                ),
                CONSTRAINT ck_review_items_target_table CHECK (
                    target_table IN (
                        'person_roles', 'scientific_production_authors',
                        'scientific_productions', 'research_entities',
                        'external_researchers'
                    )
                ),
                CONSTRAINT ck_review_items_case_status CHECK (
                    case_status IN (
                        'pending', 'in_review', 'awaiting_gestor_approval',
                        'resolved', 'reopened', 'conflicted', 'superseded'
                    )
                ),
                CONSTRAINT ck_review_items_scientific_status CHECK (
                    scientific_status IN ('pending', 'validated', 'rejected', 'discarded')
                ),
                CONSTRAINT ck_review_items_raw_hash CHECK (
                    raw_value_sha256 ~ '^[0-9a-f]{64}$'
                )
            )
            """
        ))
        connection.execute(text(
            """
            CREATE UNIQUE INDEX uq_review_items_active_case_target
                ON review_items (case_type, stable_target_key)
                WHERE case_status IN (
                    'pending', 'in_review', 'awaiting_gestor_approval',
                    'reopened', 'conflicted'
                )
            """
        ))
        connection.execute(text(
            "CREATE INDEX ix_review_items_queue ON review_items "
            "(case_type, case_status, automatic_priority, created_at)"
        ))
        connection.execute(text(
            "CREATE INDEX ix_review_items_target ON review_items (target_table, target_pk)"
        ))
        connection.execute(text(
            "CREATE INDEX ix_review_items_document ON review_items (document_key)"
        ))
        connection.execute(text(
            "CREATE INDEX ix_review_items_period ON review_items (period_id)"
        ))
        connection.execute(text(
            "CREATE INDEX ix_review_items_current_decision ON review_items (current_decision_id)"
        ))

        connection.execute(text(
            """
            CREATE TABLE review_decisions (
                id UUID NOT NULL,
                review_item_id UUID NOT NULL,
                sequence INTEGER NOT NULL,
                decision_type VARCHAR(40) NOT NULL,
                decision_lifecycle VARCHAR(30) NOT NULL,
                scope VARCHAR(30) NOT NULL,
                payload_schema VARCHAR(80) NOT NULL,
                payload_version SMALLINT NOT NULL DEFAULT 1,
                payload JSONB NOT NULL,
                reason TEXT NULL,
                actor_type VARCHAR(30) NOT NULL,
                actor_user_id INTEGER NULL,
                actor_identifier VARCHAR(180) NOT NULL,
                actor_capability VARCHAR(40) NULL,
                decided_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                expected_case_version INTEGER NOT NULL,
                previous_decision_id UUID NULL,
                corrects_decision_id UUID NULL,
                locks_projection BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT pk_review_decisions PRIMARY KEY (id),
                CONSTRAINT fk_review_decisions_review_item_id_review_items
                    FOREIGN KEY (review_item_id) REFERENCES review_items (id)
                    ON DELETE RESTRICT,
                CONSTRAINT fk_review_decisions_actor_user_id_users
                    FOREIGN KEY (actor_user_id) REFERENCES users (id)
                    ON DELETE RESTRICT,
                CONSTRAINT fk_review_decisions_previous_decision_id
                    FOREIGN KEY (previous_decision_id) REFERENCES review_decisions (id)
                    ON DELETE RESTRICT,
                CONSTRAINT fk_review_decisions_corrects_decision_id
                    FOREIGN KEY (corrects_decision_id) REFERENCES review_decisions (id)
                    ON DELETE RESTRICT,
                CONSTRAINT uq_review_decisions_item_sequence
                    UNIQUE (review_item_id, sequence),
                CONSTRAINT ck_review_decisions_decision_type CHECK (
                    decision_type IN (
                        'validated', 'corrected', 'linked', 'merged',
                        'maintained_separate', 'separated', 'rejected',
                        'discarded', 'maintained', 'reverted'
                    )
                ),
                CONSTRAINT ck_review_decisions_lifecycle CHECK (
                    decision_lifecycle IN ('proposed', 'approved', 'declined', 'superseded')
                ),
                CONSTRAINT ck_review_decisions_scope CHECK (
                    scope IN ('global_identity', 'record', 'document', 'relationship', 'period')
                ),
                CONSTRAINT ck_review_decisions_payload_object CHECK (
                    jsonb_typeof(payload) = 'object'
                ),
                CONSTRAINT ck_review_decisions_payload_version CHECK (payload_version = 1),
                CONSTRAINT ck_review_decisions_positive_sequence CHECK (sequence > 0),
                CONSTRAINT ck_review_decisions_actor_shape CHECK (
                    (
                        actor_type = 'human'
                        AND actor_user_id IS NOT NULL
                        AND actor_capability IN ('RESEARCH_MANAGER', 'SYSTEM_ADMIN')
                    )
                    OR (
                        actor_type = 'legacy'
                        AND actor_user_id IS NULL
                        AND actor_capability IS NULL
                        AND NULLIF(BTRIM(actor_identifier), '') IS NOT NULL
                    )
                ),
                CONSTRAINT ck_review_decisions_approved_locks_projection CHECK (
                    decision_lifecycle <> 'approved' OR locks_projection
                ),
                CONSTRAINT ck_review_decisions_previous_not_self CHECK (
                    previous_decision_id IS NULL OR previous_decision_id <> id
                ),
                CONSTRAINT ck_review_decisions_corrects_not_self CHECK (
                    corrects_decision_id IS NULL OR corrects_decision_id <> id
                )
            )
            """
        ))
        connection.execute(text(
            "CREATE INDEX ix_review_decisions_case ON review_decisions (review_item_id, decided_at)"
        ))
        connection.execute(text(
            "CREATE INDEX ix_review_decisions_lifecycle ON review_decisions (decision_lifecycle)"
        ))
        connection.execute(text(
            "CREATE INDEX ix_review_decisions_actor ON review_decisions (actor_user_id, decided_at)"
        ))
        connection.execute(text(
            "CREATE INDEX ix_review_decisions_previous ON review_decisions (previous_decision_id)"
        ))
        connection.execute(text(
            "CREATE INDEX ix_review_decisions_corrects ON review_decisions (corrects_decision_id)"
        ))
        connection.execute(text(
            """
            ALTER TABLE review_items
            ADD CONSTRAINT fk_review_items_current_decision_id_review_decisions
                FOREIGN KEY (current_decision_id) REFERENCES review_decisions (id)
                ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED
            """
        ))
        connection.execute(text(
            """
            CREATE FUNCTION b2b_reject_append_only_mutation()
            RETURNS TRIGGER
            LANGUAGE plpgsql
            AS $$
            BEGIN
                RAISE EXCEPTION
                    'review_decisions is append-only'
                    USING ERRCODE = '55000';
            END;
            $$
            """
        ))
        connection.execute(text(
            """
            CREATE TRIGGER trg_review_decisions_append_only
            BEFORE UPDATE OR DELETE OR TRUNCATE ON review_decisions
            FOR EACH STATEMENT
            EXECUTE FUNCTION b2b_reject_append_only_mutation()
            """
        ))
        assert_schema(connection)


def downgrade(engine: Engine) -> None:
    with engine.begin() as connection:
        later_versions = tuple(
            connection.execute(
                text(
                    """
                    SELECT version
                    FROM schema_migrations
                    WHERE version IN (:version_0019, :version_0020)
                    ORDER BY version
                    """
                ),
                {
                    "version_0019": _LATER_VERSIONS[0],
                    "version_0020": _LATER_VERSIONS[1],
                },
            ).scalars()
        )
        if later_versions:
            raise RuntimeError(
                "cannot downgrade 0018 while later revisions are recorded: "
                + ", ".join(later_versions)
            )
        connection.execute(text(
            "ALTER TABLE review_items DROP CONSTRAINT "
            "fk_review_items_current_decision_id_review_decisions"
        ))
        connection.execute(text(
            "DROP TRIGGER trg_review_decisions_append_only ON review_decisions"
        ))
        connection.execute(text("DROP TABLE review_decisions"))
        connection.execute(text("DROP TABLE review_items"))
        connection.execute(text("DROP FUNCTION b2b_reject_append_only_mutation()"))
