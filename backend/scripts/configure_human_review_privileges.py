from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
import re
from collections.abc import Sequence

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine


ROLE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

B2B_TABLE_PRIVILEGES = {
    "user_b2b_capabilities": ("SELECT", "INSERT", "UPDATE"),
    "review_items": ("SELECT", "INSERT", "UPDATE"),
    "review_decisions": ("SELECT", "INSERT"),
    "canonical_identities": ("SELECT", "INSERT", "UPDATE"),
    "person_aliases": ("SELECT", "INSERT", "UPDATE"),
    "field_overrides": ("SELECT", "INSERT", "UPDATE"),
    "audit_events": ("SELECT", "INSERT"),
}

RUNTIME_TABLE_PRIVILEGES = {
    "faculties": ("SELECT",),
    "careers": ("SELECT",),
    "users": ("SELECT",),
    "academic_periods": ("SELECT",),
    "teachers": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "external_researchers": ("SELECT", "INSERT", "DELETE"),
    "research_entities": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "person_roles": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "scientific_productions": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "scientific_production_authors": ("SELECT", "INSERT", "DELETE"),
    "research_projects": ("SELECT", "INSERT", "DELETE"),
    "project_teachers": ("SELECT", "INSERT", "DELETE"),
    "annual_goals": ("SELECT", "INSERT", "UPDATE"),
    "import_batches": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "import_jobs": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "import_review_items": ("SELECT", "INSERT", "DELETE"),
    "import_normalization_audits": ("SELECT", "INSERT", "DELETE"),
    "imported_research_records": ("INSERT", "DELETE"),
    "imported_project_participants": ("INSERT", "DELETE"),
    "imported_progress_reports": ("SELECT", "INSERT", "DELETE"),
    "imported_ocr_traces": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    **B2B_TABLE_PRIVILEGES,
}
OWNER_ONLY_TABLES = ("schema_migrations",)
ROW_LOCK_TABLES = ("import_jobs",)

_ALL_TABLE_PRIVILEGES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)
_B2B_SEQUENCE_PRIVILEGES = ("SELECT", "USAGE")
_RUNTIME_SEQUENCE_PRIVILEGES = ("USAGE",)
_B2B_FUNCTION_NAMES = (
    "b2b_reject_career_capability",
    "b2b_reject_career_role_with_capability",
    "b2b_reject_append_only_mutation",
)


@dataclass(frozen=True)
class PrivilegeReport:
    owner_role: str
    application_role: str
    schema_name: str
    schema_owner: str
    table_privileges: tuple[tuple[str, tuple[str, ...]], ...]
    owner_only_tables: tuple[str, ...]
    sequence_names: tuple[str, ...]
    sequence_privileges: tuple[tuple[str, tuple[str, ...]], ...]
    row_lock_tables: tuple[str, ...]
    functions_without_execute: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Configure least-privilege access for B2B.1 database objects."
    )
    parser.add_argument("--owner-url-env", required=True)
    parser.add_argument("--application-role-env", required=True)
    parser.add_argument("--apply", action="store_true")
    return parser


