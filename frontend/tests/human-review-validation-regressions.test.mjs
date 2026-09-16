import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

async function source(relativePath) {
  return readFile(path.join(frontendRoot, relativePath), "utf8");
}

test("effective human-review refresh invalidates pending queue and effective-reader caches", async () => {
  const dataCache = await source("lib/data-cache.tsx");

  assert.match(dataCache, /subscribeEffectiveDataRefresh/);
  assert.match(dataCache, /invalidateEffectiveDataCaches\(store\)/);
  assert.match(dataCache, /HUMAN_REVIEW_PREFIX,[\s\S]*"dashboard:"/);
  assert.match(dataCache, /"canonical-participants:"/);
  assert.match(dataCache, /"imported-progress:"/);
});

test("human-review cache remains memory-only instead of being restored from localStorage", async () => {
  const dataCache = await source("lib/data-cache.tsx");

  assert.match(
    dataCache,
    /if \(!key\.startsWith\(HUMAN_REVIEW_PREFIX\)\) cache\.set\(key, entry\)/
  );
  assert.match(
    dataCache,
    /return \[\.\.\.cache\.entries\(\)\]\.filter\(\(\[key\]\) => !key\.startsWith\(HUMAN_REVIEW_PREFIX\)\)/
  );
});

test("failed confirmations are rendered as visible alerts instead of collapsed-only details", async () => {
  const resultSummary = await source("components/human-review/ReviewResultSummary.tsx");

  assert.match(resultSummary, /if \(result\.status !== "confirmed"\)/);
  assert.match(resultSummary, /role="alert"/);
  assert.match(resultSummary, /resultLabels\[result\.status\]/);
  assert.match(resultSummary, /Referencia de soporte/);
});


test("reopened cases remain editable after a functional reversal", async () => {
  const domain = await source("lib/human-review.ts");
  const session = await source("components/human-review/ReviewSessionPage.tsx");
  const composer = await source("components/human-review/DecisionComposer.tsx");
  const queries = await readFile(path.join(frontendRoot, "..", "backend", "app", "services", "human_review_queries.py"), "utf8");

  assert.match(domain, /return status === "pending" \|\| status === "reopened"/);
  assert.match(domain, /isEditableReviewStatus\(detail\.case_status\)/);
  assert.match(session, /isEditableReviewStatus\(activeDetail\.case_status\)/);
  assert.match(session, /validateSessionDraft\(activeDetail, activeDraft, activeConflict\)/);
  assert.match(composer, /isEditableReviewStatus\(detail\.case_status\)/);
  assert.doesNotMatch(composer, /const editable = detail\.case_status === "pending"/);
  assert.match(queries, /ReviewCaseStatus\.REOPENED\.value/);
});

test("invalid correction and link drafts explain why confirmation is blocked", async () => {
  const session = await source("components/human-review/ReviewSessionPage.tsx");
  const confirmation = await source("components/human-review/SessionConfirmationBar.tsx");

  assert.match(session, /validationMessages=\{activeValidationMessages\}/);
  assert.match(confirmation, /Completa los campos obligatorios o el motivo indicado/);
  assert.match(confirmation, /role="alert"/);
});

test("reopened case detail is not labelled read-only", async () => {
  const detail = await source("components/human-review/DetectedDataPanel.tsx");

  assert.match(detail, /detail\.case_status === "reopened"/);
  assert.match(detail, /Caso reabierto (?:·|\\u00b7) editable nuevamente/);
});

test("case detail exposes the audit timeline", async () => {
  const session = await source("components/human-review/ReviewSessionPage.tsx");

  assert.match(session, /useHumanReviewAudit/);
  assert.match(session, /<AuditTimeline/);
});

test("server failures preserve a support correlation reference", async () => {
  const hooks = await source("hooks/useHumanReview.ts");

  assert.match(hooks, /error\.correlation_id\.trim\(\) \? error\.correlation_id\.trim\(\) : null/);
  assert.doesNotMatch(hooks, /approvedStatus && error\.correlation_id/);
});
