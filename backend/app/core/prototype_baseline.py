"""Fresh-only verified schema baseline for the academic prototype."""

from __future__ import annotations

import ast
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import Literal

from sqlalchemy import inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.schema import (
    CheckConstraint,
    CreateIndex,
    ForeignKeyConstraint,
    UniqueConstraint,
)

from app.core.database import Base
from app.core.migrations import MIGRATIONS
from app.core.migrations import ensure_schema_migrations_table as _ensure_control_table
from app import models as _registered_models  # noqa: F401
from scripts.configure_human_review_privileges import (
    _validate_owned_objects,
    _validate_roles,
    _verify_privileges,
    configure_human_review_privileges,
)


@dataclass(frozen=True)
class MigrationHash:
    version: str
    relative_path: str
    sha256: str


@dataclass(frozen=True)
class BaselineReport:
    versions: Sequence[str]
    runtime_objects: Sequence[str]
    application_role: str


class BaselineRefused(RuntimeError):
    """The target or repository cannot receive the verified baseline."""


@dataclass(frozen=True)
class RuntimeObject:
    name: str
    kind: Literal["index", "function", "trigger"]
    source_path: str
    ddl: str


@dataclass(frozen=True)
class KnownDeltaReport:
    author_fk: str
    historical_defaults: str
    historical_pk_names: str


MIGRATION_SHA256_MANIFEST: tuple[MigrationHash, ...] = (
    MigrationHash("20260628_0001_add_import_jobs_batch_id", "backend/app/migrations/versions/20260628_0001_add_import_jobs_batch_id.py", "5D9F432F4199CCFCFBAC32DD8FA884DB2BD78CE7A77A1B9637F3888C52C9E218"),
    MigrationHash("20260628_0002_import_batches_and_job_state", "backend/app/migrations/versions/20260628_0002_import_batches_and_job_state.py", "5A1252F6327FB5EF7AFE4E67E91D3A737725310C253233F593E6431B23BEE19F"),
    MigrationHash("20260628_0003_fix_import_batches_sequence", "backend/app/migrations/versions/20260628_0003_fix_import_batches_sequence.py", "0FFB182B1305E88672EB49BDF1A33CDA7C5AC3A39EDEDC5382E8014DDBD74E1E"),
    MigrationHash("20260628_0004_normalized_dashboard_status_fields", "backend/app/migrations/versions/20260628_0004_normalized_dashboard_status_fields.py", "435493F56887CAA9E65DE5276AE309E17EC9CFEE11C920E81AA37EB2D21DF036"),
    MigrationHash("20260628_0005_import_job_diagnostics", "backend/app/migrations/versions/20260628_0005_import_job_diagnostics.py", "BAAF6938D5CC9F40508986A3A211E3440BB8F308D31A5F0AAEBDA45D86BA4FFB"),
    MigrationHash("20260628_0006_import_job_error_details", "backend/app/migrations/versions/20260628_0006_import_job_error_details.py", "73D33DA71D39C6FB07D0FEF2A875410AA79FA29D0019501CE3E4792E387084AC"),
    MigrationHash("20260628_0007_external_researchers_and_review_items", "backend/app/migrations/versions/20260628_0007_external_researchers_and_review_items.py", "28ADA8AA829DA5DFF56A48280454DFA1F504D3B4C5FCCC1E53F92960E285E33B"),
    MigrationHash("20260628_0008_normalization_traceability", "backend/app/migrations/versions/20260628_0008_normalization_traceability.py", "80A871F77D391AC91F01E900E4394BD6101309DACF31346B55C225B5EFCAD661"),
    MigrationHash("20260629_0009_scientific_production_authors", "backend/app/migrations/versions/20260629_0009_scientific_production_authors.py", "49A60060465244CF250FE3679385C499C30A2FC302A08228E4E3EFB667712074"),
    MigrationHash("20260629_0010_person_roles", "backend/app/migrations/versions/20260629_0010_person_roles.py", "8D4E98EC8EE47F0ECC56FC9BA713A39EE1358828C25601A7F1623300BA3B6C59"),
    MigrationHash("20260629_0011_research_entities_and_pending_products", "backend/app/migrations/versions/20260629_0011_research_entities_and_pending_products.py", "049FD67A06F2874E9A155AB5B498481A9482A044E5B744CB724922B2DDC00D77"),
    MigrationHash("20260629_0012_author_traceability", "backend/app/migrations/versions/20260629_0012_author_traceability.py", "BC0B682636FB4E362F76E5B26570D6ED3745C9E0951344AAC00FA69E4F2A7DDA"),
    MigrationHash("20260710_0013_teacher_person_role_validation", "backend/app/migrations/versions/20260710_0013_teacher_person_role_validation.py", "06A6E1656DB4D141E05F10225D709CDE7BC7B2F875E5AB52B8E3FB86F105B64A"),
    MigrationHash("20260711_0014_dropbox_document_versioning", "backend/app/migrations/versions/20260711_0014_dropbox_document_versioning.py", "DB1516A8CB00FB86095773AB163EBE3CFCD75668E4D764FA5F07367E84FC0F66"),
    MigrationHash("20260711_0015_dropbox_revision_uniqueness", "backend/app/migrations/versions/20260711_0015_dropbox_revision_uniqueness.py", "E40CC1F9E1DB090240EE15B2AD333814AE0814DE886554C05B9C10E7C959B947"),
    MigrationHash("20260712_0016_canonical_identity_fields", "backend/app/migrations/versions/20260712_0016_canonical_identity_fields.py", "C5DE79A50D1FDD696020A30903608A7F1DDA53AA90E2FD7713592733F7184F44"),
    MigrationHash("20260713_0017_b2b_capabilities", "backend/app/migrations/versions/20260713_0017_b2b_capabilities.py", "D7C495F0271FF989ABC25B3B446781441CE68D72069C0C5AC8CECE91847608B6"),
    MigrationHash("20260713_0018_human_review_core", "backend/app/migrations/versions/20260713_0018_human_review_core.py", "F5DEEC37ECC277F0CDB6BA4E413A6F4C0912AEB901B9CE56D80CD9DEECC9100E"),
    MigrationHash("20260713_0019_human_review_projection", "backend/app/migrations/versions/20260713_0019_human_review_projection.py", "6741935A9326001B317517928EB7281D06DE5463A8E035283B66BAD1676FCD1C"),
    MigrationHash("20260713_0020_human_review_audit", "backend/app/migrations/versions/20260713_0020_human_review_audit.py", "6D6DAE32AFE775E81EE6D5ACA1172304676EB111AD2C756F6B50FDFB9CA41D4B"),
    MigrationHash("20260718_0021_human_review_scientific_decision_audit", "backend/app/migrations/versions/20260718_0021_human_review_scientific_decision_audit.py", "54704858458CE6F27B12BF8B2BE386B91C3A32F27D1D210CEC50DAD16E423B2A"),
    MigrationHash("20260827_0022_scoped_human_review_authorization", "backend/app/migrations/versions/20260827_0022_scoped_human_review_authorization.py", "2F6B9ACE942723F5A2095D1673E3F8051C5CB81E58E45141C4B4A31F4C4DDFCF"),
)

