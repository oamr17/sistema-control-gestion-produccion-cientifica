# Human Review Guided Validation Session Implementation Plan

> Implement each task in sequence, verify its acceptance criteria, and use the checkboxes (`- [ ]`) to track progress.

**Goal:** Convert the single-case Human Review detail into a guided, accessible validation session where an authorized manager can review evidence and prepare or confirm several related cases independently without persisting drafts.

**Architecture:** Add one read-only related-context query whose entity association is derived only from existing persisted identities and immutable foreign keys. Keep every scientific decision on the existing per-case B2B.2 commands, while the frontend stores normalized drafts in memory and coordinates valid commands with a maximum concurrency of two. Evidence remains authenticated and case-bound, loading only for the active case and revoking obsolete blob URLs.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, SQLAlchemy 2, PostgreSQL 16, unittest, Next.js 14.2.23, React 18.3.1, TypeScript 5.7.2, SWR-style project cache hooks, Node test runner, Playwright.

## Global Constraints

- Official repository: `C:\Users\OMAR\Desktop\tesis\New project`; never install or copy product changes to the OneDrive workspace.
- B2B.2 final commands remain `POST /human-review/cases/{id}/apply`, `/discard`, and `/revert`; no batch command or global transaction is added.
- Related context, previews, selection changes, validation, and evidence reads are strictly read-only and create no audit event.
- Drafts and previews exist only in React memory; reload, navigation, or close discards unconfirmed work. Do not use `localStorage`, `sessionStorage`, IndexedDB, cookies, server drafts, reservations, or autosave.
- Partial completion is intentional: successful cases remain visible, read-only, and collapsed; failed, invalid, or conflicting cases retain their local drafts.
- Group only by an unambiguous persisted identity or immutable relationship. Never group by display name, normalized name, fuzzy matching, `document_key`, or `stable_target_key` alone.
- Never serialize `stable_target_key`, raw `document_key`, table names, storage paths, bucket names, object keys, credentials, SQL, stack traces, or infrastructure details.
- No SQLAlchemy model, database schema, migration, scientific source write, OCR/parser/Dropbox/n8n behavior, or B2B.3 feature may change.
- Migration `20260718_0021_human_review_scientific_decision_audit` must retain SHA-256 `54704858458CE6F27B12BF8B2BE386B91C3A32F27D1D210CEC50DAD16E423B2A`; migration 0022 must remain absent.
- PostgreSQL integration tests use a disposable PostgreSQL 16 database via `B2B1_TEST_DATABASE_URL`; the real `newproject-postgres-1` service must remain `Exited (0)` throughout implementation and cleanup.
- Final frontend verification uses `node --test --test-concurrency=1 tests/*.test.mjs`; the sequential runner is authoritative because parallel Next.js test servers contend for local ports and memory.
- This directory currently has no `.git` metadata. At every task checkpoint, record SHA-256 hashes and changed-file inventory in `reports/human_review_guided_session/`; do not claim a Git commit. If Git metadata is restored before execution, replace the hash checkpoint with the listed commit message.

---

## File Structure

### Backend

- Create `backend/app/services/human_review_related.py`: private entity resolution, safe target lookup, and deterministic related-target selection.
- Modify `backend/app/schemas/human_review_api.py`: closed public response types for the related context.
- Modify `backend/app/services/human_review_queries.py`: `HumanReviewQueryService.get_related(...)` and mapping to sanitized public items.
- Modify `backend/app/api/v1/endpoints/human_review.py`: one authenticated read-only `GET /cases/{id}/related` route.
- Create `backend/tests/test_human_review_b2b2_related.py`: PostgreSQL entity resolution, read-only, sanitization, authorization, ordering, bounds, and index-plan coverage.
- Modify `backend/tests/test_human_review_b2b2_api.py`: route/OpenAPI/error-contract coverage.
- Modify `backend/tests/test_human_review_b2b2_authorization.py`: capability checks for related context.

### Frontend

- Modify `frontend/lib/human-review.ts`: related-context types, session state types, validation helpers, and reducer actions.
- Modify `frontend/lib/human-review-api.ts`: `getRelated(caseId, limit?)` and abortable evidence fetch.
- Modify `frontend/hooks/useHumanReview.ts`: related-context hook, active-evidence hook, and command coordinator.
- Create `frontend/components/human-review/GuidedDecisionEditor.tsx`: non-technical four-question editor shell over existing decision payload controls.
- Create `frontend/components/human-review/RelatedReviewList.tsx`: stable case selection, progress, and completed collapsed summaries.
- Create `frontend/components/human-review/ReviewResultSummary.tsx`: per-case sanitized result.
- Create `frontend/components/human-review/SessionConfirmationBar.tsx`: valid-count summary and explicit partial confirmation.
- Create `frontend/components/human-review/EvidenceWorkspace.tsx`: active-case evidence, locator, embedded PDF fallback, abort, and blob cleanup.
- Create `frontend/components/human-review/ReviewSessionPage.tsx`: session composition with normalized reducer state.
- Modify `frontend/app/human-review/cases/[id]/page.tsx`: capability gate and delegation to `ReviewSessionPage`.
- Modify `frontend/components/human-review/DecisionComposer.tsx`: extract reusable payload controls without changing single-case request builders.
- Extend `frontend/tests/human-review-api.test.mjs`, `human-review-decisions.test.mjs`, `human-review-detail.test.mjs`, `human-review-conflict.test.mjs`, and `human-review-e2e.test.mjs`.

