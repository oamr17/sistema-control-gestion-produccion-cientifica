# Canonical Identities And Geometric Production Implementation Plan

> Implement each task in sequence, verify its acceptance criteria, and use the checkboxes (`- [ ]`) to track progress.

**Goal:** Persist stable canonical identity metadata across roles and scientific authors, recover production-table columns geometrically, and expose unique participants and reviewable products without changing KPI ground truth.

**Architecture:** A pure `CanonicalIdentityResolver` produces opaque stable keys and auditable decisions from immutable evidence. A dry-run preflight executes this resolver against current data without schema or data writes. After a separate authorization gate, additive schema fields, geometry-scoped parsing, relational read models, UI changes, and a transactional backfill are applied.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, PostgreSQL 16, PyMuPDF, Pydantic, unittest, Next.js 14, React, TypeScript.

## Global Constraints

- Work only in `C:\Users\OMAR\Desktop\tesis\New project`.
- Do not modify Dropbox identity, document versioning, reconciliation, or current-version rules.
- Do not apply a migration or backfill before the final authorization gate.
- Preserve `raw_name`, source `person_key`, PDF text, `import_job_id`, traces, parsed payloads, evidence, and every source row ID.
- Never expose identity numbers, names, or emails inside canonical keys.
- Never auto-resolve an isolated prefix; require one unique candidate, threshold, corroboration, and no competitor.
- Manual locked decisions always outrank automatic decisions and survive reprocessing.
- Pending keys are document-scoped and cannot merge ambiguous identities across documents.
- Personal products come only from `scientific_production_authors.production_id`.
- KPI eligibility remains product `validated` plus at least one author `validated`.
- No hardcoded person-name replacements or modified ground truth.
- Git commits are omitted in this environment because the `git` executable is unavailable; each task ends with a test/report checkpoint instead.

---

## Phase A: Preflight Only, No Schema Or Data Writes

### Task 1: Pure Canonical Identity Resolver

**Files:**
- Create: `backend/app/services/canonical_identity.py`
- Test: `backend/tests/test_canonical_identity.py`
- Modify: `backend/app/services/investigator_seed.py`

**Interfaces:**
- Produces `IdentityEvidence`, `IdentityCandidate`, and `CanonicalIdentityDecision` dataclasses.
- Produces `CanonicalIdentityResolver.resolve(evidence: IdentityEvidence) -> CanonicalIdentityDecision`.
- Produces `opaque_institutional_key(private_record_id: str) -> str` and `document_pending_key(import_job_id: int, source_person_key: str) -> str`.
- Consumes `InvestigatorSeedService.candidate_matches(...)`, which returns every compatible candidate instead of only the best candidate.

- [ ] **Step 1: Write failing key-privacy and pending-isolation tests**

```python
def test_institutional_key_is_stable_and_opaque(self):
    left = opaque_institutional_key("0917300113")
    right = opaque_institutional_key("0917300113")
    self.assertEqual(left, right)
    self.assertTrue(left.startswith("institutional:"))
    self.assertNotIn("0917300113", left)

def test_pending_key_is_scoped_to_document(self):
    self.assertNotEqual(
        document_pending_key(235, "DELGADO LITARD"),
        document_pending_key(243, "DELGADO LITARD"),
    )
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `docker compose run --rm --no-deps backend python -m unittest tests.test_canonical_identity -v`

Expected: import failure because `canonical_identity.py` does not exist.

- [ ] **Step 3: Implement opaque UUIDv5 keys**

```python
IDENTITY_NAMESPACE = UUID("dd9a37c9-c2da-4c3b-9f25-99a889fdb2dc")

def opaque_institutional_key(private_record_id: str) -> str:
    return f"institutional:{uuid5(IDENTITY_NAMESPACE, f'institutional:{private_record_id}')}"

def document_pending_key(import_job_id: int, source_person_key: str) -> str:
    source = normalize_name_key(source_person_key)
    return f"pending:{uuid5(IDENTITY_NAMESPACE, f'pending:{import_job_id}:{source}')}"
