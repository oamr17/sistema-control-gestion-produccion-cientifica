# Prototype Handoff Stabilization Design

**Goal:** Leave the scientific-production prototype reproducible, safe for its academic demo role, and understandable for future thesis teams without changing mobile behavior or removing demo accounts. Validation cases must be understandable before opening them, and approved human identity decisions must produce one coherent effective identity across Participants, Scientific Production, and Projects.

## Scope and constraints

- Preserve the overall existing desktop layout, navigation, and visual language.
- Targeted desktop UX changes explicitly defined by this design, including human-readable validation cases, are authorized.
- Mobile behavior remains explicitly out of scope and must not be redesigned.
- Preserve demo users, demo credentials, seed data, Docker Compose local usage, and the prototype workflow.
- Do not introduce external infrastructure such as Redis, Celery, or a managed queue.
- Keep the existing public API response shapes and the Dropbox/n8n → import → review → KPI flow. The scoped-authorization amendment may change internal authorization behavior and add a demo-only evidence source behind the existing authenticated evidence endpoint; it must not expose a storage URL or add client-supplied scope.
- Migrations 0001–0021 remain byte-for-byte immutable and migration 0021 retains SHA-256 `54704858458CE6F27B12BF8B2BE386B91C3A32F27D1D210CEC50DAD16E423B2A`. Migration 0022 does not exist at design time and is not created by this amendment. A separately authorized implementation may add exactly one forward migration 0022 and the corresponding SQLAlchemy fields/constraints defined in §6; no other business-schema change or real scientific-data rewrite is authorized.
- B2B.3 is not started by this stabilization cycle.
- Do not write tests against a real PostgreSQL database.
- Do not make a general server-side pagination migration in this cycle.
- Mobile behavior is out of scope: do not redesign responsive layouts, add mobile navigation, or make mobile-specific visual changes.
- Treat the current uncommitted worktree changes as user-owned and do not overwrite, revert, or commit them.

## Target outcome

The repository must have one documented prototype mode, a deterministic local setup, runnable backend and frontend test commands, no source-writing test fixtures, no JWT passed in URLs, session-scoped client cache behavior, human-readable validation cases, cross-module effective identity consistency, persisted fail-closed Human Review scope, and a concise continuity guide.

## Design

### 1. Explicit prototype mode

The backend configuration will expose an explicit opt-in `DEMO_MODE` setting. Its absence or any value other than `true` must not silently enable demo-only destructive actions. The academic environment documents and sets `DEMO_MODE=true`; demo seed users and data remain available only through that explicit mode. Demo-only destructive maintenance actions require both their exact pre-existing authorization policy and `DEMO_MODE=true`; the demo gate never replaces or broadens that policy.

The explicit prototype bootstrap establishes a **PROTOTYPE VERIFIED SCHEMA BASELINE** for a brand-new academic-prototype database. It never replays the historical migration chain. It seeds the preserved demo users and dataset only when `DEMO_MODE=true`; missing or false demo mode performs no demo-data DML. Invoking application serving alone never triggers schema creation, baseline registration, migration replay, ACL configuration, or seed behavior.

### 2. Reproducible environment and database startup

The repository exposes two operational paths: **prototype bootstrap** and **application serving**. The prototype bootstrap is an explicit, deliberate command restricted to a brand-new academic-prototype database. Application serving starts FastAPI against an already initialized runtime database and never calls `Base.metadata.create_all()`, replays migrations, creates or records a baseline, configures ACL, or seeds data as a startup side effect.

The authorized bootstrap term is **PROTOTYPE VERIFIED SCHEMA BASELINE**. It is not migration replay, a generic stamp, an upgrade mechanism, a repair path, a migration rebaseline file, or production migration architecture. It exists only because audited `Base.metadata` can create the approved prototype tables while historical replay on top of that state collides at migration 0017 and would collide again at 0018–0020. No migration 0000 is added, migrations 0001–0021 remain byte-for-byte unchanged, and `MIGRATIONS` is not weakened. At design time the approved manifest ends at 0021. After a separately authorized implementation creates and approves migration 0022, the fresh baseline must use the amended metadata and append only 0022 to the executable ID/hash manifest; it still never replays 0001–0022.

The baseline has this fixed order:

