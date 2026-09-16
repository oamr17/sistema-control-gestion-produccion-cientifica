from __future__ import annotations

import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url


_PREFIX_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,23}$")


def require_b2b1_test_database_url() -> str:
    database_url = os.environ.get("B2B1_TEST_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError(
            "B2B1_TEST_DATABASE_URL is required; B2B.1 PostgreSQL tests may not skip"
        )
    if make_url(database_url).get_backend_name() != "postgresql":
        raise RuntimeError("B2B1_TEST_DATABASE_URL must use PostgreSQL")
    return database_url


def create_pre_0022_domain_catalog(connection, *, seed_lock_rows: bool = False) -> None:
    """Create the domain side of a valid 0021 catalog for migration harnesses."""
    marker = "marker VARCHAR(80) NOT NULL, " if seed_lock_rows else ""
    statements = (
        "CREATE TABLE faculties (id INTEGER PRIMARY KEY, name VARCHAR(180) NOT NULL UNIQUE)",
        "CREATE TABLE careers (id INTEGER PRIMARY KEY, faculty_id INTEGER NOT NULL "
        "REFERENCES faculties(id) ON DELETE RESTRICT, name VARCHAR(180) NOT NULL UNIQUE, "
        "code VARCHAR(20) NOT NULL UNIQUE)",
        "CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(180) NOT NULL UNIQUE, "
        "full_name VARCHAR(180) NOT NULL, hashed_password VARCHAR(255) NOT NULL, "
        "role VARCHAR(40) NOT NULL, career_id INTEGER NULL REFERENCES careers(id), "
        "is_active BOOLEAN NOT NULL DEFAULT TRUE)",
        "CREATE TABLE teachers (id INTEGER PRIMARY KEY, career_id INTEGER NOT NULL "
        "REFERENCES careers(id) ON DELETE RESTRICT, full_name VARCHAR(180) NOT NULL)",
        f"CREATE TABLE research_projects (id INTEGER PRIMARY KEY, {marker}name VARCHAR(250))",
        "CREATE TABLE project_teachers (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL "
        "REFERENCES research_projects(id), teacher_id INTEGER NOT NULL REFERENCES teachers(id), "
        "CONSTRAINT uq_project_teacher UNIQUE (project_id, teacher_id))",
        f"CREATE TABLE research_entities (id INTEGER PRIMARY KEY, {marker}"
        "academic_unit VARCHAR(220), career_name VARCHAR(220), normalized_name VARCHAR(380))",
        f"CREATE TABLE external_researchers (id INTEGER PRIMARY KEY, {marker}"
        "full_name VARCHAR(180), normalized_name VARCHAR(220), institution VARCHAR(220))",
        f"CREATE TABLE scientific_productions (id INTEGER PRIMARY KEY, {marker}"
        "teacher_id INTEGER NULL REFERENCES teachers(id), research_entity_id INTEGER NULL "
        "REFERENCES research_entities(id), title VARCHAR(250))",
        f"CREATE TABLE scientific_production_authors (id INTEGER PRIMARY KEY, {marker}"
        "production_id INTEGER NULL REFERENCES scientific_productions(id), teacher_id INTEGER NULL "
        "REFERENCES teachers(id), external_researcher_id INTEGER NULL REFERENCES external_researchers(id), "
        "research_entity_id INTEGER NULL REFERENCES research_entities(id), normalized_author_name VARCHAR(220))",
        f"CREATE TABLE person_roles (id INTEGER PRIMARY KEY, {marker}"
        "teacher_id INTEGER NULL REFERENCES teachers(id), external_researcher_id INTEGER NULL "
        "REFERENCES external_researchers(id), scientific_production_id INTEGER NULL "
        "REFERENCES scientific_productions(id), research_project_id INTEGER NULL "
        "REFERENCES research_projects(id), research_entity_id INTEGER NULL "
        "REFERENCES research_entities(id), normalized_name VARCHAR(220), raw_value TEXT)",
    )
    for statement in statements:
        connection.execute(text(statement))

    if seed_lock_rows:
        inserts = (
            "INSERT INTO faculties (id, name) VALUES (1, 'Phase 3A Faculty')",
            "INSERT INTO careers (id, faculty_id, name, code) "
            "VALUES (1, 1, 'Phase 3A Career', 'P3A')",
            "INSERT INTO teachers (id, career_id, full_name) VALUES (1, 1, 'Phase 3A Teacher')",
            "INSERT INTO research_projects (id, marker, name) "
            "VALUES (1, 'research_projects', 'Project')",
            "INSERT INTO project_teachers (id, project_id, teacher_id) VALUES (1, 1, 1)",
            "INSERT INTO research_entities (id, marker) VALUES (1, 'research_entities')",
            "INSERT INTO external_researchers (id, marker) VALUES (1, 'external_researchers')",
            "INSERT INTO scientific_productions "
            "(id, marker, teacher_id, research_entity_id, title) "
            "VALUES (1, 'scientific_productions', 1, 1, 'Production')",
            "INSERT INTO scientific_production_authors "
            "(id, marker, production_id, teacher_id) "
            "VALUES (1, 'scientific_production_authors', 1, 1)",
            "INSERT INTO person_roles (id, marker, teacher_id) VALUES (1, 'person_roles', 1)",
        )
        for statement in inserts:
            connection.execute(text(statement))


def remove_0022_domain_artifacts(connection) -> None:
    """Return current domain metadata to the exact pre-0022 owned-object boundary."""
    connection.execute(text("DROP INDEX IF EXISTS ix_users_faculty_id"))
    connection.execute(text(
        "ALTER TABLE users DROP CONSTRAINT IF EXISTS fk_users_faculty_id_faculties"
    ))
    connection.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS faculty_id"))
    connection.execute(text(
        "ALTER TABLE careers DROP CONSTRAINT IF EXISTS uq_careers_id_faculty_id"
    ))


@contextmanager
def isolated_postgres_schema(database_url: str, prefix: str) -> Iterator[Engine]:
    if not _PREFIX_PATTERN.fullmatch(prefix):
        raise ValueError("prefix must be lowercase SQL-safe text of at most 24 characters")

    schema_name = f"{prefix}_{uuid4().hex}"
    admin_engine = create_engine(database_url, pool_pre_ping=True)
    isolated_engine: Engine | None = None
    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))

        isolated_engine = create_engine(
            database_url,
            pool_pre_ping=True,
            connect_args={"options": f"-csearch_path={schema_name}"},
        )
        yield isolated_engine
    finally:
        if isolated_engine is not None:
            isolated_engine.dispose()
        try:
            with admin_engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        finally:
            admin_engine.dispose()
