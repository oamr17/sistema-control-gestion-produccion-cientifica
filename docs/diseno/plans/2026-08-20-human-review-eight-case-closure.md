# Human Review Eight-Case Closure Implementation Plan

> Implement each task in sequence, verify its acceptance criteria, and use the checkboxes (`- [ ]`) to track progress.

**Goal:** Close the confirmed human-review defects while preserving the definitive inventory of eight review case types and the effective-data/KPI projection contract.

**Architecture:** Keep review commands authoritative in the backend. The frontend must retain a successful local result before cache invalidation can replace the session. Compatibility-only case types must be classified from current producers and read models, then made non-actionable without being silently lost from traceability.

**Tech Stack:** FastAPI, SQLAlchemy/PostgreSQL, Next.js/React, Node test runner, Python unittest, Playwright.

**Spec:** Requisitos de cierre de los ocho casos de revisión humana, proporcionados por el responsable del proyecto.

## Global Constraints

- Preserve all eight `ReviewCaseType` values in API, database and frontend type contracts.
- Run against an isolated PostgreSQL database only; do not query, truncate or modify `science_faculty`.
- Use a focused RED/GREEN cycle for each defect before relevant regressions.
- Preserve current validated readers as the only source for operational modules and KPI calculations.

---

### Task 1: Preserve command success through cache invalidation

**Files:**
- Modify: `frontend/components/human-review/ReviewSessionPage.tsx`
- Test: `frontend/tests/human-review-e2e.test.mjs`

- [ ] Add a failing rendered regression: a successful single confirmation remains visible after the command invalidates review cache, and one click emits one POST.
- [ ] Run the focused test and confirm it fails for the missing success state.
- [ ] Apply the smallest ordering change so the result/message is committed before the detail cache is refreshed.
- [ ] Re-run the focused UI test plus API/cache tests and inspect the request count.

### Task 2: Classify compatibility-only case types without losing traceability

**Files:**
- Modify: `backend/app/services/human_review_queries.py`
- Modify: `frontend/components/human-review/ReviewQueueFilters.tsx`
- Test: `backend/tests/test_human_review_b2b2_queries.py`
- Test: `frontend/tests/human-review-queue.test.mjs`

- [ ] Add failing tests showing actionable defaults exclude `invalid_text` and `new_evidence_conflict`, while explicit type/status filters still return them with audit/detail visibility.
- [ ] Run focused backend/frontend tests and confirm the current actionable queue includes these compatibility-only types.
- [ ] Add the minimal query and UI default-filter rule; do not remove either enum value or its historic records.
- [ ] Re-run tests and inspect isolated PostgreSQL queue/audit results.

### Task 3: Make filter reset and source revision contract exact

**Files:**
- Modify: `frontend/components/human-review/ReviewQueueFilters.tsx`
- Modify: `backend/app/services/human_review_queries.py`
- Test: `frontend/tests/human-review-queue.test.mjs`
- Test: `backend/tests/test_human_review_b2b2_queries.py`

- [ ] Add failing tests for an empty reset and exact `source_revision` matching combined with status/type filters.
- [ ] Verify RED against the current reset and partial match behavior.
- [ ] Implement the smallest consistent state/query change.
- [ ] Verify focused frontend/backend tests and PostgreSQL explain/query behavior.

### Task 4: Reopen, duplicate resolutions and E2E contract

**Files:**
- Modify: `frontend/tests/human-review-e2e.test.mjs`
- Test: `backend/tests/test_human_review_b2b2_commands.py`
- Test: `backend/tests/test_human_review_b2b2_reversal.py`
- Test: `backend/tests/test_human_review_b2b2_e2e.py`

- [ ] Replace stale E2E assertions with assertions on current visible header/audit content and success state.
- [ ] Run the focal test to demonstrate it fails only for the obsolete assertion or missing success behavior.
- [ ] Verify merge, maintained-separate and separated resolution/reversion plus reopened editability in the isolated database.
- [ ] Run the E2E and relevant backend regressions serially.

### Task 5: Verify effective propagation and publish the eight-row matrix

**Files:**
- Test: `backend/tests/test_human_review_b2b2_readers.py`
- Test: `backend/tests/test_human_review_b2b2_kpi.py`
- Test: `frontend/tests/human-review-e2e.test.mjs`

- [ ] Run only the propagation tests after command/UI changes.
- [ ] Verify current decision, overrides, audit append-only history, readers, dashboard/KPI and no historical duplicate count.
- [ ] Report the fixed map of all eight case types using only `CORREGIDO Y VERIFICADO`, `PENDIENTE` or `NO APLICA`.
