# Canonical Identities And Geometric Production Design

## Goal

Represent one operational participant row per resolved identity while preserving every source variant and evidence row. Recover scientific-production table columns from PDF geometry where flattened text loses column boundaries, expose pending products, and associate people with products only through explicit authorship evidence.

Dropbox identity, document versioning, historical reconciliation, `document_key`, and current-version selection are outside this change.

## Evidence Preservation

The following fields are immutable during identity resolution and backfill:

- `raw_name`
- the existing source `person_key`
- `raw_author_name`
- PDF text and parsed payloads
- `import_job_id`
- trace, audit, entity, role, production, and authorship row IDs

Canonical fields are additive. No source row is deleted, merged, or reassigned to another import job.

## Schema Change

Migration `0015_canonical_identity_fields` adds these nullable columns to both `person_roles` and `scientific_production_authors`:

- `canonical_identity_key VARCHAR(320)`
- `canonical_name VARCHAR(220)`
- `identity_source VARCHAR(40)`
- `identity_confidence DOUBLE PRECISION`
- `identity_reason TEXT`

It also adds:

- `identity_locked BOOLEAN NOT NULL DEFAULT FALSE`
- `identity_decided_by VARCHAR(180) NULL`
- `identity_decided_at TIMESTAMPTZ NULL`

Indexes are added on `canonical_identity_key` in both tables. There is no uniqueness constraint because one identity can own multiple roles, documents, and products. The manual fields prepare a later review workflow: automatic resolution and reprocessing must never overwrite a row with `identity_locked=true`.

Downgrade removes only the new indexes and columns. It does not alter source identity fields.

## Canonical Key Priority

Resolution follows this order:

1. Preserve a locked manual decision.
2. `teacher:<teacher_id>` when a teacher is directly linked or when an approved institutional match resolves to an existing teacher.
3. `external:<external_researcher_id>` when an external researcher is directly linked or approved institutional evidence resolves to one.
4. `institutional:<uuid>` for a unique, sufficiently supported institutional seed match without an existing teacher or external row. The UUID is deterministically derived with UUIDv5 from an application namespace and the private institutional record identifier. The public key never contains the identity number, name, email, or other source value.
5. `pending:<uuid>` for unresolved evidence. The UUIDv5 input includes `import_job_id` and the normalized source key, so incomplete or ambiguous evidence can group within one document but never auto-merges identities across documents. It is not treated as a validated identity.

The resolver returns key, canonical name, source, confidence, reason, decision, matched candidate count, and supporting signals. It is shared by parser normalization, persistence, backfill, `ValidatedReadService`, and scientific authorship persistence.

## Automatic Resolution Policy

An automatic resolution requires exactly one compatible candidate, no competing candidate, and the documented confidence threshold.

- Direct teacher/external FK: confidence `1.00`.
- Exact or inverted normalized Excel alias: confidence `>=0.98`, unique candidate.
- Prefix, subset, or OCR similarity: confidence `>=0.96` plus independent corroboration.

Independent corroboration is one of:

- an already linked teacher/external identity with the same institutional seed identity;
- a fuller exact alias in the same current document;
- document-owner/path evidence matching the candidate;
- matching institutional identifier or email;
- matching project code recorded in Excel.

A generic author/director role alone is not corroboration. An isolated prefix remains `pending_review`, even when it has one best Excel suggestion. Suggestions and rejected competing candidates are retained in `identity_reason` and audit metadata.

## Audited Groups Before Backfill

The preflight command must report the exact resulting keys rather than applying them. Expected policy outcomes are:

- `Delgado Litard` and `Delgado Litardo`: separate `pending:` keys unless independent evidence is found. Their isolated prefix suggestion to Boris Ivan Delgado Litardo is not enough.
- Both Fernando Zambrano variants: the same opaque `institutional:<uuid>` because the aliases are exact and unique; no teacher row currently exists. The UUID must not expose the source identity number.
- Both Maria Estefania Sanchez variants: `teacher:568`; the exact Excel identity bridges the partial alias to the existing teacher.
- Ramirez variants: the same opaque `institutional:<uuid>` only if the current document-owner/director anchor validates the unique candidate; otherwise the uncorroborated fragments remain document-scoped pending keys.
- Jorge Merchan variants: `teacher:565` when the exact/full identity in the same jobs corroborates the OCR variants. The current source variant is `JORGE MRRCHAN` with two `R` characters.

