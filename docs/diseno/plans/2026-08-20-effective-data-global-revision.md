# Effective Data Global Revision Implementation Plan

> Implement each task in sequence, verify its acceptance criteria, and use the checkboxes (`- [ ]`) to track progress.

**Goal:** Refresh all mounted effective-data modules across authenticated browser sessions after a committed Human Review decision.

**Architecture:** A lightweight authenticated backend endpoint exposes a monotonic revision calculated from committed review decisions. The data-cache provider has one visibility-aware polling lifecycle; it publishes the existing effective-data event only after observing a changed revision, allowing existing readers to invalidate/refetch without fetching KPI data during polling.

**Tech Stack:** FastAPI, SQLAlchemy/PostgreSQL, Next.js 14, React 18, Node test runner, Python unittest, Chrome QA.

**Spec:** `docs/diseno/specs/2026-08-20-effective-data-global-revision-design.md`

## Global Constraints

- Revision changes must only be observable after the command transaction commits.
- The endpoint is authenticated and returns only the numeric revision.
- Poll at three seconds and immediately on focus/visibility recovery.
- Equal revisions do not invalidate effective-data caches.
- Preserve local successful Human Review confirmation state.
- Do not use WebSockets or SSE.

---

### Task 1: Committed revision endpoint

**Files:**
- Modify: `backend/app/api/v1/endpoints/human_review.py`
- Test: `backend/tests/test_human_review_b2b2_api.py`

**Interfaces:**
- Produces: `GET /human-review/effective-data-revision -> { revision: int }` for authenticated users.

- [ ] **Step 1: Write failing tests** for an authenticated revision response, an increment after `apply_decision` commits, and no increment when a command transaction rolls back.
- [ ] **Step 2: Run the focused backend tests** and verify the route is absent.
- [ ] **Step 3: Implement the minimal endpoint** by counting persisted `ReviewDecision` rows with `get_current_user` authentication.
- [ ] **Step 4: Re-run the focused backend tests** and verify they pass.

### Task 2: Single revision polling lifecycle

**Files:**
- Create: `frontend/lib/effective-data-revision.ts`
- Modify: `frontend/lib/api.ts`
- Modify: `frontend/lib/data-cache.tsx`
- Test: `frontend/tests/human-review-api.test.mjs`

**Interfaces:**
- Consumes: `api.effectiveDataRevision(): Promise<{ revision: number }>`.
- Produces: `startEffectiveDataRevisionPolling({ readRevision, onChanged })`, a cleanup function with one timer and immediate visibility/focus checks.

- [ ] **Step 1: Write failing tests** proving unchanged revisions do not refresh, a changed revision refreshes once, focus checks immediately, failures retry, and cleanup removes duplicate polling.
- [ ] **Step 2: Run the focused frontend test** and verify the helper is unavailable.
- [ ] **Step 3: Implement the helper and provider integration** so only changed revisions publish the existing global effective-data event.
- [ ] **Step 4: Re-run the focused frontend test** and verify it passes.

### Task 3: Cache fan-out regression

**Files:**
- Modify: `frontend/lib/data-cache.tsx`
- Modify: `frontend/tests/human-review-api.test.mjs`

**Interfaces:**
- Consumes: `human-review:effective-data-changed`.
- Produces: invalidated dashboard, participant, production, project and entity cache prefixes.

- [ ] **Step 1: Write failing cache assertions** for every affected prefix and one notification per revision change.
- [ ] **Step 2: Run the test** and verify missing prefixes fail.
- [ ] **Step 3: Add the scoped invalidations** while retaining the existing local Human Review result state.
- [ ] **Step 4: Re-run focused regressions**.

### Task 4: Verify end-to-end propagation

**Files:**
- Test: `backend/tests/test_human_review_b2b2_e2e.py`
- Test: `frontend/tests/human-review-e2e.test.mjs`

- [ ] **Step 1: Add failing regression coverage** for User A commit, User B revision detection, and refetch of current Dashboard/KPI data.
- [ ] **Step 2: Run the tests** and observe the cross-session propagation failure.
- [ ] **Step 3: Use the completed endpoint and polling lifecycle; do not add a second invalidation path.**
- [ ] **Step 4: Run backend/frontend focused suites, typecheck and build.**
- [ ] **Step 5: Validate in Chrome with two sessions** and record the elapsed propagation time plus screenshot/DOM evidence.
