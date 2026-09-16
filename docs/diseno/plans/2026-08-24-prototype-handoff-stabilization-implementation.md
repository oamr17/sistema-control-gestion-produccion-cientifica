# Prototype Handoff Stabilization Implementation Plan

> Implement each task in sequence, verify its acceptance criteria, and use the checkboxes (`- [ ]`) to track progress.

**Goal:** Leave the scientific-production prototype reproducible and safe to hand off to future thesis teams while preserving its demo workflow, source scientific data, desktop visual language, and current mobile behavior, with deterministic persisted Human Review scope for the two existing manager roles.

**Architecture:** Stabilization follows existing boundaries: FastAPI endpoints and services remain the backend API, SQLAlchemy models and the existing human-review projection remain the source of effective reads, and Next.js remains the desktop client. A prerequisite repair before Task 11 adds direct persisted scope to users/review items, one centralized backend scope policy, and two additive demo cases behind the existing authenticated evidence flow. For a brand-new academic database, an explicit **PROTOTYPE VERIFIED SCHEMA BASELINE** creates approved metadata, installs only the audited runtime-object manifest, validates the approved semantic fingerprint and migration hashes, records verified versions without replay, applies least privilege, and optionally seeds demo data. It remains operationally separate from side-effect-free application serving.

**Tech Stack:** Python 3.12 Docker image; FastAPI 0.115.6; SQLAlchemy 2.0.36; PostgreSQL 16; MinIO; n8n; PyMuPDF; pytesseract; Next.js 14.2.35; React 18.3.1; TypeScript 5.7.2; Tailwind CSS; Node test runner; Playwright; Docker Compose.

**Spec:** `docs/diseno/specs/2026-08-24-prototype-handoff-stabilization-design.md`

## Global Constraints

- Preserve the overall existing desktop layout, navigation, and visual language; only targeted desktop UX in the approved spec is authorized.
- Mobile behavior is out of scope: no responsive redesign, mobile navigation, or mobile-specific visual work.
- Preserve the two demo users, application demo credentials, existing scientific seed, and the Dropbox/n8n → import → review → KPI flow. Add only the scoped Human Review cases/evidence approved by the spec.
- `DEMO_MODE` is opt-in. Its absence must not enable demo-only destructive actions; the academic setup documents `DEMO_MODE=true`.
- Only the explicit fresh-database prototype bootstrap may call `Base.metadata.create_all()`. It never replays historical migrations; after the scoped-authorization prerequisite it may record 0001–0022 as **PROTOTYPE VERIFIED SCHEMA BASELINE** only after fresh proof, closed runtime-object installation, strict amended fingerprint, all 22 repository hashes, and least-privilege ACL validation pass.
- Migration 0021 SHA-256 must remain `54704858458CE6F27B12BF8B2BE386B91C3A32F27D1D210CEC50DAD16E423B2A`. Migration 0022 is absent during design and may be created only under a separate implementation authorization; before any execution its exact final SHA-256 must be approved and appended as the sole new `MIGRATIONS` entry.
- Do not change migrations 0001–0021, their validators, source scientific data, existing demo credentials/roles/scientific rows, or start B2B.3. The only prospective model/schema change is the exact persisted-scope model in the amended spec; no 0000, rebaseline file, `IF NOT EXISTS` repair patch, historical rewrite, or validator weakening is allowed.
- The verified baseline is fresh academic-prototype bootstrap only: not migration replay, generic stamping, upgrade, repair, or production migration architecture. Any non-fresh or unapproved catalog state must be refused before baseline registration or seed DML.
- Do not write tests against a real PostgreSQL database. Use disposable PostgreSQL or the existing isolated SQLite test support when the behavior is supported there.
- Keep public API response shapes unchanged. Role-derived Human Review actions use the existing `/human-review/me` fields, and demo evidence remains behind the existing authenticated case-evidence endpoint.
- Do not redesign authentication, introduce Redis/Celery/a managed queue, introduce general server-side pagination, or proactively refactor `imports.py` or `import_service.py`.
- Compatible patch/minor dependency upgrades and fixes required by verified Critical/High advisories are allowed. Stop before a major Next.js/framework upgrade or dependency-driven broad refactor.
- Treat all current tracked modifications, deletions, and untracked files as USER-OWNED. Do not reset, checkout, clean, stash, amend, commit, or modify Git metadata. Before modifying an overlapping file, record its scoped diff and merge only after confirming the stabilization edit does not overwrite user work.
- No test may create or modify a file under `backend/app`, `frontend/app`, `frontend/components`, `frontend/hooks`, or `frontend/lib`.

---

## Current file map and ownership boundary

| Area | Confirmed files | Responsibility in this plan |
| --- | --- | --- |
| Runtime config and startup | `backend/app/core/config.py`, `backend/app/main.py`, `backend/app/core/migrations.py`, `backend/app/core/prototype_baseline.py`, `backend/scripts/init_db.py`, `backend/scripts/configure_human_review_privileges.py`, `backend/Dockerfile` | Explicit demo mode, verified fresh-only schema baseline, unchanged migration execution semantics, approved ACL, and side-effect-free serving boundary. |
| Persisted Human Review scope | `backend/app/models/entities.py`, `backend/app/models/human_review_core.py`, future `backend/app/migrations/versions/*0022*`, new `backend/app/services/human_review_scope.py`, `backend/app/services/human_review_authorization.py`, `backend/app/services/human_review_queries.py`, `backend/app/services/human_review_commands.py`, `backend/app/api/v1/endpoints/human_review.py` | Direct persisted user/case scope, deterministic resolver/backfill, fail-closed read/write authorization, and immutable 0001–0021 boundary. |
| Local environment | `docker-compose.yml`, `.gitignore`, `README.md` | Reproducible demo setup and documented environment variables. |
| Human review reads | `backend/app/services/human_review_queries.py`, `backend/app/services/human_review_projection.py`, `backend/app/services/validated_read_service.py`, `backend/app/schemas/human_review_api.py` | Effective identity and public, human-readable review projections. |
| Operational readers | `backend/app/services/production_service.py`, `backend/app/services/research_entity_service.py`, `backend/app/services/kpi_service.py`, `backend/app/api/v1/endpoints/participants.py` | Cross-module identity visibility and KPI non-duplication. |
| Validation UI | `frontend/components/human-review/ReviewQueueTable.tsx`, `frontend/components/human-review/RelatedReviewList.tsx`, `frontend/components/human-review/ReviewSessionPage.tsx`, `frontend/hooks/useHumanReview.ts`, `frontend/lib/human-review.ts`, `frontend/lib/human-review-api.ts` | Queue, related cases, and selectors render public human context. |
| Session/client data | `frontend/lib/auth.ts`, `frontend/lib/api.ts`, `frontend/lib/data-cache.tsx`, `frontend/lib/effective-data-refresh.ts`, `frontend/lib/effective-data-revision.ts` | Session-isolated cache and authenticated client requests. |
| Evidence/downloads | `backend/app/services/evidence_service.py`, `backend/app/api/v1/endpoints/evidence.py`, `backend/app/api/v1/endpoints/imports.py`, `backend/app/schemas/imports.py`, `frontend/app/importaciones/page.tsx`, `frontend/components/human-review/EvidenceWorkspace.tsx` | Safe public evidence responses and authenticated blob downloads. |
| Scoped demo evidence | `backend/data/demo/human_review_scope_demo.pdf`, `backend/scripts/init_db.py`, `backend/app/services/human_review_queries.py`, `backend/app/api/v1/endpoints/human_review.py` | Deterministic synthetic PDF and two additive demo cases available only through the authorized Human Review evidence flow under `DEMO_MODE=true`. |
| Tests | `backend/tests/support/sqlite.py`, `backend/tests/test_human_review_b2b2_readers.py`, `backend/tests/test_human_review_b2b2_kpi.py`, `backend/tests/test_human_review_b2b2_reversal.py`, `backend/tests/test_human_review_b2b2_queries.py`, `backend/tests/test_human_review_b2b2_evidence.py`, `backend/tests/test_init_db_engine_separation.py`, `frontend/tests/human-review-api.test.mjs`, `frontend/tests/human-review-queue.test.mjs`, `frontend/tests/human-review-detail.test.mjs`, `frontend/tests/human-review-e2e.test.mjs`, `frontend/tests/b2a-contracts.test.mjs`, `frontend/tests/login-page.test.mjs` | Existing focused behavior and end-to-end regression coverage. |

Files currently modified or deleted include the human-review backend/frontend files listed above, `frontend/public/logo-vertical.png`, several historical parser files, and several backend tests. They are user-owned. The executor must inventory the scoped diff before any overlapping modification and stop to report a non-mergeable conflict.