def _quote(connection: Connection, identifier: str) -> str:
    return connection.dialect.identifier_preparer.quote(identifier)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _validate_roles(
    connection: Connection,
    application_role: str,
) -> tuple[str, str, str]:
    _require(
        bool(ROLE_NAME_PATTERN.fullmatch(application_role)),
        "application role must match ^[A-Za-z_][A-Za-z0-9_]{0,62}$",
    )
    owner_role, schema_name = connection.execute(text(
        "SELECT current_user, current_schema()"
    )).one()
    _require(bool(schema_name), "owner connection must resolve a current schema")
    _require(application_role != owner_role, "owner and application roles must be distinct")

    role = connection.execute(text(
        """
        SELECT oid, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
        FROM pg_roles
        WHERE rolname = :application_role
        """
    ), {"application_role": application_role}).one_or_none()
    _require(role is not None, f"application role {application_role!r} does not exist")
    _require(not role.rolsuper, "application role must be NOSUPERUSER")
    _require(not role.rolcreatedb, "application role must be NOCREATEDB")
    _require(not role.rolcreaterole, "application role must be NOCREATEROLE")
    _require(not role.rolreplication, "application role must be NOREPLICATION")
    _require(not role.rolbypassrls, "application role must be NOBYPASSRLS")

    inherits_owner = connection.execute(text(
        "SELECT pg_has_role(:application_role, :owner_role, 'MEMBER')"
    ), {
        "application_role": application_role,
        "owner_role": owner_role,
    }).scalar_one()
    _require(not inherits_owner, "application role must not inherit or SET ROLE to owner role")

    schema_owner = connection.execute(text(
        """
        SELECT owner.rolname
        FROM pg_namespace AS namespace
        JOIN pg_roles AS owner ON owner.oid = namespace.nspowner
        WHERE namespace.nspname = :schema_name
        """
    ), {"schema_name": schema_name}).scalar_one()
    owner_controls_schema = schema_owner == owner_role or connection.execute(text(
        "SELECT pg_has_role(:owner_role, :schema_owner, 'MEMBER')"
    ), {"owner_role": owner_role, "schema_owner": schema_owner}).scalar_one()
    _require(owner_controls_schema, "owner connection must control the target schema")
    _require(schema_owner != application_role, "application role must not own the schema")
    return owner_role, schema_name, schema_owner