1. Prove the target is a fresh prototype database using the migration-owner connection and the configured, distinct runtime role.
2. Call `Base.metadata.create_all()` through the migration-owner connection.
3. Install only the closed required runtime-object manifest below.
4. Validate the approved current-runtime fingerprint before registration and verify the closed ordered migration ID/SHA-256 manifest.
5. Create the existing migration-control table through the runner's current control-table behavior, apply and validate the approved least-privilege ACL, and only then record the exact approved executable manifest as **PROTOTYPE VERIFIED SCHEMA BASELINE** in one transaction: 0001–0021 before the scoped-authorization implementation, and 0001–0022 after 0022 is separately authorized, created, hashed, and approved.
6. Revalidate the final fingerprint, migration-control state, and runtime ACL.
7. Only when every preceding check passes, run the preserved demo seed through the application/runtime role if and only if `DEMO_MODE=true`.

#### Fresh database proof

The precondition runs before `create_all` and refuses the baseline on any unexpected user object or migration state. At minimum it proves: no application tables or sequences; no user views or materialized views; no application functions or triggers; no `schema_migrations`; no scientific or demo rows; no unexpected schema or extension; and correct separation between the migration/bootstrap owner and runtime application role. The closed allow-list permits only the configured application schema plus PostgreSQL system schemas (`pg_catalog`, `pg_toast`, `information_schema`) and the built-in `plpgsql` extension. Any additional user schema, extension, object, ownership ambiguity, role equality, or runtime ownership causes `BASELINE REFUSED / FAILED` before mutation.

#### Closed required runtime-object manifest

`Base.metadata.create_all()` does not produce the entire approved runtime state. The bootstrap may install only these audited missing objects and the existing approved ACL model:

- partial unique index `uq_import_jobs_current_document`, with the semantic definition from migration 0014: `import_jobs(document_key) WHERE is_current = TRUE AND document_key IS NOT NULL`;
- functions `b2b_reject_career_capability()` and `b2b_reject_career_role_with_capability()` from migration 0017;
- function `b2b_reject_append_only_mutation()` from migration 0018;
- triggers `trg_user_b2b_capabilities_reject_career` on `user_b2b_capabilities` and `trg_users_reject_career_with_b2b_capability` on `users` from migration 0017;
- trigger `trg_review_decisions_append_only` on `review_decisions` from migration 0018;
- trigger `trg_audit_events_append_only` on `audit_events` from migration 0020;
- final least-privilege ACL already defined by `configure_human_review_privileges` and its tests.

No other historical DDL, data backfill, validator relaxation, `IF NOT EXISTS` patch, or inferred migration object is permitted. Each installed object's normalized PostgreSQL definition, function signature/body, trigger event/table/function, and index predicate must match its cited immutable repository source. If an exact definition cannot be established, the bootstrap stops before registration.

#### Approved current-runtime fingerprint

“Strict fingerprint” means strict against the approved current prototype runtime state, not byte-for-byte equality with the historical PostgreSQL catalog. The validator compares semantic catalog facts for expected tables, columns, PostgreSQL types, nullability, required current-runtime defaults, PK semantics, FK semantics, required unique constraints, checks, required ordinary/partial/functional indexes, the audited functions and their bodies/signatures, the audited triggers, the final 0021 audit-event check, expected schemas/extensions, migration-control state, ownership, and runtime privileges. Known accepted catalog-name differences are encoded explicitly; every unapproved difference refuses the baseline.

The fingerprint has two explicit phases. The pre-registration phase requires `schema_migrations` to be absent and validates schema semantics, manifest objects, ownership separation, and absence of unexpected grants; it does not pretend the not-yet-applied final runtime ACL already exists. The final phase requires the exact approved ACL plus the current runner control-table shape and exactly the approved version identifiers: 21 before the scoped-authorization implementation and 22 after approved migration 0022 exists. It never treats missing hashes in that table as a defect because hashes are verified from the repository before insertion.

#### Known audited deltas

The verified baseline deliberately does not claim historical-catalog equality. These finite deltas require evidence before acceptance:

- **Scientific production author FK:** migration 0009 created `ScientificProductionAuthor → ScientificProduction` with `ON DELETE CASCADE`; current metadata produces `NO ACTION`. A focused application-flow test and call-site inspection must prove supported prototype deletion paths do not rely on database-level cascade. If they do, stop; do not silently alter the FK.
- **Historical server defaults:** migrations 0002–0014 contain server-side defaults not all represented by current metadata. Mapper inspection and supported-flow insert tests must prove current ORM/application writes provide every value required by the approved prototype runtime. If a supported path depends on a missing server default, stop for design review rather than adding it.
- **PK catalog names:** seven current human-review PK constraint names differ from their historical migration names. This is accepted only when no runtime SQL, ACL validator, migration-control logic, or operational script references the historical names and PK/FK semantics are equivalent. The baseline does not rename PKs to satisfy historical validators.

