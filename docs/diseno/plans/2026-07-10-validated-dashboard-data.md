# Validated Dashboard Data Implementation Plan

> Implement each task in sequence, verify its acceptance criteria, and use the checkboxes (`- [ ]`) to track progress.

**Goal:** Make dashboard and progress records read persisted validated data while fixing author parsing, page continuity, Excel partial matching, and unsafe surname merges.

**Architecture:** Complete validation status on existing normalized models, then add one relational read service consumed by both API surfaces. Preserve parsed JSON only for audit/reprocessing and keep parser/identity changes isolated behind focused regression tests.

**Tech Stack:** Python 3.13, unittest, SQLAlchemy 2.0, FastAPI, PostgreSQL migrations.

## Global Constraints

- Work only in `C:\Users\OMAR\Desktop\tesis\New project`.
- Main operational counts accept only `validation_status = 'validated'`.
- `/dashboard` and `/progress-records` must not inspect `parsed_payload`.
- Exact identity/Excel-record matches are authoritative; heuristic merges require two matching surname tokens.
- Git commands are unavailable in this environment, so verification replaces per-task commits.

---

### Task 1: Parser Author Validation And Page Continuity

**Files:**
- Modify: `backend/tests/test_pdf_parser.py`
- Modify: `backend/app/services/pdf_parser/pdf_text_extractor.py`
- Modify: `backend/app/services/pdf_parser/section_detector.py`
- Modify: `backend/app/services/pdf_parser/table_parser.py`

**Interfaces:**
- Consumes: `parse_progress_report(text)` and `extract_pdf_text(content)`.
- Produces: page markers `[[PAGE_BREAK:N]]` and label-value authors validated before accumulation.

- [ ] Add a test where an `AUTOR 1` value is title text and assert it is absent from `authors` and normalized participants.
- [ ] Add a Zambrano 7-to-8 test with an incomplete title, page boundary, repeated `TITULO`, continuation, author, and status; assert one product with the joined title.
- [ ] Run `python -m unittest tests.test_pdf_parser -v` and confirm both new tests fail for the expected accumulation/splitting behavior.
- [ ] Add `looks_like_person_name(cleaned_author)` to the label-value append condition.
- [ ] Emit and recognize `[[PAGE_BREAK:N]]`; skip the repeated title header only while a pre-boundary row has no author/status/tail.
- [ ] Re-run `python -m unittest tests.test_pdf_parser -v` and confirm all parser tests pass.

### Task 2: Excel Prefix/Subset Matching And Surname-Safe Heuristics

**Files:**
- Modify: `backend/tests/test_investigator_seed.py`
- Modify: `backend/tests/test_pdf_parser.py`
- Modify: `backend/app/services/investigator_seed.py`
- Modify: `backend/app/services/participant_identity.py`
- Modify: `backend/app/services/pdf_parser/participants.py`
- Modify: `backend/app/services/import_service.py`

**Interfaces:**
- Produces: `strict_name_prefix` and `name_token_subset` seed match types.
- Produces: `_has_two_matching_surnames(left, right) -> bool` for non-authoritative merge gates.

- [ ] Add seed tests for a strict normalized prefix and a candidate whose tokens are a proper subset despite a low flat length ratio.
- [ ] Add participant/import matching tests proving `Ana Paredes Ruiz` and `Maria Paredes Lopez` do not merge or become a pending merge from the single shared surname.
- [ ] Run the focused seed/parser tests and confirm the new assertions fail.
- [ ] Rank unique multi-token prefix/subset matches above sequence ratio in `match_name()`.
- [ ] Gate participant cluster matching, parser registry fuzzy/partial matching, and persisted token matching with two shared trailing surname tokens unless exact token sets or authoritative seed identity agree.
- [ ] Re-run focused tests and the complete parser/seed suites.

### Task 3: Persisted Validation Contract And Backfill

**Files:**
- Create: `backend/app/migrations/versions/20260710_0013_teacher_person_role_validation.py`
- Modify: `backend/app/core/migrations.py`
- Modify: `backend/app/models/entities.py`
- Modify: `backend/app/services/import_service.py`
- Create: `backend/tests/test_validated_read_models.py`

**Interfaces:**
- Adds `Teacher.validation_status: str` and `PersonRole.validation_status: str`.
- Extends `_record_person_role(..., validation_status: str | None = None)`.

- [ ] Add model/import tests asserting confident linked roles are `validated`, weak/unresolved roles are pending, and product roles inherit author status.
- [ ] Run the new test module and confirm missing columns/status behavior fails.
- [ ] Add indexed model columns with default `validated` for teachers and `pending_review` for roles.
- [ ] Implement migration DDL and deterministic updates for existing rows.
- [ ] Derive role status from explicit author status or linked identity plus confidence at least `0.90`.
- [ ] Re-run the new tests and migration import checks.

### Task 4: Shared Relational Read Service

**Files:**
- Create: `backend/app/services/validated_read_service.py`
- Modify: `backend/tests/test_validated_read_models.py`

**Interfaces:**
- Produces `ValidatedReadService(db)`.
- Produces `progress_view(row, trace) -> dict` and period/job-scoped query helpers for roles, productions/authors, and entities.

- [ ] Build an in-memory fixture whose `parsed_payload` contradicts persisted rows.
- [ ] Assert only validated relational teachers, roles, authors, productions, and entities appear in the service output.
- [ ] Run the new test and confirm it fails before the service exists.
- [ ] Implement batched relational queries, participant aggregation, product hydration, entity hydration, and progress summary serialization without reading JSON.
- [ ] Re-run the test and inspect SQL query count for accidental per-row loading.

### Task 5: Dashboard And Progress Routes Use Relational Data

**Files:**
- Modify: `backend/app/services/kpi_service.py`
- Modify: `backend/app/api/v1/endpoints/imports.py`
- Modify: `backend/tests/test_validated_read_models.py`

**Interfaces:**
- Consumes: `ValidatedReadService` from Task 4.
- Preserves: `DashboardKpi` and `ImportedProgressReportRead` response shapes.

- [ ] Add tests that poison `parsed_payload` with extra people/products/entities and assert dashboard/progress ignore them.
- [ ] Run the tests and confirm current JSON-based readers fail.
- [ ] Replace KpiService payload helpers with normalized period/job queries and validated status filters.
- [ ] Change `/progress-records` to hydrate each response from the shared relational read service; load trace source metadata without `parsed_payload`.
- [ ] Run the new relational tests, then `python -m unittest discover -s tests -v`.

### Task 6: Final Verification

**Files:**
- Review all modified backend files and migration registration.

- [ ] Run `python -m unittest discover -s tests -v` and require zero failures.
- [ ] Run `python -m compileall app tests` and require exit code 0.
- [ ] Search `kpi_service.py` and the `/progress-records` serialization path for `parsed_payload`; require no operational read.
- [ ] Inspect the final diff using available filesystem comparisons because the Git executable is unavailable.