def _validate_owned_objects(
    connection: Connection,
    owner_role: str,
    schema_name: str,
) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    managed_tables = tuple(RUNTIME_TABLE_PRIVILEGES) + OWNER_ONLY_TABLES
    table_owners = {
        row.relname: row.owner_name
        for row in connection.execute(text(
            """
            SELECT relation.relname, owner.rolname AS owner_name
            FROM pg_class AS relation
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            JOIN pg_roles AS owner ON owner.oid = relation.relowner
            WHERE namespace.nspname = :schema_name
              AND relation.relkind IN ('r', 'p')
              AND relation.relname = ANY(:table_names)
            """
        ), {
            "schema_name": schema_name,
            "table_names": list(managed_tables),
        })
    }
    _require(
        set(B2B_TABLE_PRIVILEGES).issubset(table_owners),
        "all approved B2B.1 tables must exist in the current schema",
    )
    _require(
        all(owner == owner_role for owner in table_owners.values()),
        "owner connection must own every managed table",
    )

    function_owners = {
        row.proname: row.owner_name
        for row in connection.execute(text(
            """
            SELECT function_row.proname, owner.rolname AS owner_name
            FROM pg_proc AS function_row
            JOIN pg_namespace AS namespace ON namespace.oid = function_row.pronamespace
            JOIN pg_roles AS owner ON owner.oid = function_row.proowner
            WHERE namespace.nspname = :schema_name
              AND function_row.pronargs = 0
              AND function_row.proname = ANY(:function_names)
            """
        ), {
            "schema_name": schema_name,
            "function_names": list(_B2B_FUNCTION_NAMES),
        })
    }
    _require(
        set(function_owners) == set(_B2B_FUNCTION_NAMES),
        "all approved B2B.1 functions must exist with zero arguments",
    )
    _require(
        all(owner == owner_role for owner in function_owners.values()),
        "owner connection must own every B2B.1 function",
    )

    non_owner_indexes = connection.execute(text(
        """
        SELECT COUNT(*)
        FROM pg_index AS index_catalog
        JOIN pg_class AS table_row ON table_row.oid = index_catalog.indrelid
        JOIN pg_class AS index_row ON index_row.oid = index_catalog.indexrelid
        JOIN pg_namespace AS namespace ON namespace.oid = table_row.relnamespace
        JOIN pg_roles AS index_owner ON index_owner.oid = index_row.relowner
        WHERE namespace.nspname = :schema_name
          AND table_row.relname = ANY(:table_names)
          AND index_owner.rolname <> :owner_role
        """
    ), {
        "schema_name": schema_name,
        "table_names": list(managed_tables),
        "owner_role": owner_role,
    }).scalar_one()
    _require(non_owner_indexes == 0, "owner connection must own every managed index")

    trigger_owner_mismatches = connection.execute(text(
        """
        SELECT COUNT(*)
        FROM pg_trigger AS trigger_row
        JOIN pg_class AS table_row ON table_row.oid = trigger_row.tgrelid
        JOIN pg_namespace AS namespace ON namespace.oid = table_row.relnamespace
        JOIN pg_roles AS table_owner ON table_owner.oid = table_row.relowner
        JOIN pg_proc AS function_row ON function_row.oid = trigger_row.tgfoid
        JOIN pg_roles AS function_owner ON function_owner.oid = function_row.proowner
        WHERE namespace.nspname = :schema_name
          AND NOT trigger_row.tgisinternal
          AND (
              table_owner.rolname <> :owner_role
              OR function_owner.rolname <> :owner_role
          )
        """
    ), {"schema_name": schema_name, "owner_role": owner_role}).scalar_one()
    _require(
        trigger_owner_mismatches == 0,
        "owner connection must control every non-internal trigger and trigger function",
    )

    sequence_rows = tuple(connection.execute(text(
        """
        SELECT DISTINCT sequence_row.relname, table_row.relname AS table_name,
                        owner.rolname AS owner_name
        FROM pg_class AS sequence_row
        JOIN pg_namespace AS sequence_namespace
          ON sequence_namespace.oid = sequence_row.relnamespace
        JOIN pg_roles AS owner ON owner.oid = sequence_row.relowner
        JOIN pg_depend AS dependency
          ON dependency.classid = 'pg_class'::regclass
         AND dependency.objid = sequence_row.oid
         AND dependency.deptype IN ('a', 'i')
        JOIN pg_class AS table_row ON table_row.oid = dependency.refobjid
        JOIN pg_namespace AS table_namespace ON table_namespace.oid = table_row.relnamespace
        WHERE sequence_row.relkind = 'S'
          AND sequence_namespace.nspname = :schema_name
          AND table_namespace.nspname = :schema_name
          AND table_row.relname = ANY(:table_names)
        ORDER BY sequence_row.relname, table_row.relname
        """
    ), {
        "schema_name": schema_name,
        "table_names": list(managed_tables),
    }))
    _require(
        all(row.owner_name == owner_role for row in sequence_rows),
        "owner connection must own every managed sequence",
    )
    existing_tables = tuple(
        table_name for table_name in managed_tables if table_name in table_owners
    )
    return existing_tables, tuple(
        (row.relname, row.table_name) for row in sequence_rows
    )


def _has_table_privilege(
    connection: Connection,
    application_role: str,
    qualified_table: str,
    privilege: str,
) -> bool:
    return bool(connection.execute(text(
        "SELECT has_table_privilege(:role, :object_name, :privilege)"
    ), {
        "role": application_role,
        "object_name": qualified_table,
        "privilege": privilege,
    }).scalar_one())


def _sequence_privileges_for_table(table_name: str) -> tuple[str, ...]:
    privileges = RUNTIME_TABLE_PRIVILEGES.get(table_name, ())
    if "INSERT" not in privileges:
        return ()
    if table_name in B2B_TABLE_PRIVILEGES:
        return _B2B_SEQUENCE_PRIVILEGES
    return _RUNTIME_SEQUENCE_PRIVILEGES


