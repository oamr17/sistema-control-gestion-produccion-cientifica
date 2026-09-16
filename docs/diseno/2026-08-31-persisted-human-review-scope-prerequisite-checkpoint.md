# Persisted Human Review Scope — Prerequisite Checkpoint

Date: 2026-08-31<br>
Status: **PERSISTED HUMAN REVIEW SCOPE PREREQUISITE COMPLETE**<br>
Boundary: prerequisite before Task 11 only. Task 11 was not resumed and Task 12 was not started.

## Immutable approvals

- Design SHA-256: `2A1A396C32115B0C0D8426966066FEE0D6635AC7CE84489FC089A2CE1D4A2FAE`.
- Implementation plan SHA-256: `0DA2384A042296EF205839B03083B1B318A7569BA36C9F3EDFFAAB2F3EEE8B26`.
- Migration 0021 SHA-256: `54704858458CE6F27B12BF8B2BE386B91C3A32F27D1D210CEC50DAD16E423B2A`.
- Approved migration 0022 SHA-256: `2F6B9ACE942723F5A2095D1673E3F8051C5CB81E58E45141C4B4A31F4C4DDFCF`.
- Migration 0022 is the sole appended migration; no 0023 exists and no migration 0001–0021 changed.
- Runtime-object manifest remains exactly 8 objects. The verified schema baseline records exactly 22 migrations.

## Implemented prerequisite

- Persisted faculty scope on faculty administrators and persisted faculty/career scope on review cases.
- One centralized, FK-only, fail-closed scope resolver and matching read/write predicates.
- Faculty administrators are restricted to their persisted faculty; career managers are restricted to their persisted career. Direct out-of-scope detail, evidence, apply and reversal return sanitized 403 before CAS evaluation.
- The existing two demo users and their credentials remain unchanged. No third user was added.
- Demo Case A belongs to ADM and Case B to another career in the same faculty. Both use the authenticated evidence flow.
- The synthetic two-page demo PDF has SHA-256 `9880FCCB6E380F788D4F8BE27E3F7A221E412AD1B805240D6BF1DE9E24B32381`.
- A Case A correction projects one effective identity across Participants, Scientific Production and Projects; reversal restores the previous identity and KPI state symmetrically.
- Desktop navigation and action controls use effective action membership. Mobile behavior was not redesigned or tested.

## Fresh verification evidence

### Backend and PostgreSQL 16

| Verification | Result |
| --- | --- |
| Focused scope/models/auth/API/commands/reversal | 90/90 PASS |
| PostgreSQL query service | 26/26 PASS |
| Evidence service and authenticated PDF | 22/22 PASS |
| Evidence upload/public serialization | 10/10 PASS |
| Migration 0022 catalog/backfill/rollback/runner | 18/18 PASS |
| Baseline/demo/import-boundary suite | 63 PASS, 1 PostgreSQL-only test skipped in that invocation |
| PostgreSQL catalog/fingerprint/ACL proof | 1/1 PASS in a fresh disposable database |
| PostgreSQL server | 16.13 |

The real catalog proof established the 22/22 baseline, exact semantic fingerprint,
eight runtime objects and least-privilege ACL. The normal runner also proved that an
initialized 0021 database applies only pending 0022.

### Disposable bootstrap scenarios

- `DEMO_MODE=true`: 22 migrations, exactly 2 users, 2 review cases and 1 `DEMO_PDF` import job.
- Demo assignments: `FACULTY_ADMIN` has faculty 1 and no career; `CAREER_MANAGER` has career 1 and no faculty duplication.
- Serving restart preserved 22 migrations, 2 users, 2 cases, 2 decisions, 2 audit events and 1 demo evidence job.
- A second bootstrap refused the non-fresh database before mutation.
- `DEMO_MODE=false`: 22 migrations, 0 users, 0 scientific productions, 0 review cases and 0 demo evidence jobs.

### Desktop frontend

| Verification | Result |
| --- | --- |
| Affected Human Review tests | 112/112 PASS |
| Login layout/assets test, isolated to avoid runner contention | 1/1 PASS |
| TypeScript | PASS |
| Next.js production build | PASS |
| Faculty-admin queue in 2025–2026 / Cycle 2 | Cases A and B visible |
| ADM career-manager queue in 2025–2026 / Cycle 2 | Only Case A visible |
| Authenticated demo PDF | Detail and one local PDF preview frame rendered |
| Broken login/application images | 0 |
| Console errors | 0 |
| Hydration/page errors | 0 |
| Unexpected failed requests | 0 |
| JWT/token-bearing URLs | 0 |
| Hardcoded `localhost:8000` URLs delivered by the QA build | 0 |

Next.js cancelled only its own speculative RSC prefetches, and Chromium cancelled the
local `blob:` frame when its isolated context closed. They produced no HTTP failure or
visible error and are classified as expected lifecycle cancellation, not unexpected
failed requests.

### Security boundaries

- `npm audit --omit=dev`: exactly 0 Critical and 3 High.
- The remaining High findings are Next.js, its transitive PostCSS, and Sharp. The only
  offered complete resolutions are breaking upgrades (`next@16.3.4` and
  `sharp@0.35.4`), so major-upgrade STOP remains in force; no force fix was run.
- Repository secret review found only documented local development example values, empty Dropbox
  values, and test/demo material. No real PostgreSQL, JWT, MinIO, Dropbox/API token or
  real connection-string leak was found. Demo application credentials are an accepted
  requirement.
- Upload/evidence tests prove extension and MIME allowlists, configurable size bound,
  sanitized filenames, authenticated download without JWT in URLs, and no public
  MinIO/internal path or server-generated localhost URL.
- Import and Human Review public serializers return sanitized messages/correlation
  identifiers and serialize tracebacks as null. Full diagnostics remain server-side.

## Known unrelated debt retained

- The full frontend suite still contains previously documented B2A server-dependent
  and stale Human Review E2E expectations outside this prerequisite. The affected
  prerequisite tests above are green.
- Historical backend contract harnesses still contain previously documented stale
  assumptions, and parser suites still require the two unavailable authorized academic
  fixtures. They were not rewritten or fabricated in this prerequisite.
- The three High dependency findings require a separately approved major framework
  upgrade.

## Worktree safety and exclusions

- No commit, stage, reset, checkout, clean, stash, amend or destructive Git operation was performed.
- Existing user-owned changes and thesis artifacts were preserved.
- No general server-side pagination, B2B.3, queue infrastructure, proactive import-module refactor, real scientific-data rewrite or mobile redesign was added.
- Dependency manifests were not changed by this prerequisite.
- Task 11 completed: **FALSE**.
- Task 12 started: **FALSE**.

This checkpoint authorizes no subsequent task by itself.