**Amendment sequencing:** Tasks 1–10 below retain their original 21/21 and “0022 absent” wording as completed historical checkpoint criteria. They do not authorize deleting or rejecting the future scoped-authorization migration. The only prospective implementation sequence is: separately authorize and complete the unnumbered prerequisite after Task 10 → approve migration 0022's final hash → rerun the amended Task 11 against 22/22 → leave Task 12 unstarted until separately authorized. The numbered delivery task count remains exactly 12.

### Task 1: Preflight and reproducible test baseline

**Files:**
- Create only if runner discovery proves a development dependency absent from `backend/requirements.txt` is required: `backend/requirements-dev.txt`
- Create: `reports/prototype-handoff/preflight-baseline.json` (generated checkpoint; repository-ignore status must be verified before writing)
- Modify: `backend/requirements.txt` only if the existing production runtime lacks a package required to execute its declared runtime behavior; otherwise leave unchanged.
- Modify: `frontend/package.json` only if a separate test script is required to keep API-dependent tests out of the default unit/contract runner.
- Test: `backend/tests/test_init_db_engine_separation.py`, `frontend/tests/b2a-contracts.test.mjs`, `frontend/tests/human-review-api.test.mjs`

**Consumes:** The approved design; current Git worktree; migration 0021; existing backend requirements; frontend `test`, `build`, and `lint` scripts.

**Produces:** A recorded, non-destructive baseline using the repository's actual backend runner; a development dependency declaration only when discovery proves one is needed; separate documented frontend test modes; proof that 0021 is intact and 0022 remains absent.

- [ ] **Step 1: Capture the worktree inventory without modifying it.**

  Run:

  ```powershell
  git status --short
  git diff --name-status
  git diff --check
  Get-FileHash -Algorithm SHA256 backend/app/migrations/versions/20260718_0021_human_review_scientific_decision_audit.py
  Get-ChildItem backend/app/migrations/versions -Filter '*0022*'
  ```

  Expected: record the current modifications/deletions as user-owned; 0021 hash equals the Global Constraint; no 0022 file exists. Do not repair, stage, or remove any listed item.

- [ ] **Step 2: Discover the actual backend runner before changing dependencies.**

  Inspect `backend/tests`, test imports, `unittest.TestCase`, pytest fixtures/markers, documented commands, and scripts before selecting any command. For example:

  ```powershell
  rg -n "unittest|TestCase|pytest|fixture|mark\." backend/tests README.md backend/requirements.txt backend/scripts
  rg -n "python -m unittest|unittest discover|pytest" README.md backend scripts
  ```

  Record the existing runner, its exact documented or repository-derived command, and the evidence for that selection. If the suite uses `unittest`, use its real discovery command (for example, from `backend/`, `$env:PYTHONDONTWRITEBYTECODE='1'; $env:PYTHONPATH='.'; python -m unittest discover -s tests -p "test_*.py" -v`) and do not introduce pytest. If discovery proves a test requires pytest-only collection behavior, identify that test and its existing command before declaring pytest.

- [ ] **Step 3: Run the discovered runner and declare a development dependency only when proven necessary.**

  Run the exact runner selected in Step 2 against the existing focused backend tests. The RED baseline must be an actual behavior/test failure, or a documented missing dependency that the selected existing runner demonstrably requires; it must never be an artificial failure caused by forcing `pytest`. Create `backend/requirements-dev.txt` only when that dependency is absent from `backend/requirements.txt`; include `-r requirements.txt` and only the verified development/test package version range. Otherwise do not create the file. Do not move demo credentials or runtime secrets into it.

- [ ] **Step 4: Establish frontend test modes from existing tests.**

  Run the current focused commands separately:

  ```powershell
  node --test tests/human-review-api.test.mjs
  node --test tests/b2a-contracts.test.mjs
  npx tsc --noEmit --incremental false
  ```

  Expected: identify API-independent tests versus `b2a-contracts.test.mjs`, which requires a live API at `B2A_TEST_API_URL` or `http://localhost:8000/api/v1`. If `package.json` needs scripts, add only `test:unit` and `test:api` commands that invoke these existing categories; preserve `test` until its consumers are inspected.

- [ ] **Step 5: Specify disposable infrastructure and fixture boundaries.**

  Use `backend/tests/support/sqlite.py` where its existing helpers support the tested reader behavior. For PostgreSQL-only behavior, run a fresh Compose volume or disposable PostgreSQL container whose connection string is not a real scientific database; record the generated endpoint/command in `preflight-baseline.json`. Inspect `frontend/tests/human-review-e2e.test.mjs` and move its fixture creation plan to the OS temporary directory before executing it again.

- [ ] **Step 6: Run the green baseline and record only evidence.**

  Run the documented backend focused test, frontend unit/contract test, TypeScript check, and `npm audit --omit=dev`; record commands, exit codes, exact audit Critical/High findings, 0021 hash, 0022 absence, and the test database type in `reports/prototype-handoff/preflight-baseline.json`. Do not call Docker Compose against an existing named volume as part of this step.

- [ ] **Step 7: Checkpoint.**

  Run `git status --short` and `git diff --check`; verify that no application-source fixture was produced and no user-owned change was altered.

### Task 2: Effective identity root-cause tracing and Participants reader

**Files:**
- Modify only if root cause is confirmed here: `backend/app/services/validated_read_service.py`
- Modify only if root cause is confirmed here: `backend/app/services/human_review_projection.py`
- Modify only if root cause is confirmed here: `backend/app/services/human_review_commands.py`
- Modify only if backend output is already singular and the duplication is proven client-side: `frontend/app/teachers/page.tsx`, `frontend/lib/data-cache.tsx`
- Test: `backend/tests/test_human_review_b2b2_readers.py`, `backend/tests/test_human_review_projection.py`, `backend/tests/test_human_review_b2b2_reversal.py`, `frontend/tests/b2a-contracts.test.mjs`

**Consumes:** Existing `ReviewDecision`, `FieldOverride`, effective projection functions, `ValidatedReadService.canonical_participants`, and the public `/participants` endpoint.

**Produces:** Participants exposes one effective logical identity for a corrected decision while retaining source rows only as traceability; no grouping rule relies on display-name equality or fuzzy matching.

- [ ] **Step 1: Build a disposable reproduction fixture.**

  In the existing reader/projection tests, create two source appearances with distinct persistent references that initially resolve to one original identity, then apply the existing human-decision path that corrects or links the identity. Keep the source rows intact and create a fresh SQLAlchemy session before reading the result.

- [ ] **Step 2: Run the focused test in RED.**

  Run the exact test that asserts `/participants` or `ValidatedReadService.canonical_participants` returns both original and corrected effective identities. Expected RED: two logical participant rows or duplicated authorship/KPI membership for the same persisted effective identity.

- [ ] **Step 3: Trace the public data path before changing code.**

  Inspect, in order: source target record → `ReviewDecision`/`FieldOverride` → `load_identity_projection` and other effective projection readers → `ValidatedReadService.canonical_participants` → `backend/app/api/v1/endpoints/participants.py` → `frontend/app/teachers/page.tsx` cache key and DOM. Record the first layer that emits both identities in the task checkpoint.

- [ ] **Step 4: Apply the minimal backend fix only at the confirmed emitting layer.**

  If `ValidatedReadService` emits duplicate effective groups, merge by the existing persisted effective identity reference already returned by the projection, not by `canonical_name`. If the projection itself emits inconsistent keys, correct its existing relation/override resolution without changing models, schema, source rows, or migrations. If the backend is correct, modify only the confirmed frontend grouping layer.

- [ ] **Step 5: Run GREEN and regression tests.**

  Run the new focused reproduction plus `backend/tests/test_human_review_b2b2_readers.py`, `backend/tests/test_human_review_projection.py`, and `backend/tests/test_human_review_b2b2_reversal.py`. Expected GREEN: exactly one Participants logical identity; original source appearance remains available only through traceability fields; no fuzzy/name-based merge is introduced.

- [ ] **Step 6: Checkpoint.**

  Record the confirmed root cause, affected persisted identifiers, test DB type, and scoped diff inventory. Stop if the fix needs a migration, model/schema change, or data rewrite.

### Task 3: Effective identity in Scientific Production, Projects, and KPIs

**Files:**
- Modify only if root cause is confirmed here: `backend/app/services/validated_read_service.py`
- Modify only if root cause is confirmed here: `backend/app/services/production_service.py`
- Modify only if root cause is confirmed here: `backend/app/services/research_entity_service.py`
- Modify only if root cause is confirmed here: `backend/app/services/kpi_service.py`
- Modify only if an already-correct API is duplicated by presentation: `frontend/app/production/page.tsx`, `frontend/app/projects/page.tsx`
- Test: `backend/tests/test_human_review_b2b2_readers.py`, `backend/tests/test_human_review_b2b2_kpi.py`, `backend/tests/test_human_review_b2b2_reversal.py`, `frontend/tests/b2a-contracts.test.mjs`

**Consumes:** The effective identity rule proven in Task 2; existing production, research-entity, and KPI readers; public `ScientificProductionAuthor` and person/project relationship data.