EXCLUDED_ORPHAN_MIGRATION = (
    "backend/app/migrations/versions/"
    "20260629_0002_add_scientific_production_authors.py"
)

RUNTIME_OBJECT_MANIFEST: tuple[RuntimeObject, ...] = (
    RuntimeObject(
        "uq_import_jobs_current_document",
        "index",
        "backend/app/migrations/versions/20260711_0014_dropbox_document_versioning.py",
        "CREATE UNIQUE INDEX uq_import_jobs_current_document "
        "ON import_jobs (document_key) WHERE is_current = TRUE AND document_key IS NOT NULL",
    ),
    RuntimeObject(
        "b2b_reject_career_capability",
        "function",
        "backend/app/migrations/versions/20260713_0017_b2b_capabilities.py",
        """CREATE FUNCTION b2b_reject_career_capability()
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
        $$""",
    ),
    RuntimeObject(
        "trg_user_b2b_capabilities_reject_career",
        "trigger",
        "backend/app/migrations/versions/20260713_0017_b2b_capabilities.py",
        """CREATE TRIGGER trg_user_b2b_capabilities_reject_career
        BEFORE INSERT OR UPDATE ON user_b2b_capabilities
        FOR EACH ROW
        EXECUTE FUNCTION b2b_reject_career_capability()""",
    ),
    RuntimeObject(
        "b2b_reject_career_role_with_capability",
        "function",
        "backend/app/migrations/versions/20260713_0017_b2b_capabilities.py",
        """CREATE FUNCTION b2b_reject_career_role_with_capability()
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
        $$""",
    ),
    RuntimeObject(
        "trg_users_reject_career_with_b2b_capability",
        "trigger",
        "backend/app/migrations/versions/20260713_0017_b2b_capabilities.py",
        """CREATE TRIGGER trg_users_reject_career_with_b2b_capability
        BEFORE UPDATE OF role ON users
        FOR EACH ROW
        EXECUTE FUNCTION b2b_reject_career_role_with_capability()""",
    ),
    RuntimeObject(
        "b2b_reject_append_only_mutation",
        "function",
        "backend/app/migrations/versions/20260713_0018_human_review_core.py",
        """CREATE FUNCTION b2b_reject_append_only_mutation()
        RETURNS TRIGGER
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'review_decisions is append-only'
                USING ERRCODE = '55000';
        END;
        $$""",
    ),
    RuntimeObject(
        "trg_review_decisions_append_only",
        "trigger",
        "backend/app/migrations/versions/20260713_0018_human_review_core.py",
        """CREATE TRIGGER trg_review_decisions_append_only
        BEFORE UPDATE OR DELETE OR TRUNCATE ON review_decisions
        FOR EACH STATEMENT
        EXECUTE FUNCTION b2b_reject_append_only_mutation()""",
    ),
    RuntimeObject(
        "trg_audit_events_append_only",
        "trigger",
        "backend/app/migrations/versions/20260713_0020_human_review_audit.py",
        """CREATE TRIGGER trg_audit_events_append_only
        BEFORE UPDATE OR DELETE OR TRUNCATE ON audit_events
        FOR EACH STATEMENT
        EXECUTE FUNCTION b2b_reject_append_only_mutation()""",
    ),
)

_ALLOWED_SCHEMAS = frozenset(("information_schema", "pg_catalog", "pg_toast", "public"))
_ALLOWED_EXTENSIONS = frozenset(("plpgsql",))
_ROLE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_REGISTRATION_AUTHORIZED: ContextVar[bool] = ContextVar(
    "prototype_baseline_registration_authorized", default=False
)
_APPLICATION_ROLE: ContextVar[str | None] = ContextVar(
    "prototype_baseline_application_role", default=None
)


