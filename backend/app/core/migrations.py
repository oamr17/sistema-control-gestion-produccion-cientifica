from collections.abc import Callable
from importlib import import_module

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine


Migration = tuple[str, Callable[[Engine], None]]

add_import_jobs_batch_id = import_module(
    "app.migrations.versions.20260628_0001_add_import_jobs_batch_id"
)
import_batches_and_job_state = import_module(
    "app.migrations.versions.20260628_0002_import_batches_and_job_state"
)
fix_import_batches_sequence = import_module(
    "app.migrations.versions.20260628_0003_fix_import_batches_sequence"
)
normalized_dashboard_status_fields = import_module(
    "app.migrations.versions.20260628_0004_normalized_dashboard_status_fields"
)
import_job_diagnostics = import_module(
    "app.migrations.versions.20260628_0005_import_job_diagnostics"
)
import_job_error_details = import_module(
    "app.migrations.versions.20260628_0006_import_job_error_details"
)
external_researchers_and_review_items = import_module(
    "app.migrations.versions.20260628_0007_external_researchers_and_review_items"
)
normalization_traceability = import_module(
    "app.migrations.versions.20260628_0008_normalization_traceability"
)
scientific_production_authors = import_module(
    "app.migrations.versions.20260629_0009_scientific_production_authors"
)
person_roles = import_module(
    "app.migrations.versions.20260629_0010_person_roles"
)
research_entities_and_pending_products = import_module(
    "app.migrations.versions.20260629_0011_research_entities_and_pending_products"
)
author_traceability = import_module(
    "app.migrations.versions.20260629_0012_author_traceability"
)
teacher_person_role_validation = import_module(
    "app.migrations.versions.20260710_0013_teacher_person_role_validation"
)
dropbox_document_versioning = import_module(
    "app.migrations.versions.20260711_0014_dropbox_document_versioning"
)
dropbox_revision_uniqueness = import_module(
    "app.migrations.versions.20260711_0015_dropbox_revision_uniqueness"
)
canonical_identity_fields = import_module(
    "app.migrations.versions.20260712_0016_canonical_identity_fields"
)
b2b_capabilities = import_module(
    "app.migrations.versions.20260713_0017_b2b_capabilities"
)
human_review_core = import_module(
    "app.migrations.versions.20260713_0018_human_review_core"
)
human_review_projection = import_module(
    "app.migrations.versions.20260713_0019_human_review_projection"
)
human_review_audit = import_module(
    "app.migrations.versions.20260713_0020_human_review_audit"
)
human_review_scientific_decision_audit = import_module(
    "app.migrations.versions.20260718_0021_human_review_scientific_decision_audit"
)
scoped_human_review_authorization = import_module(
    "app.migrations.versions.20260827_0022_scoped_human_review_authorization"
)


MIGRATIONS: list[Migration] = [
    (add_import_jobs_batch_id.VERSION, add_import_jobs_batch_id.upgrade),
    (import_batches_and_job_state.VERSION, import_batches_and_job_state.upgrade),
    (fix_import_batches_sequence.VERSION, fix_import_batches_sequence.upgrade),
    (normalized_dashboard_status_fields.VERSION, normalized_dashboard_status_fields.upgrade),
    (import_job_diagnostics.VERSION, import_job_diagnostics.upgrade),
    (import_job_error_details.VERSION, import_job_error_details.upgrade),
    (external_researchers_and_review_items.VERSION, external_researchers_and_review_items.upgrade),
    (normalization_traceability.VERSION, normalization_traceability.upgrade),
    (scientific_production_authors.VERSION, scientific_production_authors.upgrade),
    (person_roles.VERSION, person_roles.upgrade),
    (research_entities_and_pending_products.VERSION, research_entities_and_pending_products.upgrade),
    (author_traceability.VERSION, author_traceability.upgrade),
    (teacher_person_role_validation.VERSION, teacher_person_role_validation.upgrade),
    (dropbox_document_versioning.VERSION, dropbox_document_versioning.upgrade),
    (dropbox_revision_uniqueness.VERSION, dropbox_revision_uniqueness.upgrade),
    (canonical_identity_fields.VERSION, canonical_identity_fields.upgrade),
    (b2b_capabilities.VERSION, b2b_capabilities.upgrade),
    (human_review_core.VERSION, human_review_core.upgrade),
    (human_review_projection.VERSION, human_review_projection.upgrade),
    (human_review_audit.VERSION, human_review_audit.upgrade),
    (
        human_review_scientific_decision_audit.VERSION,
        human_review_scientific_decision_audit.upgrade,
    ),
    (
        scoped_human_review_authorization.VERSION,
        scoped_human_review_authorization.upgrade,
    ),
]


def ensure_schema_migrations_table(connection: Connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version VARCHAR(120) PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )


def run_migrations(engine: Engine) -> list[str]:
    with engine.begin() as connection:
        ensure_schema_migrations_table(connection)
        applied = {
            row[0]
            for row in connection.execute(text("SELECT version FROM schema_migrations")).fetchall()
        }

    executed: list[str] = []
    for version, migration in MIGRATIONS:
        if version in applied:
            continue
        migration(engine)
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                {"version": version},
            )
        executed.append(version)

    return executed
