# Canonical Operational Readers B2A Implementation Plan

**Goal:** Make operational readers and the four authorized screens consume the canonical identity fields persisted by B1, expose pending production, and associate products with people only through explicit authorship rows.

**Architecture:** `ValidatedReadService` remains the single operational read boundary. It will add a canonical participant projection built from current `person_roles` and current scientific-product authors, using persisted `canonical_identity_key` as the group key and the legacy `person_key` only for rows outside the B1 population. Production is classified at read time as eligible, pending, or discarded without changing the KPI rule or persisted statuses. Project director state is derived from the explicit `person_roles.research_entity_id` relation.

**Constraints:** No schema changes, parser changes, PDF reprocessing, geometry integration, Dropbox/versioning work, identity recomputation, persisted-data updates, review UI, or KPI-rule changes.

## Task 1: Baseline And Contract Tests

- Record B1 invariants and the verified pre-B2A backup.
- Add failing service and endpoint-contract tests for canonical grouping, pending visibility, explicit authorship, production filters, external counts, and project/director state.
- Preserve the existing eligible-only default for `/production`; the frontend requests `visibility=all` explicitly.

## Task 2: Canonical Read Model

- Add a current operational authorship query that follows current direct product jobs or current normalization-audit evidence.
- Aggregate one participant per persisted canonical key.
- Include variants, roles, documents, research entities, explicit authorships, evidence, source statuses, and pending reasons.
- Exclude discarded-only identities from the operational participant list while keeping their evidence available in production/audit views.

## Task 3: Production, Dashboard, And Projects

- Serialize production with canonical/raw title, source evidence, canonical authors and variants, status, confidence, reason, evidence state, and derived KPI eligibility.
- Support `visibility=all|eligible|pending|discarded` without changing persistence.
- Add external detected/eligible/pending dashboard metrics based on canonical identities.
- Add separate project and director status fields using related canonical person-role evidence.

## Task 4: Frontend

- Replace React name-based participant merging with the backend canonical projection.
- Present unique people plus explicit counts for participations, roles, and authorships.
- Add production filters and pending evidence details.
- Add external metrics and project/director compound status.
- Keep the chart label `Participaciones por carrera`.

## Task 5: Verification

- Run focused tests first, then full backend tests with PostgreSQL integrations enabled.
- Run frontend type/lint/build checks and Playwright desktop/mobile checks against the rebuilt local frontend.
- Compare endpoint metrics and named authorship counts before/after.
- Re-run immutable database hashes and confirm no B1, source, trace, payload, document, or Dropbox data changed.
