from __future__ import annotations

import hashlib
import logging
from contextlib import contextmanager
from collections.abc import Iterator

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection, Engine


VERSION = "20260827_0022_scoped_human_review_authorization"
revision = VERSION
DOWN_REVISION = "20260718_0021_human_review_scientific_decision_audit"
down_revision = DOWN_REVISION

UNRESOLVED_REASONS = (
    "unresolved_no_persisted_scope",
    "unresolved_cross_faculty",
    "unresolved_missing_target",
)

_BATCH_SIZE = 500
_LOGGER = logging.getLogger(__name__)
_MIGRATION_LOCK_KEY = int.from_bytes(
    hashlib.sha256(VERSION.encode("utf-8")).digest()[:8],
    byteorder="big",
    signed=True,
)
_OWNED_COLUMNS = (
    ("users", "faculty_id"),
    ("review_items", "scope_faculty_id"),
    ("review_items", "scope_career_id"),
    ("review_items", "scope_resolution_reason"),
)
_OWNED_CONSTRAINTS = (
    "fk_users_faculty_id_faculties",
    "uq_careers_id_faculty_id",
    "fk_review_items_scope_faculty_id_faculties",
    "fk_review_items_scope_career_faculty_careers",
    "ck_review_items_scope_hierarchy",
    "ck_review_items_scope_resolution",
)
_OWNED_INDEXES = (
    "ix_users_faculty_id",
    "ix_review_items_scope_faculty_queue",
    "ix_review_items_scope_career_queue",
)
_REQUIRED_TABLES = (
    "faculties",
    "careers",
    "users",
    "teachers",
    "research_projects",
    "project_teachers",
    "research_entities",
    "external_researchers",
    "scientific_productions",
    "scientific_production_authors",
    "person_roles",
    "review_items",
    "schema_migrations",
)
_SCOPE_INPUT_TABLES = (
    "faculties",
    "careers",
    "users",
    "teachers",
    "research_projects",
    "project_teachers",
    "research_entities",
    "external_researchers",
    "scientific_productions",
    "scientific_production_authors",
    "person_roles",
    "review_items",
)

