# Validated Dashboard Data Design

## Goal

Make persisted, validated relational records the only operational source for
`/dashboard` and `/progress-records`. Keep `parsed_payload` as extraction and
audit evidence, never as a fallback for those routes.

## Existing System

The correct checkout already persists `Teacher`, `PersonRole`,
`ScientificProduction`, `ScientificProductionAuthor`, and `ResearchEntity`
rows through `ImportService._persist_normalized_from_payload()`. The defect is
downstream: `KpiService` and `_serialize_progress_row()` rebuild their views
from `ImportedOcrTrace.parsed_payload`. In addition, `Teacher` and `PersonRole`
do not expose `validation_status`, so their validated state cannot be queried
uniformly.

## Design

- Add indexed `validation_status` columns to `teachers` and `person_roles`.
  Manual teachers and confidently persisted imported teachers backfill to
  `validated`; unresolved or low-confidence rows backfill to `pending_review`.
- Extend `_record_person_role()` to persist an explicit status. A linked person
  with confidence at least `0.90` is validated; unresolved and weaker matches
  remain pending. Product-author roles inherit the author row status.
- Add a normalized read service shared by dashboard and progress serialization.
  It queries period/job-scoped relational rows and centralizes status filtering,
  participant aggregation, product/authorship hydration, and entity hydration.
- Main KPI totals count only `validation_status = 'validated'`. Pending and
  discarded reconciliation counters may use persisted status/audit rows, but
  never raw JSON.
- Remove `parsed_payload` from the trace columns loaded by dashboard and
  `/progress-records`. Missing normalized rows produce empty operational data;
  they do not trigger a JSON fallback.

## Parser And Identity Fixes

- Label-value `AUTOR 1/2/3` fields call `looks_like_person_name()` before
  appending an author.
- PDF extraction emits explicit page boundaries. A repeated table header after
  a page boundary does not close an incomplete scientific-product title when no
  new recognized section started.
- Excel seed matching gives high confidence to a normalized strict prefix or a
  multi-token subset of one seed name, independently of flat string-length
  similarity.
- Heuristic dedupe/merge requires two matching surname tokens. Exact normalized
  names and identities resolved to the same authoritative Excel record remain
  valid identity matches rather than heuristic surname merges.

## Migration And Existing Data

The new migration adds and indexes the two missing status columns, then
backfills existing rows deterministically. Existing scientific productions,
authors, and research entities already have status fields and are not copied to
new tables. No synthetic parallel read model is introduced.

## Tests

- Parser tests reproduce invalid label-value authors and Zambrano page 7-to-8
  continuation.
- Seed tests assert explicit prefix/subset matches.
- Identity tests reject one-surname heuristic merges.
- Relational read tests use an in-memory database with deliberately conflicting
  `parsed_payload` and persisted rows; results must follow persisted validated
  rows only.
- Migration tests verify status columns/backfill behavior where practical.
- All backend tests run after the focused red-green cycles.