def _read_fresh_database_state(
    owner_engine: Engine,
    application_role: str,
) -> dict[str, object]:
    with owner_engine.connect() as connection:
        identity = connection.execute(text(
            """
            SELECT current_user AS current_user,
                   current_schema() AS schema_name,
                   schema_owner.rolname AS schema_owner,
                   (schema_owner.rolname = current_user OR
                    pg_has_role(current_user, schema_owner.rolname, 'MEMBER'))
                       AS owner_controls_schema
            FROM pg_namespace AS schema_row
            JOIN pg_roles AS schema_owner ON schema_owner.oid = schema_row.nspowner
            WHERE schema_row.nspname = current_schema()
            """
        )).mappings().one()
        role = connection.execute(text(
            """
            SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname = :role) AS role_exists,
                   COALESCE((SELECT rolsuper FROM pg_roles WHERE rolname = :role), false) AS is_superuser,
                   COALESCE((SELECT rolcreatedb FROM pg_roles WHERE rolname = :role), false) AS can_create_db,
                   COALESCE((SELECT rolcreaterole FROM pg_roles WHERE rolname = :role), false) AS can_create_role,
                   COALESCE((SELECT rolreplication FROM pg_roles WHERE rolname = :role), false) AS can_replicate,
                   COALESCE((SELECT rolbypassrls FROM pg_roles WHERE rolname = :role), false) AS bypasses_rls
            """
        ), {"role": application_role}).mappings().one()
        schemas = tuple(connection.execute(text(
            "SELECT nspname FROM pg_namespace "
            "WHERE nspname NOT LIKE 'pg_temp_%' "
            "AND nspname NOT LIKE 'pg_toast_temp_%' ORDER BY nspname"
        )).scalars())
        extensions = tuple(connection.execute(text(
            "SELECT extname FROM pg_extension ORDER BY extname"
        )).scalars())
        relation_rows = tuple(connection.execute(text(
            """
            SELECT namespace.nspname, relation.relname,
                   CASE relation.relkind
                     WHEN 'r' THEN 'table'
                     WHEN 'p' THEN 'table'
                     WHEN 'S' THEN 'sequence'
                     WHEN 'v' THEN 'view'
                     WHEN 'm' THEN 'materialized view'
                     WHEN 'f' THEN 'foreign table'
                     ELSE relation.relkind::text
                   END AS relation_kind
            FROM pg_class AS relation
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = current_schema()
              AND relation.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
            ORDER BY relation.relname
            """
        )))
        functions = tuple(connection.execute(text(
            """
            SELECT namespace.nspname, function_row.proname
            FROM pg_proc AS function_row
            JOIN pg_namespace AS namespace ON namespace.oid = function_row.pronamespace
            WHERE namespace.nspname = current_schema()
            ORDER BY function_row.proname
            """
        )))
        triggers = tuple(connection.execute(text(
            """
            SELECT namespace.nspname, trigger_row.tgname
            FROM pg_trigger AS trigger_row
            JOIN pg_class AS table_row ON table_row.oid = trigger_row.tgrelid
            JOIN pg_namespace AS namespace ON namespace.oid = table_row.relnamespace
            WHERE namespace.nspname = current_schema()
              AND NOT trigger_row.tgisinternal
            ORDER BY trigger_row.tgname
            """
        )))
    return {
        "current_user": identity["current_user"],
        "application_role_exists": role["role_exists"],
        "application_role_is_superuser": role["is_superuser"],
        "application_role_can_create_db": role["can_create_db"],
        "application_role_can_create_role": role["can_create_role"],
        "application_role_can_replicate": role["can_replicate"],
        "application_role_bypasses_rls": role["bypasses_rls"],
        "schema_name": identity["schema_name"],
        "schema_owner": identity["schema_owner"],
        "owner_controls_schema": identity["owner_controls_schema"],
        "schemas": schemas,
        "extensions": extensions,
        "relations": tuple(tuple(row) for row in relation_rows),
        "functions": tuple(tuple(row) for row in functions),
        "triggers": tuple(tuple(row) for row in triggers),
        "scientific_or_demo_rows": 0,
    }


def prove_fresh_prototype_database(
    owner_engine: Engine,
    application_role: str,
) -> None:
    if not _ROLE_NAME_PATTERN.fullmatch(application_role):
        raise BaselineRefused("runtime role name is invalid")
    state = _read_fresh_database_state(owner_engine, application_role)
    if not state["application_role_exists"]:
        raise BaselineRefused("distinct runtime role does not exist")
    if state["current_user"] == application_role:
        raise BaselineRefused("distinct runtime role is required")
    if any(
        bool(state[name])
        for name in (
            "application_role_is_superuser",
            "application_role_can_create_db",
            "application_role_can_create_role",
            "application_role_can_replicate",
            "application_role_bypasses_rls",
        )
    ):
        raise BaselineRefused("runtime role escalation capability is forbidden")
    if state["schema_owner"] == application_role:
        raise BaselineRefused("runtime role ownership of the application schema is forbidden")
    if not state["owner_controls_schema"]:
        raise BaselineRefused("migration owner does not control the application schema")

    schemas = set(state["schemas"])
    if schemas != _ALLOWED_SCHEMAS:
        raise BaselineRefused(
            f"unexpected schema set: {sorted(schemas - _ALLOWED_SCHEMAS)}"
        )
    extensions = set(state["extensions"])
    if extensions != _ALLOWED_EXTENSIONS:
        raise BaselineRefused(
            f"unexpected extension set: {sorted(extensions - _ALLOWED_EXTENSIONS)}"
        )

    relations = tuple(state["relations"])
    for _schema, name, kind in relations:
        if name == "schema_migrations":
            raise BaselineRefused("schema_migrations already exists")
        if kind == "sequence":
            raise BaselineRefused(f"application sequence exists: {name}")
        if kind in ("view", "materialized view"):
            raise BaselineRefused(f"user view exists: {name}")
        raise BaselineRefused(f"application table or relation exists: {name}")
    if state["functions"]:
        raise BaselineRefused("application function exists")
    if state["triggers"]:
        raise BaselineRefused("application trigger exists")
    if state["scientific_or_demo_rows"]:
        raise BaselineRefused("scientific/demo rows exist")


def install_runtime_object_manifest(owner_engine: Engine) -> Sequence[str]:
    try:
        with owner_engine.begin() as connection:
            for runtime_object in RUNTIME_OBJECT_MANIFEST:
                connection.execute(text(runtime_object.ddl))
    except Exception as exc:
        raise BaselineRefused(
            f"runtime object manifest installation failed: {exc}"
        ) from exc
    return tuple(item.name for item in RUNTIME_OBJECT_MANIFEST)


