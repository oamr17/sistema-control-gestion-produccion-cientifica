from __future__ import annotations

from datetime import UTC, datetime
import importlib
import importlib.util
import inspect
from time import perf_counter
import unittest
from uuid import UUID, uuid4

from pydantic import BaseModel, TypeAdapter, ValidationError

from app.models.human_review_enums import (
    DecisionScope,
    ReviewCaseStatus,
    ReviewCaseType,
    ReviewDecisionType,
    ReviewTargetTable,
    ScientificStatus,
)


API_MODULE = "app.schemas.human_review_api"
STATE_MODULE = "app.services.human_review_state"
UUID_A = UUID("11111111-1111-1111-1111-111111111111")
UUID_B = UUID("22222222-2222-2222-2222-222222222222")
UUID_C = UUID("33333333-3333-3333-3333-333333333333")


def _api():
    return importlib.import_module(API_MODULE)


class _PreTask2OpenRequest(BaseModel):
    expected_version: int


class _PreTask2PersonPayload(BaseModel):
    case_type: str
    canonical_name: str


class _PreTask2ProductPayload(BaseModel):
    case_type: str
    product_title: str | None = None


class _PreTask2RawErrorEnvelope(BaseModel):
    message: str
    details: dict[str, object] | None = None


class _PreTask2ContractBehaviorProofs(unittest.TestCase):
    """Behavior-level RED used only when the Task 2 schema module is absent."""

    def test_default_open_pydantic_accepts_forbidden_extra(self) -> None:
        with self.assertRaises(
            ValidationError,
            msg="PRE-TASK2 BEHAVIOR [default_open_extra] accepted a forbidden extra field",
        ):
            _PreTask2OpenRequest.model_validate({"expected_version": 1, "unexpected": True})

    def test_non_discriminated_payload_accepts_wrong_case_shape(self) -> None:
        adapter = TypeAdapter(_PreTask2PersonPayload | _PreTask2ProductPayload)
        wrong_case = {"case_type": "product", "canonical_name": "Wrong branch"}
        with self.assertRaises(
            ValidationError,
            msg="PRE-TASK2 BEHAVIOR [payload_discriminator] accepted the wrong case shape",
        ):
            adapter.validate_python(wrong_case)

    def test_absent_matrix_returns_no_server_decision(self) -> None:
        baseline_matrix: dict[tuple[ReviewCaseType, str], ReviewDecisionType] = {}
        actual = baseline_matrix.get((ReviewCaseType.PERSON_IDENTITY, "approve"))
        self.assertEqual(
            actual,
            ReviewDecisionType.VALIDATED,
            "PRE-TASK2 BEHAVIOR [decision_matrix] returned no server decision",
        )

    def test_missing_semantic_validator_accepts_reasonless_decision(self) -> None:
        def baseline_validate(request: dict[str, object]) -> dict[str, object]:
            return request

        request = _request(_person(), action="correct", reason=None)
        with self.assertRaises(
            ValueError,
            msg="PRE-TASK2 BEHAVIOR [semantic_reason] accepted a reasonless correction",
        ):
            baseline_validate(request)

    def test_raw_error_envelope_serializes_internal_values(self) -> None:
        hostile = "SELECT passwordHash FROM users at C:\\private\\service.py"
        dumped = _PreTask2RawErrorEnvelope(
            message=hostile,
            details={"constraint": "review_items_status_check"},
        ).model_dump(mode="json")
        self.assertNotEqual(
            dumped["message"],
            hostile,
            "PRE-TASK2 BEHAVIOR [safe_error_envelope] serialized internal values",
        )

    def test_pending_to_resolved_behavior_is_absent(self) -> None:
        state = importlib.import_module(STATE_MODULE)
        allowed = True
        try:
            state.assert_case_transition(ReviewCaseStatus.PENDING, ReviewCaseStatus.RESOLVED)
        except ValueError:
            allowed = False
        self.assertTrue(
            allowed,
            "PRE-TASK2 BEHAVIOR [pending_to_resolved] rejected the required transition",
        )


def _person(case_type: str = "person_identity", resolution=None):
    value = {
        "case_type": case_type,
        "canonical_identity_key": "human:one",
        "canonical_name": "Persona Uno",
        "aliases": ["Persona Uno"],
        "scientific_status": "validated",
    }
    if resolution is not None:
        value["resolution"] = resolution
    return value


def _request(payload: dict[str, object], action: str = "approve", reason=None):
    return {
        "expected_version": 1,
        "expected_current_decision_id": None,
        "action": action,
        "scope": "record",
        "payload": payload,
        "reason": reason,
        "correlation_id": UUID_A,
    }


def _counterpart_ref(
    target_type: str = "person_roles", target_id: int = 42
) -> dict[str, object]:
    return {"target_type": target_type, "target_id": target_id}


def _duplicate(resolution: str = "merged") -> dict[str, object]:
    return {
        "case_type": "possible_duplicate",
        "counterpart_ref": _counterpart_ref(),
        "resolution": resolution,
        "scientific_status": "validated",
    }


def _queue_item() -> dict[str, object]:
    return {
        "id": UUID_B,
        "case_type": "person_identity",
        "case_status": "pending",
        "scientific_status": "pending",
        "document_id": 1,
        "source_revision": None,
        "source_page": 1,
        "source_section": "section",
        "automatic_priority": 10,
        "manual_priority": None,
        "possible_kpi_impact": False,
        "version": 1,
        "created_at": datetime(2026, 7, 18, tzinfo=UTC),
    }


_CLOSURE_HOSTILE_PUBLIC_TEXT = (
    ("drive_relative", "C:private-service.py"),
    ("opaque_file_uri", "file:C:private-service.py"),
    ("opaque_sqlite_uri", "sqlite:private.db"),
    ("opaque_jdbc_uri", "jdbc:postgresql:private"),
    ("opaque_urn", "urn:private:synthetic-value"),
    ("hierarchical_uri", "https://internal.example/private"),
    ("sql_lf", "SELECT\nemail\nFROM users"),
    ("sql_crlf", "SELECT\r\nemail\r\nFROM users"),
    ("github_token", "ghp_SYNTHETIC0123456789abcdefghijklmnop"),
    (
        "jwt",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzeW50aGV0aWMifQ."
        "c3ludGhldGljLXNpZ25hdHVyZQ",
    ),
    ("aws_access_key", "AKIAIOSFODNN7EXAMPLE"),
    ("private_key_header", "-----BEGIN PRIVATE KEY-----"),
    ("generic_api_token", "sk_live_SYNTHETIC0123456789abcdefghijkl"),
    (
        "password_hash",
        "pbkdf2_sha256$600000$synthetic$synthesishashvalue123456789",
    ),
    ("bare_credentials", "synthetic_user:synthetic_pass@internal-db"),
    (
        "slashless_credential_uri",
        "postgresql:synthetic_user:synthetic_pass@internal-db",
    ),
    ("generic_constraint", "review_items_status_constraint"),
    ("camel_constraint", "reviewItemsStatusConstraint"),
    ("internal_host", "internal-db-primary"),
    ("internal_module", "backend.app.services.private_store"),
    ("internal_storage", "minio_private_bucket"),
)

_CLOSURE_SAFE_PUBLIC_TEXT = (
    "Methods and results",
    "Section 3.2 — Metodología",
    "Ciencias: evidencia pública",
    "page 12, table 3",
    "First public sentence.\nSecond public sentence.",
    "eligible_products",
)

_CANONICAL_HOSTILE_PUBLIC_TEXT = (
    # Exact independent Quality corpus.
    ("embedded_drive_after_equals", "path=C:users.txt"),
    ("embedded_file_uri", "value=file:C:users.txt"),
    ("embedded_drive_after_angle", "see<C:users.txt"),
    ("percent_encoded_windows_path", "C%3A%5CUsers%5COMAR%5Cprivate.txt"),
    ("openai_project_token_short", "sk-proj-SYNTHETIC0123456789abcdefghijklmnop"),
    ("google_api_key", "AIzaSySYNTHETIC0123456789abcdefghijklmnop"),
    ("sendgrid_token", "SG.SYNTHETIC0123456789.SYNTHETICabcdefghijklmnop"),
    ("gitlab_token", "glpat-SYNTHETIC0123456789abcdefghijklmnop"),
    ("pem_double_space", "-----BEGIN  PRIVATE KEY-----"),
    ("pem_newline", "-----BEGIN\nPRIVATE KEY-----"),
    ("sql_truncate", "TRUNCATE TABLE review_items"),
    ("sql_grant", "GRANT SELECT ON review_items TO app_user"),
    # Exact independent Spec corpus.
    ("encoded_traversal", "%2e%2e%2finternal%2fsecret.pdf"),
    ("fullwidth_drive_path", "C\uff1aprivate\uff3cevidence.pdf"),
    ("unicode_traversal_separators", "..\uff0fprivate\uff0fevidence.pdf"),
    ("embedded_sqlite_uri", "dsn=sqlite:records.db"),
    ("embedded_data_uri", "payload=data:text,synthetic-secret"),
    ("openai_project_token_long", "sk-proj-SYNTHETIC0123456789abcdefghijklmnopqrstuvwxyz"),
    ("github_fine_grained_token", "github_pat_SYNTHETIC0123456789abcdefghijklmnopqrstuvwxyz"),
    (
        "sha256_crypt_hash",
        "$5$syntheticsalt$abcdefghijklmnopqrstuvxyz0123456789ABCDE",
    ),
    ("django_md5_hash", "md5$synthetic$0123456789abcdef0123456789abcdef"),
    (
        "exclusion_constraint_diagnostic",
        "duplicate key violates exclusion constraint review_case_active_excl",
    ),
    (
        "not_null_constraint_diagnostic",
        "null value violates not-null constraint review_case_state_nn",
    ),
    ("zero_width_sql", "SEL\u200bECT email FR\u200bOM users"),
    ("fullwidth_sql", "\uff33\uff25\uff2c\uff25\uff23\uff34 email \uff26\uff32\uff2f\uff2d users"),
    # Representative adjacent families required by the canonical policy.
    ("double_encoded_traversal", "%252e%252e%252finternal%252fsecret.pdf"),
    ("fraction_slash_traversal", "..\u2044private\u2044evidence.pdf"),
    ("set_minus_windows_separator", "C\uff1aprivate\u2216evidence.pdf"),
    ("pem_tabs", "-----BEGIN\t\tPRIVATE\tKEY-----"),
    ("pem_split_with_word_joiner", "-----BEGIN\u2060 PRIVATE KEY-----"),
    ("anthropic_token", "sk-ant-api03-SYNTHETIC0123456789abcdefghijklmnopqrstuvwxyz"),
    ("slack_token", "xoxb-SYNTHETIC0123456789-abcdefghijklmnop"),
    ("sql_revoke", "REVOKE SELECT ON review_items FROM app_user"),
    ("sql_update_word_joiner", "UP\u2060DATE review_items SET version = 2"),
    ("foreign_key_constraint", "violates foreign key constraint review_item_case_fkey"),
)

_CANONICAL_SAFE_PUBLIC_TEXT = (
    # Exact independent Quality corpus.
    "We select participants from the eligible population.",
    "Create a table summarizing the public results.",
    "Internal-medicine outcomes in Ecuador",
    "Private-sector research collaboration",
    "Database-driven bibliometric analysis",
    "ORCID:0000-0002-1825-0097",
    "ISBN:978-9942-00-123-4",
    "Project:PRJ-2026-01",
    "Methods/Results",
    "Academic year 2025/2026",
    "yes/no",
    "doi:10.1000/xyz123",
    # Exact independent Spec corpus.
    "DOI:10.1000/xyz123",
    "We select participants from two public cohorts.",
    "The ratio was 1/2 on 2026/07/21.",
    "The private-sector sample was independently validated.",
    "Internal-validity evidence was strong.",
    "The token economy is discussed in the literature.",
    "The token economy result: public evidence.",
    "Section:Methods and results",
    "Smith/Jones (2025), pp. 12-14.",
    # Representative scientific prose adjacent to prohibited syntax.
    "We grant participants access to the public report.",
    "The constraint analysis uses a public benchmark.",
    "A database driven method and token economy model were compared.",
    "Vacuum energy is discussed in the scientific literature.",
    "We explain selected public outcomes in the report.",
)

_CANONICAL_SAFE_DOCUMENT_NAMES = (
    "FCI-021_2025.pdf",
    "Internal-medicine outcomes 2026.pdf",
    "Private-sector collaboration appendix.pdf",
    "Project-PRJ-2026-01 results.pdf",
    "Smith-Jones 2025 citation.pdf",
)