---

### Task 1: Gate A — Prove Unambiguous Entity Resolution Without Schema Changes

**Files:**
- Create: `backend/app/services/human_review_related.py`
- Create: `backend/tests/test_human_review_b2b2_related.py`
- Read only: `backend/app/models/entities.py`
- Read only: `backend/app/models/human_review_core.py`
- Read only: `backend/app/models/human_review_projection.py`
- Read only: `backend/app/services/human_review_projection.py`

**Interfaces:**
- Consumes: `ReviewItem(target_table, target_pk, case_type)`, `EffectiveHumanProjectionSource.for_record(target_table, target_pk)` and persisted target foreign keys.
- Produces: `resolve_related_entity(db: Session, item: ReviewItem) -> RelatedEntityRef` and `find_related_target_refs(db: Session, entity: RelatedEntityRef, limit: int) -> tuple[TargetRef, ...]`.

```python
@dataclass(frozen=True, slots=True)
class RelatedEntityRef:
    public_type: Literal["person", "scientific_product", "research_entity", "external_researcher"]
    public_id: str
    display_name: str
    match_kind: Literal["canonical_identity", "teacher", "external_researcher", "record"]
    match_id: UUID | int

@dataclass(frozen=True, slots=True)
class TargetRef:
    target_table: ReviewTargetTable
    target_pk: int
```

- [ ] **Step 1: Add RED tests for each permitted identity source**

  Add PostgreSQL tests proving this exact precedence:

  ```python
  # person_roles and scientific_production_authors
  # 1. active effective human canonical identity
  # 2. persisted canonical_identity_key resolved to CanonicalIdentity.id
  # 3. teacher_id
  # 4. external_researcher_id
  # 5. the exact target record only
  # scientific_productions, research_entities and external_researchers
  # use the exact persisted record id
  ```

  Include negative fixtures where two rows share `normalized_name` but have no shared key/FK; they must not be grouped. Include corrupt, merged, or missing canonical identities; resolution must fall through safely and must never infer by text.

- [ ] **Step 2: Run the gate tests and observe RED**

  Start a uniquely named disposable PostgreSQL 16 container, derive its random host port, and run from `backend`:

  ```powershell
  docker run --name human-review-guided-pg16 -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=human_review_guided -p 127.0.0.1::5432 -d postgres:16
  $taskPgReady = $false
  for ($attempt = 0; $attempt -lt 30; $attempt++) {
    docker exec human-review-guided-pg16 pg_isready -U postgres -d human_review_guided *> $null
    if ($LASTEXITCODE -eq 0) { $taskPgReady = $true; break }
    Start-Sleep -Seconds 1
  }
  if (-not $taskPgReady) { throw 'Disposable PostgreSQL 16 did not become ready' }
  $taskPgPort = ((docker port human-review-guided-pg16 5432/tcp) -split ':')[-1]
  $env:B2B1_TEST_DATABASE_URL = "postgresql+psycopg://postgres:postgres@127.0.0.1:$taskPgPort/human_review_guided"
  python -m unittest tests.test_human_review_b2b2_related.RelatedEntityResolutionTests -v
  ```

  Expected: FAIL with `ModuleNotFoundError: app.services.human_review_related` or missing resolver symbols.

- [ ] **Step 3: Implement the minimal private resolver**

  Resolve target rows through the existing `_TARGET_MODELS` equivalent, reject `target_pk is None`, and use `EffectiveHumanProjectionSource(db).for_record(...)` before persisted identity fields. Generate `public_id` only from an existing public database identifier (`CanonicalIdentity.id`, `Teacher.id`, `ExternalResearcher.id`, or the exact scientific record id); never return the private `match_kind` or `match_id` from an API schema.

  Candidate lookup must use equality predicates over indexed identifiers and produce stable `TargetRef` values sorted by `(target_table.value, target_pk)`. It must never query by name.

- [ ] **Step 4: Prove the approved cross-document branches**

  Seed at least two different `document_key` values for rows sharing one canonical identity or teacher FK and assert both target refs are returned. Assert two same-name records without the shared persisted relationship return only the anchor target.

  Run:

  ```powershell
  python -m unittest tests.test_human_review_b2b2_related.RelatedEntityResolutionTests -v
  ```

  Expected: all resolver tests PASS with zero skips.

- [ ] **Step 5: Apply the hard stop rule**

  If any currently supported `ReviewCaseType` cannot map through the exact rules above, stop execution and report the failing `(case_type, target_table)` pair. Do not add a migration, persisted locator, fuzzy match, or new functional rule. Continue to Task 2 only when every supported pair either resolves unambiguously or safely falls back to its exact target record.

- [ ] **Step 6: Record Gate A checkpoint**

  Record changed files, SHA-256 hashes, test count, PostgreSQL version, and `schema_changes: 0` in `backend/reports/human_review_guided_session/task01-gate-a.json`. If Git exists, commit with `feat(human-review): resolve related scientific entities safely`.