#### Ordered migration hash manifest

Before any baseline record is written, the repository must contain exactly this ordered executable manifest:

| Migration ID | SHA-256 |
| --- | --- |
| `20260628_0001_add_import_jobs_batch_id` | `5D9F432F4199CCFCFBAC32DD8FA884DB2BD78CE7A77A1B9637F3888C52C9E218` |
| `20260628_0002_import_batches_and_job_state` | `5A1252F6327FB5EF7AFE4E67E91D3A737725310C253233F593E6431B23BEE19F` |
| `20260628_0003_fix_import_batches_sequence` | `0FFB182B1305E88672EB49BDF1A33CDA7C5AC3A39EDEDC5382E8014DDBD74E1E` |
| `20260628_0004_normalized_dashboard_status_fields` | `435493F56887CAA9E65DE5276AE309E17EC9CFEE11C920E81AA37EB2D21DF036` |
| `20260628_0005_import_job_diagnostics` | `BAAF6938D5CC9F40508986A3A211E3440BB8F308D31A5F0AAEBDA45D86BA4FFB` |
| `20260628_0006_import_job_error_details` | `73D33DA71D39C6FB07D0FEF2A875410AA79FA29D0019501CE3E4792E387084AC` |
| `20260628_0007_external_researchers_and_review_items` | `28ADA8AA829DA5DFF56A48280454DFA1F504D3B4C5FCCC1E53F92960E285E33B` |
| `20260628_0008_normalization_traceability` | `80A871F77D391AC91F01E900E4394BD6101309DACF31346B55C225B5EFCAD661` |
| `20260629_0009_scientific_production_authors` | `49A60060465244CF250FE3679385C499C30A2FC302A08228E4E3EFB667712074` |
| `20260629_0010_person_roles` | `8D4E98EC8EE47F0ECC56FC9BA713A39EE1358828C25601A7F1623300BA3B6C59` |
| `20260629_0011_research_entities_and_pending_products` | `049FD67A06F2874E9A155AB5B498481A9482A044E5B744CB724922B2DDC00D77` |
| `20260629_0012_author_traceability` | `BC0B682636FB4E362F76E5B26570D6ED3745C9E0951344AAC00FA69E4F2A7DDA` |
| `20260710_0013_teacher_person_role_validation` | `06A6E1656DB4D141E05F10225D709CDE7BC7B2F875E5AB52B8E3FB86F105B64A` |
| `20260711_0014_dropbox_document_versioning` | `DB1516A8CB00FB86095773AB163EBE3CFCD75668E4D764FA5F07367E84FC0F66` |
| `20260711_0015_dropbox_revision_uniqueness` | `E40CC1F9E1DB090240EE15B2AD333814AE0814DE886554C05B9C10E7C959B947` |
| `20260712_0016_canonical_identity_fields` | `C5DE79A50D1FDD696020A30903608A7F1DDA53AA90E2FD7713592733F7184F44` |
| `20260713_0017_b2b_capabilities` | `D7C495F0271FF989ABC25B3B446781441CE68D72069C0C5AC8CECE91847608B6` |
| `20260713_0018_human_review_core` | `F5DEEC37ECC277F0CDB6BA4E413A6F4C0912AEB901B9CE56D80CD9DEECC9100E` |
| `20260713_0019_human_review_projection` | `6741935A9326001B317517928EB7281D06DE5463A8E035283B66BAD1676FCD1C` |
| `20260713_0020_human_review_audit` | `6D6DAE32AFE775E81EE6D5ACA1172304676EB111AD2C756F6B50FDFB9CA41D4B` |
| `20260718_0021_human_review_scientific_decision_audit` | `54704858458CE6F27B12BF8B2BE386B91C3A32F27D1D210CEC50DAD16E423B2A` |

The executable list must also equal `MIGRATIONS` in ID and order. `20260629_0002_add_scientific_production_authors.py` is an excluded orphan historical file because it is not in `MIGRATIONS`; it is never inferred, hashed into the executable manifest, executed, or recorded. Migration 0022 remains absent until separately authorized implementation. When it exists, its final path, ID, and SHA-256 must be explicitly approved and appended as the sole 22nd entry; this design intentionally contains no placeholder hash.

#### Migration control, ACL, and failure contract