```

- [ ] **Step 4: Add failing resolution-policy tests**

Cover direct teacher/external priority, exact unique Excel match, isolated prefix remaining pending, corroborated prefix resolving, multiple compatible candidates remaining pending, one-surname overlap not becoming corroboration, and locked manual decision winning.

```python
def test_isolated_prefix_remains_pending(self):
    decision = resolver.resolve(evidence_for("Delgado Litard", import_job_id=243))
    self.assertEqual(decision.status, "pending_review")
    self.assertTrue(decision.canonical_identity_key.startswith("pending:"))
    self.assertEqual(decision.identity_source, "pending")

def test_locked_manual_identity_is_never_replaced(self):
    evidence = evidence_for(
        "Fernando Zambrano Farias",
        import_job_id=235,
        identity_locked=True,
        existing_key="manual:review-17",
    )
    self.assertEqual(resolver.resolve(evidence).canonical_identity_key, "manual:review-17")
```

- [ ] **Step 5: Implement candidate enumeration and policy**

`candidate_matches` must return all records at or above suggestion threshold. `resolve` accepts prefix/subset/OCR candidates only at confidence `>=0.96` and with an independent signal from `linked_identity_key`, `fuller_alias_in_document`, `document_owner_match`, `institutional_identifier_match`, or `project_code_match`. Generic role labels do not count.

- [ ] **Step 6: Run resolver tests**

Run: `docker compose run --rm --no-deps backend python -m unittest tests.test_canonical_identity tests.test_investigator_seed tests.test_name_matching -v`

Expected: all tests pass; no database connection or write occurs.

### Task 2: Read-Only Preflight And Proposed Schema

**Files:**
- Create: `backend/scripts/preflight_canonical_identity.py`
- Create: `backend/tests/test_canonical_identity_preflight.py`
- Generate: `backend/reports/canonical_identity_preflight_<timestamp>/proposed_schema.sql`
- Generate: `backend/reports/canonical_identity_preflight_<timestamp>/row_changes.csv`
- Generate: `backend/reports/canonical_identity_preflight_<timestamp>/summary.json`
- Generate: `backend/reports/canonical_identity_preflight_<timestamp>/ambiguous_exclusions.csv`
- Generate: `backend/reports/canonical_identity_preflight_<timestamp>/audited_groups.json`

**Interfaces:**
- Produces `build_preflight(db: Session) -> CanonicalIdentityPreflight`.
- CLI has no `--apply` option. It opens a read-only PostgreSQL transaction and calls `SET TRANSACTION READ ONLY`.
- Reads current successful `person_roles`, `scientific_production_authors`, teachers, externals, jobs, seed records, and corroborating document evidence.

- [ ] **Step 1: Write a failing no-write preflight test**

```python
def test_preflight_does_not_mutate_source_or_canonical_fields(self):
    before = snapshot_identity_tables(self.db)
    report = build_preflight(self.db)
    after = snapshot_identity_tables(self.db)
    self.assertEqual(before, after)
    self.assertGreater(report.role_rows_considered, 0)
```

- [ ] **Step 2: Implement immutable snapshots and cross-table consistency checks**

The report compares hashes of `raw_name`, original `person_key`, `raw_author_name`, `import_job_id`, trace IDs, parsed payload hashes, and evidence metadata before/after. Proposed role and author keys for equivalent evidence in the same document must match or appear in `consistency_errors`.

- [ ] **Step 3: Generate, but do not execute, the schema proposal**

`proposed_schema.sql` contains additive `ALTER TABLE` statements for the five approved canonical fields plus manual-lock metadata and indexes. It begins with:

```sql
-- PROPOSAL ONLY. DO NOT EXECUTE BEFORE FINAL AUTHORIZATION.
BEGIN;
ALTER TABLE person_roles ADD COLUMN canonical_identity_key VARCHAR(320);
ALTER TABLE scientific_production_authors ADD COLUMN canonical_identity_key VARCHAR(320);
ROLLBACK;
```

The full proposal includes every approved column, index, downgrade statement, and verification query, but the preflight never sends it to PostgreSQL.

- [ ] **Step 4: Implement required before/after metrics**

`summary.json` includes source identities, proposed canonical identities, source/proposed person-document participations, proposed merges, excluded ambiguous merges, roles affected, authors affected, pending count change, current/projected eligible products, and explicit proof that KPI changes are zero unless existing validated author evidence is consolidated without changing validation status.

- [ ] **Step 5: Add audited-group assertions**

Assert details for Delgado, Zambrano, Sanchez, Ramirez, and Merchan: source keys, proposed opaque keys, candidate counts, confidence, corroboration, affected roles/authors, decision, and exclusion reason.

- [ ] **Step 6: Run the preflight in read-only mode**

Run:

```powershell
docker compose run --rm --no-deps backend python scripts/preflight_canonical_identity.py `
  --output-dir reports/canonical_identity_preflight_20260711
```