---

### Task 2: Related Context Schemas and Read-Only Query Service

**Files:**
- Modify: `backend/app/schemas/human_review_api.py`
- Modify: `backend/app/services/human_review_queries.py`
- Modify: `backend/app/services/human_review_related.py`
- Test: `backend/tests/test_human_review_b2b2_related.py`

**Interfaces:**
- Consumes: `resolve_related_entity(...)`, `find_related_target_refs(...)`, `CASE_ACTION_DECISION_TYPES`, and existing queue/detail projection helpers.
- Produces: `RelatedReviewEntity`, `RelatedReviewItem`, `RelatedReviewResponse`, and `HumanReviewQueryService.get_related(review_item_id: UUID, *, limit: int = 50) -> RelatedReviewResponse`.

```python
class RelatedReviewEntity(_ClosedModel):
    public_type: Literal["person", "scientific_product", "research_entity", "external_researcher"]
    public_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=220)

class RelatedReviewItem(_ClosedModel):
    case_id: UUID
    case_type: ReviewCaseType
    case_status: ReviewCaseStatus
    scientific_status: ScientificStatus
    version: int = Field(ge=1)
    current_decision_id: UUID | None
    detected_value: str | None
    normalized_value: str | None
    canonical_value: str | None
    possible_kpi_impact: bool
    allowed_actions: tuple[DecisionAction, ...]
    evidence_summary: EvidenceSummaryMapping

class RelatedReviewResponse(_ClosedModel):
    entity: RelatedReviewEntity
    items: tuple[RelatedReviewItem, ...] = Field(max_length=50)
    total_pending: int = Field(ge=0)
    truncated: bool
    correlation_id: UUID
```

- [ ] **Step 1: Add RED schema and service tests**

  Test closed schemas, maximum 50 items, only pending readable cases, exact deterministic order `manual_priority DESC NULLS LAST, automatic_priority DESC, created_at ASC, id ASC`, and `allowed_actions` derived from `CASE_ACTION_DECISION_TYPES` rather than a second matrix.

  Serialize the response and recursively assert absence of: `stable_target_key`, `document_key`, `target_table`, `target_pk`, `source_path`, `bucket`, `object_key`, `dropbox_path`, and backslashes.

- [ ] **Step 2: Observe RED**

  ```powershell
  python -m unittest tests.test_human_review_b2b2_related.RelatedQueryServiceTests -v
  ```

  Expected: FAIL because related schemas and `get_related` do not exist.

- [ ] **Step 3: Add the closed public schemas**

  Reuse existing scalar/public validators and evidence/KPI response types. Add a model validator that rejects duplicate `case_id` values and requires `total_pending >= len(items)` when `truncated` is true. Do not embed `ReviewCaseDetail`, because it exposes target metadata not needed by the session list.

- [ ] **Step 4: Implement `get_related` without writes**

  Use `db.no_autoflush`, resolve the anchor first, find matching target refs, then select `ReviewItem` rows with `case_status == pending`. Fetch `limit + 1`, return at most 50, and derive `truncated` from the extra row. Reuse existing safe projection/evidence summary functions; do not call command, audit, flush, commit, or rollback methods.

  ```python
  def get_related(self, review_item_id: UUID, *, limit: int = 50) -> RelatedReviewResponse:
      bounded = min(max(limit, 1), 50)
      anchor = self._get_item_or_raise(review_item_id)
      entity = resolve_related_entity(self.db, anchor)
      targets = find_related_target_refs(self.db, entity, limit=200)
      rows = self._select_pending_related(targets, limit=bounded + 1)
      return self._related_response(entity, rows[:bounded], truncated=len(rows) > bounded)
  ```

- [ ] **Step 5: Prove read-only behavior and index use**

  Capture counts and hashes of `review_items`, `review_decisions`, `field_overrides`, and `audit_events` before/after the query. Assert the SQLAlchemy session has no `new`, `dirty`, or `deleted` objects.

  Run `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` on the critical related `review_items` query with representative data and assert the plan uses `ix_review_items_target` or a bitmap/index scan rooted in that existing index; a sequential scan is allowed only for the tiny fixture, so rerun with enough rows to make index selection meaningful. Do not create an index.

- [ ] **Step 6: Run Task 2 tests GREEN**

  ```powershell
  python -m unittest tests.test_human_review_b2b2_related -v
  ```

  Expected: all related resolver/query/EXPLAIN tests PASS, zero skips.

- [ ] **Step 7: Record checkpoint**

  Write `backend/reports/human_review_guided_session/task02-related-query.json` with test totals, EXPLAIN node/index names, read-only hash proof, and changed-file hashes. If Git exists, commit with `feat(human-review): expose sanitized related review context`.

---

### Task 3: Authenticated Related Context HTTP Route

**Files:**
- Modify: `backend/app/api/v1/endpoints/human_review.py`
- Modify: `backend/tests/test_human_review_b2b2_api.py`
- Modify: `backend/tests/test_human_review_b2b2_authorization.py`
- Test: `backend/tests/test_human_review_b2b2_related.py`