def _verify_privileges(
    connection: Connection,
    application_role: str,
    schema_name: str,
    existing_tables: tuple[str, ...],
    sequence_rows: tuple[tuple[str, str], ...],
) -> None:
    qualified_schema = _quote(connection, schema_name)
    schema_usage = connection.execute(text(
        "SELECT has_schema_privilege(:role, :schema_name, 'USAGE')"
    ), {"role": application_role, "schema_name": schema_name}).scalar_one()
    schema_create = connection.execute(text(
        "SELECT has_schema_privilege(:role, :schema_name, 'CREATE')"
    ), {"role": application_role, "schema_name": schema_name}).scalar_one()
    _require(schema_usage and not schema_create, "application schema privileges differ")

    for table_name in existing_tables:
        allowed_privileges = RUNTIME_TABLE_PRIVILEGES.get(table_name, ())
        qualified_table = f"{qualified_schema}.{_quote(connection, table_name)}"
        for privilege in _ALL_TABLE_PRIVILEGES:
            actual = _has_table_privilege(
                connection,
                application_role,
                qualified_table,
                privilege,
            )
            _require(
                actual == (privilege in allowed_privileges),
                f"application privilege {privilege} differs on {table_name}",
            )

    for table_name in OWNER_ONLY_TABLES:
        if table_name not in existing_tables:
            continue
        qualified_table = f"{qualified_schema}.{_quote(connection, table_name)}"
        for privilege in _ALL_TABLE_PRIVILEGES:
            _require(
                not _has_table_privilege(
                    connection,
                    application_role,
                    qualified_table,
                    privilege,
                ),
                f"application role must not access owner-only table {table_name}",
            )

    for sequence_name, table_name in sequence_rows:
        qualified_sequence = f"{qualified_schema}.{_quote(connection, sequence_name)}"
        allowed_privileges = _sequence_privileges_for_table(table_name)
        for privilege in ("SELECT", "USAGE", "UPDATE"):
            actual = connection.execute(text(
                "SELECT has_sequence_privilege(:role, :object_name, :privilege)"
            ), {
                "role": application_role,
                "object_name": qualified_sequence,
                "privilege": privilege,
            }).scalar_one()
            _require(
                actual == (privilege in allowed_privileges),
                f"application privilege {privilege} differs on {sequence_name}",
            )

    for table_name in ROW_LOCK_TABLES:
        _require(
            "SELECT" in RUNTIME_TABLE_PRIVILEGES[table_name]
            and "UPDATE" in RUNTIME_TABLE_PRIVILEGES[table_name],
            f"row-lock table {table_name} must have SELECT and UPDATE",
        )

    for function_name in _B2B_FUNCTION_NAMES:
        qualified_function = (
            f"{qualified_schema}.{_quote(connection, function_name)}()"
        )
        can_execute = connection.execute(text(
            "SELECT has_function_privilege(:role, :object_name, 'EXECUTE')"
        ), {
            "role": application_role,
            "object_name": qualified_function,
        }).scalar_one()
        _require(not can_execute, f"application role must not execute {function_name}")

    grantable = connection.execute(text(
        """
        SELECT COUNT(*)
        FROM information_schema.role_table_grants
        WHERE grantee = :application_role AND is_grantable = 'YES'
        """
    ), {"application_role": application_role}).scalar_one()
    _require(grantable == 0, "application role must not receive grant options")

    app_owned = connection.execute(text(
        """
        SELECT
            (SELECT COUNT(*)
             FROM pg_class AS relation
             JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
             WHERE namespace.nspname = :schema_name AND relation.relowner = role_row.oid)
            + (SELECT COUNT(*)
               FROM pg_proc AS function_row
               JOIN pg_namespace AS namespace ON namespace.oid = function_row.pronamespace
               WHERE namespace.nspname = :schema_name AND function_row.proowner = role_row.oid)
            + (SELECT COUNT(*)
               FROM pg_namespace AS namespace
               WHERE namespace.nspname = :schema_name AND namespace.nspowner = role_row.oid)
        FROM pg_roles AS role_row
        WHERE role_row.rolname = :application_role
        """
    ), {
        "schema_name": schema_name,
        "application_role": application_role,
    }).scalar_one()
    _require(app_owned == 0, "application role must not own schema objects")


