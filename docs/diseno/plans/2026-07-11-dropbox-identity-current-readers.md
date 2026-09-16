# Dropbox Identity And Current Readers Implementation Plan

> Implement each task in sequence, verify its acceptance criteria, and use the checkboxes (`- [ ]`) to track progress.

**Goal:** Reconcile the ten exact Dropbox path/ID pairs and prevent every operational reader from exposing historical imported data.

**Architecture:** An evidence-driven reconciliation command performs one locked PostgreSQL transaction and verifies invariants before commit. `ValidatedReadService` becomes the single current-version boundary for relational reads, while small UI changes expose discarded counts, period cycle fallback, and multi-attribution chart semantics.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, PostgreSQL 16, unittest/pytest, Next.js 14, Docker Compose.

## Global Constraints

- Work only in `C:\Users\OMAR\Desktop\tesis\New project`.
- Modify only ten exact `path_lower` plus `filename` pairs from batches 169 and 170.
- Preserve all history, payloads, traces, normalized data, and foreign-key IDs.
- Use TDD and abort the reconciliation transaction on any failed invariant.

---

### Task 1: Reconciliation Service And Command

**Files:**
- Create: `backend/app/services/dropbox_identity_reconciliation.py`
- Create: `backend/scripts/reconcile_dropbox_identity.py`
- Create: `backend/tests/test_dropbox_identity_reconciliation.py`

**Interfaces:**
- `find_exact_pairs(session, historical_batch_id, current_batch_id) -> list[IdentityPair]`
- `reconcile_exact_pairs(session, pairs, apply=False) -> ReconciliationResult`

- [ ] Write tests for exact-only matching, conflicting `supersedes_id`, rollback, and second-run no-op.
- [ ] Run `python -m pytest tests/test_dropbox_identity_reconciliation.py -v` and confirm failures are caused by the missing service.
- [ ] Implement locked validation, demotion-before-key-change, conditional supersedes linking, and pre-commit invariants.
- [ ] Run the focused tests until green, then run the command without `--apply` and compare its ten rows with the approved dry-run map.

### Task 2: Current-Version Read Boundary

**Files:**
- Modify: `backend/app/services/validated_read_service.py`
- Modify: `backend/tests/test_validated_read_models.py`

**Interfaces:**
- Imported rows are visible only when their joined job is current and successful; rows with no import job remain visible.

- [ ] Add historical/current fixtures with different values for roles, participants, productions, entities, teachers, goals, and progress views.
- [ ] Run focused tests and confirm historical values currently leak.
- [ ] Add reusable current-job predicates/joins to every read method.
- [ ] Run focused tests and the full backend suite.

### Task 3: Discarded Metrics And Period Cycle

**Files:**
- Modify: `backend/app/services/validated_read_service.py`
- Modify: `backend/app/services/kpi_service.py`
- Modify: `backend/app/schemas/kpis.py`
- Modify: `backend/app/schemas/research_entities.py` or the existing research-entity response schema.
- Modify: `backend/tests/test_validated_read_models.py`

- [ ] Add failing tests proving current discarded authors are counted and historical discarded authors are excluded.
- [ ] Add a failing test proving `ResearchEntity.cycle=None` serializes the related period cycle.
- [ ] Implement the persisted discarded-author count and cycle fallback.
- [ ] Run focused and full tests.

### Task 4: Apply And Verify Reconciliation

**Files:**
- Generate: `backend/reports/dropbox_identity_reconciliation_20260711/apply_result.json`
- Generate: `backend/reports/dropbox_identity_reconciliation_20260711/verification.sql`
- Generate: `backend/reports/dropbox_identity_reconciliation_20260711/verification_result.txt`

- [ ] Record before counts and relationship counts for jobs 224-233.
- [ ] Execute `python scripts/reconcile_dropbox_identity.py --historical-batch 169 --current-batch 170 --apply` inside the backend container.
- [ ] Run uniqueness, current-key, historical-row, relationship, revision, and supersedes checks.
- [ ] Run the same command again and require zero modifications.

### Task 5: Endpoint Comparison And Literal Classification

**Files:**
- Generate: `backend/reports/dropbox_identity_reconciliation_20260711/after/*.json`
- Generate: `backend/reports/dropbox_identity_reconciliation_20260711/before_after.json`

- [ ] Query Dashboard, progress records, participants, production, teachers, research entities, goals, and reports with real authentication.
- [ ] Search the seven requested literals and classify each as removed historical data, current parser output, valid-person variant, or invalid text.
- [ ] Record before/after jobs, participants, external researchers, authors, discarded values, products, projects, and chart sum.

### Task 6: Frontend Semantics And Deployment

**Files:**
- Modify: `frontend/app/dashboard/page.tsx`
- Modify: `frontend/app/projects/page.tsx` only if backend cycle fallback is insufficient.
- Test: existing frontend lint/build checks.

- [ ] Update the chart title and supporting explanation for multi-career attribution.
- [ ] Build the frontend and require a successful production build.
- [ ] Rebuild and deploy only the frontend service with Docker Compose.
- [ ] Record BUILD_ID, image date, consumed endpoints, and screenshots of Dashboard, Participants, Projects, and Production.

### Task 7: Final Verification

- [ ] Run the complete backend suite and frontend build/lint.
- [ ] Verify the requested SQL invariants from a fresh database connection.
- [ ] Compare all requested before/after metrics and list remaining invalid names without changing them.
- [ ] Confirm no historical row was deleted and report every modified/generated file.