**Produces:** One effective identity is rendered consistently in Participants, Scientific Production, and Projects; correction, existing-identity link, separation, reversal, and multiple source records preserve correct KPI counts.

- [ ] **Step 1: Add cross-module RED fixtures.**

  Extend the disposable fixture from Task 2 with one person linked to multiple author/product/project source records. Cover five decisions through current public decision behavior: correction, link to existing identity, explicit separate decision, reversal, and multiple records. Assert the expected persisted effective identity references, rather than display text.

- [ ] **Step 2: Run reader and KPI RED checks.**

  Run focused cases in `test_human_review_b2b2_readers.py`, `test_human_review_b2b2_kpi.py`, and `test_human_review_b2b2_reversal.py`. Expected RED: the affected reader emits source-plus-corrected identities or the KPI count increments twice for one effective identity.

- [ ] **Step 3: Locate the first inconsistent reader.**

  Compare the effective identity keys from `ValidatedReadService`, `ProductionService`, `ResearchEntityService`, and `KpiService` using the same fresh fixture session. Modify only the first service whose public result differs from the persisted effective relation proven in Task 2.

- [ ] **Step 4: Implement the smallest consistent-reader change.**

  Reuse the existing effective projection/identity relation. Do not create a display-name map, fuzzy matching, a new API field, or a schema change. Preserve rows intentionally kept separate and preserve source scientific values.

- [ ] **Step 5: Verify end-to-end GREEN behavior.**

  Assert one logical identity across `/participants`, `/production`, and `/research-entities`; assert explicit-separate identities remain distinct; assert a reversal restores the prior effective view; assert a multi-record person contributes once where the KPI definition is identity-based and retains all source authorships where the definition is authorship-based.

- [ ] **Step 6: Checkpoint.**

  Record the cross-module fixture identifiers and expected counts in the focused test names. Run `git diff --check`; stop before any change requiring a migration, a model/schema edit, or a scientific-data write.

### Task 4: Human-readable validation cases

**Files:**
- Modify only if existing metadata does not already reach public readers: `backend/app/services/human_review_queries.py`, `backend/app/schemas/human_review_api.py`, `backend/app/api/v1/endpoints/human_review.py`
- Modify: `frontend/components/human-review/ReviewQueueTable.tsx`, `frontend/components/human-review/RelatedReviewList.tsx`
- Modify only if a selector is proven to render a case UUID as its primary label: `frontend/components/human-review/ReviewSessionPage.tsx`, `frontend/components/human-review/ReviewQueueFilters.tsx`
- Modify only if existing frontend contract types omit already-public fields: `frontend/lib/human-review.ts`, `frontend/lib/human-review-api.ts`, `frontend/hooks/useHumanReview.ts`
- Test: `backend/tests/test_human_review_b2b2_queries.py`, `backend/tests/test_human_review_b2b2_api.py`, `frontend/tests/human-review-queue.test.mjs`, `frontend/tests/human-review-detail.test.mjs`, `frontend/tests/human-review-e2e.test.mjs`

**Consumes:** Existing `ReviewQueueItem` fields (`case_type`, `case_status`, `document_name`, `source_page`, `source_section`, `detected_value`, `normalized_value`, `canonical_value`), `RelatedReviewItem`, and queue/related UI components.

**Produces:** Main queue, related case list, and any case selector identify the review type, primary data, source document/context, review reason, and status before opening; UUID is shown only in the existing advanced public-reference disclosure.

- [ ] **Step 1: Add a public-contract RED test using current metadata.**

  In `test_human_review_b2b2_queries.py` and `test_human_review_b2b2_api.py`, create an author-identity and a product fixture with document filename, page, section, detected value, case type, and pending state. Assert the response exposes sanitized public values and contains no `document_key`, `stable_target_key`, storage locator, database key, or traceback.

- [ ] **Step 2: Add UI RED tests for pre-open recognition.**

  In the queue and related-case tests, assert the rendered text includes the Spanish type label, primary value, document name, page/section when present, user-facing review reason, and status. Assert that the UUID is absent from the collapsed primary label and present only in the existing advanced reference disclosure.

- [ ] **Step 3: Verify whether a contract change is necessary.**

  Trace `HumanReviewQueryService._queue_item` and `_related_item` through `ReviewQueueItem`/`RelatedReviewItem` and `useHumanReviewQueue`. If all required public data is already present, do not widen the API. **STOP GATE:** if one strictly necessary datum is absent from every existing public field, stop, report the missing datum and its source, and propose the smallest backward-compatible public field before changing a schema or endpoint.

- [ ] **Step 4: Implement presentation from existing fields.**

  Use existing case-type and case-status mappings plus existing sanitized values. Build labels from public data only; do not surface internal IDs, `document_key`, `source_path`, `object_key`, access tokens, or raw database primary keys. Keep desktop layout/navigation intact outside the targeted queue, related list, and selector content.

- [ ] **Step 5: Run GREEN and public-safety regressions.**

  Run backend query/API tests and frontend queue/detail/E2E tests. Expected GREEN: a reviewer identifies a case without opening it; related rows carry the same context; selectors do not lead with `Caso <UUID>`; public responses remain sanitized.

- [ ] **Step 6: Checkpoint.**

  Save the focused test output and scoped diff inventory. Confirm no mobile-specific component or CSS rule was changed.

### Task 5: Session and client-cache isolation

**Files:**
- Modify: `frontend/lib/auth.ts`, `frontend/lib/data-cache.tsx`
- Modify only if logout must notify a currently mounted cache provider: `frontend/lib/effective-data-refresh.ts`, `frontend/lib/effective-data-revision.ts`
- Modify only if file/client API behavior needs an explicit session transition hook: `frontend/lib/api.ts`
- Test: `frontend/tests/human-review-api.test.mjs`, `frontend/tests/human-review-e2e.test.mjs`, `frontend/tests/human-review-validation-regressions.test.mjs`

**Consumes:** `saveSession`, `clearSession`, `getToken`, `createDataCacheStore`, local-storage cache key `scientific_data_cache_v7`, and existing human-review cache isolation behavior.

**Produces:** A token/session change clears every in-memory and persisted cache entry owned by the prior session before a later user can render it; current authentication storage remains localStorage-based.

- [ ] **Step 1: Write the A → B RED test.**

  In `human-review-api.test.mjs`, instantiate the existing data cache with a mutable token getter. Cache a Participants/Production/Projects/dashboard value for user A, persist it through the provider path, call `clearSession`, save a token for user B, and read/refetch. Expected RED: current logic clears only the `human-review:` prefix and can surface ordinary cached data from A.

- [ ] **Step 2: Run the focused RED test.**

  Run:

  ```powershell
  node --test tests/human-review-api.test.mjs
  ```

  Expected: the new A → B assertion fails while existing cache behavior remains observable.

- [ ] **Step 3: Apply the smallest session-boundary fix.**

  Make the existing `syncSession` path clear all cache/inflight/version state on token change, and make `clearSession` trigger the same boundary if a mounted provider needs notification. Remove or replace the persisted `scientific_data_cache_v7` data before hydration can expose A's entries to B. Do not redesign authentication or migrate to cookies.

- [ ] **Step 4: Run GREEN plus cache regressions.**

  Run `human-review-api.test.mjs`, `human-review-validation-regressions.test.mjs`, and the cache-focused portion of `human-review-e2e.test.mjs`. Expected GREEN: B initially renders no A value, performs a real fetch, and receives only B's response; effective-data refresh behavior still works.

- [ ] **Step 5: Checkpoint.**

  Record the cache storage key behavior and verify no token is logged, embedded in a URL, or included in the checkpoint.

### Task 6: Authenticated evidence/download safety and public error sanitation

**Files:**
- Modify: `backend/app/core/config.py`, `backend/app/services/evidence_service.py`, `backend/app/api/v1/endpoints/evidence.py`
- Modify only if the confirmed public serialization leak originates here: `backend/app/api/v1/endpoints/imports.py`, `backend/app/schemas/imports.py`
- Modify: `frontend/lib/api.ts`, `frontend/app/importaciones/page.tsx`
- Modify only if the existing authenticated evidence component needs the same public-error behavior: `frontend/components/human-review/EvidenceWorkspace.tsx`
- Create: `backend/tests/test_evidence_upload.py`
- Test: `backend/tests/test_human_review_b2b2_evidence.py`, `backend/tests/test_evidence_upload.py`, `frontend/tests/human-review-api.test.mjs`, `frontend/tests/human-review-detail.test.mjs`, `frontend/tests/human-review-e2e.test.mjs`

**Consumes:** `UploadFile`, `EvidenceService.upload_pdf`, existing MinIO client configuration, `apiFetchBlob`, import-file endpoints, human-review evidence endpoint, and existing public error envelopes.

**Produces:** Client evidence/download flows use authenticated blobs without JWT query strings; uploads enforce PDF MIME/extension policy and configurable size; public results contain sanitized filename/context only and never MinIO paths, localhost URLs, or complete tracebacks.