**Interfaces:**
- Consumes: `HumanReviewQueryService.get_related(...)`, `RelatedReviewResponse`, `require_b2b_action(B2BAction.VIEW_FOUNDATIONS)`.
- Produces: `GET /api/v1/human-review/cases/{review_item_id}/related?limit=1..50` with response model `RelatedReviewResponse`.

- [ ] **Step 1: Add RED route, OpenAPI, and authorization tests**

  Assert:

  ```python
  response = client.get(f"/api/v1/human-review/cases/{case_id}/related")
  assert response.status_code == 200
  assert response.headers["cache-control"] == "private, no-store, max-age=0"
  assert response.json()["correlation_id"] == response.headers["x-correlation-id"]
  ```

  Add 401 without authentication, 403 without `VIEW_FOUNDATIONS`, 404 for an unknown/unauthorized anchor, 422 for invalid UUID or limit, and sanitized 503 for internal query failure. Require no distinction between missing and out-of-scope anchors.

- [ ] **Step 2: Observe RED**

  ```powershell
  python -m unittest tests.test_human_review_b2b2_api tests.test_human_review_b2b2_authorization -v
  ```

  Expected: the related route returns 404/405 or is absent from OpenAPI.

- [ ] **Step 3: Register the single read-only route**

  ```python
  @router.get("/cases/{review_item_id}/related", response_model=RelatedReviewResponse)
  def get_related_review_cases(
      review_item_id: UUID,
      request: Request,
      limit: int = Query(default=50, ge=1, le=50),
      db: Session = Depends(get_db),
      _: User = Depends(require_b2b_action(B2BAction.VIEW_FOUNDATIONS)),
  ) -> RelatedReviewResponse:
      correlation_id = _request_correlation_id(request)
      return HumanReviewQueryService(db, correlation_id=correlation_id).get_related(review_item_id, limit=limit)
  ```

  Map only existing safe domain errors. Set `Cache-Control: private, no-store, max-age=0` and `X-Correlation-ID` through the existing route/response helper. Do not add POST, PATCH, proposal, or session endpoints.

- [ ] **Step 4: Verify OpenAPI and route precedence**

  Assert exactly one new GET operation and ensure `/cases/{review_item_id}` does not consume the literal `/related` suffix incorrectly. Verify the schema has no forbidden property names.

- [ ] **Step 5: Run backend focal regressions**

  ```powershell
  python -m unittest tests.test_human_review_b2b2_related tests.test_human_review_b2b2_queries tests.test_human_review_b2b2_api tests.test_human_review_b2b2_authorization tests.test_human_review_b2b2_evidence -v
  ```

  Expected: PASS with zero skips and no database mutation.

- [ ] **Step 6: Record checkpoint**

  Write `backend/reports/human_review_guided_session/task03-related-http.json` with route inventory, response-field scan, test totals, and hashes. If Git exists, commit with `feat(api): add read-only related human review route`.

---

### Task 4: Frontend Contracts, API Client, and Hooks

**Files:**
- Modify: `frontend/lib/human-review.ts`
- Modify: `frontend/lib/human-review-api.ts`
- Modify: `frontend/hooks/useHumanReview.ts`
- Modify: `frontend/tests/human-review-api.test.mjs`
- Modify: `frontend/tests/human-review-detail.test.mjs`

**Interfaces:**
- Consumes: backend `RelatedReviewResponse` JSON and existing `humanReviewRequest`/cache conventions.
- Produces: `RelatedReviewResponse`, `useHumanReviewRelated(caseId)`, and `useActiveHumanReviewEvidence(caseId)`.

```ts
export type RelatedReviewEntity = {
  public_type: "person" | "scientific_product" | "research_entity" | "external_researcher";
  public_id: string;
  display_name: string;
};

export type RelatedReviewItem = {
  case_id: string;
  case_type: ReviewCaseType;
  case_status: ReviewCaseStatus;
  scientific_status: ScientificStatus;
  version: number;
  current_decision_id: string | null;
  detected_value: string | null;
  normalized_value: string | null;
  canonical_value: string | null;
  possible_kpi_impact: boolean;
  allowed_actions: DecisionAction[];
  evidence_summary: EvidenceSummary;
};

export type RelatedReviewResponse = {
  entity: RelatedReviewEntity;
  items: RelatedReviewItem[];
  total_pending: number;
  truncated: boolean;
  correlation_id: string;
};
```

- [ ] **Step 1: Add RED client and hook tests**

  Assert URL encoding, `limit=50`, no body, authenticated request helper use, cache key isolation by case ID, and no request for a null case ID. Add an abort test showing evidence for case A cannot resolve into case B state after active selection changes.

- [ ] **Step 2: Observe RED**

  ```powershell
  node --test tests/human-review-api.test.mjs tests/human-review-detail.test.mjs
  ```

  Expected: FAIL because related contracts/client/hook are absent.

- [ ] **Step 3: Add exact types and API methods**

  ```ts
  getRelated(caseId: string, limit = 50): Promise<RelatedReviewResponse> {
    return humanReviewRequest<RelatedReviewResponse>(
      `/human-review/cases/${encodeURIComponent(caseId)}/related?limit=${limit}`,
    );
  }

  getEvidence(caseId: string, signal?: AbortSignal): Promise<Blob> {
    return apiFetchBlob(`/human-review/cases/${encodeURIComponent(caseId)}/evidence`, { signal });
  }
  ```

  Preserve all existing command cache invalidation. A successful command invalidates the affected case, its audit, queue, dashboard KPI, and the anchor related response.