The database currently has 51 visible source person keys, 87 source person-document pairs, and 120 non-discarded role rows. The exact post-policy count is produced by dry-run and is a hard gate before backfill.

## Backfill

The backfill command is dry-run by default. It reads current successful evidence, applies the resolver to both target tables, and emits:

- row ID and table;
- immutable source fields;
- old and proposed canonical fields;
- candidate count and supporting signals;
- decision and reason;
- unique identities and person-document counts before/after;
- automatic consolidations;
- ambiguous suggestions that remain pending.

Apply mode requires a restorable PostgreSQL backup. One transaction locks the selected rows, verifies immutable snapshots, updates only canonical/manual-support fields, verifies role/author consistency, and commits. Any invariant failure rolls back the complete operation. A second execution must update zero rows.

The apply report contains restoration SQL for the previous canonical fields in addition to the database backup. No source data is deleted.

## Geometric Table Extraction

Normal text extraction remains unchanged for narrative sections. For detected production-table pages only, PyMuPDF `page.get_text("words")` supplies word bounding boxes.

The geometric parser:

1. Finds the table header and its `TITULO`, `AUTOR 1..5`, `ESTADO`, `IMPACTO`, and `LINK` X ranges.
2. Groups words into visual rows using Y overlap.
3. Assigns each word to its header column by X position.
4. Reconstructs wrapped cells independently, preserving page and bounding-box evidence.
5. Sends each complete author cell through person-name validation and canonical identity resolution.

Excel is never used to invent a column split. It validates a cell after geometry has separated it. If geometry is unavailable, inconsistent, OCR-only, or crosses overlapping columns, the existing text parser remains the fallback and the row stays pending when ambiguity remains.

Each product and author audit records `extraction_method` (`geometry_words`, `text_fallback`, or `ocr_fallback`), page, bounding box, column, and fallback reason in parsed/audit metadata.

## Participant Read Model

Backend aggregation uses `canonical_identity_key`, never React name heuristics. One participant object contains:

- canonical identity key and name;
- source `person_key` values and raw/normalized variants;
- teacher/external IDs when present;
- roles and validation states;
- current documents and import job IDs;
- investigations/entities;
- explicit product authorships;
- source evidence and pending reasons.

Person-document participations remain available as nested evidence, but the operational table contains one row per canonical identity. Pending keys remain separate unless the resolver has sufficient evidence.

Product lists are built only from `scientific_production_authors.production_id -> scientific_productions.id`. Sharing an `import_job_id` does not create an authorship or product association.

## Production Read Model And UI

`GET /production` accepts `view=all|eligible|pending|discarded`. The response includes:

- source PDF and page;
- original/raw title;
- reconstructed title;
- normalized authors and individual author states;
- confidence;
- review reason;
- extraction method;
- `validation_status` and computed `kpi_eligible`.

The UI exposes segmented filters `Todos`, `Elegibles`, `Pendientes`, and `Descartados`. Pending rows remain visible and detailed. KPI eligibility remains unchanged: the product must be `validated` and have at least one `validated` author. Pending products never become KPI eligible through display filtering.

## External Metrics And Project Fields

Dashboard and Participants expose three separate counts: detected external identities, validated KPI external identities, and pending external identities.

Research entity responses expose entity validation separately from responsible-person validation. The UI may display `Proyecto validado / director pendiente`. A missing or unresolved responsible role does not rewrite entity ground truth, but it prevents the combined display from claiming that every field is validated.

## Tests

Tests are written before implementation and include real-PDF regressions for:

- separate author columns that flattened text concatenates;
- two author names concatenated into one text fragment;
- recoverable truncated name with independent corroboration;
- ambiguous truncated name that remains pending;
- unique exact Excel match;
- multiple compatible Excel candidates remaining pending;
- one-surname overlap never merging identities;
- identical canonical keys in roles and production authors;
- specific product authorship without report-wide leakage;
- all four production filters and the unchanged KPI rule;
- external detected/validated/pending counts;
- entity-valid/responsible-pending project display.

Real PDFs are assertions on literal cell values and associations, not only row counts.

## Delivery Gates

Before applying backfill, deliver the migration diff, dry-run keys for audited groups, identity counts before/after, and ambiguous non-merges. After approval of that preflight, apply the migration/backfill, reprocess only where geometric extraction requires it, rebuild the frontend, and compare endpoints and screens before/after.
