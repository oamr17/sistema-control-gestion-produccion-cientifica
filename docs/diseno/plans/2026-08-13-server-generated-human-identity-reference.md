# Server-Generated Human Identity Reference Implementation Plan

> Implement each task in sequence, verify its acceptance criteria, and use the checkboxes (`- [ ]`) to track progress.

**Goal:** Remove the manual identity-reference requirement from person/author approval and correction while preserving explicit identity links.

**Architecture:** The API accepts an absent person/author key only for the existing apply command. The command service materializes an opaque UUID reference before validation and persistence. The frontend sends null and presents a nontechnical explanation.

**Tech Stack:** FastAPI/Pydantic/SQLAlchemy, React/TypeScript, Node test runner, Python unittest.

## Global Constraints

- No schema/model/migration changes.
- No name-derived identity keys, internal IDs, or heuristics.
- `link` continues to require an explicit existing identity reference.
- All generation occurs within the apply transaction.

---

### Task 1: Generate an opaque identity key in the apply command

**Files:**
- Modify: `backend/app/schemas/human_review_api.py`
- Modify: `backend/app/services/human_review_commands.py`
- Test: `backend/tests/test_human_review_b2b2_commands.py`

- [ ] Write a failing command test for null `canonical_identity_key`.
- [ ] Allow null only for person/author payloads.
- [ ] Materialize `human:identity:<uuid4>` before the existing apply validations and persistence.
- [ ] Verify generated key is opaque, persisted, and supplied keys remain unchanged.

### Task 2: Remove the manual approval/correction field

**Files:**
- Modify: `frontend/lib/human-review.ts`
- Modify: `frontend/components/human-review/DecisionComposer.tsx`
- Test: `frontend/tests/human-review-decisions.test.mjs`

- [ ] Write a failing UI/request-builder test for a null person identity key.
- [ ] Send null when no existing key is available and remove the advanced input from approval/correction.
- [ ] Keep the explicit linker untouched.
- [ ] Run focal backend/frontend tests, TypeScript, and build.
