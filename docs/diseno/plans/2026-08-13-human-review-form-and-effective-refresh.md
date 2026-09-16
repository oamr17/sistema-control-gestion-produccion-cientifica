# Human Review Form and Effective Refresh Implementation Plan

> Implement each task in sequence, verify its acceptance criteria, and use the checkboxes (`- [ ]`) to track progress.

**Goal:** Make the human-review form preserve optional multi-word aliases, mark required fields, and refresh mounted effective-data readers after a successful B2B.2 command.

**Architecture:** Keep request sanitization as the authority for aliases. Add presentational required markers based on existing validation rules. Publish one browser-local event after command invalidation; existing reader pages subscribe and refetch their current public API query.

**Tech Stack:** Next.js 14, React, TypeScript, Node test runner.

## Global Constraints

- No backend, API, schema, migration, source-scientific-data, parser/OCR, Dropbox, n8n, or B2B.3 changes.
- `aliases` is optional and request builders remain unchanged.
- Effective values continue to come only from existing validated readers.

---

### Task 1: Optional aliases and required indicators

**Files:**
- Modify: `frontend/components/human-review/DecisionComposer.tsx`
- Modify: `frontend/components/human-review/GuidedDecisionEditor.tsx`
- Test: `frontend/tests/human-review-decisions.test.mjs`

**Interfaces:**
- Consumes: `DecisionPayload`, `validateSessionDraft` and existing request sanitization.
- Produces: form markup where multi-word aliases are retained locally and only required controls display `*`.

- [ ] **Step 1: Write failing tests**

```js
assert.equal(aliasInput.props.value, "Ana María, María José");
assert.match(markup, /Nombre canónico \*/);
assert.doesNotMatch(aliasLabel, /\*/);
```

- [ ] **Step 2: Run RED**

Run: `node --test --test-name-pattern "aliases|required" tests/human-review-decisions.test.mjs`

- [ ] **Step 3: Implement the smallest UI changes**

```tsx
onChange={(event) => onChange({ ...payload, aliases: event.target.value.split(",") })}
```

Add required asterisks only to fields that existing validation requires.

- [ ] **Step 4: Run GREEN**

Run the same command and require zero failures.

### Task 2: Effective-data refresh event

**Files:**
- Modify: `frontend/lib/human-review-api.ts`
- Modify: `frontend/app/teachers/page.tsx`
- Modify: `frontend/app/production/page.tsx`
- Modify: `frontend/app/projects/page.tsx`
- Test: `frontend/tests/human-review-api.test.mjs`
- Test: `frontend/tests/human-review-e2e.test.mjs`

**Interfaces:**
- Produces: a browser-local `human-review:effective-data-changed` event after successful apply/discard/revert.
- Consumes: existing endpoint fetch functions and current selected period/career filters.

- [ ] **Step 1: Write failing tests**

```js
assert.equal(refetches.participants, 1);
assert.equal(refetches.production, 1);
assert.equal(refetches.projects, 1);
```

- [ ] **Step 2: Run RED**

Run: `node --test --test-name-pattern "effective.*refresh|effective data" tests/human-review-api.test.mjs tests/human-review-e2e.test.mjs`

- [ ] **Step 3: Implement local publication and mounted-page listeners**

```ts
window.dispatchEvent(new Event("human-review:effective-data-changed"));
```

Each page reuses its current fetch routine from a `useCallback` listener.

- [ ] **Step 4: Run GREEN**

Run the same command and require zero failures.

### Task 3: Regression and desktop QA

**Files:**
- Test: `frontend/tests/human-review-decisions.test.mjs`
- Test: `frontend/tests/human-review-api.test.mjs`
- Test: `frontend/tests/human-review-e2e.test.mjs`

- [ ] **Step 1: Run focused regressions**

Run: `node --test tests/human-review-decisions.test.mjs tests/human-review-api.test.mjs tests/human-review-e2e.test.mjs`

- [ ] **Step 2: Compile and build**

Run: `npx tsc --noEmit` then `npm run build` from `frontend`.

- [ ] **Step 3: Desktop QA**

Use Playwright because Browser plugin is unavailable. Verify aliases with spaces, optional aliases, required indicators, and a command-triggered effective-data refresh. Do not perform mobile-specific work.
