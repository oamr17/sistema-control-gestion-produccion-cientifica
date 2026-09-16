# Production Blockers Verification Implementation Plan

> Implement each task in sequence, verify its acceptance criteria, and use the checkboxes (`- [ ]`) to track progress.

**Goal:** Verify Dropbox document-version edge cases, expose persisted pending participants without affecting KPIs, validate real OCR, and produce an end-to-end report for the ten current PDFs.

**Architecture:** Keep the existing `document_key` and `supersedes_id` design. Add database-backed serialization for revision admission, derive participant presentation fields from persisted validation state, and exercise the deployed Docker stack against isolated PostgreSQL copies so test revisions never pollute development data.

**Tech Stack:** FastAPI, SQLAlchemy, PostgreSQL 16, Pydantic, Docker Compose, PyMuPDF, Tesseract 5, Python unittest.

## Global Constraints

- Work only in `C:\Users\OMAR\Desktop\tesis\New project`.
- Do not replace the existing Dropbox versioning design.
- Do not delete records or change expected values to match current output.
- Pending records remain visible but never KPI eligible; discarded records remain hidden.
- Real-PDF and real-endpoint verification is required in addition to unit tests.

---

### Task 1: Dropbox Revision Invariants

**Files:**
- Modify: `backend/app/services/import_batching.py`
- Modify: `backend/app/api/v1/endpoints/imports.py`
- Create: `backend/app/migrations/versions/20260711_0015_dropbox_revision_uniqueness.py`
- Modify: `backend/app/core/migrations.py`
- Test: `backend/tests/test_dropbox_versioning_integration.py`

**Interfaces:**
- Consumes: `dropbox_document_key(metadata)`, `dropbox_fingerprint(metadata)`, `promote_document_version(db, job)`.
- Produces: database-serialized admission for one non-failed job per `document_key + rev`, while preserving skipped audit jobs.

- [ ] Write integration tests for cases A-H, including simultaneous requests and failed-version promotion.
- [ ] Run the tests and confirm the concurrency and uniqueness cases fail with the current implementation.
- [ ] Add an idempotent partial unique index for non-skipped, non-error `document_key + source_rev` rows.
- [ ] Serialize revision admission in PostgreSQL and convert uniqueness races into `SKIPPED` jobs.
- [ ] Keep `content_hash` audit-only and prove new `rev` remains the authoritative version boundary.
- [ ] Run focused and full backend suites.

### Task 2: Persisted Pending Participants

**Files:**
- Modify: `backend/app/services/validated_read_service.py`
- Test: `backend/tests/test_validated_read_models.py`
- Test: `backend/tests/test_zambrano_real_pdf.py`

**Interfaces:**
- Consumes: `PersonRole.validation_status`, linked identity state, role metadata, and real parser output.
- Produces: `normalized_participants` with derived `validation_status`, `review_bucket`, `show_in_participants`, and `kpi_eligible`.

- [ ] Add failing tests for validated, pending-review, and discarded roles.
- [ ] Load pending roles for `progress_view` while preserving validated-only KPI queries.
- [ ] Map persisted states to UI fields without hardcoded all-valid output.
- [ ] Recompute participant summary from actual participant states.
- [ ] Verify Jeniffer Marcillo Chasy, invalid fragments, and five Zambrano products against the real PDF.

### Task 3: OCR Runtime And Failure Semantics

**Files:**
- Modify if required: `backend/Dockerfile`
- Modify if required: `backend/app/services/pdf_parser/pdf_text_extractor.py`
- Modify if required: `backend/app/services/import_service.py`
- Test: `backend/tests/test_import_batch_flow.py`

**Interfaces:**
- Consumes: Docker Tesseract languages and `PdfTextExtraction.page_logs`.
- Produces: extracted OCR payload or explicit `REQUIRES_REVIEW`/operational error, never zero-content `SUCCESS`.

- [ ] Verify `tesseract`, `spa`, and `eng` inside the deployed backend container.
- [ ] Add a regression test that OCR failure with no text cannot finalize as `SUCCESS`.
- [ ] Reprocess the real Family E PDF and record characters, sections, entities, pending items, and errors.

### Task 4: Clean End-To-End Import

**Files:**
- Create: `backend/reports/production_readiness_20260711.json`
- Create: `backend/reports/production_readiness_20260711.md`

**Interfaces:**
- Consumes: ten Dropbox metadata records and deployed API endpoints.
- Produces: per-document extraction and visibility counts plus invariant SQL evidence.

- [ ] Clone a clean seeded database and run the ten-PDF batch through the deployed endpoint.
- [ ] Wait for all background jobs and collect per-document participant, product, project, and error counts.
- [ ] Call Dashboard, Participantes, Produccion, Proyectos, Docentes, and Importaciones/revision endpoints with real authentication.
- [ ] Record before/after counts, migrations, SQL checks, pending cases, and known limitations.
- [ ] Remove only the isolated verification container/database, leaving development data untouched.
- [ ] Run compile, complete backend tests, and final invariant queries.