The current runner's `schema_migrations` shape is authoritative: `version VARCHAR(120) PRIMARY KEY` plus `applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP`. The baseline reuses the runner's existing control-table creation behavior, validates file hashes separately, and inserts only supported version fields; it does not alter the table to store hashes. Baseline registration happens only after fresh proof, `create_all`, runtime-object installation, pre-registration fingerprint, all migration hashes, and ACL validation pass. The approved records—21 before the scoped-authorization implementation and 22 afterward—are inserted atomically and are never produced by calling `run_migrations` during fresh bootstrap.

The migration/bootstrap owner retains schema/bootstrap capability. The runtime application role receives only the existing approved application read/DML/execute privileges and must not own objects, have `CREATE` on the protected schema, hold grant option or migration privileges, own protected functions, or escalate roles. `schema_migrations` remains owner-only. After registration, the final fingerprint and ACL verifier must pass again before demo DML.

Any failure after `create_all` yields `BASELINE REFUSED / FAILED`: do not partially repair, manually mark migrations, continue to seed, or serve the application. The operator recreates the disposable fresh database and retries only after correcting code or configuration. Re-execution against the now-initialized database is a clear read-only refusal at the fresh-database gate; it is not an in-place idempotent repair and never reaches seed DML. Scientific or user data is never repaired in place.

An `.env.example` file is required, versioned, and contains every variable needed to start the prototype without real secrets. It explicitly keeps `APP_DATABASE_URL` distinct from `MIGRATION_DATABASE_URL`; runtime and migration ownership are not merged. `.env` remains ignored by Git. README plus `.env.example` must be sufficient for a fresh disposable setup, including documented application DEMO credentials without presenting them as infrastructure secrets.

The documented fresh setup sequence is explicit: create `.env` from `.env.example`, start local infrastructure, invoke the prototype bootstrap, start or use the backend/frontend, and log in with the demo accounts. A recipient does not recover secrets from old containers, edit migrations, know repository history, or discover undocumented internal commands.

The final repository scan verifies that no real PostgreSQL or infrastructure credentials, JWT secrets, MinIO credentials, Dropbox/API tokens, or connection strings are committed. Application user credentials explicitly documented as DEMO are an accepted demo requirement and are not classified as leaked infrastructure secrets.

### 3. Authentication, files, and cache

The client continues to use the current prototype authentication model, including demo accounts. On logout or session-token change, all client-side cached data will be cleared; persisted data will be namespaced to the active session or removed.

Authenticated PDF links will not include the bearer token in a query string. Existing blob-based authenticated download flows will be used consistently from the UI.

Evidence delivery keeps the existing architecture while enforcing allowed MIME types/extensions, a configurable size limit, sanitized filenames, no bearer token in URLs, no MinIO/internal storage paths in public responses, and no `localhost` hard-coded in URLs delivered to clients.

Complete tracebacks remain server-side technical logs and are never serialized to the frontend. Public API errors and UI messages contain a sanitized, user-safe error envelope only.

### 4. Cross-module effective identity consistency

Source scientific data remains immutable for traceability. A human decision and its existing effective projection define the functional representation. Readers and UI therefore show one logical effective identity, not both source and corrected identities.

The behavior must hold end-to-end in Participants, Scientific Production, and Projects for: name correction, identity correction, linking to an existing identity, an explicit decision to keep identities separate, reversal, and one person associated with multiple records. KPI counts must not double-count the same effective identity.

Identity grouping must use only persisted existing relationships and identifiers. It must not deduplicate by display name or fuzzy matching, and it must not destructively modify the scientific source record.

### 5. Human-readable validation cases

Before opening a case, the main validation queue, related cases in a guided session, and every review selector must show the review type, the primary record data, the source document, page or section when present, review reason, and status. UUIDs remain available only as advanced technical/public reference information.

For example, an author identity case displays the author name, source report, page/section, and "Requiere validar identidad"; a scientific-production case displays the product title, source report, page, and "Requiere validar información".

Implementation first uses existing case and evidence metadata. The API is not widened unless one strictly necessary display datum is absent; in that event, implementation stops and proposes the smallest compatible contract change.

### 6. Persisted, scoped Human Review authorization

#### Options evaluated and decision