- [ ] **Step 1: Add backend RED cases for the upload boundary.**

  Create `test_evidence_upload.py` with in-memory `UploadFile` fixtures and a mocked MinIO client. Assert: `.pdf` plus approved PDF MIME is accepted; wrong extension or MIME is rejected; configured-byte-limit overflow is rejected before upload; object name is UUID-based; output never returns a MinIO/internal/localhost URL; and a hostile filename cannot appear unsanitized in a public header/response.

- [ ] **Step 2: Add RED cases for public error serialization.**

  First map the current imports error contract and all consumers: inspect `backend/app/schemas/imports.py`, `backend/app/api/v1/endpoints/imports.py`, `frontend/app/importaciones/page.tsx`, `frontend/lib/api.ts`, applicable frontend import types, and every `error_traceback`/`traceback` reference. Extend `test_human_review_b2b2_evidence.py` and the confirmed imports response test so an exception with a synthetic traceback remains in server diagnostics while no public JSON response exposes `traceback`, `source_path`, `object_key`, access token, bucket, or connection string. The expected public result must use the current public error envelope, a null trace field, or a generic sanitized diagnostic selected from the mapped contract.

- [ ] **Step 3: Add frontend RED tests for authenticated download behavior.**

  In `human-review-api.test.mjs` and `human-review-detail.test.mjs`, assert `apiFetchBlob` sends `Authorization` and no generated URL contains `?token=`. In `human-review-e2e.test.mjs`, assert a PDF preview uses only an object URL generated from a blob and UI error text is the public sanitized envelope.

- [ ] **Step 4: Apply minimal flow changes.**

  Add configuration for allowed evidence MIME/extension and maximum evidence bytes in `Settings`; validate and sanitize at the evidence upload boundary; retain MinIO as the provider. Replace `authenticatedFileUrl` callers in `importaciones/page.tsx` with an authenticated blob download/open path. Based on the Step 2 contract map, retain the imports response property and return only a null/sanitized/generic public value whenever that preserves compatibility; retain complete technical detail only in server logs/diagnostics. Do not remove or contractually change `error_traceback` outright unless every consumer is proven compatible and the change is strictly necessary and backward-compatible. **STOP GATE:** if a safe, contract-preserving public response cannot be selected from the existing envelope/fields, stop and report the consumers, incompatibility, and minimal backward-compatible alternative before altering a schema or endpoint. Do not change storage provider or expose a new storage URL.

- [ ] **Step 5: Run GREEN and adversarial regressions.**

  Run the new upload test, existing human-review evidence tests, frontend API/detail/E2E tests, and a focused import-file download test. Expected GREEN: approved PDF works; invalid/oversize input fails safely; no JWT or localhost/internal path reaches a client; no complete traceback reaches frontend state/DOM.

- [ ] **Step 6: Checkpoint.**

  Record allowed MIME/extensions and test size limit values in the checkpoint, not any credentials. Stop if validation requires a model/schema migration or a storage-provider replacement.

### Task 7: Explicit demo mode and verified schema baseline boundary

**Files:**
- Create: `backend/app/core/prototype_baseline.py`
- Create: `backend/tests/test_prototype_verified_schema_baseline.py`
- Modify: `backend/app/core/migrations.py`, `backend/scripts/init_db.py`, `backend/scripts/configure_human_review_privileges.py`
- Preserve and modify only where a failing regression proves the prior boundary incomplete: `backend/app/core/config.py`, `backend/app/main.py`, `backend/Dockerfile`, `docker-compose.yml`
- Modify only after confirming destructive maintenance endpoints are the existing scope: `backend/app/api/v1/endpoints/admin.py`
- Test: `backend/tests/test_init_db_engine_separation.py`, `backend/tests/test_prototype_demo_mode.py`, `backend/tests/test_prototype_verified_schema_baseline.py`, `backend/tests/test_human_review_privileges.py`

**Consumes:** The exact ordered 21-pair migration hash manifest and fingerprint contract in the approved spec; `Base.metadata`; `MIGRATIONS`; the current `schema_migrations(version, applied_at)` creation behavior; audited DDL in migrations 0014, 0017, 0018, and 0020; `configure_human_review_privileges`; current `seed()` statements/values; existing opt-in `Settings.demo_mode`; and the already-established Uvicorn-only serving boundary.

**Produces:** A fresh-only **PROTOTYPE VERIFIED SCHEMA BASELINE** boundary that refuses unknown state, creates current metadata without replaying migrations, installs only the closed runtime manifest, validates the approved semantic fingerprint and all repository hashes, atomically records the 21 verified version IDs using the existing control-table shape, proves the approved ACL, optionally reaches the unchanged demo seed through the runtime role, and preserves side-effect-free serving.

**Interfaces:** `MigrationHash` is a frozen dataclass with `version: str`, `relative_path: str`, and `sha256: str`. `BaselineReport` is a frozen dataclass with `versions: Sequence[str]`, `runtime_objects: Sequence[str]`, and `application_role: str`. `BaselineRefused` subclasses `RuntimeError`. The exact callable signatures are `prove_fresh_prototype_database(owner_engine: Engine, application_role: str) -> None`, `install_runtime_object_manifest(owner_engine: Engine) -> Sequence[str]`, `validate_runtime_fingerprint(owner_engine: Engine, *, phase: Literal["pre_registration", "final"]) -> None`, `verify_migration_hash_manifest(repository_root: Path) -> Sequence[str]`, `register_verified_baseline(owner_engine: Engine, versions: Sequence[str]) -> None`, and `establish_prototype_verified_schema_baseline(owner_engine: Engine, application_role: str, repository_root: Path) -> BaselineReport`.

- [ ] **Step 1: Reconcile prior Task 7 tests without weakening its completed behavior.**

  Inventory the existing scoped diffs in `config.py`, `main.py`, `init_db.py`, `backend/Dockerfile`, `docker-compose.yml`, `test_init_db_engine_separation.py`, and `test_prototype_demo_mode.py`. Preserve tests proving: missing/false `DEMO_MODE` disables demo-only destructive actions and demo seed; `DEMO_MODE=true` retains the exact approved demo users/passwords/roles/seed values; each destructive endpoint still requires its pre-existing authorization; and FastAPI/Uvicorn serving invokes no `init_db.py`, `create_all`, migration, baseline, ACL, or seed operation. Replace only the obsolete expectation that `prototype_bootstrap()` calls `run_migrations`.

- [ ] **Step 2: Write RED fresh-proof and orchestration tests using catalog mocks.**

  In `test_prototype_verified_schema_baseline.py`, mock catalog result sets and assert `prove_fresh_prototype_database` refuses each of: application table; application sequence; user view/materialized view; application function/trigger; `schema_migrations`; any scientific/demo row signal; unapproved schema/extension; identical owner/runtime role; runtime-owned object; or unexpected user object. Allow only `pg_catalog`, `pg_toast`, `information_schema`, the configured application schema, and `plpgsql`. In `test_init_db_engine_separation.py`, assert the exact order `prove fresh → create_all → install manifest → pre-registration fingerprint → verify 21 hashes → ensure control table → configure/validate ACL → atomic baseline registration → final fingerprint/ACL → conditional runtime-role seed`. Assert every failure stops later calls and never reaches `seed()` or serving.

- [ ] **Step 3: Write RED migration-manifest and control-table tests.**

  Define a closed `MIGRATION_SHA256_MANIFEST: Sequence[MigrationHash]` by copying all 21 exact ID/SHA-256 pairs from the spec and mapping each ID to its executable file under `backend/app/migrations/versions`. Assert the constant order equals `MIGRATIONS`, every digest matches, 0021 equals `54704858458CE6F27B12BF8B2BE386B91C3A32F27D1D210CEC50DAD16E423B2A`, no executable 0022 exists, and `20260629_0002_add_scientific_production_authors.py` is explicitly excluded. A missing/extra/reordered file, changed hash, inferred orphan, or 0022 raises `BaselineRefused` before control-table mutation.

  Extract the runner's existing control-table statement into `ensure_schema_migrations_table(connection: Connection) -> None` and make `run_migrations` call that helper without changing its replay semantics. Tests must assert the table remains exactly `version VARCHAR(120) PRIMARY KEY` and `applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP`; baseline registration inserts only `version`, requires an empty control table, inserts all 21 IDs in one transaction, and never calls a historical `upgrade` callable or `run_migrations`.