- [ ] **Step 4: Implement hooks without waterfalls**

  `useHumanReviewRelated` starts with the detail/capability load at page mount. `useActiveHumanReviewEvidence` owns an `AbortController`, aborts on `caseId` change/unmount, ignores stale completions, and returns only `{blob, isLoading, error, reload}`; it does not create object URLs.

- [ ] **Step 5: Run Task 4 GREEN**

  ```powershell
  node --test tests/human-review-api.test.mjs tests/human-review-detail.test.mjs
  npx tsc --noEmit
  ```

  Expected: tests and TypeScript PASS.

- [ ] **Step 6: Record checkpoint**

  Write `frontend/reports/human_review_guided_session/task04-client-hooks.json` with test totals and hashes. If Git exists, commit with `feat(frontend): load related review context and active evidence`.

---

### Task 5: Normalized Session Reducer and Partial Command Coordinator

**Files:**
- Modify: `frontend/lib/human-review.ts`
- Modify: `frontend/hooks/useHumanReview.ts`
- Modify: `frontend/tests/human-review-decisions.test.mjs`
- Modify: `frontend/tests/human-review-conflict.test.mjs`

**Interfaces:**
- Consumes: `DecisionDraft`, request builders, `RelatedReviewItem[]`, and existing apply/discard command functions.
- Produces: `ReviewSessionState`, `reviewSessionReducer`, `validateSessionDraft`, and `confirmValidDrafts`.

```ts
export type ReviewSessionState = {
  activeCaseId: string;
  draftByCaseId: Record<string, DecisionDraft | undefined>;
  validationByCaseId: Record<string, string[]>;
  resultByCaseId: Record<string, ReviewCommandResult | undefined>;
  conflictByCaseId: Record<string, OptimisticConflictSnapshot | undefined>;
  requestStateByCaseId: Record<string, "idle" | "queued" | "sending" | "succeeded" | "failed">;
};

export type ReviewCommandResult = {
  status: "confirmed" | "conflict" | "forbidden" | "invalid" | "unavailable" | "not_found";
  correlationId: string | null;
  message: string;
};
```

- [ ] **Step 1: Add RED reducer tests**

  Cover draft survival across selection changes, independent cancellation, confirmed-draft removal, compatible 409 reload preservation, total clear only after explicit confirmation, and initialization keyed by `case_id`. Assert no storage API is referenced.

- [ ] **Step 2: Add RED coordinator tests**

  Use deferred promises to prove no more than two commands are in flight. Feed valid A/B/C plus invalid D and return A=200, B=409, C=503. Assert A collapses and loses its draft, B/C keep drafts, D sends no request, no automatic retry occurs, and A is not reverted.

- [ ] **Step 3: Observe RED**

  ```powershell
  node --test tests/human-review-decisions.test.mjs tests/human-review-conflict.test.mjs
  ```

  Expected: FAIL on missing reducer/coordinator symbols.

- [ ] **Step 4: Implement a pure reducer**

  Use discriminated actions with exact case-scoped updates:

  ```ts
  type ReviewSessionAction =
    | { type: "select"; caseId: string }
    | { type: "draftChanged"; caseId: string; draft: DecisionDraft }
    | { type: "cancelDraft"; caseId: string }
    | { type: "requestStateChanged"; caseId: string; state: ReviewSessionState["requestStateByCaseId"][string] }
    | { type: "commandSucceeded"; caseId: string; result: ReviewCommandResult }
    | { type: "commandFailed"; caseId: string; result: ReviewCommandResult }
    | { type: "conflictReloaded"; caseId: string; detail: CaseDetail }
    | { type: "clearAll" };
  ```

  Never rebuild the entire state from refreshed related data; reconcile only authoritative baselines and preserve compatible drafts.

- [ ] **Step 5: Implement bounded coordination**

  `confirmValidDrafts` validates all drafts first, queues only valid/non-blocked cases, and runs two workers over a stable case-ID list. Each worker calls the existing per-case API exactly once and dispatches a result for that ID. Convert 403/404/409/422/503 through existing sanitized error parsing; never place raw server text into `message`.

- [ ] **Step 6: Run Task 5 GREEN and regress existing request builders**

  ```powershell
  node --test tests/human-review-decisions.test.mjs tests/human-review-conflict.test.mjs tests/human-review-api.test.mjs
  npx tsc --noEmit
  ```

  Expected: PASS, zero duplicate sends, zero retries.

- [ ] **Step 7: Record checkpoint**

  Write `frontend/reports/human_review_guided_session/task05-session-state.json`, including maximum observed concurrency and per-status assertions. If Git exists, commit with `feat(frontend): coordinate independent review decisions`.

---

### Task 6: Guided Editor, Related Review List, and Result Summaries