| Option | Strengths | Costs and risks | Decision |
| --- | --- | --- | --- |
| A. Direct scope columns on `users` and `review_items` | Few joins; direct FK integrity; one uniform queue/detail/command predicate; explicit inter-career semantics; simple tests and indexes. | Duplicates the resolved scope snapshot on each review item and requires one forward migration. | **Selected.** The duplication is intentional authorization state captured when the case is created, not duplicated display data. |
| B. Separate `user_scope` and `review_item_scope` tables | Supports multiple future scopes without changing the core rows again. | Adds two tables, cardinality rules, joins, lifecycle management, and ambiguity the current one-faculty/one-career-per-manager business rule does not need. | Rejected as overengineering for this prototype. Revisit only if one user or case must hold multiple independent scopes. |
| C. Dynamic derivation from source relationships | No schema change and no stored scope snapshot. | Already proven incomplete for research entities, external researchers, some person/author/product cases, and inter-career projects; authorization would vary by case type and remain difficult to filter safely. | Rejected because it cannot guarantee the authoritative rule. |

Option A is the smallest model that is deterministic, server-side, fail-closed, independent of display names, and uniform across all protected surfaces.

#### Exact persisted relationships

The future model adds these nullable columns; nullable is required so existing unresolved rows can remain fail-closed without guessed backfill:

- `users.faculty_id INTEGER NULL` → `faculties.id`, `ON DELETE RESTRICT`, indexed. A `FACULTY_ADMIN` is authorized only when this persisted value is present. Existing `CAREER_MANAGER.career_id` remains authoritative and its faculty is derived only through `careers.faculty_id`; `faculty_id` is not duplicated for career managers.
- `review_items.scope_faculty_id INTEGER NULL` → `faculties.id`, `ON DELETE RESTRICT`.
- `review_items.scope_career_id INTEGER NULL` plus `scope_faculty_id` → composite FK `careers(id, faculty_id)`, `ON DELETE RESTRICT`. `careers` therefore gains the supporting unique constraint `(id, faculty_id)`; this is redundant for entity identity but required for relational enforcement that a case's career belongs to its stored faculty.
- `review_items.scope_resolution_reason VARCHAR(80) NULL`, internal only. It stores a finite safe reason code when both scope IDs are null; it is not a user-facing status and does not add a workflow enum.

Constraints require: a career scope always has a faculty scope; a row with null faculty and career has a nonblank resolution reason; a row with a resolved faculty has no unresolved reason. Scope indexes support faculty and career queue predicates together with case status and stable priority ordering. New user-role rows are validated by the application boundary: `FACULTY_ADMIN` requires `faculty_id`; `CAREER_MANAGER` requires `career_id`. The database columns stay nullable because no trustworthy assignment exists for every historical user.

At case creation, one server-owned scope resolver follows persisted FKs from the target and computes a set of career IDs. Exactly one career produces faculty + career scope. Multiple careers in one faculty produce faculty scope with null career. A direct persisted faculty relationship may produce faculty-only scope. No career/faculty, a missing target, or careers spanning multiple faculties produces both scope IDs null plus an internal reason code. Display names, `academic_unit`, `career_name`, raw text, fuzzy matching, email, and client-provided IDs are never inputs. The case remains persisted for traceability but is invisible and non-actionable to scoped managers until a separately designed administrative resolution exists.

#### Authorization matrix and enforcement

| Actor | Human Review actions | Case scope |
| --- | --- | --- |
| `FACULTY_ADMIN` with persisted `faculty_id` | view queue/detail/related/evidence, apply/correct/link/discard, conflict/CAS retry, and reverse | `case.scope_faculty_id == user.faculty_id`; no access when case faculty is null or different |
| `CAREER_MANAGER` with persisted `career_id` | same review/correction/reversal actions | `case.scope_career_id IS NOT NULL AND case.scope_career_id == user.career_id`; faculty-only, unresolved, and other-career cases denied |
| persisted `RESEARCH_MANAGER` capability | preserves its current scientific/audit/propose action set | may add action types but never widens the owning user's faculty/career scope |
| persisted `SYSTEM_ADMIN` capability | preserves its current technical/audit action set and remains unable to apply/reverse scientific decisions | may add action types but never widens the owning user's faculty/career scope |
| inactive, unassigned, or scope-incomplete user | none | deny |

The role supplies the new baseline Human Review action set; a capability can add only its existing action types. No capability overrides geographic scope. `CAREER_MANAGER` continues to receive no active `user_b2b_capabilities` row, so the immutable 0017 guards remain valid. The existing `/human-review/me` response shape can return role-derived actions without adding a public field; frontend gates must consume actions instead of requiring the capability label to be `RESEARCH_MANAGER` or `SYSTEM_ADMIN`.