- [ ] **Step 4: Write RED closed-manifest and fingerprint tests.**

  Define the closed runtime manifest as exactly: partial unique index `uq_import_jobs_current_document`; functions `b2b_reject_career_capability`, `b2b_reject_career_role_with_capability`, `b2b_reject_append_only_mutation`; triggers `trg_user_b2b_capabilities_reject_career`, `trg_users_reject_career_with_b2b_capability`, `trg_review_decisions_append_only`, `trg_audit_events_append_only`; and the existing `configure_human_review_privileges` ACL. Copy no other migration DDL or DML. Unit/static tests compare the normalized index predicate, function signatures/bodies, and trigger table/events/function to the immutable source statements in migrations 0014, 0017, 0018, and 0020. If the exact definition cannot be derived and asserted, stop instead of implementing an approximation.

  Feed the fingerprint validator approved and adversarial catalog snapshots. It must compare expected tables, columns, PostgreSQL types, nullability, required current-runtime defaults, PK/FK semantics, unique constraints, checks, ordinary/partial/functional indexes, audited function bodies/signatures, audited triggers, the final 0021 audit-event check, expected schemas/extensions, ownership, control-table phase/state, and runtime privileges. Encode the seven accepted `*_pkey` versus historical `pk_*` human-review naming deltas explicitly; never make the validator ignore arbitrary constraint names or semantic differences.

- [ ] **Step 5: Prove the three known deltas before baseline acceptance.**

  Add a focused call-site inspection plus application deletion-flow test proving supported prototype paths do not depend on database `ON DELETE CASCADE` from `scientific_production_authors.production_id`; if they do, STOP without changing the FK. Inventory the server defaults introduced by migrations 0002–0014 and add supported-flow insert tests proving current ORM/application code supplies every required value; if a supported path depends on an absent server default, STOP. Scan runtime SQL, ACL/validators, and operational scripts for the seven historical `pk_*` names and assert PK/FK semantics are equivalent; any name dependency or semantic mismatch is a STOP. Do not change models, FKs, defaults, or PK names.

- [ ] **Step 6: Implement the minimal baseline boundary and failure contract.**

  Implement the interfaces in `prototype_baseline.py` and keep `init_db.py` as the thin CLI orchestrator. Use strict catalog queries with bound values/validated identifiers; issue fresh-state manifest DDL without generic `IF NOT EXISTS` repair clauses. Reuse `configure_human_review_privileges(owner_engine, application_role)` and its returned report; do not invent broader grants. Registration is allowed only after fresh proof, `create_all`, exact manifest, pre-registration fingerprint, 21 hash checks, and ACL validation pass. After registration, require final fingerprint and ACL validation before the unchanged `seed()` is called through `SessionLocal`/the runtime role when `settings.demo_mode is True`.

  On any failure after `create_all`, raise/report `BASELINE REFUSED / FAILED`, perform no partial repair or manual migration marking, do not seed, and do not serve; the operator must recreate the disposable database. A second invocation is refused read-only by the fresh gate because application objects/`schema_migrations` exist. Never replay migrations or use the baseline against an existing database.

- [ ] **Step 7: Run the smallest PostgreSQL 16 catalog verification required by Task 7.**

  Use one newly named disposable PostgreSQL 16 database only for catalog semantics that mocks cannot prove. Verify manifest definitions via `pg_get_indexdef`, `pg_get_functiondef`, and `pg_get_triggerdef`; verify the pre-registration and final semantic fingerprints; verify the exact control-table columns and 21 IDs; verify the final 0021 check; and run `configure_human_review_privileges` assertions that the runtime role owns no object, lacks schema `CREATE`, grant option, migration privileges, protected-function ownership, and role escalation. This is not Scenario A–D and must not test login/demo seed. Destroy only the explicitly named disposable target. Any mismatch is STOP, not a reason to modify migrations or models.

- [ ] **Step 8: Run GREEN regressions and checkpoint the reopened Task 7.**

  Run `test_prototype_verified_schema_baseline.py`, `test_init_db_engine_separation.py`, `test_prototype_demo_mode.py`, and `test_human_review_privileges.py`, plus the minimal PostgreSQL catalog test if Step 7 proved it necessary. Expected GREEN: the 0017 and future 0018–0020 collisions are impossible because no historical migration is replayed; the partial Dropbox index, B2B guards, append-only guards, final 0021 check, migration hashes/control state, and ACL all match; missing/false demo mode performs no demo DML; true mode reaches unchanged seed only after final validation; serving remains side-effect-free. Re-run all migration hashes, prove 0022 absent, record the orphan exclusion and known-delta evidence, and confirm no real database, migration, model, business schema, seed value, API, dependency, mobile behavior, or B2B.3 was changed.

### Task 8: Reproducible environment and fresh setup verification

**Execution prerequisite:** Task 8 remains blocked until the amended Task 7 is separately authorized, implemented, and reverified. When unblocked, Task 8 reruns all four scenarios from zero; no result from the blocked attempt is treated as passing evidence.

**Files:**
- Retain and modify without reverting prior partial work: `.env.example`, `docker-compose.yml`, `README.md`, `backend/tests/test_prototype_demo_mode.py`
- Modify only if variables need an ignore rule that does not already exist: `.gitignore`
- Test: `backend/tests/test_prototype_demo_mode.py`, `backend/tests/test_prototype_verified_schema_baseline.py`, `frontend/tests/login-page.test.mjs`

**Consumes:** Completed/reverified Task 7; current Task 8 partial files; Compose service variables; `Settings` names; Uvicorn-only backend command; explicit one-shot verified-baseline command; frontend `NEXT_PUBLIC_API_URL`; and unchanged demo accounts.

**Produces:** A fresh recipient can use `.env.example` alone to start disposable local infrastructure, establish **PROTOTYPE VERIFIED SCHEMA BASELINE** without replaying migrations, start side-effect-free application serving, and use demo accounts only under explicit `DEMO_MODE=true`; Scenarios A–D provide fresh PostgreSQL 16 evidence.

- [ ] **Step 1: Preserve and reconcile the blocked Task 8 partial work.**

  Inventory the existing diffs in `.env.example`, `docker-compose.yml`, `README.md`, and `test_prototype_demo_mode.py`; do not revert or recreate them. Replace only assertions/documentation that still describe the obsolete migration-replay bootstrap sequence. Keep tests that declare every Compose substitution, `DEMO_MODE=true` for the academic environment, distinct `APP_DATABASE_URL` and `MIGRATION_DATABASE_URL`, the explicit one-shot bootstrap, unchanged demo credentials, and Uvicorn-only serving. Add assertions that the bootstrap command establishes **PROTOTYPE VERIFIED SCHEMA BASELINE** and that serving has no schema/baseline/ACL/seed side effects.

- [ ] **Step 2: Finish the reproducible environment contract without real secrets.**

  Trace each Dropbox/n8n/external-integration setting from Compose and `Settings` to its startup consumer. Ensure `.env.example` includes `APP_DATABASE_URL`, `MIGRATION_DATABASE_URL`, `JWT_SECRET`, `INGEST_API_KEY`, `DROPBOX_CLIENT_ID`, `DROPBOX_CLIENT_SECRET`, `DROPBOX_REFRESH_TOKEN`, `N8N_WEBHOOK_URL`, `DEMO_MODE=true`, MinIO variables, and Task 6 evidence limits/types, using deterministic non-production placeholders. Preserve explicit runtime errors for optional integrations when invoked unconfigured. The one-shot bootstrap receives the migration-owner URL, the runtime URL/role needed for post-baseline demo seed, and the repository needed for hash verification; the long-running backend receives only runtime configuration. Document: copy `.env.example`; start PostgreSQL/MinIO; invoke the verified-baseline bootstrap; start backend/frontend; verify demo login; stop only the named disposable project. Never request/recover real credentials or rewrite demo application credentials as infrastructure secrets.

- [ ] **Step 3: Rerun Scenarios A–D from zero on disposable PostgreSQL 16.**

  Use new project names and new disposable volumes with `.env.example` alone; never point to a scientific or user-owned database.

  - **Scenario A:** On a fresh database, run the verified baseline with `DEMO_MODE=true`; prove fresh precondition, exact current-runtime fingerprint, exact closed runtime objects, final ACL, exactly 21 baseline records through 0021, excluded orphan, absent 0022, unchanged demo users/roles/data, backend health, and successful demo login.
  - **Scenario B:** Stop/restart only application serving against Scenario A's initialized database; prove no `create_all`, migration replay, baseline/control-table mutation, ACL mutation, or seed DML and confirm health/login still work.
  - **Scenario C:** Invoke bootstrap again against Scenario A's initialized database; require clear `BASELINE REFUSED / FAILED` at the fresh-database gate before any mutation or seed DML. Prove fingerprint/control rows and user/scientific/demo counts are unchanged.
  - **Scenario D:** On another fresh database, run the verified baseline with `DEMO_MODE=false`; prove the same fingerprint/runtime objects/21 records/ACL while demo users and demo dataset are absent, then start serving without bootstrap side effects.

  If any scenario requires a migration/model/business-schema/seed-value change, generic stamp, weakened validator/ACL, external secret, API/dependency/mobile change, B2B.3, or undocumented manual adjustment, stop with exact evidence. Tear down only the explicitly named disposable verification projects and volumes.