**Files:**
- Create: `frontend/components/human-review/GuidedDecisionEditor.tsx`
- Create: `frontend/components/human-review/RelatedReviewList.tsx`
- Create: `frontend/components/human-review/ReviewResultSummary.tsx`
- Create: `frontend/components/human-review/SessionConfirmationBar.tsx`
- Modify: `frontend/components/human-review/DecisionComposer.tsx`
- Modify: `frontend/tests/human-review-detail.test.mjs`
- Modify: `frontend/tests/human-review-decisions.test.mjs`

**Interfaces:**
- Consumes: `CaseDetail`, `RelatedReviewItem`, `DecisionDraft`, `ReviewCommandResult`, and session callbacks scoped by `caseId`.
- Produces: accessible presentational components with no API calls or storage writes.

- [ ] **Step 1: Add RED component contract tests**

  Assert the four Spanish prompts appear in order:

  ```text
  Qué detectó el sistema
  Qué debes verificar
  Qué dato quedará registrado
  Por qué se realiza el cambio
  ```

  Assert case-specific guidance for all six case types, stable item keys, textual state labels, completed rows collapsed/read-only, and advanced public identifiers hidden behind explanatory disclosure. Verify internal table/key labels never render.

- [ ] **Step 2: Observe RED**

  ```powershell
  node --test tests/human-review-detail.test.mjs tests/human-review-decisions.test.mjs
  ```

  Expected: FAIL because the guided components do not exist.

- [ ] **Step 3: Extract reusable payload controls from `DecisionComposer`**

  Export a `DecisionPayloadFields` component receiving `{detail, draft, disabled, onChange}` and keep all action/payload compatibility logic in `human-review.ts`. Preserve existing `withDecisionAction` behavior so selecting “Corregir” never restores an older canonical name.

- [ ] **Step 4: Implement the guided editor**

  Render semantic `<section>` elements with headings, inline examples, and an associated reason field. Keep preview local through `buildDecisionPreview`; opening or closing it performs no request. Use `aria-describedby` for guidance and put validation errors adjacent to the relevant field.

- [ ] **Step 5: Implement list, progress, and results**

  `RelatedReviewList` uses buttons with `aria-expanded` and `aria-current`; successful rows render `ReviewResultSummary` collapsed by default but remain visible. `SessionConfirmationBar` displays prepared/invalid/sending/confirmed counts and disables duplicate submission while any selected case is queued or sending.

- [ ] **Step 6: Run Task 6 GREEN**

  ```powershell
  node --test tests/human-review-detail.test.mjs tests/human-review-decisions.test.mjs
  npx tsc --noEmit
  ```

  Expected: PASS with no regression in existing single-case preview/request tests.

- [ ] **Step 7: Record checkpoint**

  Write `frontend/reports/human_review_guided_session/task06-guided-ui.json` with accessible-name snapshots, forbidden-copy scan, and hashes. If Git exists, commit with `feat(frontend): add guided multi-review editor`.

---

### Task 7: Active Evidence Workspace and Secure Blob Lifecycle

**Files:**
- Create: `frontend/components/human-review/EvidenceWorkspace.tsx`
- Modify: `frontend/components/human-review/EvidencePanel.tsx`
- Modify: `frontend/hooks/useHumanReview.ts`
- Modify: `frontend/tests/human-review-detail.test.mjs`
- Modify: `frontend/tests/human-review-e2e.test.mjs`

**Interfaces:**
- Consumes: active `caseId`, its public `evidence_summary`, `useActiveHumanReviewEvidence`, and existing object URL helpers.
- Produces: one active PDF object URL, safe fallback metadata, open/download actions, and deterministic cleanup.

- [ ] **Step 1: Add RED evidence lifecycle tests**

  Select A, start its deferred evidence response, select B, resolve B then A, and assert only B is displayed. Assert A is aborted, the previous object URL is revoked exactly once, B is revoked on unmount, and 403/404/503 render sanitized messages with correlation ID only.

- [ ] **Step 2: Observe RED**

  ```powershell
  node --test tests/human-review-detail.test.mjs tests/human-review-e2e.test.mjs
  ```

  Expected: FAIL because `EvidenceWorkspace` and abort behavior are absent.

- [ ] **Step 3: Implement the workspace**

  Keep object URL creation in `EvidenceWorkspace`, not the hook. For PDF, render a titled `<iframe>` or `<object>` and append ``#page=${page}`` only to the local blob URL after validating `page` as a positive integer. Always show public filename, page, section, and safe fragment. Provide authenticated open/download fallback based on the same blob; never expose a remote storage URL.

- [ ] **Step 4: Implement mobile disclosure**

  Above the mobile editor, render a native button/disclosure labeled `Ver evidencia de esta revisión`. Preserve active case selection when opening/closing. Do not trap scrolling or focus and do not reload the blob merely because the disclosure toggles.

- [ ] **Step 5: Run Task 7 GREEN and sensitive scan**

  ```powershell
  node --test tests/human-review-detail.test.mjs tests/human-review-e2e.test.mjs
  rg -n -i "dropbox_path|document_key|stable_target_key|bucket|object_key|access[_-]?key|secret[_-]?key|C:\\\\|/home/" frontend/components/human-review frontend/app/human-review frontend/lib frontend/hooks
  ```

  Expected: tests PASS; scan returns no rendered/internal-path additions (type-only backend field names already present outside new code must be reviewed separately, not hidden).

