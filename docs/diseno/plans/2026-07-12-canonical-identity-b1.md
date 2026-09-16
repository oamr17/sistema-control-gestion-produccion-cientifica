# Canonical Identity B1 Implementation Plan

> Implement each task in sequence, verify its acceptance criteria, and use the checkboxes (`- [ ]`) to track progress.

**Goal:** Add reversible canonical identity fields, capture a restorable snapshot, persist the approved 163-row canonical backfill, and prove idempotency and data invariants.

**Architecture:** The project uses its own ordered `schema_migrations` runner rather than Alembic CLI. Revision `20260712_0016_canonical_identity_fields` will therefore expose the existing `VERSION/upgrade/downgrade` interface and explicit `revision/down_revision` metadata. A guarded backfill command will reuse the approved pure preflight resolver, lock all target rows in one PostgreSQL transaction, compare exact approved metrics, and write only canonical columns. An external JSON snapshot plus the verified PostgreSQL custom-format backup provide row-level and database-level restoration.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0, PostgreSQL 16, unittest, Docker Compose, pg_dump/pg_restore.

## Global Constraints

- Work only in `C:\Users\OMAR\Desktop\tesis\New project`.
- B1 only: no geometry integration, frontend change, persistent PDF reprocessing, or human-review module.
- The prior chain head is `20260711_0015_dropbox_revision_uniqueness`; the new revision is `20260712_0016_canonical_identity_fields` with that exact down revision.
- Do not modify raw names, original person keys, import/production links, payloads, traces, validation/KPI states, or locked human decisions.
- Dry-run must reproduce 85 to 54 identities, 122 to 83 participations, 3 automatic groups, 53 pending rows, 120 roles, 43 authors, and KPI 2 to 2.
- Only Zambrano, Merchan, and Sanchez may be automatic merge groups. Delgado and Ramirez remain pending.
- Apply is one transaction with row locks; any metric, lock, immutable-hash, or cross-table mismatch raises and rolls back.
- A second identical apply reports zero changed rows.

---

### Task 1: Reversible Revision 0016 And ORM Contract

**Files:**
- Create: `backend/app/migrations/versions/20260712_0016_canonical_identity_fields.py`
- Modify: `backend/app/core/migrations.py`
- Modify: `backend/app/models/entities.py`
- Create: `backend/tests/test_canonical_identity_migration.py`

**Interfaces:**
- Migration exports `VERSION`, `revision`, `down_revision`, `upgrade(engine)`, and `downgrade(engine)`.
- Both ORM models expose the eight canonical/manual fields.

- [ ] Write migration-shape tests first for both tables, both indexes, exact revision metadata, and symmetric downgrade.
- [ ] Run the tests and confirm failure because revision 0016 and model fields do not exist.
- [ ] Add nullable canonical fields, non-null `identity_locked` with false default, and canonical-key indexes to both tables.
- [ ] Register revision 0016 after revision 0015 in the custom runner.
- [ ] Run focused tests until green.
- [ ] Restore the verified pre-B1 backup into a disposable database, run upgrade, downgrade, and upgrade again, and verify raw row counts/hashes are unchanged.

### Task 2: Auditable Snapshot And Transactional Backfill

**Files:**
- Create: `backend/scripts/backfill_canonical_identity.py`
- Create: `backend/tests/test_canonical_identity_backfill.py`

**Interfaces:**
- `build_snapshot(connection, dataset, decisions) -> dict` records all 163 targets, prior canonical values, immutable source fields, per-row evidence hashes, and global hashes.
- `verify_snapshot(connection, snapshot) -> None` rejects any source/evidence mismatch.
- CLI modes are `--dry-run`, `--snapshot-only`, `--apply`, and `--restore-snapshot`.
- Apply requires `--snapshot-path` and `--backup-path`; restore requires the same snapshot and explicit confirmation.

- [ ] Write failing tests for snapshot completeness, exact restoration fields, metric gate, allowed merge gate, lock preservation/conflict, transaction rollback, immutable fields, and second-run idempotency.
- [ ] Confirm failures are due to the missing backfill command.
- [ ] Implement dry-run by calling the approved preflight dataset/simulation functions.
- [ ] Implement deterministic JSON snapshot creation with SHA-256 manifest and 120 role plus 43 author entries.
- [ ] Implement snapshot verification and restoration of canonical/manual fields only.
- [ ] Implement PostgreSQL `FOR UPDATE` locking and one-transaction apply with post-write invariant checks.
- [ ] Run focused tests until green.

### Task 3: Development Dry-Run, Snapshot, And Migration Gate

**Files:**
- Generate: `backend/reports/canonical_identity_b1_<timestamp>/`

**Interfaces:**
- Operator artifacts include chain, backup manifest, dry-run JSON, snapshot JSON/hash, migration upgrade/downgrade output, and SQL invariants.

- [ ] Run the five Dropbox PostgreSQL integrations with `DROPBOX_VERSIONING_TEST_DATABASE_URL`; stop on failure.
- [ ] Record chain head 0015 and the 16 applied historical versions.
- [ ] Verify the custom-format backup by restoring it and comparing counts.
- [ ] Run revision 0016 upgrade/downgrade/upgrade on the restored disposable database.
- [ ] Run development dry-run and require the exact approved metrics and merge names.
- [ ] Apply revision 0016 to development only after all previous gates pass.
- [ ] Create the 163-row snapshot before any canonical update and verify its manifest.

### Task 4: Apply, Idempotency, And Database Invariants

**Files:**
- Generate: B1 apply, second-run, SQL, and hash reports in the delivery directory.

**Interfaces:**
- First apply returns exact target count and changed count.
- Second apply returns `changed_rows=0`.

- [ ] Capture immutable before hashes for raw data, jobs, payloads, traces, evidence, validation, and KPI fields.
- [ ] Execute guarded apply in one transaction with the verified snapshot and backup.
- [ ] Query the 163 operational rows and verify 54 equivalent canonical keys, three automatic groups, 53 pending rows, zero consistency errors, KPI 2, and external counts 5/1/4.
- [ ] Compare immutable hashes before and after.
- [ ] Execute apply a second time and require zero changes.
- [ ] Prove the snapshot restore command on a disposable restored database, not development.

### Task 5: Full Verification And B1 Delivery Gate

**Files:**
- Generate: `backend/reports/canonical_identity_b1_<timestamp>/b1_delivery_report.md`

**Interfaces:**
- Final report links every generated artifact and records commands/results.

- [ ] Run full backend unittest discovery with the disposable PostgreSQL URL so all five integrations execute.
- [ ] Run `python -m compileall app scripts tests`.
- [ ] Query the existing backend endpoints and compare response shapes before/after without changing their contracts.
- [ ] Review changed files for prohibited geometry/frontend/reprocessing/review-module work.
- [ ] Run an independent final code/spec review and resolve every important finding.
- [ ] Deliver migration identity/chain, backup/hash, snapshot, dry-run/apply/idempotency, SQL, upgrade/downgrade, hash comparison, and limitations; stop before B2.