- [ ] **Step 4: Checkpoint Task 8 evidence.**

  Verify `.env` remains ignored, `.env.example` is versionable, no real token/connection string is printed, and no old container/environment value was required. Record exact baseline and serving commands, project/volume names, PostgreSQL 16 target, A–D results, 21 IDs/hashes, manifest/fingerprint/ACL outcomes, refusal result, login result, and scoped cleanup outside application source. Task 8 passes only if all four fresh rerun scenarios pass.

### Task 9: Compatible dependency and security fixes

**Files:**
- Modify: `frontend/package.json`, `frontend/package-lock.json`
- Modify only if a compatible dependency update changes Next configuration behavior: `frontend/next.config.js`
- Test: `frontend/tests/login-page.test.mjs`, `frontend/tests/human-review-api.test.mjs`, `frontend/tests/human-review-queue.test.mjs`, `frontend/tests/human-review-detail.test.mjs`, `frontend/tests/human-review-e2e.test.mjs`

**Consumes:** Task 1 audit baseline and the existing frontend dependency lockfile.

**Produces:** Exact `npm audit --omit=dev` Critical/High findings are recorded and resolved with compatible patch/minor upgrades where available; any advisory requiring a major upgrade is documented as future work.

- [ ] **Step 1: Freeze the RED audit evidence.**

  Run:

  ```powershell
  npm audit --omit=dev --json
  ```

  Record package, installed version, advisory, severity, affected range, available fix, and whether the available fix is major. Do not run an automatic force fix.

- [ ] **Step 2: Select one compatible upgrade at a time.**

  For each Critical/High finding with a patch/minor-compatible resolution, update only the affected direct dependency/lockfile path. Before the next package, run the focused tests for the dependency's surface. **STOP GATE:** if resolving the advisory requires a major Next.js/framework upgrade or broad refactor, leave the installed version unchanged and add its exact advisory/fix requirement to future work.

- [ ] **Step 3: Run GREEN verification per accepted upgrade.**

  After each upgrade, run `npx tsc --noEmit --incremental false`, the focused frontend tests, and `npm run build`. Then rerun `npm audit --omit=dev --json` and record the changed Critical/High totals.

- [ ] **Step 4: Checkpoint.**

  Record each package/version transition and the exact remaining audit result. Confirm no major version, framework migration, or unrelated dependency rewrite occurred.

### Task 10: Delivery documentation and non-destructive repository hygiene

**Files:**
- Create: `docs/prototype-handoff.md`
- Modify: `README.md`
- Modify only if a clearly generated handoff report directory needs an existing ignore rule: `.gitignore`
- Test: `frontend/tests/login-page.test.mjs`, `backend/tests/test_prototype_demo_mode.py`

**Consumes:** Tasks 1–9 checkpoints, approved design, Compose setup, demo credentials, existing README, and existing test commands.

**Produces:** A concise handoff guide and README that allow a future tesista to run demo mode, understand effective identity/validation, execute tests, and distinguish known limitations from completed prototype behavior.

- [ ] **Step 1: Write the handoff guide from verified commands only.**

  `docs/prototype-handoff.md` must explain: prototype purpose; architecture/module map; `.env.example` setup; `DEMO_MODE=true`; demo accounts; the explicit infrastructure → verified schema baseline → application serving sequence; fresh-only refusal; closed runtime-object/fingerprint/hash/ACL verification; backend/frontend/API test commands; Docker Compose flow; Dropbox/n8n import flow; human review and effective projection; evidence/download behavior; known limitations; API vNext pagination; queue/major-upgrade future work. Include literally: “PROTOTYPE VERIFIED SCHEMA BASELINE is available only for a brand-new academic-prototype database. It creates current metadata, installs and validates the closed audited runtime state, and records the immutable migration IDs only after verification; it does not replay migrations, stamp or upgrade existing databases, or define production migration architecture.”

- [ ] **Step 2: Update README without replacing its thesis context.**

  Add concise links to the handoff guide and exact fresh-start/test commands. Keep the existing user-facing demo accounts and academic narrative. Do not claim production readiness.

- [ ] **Step 3: Inventory only unequivocal generated artifacts.**

  Compare `git status --short` to Task 1's inventory. Delete only an artifact when all three conditions hold: it was generated by this stabilization run, it is outside application source, and its path is explicitly recorded in the checkpoint. For every other untracked/deleted/modified item, list it as user-owned and stop rather than guessing.

- [ ] **Step 4: Verify documentation and hygiene.**

  Run `git diff --check`, verify every documented command exists, verify `.env` is ignored and `.env.example` is not ignored, and run demo login/header smoke checks. Expected GREEN: no user-owned file is reverted or staged.

- [ ] **Step 5: Checkpoint.**

  Record a scoped diff inventory and documentation links; do not commit.

### Prerequisite repair before Task 11 (does not change the 12-task count)

**Authorization gate:** This section is planned only. Do not execute it, create migration 0022, change product code, or resume Task 11 until a separate implementation authorization names this prerequisite. Tasks 1–10 remain completed checkpoints; this repair exists solely to unblock the existing Task 11.

**Files:**
- Create: `backend/app/services/human_review_scope.py`
- Create after separate migration authorization: `backend/app/migrations/versions/20260827_0022_scoped_human_review_authorization.py`
- Create: `backend/tests/test_human_review_scope.py`
- Create: `backend/tests/test_human_review_scope_migration_0022.py`
- Create: `backend/tests/test_human_review_demo_scope.py`
- Create: `backend/data/demo/human_review_scope_demo.pdf`
- Modify: `backend/app/models/entities.py`, `backend/app/models/human_review_core.py`
- Modify: `backend/app/services/human_review_authorization.py`, `backend/app/services/human_review_queries.py`, `backend/app/services/human_review_commands.py`
- Modify: `backend/app/api/dependencies.py`, `backend/app/api/v1/endpoints/human_review.py`
- Modify: `backend/app/core/migrations.py`, `backend/app/core/prototype_baseline.py`, `backend/scripts/init_db.py`
- Modify: `frontend/components/AppShell.tsx`, `frontend/components/human-review/ReviewSessionPage.tsx`, `frontend/lib/human-review.ts`, `frontend/tests/human-review-queue.test.mjs`, `frontend/tests/human-review-detail.test.mjs`, `frontend/tests/human-review-e2e.test.mjs`
- Modify: `README.md`, `docs/prototype-handoff.md`
- Test: existing Human Review authorization/query/API/command/evidence/reversal/reader/KPI/demo/baseline/privilege suites plus the three new backend modules and focused frontend suites.

**Interfaces:**

```python
@dataclass(frozen=True)
class ResolvedReviewScope:
    faculty_id: int | None
    career_id: int | None
    resolution_reason: str | None

def resolve_target_scope(
    db: Session,
    target_table: ReviewTargetTable,
    target_pk: int,
) -> ResolvedReviewScope: ...

def review_scope_predicate(
    user: User,
) -> ColumnElement[bool]: ...

def assert_review_item_scope(
    db: Session,
    user: User,
    item: ReviewItem,
) -> None: ...
```

`resolve_target_scope` is server-owned and consumes only persisted FK relationships. `review_scope_predicate` is the sole list/facet/filter boundary. `assert_review_item_scope` raises the existing sanitized 403-domain error and is called before protected detail, evidence, preview, audit, command, reversal, conflict/CAS, or projection behavior. Capability actions may add action types but never alter either scope function.

**Produces:** Existing demo users gain role-appropriate Human Review access without a third account; `FACULTY_ADMIN` is restricted to its persisted faculty; `CAREER_MANAGER` is restricted to its persisted career; unresolved and inter-career semantics are deterministic; two demo cases and one synthetic PDF make the full Task 11 runtime journey reproducible.

- [ ] **Step 1: Reconfirm the design-only boundary and write RED scope contracts.**

  Before any edit, verify 0021's exact hash, confirm 0022 absent, record the current design/plan hashes and worktree inventory, and verify the target is disposable. In `test_human_review_scope.py`, create two faculties, three careers, faculty/career managers, single-career targets, a same-faculty inter-career project, cross-faculty targets, external/unlinked targets, and cases for every existing `ReviewCaseType`. Assert the exact `ResolvedReviewScope` triples from the amended spec and that no name/email/raw-text value participates.

  Run:

  ```powershell
  python -B -m unittest tests.test_human_review_scope -v
  ```

  Expected RED: `human_review_scope` and the new persisted columns do not exist. Stop if any required case type needs a relationship beyond the approved fields.