- [ ] **Step 6: Record checkpoint**

  Write `frontend/reports/human_review_guided_session/task07-evidence.json` with abort/revocation counts, scan result, and hashes. If Git exists, commit with `feat(frontend): switch evidence with active review`.

---

### Task 8: Session Page Integration, Responsive Layout, and Accessibility

**Files:**
- Create: `frontend/components/human-review/ReviewSessionPage.tsx`
- Modify: `frontend/app/human-review/cases/[id]/page.tsx`
- Modify: `frontend/tests/human-review-detail.test.mjs`
- Modify: `frontend/tests/human-review-conflict.test.mjs`
- Modify: `frontend/tests/human-review-e2e.test.mjs`

**Interfaces:**
- Consumes: related/detail/capability hooks, session reducer, command coordinator, guided components, and evidence workspace.
- Produces: the complete desktop split view and mobile single-column validation session.

- [ ] **Step 1: Add RED integration tests**

  Cover initial active case selection, automatic evidence switch, multiple retained drafts, partial confirmation, collapsed successes, preserved failures, 409 explicit reload, individual cancel, confirmed clear-all, empty-related fallback to anchor, truncated notice, and navigation/reload discard semantics.

- [ ] **Step 2: Observe RED**

  ```powershell
  node --test tests/human-review-detail.test.mjs tests/human-review-conflict.test.mjs tests/human-review-e2e.test.mjs
  ```

  Expected: FAIL because `ReviewSessionPage` is absent.

- [ ] **Step 3: Compose data loading without avoidable waterfalls**

  Start capabilities, anchor detail, and related context together. Fetch full `CaseDetail` lazily for a related item only when first activated, cache it by case ID in memory, and preserve its draft when switching. An inaccessible lazy detail becomes a sanitized per-case failure and does not collapse other cases.

- [ ] **Step 4: Implement desktop and mobile structure**

  Desktop uses a two-column grid with sticky evidence only when viewport height permits; validation remains the primary scrolling region. Mobile uses one column with evidence disclosure above the active editor. Do not add horizontal tables, fixed heights that clip forms, or nested scroll traps.

- [ ] **Step 5: Add accessibility behavior**

  Move focus to the active review heading after keyboard selection, keep visible focus rings, announce one concise batch result through `aria-live="polite"`, respect `prefers-reduced-motion`, and ensure status is always expressed by text/icon as well as color.

- [ ] **Step 6: Run Task 8 GREEN, TypeScript, and production build**

  ```powershell
  node --test tests/human-review-detail.test.mjs tests/human-review-conflict.test.mjs tests/human-review-e2e.test.mjs
  npx tsc --noEmit
  npm run build
  ```

  Expected: all tests, TypeScript, and Next.js build PASS; `/human-review/cases/[id]` is generated.

- [ ] **Step 7: Browser QA with regular Playwright fallback**

  The Browser plugin is not available in this environment, so use project Playwright. At 1440×900 and 390×844, verify keyboard navigation, active evidence switching, partial results, no horizontal overflow, no hydration/console errors, and no stale blob after switching. Capture screenshots in `frontend/reports/human_review_guided_session/qa/`.

- [ ] **Step 8: Record checkpoint**

  Write `frontend/reports/human_review_guided_session/task08-session-page.json` with viewport results, console count, screenshots, build result, and hashes. If Git exists, commit with `feat(frontend): integrate guided validation session`.

---

### Task 9: Final Security, Performance, PostgreSQL, and Regression Closure

**Files:**
- Modify only if a reproducible in-scope defect is found: files listed in Tasks 1–8.
- Create: `backend/reports/human_review_guided_session/final-verification.json`
- Create: `frontend/reports/human_review_guided_session/final-verification.json`

**Interfaces:**
- Consumes: all completed Tasks 1–8.
- Produces: final evidence-backed closure with no schema change, sensitive exposure, skip, expected failure, or disposable resource remaining.

- [ ] **Step 1: Run backend focal suites on disposable PostgreSQL 16**

  ```powershell
  python -m unittest tests.test_human_review_b2b2_related tests.test_human_review_b2b2_queries tests.test_human_review_b2b2_api tests.test_human_review_b2b2_authorization tests.test_human_review_b2b2_evidence tests.test_human_review_b2b2_commands tests.test_human_review_b2b2_concurrency tests.test_human_review_b2b2_reversal -v
  ```

  Expected: PASS with zero skips/expected failures. Reconfirm read-only hash proof and EXPLAIN index evidence.

- [ ] **Step 2: Run the backend full suite once after stabilization**

  Export the same disposable URL to every PostgreSQL integration variable used by the suite, then run:

  ```powershell
  $env:CANONICAL_IDENTITY_TEST_DATABASE_URL = $env:B2B1_TEST_DATABASE_URL
  $env:CANONICAL_OPERATIONAL_TEST_DATABASE_URL = $env:B2B1_TEST_DATABASE_URL
  $env:DROPBOX_VERSIONING_TEST_DATABASE_URL = $env:B2B1_TEST_DATABASE_URL
  python -m unittest discover -s tests -p 'test_*.py' -v
  python -m compileall app scripts tests
  ```

  Expected: full suite and compileall PASS, zero skips and zero expected failures. Do not use retries as the primary fix for any failure.