def configure_human_review_privileges(
    owner_engine: Engine,
    application_role: str,
) -> PrivilegeReport:
    with owner_engine.begin() as connection:
        owner_role, schema_name, schema_owner = _validate_roles(
            connection,
            application_role,
        )
        existing_tables, sequence_rows = _validate_owned_objects(
            connection,
            owner_role,
            schema_name,
        )

        quoted_schema = _quote(connection, schema_name)
        quoted_application_role = _quote(connection, application_role)
        connection.execute(text(
            f"REVOKE CREATE ON SCHEMA {quoted_schema} FROM PUBLIC"
        ))
        connection.execute(text(
            f"REVOKE ALL PRIVILEGES ON SCHEMA {quoted_schema} "
            f"FROM {quoted_application_role}"
        ))
        connection.execute(text(
            f"GRANT USAGE ON SCHEMA {quoted_schema} TO {quoted_application_role}"
        ))

        for table_name in existing_tables:
            qualified_table = f"{quoted_schema}.{_quote(connection, table_name)}"
            connection.execute(text(
                f"REVOKE ALL PRIVILEGES ON TABLE {qualified_table} FROM PUBLIC"
            ))
            connection.execute(text(
                f"REVOKE ALL PRIVILEGES ON TABLE {qualified_table} "
                f"FROM {quoted_application_role}"
            ))
            privileges = RUNTIME_TABLE_PRIVILEGES.get(table_name, ())
            if privileges:
                connection.execute(text(
                    f"GRANT {', '.join(privileges)} ON TABLE {qualified_table} "
                    f"TO {quoted_application_role}"
                ))

        for sequence_name, table_name in sequence_rows:
            qualified_sequence = (
                f"{quoted_schema}.{_quote(connection, sequence_name)}"
            )
            connection.execute(text(
                f"REVOKE ALL PRIVILEGES ON SEQUENCE {qualified_sequence} FROM PUBLIC"
            ))
            connection.execute(text(
                f"REVOKE ALL PRIVILEGES ON SEQUENCE {qualified_sequence} "
                f"FROM {quoted_application_role}"
            ))
            privileges = _sequence_privileges_for_table(table_name)
            if privileges:
                connection.execute(text(
                    f"GRANT {', '.join(privileges)} "
                    f"ON SEQUENCE {qualified_sequence} TO {quoted_application_role}"
                ))

        for function_name in _B2B_FUNCTION_NAMES:
            qualified_function = (
                f"{quoted_schema}.{_quote(connection, function_name)}()"
            )
            connection.execute(text(
                f"REVOKE ALL PRIVILEGES ON FUNCTION {qualified_function} FROM PUBLIC"
            ))
            connection.execute(text(
                f"REVOKE ALL PRIVILEGES ON FUNCTION {qualified_function} "
                f"FROM {quoted_application_role}"
            ))

        _verify_privileges(
            connection,
            application_role,
            schema_name,
            existing_tables,
            sequence_rows,
        )

    return PrivilegeReport(
        owner_role=owner_role,
        application_role=application_role,
        schema_name=schema_name,
        schema_owner=schema_owner,
        table_privileges=tuple(RUNTIME_TABLE_PRIVILEGES.items()),
        owner_only_tables=OWNER_ONLY_TABLES,
        sequence_names=tuple(name for name, _table_name in sequence_rows),
        sequence_privileges=tuple(
            (name, _sequence_privileges_for_table(table_name))
            for name, table_name in sequence_rows
        ),
        row_lock_tables=ROW_LOCK_TABLES,
        functions_without_execute=_B2B_FUNCTION_NAMES,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.apply:
        parser.error("--apply is required")

    owner_url = os.environ.get(args.owner_url_env, "").strip()
    application_role = os.environ.get(args.application_role_env, "").strip()
    if not owner_url:
        parser.error(f"environment variable {args.owner_url_env!r} is required")
    if not application_role:
        parser.error(
            f"environment variable {args.application_role_env!r} is required"
        )

    owner_engine = create_engine(owner_url, pool_pre_ping=True)
    try:
        report = configure_human_review_privileges(
            owner_engine,
            application_role,
        )
    finally:
        owner_engine.dispose()
    print(json.dumps(report.to_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