- [ ] **Step 2: Write migration-0022 RED catalog/backfill/rollback tests.**

  In `test_human_review_scope_migration_0022.py`, require exact migration ID `20260827_0022_scoped_human_review_authorization` and exact schema:

  - nullable `users.faculty_id` with FK `fk_users_faculty_id_faculties`, `ON DELETE RESTRICT`, and `ix_users_faculty_id`;
  - nullable `review_items.scope_faculty_id`, `scope_career_id`, and `scope_resolution_reason VARCHAR(80)`;
  - `uq_careers_id_faculty_id` on `careers(id, faculty_id)`;
  - `fk_review_items_scope_faculty_id_faculties` and composite `fk_review_items_scope_career_faculty_careers`, both `ON DELETE RESTRICT`;
  - `ck_review_items_scope_hierarchy` and `ck_review_items_scope_resolution` with the exact null/reason semantics from the spec;
  - queue indexes `ix_review_items_scope_faculty_queue` and `ix_review_items_scope_career_queue` covering scope, status, automatic priority, creation time, and ID.

  Test upgrade from an immutable 0021 catalog, deterministic backfill outcomes, unresolved reason counts, no source/decision/audit rewrite, downgrade order, and a second-upgrade refusal through the existing migration runner. Do not require a hash constant until the final migration file exists; once created, calculate it, obtain explicit approval, and only then append it to `MIGRATIONS`/baseline manifest.

- [ ] **Step 3: Implement only the approved model and migration schema.**

  Add the three model fields and exact constraints/indexes above. Migration 0022 adds nullable columns first, performs self-contained deterministic FK-only backfill in bounded transactions, adds/validates constraints and indexes, and emits only aggregate reason counts plus an input/output hash. Existing faculty administrators remain null unless an explicit approved user-ID/faculty-ID assignment is supplied; no email, displayed name, arbitrary first career, or fuzzy/text inference is allowed. Existing career IDs remain unchanged.

  Run:

  ```powershell
  python -B -m unittest tests.test_human_review_scope_migration_0022 -v
  ```

  Expected GREEN on a disposable PostgreSQL 16 database: schema exact, unambiguous rows scoped, ambiguous rows fail-closed, downgrade removes only 0022 objects, and migrations 0001–0021 remain byte-identical.

- [ ] **Step 4: Implement the centralized resolver and role/action matrix.**

  Implement `ResolvedReviewScope`, `resolve_target_scope`, `review_scope_predicate`, and `assert_review_item_scope`. One career sets faculty + career; several careers in one faculty set faculty only; missing/cross-faculty scope sets both IDs null with one of `unresolved_no_persisted_scope`, `unresolved_cross_faculty`, or `unresolved_missing_target`. `FACULTY_ADMIN` requires persisted `user.faculty_id`; `CAREER_MANAGER` requires persisted `user.career_id`. Both receive view/apply/revert actions; existing `RESEARCH_MANAGER` and `SYSTEM_ADMIN` action types remain, but neither widens geographic scope. Preserve immutable 0017 capability guards.

  Extend the existing `/human-review/me` implementation to return role-derived actions in its current fields. Do not add a response property or capability enum value. Frontend recognition uses action membership, not a hard-coded capability label.

- [ ] **Step 5: Apply scope to every backend read and write surface.**

  Pass the persisted actor into `HumanReviewQueryService`. Apply `review_scope_predicate` to queue totals, rows, facets, and effective revision. Filter related results individually. Call `assert_review_item_scope` before detail, audit, evidence metadata/stream, and preview. In apply/discard/revert, acquire the current item under the same scope, recheck after row lock, and only then evaluate expected version/current decision. An unauthorized direct UUID returns sanitized 403 before target/evidence/version disclosure; queue/list exclusion remains silent. Reject an explicit out-of-career filter from a career manager.

  Run the new scope suite plus existing authorization, queries, API, evidence, decisions, conflict, audit, reversal, effective-reader, and KPI tests. Required negative matrix: faculty admin outside faculty denied; career manager other-career list false and direct detail/correction/reversal/evidence denied; faculty-only/inter-career denied to career manager; unresolved denied to both scoped roles.

- [ ] **Step 6: Update desktop navigation and gates without mobile work.**

  Make the existing desktop Human Review navigation visible when `/human-review/me.actions` includes `view_foundations`. Gate buttons by the existing action strings. Remove frontend assumptions that only capability labels `RESEARCH_MANAGER`/`SYSTEM_ADMIN` are recognized. Keep backend scope authoritative and do not send faculty/career scope in mutation payloads. Add frontend tests for both demo roles, unavailable actions, sanitized 403, and no cross-session cached cases. Do not modify responsive/mobile-specific behavior.

- [ ] **Step 7: Add the minimum deterministic demo fixture and authenticated PDF.**

  Generate once and version `backend/data/demo/human_review_scope_demo.pdf`; it must contain only “DEMO / SYNTHETIC” content for two sections, have a fixed recorded SHA-256, valid PDF MIME/header, bounded size, and no real personal data. Do not use the thesis PDF or restore parser fixtures. The file is not a static/public frontend asset.

  Under `DEMO_MODE=true`, keep both accounts/credentials/roles/scientific rows, set the existing faculty account's `faculty_id` from the persisted seeded `Faculty.id`, keep the ADM manager's current `career_id`, and add exactly:

  - Case A: ADM identity correction with additive person-role/authorship links across existing ADM teacher, production, and project data; both users may read/apply/reverse it and its correction must produce one effective identity across the three modules without KPI double count.
  - Case B: another-career case in the same faculty anchored through a persisted non-ADM teacher/product; only the faculty admin may read/apply/reverse it.

  Seed deterministic IDs/keys/revisions and a `DEMO_PDF` ImportJob/OCR trace for the synthetic asset. Add a `DEMO_MODE=true` allow-listed server adapter behind the existing evidence endpoint; validate canonical asset path, fixed hash, configured size, PDF header, and page count before streaming. `DEMO_MODE=false` must produce zero demo users, scientific rows, review items, evidence metadata, and evidence access.

- [ ] **Step 8: Amend verified baseline, environment docs, and tests.**

  After migration 0022's exact hash is separately approved, append it as entry 22 in `MIGRATIONS` and `MIGRATION_SHA256_MANIFEST`; update the current-runtime fingerprint for the approved columns/FKs/checks/indexes. Keep the runtime-object manifest at 8/8 unless catalog proof shows a new required object is not produced by metadata; if so, stop for design review rather than inferring DDL. Fresh bootstrap must record 22/22 without replay. Existing 0021 databases use only pending 0022 through the migration owner after backup/preflight.

  Update README and handoff documentation minimally: faculty admins review their assigned faculty; career managers review only their assigned career; demo evidence is synthetic; no credentials beyond the existing demo-account policy. Re-run `DEMO_MODE=true/false`, fresh refusal, serving-side-effect, fingerprint, ACL, and secret-scan tests.

- [ ] **Step 9: Run prerequisite integrated verification and checkpoint.**

  On a new disposable PostgreSQL 16/Compose project, prove 0021 unchanged, approved 0022 exact, baseline 22/22, manifest 8/8, fingerprint/ACL PASS, two unchanged demo accounts, Cases A/B, authenticated PDF, scope matrix, effective identity/reversal/KPI, side-effect-free restart, safe bootstrap refusal, and zero demo data/evidence under `DEMO_MODE=false`. Run `git diff --check`, all affected backend/frontend tests, TypeScript, build, and desktop QA with zero broken images, console/hydration errors, or unexpected requests. Record a prerequisite checkpoint; do not call Task 11 complete and do not start Task 12.

### Task 11: Final integrated verification

**Files:**
- Modify only if an integration failure identifies a confirmed defect in an already-scoped Task file.
- Create: `reports/prototype-handoff/final-verification.json` (generated checkpoint; verify ignore status before writing)
- Test: all confirmed backend tests; all confirmed frontend tests; desktop runtime check against the disposable Compose project.

**Consumes:** Completed Tasks 1–10; separately authorized and completed persisted-scope prerequisite checkpoint; approved design verification criteria; disposable Compose setup.

**Produces:** Fresh, traceable evidence that the prototype meets the approved delivery criteria with only the separately approved 0022 persisted-scope schema, no mobile redesign, and no real-data writes.

- [ ] **Step 1: Reconfirm amended immutable boundaries.**

  Re-run migration 0021 SHA-256; verify separately approved migration 0022 is the sole appended migration and matches its approved checkpoint hash; confirm no 0023; scan for B2B.3 additions; check `git diff --check`; and compare worktree status against the Task 1/prerequisite inventories. Stop on any 0001–0021 change, unapproved schema/model change beyond the exact scope fields/constraints, or unclassified user-owned conflict.

- [ ] **Step 2: Run backend evidence against disposable infrastructure.**

  Run focused identity, query, evidence, demo-mode, verified-baseline boundary, startup, authorization, scope-resolver/migration/backfill, and KPI tests; then run the complete backend suite using the declared disposable DB strategy. Include a fresh 22/22 verified baseline, serving-only restart, fresh-gate refusal, and `DEMO_MODE=false` PostgreSQL 16 scenarios. Record each command, test count, exit status, and database target. A real PostgreSQL connection string is a hard stop.

