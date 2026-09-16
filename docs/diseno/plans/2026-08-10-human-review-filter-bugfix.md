# Human Review Filter Bugfix Implementation Plan

> Implement each task in sequence, verify its acceptance criteria, and use the checkboxes (`- [ ]`) to track progress.

**Goal:** Eliminate the Human Review multiselect crash and expose an explicit accessible link from every queue case to its existing audited detail workflow.

**Architecture:** Preserve the current local filter state and API client. Snapshot selected DOM option values synchronously at each change boundary, then use functional React state updates with plain typed arrays. Extend the queue table only with navigation and pass the existing query loading state down to prevent duplicate submissions.

**Tech Stack:** Next.js 14, React 18, TypeScript, Node test runner, React DOM server tests, Playwright, FastAPI OpenAPI read-only verification.

## Global Constraints

- Modify only `C:\Users\OMAR\Desktop\tesis\New project`.
- Do not modify backend, OpenAPI, query semantics, dates, DecisionComposer, commands, ACL, migration 0021, or create 0022.
- Do not start B2B.3 and do not perform scientific writes.
- Public document filtering remains `document_id`; status and case type remain repeated `status` and `case_type` query parameters.
- Browser plugin is unavailable in this session; use the repository Playwright workflow and temporary Playwright scripts outside the repository.
- The repository has no Git metadata; use before/after SHA-256 hashes and an explicit file inventory instead of commits.

---

### Task 1: Reproduce the deferred React event failure with an automated RED

**Files:**
- Modify: `frontend/tests/human-review-e2e.test.mjs`
- Test: `frontend/tests/human-review-e2e.test.mjs`

**Interfaces:**
- Consumes: the existing mocked Human Review API and real rendered `ReviewQueueFilters` component.
- Produces: a browser regression test that selects status and then case type before applying and asserts the combined repeated query parameters and absence of page errors.

- [ ] **Step 1: Add page-error collection and the failing interaction**

Add a focused test that uses the existing `newPage()` and API fixtures:

```javascript
await page.goto(`${baseUrl}/human-review`);
await page.getByLabel("Estados").selectOption(["pending"]);
await page.getByLabel("Tipos de caso").selectOption(["person_identity"]);
await page.getByRole("button", { name: /Aplicar filtros/i }).click();
assert.deepEqual(new URL(api.queueRequests.at(-1)).searchParams.getAll("status"), ["pending"]);
assert.deepEqual(new URL(api.queueRequests.at(-1)).searchParams.getAll("case_type"), ["person_identity"]);
assert.deepEqual(pageErrors, []);
```

- [ ] **Step 2: Run the focused E2E test and record RED**

Run:

```powershell
node --test --test-name-pattern="status.*case type|combined filters" tests/human-review-e2e.test.mjs
```

Expected: FAIL with the captured client exception `Cannot read properties of null (reading 'selectedOptions')` or the missing combined request caused by that exception.

### Task 2: Capture multiselect values synchronously and add loading protection

**Files:**
- Modify: `frontend/components/human-review/ReviewQueueFilters.tsx`
- Modify: `frontend/app/human-review/page.tsx`
- Modify: `frontend/tests/human-review-queue.test.mjs`
- Test: `frontend/tests/human-review-e2e.test.mjs`
- Test: `frontend/tests/human-review-queue.test.mjs`

**Interfaces:**
- Consumes: `QueueQuery`, `ReviewCaseStatus[]`, `ReviewCaseType[]`, and `queue.isInitialLoading || queue.isUpdating`.
- Produces: `ReviewQueueFilters({ query, onApply, isApplying })` with safe handlers and a non-duplicable Apply submit.

- [ ] **Step 1: Add a loading-state assertion before product code**

Extend the queue component test to render the filter with `isApplying: true` and assert that `Aplicar filtros` is disabled while `Limpiar filtros` remains available.

- [ ] **Step 2: Snapshot selected options before the functional updater**

Implement both handlers in this shape, adapted to the existing types:

```tsx
onChange={(event) => {
  const selectedStatuses = Array.from(
    event.currentTarget.selectedOptions,
    (option) => option.value as ReviewCaseStatus
  );
  setForm((previous) => ({ ...previous, statuses: selectedStatuses }));
}}
```

Use the equivalent captured `ReviewCaseType[]` for `caseTypes`. Do not access the event inside either updater.

- [ ] **Step 3: Wire loading and disable only duplicate Apply submissions**

Add `isApplying?: boolean`, default it to false, pass it from the page, and render:

```tsx
<button type="submit" disabled={isApplying}>...</button>
```

Keep Clear usable and keep the current loading copy elsewhere in the queue.

- [ ] **Step 4: Run focused tests and record GREEN**

Run:

```powershell
node --test tests/human-review-queue.test.mjs
node --test --test-name-pattern="status.*case type|combined filters" tests/human-review-e2e.test.mjs
```