Expected: exit `0`; `database_writes=0`; `consistency_errors=[]`; no migration version changes; no canonical columns exist yet.

- [ ] **Step 7: Verify database schema and row hashes remain unchanged**

Run `psql` queries against `information_schema.columns`, migration history, row counts, and immutable-field hashes. Store output as `database_unchanged.txt`.

### Task 3: Geometry Feasibility Preflight

**Files:**
- Create: `backend/app/services/pdf_parser/geometric_table_extractor.py`
- Test: `backend/tests/test_geometric_table_extractor.py`
- Use real PDFs: `backend/reports/identity_product_investigation_20260711/job234_ginfaes.pdf`, `job242_tributaria.pdf`, and the Zambrano fixture.

**Interfaces:**
- Produces `extract_production_table_words(page: fitz.Page) -> list[GeometricProductionRow]`.
- Produces `GeometricCell(text, page, column, bbox)` and `GeometricProductionRow(cells, method, warnings)`.
- Does not replace `PdfTextExtraction.text`; it is called only after a production-table header is detected.

- [ ] **Step 1: Write failing real-PDF column tests**

```python
def test_tributaria_pdf_keeps_author_columns_separate(self):
    rows = extract_real_pdf_rows(TRIBUTARIA_PDF)
    row = find_row(rows, "PLANIFICACION TRIBUTARIA Y FINANCIERA")
    self.assertEqual(row.author_cells, [
        "ORTIZ GUEVARA DOLORES",
        "VITERI VERA MARIA DEL PILAR",
        "ORTEGA DECIMAVILLA ELVIRA",
        "RIVADENEIRA CAMPOVERDE JORGE",
    ])
```

Also assert GINFAES separates `Martha Rodriguez` and `Carlos Apolinario`, while a non-table project title still comes from the current text path.

- [ ] **Step 2: Implement header X ranges and Y-row grouping**

Use `page.get_text("words")`, locate the header labels, derive column intervals from header centers, group words by Y overlap, and append wrapped words only inside their assigned interval.

- [ ] **Step 3: Add fallback tests**

Malformed geometry, OCR-only pages, missing headers, and overlapping columns return no geometric rows plus a fallback reason. The caller continues using the existing table parser.

- [ ] **Step 4: Run geometry tests without reprocessing or persistence**

Run: `docker compose run --rm --no-deps backend python -m unittest tests.test_geometric_table_extractor tests.test_pdf_parser -v`

Expected: real-PDF cell assertions pass; database row counts remain unchanged.

### Task 4: Present Preflight Gate

**Files:**
- Generate: `backend/reports/canonical_identity_preflight_20260711/preflight_report.md`

- [ ] **Step 1: Assemble the operator report**

Include schema proposal, opaque-key proof, cross-table consistency, pending isolation, manual precedence, immutable hashes, before/after counts, proposed/excluded merges, role/author effects, pending changes, KPI impact, audited groups, geometry results, authorship-only SQL, rollback design, and real-PDF tests.

- [ ] **Step 2: Stop before migration/backfill**

Do not create a migration file in `backend/app/migrations/versions`, run schema SQL, update canonical fields, or reprocess PDFs. Present the report and wait for final authorization.

---

## Phase B: Blocked Until Final Authorization

### Task 5: Additive Migration And ORM Fields