One centralized scope policy supplies both an SQL predicate and an item assertion. Queue rows, facets, filters, effective-data revision, detail, related cases, audit, evidence/PDF metadata and stream, previews, apply/correct/link/discard, reversal, conflict/CAS, and projection-changing actions all use it. Queue/list surfaces silently exclude out-of-scope rows. A client-supplied filter is intersected with scope; an explicit other-career filter from a career manager is rejected. Direct access or mutation of a known out-of-scope UUID returns the existing sanitized HTTP 403 convention. Authorization runs before evidence lookup, preview calculation, version/CAS disclosure, or mutation and is rechecked on the row locked for a command.

#### Inter-career and unresolved policy

An inter-career project whose persisted participating careers all belong to one faculty receives that faculty and null career. The assigned faculty administrator may operate it; every career manager is denied. Access by multiple career managers is not inferred and would require a separate future business rule.

Unresolved scope uses null faculty + null career and a safe internal reason such as `unresolved_no_persisted_scope`, `unresolved_cross_faculty`, or `unresolved_missing_target`. It is not a new case status. It is excluded from scoped queues/facets and denied on direct read, evidence, decisions, reversal, and conflict/CAS. Migration and server diagnostics record counts by reason without exposing internal IDs or personal data publicly.

#### Migration and backfill strategy

The separately authorized forward migration 0022 will add the columns, FKs, supporting unique/check constraints, and indexes above. It first adds nullable columns, then backfills in bounded transactions using only existing persisted target FKs, then adds/validates constraints and indexes. Existing `CAREER_MANAGER.career_id` values are preserved. Historical faculty administrators receive `faculty_id` only from a separately approved exact user-ID/faculty-ID assignment; none is inferred from email or topology. Until assigned, they fail closed.

For existing review items, the same scope resolver used by new case creation is run from a frozen migration implementation. One unambiguous career sets both scope IDs; multiple same-faculty careers set faculty only; ambiguous/missing/cross-faculty targets remain null with a reason. No source scientific row, canonical identity, decision, or audit event is rewritten. The migration emits a non-secret count/hash report and stops on FK inconsistency. It never guesses from names, chooses the first career, or performs fuzzy matching.

Migration 0022 is a normal forward upgrade only for an already initialized 0021 database and is run by the migration owner after backup/preflight. Fresh prototype bootstrap does not replay it: amended `Base.metadata` creates the target schema, the fingerprint includes the new columns/constraints/indexes, its approved hash becomes manifest entry 22, and the baseline atomically records 0001–0022. Downgrade requires matching application rollback and removes indexes/FKs/constraints before the new columns; it cannot reconstruct the superseded authorization behavior and therefore is not an online operational reversal.

#### Additive demo cases and evidence

`DEMO_MODE=true` keeps the two existing accounts, credentials, roles, and scientific records. The faculty demo account receives the seeded faculty's persisted ID; the career account keeps its existing ADM `career_id`. No third user is created. The minimum fixture is two additive cases:

- **Case A — ADM:** an identity-correction case backed by additive `PersonRole`/authorship relationships tied through existing ADM teacher, production, and project records. It supports readable queue context, correction, one effective identity across Participants/Production/Projects, KPI non-duplication, and reversal. Both demo users may read and mutate it.
- **Case B — another career in the same faculty:** a readable case anchored to an existing non-ADM teacher/product relationship. The faculty administrator may read/mutate/reverse it; the ADM manager cannot list it and receives 403 for direct detail, mutation, reversal, or evidence.

No Case C is required: the two cases prove both scope levels, while inter-career behavior is covered by backend tests using the existing multi-career project topology. Seed IDs, stable target keys, review UUIDs, source revisions, and expected values are deterministic. `DEMO_MODE=false` creates no users, scientific seed, review items, evidence metadata, or demo evidence access.

The repository will contain one small versionable `backend/data/demo/human_review_scope_demo.pdf` with a fixed approved SHA-256. It contains only clearly marked “DEMO / SYNTHETIC” content and the two deterministic sections/pages used by Cases A and B; it contains no real personal/confidential data and is not the thesis PDF or either missing parser fixture. A demo `ImportJob`/OCR-trace chain points to an internal `DEMO_PDF` source only when `DEMO_MODE=true`. The existing authenticated `/human-review/cases/{id}/evidence` endpoint remains the sole public path. Under demo mode, a narrowly allow-listed server adapter reads the exact repository asset and validates configured size, `%PDF` content, page count, and fixed hash before streaming; normal Dropbox behavior is unchanged. The asset is never served statically, its internal path is never serialized, and JWT, MIME/extension, filename sanitation, cache, and scope checks remain unchanged.