Expected: all selected tests PASS with a combined request containing `status=pending&case_type=person_identity` and no page errors.

### Task 3: Add an explicit accessible case-detail action

**Files:**
- Modify: `frontend/components/human-review/ReviewQueueTable.tsx`
- Modify: `frontend/tests/human-review-queue.test.mjs`
- Modify: `frontend/tests/human-review-e2e.test.mjs`

**Interfaces:**
- Consumes: public queue item `id`.
- Produces: a same-tab Next.js `Link` labeled `Revisar caso` targeting `/human-review/cases/{encoded id}`.

- [ ] **Step 1: Replace the obsolete no-link assertion with a failing navigation contract**

Assert the rendered queue contains header `Acciones`, link text `Revisar caso`, and literal `href="/human-review/cases/case-1"`, with no `target="_blank"` and no internal stable keys.

- [ ] **Step 2: Run the queue test and record RED**

Run:

```powershell
node --test tests/human-review-queue.test.mjs
```

Expected: FAIL because the existing table has no action column or case link.

- [ ] **Step 3: Implement the minimal table link**

Import `Link` from `next/link`, append the `Acciones` header, and render:

```tsx
<Link href={`/human-review/cases/${encodeURIComponent(item.id)}`} className="focus-ring ...">
  Revisar caso
</Link>
```

Do not add row editing, a new tab, or any technical identifiers.

- [ ] **Step 4: Run the queue test and record GREEN**

Run `node --test tests/human-review-queue.test.mjs` and expect all queue tests PASS.

### Task 4: Expand filter, reset, error, date, and detail browser coverage

**Files:**
- Modify: `frontend/tests/human-review-e2e.test.mjs`
- Test: `frontend/tests/human-review-api.test.mjs`
- Test: `frontend/tests/human-review-queue.test.mjs`

**Interfaces:**
- Consumes: the existing mocked queue/detail/capability endpoints and request log.
- Produces: deterministic coverage for all authorized interactions without command confirmation.

- [ ] **Step 1: Cover the complete filter matrix**

Exercise and assert literal repeated parameters for one status, one case type, multiple statuses, multiple case types, combined status/type, clearing, and applying with no filters. Assert every apply starts with `page=1` and does not emit empty or forbidden parameters.

- [ ] **Step 2: Cover dates and local error handling**

Assert both datetime inputs start as empty strings. Fill a literal local datetime and assert the emitted ISO value. Use the existing structured error fixture to assert the safe local alert and correlation ID without a global error or session loss.

- [ ] **Step 3: Cover loading and navigation without writes**

Hold a list response, assert Apply is disabled, release it, then use `Revisar caso` to navigate to the matching detail. Assert `Preparar una decisión` is visible for `RESEARCH_MANAGER`; open preview and cancel, and assert the command request log remains empty.

- [ ] **Step 4: Run related frontend regressions**

Run:

```powershell
node --test tests/human-review-api.test.mjs tests/human-review-queue.test.mjs tests/human-review-detail.test.mjs tests/human-review-decisions.test.mjs tests/human-review-e2e.test.mjs
```

Expected: all related tests PASS with 0 fail, skip, or todo.

### Task 5: Verify the installed behavior and repository invariants

**Files:**
- No product changes.
- Evidence screenshots: `%TEMP%\human-review-filter-bugfix\` outside the repository.

**Interfaces:**
- Consumes: the rebuilt frontend, live read-only backend endpoints, and repository files.
- Produces: fresh completion evidence and SHA-256 inventory.

- [ ] **Step 1: Run the complete frontend suite**

Run `pnpm test` and require at least 58 plus the new tests, with 0 failures, skips, or todos.

- [ ] **Step 2: Run TypeScript and the production build**

Run `pnpm exec tsc --noEmit` and `pnpm build`; require exit code 0.

- [ ] **Step 3: Rebuild the frontend container only if necessary and run live Playwright QA**

At exact `http://localhost:3000/human-review`, verify desktop 1440x1000 and mobile 390x844: initial list, empty dates, single and combined filters, reset, action link, detail, DecisionComposer, preview/cancel, zero scientific command requests, zero relevant console/page/hydration errors, no framework overlay, and no viewport overflow. Save screenshots only under `%TEMP%\human-review-filter-bugfix\`.

- [ ] **Step 4: Verify read-only contracts and invariants**

Read the live OpenAPI and require 8 Human Review paths plus the exact list query parameters. Verify `/human-review/me` still returns the approved Research Manager actions. Hash migration 0021, assert no 0022 file, compare backend tree hashes against the pre-change inventory, and assert B2B.3 files were not created.

- [ ] **Step 5: Clean up and inspect the final change inventory**

Remove temporary listeners/processes created for tests, retain only requested external screenshots, confirm no disposable containers or volumes were introduced, compute final hashes for every modified frontend/doc file, and confirm backend is byte-for-byte unchanged.