**Files:**
- Create after approval: `backend/app/migrations/versions/20260711_0015_canonical_identity_fields.py`
- Modify: `backend/app/models/entities.py`
- Test: `backend/tests/test_canonical_identity_migration.py`

**Interfaces:**
- Adds the same canonical/manual columns to `PersonRole` and `ScientificProductionAuthor` ORM models.
- Migration upgrade/downgrade is symmetric and contains no data update.

- [ ] **Step 1: Write a migration-shape test**

Assert both tables expose `canonical_identity_key`, `canonical_name`, `identity_source`, `identity_confidence`, `identity_reason`, `identity_locked`, `identity_decided_by`, and `identity_decided_at`, with an index on each canonical key.

- [ ] **Step 2: Create the additive migration**

```python
for table in ("person_roles", "scientific_production_authors"):
    op.add_column(table, sa.Column("canonical_identity_key", sa.String(320), nullable=True))
    op.add_column(table, sa.Column("canonical_name", sa.String(220), nullable=True))
    op.add_column(table, sa.Column("identity_source", sa.String(40), nullable=True))
    op.add_column(table, sa.Column("identity_confidence", sa.Float(), nullable=True))
    op.add_column(table, sa.Column("identity_reason", sa.Text(), nullable=True))
    op.add_column(table, sa.Column("identity_locked", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column(table, sa.Column("identity_decided_by", sa.String(180), nullable=True))
    op.add_column(table, sa.Column("identity_decided_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(f"ix_{table}_canonical_identity_key", table, ["canonical_identity_key"])
```

- [ ] **Step 3: Run upgrade and downgrade only on a restored disposable database**

Expected: upgrade adds 16 columns and two indexes; downgrade removes only those additions; immutable hashes and row counts remain identical.

- [ ] **Step 4: Stop if the production database URL is detected**

The migration test command must require an explicit disposable `CANONICAL_IDENTITY_TEST_DATABASE_URL`; absence skips rather than falling back to development.

### Task 6: Canonical Persistence And Transactional Backfill

**Files:**
- Modify: `backend/app/services/import_service.py`
- Create: `backend/scripts/backfill_canonical_identity.py`
- Test: `backend/tests/test_canonical_identity_backfill.py`

**Interfaces:**
- Produces `apply_identity_decision(row, decision) -> bool`; returns false for locked or unchanged rows.
- CLI requires `--apply --backup-path <existing-restorable-file>` for writes.

- [ ] **Step 1: Write failing consistency and manual-precedence tests**

```python
def test_role_and_author_receive_same_canonical_key(self):
    result = backfill_document(self.db, self.job.id)
    self.assertEqual(result.role.canonical_identity_key, result.author.canonical_identity_key)

def test_locked_decision_survives_backfill_and_reprocess(self):
    self.role.identity_locked = True
    self.role.canonical_identity_key = "manual:review-17"
    backfill_document(self.db, self.job.id)
    self.assertEqual(self.role.canonical_identity_key, "manual:review-17")
```

- [ ] **Step 2: Persist one shared decision object**

Resolve a document evidence registry once, then pass its decision to both `_record_person_role` and `_persist_production_authors`. Do not resolve the two tables independently.

- [ ] **Step 3: Implement guarded apply mode**

```python
if args.apply and not args.backup_path.exists():
    parser.error("--apply requires an existing restorable --backup-path")
with db.begin():
    lock_target_rows(db)
    before = immutable_snapshot(db)
    changed = apply_decisions(db, decisions)
    verify_immutable_snapshot(before, immutable_snapshot(db))
    verify_cross_table_consistency(db)
```

- [ ] **Step 4: Inject a consistency failure and verify full rollback**

Expected: zero canonical changes after the raised invariant error.

- [ ] **Step 5: Apply only after the operator's final authorization**

Run once with backup, save row-level output, then run a second time and require `changed_rows=0`.

### Task 7: Geometry Integration And Reprocessing

**Files:**
- Modify: `backend/app/services/pdf_parser/pdf_text_extractor.py`
- Modify: `backend/app/services/pdf_parser/table_parser.py`
- Modify: `backend/app/services/import_service.py`
- Test: `backend/tests/test_pdf_parser.py`