_FINAL_CLOSE_PROHIBITED_PUBLIC_TEXT = (
    "Loaded evidence from /srv/newproject/private/review.pdf",
    "token=SYNTHETIC0123456789abcdef",
    "refresh_token=SYNTHETIC0123456789abcdef",
    "client_secret=SYNTHETIC0123456789abcdef",
    "VACUUM FULL review_items",
    "EXPLAIN SELECT email FROM users",
    "unsalted_md5$0123456789abcdef0123456789abcdef",
    # Final independent Spec path corpus plus punctuation/quoting adjacencies.
    "trace:/opt/newproject/restricted/evidence/report.pdf",
    "origin|/var/lib/newproject/private/ledger.json",
    "internal-source?/srv/apps/newproject/secrets/key.pem",
    "Evidence: '/srv/newproject/private/review.pdf'",
    'Evidence: "/srv/newproject/private/review.pdf"',
    # Assigned-secret families: classification is by normalized semantic key.
    "session_token=SYNTHETIC9876543210abcdef",
    "oauth_token: SYNTHETIC9876543210abcdef",
    "id_token = SYNTHETIC9876543210abcdef",
    "api-key: SYNTHETIC9876543210abcdef",
    "authorization_token=SYNTHETIC9876543210abcdef",
    "sessionId: SYNTHETIC9876543210abcdef",
    # SQL commands/fragments must be found at any position, not only offset zero.
    "Database said: SELECT password FROM users",
    "Audit: INSERT INTO review_items(id) VALUES (1)",
    "Audit: UPDATE review_items SET version = 2",
    "Audit: DELETE FROM review_items",
    "Audit: CREATE TABLE leaked_items (id integer)",
    "Audit: DROP TABLE review_items",
    "Audit: ALTER TABLE review_items ADD COLUMN leaked text",
    "Audit: TRUNCATE TABLE review_items",
    "Audit: GRANT SELECT ON review_items TO app_user",
    "Audit: REVOKE SELECT ON review_items FROM app_user",
    "Audit: VACUUM FULL review_items",
    "Audit: EXPLAIN SELECT email FROM users",
    "Database said select password from users",
    "Audit said SeLeCt password FrOm users",
    "ANALYZE VERBOSE review_items",
    "Audit said analyze users",
    "REINDEX TABLE review_items",
    "REINDEX review_items",
    "LOCK TABLE review_items IN ACCESS EXCLUSIVE MODE",
    "CALL rebuild_review_cache()",
    "ANALYZE users",
    "VACUUM review_items",
    # Common producer hash representations.
    "unsalted_sha1$0123456789abcdef0123456789abcdef01234567",
    "mysql_native_password=*94BDCEBE19083CE2A1F959FD02F964C7AF4CFC29",
    "SCRAM-SHA-256$4096:c2FsdA==$c3RvcmVkOnNlcnZlcg==",
    "postgres credential md5d41d8cd98f00b204e9800998ecf8427e",
    "sha256$synthetic$0123456789abcdef0123456789abcdef",
    # URL contents are inspected before the URL is masked.
    "https://example.com/evidence?token=SYNTHETIC0123456789abcdef",
    "Open: https://example.com/cb?refresh_token=SYNTHETIC0123456789abcdef",
    "https://example.com/cb?client_secret=SYNTHETIC0123456789abcdef",
    "https://example.com/cb?next=token%3DSYNTHETIC0123456789abcdef",
    "https://example.com/cb#session_token=SYNTHETIC0123456789abcdef",
    "https://api.example.com/path?access_key=SYNTHETIC0123456789abcdef",
    "https://example.com/#client%5Fsecret=SYNTHETIC0123456789abcdef",
    "https://synthetic-user:synthetic-pass@example.com/public",
    # Numeric aliases and semantic internal/infrastructure host labels.
    "http://127.1:9000/private/object.pdf",
    "https://minio-private.example.org/bucket/object",
    "https://storage-internal.example.org/object",
    "https://minio01.corp.example.com/private/review.pdf",
    "https://10.0.0.1.sslip.io/private/review.pdf",
    "https://10-0-0-1.nip.io/private/review.pdf",
    "http://127.1.sslip.io/private/review.pdf",
    "http://2130706433/private/review.pdf",
    "https://db.corp/private/review.pdf",
    "https://internal.storage.googleapis.com/private/review.pdf",
    "https://private.s3.amazonaws.com/private/review.pdf",
    "https://minio01.storage.googleapis.com/private/review.pdf",
    # Architectural-final independent Spec SQL grammar corpus.
    "Database said: SELECT password FROM users AS u",
    "Audit: VACUUM (FULL, ANALYZE) review_items",
    "Audit: ANALYZE review_items (status)",
    # Architectural-final independent Spec URL-component and host corpus.
    "https://example.com/download/access_token=SYNTHETIC0123456789abcdef",
    "https://example.com/object?X-Amz-Signature=SYNTHETIC0123456789abcdef",
    "https://internal01.example.org/private/review.pdf",
    "https://private2.example.org/private/review.pdf",
    "https://corp01.example.org/private/review.pdf",
    "https://review.default.svc/private/review.pdf",
    "https://review.service.consul/private/review.pdf",
    # Architectural-final independent Quality URL-component corpus.
    "https://example.com/access_token=SYNTHETIC0123456789abcdef",
    "https://example.com/public?next=/srv/newproject/private/review.pdf",
    "https://example.com/public?next=C%3A%5Cprivate%5Creview.pdf",
    "https://example.com/public?statement=VACUUM%20FULL%20review_items",
    "https://example.com/public#SELECT%20email%20FROM%20users",
    "https://example.com/public#token=x",
    "https://example.com/public?x-amz-signature=SYNTHETIC0123456789abcdef",
    "https://example.com/public?sig=SYNTHETIC0123456789abcdef",
    "https://127-1.sslip.io/private",
    "https://internal01.example.com/private",
    "https://private2.example.com/private",
    "https://miniointernal.example.com/object",
    "https://service.test/object",
    "https://service.invalid/object",
    "https://999.999.999.999/object",
    # Adjacent host-policy controls required by the final correction brief.
    "https://s3-internal.example.com/object",
    "https://redis01.internal.example.com/object",
    "https://db01.corp.example.com/object",
    # Fixed-point canonicalization, compact assignments, SQL and hash families.
    "..%2525252fprivate%2525252fsecret.pdf",
    "\uff0e\uff0e\uff05\uff12\uff46private\uff05\uff12\uff46secret.pdf",
    "token=x",
    "authtoken=SYNTHETIC0123456789abcdef",
    "x-amz-signature=SYNTHETIC0123456789abcdef",
    "authToken=x",
    "Signature=x",
    "sig=x",
    "Audit: SELECT current_user",
    "Audit: SELECT email FROM users AS u",
    "Audit: SELECT email FROM users -- leaked",
    "Audit: VACUUM FULL review_items (email)",
    "Audit: EXPLAIN (ANALYZE, BUFFERS) SELECT 1",
    "Audit: LOCK review_items IN ACCESS EXCLUSIVE MODE",
    "Audit: CREATE OR REPLACE FUNCTION leaked() RETURNS void",
    "Audit: SET search_path TO private",
    "Audit: DO $$ BEGIN RAISE NOTICE leaked; END $$",
    "sha1$$0123456789abcdef0123456789abcdef01234567",
    "md5$$0123456789abcdef0123456789abcdef",
    "{SSHA256}U1lOVEhFVElDX1NBTFRFRF9IQVNIX1ZBTFVF",
    "{PBKDF2-SHA256}U1lOVEhFVElDX1BBU1NXT1JEX0hBU0g=",
    # General contract-boundary mutations: URL path credentials, signing
    # providers, nested URIs and non-canonical numeric hosts.
    "https://example.com/access_token/SYNTHETIC0123456789abcdef",
    "https://example.com/x-goog-signature/SYNTHETIC0123456789abcdef",
    "https://example.com/public?X-Goog-Signature=SYNTHETIC0123456789abcdef",
    "https://example.com/public?X-Amz-Credential=SYNTHETIC0123456789abcdef",
    "https://example.com/public?authToken=x",
    "https://example.com/public?apiKey=x",
    (
        "https://example.com/public?"
        "next=https%3A%2F%2Finternal01.example.com%2Fprivate"
    ),
    "http://0x7f000001/private",
    "http://0177.0.0.1/private",
    "http://[fe80::1%25eth0]/private",
    # General command-position SQL grammar families.
    "Audit: WITH leaked AS (SELECT email FROM users) SELECT * FROM leaked",
    "Audit: ALTER TABLE review_items ADD COLUMN leaked text",
    "Audit: DROP TABLE review_items",
    "Audit: TRUNCATE review_items",
    (
        "Audit: MERGE INTO review_items AS target USING staged_items AS source "
        "ON target.id = source.id WHEN MATCHED THEN UPDATE SET version = 2"
    ),
    "Audit: COPY review_items TO '/tmp/review_items.csv'",
    "Audit: PREPARE leaked AS SELECT email FROM users",
    "Audit: EXECUTE leaked",
    "Audit: BEGIN",
    "Audit: COMMIT",
    "Audit: ROLLBACK",
    "Audit: CREATE VIEW leaked_view AS SELECT email FROM users",
    "Audit: CREATE PROCEDURE leaked() LANGUAGE SQL AS $$ SELECT 1 $$",
    (
        "Audit: CREATE TRIGGER leaked BEFORE INSERT ON review_items "
        "EXECUTE FUNCTION leaked()"
    ),
    # Root pre-overlay boundary probes.
    "apikey=x",
    "clientsecret=x",
    "accesskey=x",
    "authorizationcode=x",
    "Output SELECT password FROM users",
    "Result SeLeCt current_user",
    "https://example.com/public?x=%00SELECT",
    "https://service.localdomain/object",
    "Evidence foo/private ledger",
    # Zero-finding Spec preflight exact corpus and bounded family adjacencies.
    "authkey=x",
    "authenticationcode=x",
    "jwtkey=x",
    "jwtcode=x",
    "signingcode=x",
    "https://service.in-addr.arpa/object",
    "https://service.ip6.arpa/object",
    "Audit: SHOW search_path",
    "VALUES (1, 2)",
    "Audit: RESET search_path",
    "Audit: DISCARD ALL",
    "Audit: CHECKPOINT",
    "Audit: LISTEN review_events",
    "Audit: NOTIFY review_events, 'payload'",
    "Audit: UNLISTEN review_events",
    "Audit: COMMENT ON TABLE review_items IS 'public'",
    "NTLM:0123456789abcdef0123456789abcdef",
    "LM=0123456789abcdef0123456789abcdef",
    (
        "SHA256:"
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    ),
    "SSHA256=U1lOVEhFVElDX1NBTFRFRF9IQVNIX1ZBTFVF",
    # Zero-finding Quality preflight exact corpus.
    "sessionid=x",
    "oauthcode=x",
    "pwd=x",
    "dbpassword=x",
    "jwt=x",
    "to\uFE0Fken=x",
    "to\u034Fken=x",
    "SE\uFE0FLECT email FROM users",
    "https://example.org/public#pwd=x",
    "https://example.org/public?sessionid=x",
    "Audit: COMMENT ON TABLE review_items IS private",
    "Audit: REFRESH MATERIALIZED VIEW private_view",
    "Audit: SAVEPOINT private_state",
    "NTLM:8846f7eaee8fb117ad06bdd830b7586c",
    "ntlm$8846f7eaee8fb117ad06bdd830b7586c",
    "C:private",
    "Object key: research-bucket/raw-object",
    # Final independent Quality/Spec exact findings.
    "authNKey=x",
    "https://example.org/public?authNKey=x",
    "authnkey=x",
    "https://example.org/public?authnkey=x",
    "bcrypt:$2b$12$abcdefghijklmnopqrstuu0123456789ABCDEFGHIJKLMNOPQ",
    "Storage locator: collection/archive",
    "Blob location: collection\\archive",
    "Artifact path=collection/archive",
    "authzcode=x",
    "authntoken=x",
    # Explicit PostgreSQL command-position dialect coverage.
    "Audit: ABORT",
    "Audit: ALTER TABLE review_items ADD COLUMN public_note text",
    "Audit: ANALYZE review_items",
    "Audit: BEGIN",
    "Audit: CALL rebuild_review_cache()",
    "Audit: CHECKPOINT",
    "Audit: CLOSE c",
    "Audit: CLUSTER review_items",
    "Audit: COMMENT ON TABLE review_items IS 'private'",
    "Audit: COMMIT",
    "Audit: COPY review_items TO STDOUT",
    "Audit: CREATE TABLE leaked_items (id integer)",
    "Audit: DEALLOCATE c",
    "Audit: DECLARE c CURSOR FOR SELECT 1",
    "Audit: DELETE FROM review_items",
    "Audit: DISCARD ALL",
    "Audit: DO $$ BEGIN NULL; END $$",
    "Audit: DROP TABLE review_items",
    "Audit: END",
    "Audit: EXECUTE c",
    "Audit: EXPLAIN SELECT 1",
    "Audit: FETCH ALL FROM c",
    "Audit: GRANT SELECT ON review_items TO app_user",
    (
        "Audit: IMPORT FOREIGN SCHEMA remote_schema FROM SERVER remote_server "
        "INTO public"
    ),
    "Audit: INSERT INTO review_items(id) VALUES (1)",
    "Audit: LISTEN review_events",
    "Audit: LOAD 'private_library'",
    "Audit: LOCK TABLE review_items IN ACCESS EXCLUSIVE MODE",
    (
        "Audit: MERGE INTO review_items AS target USING staged_items AS source "
        "ON target.id = source.id WHEN MATCHED THEN UPDATE SET version = 2"
    ),
    "Audit: MOVE FORWARD ALL FROM c",
    "Audit: NOTIFY review_events, 'payload'",
    "Audit: PREPARE leaked AS SELECT email FROM users",
    "Audit: REASSIGN OWNED BY old_user TO new_user",
    "Audit: REFRESH MATERIALIZED VIEW private_view",
    "Audit: REINDEX TABLE review_items",
    "Audit: RELEASE SAVEPOINT private_state",
    "Audit: RESET search_path",
    "Audit: REVOKE SELECT ON review_items FROM app_user",
    "Audit: ROLLBACK",
    "Audit: SAVEPOINT private_state",
    (
        "Audit: SECURITY LABEL FOR provider ON TABLE review_items "
        "IS 'private'"
    ),
    "Audit: SELECT current_user",
    "Audit: SET search_path TO private",
    "Audit: SHOW search_path",
    "Audit: START TRANSACTION",
    "Audit: TRUNCATE review_items",
    "Audit: UNLISTEN review_events",
    "Audit: UPDATE review_items SET version = 2",
    "Audit: VACUUM review_items",
    "Audit: VALUES (1, 2)",
)

_FINAL_CLOSE_PUBLIC_URLS = (
    "https://doi.org/10.1000/xyz123",
    "https://orcid.org/0000-0002-1825-0097",
    "Open data: https://zenodo.org/records/12345",
    "https://storage.googleapis.com/gcp-public-data-landsat/index.csv",
    "https://s3.amazonaws.com/doc/2006-03-01/",
    "https://pubmed.ncbi.nlm.nih.gov/12345678/",
    "https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/ABC123",
    "https://repositorio.uta.edu.ec/items/public-record",
    # Architectural-final independent Spec public-reference controls.
    "https://s3.us-east-1.amazonaws.com/aws-publicdatasets/index.html",
    "https://minio.org/docs/",
    "https://redis.io/docs/latest/",
    "https://db.nomics.world/",
    "https://minio.org",
    "https://redis.io",
    "https://db.nomics.world",
)

_ARCHITECTURAL_FINAL_SAFE_NARRATIVE_TEXT = (
    # Exact independent Quality SQL false-positive corpus.
    "We analyze evidence.",
    "Researchers select participants from cohorts.",
    "Please insert into the report a public citation.",
    "We delete from the draft any duplicate citation.",
    "We update results set after peer review.",
    "We grant access on request to participants.",
    "Create table 2 from public results.",
    # Exact independent Quality slash-idiom corpus.
    "Use qualitative and/or quantitative evidence.",
    "The pre/post intervention comparison was significant.",
    "The input/output ratio was 1.2.",
    # Adjacent prose controls for the general SQL command-position families.
    "With public evidence, we select participants from cohorts.",
    "We prepare the public report before review.",
    "The team executes the approved scientific protocol.",
    "We begin analysis after preregistration.",
    "We commit to transparent methods.",
    "We roll back the manuscript edits after peer review.",
    "The copy editors reviewed the public report.",
    "Merging public datasets requires documented provenance.",
    "The case/control comparison was public.",
    "The results show strong evidence.",
    "The risk/benefit assessment was favorable.",
    # Zero-finding Quality false-positive controls.
    "Show the public evidence in Table 2.",
    "Values in the public cohort were reproducible.",
    "We comment on the published table.",
    "Refresh the literature review before submission.",
    "The OAuth code flow is discussed in the public protocol.",
    "NTLM was compared as a legacy authentication protocol.",
    "Object-key vocabularies are discussed in public data management.",
    "Group C: private-sector outcomes were independently validated.",
    # Final independent review false-positive controls.
    "The AuthN key protocol is discussed in the public study.",
    "Fetch results from the public archive before analysis.",
    "Close reading improved the manuscript.",
    "Cluster analysis identified public patterns.",
    "Security labels were compared in the published protocol.",
    "AuthN key rotation is discussed as a public policy.",
    "Bcrypt was compared as a legacy authentication protocol.",
    "The collection/archive distinction is conceptual.",
    "Declare the study design before analysis.",
)


def _canonical_evidence_data() -> dict[str, object]:
    return {
        "document_name": "FCI-021_2025.pdf",
        "page": 12,
        "section": "Methods and results",
        "locator": "page 12, table 3",
        "fragment": "Public scientific evidence excerpt.",
        "stream_path": "evidence/FCI-021_2025.pdf",
        "correlation_id": UUID_A,
    }


def _canonical_audit_item(*, summary: object, metric: object) -> dict[str, object]:
    return {
        "id": str(UUID_C),
        "event_type": "scientific_decision_applied",
        "actor_id": 1,
        "review_item_id": str(UUID_A),
        "decision_id": str(UUID_B),
        "created_at": "2026-07-21T15:00:00+00:00",
        "correlation_id": str(UUID_C),
        "summary": summary,
        "payload": {
            "kind": "scientific_decision_applied",
            "schema_version": 1,
            "decision_id": str(UUID_B),
            "decision_type": "validated",
            "previous_case_status": "pending",
            "resulting_case_status": "resolved",
            "review_item_id": str(UUID_A),
            "kpi_effect": ({"metric": metric, "before": 1, "after": 2, "delta": 1},),
        },
    }


def _canonical_audit_data(*, summary: object, metric: object) -> dict[str, object]:
    return {
        "items": (_canonical_audit_item(summary=summary, metric=metric),),
        "page": 1,
        "page_size": 25,
        "total": 1,
        "correlation_id": UUID_A,
    }


def _canonical_case_data() -> dict[str, object]:
    return _queue_item() | {
        "target_table": "person_roles",
        "target_pk": 1,
        "field_path": "canonical_name",
        "detected_value": "Detected public value",
        "normalized_value": "Normalized public value",
        "canonical_value": "Canonical public value",
        "current_decision_id": None,
        "overrides": ({
            "decision_id": str(UUID_B),
            "field": "canonical_name",
            "field_path": "canonical_name",
            "scope": "record",
            "scope_id": str(UUID_A),
            "value": "Public corrected value",
            "created_at": "2026-07-21T15:00:00+00:00",
        },),
        "effective_memberships": (),
        "evidence_summary": {
            "available": True,
            "count": 1,
            "document_name": "FCI-021_2025.pdf",
            "page": 12,
            "section": "Methods and results",
            "locator": "page 12, table 3",
            "fragment": "Public scientific evidence excerpt.",
            "stream_path": "evidence/FCI-021_2025.pdf",
        },
    }