### 7. Tests and repeatability

Backend development requirements will include the test runner and a documented command that can execute the test suite. Frontend tests will be divided by environment requirement: unit/contract tests do not require a live API, while API integration tests declare and verify their prerequisite.

Browser E2E fixtures will be written under the OS temporary directory or generated through a test-only route outside the application source tree. Cleanup will be unconditional even after a failure.

Bootstrap verification uses a fresh disposable PostgreSQL 16 instance, never a scientific or user-owned database. It covers: fresh database plus verified baseline and `DEMO_MODE=true`; normal serving restart after initialization; read-only baseline refusal on re-execution; and a separate fresh verified baseline with `DEMO_MODE=false`. Before scoped-authorization implementation the historical evidence remains 21/21. After separately approved 0022 implementation, tests require the amended runtime fingerprint and exactly 22 baseline records, scoped demo users/cases/evidence only with explicit demo mode, no demo DML when false, refusal before seed mutation, and application serving without schema creation, migration replay, baseline, ACL, or seed side effects.

### 8. Import module boundaries

The import workflow will retain behavior but gain clearer boundaries. HTTP endpoint functions will remain thin, while operational helpers are separated by responsibility:

- upload/request validation and authorization;
- batch lifecycle and asynchronous orchestration;
- read-only diagnostics and audit projections;
- PDF/OCR parsing and normalization.

No proactive refactor of `imports.py` or `import_service.py` is required. Operational helpers may be extracted only when a confirmed stabilization fix touches that responsibility and extraction is the smallest safe way to implement and test it. File size alone is not justification for refactoring. A full worker-queue migration is documented as a future evolution, not implemented.

### 9. Read scalability

General server-side pagination is explicitly deferred to API vNext. This cycle measures current endpoints, documents actual limitations, and changes a specific endpoint only if the existing dataset proves a reproducible user-visible problem. No API contract changes occur solely for hypothetical scale.

### 10. Dependencies and documentation

Frontend dependencies flagged by the audit will be updated for compatible patch/minor releases and changes required by verified Critical/High advisories. A major Next.js upgrade, framework migration, or dependency-driven general refactor requires a stop and explicit approval; if an advisory needs that upgrade, it is documented as future work.

The handoff documentation will cover architecture, demo setup, test commands, import flow, module map, known limitations, and a future-work backlog.

## Verification

- Python syntax and backend tests run from the documented command.
- TypeScript type check, frontend unit/contract tests, and the documented API-dependent test mode run independently.
- The production build completes.
- A logout followed by another login cannot reveal cached information from the preceding user.
- PDF viewing works without a token-bearing URL.
- Demo seed and demo login continue to work in prototype mode.
- No test creates or modifies files in the application source tree.
- Effective identity tests pass: Participants, Scientific Production, Projects, one cross-module identity, reversal, and no KPI double count.
- Human-readable case tests pass: queue, related cases, human label, primary record data, document/context, UUID only as advanced information, and no internal identifiers exposed.
- Scoped authorization tests pass server-side for queue/facets/filters, detail, related, audit, evidence/PDF, previews, apply/correct/link/discard, reversal, conflict/CAS, effective revision, and projection changes. `FACULTY_ADMIN` is restricted to its persisted faculty; `CAREER_MANAGER` is restricted to its persisted career; direct cross-scope reads and mutations return sanitized 403; unresolved scope fails closed.
- Demo scope tests pass with exactly the existing two accounts, minimum Cases A/B, authenticated synthetic PDF, Case A effective-identity/reversal/KPI journey, faculty-wide access, career-only visibility, and no Case B leakage to the ADM manager.
- A fresh disposable PostgreSQL 16 setup succeeds from README plus `.env.example` by running the documented explicit prototype-bootstrap command before application serving.
- Fresh-database proof rejects any unexpected object, migration state, scientific/demo row, owner/runtime-role collapse, schema, or extension before `create_all`.
- Empty-database bootstrap creates the approved metadata tables, installs exactly the audited runtime-object manifest, validates the approved semantic fingerprint and every approved repository hash, applies/verifies the approved ACL, and atomically records the exact manifest as **PROTOTYPE VERIFIED SCHEMA BASELINE** without executing migrations: 21/21 before the amendment implementation and 22/22 afterward.
- The manifest includes `uq_import_jobs_current_document`, both B2B guard functions/triggers, and the append-only function/triggers; their catalog definitions match the cited repository migrations.
- The excluded orphan migration is neither executed nor recorded. Migration 0022 remains absent during design; after separately authorized implementation it must be the sole appended migration with an approved exact hash. `schema_migrations` retains only its current `version`/`applied_at` shape.
- The CASCADE, historical server-default, and PK-name deltas pass their required supported-flow/static/semantic evidence; a required CASCADE/default or name dependency causes STOP.
- Final runtime ACL proves the application role has only approved DML/read/execute access and lacks ownership, protected-schema `CREATE`, grant option, migration capability, protected-function ownership, and role escalation.
- Normal serving against an initialized database performs no `create_all`, migration replay, baseline creation/registration, ACL configuration, or seed side effect.
- Bootstrap re-execution is refused at the fresh-database gate before mutation or seed DML; silent duplicate or corrupted seed data is absent.
- With `DEMO_MODE=false`, a separate fresh bootstrap establishes the verified schema baseline but creates no demo users or demo dataset.
- `npm audit --omit=dev`
- The exact audit result, Critical/High findings, and each compatible resolution are recorded. Major upgrades continue to require STOP and explicit approval.
- Final secret verification finds no real PostgreSQL/infrastructure credentials, JWT secrets, MinIO credentials, Dropbox/API tokens, or connection strings; explicitly documented DEMO application-user credentials are accepted.
- Desktop runtime QA passes with broken images = 0, console errors = 0, hydration errors = 0, and unexpected failed requests = 0; assets used by the login are specifically verified.
- Evidence tests verify allowed MIME/extension, configurable size limit, sanitized filename, no JWT-bearing URL, no MinIO/internal path exposure, and no hard-coded `localhost` URL delivered to a client.
- Public error tests verify that complete tracebacks are never serialized to the frontend.

