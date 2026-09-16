# Dropbox Identity And Current Readers Design

## Goal

Unify the ten exact historical `dropbox_path:` identities from batch 169 with their canonical `dropbox:id:` identities from batch 170, preserve all history, and ensure operational readers expose data only from current successful document versions.

## Reconciliation Boundary

Only the ten pairs returned by an exact equality join on normalized `path_lower` and exact `filename` are eligible. No fuzzy filename, person-name, title, hash inference, or manual name list participates. The dry-run artifacts are `backend/reports/dropbox_identity_reconciliation_20260711/dry_run_pairs.csv` and `.json`.

For each confirmed pair, one PostgreSQL transaction will:

1. Lock both jobs.
2. Verify the historical key is `dropbox_path:`, the canonical key is `dropbox:id:`, and the current job remains current.
3. Set the historical job `is_current=false` before changing its key.
4. Replace only its `document_key` with the canonical ID key.
5. Set the current job `supersedes_id` to the historical job only when it is null. An existing valid chain is preserved; a conflicting chain aborts the entire transaction.
6. Verify all invariants before commit. Any mismatch raises and rolls back.

The historical job ID, source identifier, filename, revision, trace, parsed payload, normalized entities, and every foreign-key relationship remain unchanged. Re-running the command finds the already canonicalized pair and reports no updates.

## Delivery Mechanism

Add an idempotent reconciliation service plus CLI command. Dry-run is the default; `--apply` performs the single transaction. The command writes before/after maps and verification results. This is preferred over an automatic startup migration because the operation is evidence-driven, must produce an operator-readable report, and must abort on any ambiguity.

## Current-Version Reads

`ValidatedReadService` will centralize joins to `ImportJob` and require `ImportJob.is_current IS TRUE` and `ImportJob.status = 'SUCCESS'` for imported operational data. Records without an import job are retained as manually-created operational records. This rule applies to period/job role reads, participant roles, productions, entities, teacher views, progress-record views, KPI/dashboard, goals, and generated reports.

Tests will create a historical job and a current job with deliberately different teachers, roles, productions, entities, and progress rows. Every service and endpoint-facing view must return only the current values.

## UI Consistency

Discarded-author metrics will come from persisted `scientific_production_authors.validation_status='discarded_invalid'` scoped to current jobs, rather than relying on person roles that intentionally omit discarded values. Research entities will expose `cycle` from their related academic period when their own cycle is null.

The chart will be renamed to `Participaciones por carrera`, because current attribution deliberately allows one product to contribute to multiple careers. The supporting text will state that the global KPI total counts unique products while the bars count career participations.

## Verification

Verification includes a restorable custom-format PostgreSQL backup, dry-run map count of exactly ten, transaction row counts, unchanged relationship counts by historical job ID, uniqueness checks, second-run idempotency, full backend tests, endpoint JSON before/after comparison, literal-name classification, frontend rebuild, and visual checks on Dashboard, Participants, Projects, and Production.

## Constraints

- Do not delete historical rows.
- Do not change ground truth or individual names.
- Do not reconcile uncertain pairs.
- Do not overwrite a valid `supersedes_id` chain.
- Roll back the complete reconciliation if any invariant fails.