class HumanReviewB2B2ContractTests(unittest.TestCase):
    def test_all_required_contracts_and_helpers_exist(self) -> None:
        api = _api()
        required = (
            "ReviewQueueQuery", "ReviewQueueItem", "ReviewQueueResponse",
            "ReviewCaseDetail", "EffectiveCapabilitiesResponse",
            "PersonDecisionPayload", "ProductDecisionPayload",
            "RelationDecisionPayload", "ExternalDecisionPayload",
            "CounterpartReference", "CounterpartOption",
            "DuplicateDecisionPayload", "DecisionPayloadV1",
            "ApplyDecisionRequest", "ApplyDecisionResponse", "DiscardRequest",
            "RevertRequest", "KpiEffectItem", "KpiEffect",
            "EvidenceResponse", "AuditTimelineResponse",
            "HumanReviewErrorResponse", "HumanReviewApiError",
            "HumanReviewValidationError", "IncompatibleDecisionError",
            "derive_decision_type", "validate_apply_decision",
            "sanitize_error_details",
        )
        for name in required:
            with self.subTest(name=name):
                self.assertTrue(hasattr(api, name), f"missing Task 2 surface {name}")

        expected_payload_fields = {
            api.PersonDecisionPayload: (
                "case_type", "canonical_identity_key", "canonical_name",
                "aliases", "scientific_status", "resolution",
            ),
            api.ProductDecisionPayload: (
                "case_type", "product_title", "scientific_status",
            ),
            api.RelationDecisionPayload: (
                "case_type", "project_director_identity_key",
                "relationship_status", "scientific_status",
            ),
            api.ExternalDecisionPayload: (
                "case_type", "external_identity_key", "external_institution",
                "scientific_status", "resolution",
            ),
            api.DuplicateDecisionPayload: (
                "case_type", "counterpart_ref", "resolution",
                "scientific_status",
            ),
        }
        for model, fields in expected_payload_fields.items():
            with self.subTest(model=model.__name__):
                self.assertEqual(tuple(model.model_fields), fields)

    def test_closed_models_reject_extra_fields_and_client_decision_type(self) -> None:
        api = _api()
        closed_models = (
            api.ReviewQueueQuery, api.ReviewQueueItem, api.ReviewQueueResponse,
            api.ReviewCaseDetail, api.EffectiveCapabilitiesResponse,
            api.PersonDecisionPayload, api.ProductDecisionPayload,
            api.RelationDecisionPayload, api.ExternalDecisionPayload,
            api.DuplicateDecisionPayload, api.ApplyDecisionRequest,
            api.ApplyDecisionResponse, api.DiscardRequest, api.RevertRequest,
            api.KpiEffectItem, api.KpiEffect, api.EvidenceResponse,
            api.AuditTimelineResponse, api.HumanReviewErrorResponse,
        )
        for model in closed_models:
            with self.subTest(model=model.__name__):
                self.assertEqual(model.model_config.get("extra"), "forbid")
        with self.assertRaises(ValidationError):
            api.PersonDecisionPayload.model_validate(_person() | {"unexpected": True})
        with self.assertRaises(ValidationError):
            api.ApplyDecisionRequest.model_validate(
                _request(_person()) | {"decision_type": "validated"}
            )
        self.assertNotIn("decision_type", api.ApplyDecisionRequest.model_fields)

    def test_structural_bounds_uuid_nullability_and_required_reason(self) -> None:
        api = _api()
        for version in (0, -1, True):
            with self.subTest(version=version), self.assertRaises(ValidationError):
                api.ApplyDecisionRequest.model_validate(
                    _request(_person()) | {"expected_version": version}
                )
        for field in ("correlation_id", "expected_current_decision_id"):
            invalid = _request(_person()) | {field: "not-a-uuid"}
            with self.subTest(field=field), self.assertRaises(ValidationError):
                api.ApplyDecisionRequest.model_validate(invalid)
        apply_request = api.ApplyDecisionRequest.model_validate(_request(_person()))
        self.assertIsNone(apply_request.expected_current_decision_id)
        discard = api.DiscardRequest(
            expected_version=1,
            expected_current_decision_id=None,
            reason="duplicate evidence",
            correlation_id=UUID_A,
        )
        self.assertIsNone(discard.expected_current_decision_id)
        for model, value in (
            (api.DiscardRequest, {
                "expected_version": 1,
                "expected_current_decision_id": None,
                "reason": "   ",
                "correlation_id": UUID_A,
            }),
            (api.RevertRequest, {
                "expected_version": 1,
                "expected_current_decision_id": None,
                "decision_id_to_revert": UUID_B,
                "reason": "required",
                "correlation_id": UUID_A,
            }),
            (api.RevertRequest, {
                "expected_version": 1,
                "expected_current_decision_id": UUID_B,
                "decision_id_to_revert": UUID_C,
                "reason": "x" * 4001,
                "correlation_id": UUID_A,
            }),
        ):
            with self.subTest(model=model.__name__), self.assertRaises(ValidationError):
                model.model_validate(value)

    def test_payload_union_is_discriminated_and_rejects_empty_or_wrong_shapes(self) -> None:
        api = _api()
        adapter = TypeAdapter(api.DecisionPayloadV1)
        valid = (
            (_person(), api.PersonDecisionPayload),
            ({"case_type": "product", "product_title": "Article", "scientific_status": "validated"}, api.ProductDecisionPayload),
            ({"case_type": "project_director_relation", "project_director_identity_key": "human:one", "relationship_status": "linked", "scientific_status": "validated"}, api.RelationDecisionPayload),
            ({"case_type": "external_identity", "external_identity_key": "external:one", "external_institution": None, "scientific_status": "validated"}, api.ExternalDecisionPayload),
            (_duplicate(), api.DuplicateDecisionPayload),
        )
        for value, expected in valid:
            with self.subTest(case_type=value["case_type"]):
                self.assertIsInstance(adapter.validate_python(value), expected)
        invalid = (
            _person() | {"canonical_name": "   "},
            _person() | {"case_type": "product"},
            _person(resolution="merged"),
            _duplicate() | {"counterpart_ref": {"target_type": "person_roles", "target_id": 0}},
            {"case_type": "invalid_text", "scientific_status": "pending"},
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                adapter.validate_python(value)

    def test_complete_case_action_matrix_and_server_side_derivation(self) -> None:
        api = _api()
        actions = ("approve", "correct", "link")
        expected_actions = {
            ReviewCaseType.PERSON_IDENTITY: frozenset(actions),
            ReviewCaseType.AUTHOR_IDENTITY: frozenset(actions),
            ReviewCaseType.PRODUCT: frozenset({"approve", "correct"}),
            ReviewCaseType.PROJECT_DIRECTOR_RELATION: frozenset(actions),
            ReviewCaseType.EXTERNAL_IDENTITY: frozenset(actions),
            ReviewCaseType.POSSIBLE_DUPLICATE: frozenset({"link"}),
            ReviewCaseType.INVALID_TEXT: frozenset(),
            ReviewCaseType.NEW_EVIDENCE_CONFLICT: frozenset(),
        }
        payload_values = {
            ReviewCaseType.PERSON_IDENTITY: _person("person_identity", "linked"),
            ReviewCaseType.AUTHOR_IDENTITY: _person("author_identity", "linked"),
            ReviewCaseType.PRODUCT: {"case_type": "product", "product_title": "Article", "scientific_status": "validated"},
            ReviewCaseType.PROJECT_DIRECTOR_RELATION: {"case_type": "project_director_relation", "project_director_identity_key": "human:one", "relationship_status": "linked", "scientific_status": "validated"},
            ReviewCaseType.EXTERNAL_IDENTITY: {"case_type": "external_identity", "external_identity_key": "external:one", "scientific_status": "validated", "resolution": "linked"},
            ReviewCaseType.POSSIBLE_DUPLICATE: _duplicate(),
        }
        adapter = TypeAdapter(api.DecisionPayloadV1)
        fallback = adapter.validate_python(payload_values[ReviewCaseType.PRODUCT])
        for case_type in ReviewCaseType:
            for action in actions:
                raw_payload = dict(payload_values[case_type]) if case_type in payload_values else None
                if (
                    raw_payload is not None
                    and action != "link"
                    and case_type in {ReviewCaseType.PERSON_IDENTITY, ReviewCaseType.AUTHOR_IDENTITY, ReviewCaseType.EXTERNAL_IDENTITY}
                    and "resolution" in raw_payload
                ):
                    raw_payload.pop("resolution")
                payload = adapter.validate_python(raw_payload) if raw_payload is not None else fallback
                with self.subTest(case_type=case_type, action=action):
                    if action not in expected_actions[case_type]:
                        with self.assertRaises(api.IncompatibleDecisionError):
                            api.derive_decision_type(case_type, action, payload)
                        continue
                    if action == "approve":
                        expected = ReviewDecisionType.VALIDATED
                    elif action == "correct":
                        expected = ReviewDecisionType.CORRECTED
                    else:
                        expected = ReviewDecisionType.MERGED if case_type is ReviewCaseType.POSSIBLE_DUPLICATE else ReviewDecisionType.LINKED
                    self.assertEqual(api.derive_decision_type(case_type, action, payload), expected)

        for resolution in ("linked", "maintained_separate", "separated"):
            for case_type in (
                ReviewCaseType.PERSON_IDENTITY,
                ReviewCaseType.AUTHOR_IDENTITY,
                ReviewCaseType.PROJECT_DIRECTOR_RELATION,
                ReviewCaseType.EXTERNAL_IDENTITY,
            ):
                value = dict(payload_values[case_type])
                value["relationship_status" if case_type is ReviewCaseType.PROJECT_DIRECTOR_RELATION else "resolution"] = resolution
                with self.subTest(case_type=case_type, resolution=resolution):
                    self.assertEqual(
                        api.derive_decision_type(case_type, "link", adapter.validate_python(value)),
                        ReviewDecisionType(resolution),
                    )
        for resolution in ("merged", "maintained_separate", "separated"):
            value = dict(payload_values[ReviewCaseType.POSSIBLE_DUPLICATE]) | {"resolution": resolution}
            with self.subTest(case_type="possible_duplicate", resolution=resolution):
                self.assertEqual(
                    api.derive_decision_type(ReviewCaseType.POSSIBLE_DUPLICATE, "link", adapter.validate_python(value)),
                    ReviewDecisionType(resolution),
                )

    def test_semantic_reason_rules_include_changed_normalized_value(self) -> None:
        api = _api()
        approve = api.ApplyDecisionRequest.model_validate(_request(_person()))
        self.assertEqual(
            api.validate_apply_decision(approve, normalized_value="same", final_value="same"),
            ReviewDecisionType.VALIDATED,
        )
        required = (
            api.ApplyDecisionRequest.model_validate(_request(_person(), "correct")),
            api.ApplyDecisionRequest.model_validate(_request(_person(resolution="maintained_separate"), "link")),
            api.ApplyDecisionRequest.model_validate(_request(_person(resolution="separated"), "link")),
            api.ApplyDecisionRequest.model_validate(_request(_duplicate(), "link")),
        )
        for request in required:
            with self.subTest(action=request.action), self.assertRaises(api.HumanReviewValidationError):
                api.validate_apply_decision(request)
        with self.assertRaises(api.HumanReviewValidationError):
            api.validate_apply_decision(approve, normalized_value="old", final_value="new")
        corrected = api.ApplyDecisionRequest.model_validate(_request(_person(), "correct", "human correction"))
        self.assertEqual(api.validate_apply_decision(corrected), ReviewDecisionType.CORRECTED)

    def test_current_closed_transition_matrix_allows_resolving_reopened_cases_without_writes(self) -> None:
        state = importlib.import_module(STATE_MODULE)
        approved = {
            (ReviewCaseStatus.PENDING, ReviewCaseStatus.IN_REVIEW),
            (ReviewCaseStatus.PENDING, ReviewCaseStatus.RESOLVED),
            (ReviewCaseStatus.PENDING, ReviewCaseStatus.SUPERSEDED),
            (ReviewCaseStatus.IN_REVIEW, ReviewCaseStatus.AWAITING_GESTOR_APPROVAL),
            (ReviewCaseStatus.IN_REVIEW, ReviewCaseStatus.RESOLVED),
            (ReviewCaseStatus.IN_REVIEW, ReviewCaseStatus.CONFLICTED),
            (ReviewCaseStatus.AWAITING_GESTOR_APPROVAL, ReviewCaseStatus.IN_REVIEW),
            (ReviewCaseStatus.AWAITING_GESTOR_APPROVAL, ReviewCaseStatus.RESOLVED),
            (ReviewCaseStatus.RESOLVED, ReviewCaseStatus.REOPENED),
            (ReviewCaseStatus.RESOLVED, ReviewCaseStatus.CONFLICTED),
            (ReviewCaseStatus.RESOLVED, ReviewCaseStatus.SUPERSEDED),
            (ReviewCaseStatus.REOPENED, ReviewCaseStatus.IN_REVIEW),
            (ReviewCaseStatus.REOPENED, ReviewCaseStatus.RESOLVED),
            (ReviewCaseStatus.CONFLICTED, ReviewCaseStatus.IN_REVIEW),
            (ReviewCaseStatus.CONFLICTED, ReviewCaseStatus.RESOLVED),
        }
        for current in ReviewCaseStatus:
            for target in ReviewCaseStatus:
                try:
                    state.assert_case_transition(current, target)
                    allowed = True
                except ValueError:
                    allowed = False
                with self.subTest(current=current, target=target):
                    self.assertEqual(allowed, (current, target) in approved)
        self.assertEqual(len(ReviewCaseStatus), 7)
        source = inspect.getsource(state.assert_case_transition).lower()
        for forbidden in ("commit", "execute", "flush", "add(", "update(", "insert("):
            self.assertNotIn(forbidden, source)

    def test_incompatible_state_uses_the_safe_domain_error(self) -> None:
        api = _api()
        state = importlib.import_module(STATE_MODULE)
        try:
            state.assert_case_transition(ReviewCaseStatus.PENDING, ReviewCaseStatus.REOPENED)
        except ValueError as error:
            self.assertIsInstance(error, api.IncompatibleDecisionError)
            self.assertEqual(error.code, "INCOMPATIBLE_DECISION")
            self.assertFalse(hasattr(error, "status_code"))
            self.assertFalse(hasattr(error, "response"))
            self.assertIsNone(getattr(error, "correlation_id", None))
        else:
            self.fail("incompatible transition was accepted")

    def test_safe_error_envelope_sanitizes_internal_details(self) -> None:
        api = _api()
        response = api.HumanReviewErrorResponse(
            code="INCOMPATIBLE_DECISION",
            message="Decision is incompatible",
            correlation_id=UUID_A,
            details={
                "field": "action",
                "sql": "SELECT password_hash FROM users",
                "nested": {"source_path": "C:\\private\\evidence.pdf", "safe": "value"},
                "token": "credential-value",
                "diagnostic": "SQLSTATE 23505 from review_items",
                "debug": "Traceback: internal failure",
            },
        )
        dumped = response.model_dump(mode="json")
        serialized = str(dumped).lower()
        self.assertEqual(dumped["correlation_id"], str(UUID_A))
        self.assertIn("field", dumped["details"])
        for forbidden in ("select password", "source_path", "c:\\private", "credential-value", "token", "sqlstate", "traceback"):
            self.assertNotIn(forbidden, serialized)

    def test_response_contracts_never_expose_internal_storage_fields(self) -> None:
        api = _api()
        forbidden = {"source_path", "bucket_key", "minio_url", "dropbox_url", "internal_url"}
        for model in (api.ReviewQueueItem, api.ReviewCaseDetail, api.EvidenceResponse, api.AuditTimelineResponse):
            with self.subTest(model=model.__name__):
                self.assertTrue(forbidden.isdisjoint(model.model_fields))
        query = api.ReviewQueueQuery()
        self.assertEqual((query.page, query.page_size, query.sort), (1, 25, "priority_oldest"))
        for value in ({"page": 0}, {"page_size": 0}, {"page_size": 101}, {"q": "x" * 321}):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                api.ReviewQueueQuery.model_validate(value)

    def test_queue_contract_uses_exact_authoritative_boundaries(self) -> None:
        api = _api()
        for value in (
            {"document_id": 1}, {"document_id": 900},
            {"source_revision": "x"}, {"source_revision": "x" * 120},
            {"q": "x" * 3}, {"q": "x" * 128},
        ):
            with self.subTest(valid=value):
                try:
                    api.ReviewQueueQuery.model_validate(value)
                except ValidationError as error:
                    self.fail(f"authoritative valid queue boundary was rejected: {error}")
        for value in (
            {"document_id": 0}, {"document_id": -1}, {"document_key": "private"},
            {"source_revision": ""}, {"source_revision": "x" * 121},
            {"q": "x" * 2}, {"q": "x" * 129},
        ):
            with self.subTest(invalid=value), self.assertRaises(ValidationError):
                api.ReviewQueueQuery.model_validate(value)
        try:
            api.ReviewQueueItem.model_validate(_queue_item() | {"document_id": 900})
        except ValidationError as error:
            self.fail(f"canonical 900-character queue item key was rejected: {error}")
        with self.assertRaises(ValidationError):
            api.ReviewQueueItem.model_validate(_queue_item() | {"document_id": 0})

    def test_kpi_values_are_strict_integers(self) -> None:
        api = _api()
        self.assertEqual(api.KpiEffectItem(metric="affected", before=1, after=2, delta=1).delta, 1)
        for field in ("before", "after", "delta"):
            for invalid in (True, 1.5, "1"):
                value = {"metric": "affected", "before": 1, "after": 2, "delta": 1} | {field: invalid}
                with self.subTest(field=field, invalid=invalid), self.assertRaises(ValidationError):
                    api.KpiEffectItem.model_validate(value)

    def test_every_command_is_strict_closed_and_checks_uuid_nullability_and_text(self) -> None:
        api = _api()
        commands = (
            (api.ApplyDecisionRequest, _request(_person()), ("correlation_id", "expected_current_decision_id")),
            (api.DiscardRequest, {"expected_version": 1, "expected_current_decision_id": None, "reason": "required", "correlation_id": UUID_A}, ("correlation_id", "expected_current_decision_id")),
            (api.RevertRequest, {"expected_version": 1, "expected_current_decision_id": UUID_B, "decision_id_to_revert": UUID_C, "reason": "required", "correlation_id": UUID_A}, ("correlation_id", "expected_current_decision_id", "decision_id_to_revert")),
        )
        for model, valid, uuid_fields in commands:
            model.model_validate(valid)
            for version in (0, -1, True, 1.0, "1"):
                with self.subTest(model=model.__name__, version=version), self.assertRaises(ValidationError):
                    model.model_validate(valid | {"expected_version": version})
            for field in uuid_fields:
                with self.subTest(model=model.__name__, uuid_field=field), self.assertRaises(ValidationError):
                    model.model_validate(valid | {field: "not-a-uuid"})
            with self.subTest(model=model.__name__, extra=True), self.assertRaises(ValidationError):
                model.model_validate(valid | {"unexpected": True})
        for model, valid, _uuid_fields in commands[1:]:
            for reason in ("", "   ", "x" * 4001):
                with self.subTest(model=model.__name__, reason_length=len(reason)), self.assertRaises(ValidationError):
                    model.model_validate(valid | {"reason": reason})
            model.model_validate(valid | {"reason": "x" * 4000})
        api.ApplyDecisionRequest.model_validate(_request(_person()) | {"reason": None})
        api.ApplyDecisionRequest.model_validate(_request(_person()) | {"expected_current_decision_id": None})
        api.DiscardRequest.model_validate(commands[1][1] | {"expected_current_decision_id": None})
        for model, valid, field in (
            (api.ApplyDecisionRequest, commands[0][1], "correlation_id"),
            (api.DiscardRequest, commands[1][1], "correlation_id"),
            (api.RevertRequest, commands[2][1], "correlation_id"),
            (api.RevertRequest, commands[2][1], "expected_current_decision_id"),
            (api.RevertRequest, commands[2][1], "decision_id_to_revert"),
        ):
            with self.subTest(model=model.__name__, null_field=field), self.assertRaises(ValidationError):
                model.model_validate(valid | {field: None})

    def test_semantic_errors_preserve_request_correlation_and_have_no_http_metadata(self) -> None:
        api = _api()
        requests = (
            (api.ApplyDecisionRequest.model_validate(_request(_person(), "correct")), {}),
            (api.ApplyDecisionRequest.model_validate(_request(_person())), {}),
            (api.ApplyDecisionRequest.model_validate(_request(_person())), {"case_type": ReviewCaseType.PRODUCT, "normalized_value": "x", "final_value": "x"}),
            (api.ApplyDecisionRequest.model_validate(_request(_person(), "link")), {}),
        )
        for request, kwargs in requests:
            with self.subTest(action=request.action, kwargs=kwargs):
                with self.assertRaises(ValueError) as raised:
                    api.validate_apply_decision(request, **kwargs)
                error = raised.exception
                self.assertEqual(getattr(error, "correlation_id", None), UUID_A)
                self.assertIn(getattr(error, "code", None), {"HUMAN_REVIEW_VALIDATION", "INCOMPATIBLE_DECISION"})
                self.assertFalse(hasattr(error, "status_code"))
                self.assertFalse(hasattr(error, "response"))

    def test_reasonless_approve_requires_explicit_unchanged_comparison(self) -> None:
        api = _api()
        request = api.ApplyDecisionRequest.model_validate(_request(_person()))
        for kwargs in ({}, {"normalized_value": "same"}, {"final_value": "same"}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(api.HumanReviewValidationError) as raised:
                    api.validate_apply_decision(request, **kwargs)
                self.assertEqual(getattr(raised.exception, "correlation_id", None), UUID_A)
        self.assertEqual(
            api.validate_apply_decision(request, normalized_value="same", final_value="same"),
            ReviewDecisionType.VALIDATED,
        )

    def test_reasonless_linked_and_correct_decisions_are_never_unproven(self) -> None:
        api = _api()
        linked = api.ApplyDecisionRequest.model_validate(
            _request(_person(resolution="linked"), "link")
        )
        corrected = api.ApplyDecisionRequest.model_validate(
            _request(_person(), "correct")
        )
        for request, kwargs in (
            (linked, {}),
            (linked, {"normalized_value": "same"}),
            (linked, {"final_value": "same"}),
            (linked, {"normalized_value": "same", "final_value": "same"}),
            (corrected, {"normalized_value": "same", "final_value": "same"}),
        ):
            with self.subTest(action=request.action, kwargs=kwargs):
                with self.assertRaises(api.HumanReviewValidationError) as raised:
                    api.validate_apply_decision(request, **kwargs)
                self.assertEqual(raised.exception.correlation_id, UUID_A)
        linked_with_reason = api.ApplyDecisionRequest.model_validate(
            _request(_person(resolution="linked"), "link", "identity linkage reviewed")
        )
        self.assertEqual(
            api.validate_apply_decision(linked_with_reason),
            ReviewDecisionType.LINKED,
        )

    def test_non_link_actions_reject_every_supplied_optional_resolution(self) -> None:
        api = _api()
        adapter = TypeAdapter(api.DecisionPayloadV1)
        cases = (
            (ReviewCaseType.PERSON_IDENTITY, "person_identity"),
            (ReviewCaseType.AUTHOR_IDENTITY, "author_identity"),
            (ReviewCaseType.EXTERNAL_IDENTITY, "external_identity"),
        )
        resolutions = ("linked", "maintained_separate", "separated")
        for case_type, payload_case_type in cases:
            for action in ("approve", "correct"):
                for resolution in resolutions:
                    if case_type is ReviewCaseType.EXTERNAL_IDENTITY:
                        raw = {
                            "case_type": payload_case_type,
                            "external_identity_key": "external:one",
                            "scientific_status": "validated",
                            "resolution": resolution,
                        }
                    else:
                        raw = _person(payload_case_type, resolution)
                    payload = adapter.validate_python(raw)
                    self.assertEqual(payload.resolution, resolution)
                    with self.subTest(
                        case_type=case_type,
                        action=action,
                        resolution=resolution,
                    ), self.assertRaises(api.IncompatibleDecisionError):
                        api.derive_decision_type(case_type, action, payload)

            if case_type is ReviewCaseType.EXTERNAL_IDENTITY:
                raw_without_resolution = {
                    "case_type": payload_case_type,
                    "external_identity_key": "external:one",
                    "scientific_status": "validated",
                }
            else:
                raw_without_resolution = _person(payload_case_type)
            payload_without_resolution = adapter.validate_python(raw_without_resolution)
            self.assertIsNone(payload_without_resolution.resolution)
            self.assertEqual(
                api.derive_decision_type(case_type, "approve", payload_without_resolution),
                ReviewDecisionType.VALIDATED,
            )
            self.assertEqual(
                api.derive_decision_type(case_type, "correct", payload_without_resolution),
                ReviewDecisionType.CORRECTED,
            )

    def test_every_link_capable_payload_rejects_missing_or_invalid_resolution(self) -> None:
        api = _api()
        adapter = TypeAdapter(api.DecisionPayloadV1)
        optional_missing = (
            (ReviewCaseType.PERSON_IDENTITY, _person("person_identity")),
            (ReviewCaseType.AUTHOR_IDENTITY, _person("author_identity")),
            (
                ReviewCaseType.EXTERNAL_IDENTITY,
                {
                    "case_type": "external_identity",
                    "external_identity_key": "external:one",
                    "scientific_status": "validated",
                },
            ),
        )
        for case_type, raw in optional_missing:
            payload = adapter.validate_python(raw)
            with self.subTest(case_type=case_type, missing=True), self.assertRaises(
                api.IncompatibleDecisionError
            ):
                api.derive_decision_type(case_type, "link", payload)

        structurally_invalid = (
            _person("person_identity", "merged"),
            _person("author_identity", "merged"),
            {
                "case_type": "project_director_relation",
                "project_director_identity_key": "human:one",
                "scientific_status": "validated",
            },
            {
                "case_type": "project_director_relation",
                "project_director_identity_key": "human:one",
                "relationship_status": "merged",
                "scientific_status": "validated",
            },
            {
                "case_type": "external_identity",
                "external_identity_key": "external:one",
                "scientific_status": "validated",
                "resolution": "merged",
            },
            {
                "case_type": "possible_duplicate",
                "counterpart_ref": _counterpart_ref(),
                "scientific_status": "validated",
            },
            {
                "case_type": "possible_duplicate",
                "counterpart_ref": _counterpart_ref(),
                "resolution": "linked",
                "scientific_status": "validated",
            },
        )
        for raw in structurally_invalid:
            with self.subTest(case_type=raw["case_type"], raw=raw), self.assertRaises(
                ValidationError
            ):
                adapter.validate_python(raw)

    def test_apply_reason_and_every_payload_text_field_exact_boundaries(self) -> None:
        api = _api()
        api.ApplyDecisionRequest.model_validate(
            _request(_person()) | {"reason": "x" * 4000}
        )
        with self.assertRaises(ValidationError):
            api.ApplyDecisionRequest.model_validate(
                _request(_person()) | {"reason": "x" * 4001}
            )

        text_cases = (
            (
                api.PersonDecisionPayload,
                _person(),
                "canonical_identity_key",
                True,
                320,
            ),
            (api.PersonDecisionPayload, _person(), "canonical_name", False, 500),
            (
                api.ProductDecisionPayload,
                {
                    "case_type": "product",
                    "product_title": "Article",
                    "scientific_status": "validated",
                },
                "product_title",
                True,
                1000,
            ),
            (
                api.RelationDecisionPayload,
                {
                    "case_type": "project_director_relation",
                    "project_director_identity_key": "human:one",
                    "relationship_status": "linked",
                    "scientific_status": "validated",
                },
                "project_director_identity_key",
                True,
                320,
            ),
            (
                api.ExternalDecisionPayload,
                {
                    "case_type": "external_identity",
                    "external_identity_key": "external:one",
                    "external_institution": "Institution",
                    "scientific_status": "validated",
                },
                "external_identity_key",
                True,
                320,
            ),
            (
                api.ExternalDecisionPayload,
                {
                    "case_type": "external_identity",
                    "external_identity_key": "external:one",
                    "external_institution": "Institution",
                    "scientific_status": "validated",
                },
                "external_institution",
                True,
                500,
            ),
        )
        for model, base, field, nullable, maximum in text_cases:
            with self.subTest(model=model.__name__, field=field, boundary="min"):
                model.model_validate(base | {field: "x"})
            with self.subTest(model=model.__name__, field=field, boundary="max"):
                model.model_validate(base | {field: "x" * maximum})
            if nullable:
                with self.subTest(model=model.__name__, field=field, boundary="null"):
                    model.model_validate(base | {field: None})
            else:
                with self.subTest(model=model.__name__, field=field, boundary="missing"), self.assertRaises(
                    ValidationError
                ):
                    without_field = dict(base)
                    without_field.pop(field)
                    model.model_validate(without_field)
            for invalid in ("", "   ", "x" * (maximum + 1)):
                with self.subTest(
                    model=model.__name__, field=field, invalid_length=len(invalid)
                ), self.assertRaises(ValidationError):
                    model.model_validate(base | {field: invalid})

        for alias in ("x", "x" * 500):
            api.PersonDecisionPayload.model_validate(_person() | {"aliases": [alias]})
        for alias in ("", "   ", "x" * 501):
            with self.subTest(field="aliases", invalid_length=len(alias)), self.assertRaises(
                ValidationError
            ):
                api.PersonDecisionPayload.model_validate(_person() | {"aliases": [alias]})
        api.PersonDecisionPayload.model_validate(
            _person() | {"aliases": [f"Alias {index}" for index in range(100)]}
        )
        with self.assertRaises(ValidationError):
            api.PersonDecisionPayload.model_validate(
                _person() | {"aliases": [f"Alias {index}" for index in range(101)]}
            )

        duplicate_base = _duplicate()
        api.DuplicateDecisionPayload.model_validate(duplicate_base)
        for target_type in ReviewTargetTable:
            with self.subTest(target_type=target_type.value):
                parsed = api.DuplicateDecisionPayload.model_validate(
                    duplicate_base
                    | {"counterpart_ref": _counterpart_ref(target_type.value, 99)}
                )
                self.assertEqual(parsed.counterpart_ref.target_type, target_type)
        for invalid in (0, -1, True, "42"):
            with self.subTest(field="target_id", value=invalid), self.assertRaises(
                ValidationError
            ):
                api.DuplicateDecisionPayload.model_validate(
                    duplicate_base
                    | {"counterpart_ref": _counterpart_ref(target_id=invalid)}
                )
        with self.assertRaises(ValidationError):
            api.DuplicateDecisionPayload.model_validate(
                duplicate_base
                | {"counterpart_stable_target_key": "b2b:v1:person_identity:" + "a" * 64}
            )
        missing_counterpart = dict(duplicate_base)
        missing_counterpart.pop("counterpart_ref")
        with self.assertRaises(ValidationError):
            api.DuplicateDecisionPayload.model_validate(missing_counterpart)

    def test_exhaustive_hostile_values_fixed_messages_and_true_immutability(self) -> None:
        api = _api()
        fixed_messages = {
            "HUMAN_REVIEW_VALIDATION": "The human review request is invalid",
            "AUTHENTICATION_REQUIRED": "Authentication is required",
            "B2B_CAPABILITY_REQUIRED": "B2B capability is required",
            "REVIEW_CASE_NOT_FOUND": "Review case was not found",
            "REVIEW_CASE_VERSION_CONFLICT": "Review case version conflict",
            "INCOMPATIBLE_DECISION": "Decision is incompatible",
            "INVALID_COMMAND_PAYLOAD": "Command payload is invalid",
            "HUMAN_REVIEW_INTERNAL_ERROR": "Request could not be completed",
            "EVIDENCE_UNAVAILABLE": "Evidence is unavailable",
        }
        hostile_values = (
            "Bearer synthetic-token-value",
            "password synthetic-secret-value",
            "postgresql://synthetic_user:synthetic_pass@internal-db:5432/private",
            "redis://synthetic_user:synthetic_pass@internal-cache:6379/private",
            "duplicate key violates unique constraint review_items_document_key_key",
            "insert violates foreign key constraint review_items_owner_fkey",
            "ck_review_items_case_status",
            "uq_review_items_target",
            "pk_review_items",
            "relative/internal/private.pdf",
            "backend/app/schemas/human_review_api.py",
            "/root/private/service.py",
            "C:\\Users\\OMAR\\private\\service.py",
        )
        for code, expected_message in fixed_messages.items():
            for hostile in hostile_values:
                response = api.HumanReviewErrorResponse(
                    code=code,
                    message=hostile,
                    correlation_id=UUID_A,
                    details={"reason": hostile, "field": "reason"},
                )
                dumped = response.model_dump(mode="json")
                with self.subTest(code=code, hostile=hostile, surface="message"):
                    self.assertEqual(dumped["message"], expected_message)
                with self.subTest(code=code, hostile=hostile, surface="details"):
                    self.assertNotIn(hostile, str(dumped["details"]))

        hostile_nested = {
            "event_type": "scientific_decision_applied",
            "accessToken": "synthetic-access-token",
            "passwordHashSuffix": "synthetic-password-hash",
            "sourcePath": "relative/internal/private.pdf",
            "bucketKey": "private/object/key",
            "storage_locator": "private-storage/object",
            "object_key": "private/object-key",
            "internalUrl": "postgresql://user:pass@internal-db/private",
            "constraintName": "review_items_document_key_key",
            "nested": {
                "safe": "public",
                "storage_locator": "private-storage/nested-object",
            },
        }
        detail = _queue_item() | {
            "target_table": "person_roles",
            "target_pk": 1,
            "field_path": "canonical_name",
            "detected_value": None,
            "normalized_value": None,
            "canonical_value": None,
            "current_decision_id": None,
            "overrides": (hostile_nested,),
            "effective_memberships": (),
            "evidence_summary": hostile_nested,
        }
        responses = (
            api.ReviewCaseDetail.model_validate(detail),
            api.ReviewQueueResponse(
                items=(),
                total=0,
                page=1,
                page_size=25,
                facets={"statuses": {"pending": 1}, "storage_locator": {"private": 2}},
                correlation_id=UUID_A,
            ),
            api.AuditTimelineResponse(
                items=(hostile_nested,), page=1, page_size=25, total=1,
                correlation_id=UUID_A,
            ),
        )
        forbidden_tokens = (
            "accesstoken", "access_token", "passwordhashsuffix",
            "password_hash_suffix", "sourcepath", "source_path", "bucketkey",
            "bucket_key", "storage_locator", "object_key", "internalurl",
            "internal_url", "constraintname", "constraint_name", "private/object",
            "private-storage", "postgresql://",
        )
        for response in responses:
            serialized = response.model_dump_json().lower()
            with self.subTest(model=type(response).__name__):
                for token in forbidden_tokens:
                    self.assertNotIn(token, serialized)

        evidence_base = {
            "document_name": "Public evidence",
            "page": 1,
            "section": "Methods",
            "stream_path": "evidence/public-item",
            "correlation_id": UUID_A,
        }
        for hostile in hostile_values:
            evidence = api.EvidenceResponse.model_validate(
                evidence_base | {"locator": hostile, "fragment": hostile}
            )
            dumped = evidence.model_dump(mode="json")
            with self.subTest(surface="locator", hostile=hostile):
                self.assertNotEqual(dumped["locator"], hostile)
            with self.subTest(surface="fragment", hostile=hostile):
                self.assertNotEqual(dumped["fragment"], hostile)
        safe_evidence = api.EvidenceResponse.model_validate(
            evidence_base | {"locator": "page 1, Methods", "fragment": "Public excerpt"}
        )
        self.assertEqual(safe_evidence.stream_path, "evidence/public-item")
        self.assertEqual(safe_evidence.locator, "page 1, Methods")
        self.assertEqual(safe_evidence.fragment, "Public excerpt")

        immutable_error = api.HumanReviewErrorResponse(
            code="INCOMPATIBLE_DECISION",
            message="ignored caller text",
            correlation_id=UUID_A,
            details={"field": "reason"},
        )
        mutation_error = None
        try:
            dict.__setitem__(immutable_error.details, "accessToken", "post-mutation-secret")
        except (AttributeError, TypeError) as error:
            mutation_error = error
        with self.subTest(surface="dict_descriptor_bypass"):
            self.assertIsNotNone(
                mutation_error,
                "dict.__setitem__ must not apply to the immutable mapping implementation",
            )
        with self.subTest(surface="post_mutation_serialization"):
            self.assertNotIn("post-mutation-secret", immutable_error.model_dump_json())
        nested_mappings = (
            immutable_error.details,
            responses[0].evidence_summary,
            responses[1].facets,
            responses[2].items[0],
        )
        for nested in nested_mappings:
            with self.subTest(surface="non_dict_mapping", nested_type=type(nested).__name__):
                self.assertNotIsInstance(nested, dict)

    def test_hostile_nested_response_and_error_data_never_serializes(self) -> None:
        api = _api()
        hostile = {
            "safe": "public",
            "token ": "credential-value",
            "password_hash_suffix": "hash-value",
            "constraint_name": "ck_review_items_case_status",
            "nested": {
                "safe": "/tmp/private/service.py",
                "debug": "C:/private/service.py",
                "url": "http://minio:9000/private",
                "sql": "SELECT password_hash FROM users",
                "stack": "Traceback internal.py line 1",
            },
        }
        error = api.HumanReviewErrorResponse(
            code="INCOMPATIBLE_DECISION",
            message="Decision is incompatible",
            correlation_id=UUID_A,
            details=hostile,
        )
        detail = _queue_item() | {
            "target_table": "person_roles",
            "target_pk": 1,
            "field_path": "canonical_name",
            "detected_value": None,
            "normalized_value": None,
            "canonical_value": None,
            "current_decision_id": None,
            "overrides": (hostile,),
            "effective_memberships": (),
            "evidence_summary": hostile,
        }
        responses = (
            error,
            api.ReviewCaseDetail.model_validate(detail),
            api.ReviewQueueResponse(
                items=(), total=0, page=1, page_size=25,
                facets={"token ": {"password_hash_suffix": 1}, "safe": {"minio_url": 2}},
                correlation_id=UUID_A,
            ),
            api.AuditTimelineResponse(items=(hostile,), page=1, page_size=25, total=1, correlation_id=UUID_A),
        )
        forbidden = (
            "credential-value", "hash-value", "constraint_name", "ck_review_items",
            "/tmp/private", "c:/private", "minio", "select password", "traceback",
            "token ", "password_hash_suffix",
        )
        for response in responses:
            serialized = response.model_dump_json().lower()
            with self.subTest(model=type(response).__name__):
                for value in forbidden:
                    self.assertNotIn(value, serialized)
        with self.assertRaises(TypeError):
            error.details["token "] = "added-after-validation"
        self.assertNotIn("added-after-validation", error.model_dump_json())

    def test_evidence_stream_path_is_application_relative_only(self) -> None:
        api = _api()
        base = {"document_name": None, "page": None, "section": None, "locator": None, "fragment": None, "correlation_id": UUID_A}
        for valid in (None, "evidence/11111111-1111-1111-1111-111111111111", "documents/doc-1/pages/2"):
            with self.subTest(valid=valid):
                api.EvidenceResponse.model_validate(base | {"stream_path": valid})
        for invalid in (
            "/tmp/private/service.py", "../private", "C:/private/service.py",
            "C:\\private\\service.py", "http://minio:9000/private",
            "https://dropbox.com/private", "//internal/share", "evidence/../private",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                api.EvidenceResponse.model_validate(base | {"stream_path": invalid})

    def test_all_approved_nullable_response_fields_are_required(self) -> None:
        api = _api()
        expected_required = {
            api.ReviewQueueItem: {
                "source_revision", "source_page", "manual_priority",
            },
            api.ReviewCaseDetail: {
                "source_revision", "source_page", "manual_priority", "target_pk",
                "detected_value", "normalized_value", "canonical_value",
                "current_decision_id",
            },
            api.EffectiveCapabilitiesResponse: {"capability"},
            api.EvidenceResponse: {
                "document_name", "page", "section", "locator", "fragment", "stream_path",
            },
        }
        for model, required_fields in expected_required.items():
            actual = set(model.model_json_schema().get("required", ()))
            with self.subTest(model=model.__name__, surface="json_schema"):
                self.assertTrue(
                    required_fields <= actual,
                    f"required-nullable fields missing from schema: {sorted(required_fields - actual)}",
                )

        queue = _queue_item() | {"source_revision": None, "source_page": None, "manual_priority": None}
        detail = queue | {
            "target_table": "person_roles",
            "target_pk": None,
            "field_path": "canonical_name",
            "detected_value": None,
            "normalized_value": None,
            "canonical_value": None,
            "current_decision_id": None,
            "overrides": (),
            "effective_memberships": (),
            "evidence_summary": {},
        }
        evidence = {
            "document_name": None,
            "page": None,
            "section": None,
            "locator": None,
            "fragment": None,
            "stream_path": None,
            "correlation_id": UUID_A,
        }
        cases = (
            (api.ReviewQueueItem, queue, ("source_revision", "source_page", "manual_priority")),
            (api.ReviewCaseDetail, detail, (
                "source_revision", "source_page", "manual_priority", "target_pk",
                "detected_value", "normalized_value", "canonical_value", "current_decision_id",
            )),
            (api.EffectiveCapabilitiesResponse, {"capability": None, "actions": ()}, ("capability",)),
            (api.EvidenceResponse, evidence, (
                "document_name", "page", "section", "locator", "fragment", "stream_path",
            )),
        )
        for model, complete, fields in cases:
            validated = model.model_validate(complete)
            for field in fields:
                with self.subTest(model=model.__name__, field=field, surface="explicit_null"):
                    self.assertIsNone(getattr(validated, field))
                omitted = dict(complete)
                omitted.pop(field)
                with self.subTest(model=model.__name__, field=field, surface="omitted"), self.assertRaises(
                    ValidationError
                ):
                    model.model_validate(omitted)

    def test_public_response_mapping_json_schemas_are_closed(self) -> None:
        api = _api()
        fields = (
            (api.ReviewQueueResponse, "facets"),
            (api.ReviewCaseDetail, "overrides"),
            (api.ReviewCaseDetail, "evidence_summary"),
            (api.AuditTimelineResponse, "items"),
            (api.HumanReviewErrorResponse, "details"),
        )

        def assert_no_open_mapping(node: object, label: str) -> None:
            if isinstance(node, dict):
                if "additionalProperties" in node:
                    self.assertIs(
                        node["additionalProperties"],
                        False,
                        f"{label} exposes an open Mapping JSON schema",
                    )
                for value in node.values():
                    assert_no_open_mapping(value, label)
            elif isinstance(node, list):
                for value in node:
                    assert_no_open_mapping(value, label)

        for model, field in fields:
            schema = model.model_json_schema()["properties"][field]
            with self.subTest(model=model.__name__, field=field):
                assert_no_open_mapping(schema, f"{model.__name__}.{field}")

    def test_every_public_string_surface_and_model_copy_are_safe(self) -> None:
        api = _api()
        evidence_base = {
            "document_name": "public-evidence.pdf",
            "page": 1,
            "section": "Methods",
            "locator": "page 1",
            "fragment": "Public excerpt",
            "stream_path": "evidence/public-evidence.pdf",
            "correlation_id": UUID_A,
        }
        hostile_by_field = {
            "document_name": "C:\\private\\source.pdf",
            "section": "postgresql://synthetic:synthetic@internal/private",
            "locator": "review_items_status_check",
            "fragment": "passwordHashSuffix synthetic-hash",
        }
        for field, hostile in hostile_by_field.items():
            evidence = api.EvidenceResponse.model_validate(evidence_base | {field: hostile})
            with self.subTest(field=field, surface="normal_validation"):
                self.assertNotEqual(evidence.model_dump(mode="json")[field], hostile)
            copied = api.EvidenceResponse.model_validate(evidence_base).model_copy(
                update={field: hostile}
            )
            with self.subTest(field=field, surface="model_copy"):
                self.assertNotEqual(copied.model_dump(mode="json")[field], hostile)
            forced = api.EvidenceResponse.model_validate(evidence_base)
            object.__setattr__(forced, field, hostile)
            with self.subTest(field=field, surface="serialization_recheck"):
                self.assertNotEqual(forced.model_dump(mode="json")[field], hostile)

        safe = api.EvidenceResponse.model_validate(evidence_base)
        self.assertEqual(safe.document_name, "public-evidence.pdf")
        self.assertEqual(safe.section, "Methods")
        with self.assertRaises(ValidationError):
            safe.model_copy(update={"stream_path": "../private/source.pdf"})

    def _dump_without_exception(self, model, *, context: str) -> dict[str, object]:
        try:
            return model.model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001 - converts serializer crashes into behavioral RED
            self.fail(f"{context} raised {type(exc).__name__}: {exc}")

    def test_canonical_hostile_corpus_is_redacted_on_every_public_surface_and_boundary(self) -> None:
        api = _api()

        for label, hostile in _CANONICAL_HOSTILE_PUBLIC_TEXT:
            for field in ("document_name", "section", "locator", "fragment"):
                for boundary in ("normal", "model_copy", "forced_serialization"):
                    with self.subTest(
                        corpus="hostile", label=label, surface="evidence",
                        field=field, boundary=boundary,
                    ):
                        base = _canonical_evidence_data()
                        if boundary == "normal":
                            model = api.EvidenceResponse.model_validate(base | {field: hostile})
                        elif boundary == "model_copy":
                            model = api.EvidenceResponse.model_validate(base).model_copy(
                                update={field: hostile}
                            )
                        else:
                            model = api.EvidenceResponse.model_validate(base)
                            object.__setattr__(model, field, hostile)
                        dumped = self._dump_without_exception(
                            model,
                            context=f"hostile evidence {label}/{field}/{boundary}",
                        )
                        self.assertNotEqual(dumped[field], hostile)

            for boundary in ("normal", "model_copy", "forced_serialization"):
                with self.subTest(
                    corpus="hostile", label=label, surface="kpi_metric", boundary=boundary,
                ):
                    base = {"metric": "eligible_products", "before": 1, "after": 2, "delta": 1}
                    if boundary == "normal":
                        model = api.KpiEffectItem.model_validate(base | {"metric": hostile})
                    elif boundary == "model_copy":
                        model = api.KpiEffectItem.model_validate(base).model_copy(
                            update={"metric": hostile}
                        )
                    else:
                        model = api.KpiEffectItem.model_validate(base)
                        object.__setattr__(model, "metric", hostile)
                    dumped = self._dump_without_exception(
                        model,
                        context=f"hostile KPI {label}/{boundary}",
                    )
                    self.assertNotEqual(dumped["metric"], hostile)

            for audit_surface in ("summary", "nested_metric"):
                for boundary in ("normal", "model_copy", "forced_serialization"):
                    with self.subTest(
                        corpus="hostile", label=label, surface=f"audit_{audit_surface}",
                        boundary=boundary,
                    ):
                        summary = hostile if audit_surface == "summary" else "Scientific decision applied"
                        metric = hostile if audit_surface == "nested_metric" else "eligible_products"
                        data = _canonical_audit_data(summary=summary, metric=metric)
                        safe_data = _canonical_audit_data(
                            summary="Scientific decision applied", metric="eligible_products"
                        )
                        if boundary == "normal":
                            model = api.AuditTimelineResponse.model_validate(data)
                        elif boundary == "model_copy":
                            model = api.AuditTimelineResponse.model_validate(safe_data).model_copy(
                                update={"items": data["items"]}
                            )
                        else:
                            model = api.AuditTimelineResponse.model_validate(safe_data)
                            object.__setattr__(model, "items", data["items"])
                        dumped = self._dump_without_exception(
                            model,
                            context=f"hostile audit {label}/{audit_surface}/{boundary}",
                        )["items"][0]
                        actual = (
                            dumped.get("summary")
                            if audit_surface == "summary"
                            else dumped.get("payload", {}).get("kpi_effect", ({},))[0].get("metric")
                        )
                        self.assertNotEqual(actual, hostile)

            for field in ("detected_value", "normalized_value", "canonical_value"):
                for boundary in ("normal", "model_copy", "forced_serialization"):
                    with self.subTest(
                        corpus="hostile", label=label, surface="case_value",
                        field=field, boundary=boundary,
                    ):
                        base = _canonical_case_data()
                        if boundary == "normal":
                            model = api.ReviewCaseDetail.model_validate(base | {field: hostile})
                        elif boundary == "model_copy":
                            model = api.ReviewCaseDetail.model_validate(base).model_copy(
                                update={field: hostile}
                            )
                        else:
                            model = api.ReviewCaseDetail.model_validate(base)
                            object.__setattr__(model, field, hostile)
                        dumped = self._dump_without_exception(
                            model,
                            context=f"hostile case {label}/{field}/{boundary}",
                        )
                        self.assertNotEqual(dumped[field], hostile)

            nested_surfaces = (
                "override_value", "document_name", "section", "locator", "fragment",
            )
            for surface in nested_surfaces:
                for boundary in ("normal", "model_copy", "forced_serialization"):
                    with self.subTest(
                        corpus="hostile", label=label, surface=f"nested_{surface}",
                        boundary=boundary,
                    ):
                        data = _canonical_case_data()
                        override = dict(data["overrides"][0])
                        summary = dict(data["evidence_summary"])
                        if surface == "override_value":
                            override["value"] = hostile
                        else:
                            summary[surface] = hostile
                        updates = {"overrides": (override,), "evidence_summary": summary}
                        if boundary == "normal":
                            model = api.ReviewCaseDetail.model_validate(data | updates)
                        elif boundary == "model_copy":
                            model = api.ReviewCaseDetail.model_validate(data).model_copy(update=updates)
                        else:
                            model = api.ReviewCaseDetail.model_validate(data)
                            object.__setattr__(model, "overrides", updates["overrides"])
                            object.__setattr__(model, "evidence_summary", updates["evidence_summary"])
                        dumped = self._dump_without_exception(
                            model,
                            context=f"hostile nested {label}/{surface}/{boundary}",
                        )
                        actual = (
                            dumped["overrides"][0].get("value")
                            if surface == "override_value"
                            else dumped["evidence_summary"].get(surface)
                        )
                        self.assertNotEqual(actual, hostile)

    def test_canonical_error_details_remain_closed_at_every_boundary(self) -> None:
        api = _api()
        base = {
            "code": "INCOMPATIBLE_DECISION",
            "message": "ignored caller text",
            "correlation_id": UUID_A,
            "details": {"field": "reason"},
        }
        for label, hostile in _CANONICAL_HOSTILE_PUBLIC_TEXT:
            for boundary in ("normal", "model_copy", "forced_serialization"):
                with self.subTest(label=label, boundary=boundary):
                    details = {"field": hostile}
                    if boundary == "normal":
                        model = api.HumanReviewErrorResponse.model_validate(base | {"details": details})
                    elif boundary == "model_copy":
                        model = api.HumanReviewErrorResponse.model_validate(base).model_copy(
                            update={"details": details}
                        )
                    else:
                        model = api.HumanReviewErrorResponse.model_validate(base)
                        object.__setattr__(model, "details", details)
                    dumped = self._dump_without_exception(
                        model,
                        context=f"canonical error details {label}/{boundary}",
                    )
                    self.assertNotEqual(dumped["details"].get("field"), hostile)

    def test_canonical_legitimate_corpus_is_preserved_exactly_at_every_boundary(self) -> None:
        api = _api()

        for value in _CANONICAL_SAFE_DOCUMENT_NAMES:
            for boundary in ("normal", "model_copy", "forced_serialization"):
                with self.subTest(corpus="safe", surface="document_name", value=value, boundary=boundary):
                    base = _canonical_evidence_data()
                    if boundary == "normal":
                        model = api.EvidenceResponse.model_validate(base | {"document_name": value})
                    elif boundary == "model_copy":
                        model = api.EvidenceResponse.model_validate(base).model_copy(
                            update={"document_name": value}
                        )
                    else:
                        model = api.EvidenceResponse.model_validate(base)
                        object.__setattr__(model, "document_name", value)
                    dumped = self._dump_without_exception(
                        model,
                        context=f"safe document_name {value}/{boundary}",
                    )
                    self.assertEqual(dumped["document_name"], value)

        for value in _CANONICAL_SAFE_PUBLIC_TEXT:
            for field in ("section", "locator", "fragment"):
                for boundary in ("normal", "model_copy", "forced_serialization"):
                    with self.subTest(
                        corpus="safe", surface="evidence", field=field,
                        value=value, boundary=boundary,
                    ):
                        base = _canonical_evidence_data()
                        if boundary == "normal":
                            model = api.EvidenceResponse.model_validate(base | {field: value})
                        elif boundary == "model_copy":
                            model = api.EvidenceResponse.model_validate(base).model_copy(
                                update={field: value}
                            )
                        else:
                            model = api.EvidenceResponse.model_validate(base)
                            object.__setattr__(model, field, value)
                        dumped = self._dump_without_exception(
                            model,
                            context=f"safe evidence {field}/{value}/{boundary}",
                        )
                        self.assertEqual(dumped[field], value)

            for boundary in ("normal", "model_copy", "forced_serialization"):
                with self.subTest(corpus="safe", surface="kpi", value=value, boundary=boundary):
                    base = {"metric": "eligible_products", "before": 1, "after": 2, "delta": 1}
                    if boundary == "normal":
                        model = api.KpiEffectItem.model_validate(base | {"metric": value})
                    elif boundary == "model_copy":
                        model = api.KpiEffectItem.model_validate(base).model_copy(
                            update={"metric": value}
                        )
                    else:
                        model = api.KpiEffectItem.model_validate(base)
                        object.__setattr__(model, "metric", value)
                    dumped = self._dump_without_exception(
                        model,
                        context=f"safe KPI {value}/{boundary}",
                    )
                    self.assertEqual(dumped["metric"], value)

            for audit_surface in ("summary", "nested_metric"):
                for boundary in ("normal", "model_copy", "forced_serialization"):
                    with self.subTest(
                        corpus="safe", surface=f"audit_{audit_surface}",
                        value=value, boundary=boundary,
                    ):
                        summary = value if audit_surface == "summary" else "Scientific decision applied"
                        metric = value if audit_surface == "nested_metric" else "eligible_products"
                        data = _canonical_audit_data(summary=summary, metric=metric)
                        if boundary == "normal":
                            model = api.AuditTimelineResponse.model_validate(data)
                        elif boundary == "model_copy":
                            model = api.AuditTimelineResponse.model_validate(
                                _canonical_audit_data(
                                    summary="Scientific decision applied", metric="eligible_products"
                                )
                            ).model_copy(update={"items": data["items"]})
                        else:
                            model = api.AuditTimelineResponse.model_validate(
                                _canonical_audit_data(
                                    summary="Scientific decision applied", metric="eligible_products"
                                )
                            )
                            object.__setattr__(model, "items", data["items"])
                        item = self._dump_without_exception(
                            model,
                            context=f"safe audit {audit_surface}/{value}/{boundary}",
                        )["items"][0]
                        actual = (
                            item.get("summary")
                            if audit_surface == "summary"
                            else item["payload"]["kpi_effect"][0].get("metric")
                        )
                        self.assertEqual(actual, value)

            for field in ("detected_value", "normalized_value", "canonical_value"):
                for boundary in ("normal", "model_copy", "forced_serialization"):
                    with self.subTest(
                        corpus="safe", surface="case_value", field=field,
                        value=value, boundary=boundary,
                    ):
                        base = _canonical_case_data()
                        if boundary == "normal":
                            model = api.ReviewCaseDetail.model_validate(base | {field: value})
                        elif boundary == "model_copy":
                            model = api.ReviewCaseDetail.model_validate(base).model_copy(
                                update={field: value}
                            )
                        else:
                            model = api.ReviewCaseDetail.model_validate(base)
                            object.__setattr__(model, field, value)
                        dumped = self._dump_without_exception(
                            model,
                            context=f"safe case {field}/{value}/{boundary}",
                        )
                        self.assertEqual(dumped[field], value)

            for nested_surface in ("override_value", "evidence_fragment"):
                for boundary in ("normal", "model_copy", "forced_serialization"):
                    with self.subTest(
                        corpus="safe", surface=nested_surface, value=value, boundary=boundary,
                    ):
                        data = _canonical_case_data()
                        override = dict(data["overrides"][0])
                        summary = dict(data["evidence_summary"])
                        if nested_surface == "override_value":
                            override["value"] = value
                        else:
                            summary["fragment"] = value
                        updates = {"overrides": (override,), "evidence_summary": summary}
                        if boundary == "normal":
                            model = api.ReviewCaseDetail.model_validate(data | updates)
                        elif boundary == "model_copy":
                            model = api.ReviewCaseDetail.model_validate(data).model_copy(update=updates)
                        else:
                            model = api.ReviewCaseDetail.model_validate(data)
                            object.__setattr__(model, "overrides", updates["overrides"])
                            object.__setattr__(model, "evidence_summary", updates["evidence_summary"])
                        dumped = self._dump_without_exception(
                            model,
                            context=f"safe nested {nested_surface}/{value}/{boundary}",
                        )
                        actual = (
                            dumped["overrides"][0]["value"]
                            if nested_surface == "override_value"
                            else dumped["evidence_summary"]["fragment"]
                        )
                        self.assertEqual(actual, value)

    def test_field_specific_4000_character_values_are_preserved_without_truncation(self) -> None:
        api = _api()
        for length in (1001, 4000):
            value = "A" * length
            for boundary in ("normal", "model_copy", "forced_serialization"):
                with self.subTest(length=length, surface="evidence_fragment", boundary=boundary):
                    base = _canonical_evidence_data()
                    if boundary == "normal":
                        model = api.EvidenceResponse.model_validate(base | {"fragment": value})
                    elif boundary == "model_copy":
                        model = api.EvidenceResponse.model_validate(base).model_copy(
                            update={"fragment": value}
                        )
                    else:
                        model = api.EvidenceResponse.model_validate(base)
                        object.__setattr__(model, "fragment", value)
                    self.assertEqual(
                        self._dump_without_exception(
                            model, context=f"length evidence {length}/{boundary}"
                        )["fragment"],
                        value,
                    )

                for field in ("detected_value", "normalized_value", "canonical_value"):
                    with self.subTest(length=length, surface="case", field=field, boundary=boundary):
                        base = _canonical_case_data()
                        if boundary == "normal":
                            model = api.ReviewCaseDetail.model_validate(base | {field: value})
                        elif boundary == "model_copy":
                            model = api.ReviewCaseDetail.model_validate(base).model_copy(
                                update={field: value}
                            )
                        else:
                            model = api.ReviewCaseDetail.model_validate(base)
                            object.__setattr__(model, field, value)
                        self.assertEqual(
                            self._dump_without_exception(
                                model, context=f"length case {field}/{length}/{boundary}"
                            )[field],
                            value,
                        )

                for nested_surface in ("override_value", "evidence_fragment"):
                    with self.subTest(length=length, surface=nested_surface, boundary=boundary):
                        data = _canonical_case_data()
                        override = dict(data["overrides"][0])
                        summary = dict(data["evidence_summary"])
                        if nested_surface == "override_value":
                            override["value"] = value
                        else:
                            summary["fragment"] = value
                        updates = {"overrides": (override,), "evidence_summary": summary}
                        if boundary == "normal":
                            model = api.ReviewCaseDetail.model_validate(data | updates)
                        elif boundary == "model_copy":
                            model = api.ReviewCaseDetail.model_validate(data).model_copy(update=updates)
                        else:
                            model = api.ReviewCaseDetail.model_validate(data)
                            object.__setattr__(model, "overrides", updates["overrides"])
                            object.__setattr__(model, "evidence_summary", updates["evidence_summary"])
                        dumped = self._dump_without_exception(
                            model, context=f"length nested {nested_surface}/{length}/{boundary}"
                        )
                        actual = (
                            dumped["overrides"][0]["value"]
                            if nested_surface == "override_value"
                            else dumped["evidence_summary"]["fragment"]
                        )
                        self.assertEqual(actual, value)

    def test_forced_unexpected_public_types_serialize_safely_without_exception(self) -> None:
        api = _api()
        unexpected_values = (
            ("dict", {"source_path": "C:/private/a.pdf"}),
            ("list", ["C:/private/a.pdf"]),
            ("int", 7),
            ("none", None),
        )
        for type_label, unexpected in unexpected_values:
            for field in ("document_name", "section", "locator", "fragment", "stream_path"):
                with self.subTest(type=type_label, surface="evidence", field=field):
                    model = api.EvidenceResponse.model_validate(_canonical_evidence_data())
                    object.__setattr__(model, field, unexpected)
                    dumped = self._dump_without_exception(
                        model, context=f"forced evidence type {type_label}/{field}"
                    )
                    self.assertNotIn("C:/private/a.pdf", repr(dumped[field]))
                    self.assertNotIsInstance(dumped[field], (dict, list))

            with self.subTest(type=type_label, surface="kpi_metric"):
                model = api.KpiEffectItem(
                    metric="eligible_products", before=1, after=2, delta=1
                )
                object.__setattr__(model, "metric", unexpected)
                dumped = self._dump_without_exception(
                    model, context=f"forced KPI type {type_label}"
                )
                self.assertNotIn("C:/private/a.pdf", repr(dumped["metric"]))
                self.assertNotIsInstance(dumped["metric"], (dict, list))

            for audit_surface in ("items", "summary", "nested_metric"):
                with self.subTest(type=type_label, surface=f"audit_{audit_surface}"):
                    model = api.AuditTimelineResponse.model_validate(
                        _canonical_audit_data(
                            summary="Scientific decision applied", metric="eligible_products"
                        )
                    )
                    if audit_surface == "items":
                        object.__setattr__(model, "items", unexpected)
                    else:
                        item = _canonical_audit_item(
                            summary=unexpected if audit_surface == "summary" else "Scientific decision applied",
                            metric=unexpected if audit_surface == "nested_metric" else "eligible_products",
                        )
                        object.__setattr__(model, "items", (item,))
                    dumped = self._dump_without_exception(
                        model, context=f"forced audit type {type_label}/{audit_surface}"
                    )
                    self.assertNotIn("C:/private/a.pdf", repr(dumped))

            for field in ("detected_value", "normalized_value", "canonical_value", "effective_memberships"):
                with self.subTest(type=type_label, surface="case", field=field):
                    model = api.ReviewCaseDetail.model_validate(_canonical_case_data())
                    forced_value = (unexpected,) if field == "effective_memberships" else unexpected
                    object.__setattr__(model, field, forced_value)
                    dumped = self._dump_without_exception(
                        model, context=f"forced case type {type_label}/{field}"
                    )
                    self.assertNotIn("C:/private/a.pdf", repr(dumped[field]))
                    if field != "effective_memberships":
                        self.assertNotIsInstance(dumped[field], (dict, list))

            for nested_field in ("overrides", "evidence_summary"):
                with self.subTest(type=type_label, surface=f"case_{nested_field}"):
                    model = api.ReviewCaseDetail.model_validate(_canonical_case_data())
                    object.__setattr__(model, nested_field, unexpected)
                    dumped = self._dump_without_exception(
                        model, context=f"forced nested container {type_label}/{nested_field}"
                    )
                    self.assertNotIn("C:/private/a.pdf", repr(dumped[nested_field]))

    def test_closure_evidence_strings_reject_security_syntax_at_all_boundaries(self) -> None:
        api = _api()
        base = {
            "document_name": "public-evidence.pdf",
            "page": 1,
            "section": "Methods",
            "locator": "page 1",
            "fragment": "Public excerpt",
            "stream_path": "evidence/public-evidence.pdf",
            "correlation_id": UUID_A,
        }
        for label, hostile in _CLOSURE_HOSTILE_PUBLIC_TEXT:
            for field in ("document_name", "section", "locator", "fragment"):
                with self.subTest(label=label, field=field, boundary="normal"):
                    normal = api.EvidenceResponse.model_validate(base | {field: hostile})
                    self.assertNotEqual(normal.model_dump(mode="json")[field], hostile)
                with self.subTest(label=label, field=field, boundary="model_copy"):
                    copied = api.EvidenceResponse.model_validate(base).model_copy(
                        update={field: hostile}
                    )
                    self.assertNotEqual(copied.model_dump(mode="json")[field], hostile)
                with self.subTest(label=label, field=field, boundary="forced_serialization"):
                    forced = api.EvidenceResponse.model_validate(base)
                    object.__setattr__(forced, field, hostile)
                    self.assertNotEqual(forced.model_dump(mode="json")[field], hostile)

    def test_closure_kpi_metric_rejects_security_syntax_at_all_boundaries(self) -> None:
        api = _api()
        base = {"metric": "eligible_products", "before": 1, "after": 2, "delta": 1}
        for label, hostile in _CLOSURE_HOSTILE_PUBLIC_TEXT:
            with self.subTest(label=label, boundary="normal"):
                normal = api.KpiEffectItem.model_validate(base | {"metric": hostile})
                self.assertNotEqual(normal.model_dump(mode="json")["metric"], hostile)
            with self.subTest(label=label, boundary="model_copy"):
                copied = api.KpiEffectItem.model_validate(base).model_copy(
                    update={"metric": hostile}
                )
                self.assertNotEqual(copied.model_dump(mode="json")["metric"], hostile)
            with self.subTest(label=label, boundary="forced_serialization"):
                forced = api.KpiEffectItem.model_validate(base)
                object.__setattr__(forced, "metric", hostile)
                self.assertNotEqual(forced.model_dump(mode="json")["metric"], hostile)

    def test_closure_audit_strings_and_payloads_reject_security_syntax_at_all_boundaries(self) -> None:
        api = _api()

        def audit_item(value: str) -> dict[str, object]:
            return {
                "event_type": value,
                "actor_id": value,
                "decision_id": value,
                "summary": value,
                "payload": {
                    "kind": value,
                    "decision_id": value,
                    "decision_type": value,
                    "kpi_effect": ({"metric": value, "before": 1, "after": 2, "delta": 1},),
                },
            }

        def exposed_values(response) -> tuple[object, ...]:
            item = response.model_dump(mode="json")["items"][0]
            payload = item.get("payload", {})
            effects = payload.get("kpi_effect", ())
            effect = effects[0] if effects else {}
            return (
                item.get("event_type"),
                item.get("actor_id"),
                item.get("decision_id"),
                item.get("summary"),
                payload.get("kind"),
                payload.get("decision_id"),
                payload.get("decision_type"),
                effect.get("metric"),
            )

        safe_item = audit_item("scientific_decision_applied") | {
            "actor_id": "1",
            "decision_id": str(UUID_B),
            "summary": "Scientific decision applied",
            "payload": {
                "kind": "scientific_decision_applied",
                "decision_id": str(UUID_B),
                "decision_type": "validated",
                "kpi_effect": ({"metric": "eligible_products", "before": 1, "after": 2, "delta": 1},),
            },
        }
        response_base = {
            "items": (safe_item,), "page": 1, "page_size": 25, "total": 1,
            "correlation_id": UUID_A,
        }
        for label, hostile in _CLOSURE_HOSTILE_PUBLIC_TEXT:
            with self.subTest(label=label, boundary="normal"):
                normal = api.AuditTimelineResponse.model_validate(
                    response_base | {"items": (audit_item(hostile),)}
                )
                self.assertNotIn(hostile, exposed_values(normal))
            with self.subTest(label=label, boundary="model_copy"):
                copied = api.AuditTimelineResponse.model_validate(response_base).model_copy(
                    update={"items": (audit_item(hostile),)}
                )
                self.assertNotIn(hostile, exposed_values(copied))
            with self.subTest(label=label, boundary="forced_serialization"):
                forced = api.AuditTimelineResponse.model_validate(response_base)
                object.__setattr__(forced, "items", (audit_item(hostile),))
                self.assertNotIn(hostile, exposed_values(forced))

    def test_closure_error_details_are_closed_per_key_at_all_boundaries(self) -> None:
        api = _api()
        detail_hostiles = _CLOSURE_HOSTILE_PUBLIC_TEXT + (
            ("lowercase_github_token", "ghp_synthetic0123456789abcdef"),
            ("token_identifier", "synthetic_token_value"),
            ("password_hash_identifier", "password_hash_material"),
            ("private_key_identifier", "private_key_material"),
            ("internal_identifier", "internal_database_name"),
        )
        base = {
            "code": "INCOMPATIBLE_DECISION",
            "message": "ignored caller text",
            "correlation_id": UUID_A,
            "details": {"field": "reason"},
        }
        for label, hostile in detail_hostiles:
            with self.subTest(label=label, boundary="normal"):
                normal = api.HumanReviewErrorResponse.model_validate(
                    base | {"details": {"field": hostile}}
                )
                self.assertNotEqual(normal.model_dump(mode="json")["details"].get("field"), hostile)
            with self.subTest(label=label, boundary="model_copy"):
                copied = api.HumanReviewErrorResponse.model_validate(base).model_copy(
                    update={"details": {"field": hostile}}
                )
                self.assertNotEqual(copied.model_dump(mode="json")["details"].get("field"), hostile)
            with self.subTest(label=label, boundary="forced_serialization"):
                forced = api.HumanReviewErrorResponse.model_validate(base)
                object.__setattr__(forced, "details", {"field": hostile})
                self.assertNotEqual(forced.model_dump(mode="json")["details"].get("field"), hostile)

    def test_closure_nested_mappings_reject_security_syntax_at_all_boundaries(self) -> None:
        api = _api()

        def detail_data(value: str) -> dict[str, object]:
            return _queue_item() | {
                "target_table": "person_roles",
                "target_pk": 1,
                "field_path": "canonical_name",
                "detected_value": None,
                "normalized_value": None,
                "canonical_value": None,
                "current_decision_id": None,
                "overrides": ({"field": value, "scope": value, "value": value},),
                "effective_memberships": (),
                "evidence_summary": {
                    "document_name": value,
                    "section": value,
                    "locator": value,
                    "fragment": value,
                },
            }

        def exposed_values(response) -> tuple[object, ...]:
            dumped = response.model_dump(mode="json")
            override = dumped["overrides"][0]
            summary = dumped["evidence_summary"]
            return (
                override.get("field"),
                override.get("scope"),
                override.get("value"),
                summary.get("document_name"),
                summary.get("section"),
                summary.get("locator"),
                summary.get("fragment"),
            )

        safe_data = detail_data("Public corrected value") | {
            "overrides": ({"field": "canonical_name", "scope": "record", "value": "Public corrected value"},),
            "evidence_summary": {
                "document_name": "FCI-021_2025.pdf",
                "section": "Methods",
                "locator": "page 3, table 2",
                "fragment": "Public evidence excerpt",
            },
        }
        for label, hostile in _CLOSURE_HOSTILE_PUBLIC_TEXT:
            hostile_data = detail_data(hostile)
            with self.subTest(label=label, boundary="normal"):
                normal = api.ReviewCaseDetail.model_validate(hostile_data)
                self.assertNotIn(hostile, exposed_values(normal))
            with self.subTest(label=label, boundary="model_copy"):
                copied = api.ReviewCaseDetail.model_validate(safe_data).model_copy(
                    update={
                        "overrides": hostile_data["overrides"],
                        "evidence_summary": hostile_data["evidence_summary"],
                    }
                )
                self.assertNotIn(hostile, exposed_values(copied))
            with self.subTest(label=label, boundary="forced_serialization"):
                forced = api.ReviewCaseDetail.model_validate(safe_data)
                object.__setattr__(forced, "overrides", hostile_data["overrides"])
                object.__setattr__(forced, "evidence_summary", hostile_data["evidence_summary"])
                self.assertNotIn(hostile, exposed_values(forced))

    def test_closure_security_policy_preserves_legitimate_public_values_at_all_boundaries(self) -> None:
        api = _api()
        evidence_values = {
            "document_name": "FCI-021_2025.pdf",
            "section": "Section 3.2 — Metodología",
            "locator": "page 12, table 3",
            "fragment": "First public sentence.\nSecond public sentence.",
        }
        evidence_base = evidence_values | {
            "page": 12,
            "stream_path": "evidence/FCI-021_2025.pdf",
            "correlation_id": UUID_A,
        }
        for field, value in evidence_values.items():
            with self.subTest(surface="evidence", field=field, boundary="normal"):
                self.assertEqual(
                    api.EvidenceResponse.model_validate(evidence_base).model_dump(mode="json")[field],
                    value,
                )
            with self.subTest(surface="evidence", field=field, boundary="model_copy"):
                copied = api.EvidenceResponse.model_validate(evidence_base).model_copy(
                    update={field: value}
                )
                self.assertEqual(copied.model_dump(mode="json")[field], value)
            with self.subTest(surface="evidence", field=field, boundary="forced_serialization"):
                forced = api.EvidenceResponse.model_validate(evidence_base)
                object.__setattr__(forced, field, value)
                self.assertEqual(forced.model_dump(mode="json")[field], value)

        for metric in ("eligible_products", "validated people", "KPI 2: eligible products"):
            base_kpi = api.KpiEffectItem(metric="eligible_products", before=1, after=2, delta=1)
            with self.subTest(surface="kpi", value=metric, boundary="normal"):
                self.assertEqual(
                    api.KpiEffectItem(metric=metric, before=1, after=2, delta=1).metric,
                    metric,
                )
            with self.subTest(surface="kpi", value=metric, boundary="model_copy"):
                self.assertEqual(base_kpi.model_copy(update={"metric": metric}).metric, metric)
            with self.subTest(surface="kpi", value=metric, boundary="forced_serialization"):
                object.__setattr__(base_kpi, "metric", metric)
                self.assertEqual(base_kpi.model_dump(mode="json")["metric"], metric)

        safe_details = {
            "field": "reason",
            "current_status": "pending",
            "target_status": "resolved",
            "case_type": "person_identity",
            "action": "correct",
            "resolution": "separated",
            "scope": "record",
            "expected_version": 1,
            "actual_version": 2,
        }
        error_base = api.HumanReviewErrorResponse(
            code="INCOMPATIBLE_DECISION",
            message="ignored caller text",
            correlation_id=UUID_A,
            details=safe_details,
        )
        with self.subTest(surface="error_details", boundary="normal"):
            self.assertEqual(error_base.model_dump(mode="json")["details"], safe_details)
        with self.subTest(surface="error_details", boundary="model_copy"):
            self.assertEqual(
                error_base.model_copy(update={"details": safe_details}).model_dump(mode="json")["details"],
                safe_details,
            )
        with self.subTest(surface="error_details", boundary="forced_serialization"):
            object.__setattr__(error_base, "details", safe_details)
            self.assertEqual(error_base.model_dump(mode="json")["details"], safe_details)

        safe_audit_item = {
            "id": str(UUID_C),
            "event_type": "scientific_decision_applied",
            "actor_id": 1,
            "review_item_id": str(UUID_A),
            "decision_id": str(UUID_B),
            "created_at": "2026-07-21T15:00:00+00:00",
            "correlation_id": str(UUID_C),
            "summary": "Scientific decision applied",
            "payload": {
                "kind": "scientific_decision_applied",
                "schema_version": 1,
                "decision_id": str(UUID_B),
                "decision_type": "validated",
                "previous_case_status": "pending",
                "resulting_case_status": "resolved",
                "review_item_id": str(UUID_A),
                "kpi_effect": ({"metric": "eligible_products", "before": 1, "after": 2, "delta": 1},),
            },
        }
        audit_base = {
            "items": (safe_audit_item,), "page": 1, "page_size": 25, "total": 1,
            "correlation_id": UUID_A,
        }
        expected_audit_summary = "Scientific decision applied"
        expected_audit_metric = "eligible_products"
        for boundary in ("normal", "model_copy", "forced_serialization"):
            audit = api.AuditTimelineResponse.model_validate(audit_base)
            if boundary == "model_copy":
                audit = audit.model_copy(update={"items": (safe_audit_item,)})
            elif boundary == "forced_serialization":
                object.__setattr__(audit, "items", (safe_audit_item,))
            dumped_audit = audit.model_dump(mode="json")["items"][0]
            with self.subTest(surface="audit", boundary=boundary, field="summary"):
                self.assertEqual(dumped_audit["summary"], expected_audit_summary)
            with self.subTest(surface="audit", boundary=boundary, field="metric"):
                self.assertEqual(
                    dumped_audit["payload"]["kpi_effect"][0]["metric"],
                    expected_audit_metric,
                )

        safe_detail_data = _queue_item() | {
            "target_table": "person_roles",
            "target_pk": 1,
            "field_path": "canonical_name",
            "detected_value": None,
            "normalized_value": None,
            "canonical_value": None,
            "current_decision_id": None,
            "overrides": ({
                "decision_id": str(UUID_B),
                "field": "canonical_name",
                "field_path": "canonical_name",
                "scope": "record",
                "scope_id": str(UUID_A),
                "value": "María Pérez",
                "created_at": "2026-07-21T15:00:00+00:00",
            },),
            "effective_memberships": (),
            "evidence_summary": {
                "available": True,
                "count": 1,
                "document_name": "FCI-021_2025.pdf",
                "page": 12,
                "section": "Methods and results",
                "locator": "page 12, table 3",
                "fragment": "First public sentence.\nSecond public sentence.",
                "stream_path": "evidence/FCI-021_2025.pdf",
            },
        }
        for boundary in ("normal", "model_copy", "forced_serialization"):
            detail = api.ReviewCaseDetail.model_validate(safe_detail_data)
            if boundary == "model_copy":
                detail = detail.model_copy(update={
                    "overrides": safe_detail_data["overrides"],
                    "evidence_summary": safe_detail_data["evidence_summary"],
                })
            elif boundary == "forced_serialization":
                object.__setattr__(detail, "overrides", safe_detail_data["overrides"])
                object.__setattr__(detail, "evidence_summary", safe_detail_data["evidence_summary"])
            dumped_detail = detail.model_dump(mode="json")
            with self.subTest(surface="nested_override", boundary=boundary):
                self.assertEqual(dumped_detail["overrides"][0]["value"], "María Pérez")
            with self.subTest(surface="nested_evidence", boundary=boundary):
                self.assertEqual(
                    dumped_detail["evidence_summary"]["fragment"],
                    "First public sentence.\nSecond public sentence.",
                )

    def test_model_copy_revalidates_fixed_error_messages(self) -> None:
        api = _api()
        hostile = "passwordHashSuffix synthetic-hash"
        error = api.HumanReviewErrorResponse(
            code="INCOMPATIBLE_DECISION",
            message="ignored caller text",
            correlation_id=UUID_A,
            details={"field": "reason"},
        )
        copied_error = error.model_copy(update={"message": "internal://private/service"})
        self.assertEqual(copied_error.message, "Decision is incompatible")
        self.assertNotIn("internal://", copied_error.model_dump_json())

    def test_audit_and_kpi_generic_public_scalars_use_the_safety_predicate(self) -> None:
        api = _api()
        hostile = "passwordHashSuffix synthetic-hash"
        kpi = api.KpiEffectItem(metric=hostile, before=1, after=2, delta=1)
        self.assertNotIn(hostile, kpi.model_dump_json())
        copied_kpi = api.KpiEffectItem(metric="public_metric", before=1, after=2, delta=1).model_copy(
            update={"metric": hostile}
        )
        self.assertNotIn(hostile, copied_kpi.model_dump_json())

        audit = api.AuditTimelineResponse(
            items=({
                "event_type": "scientific_decision_applied",
                "summary": hostile,
                "payload": {
                    "kind": "scientific_decision_applied",
                    "kpi_effect": ({"metric": hostile, "before": 1, "after": 2, "delta": 1},),
                },
            },),
            page=1,
            page_size=25,
            total=1,
            correlation_id=UUID_A,
        )
        self.assertNotIn(hostile, audit.model_dump_json())

    def test_final_close_prohibited_values_are_redacted_on_direct_and_nested_boundaries(
        self,
    ) -> None:
        api = _api()

        for prohibited in _FINAL_CLOSE_PROHIBITED_PUBLIC_TEXT:
            for boundary in ("normal", "model_copy", "forced_serialization"):
                evidence_data = _canonical_evidence_data() | {"fragment": prohibited}
                if boundary == "normal":
                    evidence = api.EvidenceResponse.model_validate(evidence_data)
                elif boundary == "model_copy":
                    evidence = api.EvidenceResponse.model_validate(
                        _canonical_evidence_data()
                    ).model_copy(update={"fragment": prohibited})
                else:
                    evidence = api.EvidenceResponse.model_validate(_canonical_evidence_data())
                    object.__setattr__(evidence, "fragment", prohibited)
                with self.subTest(
                    value=prohibited, surface="evidence_fragment", boundary=boundary
                ):
                    self.assertNotEqual(
                        evidence.model_dump(mode="json")["fragment"], prohibited
                    )

                kpi_data = {
                    "metric": prohibited,
                    "before": 1,
                    "after": 2,
                    "delta": 1,
                }
                if boundary == "normal":
                    kpi = api.KpiEffectItem.model_validate(kpi_data)
                elif boundary == "model_copy":
                    kpi = api.KpiEffectItem(
                        metric="eligible_products", before=1, after=2, delta=1
                    ).model_copy(update={"metric": prohibited})
                else:
                    kpi = api.KpiEffectItem(
                        metric="eligible_products", before=1, after=2, delta=1
                    )
                    object.__setattr__(kpi, "metric", prohibited)
                with self.subTest(
                    value=prohibited, surface="kpi_metric", boundary=boundary
                ):
                    self.assertNotEqual(kpi.model_dump(mode="json")["metric"], prohibited)

                audit_data = _canonical_audit_data(
                    summary=prohibited, metric=prohibited
                )
                safe_audit_data = _canonical_audit_data(
                    summary="Scientific decision applied", metric="eligible_products"
                )
                if boundary == "normal":
                    audit = api.AuditTimelineResponse.model_validate(audit_data)
                elif boundary == "model_copy":
                    audit = api.AuditTimelineResponse.model_validate(
                        safe_audit_data
                    ).model_copy(update={"items": audit_data["items"]})
                else:
                    audit = api.AuditTimelineResponse.model_validate(safe_audit_data)
                    object.__setattr__(audit, "items", audit_data["items"])
                dumped_audit = audit.model_dump(mode="json")["items"][0]
                with self.subTest(
                    value=prohibited, surface="audit_summary", boundary=boundary
                ):
                    self.assertNotEqual(dumped_audit["summary"], prohibited)
                with self.subTest(
                    value=prohibited, surface="audit_nested_metric", boundary=boundary
                ):
                    self.assertNotEqual(
                        dumped_audit["payload"]["kpi_effect"][0]["metric"], prohibited
                    )

                case_data = _canonical_case_data()
                override = dict(case_data["overrides"][0])
                override["value"] = prohibited
                evidence_summary = dict(case_data["evidence_summary"])
                evidence_summary["fragment"] = prohibited
                case_updates = {
                    "detected_value": prohibited,
                    "overrides": (override,),
                    "evidence_summary": evidence_summary,
                }
                if boundary == "normal":
                    case = api.ReviewCaseDetail.model_validate(case_data | case_updates)
                elif boundary == "model_copy":
                    case = api.ReviewCaseDetail.model_validate(case_data).model_copy(
                        update=case_updates
                    )
                else:
                    case = api.ReviewCaseDetail.model_validate(case_data)
                    for field, value in case_updates.items():
                        object.__setattr__(case, field, value)
                dumped_case = case.model_dump(mode="json")
                with self.subTest(
                    value=prohibited, surface="case_detected_value", boundary=boundary
                ):
                    self.assertNotEqual(dumped_case["detected_value"], prohibited)
                with self.subTest(
                    value=prohibited, surface="case_nested_override", boundary=boundary
                ):
                    self.assertNotEqual(dumped_case["overrides"][0]["value"], prohibited)
                with self.subTest(
                    value=prohibited,
                    surface="case_nested_evidence_fragment",
                    boundary=boundary,
                ):
                    self.assertNotEqual(
                        dumped_case["evidence_summary"]["fragment"], prohibited
                    )

    def test_final_close_public_urls_are_preserved_only_on_narrative_boundaries(
        self,
    ) -> None:
        api = _api()

        for public_reference in _FINAL_CLOSE_PUBLIC_URLS:
            for boundary in ("normal", "model_copy", "forced_serialization"):
                evidence_data = _canonical_evidence_data() | {
                    "fragment": public_reference
                }
                if boundary == "normal":
                    evidence = api.EvidenceResponse.model_validate(evidence_data)
                elif boundary == "model_copy":
                    evidence = api.EvidenceResponse.model_validate(
                        _canonical_evidence_data()
                    ).model_copy(update={"fragment": public_reference})
                else:
                    evidence = api.EvidenceResponse.model_validate(_canonical_evidence_data())
                    object.__setattr__(evidence, "fragment", public_reference)
                with self.subTest(
                    value=public_reference,
                    surface="evidence_fragment",
                    boundary=boundary,
                ):
                    self.assertEqual(
                        evidence.model_dump(mode="json")["fragment"], public_reference
                    )

                if boundary == "normal":
                    kpi = api.KpiEffectItem(
                        metric=public_reference, before=1, after=2, delta=1
                    )
                elif boundary == "model_copy":
                    kpi = api.KpiEffectItem(
                        metric="eligible_products", before=1, after=2, delta=1
                    ).model_copy(update={"metric": public_reference})
                else:
                    kpi = api.KpiEffectItem(
                        metric="eligible_products", before=1, after=2, delta=1
                    )
                    object.__setattr__(kpi, "metric", public_reference)
                with self.subTest(
                    value=public_reference, surface="kpi_metric", boundary=boundary
                ):
                    self.assertNotEqual(
                        kpi.model_dump(mode="json")["metric"], public_reference
                    )

                audit_data = _canonical_audit_data(
                    summary=public_reference, metric=public_reference
                )
                safe_audit_data = _canonical_audit_data(
                    summary="Scientific decision applied", metric="eligible_products"
                )
                if boundary == "normal":
                    audit = api.AuditTimelineResponse.model_validate(audit_data)
                elif boundary == "model_copy":
                    audit = api.AuditTimelineResponse.model_validate(
                        safe_audit_data
                    ).model_copy(update={"items": audit_data["items"]})
                else:
                    audit = api.AuditTimelineResponse.model_validate(safe_audit_data)
                    object.__setattr__(audit, "items", audit_data["items"])
                dumped_audit = audit.model_dump(mode="json")["items"][0]
                with self.subTest(
                    value=public_reference, surface="audit_summary", boundary=boundary
                ):
                    self.assertEqual(dumped_audit["summary"], public_reference)
                with self.subTest(
                    value=public_reference,
                    surface="audit_nested_metric",
                    boundary=boundary,
                ):
                    self.assertNotEqual(
                        dumped_audit["payload"]["kpi_effect"][0]["metric"],
                        public_reference,
                    )

                case_data = _canonical_case_data()
                override = dict(case_data["overrides"][0])
                override["value"] = public_reference
                evidence_summary = dict(case_data["evidence_summary"])
                evidence_summary["fragment"] = public_reference
                case_updates = {
                    "detected_value": public_reference,
                    "overrides": (override,),
                    "evidence_summary": evidence_summary,
                }
                if boundary == "normal":
                    case = api.ReviewCaseDetail.model_validate(case_data | case_updates)
                elif boundary == "model_copy":
                    case = api.ReviewCaseDetail.model_validate(case_data).model_copy(
                        update=case_updates
                    )
                else:
                    case = api.ReviewCaseDetail.model_validate(case_data)
                    for field, value in case_updates.items():
                        object.__setattr__(case, field, value)
                dumped_case = case.model_dump(mode="json")
                with self.subTest(
                    value=public_reference,
                    surface="case_detected_value",
                    boundary=boundary,
                ):
                    self.assertEqual(dumped_case["detected_value"], public_reference)
                with self.subTest(
                    value=public_reference,
                    surface="case_nested_override",
                    boundary=boundary,
                ):
                    self.assertEqual(
                        dumped_case["overrides"][0]["value"], public_reference
                    )
                with self.subTest(
                    value=public_reference,
                    surface="case_nested_evidence_fragment",
                    boundary=boundary,
                ):
                    self.assertEqual(
                        dumped_case["evidence_summary"]["fragment"], public_reference
                    )

    def test_architectural_final_scientific_prose_is_preserved_on_narrative_boundaries(
        self,
    ) -> None:
        api = _api()

        for safe_text in _ARCHITECTURAL_FINAL_SAFE_NARRATIVE_TEXT:
            for boundary in ("normal", "model_copy", "forced_serialization"):
                evidence_data = _canonical_evidence_data() | {"fragment": safe_text}
                if boundary == "normal":
                    evidence = api.EvidenceResponse.model_validate(evidence_data)
                elif boundary == "model_copy":
                    evidence = api.EvidenceResponse.model_validate(
                        _canonical_evidence_data()
                    ).model_copy(update={"fragment": safe_text})
                else:
                    evidence = api.EvidenceResponse.model_validate(
                        _canonical_evidence_data()
                    )
                    object.__setattr__(evidence, "fragment", safe_text)
                with self.subTest(
                    value=safe_text, surface="evidence_fragment", boundary=boundary
                ):
                    self.assertEqual(
                        evidence.model_dump(mode="json")["fragment"], safe_text
                    )

                audit_data = _canonical_audit_data(
                    summary=safe_text, metric="eligible_products"
                )
                safe_audit_data = _canonical_audit_data(
                    summary="Scientific decision applied", metric="eligible_products"
                )
                if boundary == "normal":
                    audit = api.AuditTimelineResponse.model_validate(audit_data)
                elif boundary == "model_copy":
                    audit = api.AuditTimelineResponse.model_validate(
                        safe_audit_data
                    ).model_copy(update={"items": audit_data["items"]})
                else:
                    audit = api.AuditTimelineResponse.model_validate(safe_audit_data)
                    object.__setattr__(audit, "items", audit_data["items"])
                with self.subTest(
                    value=safe_text, surface="audit_summary", boundary=boundary
                ):
                    self.assertEqual(
                        audit.model_dump(mode="json")["items"][0]["summary"], safe_text
                    )

                case_data = _canonical_case_data()
                override = dict(case_data["overrides"][0])
                override["value"] = safe_text
                evidence_summary = dict(case_data["evidence_summary"])
                evidence_summary["fragment"] = safe_text
                updates = {
                    "detected_value": safe_text,
                    "overrides": (override,),
                    "evidence_summary": evidence_summary,
                }
                if boundary == "normal":
                    case = api.ReviewCaseDetail.model_validate(case_data | updates)
                elif boundary == "model_copy":
                    case = api.ReviewCaseDetail.model_validate(case_data).model_copy(
                        update=updates
                    )
                else:
                    case = api.ReviewCaseDetail.model_validate(case_data)
                    for field, value in updates.items():
                        object.__setattr__(case, field, value)
                dumped_case = case.model_dump(mode="json")
                for surface, actual in (
                    ("case_detected_value", dumped_case["detected_value"]),
                    ("case_nested_override", dumped_case["overrides"][0]["value"]),
                    (
                        "case_nested_evidence_fragment",
                        dumped_case["evidence_summary"]["fragment"],
                    ),
                ):
                    with self.subTest(
                        value=safe_text, surface=surface, boundary=boundary
                    ):
                        self.assertEqual(actual, safe_text)

    def test_public_collection_limits_validate_at_100_and_reject_101(self) -> None:
        api = _api()
        queue_item = _queue_item()
        override = _canonical_case_data()["overrides"][0]
        audit_item = _canonical_audit_item(
            summary="Scientific decision applied", metric="eligible_products"
        )
        kpi_item = {"metric": "eligible_products", "before": 1, "after": 2, "delta": 1}
        factories = (
            (
                api.ReviewQueueResponse,
                "items",
                {
                    "items": (queue_item,),
                    "total": 1,
                    "page": 1,
                    "page_size": 25,
                    "facets": {"statuses": {"pending": 1}},
                    "correlation_id": UUID_A,
                },
                queue_item,
            ),
            (
                api.AuditTimelineResponse,
                "items",
                _canonical_audit_data(
                    summary="Scientific decision applied", metric="eligible_products"
                ),
                audit_item,
            ),
            (
                api.ReviewCaseDetail,
                "overrides",
                _canonical_case_data(),
                override,
            ),
            (
                api.ReviewCaseDetail,
                "effective_memberships",
                _canonical_case_data(),
                "public-membership",
            ),
            (
                api.KpiEffect,
                "affected",
                {"affected": (kpi_item,)},
                kpi_item,
            ),
        )

        for model_type, field, base, item in factories:
            hundred = tuple(item for _ in range(100))
            hundred_one = tuple(item for _ in range(101))
            with self.subTest(
                model=model_type.__name__, field=field, boundary="normal_100"
            ):
                accepted = model_type.model_validate(base | {field: hundred})
                self.assertEqual(len(accepted.model_dump(mode="json")[field]), 100)
            with self.subTest(
                model=model_type.__name__, field=field, boundary="normal_101"
            ), self.assertRaises(ValidationError):
                model_type.model_validate(base | {field: hundred_one})
            with self.subTest(
                model=model_type.__name__, field=field, boundary="model_copy_101"
            ), self.assertRaises(ValidationError):
                model_type.model_validate(base).model_copy(
                    update={field: hundred_one}
                )
            with self.subTest(
                model=model_type.__name__, field=field, boundary="forced_101"
            ):
                forced = model_type.model_validate(base)
                object.__setattr__(forced, field, hundred_one)
                dumped = self._dump_without_exception(
                    forced,
                    context=f"forced cardinality {model_type.__name__}.{field}",
                )
                self.assertEqual(
                    len(dumped[field]),
                    0,
                    "forced oversized collections must be rejected, not truncated",
                )

            schema = model_type.model_json_schema()["properties"][field]
            with self.subTest(
                model=model_type.__name__, field=field, boundary="json_schema"
            ):
                self.assertEqual(schema.get("maxItems"), 100)

    def test_maximal_audit_page_sanitization_stays_within_engineering_budget(
        self,
    ) -> None:
        api = _api()
        long_safe_summary = "A" * 4000
        audit_item = _canonical_audit_item(
            summary=long_safe_summary, metric="eligible_products"
        )
        data = {
            "items": tuple(audit_item for _ in range(100)),
            "page": 1,
            "page_size": 100,
            "total": 100,
            "correlation_id": UUID_A,
        }
        started = perf_counter()
        model = api.AuditTimelineResponse.model_validate(data)
        dumped = model.model_dump(mode="json")
        elapsed = perf_counter() - started
        self.assertEqual(len(dumped["items"]), 100)
        self.assertLess(
            elapsed,
            15.0,
            f"100-item maximal audit page took {elapsed:.3f}s",
        )


def load_tests(loader, standard_tests, pattern):
    if importlib.util.find_spec(API_MODULE) is None:
        return loader.loadTestsFromTestCase(_PreTask2ContractBehaviorProofs)
    return loader.loadTestsFromTestCase(HumanReviewB2B2ContractTests)


if __name__ == "__main__":
    unittest.main()