**Interfaces:**
- Extends `PdfTextExtraction` with optional `production_tables: list[GeometricProductionRow]` while preserving `text` unchanged.
- `parse_progress_pdf(text, *, geometric_production_rows=None)` prefers geometry only for provided production rows.

- [ ] **Step 1: Write an integration test proving narrative extraction is unchanged**

Parse the Zambrano project title with and without geometry and assert identical narrative-section output.

- [ ] **Step 2: Wire geometry only after a production header match**

```python
production_tables = []
for page in document:
    words = page.get_text("words")
    if has_production_table_header(words):
        production_tables.extend(extract_production_table_words(page))
```

- [ ] **Step 3: Persist extraction provenance in audit metadata**

Store `extraction_method`, `source_page`, `bbox`, `column`, and `fallback_reason` in parsed product/authorship objects and `ImportNormalizationAudit.metadata_json`.
The allowed method values are exactly `geometry_words`, `text_fallback`, and `ocr_fallback`.

- [ ] **Step 4: Run real-PDF parser tests before any reprocess**

Expected: literal table cells pass for GINFAES, tributaria, and Zambrano; malformed geometry uses current fallback.

- [ ] **Step 5: Reprocess only current successful jobs after backfill authorization**

Use the existing current-batch reprocessor; assert `document_key`, `source_rev`, `is_current`, and `supersedes_id` snapshots remain unchanged.

### Task 8: Unique Participant Read Model And Explicit Authorships

**Files:**
- Modify: `backend/app/services/validated_read_service.py`
- Modify: `backend/app/schemas/imports.py`
- Modify: `frontend/app/teachers/page.tsx`
- Modify: `frontend/lib/types.ts`
- Test: `backend/tests/test_validated_read_models.py`

**Interfaces:**
- Produces `ValidatedReadService.canonical_participants(period_id) -> list[dict[str, Any]]`.
- Each result includes `canonical_identity_key`, `canonical_name`, `variants`, `roles`, `documents`, `investigations`, `authorships`, `evidences`, and `pending_states`.

- [ ] **Step 1: Write a failing unique-person and leakage test**

```python
def test_participant_products_require_explicit_authorship(self):
    rows = self.service.canonical_participants(self.period.id)
    jose = next(row for row in rows if row["canonical_name"] == "Jose Manuel Santos Jaen")
    self.assertEqual(jose["authorships"], [])
```

- [ ] **Step 2: Aggregate backend rows by canonical key**

Use canonical key as the dictionary key. Preserve each source key/raw name as a variant and each `(canonical_key, import_job_id)` as a document participation.

- [ ] **Step 3: Query explicit authorships**

Join `ScientificProductionAuthor.production_id == ScientificProduction.id`; scope evidence through current successful jobs. Never join products through a participant role's `import_job_id` alone.

- [ ] **Step 4: Remove report-wide product assignment in React**

Delete `relatedProducts = reportProductTitles(report)` from participant product computation. Render only backend `authorships` and retain report products solely in report-level views.

- [ ] **Step 5: Verify audited people**

Assert Carlos has one explicit product, Maria Valls three, Jose Santos zero, and Fernando the products supported by author rows after canonical resolution.

### Task 9: Production Filters And Review Detail

**Files:**
- Modify: `backend/app/api/v1/endpoints/production.py`
- Modify: `backend/app/services/production_service.py`
- Modify: `backend/app/services/validated_read_service.py`
- Modify: `backend/app/schemas/production.py`
- Modify: `frontend/app/production/page.tsx`
- Test: `backend/tests/test_validated_read_models.py`

**Interfaces:**
- Adds `ProductionView(str, Enum)` values `all`, `eligible`, `pending`, `discarded`.
- Extends `ProductionRead` with `original_title`, `reconstructed_title`, `authors`, `confidence_score`, `extraction_method`, and `kpi_eligible`.

- [ ] **Step 1: Write failing filter/KPI tests**