_EXPECTED_CONSTRAINT_DEFINITIONS = {
    "fk_users_faculty_id_faculties": (
        "FOREIGN KEY (faculty_id) REFERENCES faculties(id) ON DELETE RESTRICT"
    ),
    "uq_careers_id_faculty_id": "UNIQUE (id, faculty_id)",
    "fk_review_items_scope_faculty_id_faculties": (
        "FOREIGN KEY (scope_faculty_id) REFERENCES faculties(id) ON DELETE RESTRICT"
    ),
    "fk_review_items_scope_career_faculty_careers": (
        "FOREIGN KEY (scope_career_id, scope_faculty_id) "
        "REFERENCES careers(id, faculty_id) ON DELETE RESTRICT"
    ),
    "ck_review_items_scope_hierarchy": (
        "CHECK (scope_career_id IS NULL OR scope_faculty_id IS NOT NULL)"
    ),
    "ck_review_items_scope_resolution": (
        "CHECK (scope_faculty_id IS NULL AND scope_career_id IS NULL "
        "AND scope_resolution_reason IS NOT NULL AND "
        "(scope_resolution_reason::text = ANY (ARRAY["
        "'unresolved_no_persisted_scope'::character varying, "
        "'unresolved_cross_faculty'::character varying, "
        "'unresolved_missing_target'::character varying]::text[])) "
        "OR scope_faculty_id IS NOT NULL AND scope_resolution_reason IS NULL)"
    ),
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(f"0022 preflight failed: {message}")


def _normalized_sql(value: str) -> str:
    return " ".join(value.split())


@contextmanager
def _locked_connection(engine: Engine) -> Iterator[Connection]:
    with engine.connect() as connection:
        lock_acquired = connection.execute(
            text("SELECT pg_try_advisory_lock(:lock_key)"),
            {"lock_key": _MIGRATION_LOCK_KEY},
        ).scalar_one()
        connection.commit()
        _require(lock_acquired, "another 0022 migration session holds the advisory lock")
        try:
            yield connection
        finally:
            if connection.in_transaction():
                connection.rollback()
            connection.execute(
                text("SELECT pg_advisory_unlock(:lock_key)"),
                {"lock_key": _MIGRATION_LOCK_KEY},
            )
            connection.commit()


def _scope_columns(connection: Connection) -> dict[tuple[str, str], tuple[object, ...]]:
    return {
        (row.table_name, row.column_name): (
            row.data_type,
            row.character_maximum_length,
            row.is_nullable,
        )
        for row in connection.execute(text("""
            SELECT table_name, column_name, data_type,
                   character_maximum_length, is_nullable
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND (
                (table_name = 'users' AND column_name = 'faculty_id')
                OR (table_name = 'review_items' AND column_name LIKE 'scope_%')
              )
        """))
    }


def _expected_scope_columns() -> dict[tuple[str, str], tuple[object, ...]]:
    return {
        ("users", "faculty_id"): ("integer", None, "YES"),
        ("review_items", "scope_faculty_id"): ("integer", None, "YES"),
        ("review_items", "scope_career_id"): ("integer", None, "YES"),
        ("review_items", "scope_resolution_reason"): ("character varying", 80, "YES"),
    }


def _preflight(connection: Connection) -> str:
    tables = {
        row[0]
        for row in connection.execute(text("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = current_schema()
        """))
    }
    missing_tables = sorted(set(_REQUIRED_TABLES) - tables)
    _require(not missing_tables, f"required 0021 tables are missing: {missing_tables}")

    applied = {
        row[0]
        for row in connection.execute(text("SELECT version FROM schema_migrations"))
    }
    _require(DOWN_REVISION in applied, f"required predecessor is not recorded: {DOWN_REVISION}")
    _require(VERSION not in applied, f"migration record already exists: {VERSION}")

    existing_columns = _scope_columns(connection)

    existing_constraints = {
        row[0]
        for row in connection.execute(
            text("""
                SELECT constraint_row.conname
                FROM pg_constraint AS constraint_row
                JOIN pg_class AS table_row ON table_row.oid = constraint_row.conrelid
                JOIN pg_namespace AS namespace_row ON namespace_row.oid = table_row.relnamespace
                WHERE namespace_row.nspname = current_schema()
                  AND constraint_row.conname IN :names
            """).bindparams(bindparam("names", expanding=True)),
            {"names": _OWNED_CONSTRAINTS},
        )
    }

    existing_indexes = {
        row[0]
        for row in connection.execute(
            text("""
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = current_schema()
                  AND indexname IN :names
            """).bindparams(bindparam("names", expanding=True)),
            {"names": _OWNED_INDEXES},
        )
    }

    if not existing_columns and not existing_constraints and not existing_indexes:
        migration_state = "fresh"
    elif (
        existing_columns == _expected_scope_columns()
        and not existing_constraints
        and not existing_indexes
    ):
        migration_state = "resumable"
    elif (
        existing_columns == _expected_scope_columns()
        and existing_constraints == set(_OWNED_CONSTRAINTS)
        and existing_indexes == set(_OWNED_INDEXES)
    ):
        assert_schema(connection)
        migration_state = "complete"
    else:
        raise RuntimeError(
            "0022 preflight failed: inconsistent partial 0022 catalog state; "
            f"columns={sorted(existing_columns)} "
            f"constraints={sorted(existing_constraints)} indexes={sorted(existing_indexes)}"
        )

    if migration_state != "fresh":
        historical_faculty_assignments = connection.execute(text("""
            SELECT COUNT(*)
            FROM users
            WHERE faculty_id IS NOT NULL
        """)).scalar_one()
        _require(
            historical_faculty_assignments == 0,
            "historical users must keep faculty_id null during migration 0022",
        )

    broken_teacher_scope = connection.execute(text("""
        SELECT COUNT(*)
        FROM teachers AS teacher_row
        LEFT JOIN careers AS career_row ON career_row.id = teacher_row.career_id
        LEFT JOIN faculties AS faculty_row ON faculty_row.id = career_row.faculty_id
        WHERE career_row.id IS NULL OR faculty_row.id IS NULL
    """)).scalar_one()
    _require(broken_teacher_scope == 0, "persisted teacher/career/faculty FK chain is inconsistent")
    return migration_state


def _hash_query(connection: Connection, statement: str) -> str:
    digest = hashlib.sha256()
    for row in connection.execute(text(statement)):
        encoded = "\x1f".join("<null>" if value is None else str(value) for value in row)
        digest.update(encoded.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest().upper()


def _scope_input_hash(connection: Connection) -> str:
    queries = (
        (
            "review_items",
            "SELECT id, target_table, target_pk FROM review_items ORDER BY id",
        ),
        ("faculties", "SELECT id FROM faculties ORDER BY id"),
        ("careers", "SELECT id, faculty_id FROM careers ORDER BY id"),
        ("teachers", "SELECT id, career_id FROM teachers ORDER BY id"),
        (
            "person_roles",
            "SELECT id, teacher_id, external_researcher_id, scientific_production_id, "
            "research_project_id, research_entity_id FROM person_roles ORDER BY id",
        ),
        (
            "scientific_productions",
            "SELECT id, teacher_id, research_entity_id FROM scientific_productions ORDER BY id",
        ),
        (
            "scientific_production_authors",
            "SELECT id, production_id, teacher_id, external_researcher_id, research_entity_id "
            "FROM scientific_production_authors ORDER BY id",
        ),
        ("research_projects", "SELECT id FROM research_projects ORDER BY id"),
        (
            "project_teachers",
            "SELECT id, project_id, teacher_id FROM project_teachers ORDER BY id",
        ),
        ("research_entities", "SELECT id FROM research_entities ORDER BY id"),
        ("external_researchers", "SELECT id FROM external_researchers ORDER BY id"),
    )
    digest = hashlib.sha256()
    for label, statement in queries:
        digest.update(label.encode("utf-8"))
        digest.update(b"\x00")
        for row in connection.execute(text(statement)):
            encoded = "\x1f".join(
                "<null>" if value is None else str(value)
                for value in row
            )
            digest.update(encoded.encode("utf-8"))
            digest.update(b"\n")
    return digest.hexdigest().upper()


def _add_columns(connection: Connection) -> None:
    connection.execute(text("ALTER TABLE users ADD COLUMN faculty_id INTEGER NULL"))
    connection.execute(text("""
        ALTER TABLE review_items
            ADD COLUMN scope_faculty_id INTEGER NULL,
            ADD COLUMN scope_career_id INTEGER NULL,
            ADD COLUMN scope_resolution_reason VARCHAR(80) NULL
    """))


def _backfill_batch(
    connection: Connection,
    batch_ids: tuple[object, ...],
) -> int:
    result = connection.execute(text("""
        WITH batch AS (
            SELECT id, target_table, target_pk
            FROM review_items
            WHERE id = ANY(CAST(:batch_ids AS UUID[]))
        ),
        target_state AS (
            SELECT batch.id,
                   CASE batch.target_table
                       WHEN 'person_roles' THEN EXISTS (
                           SELECT 1 FROM person_roles WHERE id = batch.target_pk
                       )
                       WHEN 'scientific_production_authors' THEN EXISTS (
                           SELECT 1 FROM scientific_production_authors WHERE id = batch.target_pk
                       )
                       WHEN 'scientific_productions' THEN EXISTS (
                           SELECT 1 FROM scientific_productions WHERE id = batch.target_pk
                       )
                       WHEN 'research_entities' THEN EXISTS (
                           SELECT 1 FROM research_entities WHERE id = batch.target_pk
                       )
                       WHEN 'external_researchers' THEN EXISTS (
                           SELECT 1 FROM external_researchers WHERE id = batch.target_pk
                       )
                       ELSE FALSE
                   END AS target_exists
            FROM batch
        ),
        career_candidates AS (
            SELECT batch.id AS review_item_id, teacher_row.career_id
            FROM batch
            JOIN person_roles AS role_row
              ON batch.target_table = 'person_roles' AND role_row.id = batch.target_pk
            JOIN teachers AS teacher_row ON teacher_row.id = role_row.teacher_id

            UNION

            SELECT batch.id, teacher_row.career_id
            FROM batch
            JOIN person_roles AS role_row
              ON batch.target_table = 'person_roles' AND role_row.id = batch.target_pk
            JOIN scientific_productions AS production_row
              ON production_row.id = role_row.scientific_production_id
            JOIN teachers AS teacher_row ON teacher_row.id = production_row.teacher_id

            UNION

            SELECT batch.id, teacher_row.career_id
            FROM batch
            JOIN person_roles AS role_row
              ON batch.target_table = 'person_roles' AND role_row.id = batch.target_pk
            JOIN project_teachers AS project_teacher_row
              ON project_teacher_row.project_id = role_row.research_project_id
            JOIN teachers AS teacher_row ON teacher_row.id = project_teacher_row.teacher_id

            UNION

            SELECT batch.id, teacher_row.career_id
            FROM batch
            JOIN scientific_production_authors AS author_row
              ON batch.target_table = 'scientific_production_authors'
             AND author_row.id = batch.target_pk
            JOIN teachers AS teacher_row ON teacher_row.id = author_row.teacher_id

            UNION

            SELECT batch.id, teacher_row.career_id
            FROM batch
            JOIN scientific_production_authors AS author_row
              ON batch.target_table = 'scientific_production_authors'
             AND author_row.id = batch.target_pk
            JOIN scientific_productions AS production_row
              ON production_row.id = author_row.production_id
            JOIN teachers AS teacher_row ON teacher_row.id = production_row.teacher_id

            UNION

            SELECT batch.id, teacher_row.career_id
            FROM batch
            JOIN scientific_productions AS production_row
              ON batch.target_table = 'scientific_productions'
             AND production_row.id = batch.target_pk
            JOIN teachers AS teacher_row ON teacher_row.id = production_row.teacher_id

            UNION

            SELECT batch.id, teacher_row.career_id
            FROM batch
            JOIN scientific_productions AS production_row
              ON batch.target_table = 'scientific_productions'
             AND production_row.id = batch.target_pk
            JOIN scientific_production_authors AS author_row
              ON author_row.production_id = production_row.id
            JOIN teachers AS teacher_row ON teacher_row.id = author_row.teacher_id
        ),
        scope_rollup AS (
            SELECT target_state.id,
                   target_state.target_exists,
                   COUNT(DISTINCT career_candidates.career_id) AS career_count,
                   COUNT(DISTINCT career_row.faculty_id) AS faculty_count,
                   MIN(career_candidates.career_id) AS only_career_id,
                   MIN(career_row.faculty_id) AS only_faculty_id
            FROM target_state
            LEFT JOIN career_candidates
              ON career_candidates.review_item_id = target_state.id
            LEFT JOIN careers AS career_row
              ON career_row.id = career_candidates.career_id
            GROUP BY target_state.id, target_state.target_exists
        ),
        resolved AS (
            SELECT id,
                   CASE
                       WHEN target_exists AND faculty_count = 1 THEN only_faculty_id
                       ELSE NULL
                   END AS faculty_id,
                   CASE
                       WHEN target_exists AND faculty_count = 1 AND career_count = 1
                           THEN only_career_id
                       ELSE NULL
                   END AS career_id,
                   CASE
                       WHEN NOT target_exists THEN 'unresolved_missing_target'
                       WHEN career_count = 0 THEN 'unresolved_no_persisted_scope'
                       WHEN faculty_count > 1 THEN 'unresolved_cross_faculty'
                       ELSE NULL
                   END AS resolution_reason
            FROM scope_rollup
        )
        UPDATE review_items AS item_row
        SET scope_faculty_id = resolved.faculty_id,
            scope_career_id = resolved.career_id,
            scope_resolution_reason = resolved.resolution_reason
        FROM resolved
        WHERE item_row.id = resolved.id
          AND (
              item_row.scope_faculty_id,
              item_row.scope_career_id,
              item_row.scope_resolution_reason
          ) IS DISTINCT FROM (
              resolved.faculty_id,
              resolved.career_id,
              resolved.resolution_reason
          )
    """), {"batch_ids": list(batch_ids)})
    return result.rowcount


def _next_backfill_ids(
    connection: Connection,
    lower_bound: object | None,
) -> tuple[object, ...]:
    ids = tuple(
        row[0]
        for row in connection.execute(
            text("""
                SELECT id
                FROM review_items
                WHERE (CAST(:lower_bound AS UUID) IS NULL
                       OR id > CAST(:lower_bound AS UUID))
                ORDER BY id
                LIMIT :batch_size
            """),
            {"lower_bound": lower_bound, "batch_size": _BATCH_SIZE},
        )
    )
    return ids


def _add_constraints_and_indexes(connection: Connection) -> None:
    connection.execute(text("""
        ALTER TABLE careers
            ADD CONSTRAINT uq_careers_id_faculty_id UNIQUE (id, faculty_id)
    """))
    connection.execute(text("""
        ALTER TABLE users
            ADD CONSTRAINT fk_users_faculty_id_faculties
            FOREIGN KEY (faculty_id) REFERENCES faculties(id)
            ON DELETE RESTRICT NOT VALID
    """))
    connection.execute(text("""
        ALTER TABLE review_items
            ADD CONSTRAINT fk_review_items_scope_faculty_id_faculties
            FOREIGN KEY (scope_faculty_id) REFERENCES faculties(id)
            ON DELETE RESTRICT NOT VALID,
            ADD CONSTRAINT fk_review_items_scope_career_faculty_careers
            FOREIGN KEY (scope_career_id, scope_faculty_id)
            REFERENCES careers(id, faculty_id)
            ON DELETE RESTRICT NOT VALID,
            ADD CONSTRAINT ck_review_items_scope_hierarchy
            CHECK (scope_career_id IS NULL OR scope_faculty_id IS NOT NULL) NOT VALID,
            ADD CONSTRAINT ck_review_items_scope_resolution
            CHECK (
                (
                    scope_faculty_id IS NULL
                    AND scope_career_id IS NULL
                    AND scope_resolution_reason IS NOT NULL
                    AND scope_resolution_reason IN (
                        'unresolved_no_persisted_scope',
                        'unresolved_cross_faculty',
                        'unresolved_missing_target'
                    )
                )
                OR (
                    scope_faculty_id IS NOT NULL
                    AND scope_resolution_reason IS NULL
                )
            ) NOT VALID
    """))

    for table_name, constraint_name in (
        ("users", "fk_users_faculty_id_faculties"),
        ("review_items", "fk_review_items_scope_faculty_id_faculties"),
        ("review_items", "fk_review_items_scope_career_faculty_careers"),
        ("review_items", "ck_review_items_scope_hierarchy"),
        ("review_items", "ck_review_items_scope_resolution"),
    ):
        connection.execute(text(
            f"ALTER TABLE {table_name} VALIDATE CONSTRAINT {constraint_name}"
        ))

    connection.execute(text("CREATE INDEX ix_users_faculty_id ON users (faculty_id)"))
    connection.execute(text("""
        CREATE INDEX ix_review_items_scope_faculty_queue
        ON review_items (
            scope_faculty_id,
            case_status,
            automatic_priority,
            created_at,
            id
        )
    """))
    connection.execute(text("""
        CREATE INDEX ix_review_items_scope_career_queue
        ON review_items (
            scope_career_id,
            case_status,
            automatic_priority,
            created_at,
            id
        )
    """))


def _diagnostics(connection: Connection, input_hash: str) -> dict[str, object]:
    counts = connection.execute(text("""
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE scope_career_id IS NOT NULL) AS career_scoped,
               COUNT(*) FILTER (
                   WHERE scope_faculty_id IS NOT NULL AND scope_career_id IS NULL
               ) AS faculty_scoped,
               COUNT(*) FILTER (WHERE scope_faculty_id IS NULL) AS unresolved
        FROM review_items
    """)).one()
    reason_counts = {
        row.scope_resolution_reason: row.count
        for row in connection.execute(text("""
            SELECT scope_resolution_reason, COUNT(*) AS count
            FROM review_items
            WHERE scope_resolution_reason IS NOT NULL
            GROUP BY scope_resolution_reason
            ORDER BY scope_resolution_reason
        """))
    }
    output_hash = _hash_query(
        connection,
        "SELECT id, scope_faculty_id, scope_career_id, scope_resolution_reason "
        "FROM review_items ORDER BY id",
    )
    return {
        "total": counts.total,
        "career_scoped": counts.career_scoped,
        "faculty_scoped": counts.faculty_scoped,
        "unresolved": counts.unresolved,
        "reason_counts": reason_counts,
        "input_sha256": input_hash,
        "output_sha256": output_hash,
    }


def assert_schema(connection: Connection) -> None:
    _require(
        _scope_columns(connection) == _expected_scope_columns(),
        "0022 columns/types/nullability differ",
    )

    constraints = {
        row.name: (row.kind, row.validated, row.delete_action, row.definition)
        for row in connection.execute(
            text("""
                SELECT constraint_row.conname AS name,
                       constraint_row.contype AS kind,
                       constraint_row.convalidated AS validated,
                       constraint_row.confdeltype AS delete_action,
                       pg_get_constraintdef(constraint_row.oid, true) AS definition
                FROM pg_constraint AS constraint_row
                JOIN pg_class AS table_row ON table_row.oid = constraint_row.conrelid
                JOIN pg_namespace AS namespace_row ON namespace_row.oid = table_row.relnamespace
                WHERE namespace_row.nspname = current_schema()
                  AND constraint_row.conname IN :names
            """).bindparams(bindparam("names", expanding=True)),
            {"names": _OWNED_CONSTRAINTS},
        )
    }
    _require(set(constraints) == set(_OWNED_CONSTRAINTS), "0022 constraints differ")
    _require(all(value[1] for value in constraints.values()), "an 0022 constraint is not valid")
    _require(
        {
            name: _normalized_sql(value[3])
            for name, value in constraints.items()
        }
        == {
            name: _normalized_sql(definition)
            for name, definition in _EXPECTED_CONSTRAINT_DEFINITIONS.items()
        },
        "0022 constraint definitions differ",
    )
    for foreign_key in (
        "fk_users_faculty_id_faculties",
        "fk_review_items_scope_faculty_id_faculties",
        "fk_review_items_scope_career_faculty_careers",
    ):
        _require(
            constraints[foreign_key][0] == "f" and constraints[foreign_key][2] == "r",
            f"{foreign_key} is not an ON DELETE RESTRICT foreign key",
        )
    _require(
        constraints["uq_careers_id_faculty_id"][0] == "u",
        "supporting careers constraint is not unique",
    )
    _require(
        constraints["ck_review_items_scope_hierarchy"][0] == "c"
        and constraints["ck_review_items_scope_resolution"][0] == "c",
        "scope checks are not check constraints",
    )

    indexes = {
        row.name: (tuple(row.columns), row.is_unique, row.is_valid)
        for row in connection.execute(
            text("""
                SELECT index_row.relname AS name,
                       ARRAY_AGG(attribute_row.attname ORDER BY key_row.ordinality) AS columns,
                       index_catalog.indisunique AS is_unique,
                       index_catalog.indisvalid AS is_valid
                FROM pg_index AS index_catalog
                JOIN pg_class AS index_row ON index_row.oid = index_catalog.indexrelid
                JOIN pg_class AS table_row ON table_row.oid = index_catalog.indrelid
                JOIN pg_namespace AS namespace_row ON namespace_row.oid = table_row.relnamespace
                JOIN LATERAL UNNEST(index_catalog.indkey)
                  WITH ORDINALITY AS key_row(attribute_number, ordinality) ON TRUE
                JOIN pg_attribute AS attribute_row
                  ON attribute_row.attrelid = table_row.oid
                 AND attribute_row.attnum = key_row.attribute_number
                WHERE namespace_row.nspname = current_schema()
                  AND index_row.relname IN :names
                GROUP BY index_row.relname, index_catalog.indisunique, index_catalog.indisvalid
            """).bindparams(bindparam("names", expanding=True)),
            {"names": _OWNED_INDEXES},
        )
    }
    _require(indexes == {
        "ix_users_faculty_id": (("faculty_id",), False, True),
        "ix_review_items_scope_faculty_queue": (
            (
                "scope_faculty_id",
                "case_status",
                "automatic_priority",
                "created_at",
                "id",
            ),
            False,
            True,
        ),
        "ix_review_items_scope_career_queue": (
            (
                "scope_career_id",
                "case_status",
                "automatic_priority",
                "created_at",
                "id",
            ),
            False,
            True,
        ),
    }, "0022 indexes differ")


def upgrade(engine: Engine) -> None:
    with _locked_connection(engine) as connection:
        with connection.begin():
            migration_state = _preflight(connection)
            input_hash = _scope_input_hash(connection)

        if migration_state == "fresh":
            with connection.begin():
                _add_columns(connection)

        lower_bound: object | None = None
        while True:
            with connection.begin():
                batch_ids = _next_backfill_ids(connection, lower_bound)
                if not batch_ids:
                    break
                _backfill_batch(connection, batch_ids)
            lower_bound = batch_ids[-1]

        with connection.begin():
            connection.execute(text(
                "LOCK TABLE " + ", ".join(_SCOPE_INPUT_TABLES) + " IN SHARE MODE"
            ))
            final_input_hash = _scope_input_hash(connection)
            _require(
                final_input_hash == input_hash,
                "persisted scope input graph changed during migration; rerun is required",
            )
            validation_lower_bound: object | None = None
            mismatch_count = 0
            while True:
                validation_ids = _next_backfill_ids(
                    connection,
                    validation_lower_bound,
                )
                if not validation_ids:
                    break
                mismatch_count += _backfill_batch(connection, validation_ids)
                validation_lower_bound = validation_ids[-1]
            _require(
                mismatch_count == 0,
                "persisted scope diverged from the frozen resolver; rerun is required",
            )
            historical_faculty_assignments = connection.execute(text("""
                SELECT COUNT(*)
                FROM users
                WHERE faculty_id IS NOT NULL
            """)).scalar_one()
            _require(
                historical_faculty_assignments == 0,
                "historical users must keep faculty_id null during migration 0022",
            )
            if migration_state != "complete":
                _add_constraints_and_indexes(connection)
            assert_schema(connection)
            diagnostics = _diagnostics(connection, input_hash)

    _LOGGER.info(
        "0022 backfill diagnostics total=%d career_scoped=%d faculty_scoped=%d "
        "unresolved=%d reason_counts=%s input_sha256=%s output_sha256=%s",
        diagnostics["total"],
        diagnostics["career_scoped"],
        diagnostics["faculty_scoped"],
        diagnostics["unresolved"],
        diagnostics["reason_counts"],
        diagnostics["input_sha256"],
        diagnostics["output_sha256"],
    )


def downgrade(engine: Engine) -> None:
    with _locked_connection(engine) as connection:
        with connection.begin():
            is_recorded = connection.execute(
                text("SELECT EXISTS (SELECT 1 FROM schema_migrations WHERE version = :version)"),
                {"version": VERSION},
            ).scalar_one()
            _require(is_recorded, "migration 0022 must be recorded before downgrade")
            assert_schema(connection)
            connection.execute(text("DROP INDEX ix_review_items_scope_career_queue"))
            connection.execute(text("DROP INDEX ix_review_items_scope_faculty_queue"))
            connection.execute(text("DROP INDEX ix_users_faculty_id"))
            connection.execute(text("""
                ALTER TABLE review_items
                    DROP CONSTRAINT fk_review_items_scope_career_faculty_careers,
                    DROP CONSTRAINT fk_review_items_scope_faculty_id_faculties,
                    DROP CONSTRAINT ck_review_items_scope_resolution,
                    DROP CONSTRAINT ck_review_items_scope_hierarchy
            """))
            connection.execute(text("""
                ALTER TABLE review_items
                    DROP COLUMN scope_resolution_reason,
                    DROP COLUMN scope_career_id,
                    DROP COLUMN scope_faculty_id
            """))
            connection.execute(text("""
                ALTER TABLE users
                    DROP CONSTRAINT fk_users_faculty_id_faculties,
                    DROP COLUMN faculty_id
            """))
            connection.execute(text(
                "ALTER TABLE careers DROP CONSTRAINT uq_careers_id_faculty_id"
            ))
