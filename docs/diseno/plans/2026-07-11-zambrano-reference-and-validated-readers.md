# Zambrano Reference And Validated Readers Implementation Plan

> Implement each task in sequence, verify its acceptance criteria, and use the checkboxes (`- [ ]`) to track progress.

**Goal:** Make the real Zambrano PDF produce the five independently verified scientific products exactly, preserve clean non-table wrapped titles, and route all live participant/product readers through validated relational queries.

**Architecture:** Keep PDF extraction as source evidence, but make the scientific-table parser own row and column boundaries: URL continuation stays in the link column, title fragments are rejoined with controlled mid-word repair, and generic fallback candidates cannot override structured rows. Extend `ValidatedReadService` with scoped list/read projections and make the five remaining live endpoints delegate to it. No database DDL is required.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy, PyMuPDF, unittest.

## Global Constraints

- Work only in `C:\Users\OMAR\Desktop\tesis\New project`.
- Use the real Zambrano PDF as a regression fixture and compare titles, authors, and links exactly.
- Do not create `extracted_records` or `extracted_record_values`.
- Do not execute schema changes against the development database.

---

### Task 1: Real PDF parser contract

**Files:**
- Create: `backend/tests/fixtures/zambrano_fci021_2025.pdf`
- Modify: `backend/tests/test_pdf_parser.py`
- Modify: `backend/app/services/pdf_parser/table_parser.py`

- [ ] Add a real-PDF test asserting all five exact titles, authors, and links plus the non-table project title.
- [ ] Run it red and record title/link/duplicate differences.
- [ ] Repair wrapped-word joins, URL continuation, and structured-vs-fallback candidate selection.
- [ ] Run the real fixture and parser suite green.

### Task 2: Validated relational readers

**Files:**
- Modify: `backend/app/services/validated_read_service.py`
- Modify: `backend/app/api/v1/endpoints/participants.py`
- Modify: `backend/app/services/production_service.py`
- Modify: `backend/app/services/teacher_service.py`
- Modify: `backend/app/services/research_entity_service.py`
- Modify: `backend/app/api/v1/endpoints/goals.py`
- Modify: `backend/tests/test_validated_read_models.py`

- [ ] Add failing tests proving invalid relational rows and poisoned `parsed_payload` are excluded.
- [ ] Add scoped validated list/projection methods to `ValidatedReadService`.
- [ ] Delegate the five live endpoints/services to those methods while preserving response shapes and authorization scope.
- [ ] Run focused and full tests green.

### Task 3: Identity and live-screen audit

**Files:**
- Read only: Dropbox fingerprint/import code and frontend API consumers.

- [ ] Document rename/new-revision behavior for `document_key` and versioning.
- [ ] Verify which review/audit states are consumed by live frontend screens.
- [ ] Run compileall and the full backend suite before reporting completion.