Assert `all=19`, `eligible=2`, `pending=17`, and `discarded=0` for the current fixture dataset, while deriving expectations from persisted statuses in unit fixtures rather than changing ground truth constants.

- [ ] **Step 2: Implement service predicates**

```python
eligible = production.validation_status == "validated" and any(
    author.validation_status == "validated" for author in production.authors
)
```

`pending` is neither eligible nor discarded; filters do not mutate status.

- [ ] **Step 3: Return review detail and provenance**

Map raw title to `original_title`, current parsed title to `reconstructed_title`, author rows to structured author items, and extraction metadata from the current normalization audit.

- [ ] **Step 4: Add a segmented control to the production page**

Use four buttons with `aria-pressed`, stable dimensions, and query-backed filtering. Pending detail displays PDF, page, both title forms, authors, confidence, and reason.

- [ ] **Step 5: Verify API and UI counts**

Run endpoint tests and Playwright assertions for all four filters; KPI card remains `2`.

### Task 10: External Metrics And Project Field Status

**Files:**
- Modify: `backend/app/schemas/kpis.py`
- Modify: `backend/app/services/kpi_service.py`
- Modify: `backend/app/schemas/research_entities.py`
- Modify: `backend/app/services/validated_read_service.py`
- Modify: `frontend/app/dashboard/page.tsx`
- Modify: `frontend/app/teachers/page.tsx`
- Modify: `frontend/app/projects/page.tsx`
- Test: `backend/tests/test_validated_read_models.py`

**Interfaces:**
- Adds `external_detected_count`, `external_validated_kpi_count`, and `external_pending_count` to `DashboardKpi`.
- Adds `responsible_validation_status` and `responsible_review_reason` to `ResearchEntityRead`.

- [ ] **Step 1: Write failing external/project tests**

Current integration fixture expectation: five external identities detected, one validated KPI, four pending. An entity with `validation_status=validated` and unresolved director returns `responsible_validation_status=pending_review`.

- [ ] **Step 2: Derive external metrics from canonical participants**

Count distinct canonical keys, not role rows. Validated external requires a linked external record, non-review status, and validated role evidence.

- [ ] **Step 3: Derive responsible field status from responsible roles**

Use roles linked to the entity with types `director`, `director_proyecto`, `coordinador`, `tutor_semillero`, or `responsable_informe`. Missing or unresolved evidence returns pending without changing `ResearchEntity.validation_status`.

- [ ] **Step 4: Render separate labels**

Dashboard and Participants show all three external counts. Projects renders entity and responsible status independently, including `Proyecto validado / director pendiente`.

- [ ] **Step 5: Run service and frontend tests**

Expected: external counters reconcile across dashboard/participants; seven current entities retain ground truth and expose their responsible-field state.

### Task 11: Final Verification And Before/After Delivery

**Files:**
- Generate: `backend/reports/canonical_identity_delivery_<timestamp>/`

- [ ] **Step 1: Run full backend tests**

Run: `docker compose exec -T backend python -m unittest discover -s tests -v`

Expected: all non-environment-gated tests pass; skipped integration tests are listed explicitly.

- [ ] **Step 2: Build the frontend**

Run: `docker compose build frontend`

Expected: Next.js compile, lint, type check, and static generation succeed.

- [ ] **Step 3: Query authenticated endpoints**

Save JSON for dashboard, progress records, participants, production with each view, research entities, teachers, goals, and reports. Compare unique identities, person-document participations, consolidations, discarded fragments, products/person, externals, pending products, and responsible field states.

- [ ] **Step 4: Verify idempotency and manual precedence**

Second backfill run must report zero changes. A controlled test reprocess must leave a locked `manual:` key unchanged.

- [ ] **Step 5: Deploy and inspect**

Rebuild backend/frontend containers, record image IDs and frontend BUILD_ID, then run Playwright desktop/mobile checks for Participants, Production, Projects, and Dashboard.

- [ ] **Step 6: Deliver operator evidence**

Include modified files, migration ID, backup path/hash, SQL, before/after JSON, row counts, immutable hashes, rollback result, test output, screenshots, known limitations, and every identity still requiring human review.