## Non-goals and future work

- Mobile redesign or responsive navigation changes.
- Removing demo accounts or demo data.
- Converting the prototype into a hardened production deployment.
- Replacing in-process PDF processing with external queue infrastructure.
- A broad frontend redesign.
- General server-side pagination; it is future work for API vNext unless a current reproducible endpoint problem requires a narrow fix.
- Production migration architecture or rebaselining the historical chain. **PROTOTYPE VERIFIED SCHEMA BASELINE** is a fresh academic-prototype bootstrap only; it never applies historical migrations, stamps an existing database, upgrades a database, or repairs data in place. A future production migration architecture requires a separate design.
- Multi-faculty or multi-career assignment per user, shared inter-career access for several career managers, and an administrative UI for resolving null case scope. These require separate business rules.

## Immutable boundaries

- Migration 0021 SHA256 is `54704858458CE6F27B12BF8B2BE386B91C3A32F27D1D210CEC50DAD16E423B2A` and must remain intact.
- Migration 0022 is absent during this design amendment and is not created now. Its future creation and implementation require separate authorization; it may contain only the persisted-scope schema and deterministic backfill defined in §6.
- Migrations 0001–0021 and their validators remain immutable. `MIGRATIONS` remains unchanged during design and may later append only approved 0022; no 0000, rebaseline migration, `IF NOT EXISTS` patch, historical rewrite, or validator weakening is authorized.
- SQLAlchemy models and the business schema remain unchanged during this design amendment. A separately authorized implementation may add only `User.faculty_id`, the `ReviewItem` scope columns/reason, and their exact FK/unique/check/index metadata from §6. The known author-FK, historical-default, and PK-name deltas are tested/encoded, not silently rewritten.
- The baseline refuses non-fresh or drifted databases and never becomes a generic stamping, replay, repair, or upgrade command.
- Application serving remains Uvicorn/FastAPI-only and has no schema, migration, baseline, ACL, or seed side effects.
- B2B.3 remains not started.
- Real scientific data remains unchanged.
- Tests must use disposable/test infrastructure only, never a real PostgreSQL database.
- Existing uncommitted work belongs to the user: no destructive checkout, reset, clean, amend, automatic stash, or commit is allowed. Inventory, hashes, and diffs distinguish prior work from stabilization changes.
- Existing completed stabilization work and user-owned changes must be retained rather than reverted. The scoped-authorization prerequisite is separately authorized for design only; Task 11 remains blocked until its future implementation and fresh verification are separately authorized and pass.

## Delivery declaration

When every verification criterion is demonstrated, the repository is declared:

**PROTOTYPE DELIVERY READY**

It is not declared production ready.