def _normalize_fingerprint(value: object) -> object:
    if isinstance(value, dict):
        return {key: _normalize_fingerprint(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return tuple(_normalize_fingerprint(item) for item in value)
    if isinstance(value, (set, frozenset)):
        normalized = (_normalize_fingerprint(item) for item in value)
        return tuple(sorted(normalized, key=repr))
    if isinstance(value, str):
        return " ".join(value.split())
    return value


def _canonical_sql(value: object) -> str:
    sql = str(value).lower().replace('"', "")
    sql = re.sub(r"\bpublic\.", "", sql)
    sql = re.sub(
        r"::(?:character varying|timestamp without time zone|timestamp with time zone|"
        r"double precision|bigint|integer|smallint|boolean|text|uuid|jsonb|date)"
        r"(?:\[\])?",
        "",
        sql,
    )
    sql = re.sub(r"\s+", " ", sql).strip().rstrip(";")
    return sql


def _strip_redundant_boolean_parentheses(sql: str) -> str:
    while True:
        stack: list[int] = []
        removable: tuple[int, int] | None = None
        for position, character in enumerate(sql):
            if character == "(":
                stack.append(position)
            elif character == ")" and stack:
                start = stack.pop()
                content = sql[start + 1:position]
                if start > 0 and (sql[start - 1].isalnum() or sql[start - 1] == "_"):
                    continue
                lowered = f" {content.lower()} "
                has_and = " and " in lowered
                has_or = " or " in lowered
                has_comparison = any(
                    marker in lowered
                    for marker in (" = ", " <> ", " is ", " in ", " ~ ")
                )
                outside_left = f" {sql[:start].lower()} "
                outside_right = f" {sql[position + 1:].lower()} "
                adjacent_and = outside_left.rstrip().endswith(" and") or outside_right.lstrip().startswith("and ")
                if (has_and and not has_or) or (has_comparison and not has_or):
                    removable = (start, position)
                    break
                if has_or and not adjacent_and and not stack:
                    removable = (start, position)
                    break
        if removable is None:
            return sql
        start, end = removable
        sql = f"{sql[:start]}{sql[start + 1:end]}{sql[end + 1:]}"
        sql = re.sub(r"\s+", " ", sql).strip()


def _canonical_check_sql(value: object) -> str:
    sql = _canonical_sql(value)
    sql = re.sub(r"\(([a-z_][a-z0-9_]*)\)", r"\1", sql)
    any_start = re.compile(r"\b([a-z_][a-z0-9_]*)\s*=\s*any\s*\(")
    while (match := any_start.search(sql)) is not None:
        opening = match.end() - 1
        depth = 0
        closing = -1
        for position in range(opening, len(sql)):
            if sql[position] == "(":
                depth += 1
            elif sql[position] == ")":
                depth -= 1
                if depth == 0:
                    closing = position
                    break
        if closing < 0:
            break
        array_expression = sql[opening + 1:closing].strip()
        while array_expression.startswith("(") and array_expression.endswith(")"):
            array_expression = array_expression[1:-1].strip()
        array_match = re.fullmatch(r"array\[(.*)\]", array_expression)
        if not array_match:
            break
        replacement = f"{match.group(1)} in ({array_match.group(1)})"
        sql = f"{sql[:match.start()]}{replacement}{sql[closing + 1:]}"
    return _strip_redundant_boolean_parentheses(sql)


def _canonical_index_sql(value: object) -> str:
    sql = _canonical_sql(value).replace(" using btree ", " ")
    sql = re.sub(
        r"coalesce\(([a-z_][a-z0-9_]*), '(-?\d+)'\)",
        r"coalesce(\1, \2)",
        sql,
    )
    if " where " in sql:
        prefix, predicate = sql.split(" where ", 1)
        sql = f"{prefix} where {_canonical_check_sql(predicate)}"
    return sql


def _canonical_trigger_sql(value: object) -> str:
    sql = _canonical_sql(value)
    match = re.fullmatch(
        r"create trigger ([a-z_][a-z0-9_]*) "
        r"(before|after|instead of) (.*?) on ([a-z_][a-z0-9_]*) "
        r"for each (row|statement) execute function ([a-z_][a-z0-9_]*)\(\)",
        sql,
    )
    if not match:
        raise BaselineRefused(f"cannot normalize runtime trigger definition: {sql}")
    events = " or ".join(sorted(part.strip() for part in match.group(3).split(" or ")))
    return (
        f"create trigger {match.group(1)} {match.group(2)} {events} "
        f"on {match.group(4)} for each {match.group(5)} "
        f"execute function {match.group(6)}()"
    )


def _canonical_function_sql(value: object) -> str:
    sql = str(value)
    result_match = re.search(r"returns\s+([^\s]+)", sql, re.IGNORECASE)
    language_match = re.search(r"language\s+([^\s]+)", sql, re.IGNORECASE)
    body_match = re.search(
        r"as\s+\$(?P<tag>[a-zA-Z0-9_]*)\$(?P<body>.*?)\$(?P=tag)\$",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    if not result_match or not language_match or not body_match:
        raise BaselineRefused("cannot normalize runtime function definition")
    return _canonical_sql(
        f"returns {result_match.group(1)} language {language_match.group(1)} "
        f"body {body_match.group('body')}"
    )


def _canonical_type(type_value: object) -> str:
    canonical = _canonical_sql(
        type_value.compile(dialect=postgresql.dialect())
        if hasattr(type_value, "compile")
        else type_value
    )
    if canonical == "float":
        return "double precision"
    if canonical == "varchar" or canonical.startswith("varchar("):
        return canonical.replace("varchar", "character varying", 1)
    return canonical


def _metadata_column_default(column: object) -> str | None:
    server_default = getattr(column, "server_default", None)
    if server_default is not None:
        return _canonical_sql(server_default.arg)
    if (
        getattr(column, "primary_key", False)
        and getattr(column, "autoincrement", None) is not False
        and _canonical_type(getattr(column, "type")) in {"integer", "bigint", "smallint"}
    ):
        return "<sequence>"
    return None


def _reflected_column_default(value: object) -> str | None:
    if value is None:
        return None
    canonical = _canonical_sql(value)
    if canonical.startswith("nextval("):
        return "<sequence>"
    return canonical


def _metadata_index_fingerprint() -> tuple[tuple[object, ...], ...]:
    indexes: list[tuple[object, ...]] = []
    for table_name, table in sorted(Base.metadata.tables.items()):
        for index in sorted(table.indexes, key=lambda item: item.name or ""):
            ddl = _canonical_index_sql(
                CreateIndex(index).compile(dialect=postgresql.dialect())
            )
            indexes.append((table_name, index.name, bool(index.unique), ddl))
    partial = next(
        item for item in RUNTIME_OBJECT_MANIFEST
        if item.name == "uq_import_jobs_current_document"
    )
    indexes.append((
        "import_jobs", partial.name, True, _canonical_index_sql(partial.ddl)
    ))
    return tuple(indexes)


def _runtime_function_definition(runtime_object: RuntimeObject) -> str:
    return _canonical_function_sql(runtime_object.ddl)


def _runtime_trigger_definition(runtime_object: RuntimeObject) -> tuple[str, str, str]:
    table_match = re.search(
        r"\bon\s+([a-zA-Z_][a-zA-Z0-9_]*)",
        runtime_object.ddl,
        re.IGNORECASE,
    )
    if not table_match:
        raise BaselineRefused(f"cannot derive runtime trigger: {runtime_object.name}")
    return (
        runtime_object.name,
        table_match.group(1),
        _canonical_trigger_sql(runtime_object.ddl),
    )


def _expected_runtime_fingerprint(
    phase: Literal["pre_registration", "final"],
) -> Mapping[str, object]:
    tables = tuple(sorted(Base.metadata.tables))
    columns: list[tuple[object, ...]] = []
    primary_keys: list[tuple[object, ...]] = []
    foreign_keys: list[tuple[object, ...]] = []
    unique_constraints: list[tuple[object, ...]] = []
    checks: list[tuple[object, ...]] = []
    for table_name, table in sorted(Base.metadata.tables.items()):
        for column in table.columns:
            columns.append((
                table_name,
                column.name,
                _canonical_type(column.type),
                bool(column.nullable),
                _metadata_column_default(column),
            ))
        primary_keys.append((
            table_name,
            table.primary_key.name or f"{table_name}_pkey",
            tuple(column.name for column in table.primary_key),
        ))
        for constraint in table.constraints:
            if isinstance(constraint, ForeignKeyConstraint):
                elements = tuple(constraint.elements)
                foreign_keys.append((
                    table_name,
                    tuple(element.parent.name for element in elements),
                    elements[0].column.table.name,
                    tuple(element.column.name for element in elements),
                    constraint.ondelete,
                    constraint.onupdate,
                    constraint.deferrable,
                    constraint.initially,
                ))
            elif isinstance(constraint, UniqueConstraint):
                unique_constraints.append((
                    table_name,
                    tuple(column.name for column in constraint.columns),
                ))
            elif isinstance(constraint, CheckConstraint):
                checks.append((
                    table_name,
                    constraint.name,
                    _canonical_check_sql(constraint.sqltext),
                ))

    functions = tuple(
        (item.name, _runtime_function_definition(item))
        for item in RUNTIME_OBJECT_MANIFEST
        if item.kind == "function"
    )
    triggers = tuple(
        _runtime_trigger_definition(item)
        for item in RUNTIME_OBJECT_MANIFEST
        if item.kind == "trigger"
    )
    return {
        "tables": tables,
        "columns": tuple(columns),
        "primary_keys": tuple(primary_keys),
        "foreign_keys": tuple(foreign_keys),
        "unique_constraints": tuple(unique_constraints),
        "checks": tuple(checks),
        "indexes": _metadata_index_fingerprint(),
        "functions": functions,
        "triggers": triggers,
        "schemas": tuple(sorted(_ALLOWED_SCHEMAS)),
        "extensions": tuple(sorted(_ALLOWED_EXTENSIONS)),
        "migration_control": (
            "absent"
            if phase == "pre_registration"
            else tuple(item.version for item in MIGRATION_SHA256_MANIFEST)
        ),
        "privileges": (
            "approved pre-registration isolation"
            if phase == "pre_registration"
            else "approved final least privilege"
        ),
    }


def _read_pre_registration_privileges(
    connection: Connection,
    application_role: str,
    schema_name: str,
) -> str:
    role_state = connection.execute(text(
        """
        SELECT role_row.rolsuper, role_row.rolcreatedb, role_row.rolcreaterole,
               role_row.rolreplication, role_row.rolbypassrls,
               has_schema_privilege(:role, :schema, 'CREATE') AS schema_create
        FROM pg_roles AS role_row
        WHERE role_row.rolname = :role
        """
    ), {"role": application_role, "schema": schema_name}).mappings().one()
    if any(bool(role_state[name]) for name in (
        "rolsuper", "rolcreatedb", "rolcreaterole", "rolreplication",
        "rolbypassrls", "schema_create",
    )):
        raise BaselineRefused("pre-registration runtime role isolation differs")
    ownership = connection.execute(text(
        """
        SELECT
          (SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
           JOIN pg_roles r ON r.oid=c.relowner
           WHERE n.nspname=:schema AND r.rolname=:role)
          +
          (SELECT COUNT(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           JOIN pg_roles r ON r.oid=p.proowner
           WHERE n.nspname=:schema AND r.rolname=:role)
        """
    ), {"role": application_role, "schema": schema_name}).scalar_one()
    grants = connection.execute(text(
        """
        SELECT COUNT(*) FROM information_schema.role_table_grants
        WHERE grantee=:role
        """
    ), {"role": application_role}).scalar_one()
    if ownership or grants:
        raise BaselineRefused("unexpected pre-registration ownership or table grant")
    return "approved pre-registration isolation"


def _read_runtime_fingerprint(
    owner_engine: Engine,
    phase: Literal["pre_registration", "final"],
) -> Mapping[str, object]:
    application_role = _APPLICATION_ROLE.get()
    if not application_role:
        raise BaselineRefused("runtime role is unavailable for fingerprint validation")
    with owner_engine.connect() as connection:
        inspector = inspect(connection)
        schema_name = connection.execute(text("SELECT current_schema()" )).scalar_one()
        table_names = tuple(sorted(
            name for name in inspector.get_table_names(schema=schema_name)
            if name != "schema_migrations"
        ))
        columns = tuple(
            (
                table_name,
                column["name"],
                _canonical_type(column["type"]),
                bool(column["nullable"]),
                _reflected_column_default(column.get("default")),
            )
            for table_name in table_names
            for column in inspector.get_columns(table_name, schema=schema_name)
        )
        primary_keys = tuple(
            (
                table_name,
                inspector.get_pk_constraint(table_name, schema=schema_name)["name"],
                tuple(inspector.get_pk_constraint(table_name, schema=schema_name)["constrained_columns"]),
            )
            for table_name in table_names
        )
        foreign_keys = tuple(
            (
                table_name,
                tuple(foreign_key["constrained_columns"]),
                foreign_key["referred_table"],
                tuple(foreign_key["referred_columns"]),
                foreign_key.get("options", {}).get("ondelete"),
                foreign_key.get("options", {}).get("onupdate"),
                foreign_key.get("options", {}).get("deferrable"),
                foreign_key.get("options", {}).get("initially"),
            )
            for table_name in table_names
            for foreign_key in inspector.get_foreign_keys(table_name, schema=schema_name)
        )
        unique_constraints = tuple(
            (table_name, tuple(constraint["column_names"]))
            for table_name in table_names
            for constraint in inspector.get_unique_constraints(table_name, schema=schema_name)
        )
        checks = tuple(
            (table_name, constraint["name"], _canonical_check_sql(constraint["sqltext"]))
            for table_name in table_names
            for constraint in inspector.get_check_constraints(table_name, schema=schema_name)
        )
        indexes = tuple(
            (
                row.table_name,
                row.index_name,
                bool(row.is_unique),
                _canonical_index_sql(row.index_definition),
            )
            for row in connection.execute(text(
                """
                SELECT table_row.relname AS table_name,
                       index_row.relname AS index_name,
                       index_catalog.indisunique AS is_unique,
                       pg_get_indexdef(index_catalog.indexrelid) AS index_definition
                FROM pg_index AS index_catalog
                JOIN pg_class AS table_row ON table_row.oid=index_catalog.indrelid
                JOIN pg_class AS index_row ON index_row.oid=index_catalog.indexrelid
                JOIN pg_namespace AS namespace ON namespace.oid=table_row.relnamespace
                WHERE namespace.nspname=:schema
                  AND NOT index_catalog.indisprimary
                  AND NOT EXISTS (
                    SELECT 1 FROM pg_constraint constraint_row
                    WHERE constraint_row.conindid=index_catalog.indexrelid
                  )
                ORDER BY table_row.relname, index_row.relname
                """
            ), {"schema": schema_name})
        )
        functions = tuple(
            (
                row.function_name,
                _canonical_function_sql(row.function_definition),
            )
            for row in connection.execute(text(
                """
                SELECT function_row.proname AS function_name,
                       pg_get_functiondef(function_row.oid) AS function_definition
                FROM pg_proc AS function_row
                JOIN pg_namespace AS namespace ON namespace.oid=function_row.pronamespace
                WHERE namespace.nspname=:schema AND function_row.pronargs=0
                ORDER BY function_row.proname
                """
            ), {"schema": schema_name})
        )
        triggers = tuple(
            (
                row.trigger_name,
                row.table_name,
                _canonical_trigger_sql(pg_definition),
            )
            for row in connection.execute(text(
                """
                SELECT trigger_row.tgname AS trigger_name,
                       table_row.relname AS table_name,
                       pg_get_triggerdef(trigger_row.oid, true) AS trigger_definition
                FROM pg_trigger AS trigger_row
                JOIN pg_class AS table_row ON table_row.oid=trigger_row.tgrelid
                JOIN pg_namespace AS namespace ON namespace.oid=table_row.relnamespace
                WHERE namespace.nspname=:schema AND NOT trigger_row.tgisinternal
                ORDER BY trigger_row.tgname
                """
            ), {"schema": schema_name})
            for pg_definition in (row.trigger_definition,)
        )
        schemas = tuple(connection.execute(text(
            "SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_temp_%' "
            "AND nspname NOT LIKE 'pg_toast_temp_%' ORDER BY nspname"
        )).scalars())
        extensions = tuple(connection.execute(text(
            "SELECT extname FROM pg_extension ORDER BY extname"
        )).scalars())
        control_exists = inspector.has_table("schema_migrations", schema=schema_name)
        if phase == "pre_registration":
            migration_control: object = "absent" if not control_exists else "present"
            privileges = _read_pre_registration_privileges(
                connection, application_role, schema_name
            )
        else:
            if not control_exists:
                migration_control = "absent"
            else:
                control_columns = inspector.get_columns(
                    "schema_migrations", schema=schema_name
                )
                control_pk = inspector.get_pk_constraint(
                    "schema_migrations", schema=schema_name
                )
                control_shape = tuple(
                    (
                        column["name"],
                        _canonical_type(column["type"]),
                        bool(column["nullable"]),
                        _reflected_column_default(column.get("default")),
                    )
                    for column in control_columns
                )
                if control_shape != (
                    ("version", "character varying(120)", False, None),
                    ("applied_at", "timestamp without time zone", True, "current_timestamp"),
                ) or tuple(control_pk["constrained_columns"]) != ("version",):
                    raise BaselineRefused("schema_migrations control-table shape differs")
                migration_control = tuple(connection.execute(text(
                    "SELECT version FROM schema_migrations ORDER BY applied_at, version"
                )).scalars())
            try:
                owner_role, validated_schema, _schema_owner = _validate_roles(
                    connection, application_role
                )
                existing_tables, sequence_rows = _validate_owned_objects(
                    connection, owner_role, validated_schema
                )
                _verify_privileges(
                    connection,
                    application_role,
                    validated_schema,
                    existing_tables,
                    sequence_rows,
                )
            except ValueError as exc:
                raise BaselineRefused(f"final runtime ACL differs: {exc}") from exc
            privileges = "approved final least privilege"
    return {
        "tables": table_names,
        "columns": columns,
        "primary_keys": primary_keys,
        "foreign_keys": foreign_keys,
        "unique_constraints": unique_constraints,
        "checks": checks,
        "indexes": indexes,
        "functions": functions,
        "triggers": triggers,
        "schemas": schemas,
        "extensions": extensions,
        "migration_control": migration_control,
        "privileges": privileges,
    }


def validate_runtime_fingerprint(
    owner_engine: Engine,
    *,
    phase: Literal["pre_registration", "final"],
) -> None:
    if phase not in ("pre_registration", "final"):
        raise BaselineRefused(f"unsupported fingerprint phase: {phase}")
    expected = _expected_runtime_fingerprint(phase)
    actual = _read_runtime_fingerprint(owner_engine, phase)
    expected_keys = set(expected)
    actual_keys = set(actual)
    if expected_keys != actual_keys:
        raise BaselineRefused(
            "fingerprint key mismatch: "
            f"missing={sorted(expected_keys - actual_keys)}, "
            f"unexpected={sorted(actual_keys - expected_keys)}"
        )
    for key in sorted(expected):
        normalized_actual = _normalize_fingerprint(actual[key])
        normalized_expected = _normalize_fingerprint(expected[key])
        if key != "migration_control" and isinstance(normalized_actual, tuple):
            normalized_actual = tuple(sorted(normalized_actual, key=repr))
            normalized_expected = tuple(sorted(normalized_expected, key=repr))
        if normalized_actual != normalized_expected:
            raise BaselineRefused(f"runtime fingerprint mismatch: {key}")


def verify_migration_hash_manifest(repository_root: Path) -> Sequence[str]:
    repository_root = repository_root.resolve()
    expected_versions = tuple(item.version for item in MIGRATION_SHA256_MANIFEST)
    runner_versions = tuple(version for version, _upgrade in MIGRATIONS)
    if runner_versions != expected_versions:
        raise BaselineRefused("ordered MIGRATIONS identifiers differ from the approved manifest")
    versions_directory = repository_root / "backend/app/migrations/versions"
    if not versions_directory.is_dir():
        raise BaselineRefused("migration versions directory is missing")
    allowed_paths = {
        (repository_root / item.relative_path).resolve()
        for item in MIGRATION_SHA256_MANIFEST
    }
    allowed_paths.add((repository_root / EXCLUDED_ORPHAN_MIGRATION).resolve())
    unexpected = tuple(
        path
        for path in versions_directory.glob("20*.py")
        if path.resolve() not in allowed_paths
    )
    if unexpected:
        raise BaselineRefused(
            "unexpected executable-looking migration file: "
            + ", ".join(path.name for path in unexpected)
        )
    for item in MIGRATION_SHA256_MANIFEST:
        path = (repository_root / item.relative_path).resolve()
        if not path.is_file():
            raise BaselineRefused(f"migration file is missing: {item.version}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest().upper()
        if actual != item.sha256:
            raise BaselineRefused(
                f"migration SHA-256 mismatch: {item.version}"
            )
    orphan = (repository_root / EXCLUDED_ORPHAN_MIGRATION).resolve()
    if not orphan.is_file():
        raise BaselineRefused("approved excluded orphan migration is missing")
    return expected_versions


def ensure_schema_migrations_table(owner_engine: Engine) -> None:
    with owner_engine.begin() as connection:
        _ensure_control_table(connection)


@contextmanager
def _allow_baseline_registration() -> Iterator[None]:
    token = _REGISTRATION_AUTHORIZED.set(True)
    try:
        yield
    finally:
        _REGISTRATION_AUTHORIZED.reset(token)


def register_verified_baseline(
    owner_engine: Engine,
    versions: Sequence[str],
) -> None:
    if not _REGISTRATION_AUTHORIZED.get():
        raise BaselineRefused(
            "baseline registration is allowed only inside verified bootstrap"
        )
    expected_versions = tuple(item.version for item in MIGRATION_SHA256_MANIFEST)
    if tuple(versions) != expected_versions:
        raise BaselineRefused("baseline versions differ from the approved ordered manifest")
    try:
        with owner_engine.begin() as connection:
            existing = connection.execute(
                text("SELECT version FROM schema_migrations")
            ).fetchall()
            if existing:
                raise BaselineRefused(
                    "schema_migrations must be empty before verified baseline registration"
                )
            connection.execute(
                text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                [{"version": version} for version in expected_versions],
            )
            count = connection.execute(
                text("SELECT COUNT(*) FROM schema_migrations")
            ).scalar_one()
            if count != len(expected_versions):
                raise BaselineRefused("partial baseline registration detected")
    except BaselineRefused:
        raise
    except Exception as exc:
        raise BaselineRefused(f"baseline registration failed: {exc}") from exc


_HISTORICAL_DEFAULT_CLIENT_FALLBACKS = (
    ("import_batches", "source_type"),
    ("import_batches", "status"),
    ("import_batches", "total_files"),
    ("import_batches", "created_at"),
    ("import_jobs", "retry_count"),
    ("import_jobs", "max_retries"),
    ("import_jobs", "used_ocr"),
    ("import_jobs", "is_current"),
    ("person_roles", "created_at"),
    ("person_roles", "validation_status"),
    ("import_normalization_audits", "created_at"),
    ("scientific_production_authors", "created_at"),
    ("scientific_production_authors", "validation_status"),
    ("scientific_productions", "status"),
    ("scientific_productions", "validation_status"),
    ("research_projects", "status"),
    ("research_projects", "progress_percentage"),
    ("research_entities", "validation_status"),
    ("research_entities", "created_at"),
    ("teachers", "validation_status"),
    ("external_researchers", "source_section"),
    ("external_researchers", "requires_review"),
    ("external_researchers", "created_at"),
    ("import_review_items", "created_at"),
)

_HISTORICAL_PK_NAME_DELTAS = {
    "pk_user_b2b_capabilities": "user_b2b_capabilities_pkey",
    "pk_review_items": "review_items_pkey",
    "pk_review_decisions": "review_decisions_pkey",
    "pk_canonical_identities": "canonical_identities_pkey",
    "pk_person_aliases": "person_aliases_pkey",
    "pk_field_overrides": "field_overrides_pkey",
    "pk_audit_events": "audit_events_pkey",
}
_HISTORICAL_PK_NAMES = tuple(_HISTORICAL_PK_NAME_DELTAS)


def _functions_with_production_deletes(path: Path) -> tuple[str, ...]:
    if not path.is_file():
        raise BaselineRefused(f"known-delta evidence source is missing: {path}")
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    unsafe: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        delete_targets: list[tuple[int, str]] = []
        for descendant in ast.walk(node):
            if not (
                isinstance(descendant, ast.Call)
                and isinstance(descendant.func, ast.Attribute)
                and descendant.func.attr == "delete"
            ):
                continue
            call_source = ast.get_source_segment(source, descendant) or ""
            if re.search(r"query\(\s*ScientificProduction\s*\)", call_source):
                delete_targets.append((descendant.lineno, "production"))
            elif re.search(
                r"query\(\s*ScientificProductionAuthor\s*\)", call_source
            ):
                delete_targets.append((descendant.lineno, "author"))
        author_deletes = tuple(
            line for line, target in delete_targets if target == "author"
        )
        if any(
            not any(author_line < production_line for author_line in author_deletes)
            for production_line, target in delete_targets
            if target == "production"
        ):
            unsafe.append(f"{path.name}:{node.name}")
    return tuple(unsafe)


def verify_known_catalog_deltas(repository_root: Path) -> KnownDeltaReport:
    author_table = Base.metadata.tables["scientific_production_authors"]
    production_fk = next(
        fk
        for fk in author_table.foreign_key_constraints
        if tuple(fk.column_keys) == ("production_id",)
    )
    if production_fk.ondelete is not None:
        raise BaselineRefused("author FK delta is no longer the approved NO ACTION state")
    unsafe_deletes: list[str] = []
    for relative in (
        "backend/app/api/v1/endpoints/admin.py",
        "backend/app/services/import_service.py",
    ):
        unsafe_deletes.extend(
            _functions_with_production_deletes(repository_root / relative)
        )
    if unsafe_deletes:
        raise BaselineRefused(
            "author FK CASCADE is required by a supported delete path: "
            + ", ".join(unsafe_deletes)
        )

    missing_client_defaults = tuple(
        f"{table_name}.{column_name}"
        for table_name, column_name in _HISTORICAL_DEFAULT_CLIENT_FALLBACKS
        if Base.metadata.tables[table_name].c[column_name].default is None
        and Base.metadata.tables[table_name].c[column_name].server_default is None
    )
    if missing_client_defaults:
        raise BaselineRefused(
            "historical server default is required by current ORM writes: "
            + ", ".join(missing_client_defaults)
        )

    runtime_paths = tuple((repository_root / "backend/app").rglob("*.py")) + tuple(
        (repository_root / "backend/scripts").rglob("*.py")
    )
    historical_name_dependencies: list[str] = []
    for path in runtime_paths:
        if "migrations/versions" in path.as_posix():
            continue
        if path.resolve() == Path(__file__).resolve():
            # This verifier owns the explicit closed list of accepted historical
            # names; that evidence list is not a runtime dependency on them.
            continue
        source = path.read_text(encoding="utf-8")
        if any(name in source for name in _HISTORICAL_PK_NAMES):
            historical_name_dependencies.append(str(path.relative_to(repository_root)))
    if historical_name_dependencies:
        raise BaselineRefused(
            "historical PK name dependency found: "
            + ", ".join(historical_name_dependencies)
        )
    for current_name in _HISTORICAL_PK_NAME_DELTAS.values():
        table_name = current_name.removesuffix("_pkey")
        if tuple(column.name for column in Base.metadata.tables[table_name].primary_key) != ("id",):
            raise BaselineRefused(f"PK semantics differ for {table_name}")
    return KnownDeltaReport(
        author_fk="NO ACTION accepted; supported deletes remove authors first",
        historical_defaults="ORM client defaults cover audited historical server defaults",
        historical_pk_names="catalog naming delta accepted; PK/FK semantics verified",
    )


def establish_prototype_verified_schema_baseline(
    owner_engine: Engine,
    application_role: str,
    repository_root: Path,
) -> BaselineReport:
    role_token = _APPLICATION_ROLE.set(application_role)
    try:
        prove_fresh_prototype_database(owner_engine, application_role)
        Base.metadata.create_all(bind=owner_engine)
        runtime_objects = tuple(install_runtime_object_manifest(owner_engine))
        validate_runtime_fingerprint(owner_engine, phase="pre_registration")
        versions = tuple(verify_migration_hash_manifest(repository_root))
        ensure_schema_migrations_table(owner_engine)
        verify_known_catalog_deltas(repository_root)
        configure_human_review_privileges(owner_engine, application_role)
        with _allow_baseline_registration():
            register_verified_baseline(owner_engine, versions)
        validate_runtime_fingerprint(owner_engine, phase="final")
        return BaselineReport(
            versions=versions,
            runtime_objects=runtime_objects,
            application_role=application_role,
        )
    finally:
        _APPLICATION_ROLE.reset(role_token)