- [ ] **Step 3: Run frontend evidence in layers.**

  Run focused queue/related, cache, evidence, login, and E2E tests; then the full frontend test mode after API prerequisites are up. Run `npx tsc --noEmit --incremental false` and `npm run build`. Record any B2A endpoint prerequisite separately from unit/contract tests.

- [ ] **Step 4: Run the desktop runtime QA.**

  Against the disposable frontend/backend, verify desktop login assets specifically. As `FACULTY_ADMIN`: login → visible Human Review → Cases A/B → open A → synthetic authenticated PDF → correction → one effective identity in Participants/Scientific Production/Projects → no KPI double count → reversal; also prove an outside-faculty direct UUID returns 403 in the backend matrix. As the ADM `CAREER_MANAGER`: login → only Case A visible → own-career correction/reversal allowed → Case B absent from lists and direct detail/correction/reversal/evidence return 403. Then rerun logout A → login B cache isolation. Collect: broken images = 0, console errors = 0, hydration errors = 0, unexpected failed requests = 0. Do not add or redesign mobile QA/work.

- [ ] **Step 5: Run final security/configuration checks.**

  Run `npm audit --omit=dev`; record exact Critical/High totals and accepted compatible resolutions. Scan tracked files for real PostgreSQL/infrastructure credentials, JWT secrets, MinIO credentials, Dropbox/API tokens, and connection strings; explicitly exclude the approved demo application-user credentials from failure classification. Verify public responses and frontend DOM contain no JWT query string, MinIO/internal path, localhost evidence URL, or complete traceback.

- [ ] **Step 6: Fresh setup confirmation and checkpoint.**

  Recreate the disposable setup exclusively from README and `.env.example`, with no user environment overrides, manual credential substitutions, or external secret recovery: start infrastructure, establish the documented 22/22 **PROTOTYPE VERIFIED SCHEMA BASELINE**, start application serving, and verify both demo roles plus Cases A/B under `DEMO_MODE=true`. Confirm serving alone has no `create_all`, migration replay, baseline/control-table mutation, ACL, or seed side effect; repeat fresh bootstrap with `DEMO_MODE=false` and require zero demo users/scientific rows/review cases/evidence. Write commands/results to `final-verification.json`, preserving no secret values. Stop if any verification requires a major framework upgrade, API response widening, migration beyond approved 0022, schema/model change beyond the approved scope fields, generic stamp, baseline on non-fresh state, B2B.3, queue infrastructure, general pagination, import refactor, mobile work, destructive Git action, or manual external configuration.

### Task 12: Independent closure review and delivery report

**Files:**
- Create: `reports/prototype-handoff/prototype-delivery-final.md` (generated report; verify ignore status before writing)
- Create: `reports/prototype-handoff/prototype-delivery-final.json` (generated report; verify ignore status before writing)
- Modify: none unless a documentation-only correction is required by a failed evidence check in Task 11.
- Test: review of Task 11 evidence; `git diff --check`; SHA-256 checks for approved design, immutable migration 0021, and separately approved migration 0022.

**Consumes:** Approved spec, this implementation plan, Task 1 baseline, Task 11 final verification, and current worktree inventory.

**Produces:** An independent, evidence-backed delivery decision and machine-readable report. The permitted declaration is `PROTOTYPE DELIVERY READY`; `PRODUCTION READY` is never emitted.

- [ ] **Step 1: Review spec coverage.**

  Map every approved design section to completed Task evidence: explicit demo mode; fresh-only verified schema baseline/environment and serving separation; runtime-object manifest; semantic fingerprint; migration hashes/control state; known deltas; ACL; cache; evidence/errors; effective identity; human-readable cases; tests; dependency audit; documentation/known limitation; immutable boundaries. List a failed or skipped criterion explicitly rather than inferring success.

- [ ] **Step 2: Review quality and scope.**

  Check the final diff for: no 0001–0021 modification; exactly one approved 0022 and no 0023; only the approved persisted-scope model/schema change; no B2B.3, real-data write, major framework upgrade, general pagination, proactive import refactor, mobile redesign, destructive Git operation, or source-writing test fixture. The approved synthetic demo PDF is a versioned demo asset, not a test-generated source fixture.

- [ ] **Step 3: Write the final reports from evidence.**

  `prototype-delivery-final.md` and `.json` include: design SHA-256; all 22 verified migration hashes, immutable 0021 proof, approved 0022 proof, orphan/no-0023 status; manifest/fingerprint/control-table/ACL and known-delta evidence; scope authorization matrix; demo Cases A/B and synthetic-PDF evidence; test command/result matrix; audit result; secret-scan result; desktop QA result; fresh-setup A–D result; worktree safety inventory; known limitations; and the delivery declaration. Do not include access tokens, passwords beyond approved demo account references, database URLs, or private traces.

- [ ] **Step 4: Issue the conditional declaration.**

  Emit `PROTOTYPE DELIVERY READY` only when every required Task 11 verification is green and immutable boundaries match. Otherwise report `PROTOTYPE DELIVERY BLOCKED` with the exact unmet criteria. Never emit `PRODUCTION READY`.

- [ ] **Step 5: Final checkpoint.**

  Run `git status --short`, `git diff --check`, and hashes for the design, migration 0021, and approved migration 0022. Confirm the implementation did not stage, commit, reset, checkout, clean, stash, amend, or alter user-owned work.

## Plan self-review

| Approved design section | Implementation task(s) |
| --- | --- |
| Explicit opt-in prototype mode | 7, 8, 11, 12 |
| Reproducible environment, verified schema baseline, ACL, and serving boundary | 1, 7, 8, 10, 11, 12 |
| Session, files, evidence, and public errors | 5, 6, 11 |
| Cross-module effective identity and KPI correctness | 2, 3, 11 |
| Human-readable validation cases | 4, 11 |
| Persisted Human Review role/faculty/career scope | prerequisite before 11, 11, 12 |
| Additive demo Cases A/B and synthetic authenticated PDF | prerequisite before 11, 11, 12 |
| Test isolation and reproducibility | 1, 2–8, 11 |
| Import boundary YAGNI | 6, 11, 12 |
| Pagination deferred | 11, 12 |
| Compatible dependency remediation | 1, 9, 11 |
| Handoff documentation and hygiene | 10, 12 |
| Immutable migrations 0001–0021, approved 0022-only scope schema, fresh-only refusal, and worktree safety | 1, 7, prerequisite before 11, 11, 12 |

## Verified schema baseline amendment self-review

| Required resolution | Plan evidence |
| --- | --- |
| 0017 `DuplicateTable` collision | Task 7 never calls migration upgrades/`run_migrations`; it registers only after validation. |
| Future 0018–0020 create-table collisions | Task 7 prohibits all historical replay and fingerprints current metadata instead. |
| Missing partial Dropbox index | Task 7 closed manifest installs and semantically verifies `uq_import_jobs_current_document`. |
| Missing B2B functions/triggers | Task 7 closed manifest installs/verifies both functions and both triggers from 0017. |
| Missing append-only function/triggers | Task 7 installs/verifies the 0018 function and 0018/0020 triggers. |
| ACL final state | Task 7 reuses and verifies `configure_human_review_privileges`; Tasks 8/11 rerun proof. |
| Orphan migration exclusion | Task 7 names and excludes `20260629_0002_add_scientific_production_authors.py`. |
| Migration hash validation | Task 7 preserves the closed historical 21-pair manifest; the prerequisite may append only separately approved 0022 as pair 22 before amended registration. |
| Actual `schema_migrations` shape | Task 7 preserves `version` plus `applied_at`, stores no hashes, and inserts only supported fields. |
| CASCADE/default/PK-name deltas | Task 7 Step 5 requires application-flow/static/semantic proof and STOP on dependency. |
| Fresh-only refusal | Task 7 refuses unexpected pre-state; Task 8 Scenario C proves mutation-free re-run refusal. |
| No serving side effects | Prior Task 7 Uvicorn/FastAPI boundary is preserved and reverified in Tasks 7, 8, and 11. |
| `DEMO_MODE` behavior | Prior opt-in gate and unchanged demo values are preserved; seed occurs only after final validation. |
| Task 8 partial work | `.env.example`, `docker-compose.yml`, `README.md`, and `test_prototype_demo_mode.py` are explicitly retained. |
| Persisted scoped authorization | The prerequisite adds only the direct User/ReviewItem scope model, deterministic FK-only backfill, server-side read/write policy, demo Cases A/B, and authenticated synthetic PDF from the amended spec. |

Placeholder scan requirements: this plan contains no unspecified function names or migration numbers. The future 0022 hash is intentionally not fabricated: implementation must calculate it from the final separately authorized file and obtain approval before manifest insertion. The plan contains no general pagination task, worker-queue task, mobile redesign task, commit step, or destructive Git command. Every conditional modification names its confirmed current file and requires root-cause evidence before editing.

Execution is authorized only after a separate approval. Creating this plan changes no product code, no real scientific data, and starts no implementation task.