- [ ] **Step 3: Run frontend focal and full suites**

  ```powershell
  node --test tests/human-review-api.test.mjs tests/human-review-decisions.test.mjs tests/human-review-detail.test.mjs tests/human-review-conflict.test.mjs tests/human-review-e2e.test.mjs
  node --test --test-concurrency=1 tests/*.test.mjs
  npx tsc --noEmit
  npm run build
  ```

  Expected: all tests PASS with zero skipped or pending tests, followed by TypeScript and build PASS.

- [ ] **Step 4: Verify performance boundaries**

  With 50 related items, assert related query count is bounded and does not execute one evidence fetch per item. In browser instrumentation, typing in one draft must not rerender every editor, evidence must fetch only for the active case, and batch confirmation must never exceed two simultaneous command requests.

- [ ] **Step 5: Run final security and placeholder scans**

  ```powershell
  rg -n -i "dropbox_path|stable_target_key|document_key|bucket|object_key|access[_-]?key|secret[_-]?key|postgresql://|C:\\\\Users\\\\|OneDrive" backend/app/services/human_review_related.py backend/app/services/human_review_queries.py backend/app/schemas/human_review_api.py frontend/components/human-review frontend/lib/human-review.ts frontend/lib/human-review-api.ts frontend/hooks/useHumanReview.ts
  $debtPattern = ('TO' + 'DO|TB' + 'D|FIX' + 'ME|\.skip\(|xfail|test\.' + 'todo|describe\.skip|it\.skip')
  rg -n $debtPattern backend/app backend/tests/test_human_review_b2b2_related.py frontend/components/human-review frontend/lib frontend/hooks frontend/tests
  ```

  Expected: no new sensitive serialization, secrets, placeholders, skips, or expected failures. Any legitimate private backend field reference must remain internal and have a negative serialization test.

- [ ] **Step 6: Verify immutable boundaries**

  Confirm migration 0021 hash exactly matches the global constraint, `Get-ChildItem backend/app/migrations/versions -Filter '*0022*'` returns nothing, no model/migration file changed, PostgreSQL real remained `Exited (0)`, and B2B.3 was not started.

- [ ] **Step 7: Cleanup all disposable resources**

  Remove only the explicitly named disposable PostgreSQL container/network/volume after verifying each resolved name belongs to this task. Confirm zero task-owned containers, volumes, listeners, dev servers, temporary workspaces, or object URLs remain.

- [ ] **Step 8: Perform Spec and Quality reviews**

  Spec review must map every requirement in `docs/diseno/specs/2026-08-10-human-review-guided-validation-session-design.md` to a passing test or artifact. Quality review must inspect entity matching, authorization, sanitization, reducer isolation, concurrency, abort/revocation, accessibility, and regression evidence. Closure requires `Critical / Important / Minor = 0 / 0 / 0`.

- [ ] **Step 9: Write final reports**

  Each `final-verification.json` records exact counts, commands, exit codes, skips, expected failures, hashes, PostgreSQL version, EXPLAIN indexes, OpenAPI route, TypeScript/build results, QA artifacts, scan results, resource cleanup, 0021 hash, 0022 absence, real PostgreSQL status, and `b2b3_started: false`.

  If Git exists, commit final evidence with `test(human-review): verify guided validation session`; otherwise the SHA-256 inventories are the authoritative checkpoint.

---

## Completion Definition

The feature is complete only when Gate A proves safe entity association without schema changes; related context is authorized, bounded, indexed, sanitized, and read-only; multiple local drafts survive navigation; evidence follows the active case; valid commands execute independently with concurrency at most two; partial successes remain visible and collapsed; failures preserve drafts; backend/frontend full suites, compileall, TypeScript, build, visual QA, security scans, Spec review, and Quality review all pass; PostgreSQL real remains stopped; migration 0021 is unchanged; 0022 and B2B.3 remain absent.

## Spec Coverage Matrix

| Approved design section | Implemented and verified by |
|---|---|
| Purpose and approved multi-review behavior | Tasks 5, 6, and 8 |
| Desktop and mobile experience | Tasks 7 and 8 |
| Guided non-technical language | Task 6 |
| Read-only related query and safe entity association | Tasks 1–3 |
| Existing independent commands and partial completion | Task 5 |
| Active evidence switching and blob lifecycle | Tasks 4 and 7 |
| Normalized in-memory state and draft retention | Tasks 5 and 8 |
| Components and separation of concerns | Tasks 6–8 |
| Conflict/error matrix | Tasks 5 and 8 |
| Accessibility and responsive behavior | Task 8 |
| React/Next.js performance boundaries | Tasks 4, 5, 8, and 9 |
| Authorization, sanitization, and absence of internal paths | Tasks 2, 3, 7, and 9 |
| Backend, frontend, PostgreSQL, browser, and regression tests | Tasks 1–9 |
| Acceptance criteria and cleanup | Task 9 and Completion Definition |
| Out-of-scope exclusions, no migration, and no B2B.3 | Global Constraints and Task 9 |
