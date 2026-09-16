import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import test, { after } from "node:test";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const tsc = path.join(frontendRoot, "node_modules", "typescript", "bin", "tsc");
let compiledRoot;
let modulesPromise;

async function compileTask13Modules() {
  compiledRoot = await mkdtemp(path.join(tmpdir(), "b2b2-task13-runtime-"));
  const sources = [
    "lib/api.ts",
    "lib/human-review.ts",
    "lib/human-review-api.ts",
    "components/human-review/DetectedDataPanel.tsx",
    "components/human-review/OptimisticConflictDialog.tsx",
    "components/human-review/CapabilityGate.tsx",
    "components/human-review/ReviewSessionPage.tsx"
  ];
  const configPath = path.join(compiledRoot, "tsconfig.runtime.json");
  await writeFile(configPath, JSON.stringify({
    compilerOptions: {
      noEmit: false, incremental: false, module: "commonjs", moduleResolution: "node", target: "ES2022",
      jsx: "react-jsx", esModuleInterop: true, skipLibCheck: true, types: ["node"],
      typeRoots: [path.join(frontendRoot, "node_modules", "@types")], baseUrl: frontendRoot,
      paths: { "@/*": ["*"] }, rootDir: frontendRoot, outDir: compiledRoot
    },
    files: sources.map((source) => path.join(frontendRoot, source))
  }), "utf8");
  const result = spawnSync(process.execPath, [tsc, "--project", configPath], { cwd: frontendRoot, encoding: "utf8" });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  process.env.NODE_PATH = path.join(frontendRoot, "node_modules");
  require("node:module")._initPaths();
  return {
    React: require("react"),
    renderToStaticMarkup: require("react-dom/server").renderToStaticMarkup,
    contract: require(path.join(compiledRoot, "lib", "human-review.js")),
    api: require(path.join(compiledRoot, "lib", "api.js")),
    client: require(path.join(compiledRoot, "lib", "human-review-api.js")),
    dialog: require(path.join(compiledRoot, "components", "human-review", "OptimisticConflictDialog.js")),
    gate: require(path.join(compiledRoot, "components", "human-review", "CapabilityGate.js")),
    session: require(path.join(compiledRoot, "components", "human-review", "ReviewSessionPage.js"))
  };
}

function loadModules() {
  modulesPromise ??= compileTask13Modules();
  return modulesPromise;
}

function detail(overrides = {}) {
  return {
    id: "case-13", case_type: "person_identity", case_status: "pending", scientific_status: "pending",
    document_id: 41, source_revision: "rev-4", source_page: 8, source_section: "Participantes",
    automatic_priority: 7, manual_priority: null, possible_kpi_impact: true, version: 3,
    created_at: "2026-08-01T10:00:00Z", target_table: "person_roles", target_pk: 919,
    field_path: "canonical_name", detected_value: "Ada Lovelace", normalized_value: "Ada Lovelace",
    canonical_value: "Ada Lovelace", current_decision_id: "decision-old", overrides: [],
    effective_memberships: [], counterpart_options: [], evidence_summary: { available: true }, ...overrides
  };
}

after(async () => { if (compiledRoot) await rm(compiledRoot, { recursive: true, force: true }); });

test("only the structured REVIEW_CASE_VERSION_CONFLICT opens a safe conflict dialog", async () => {
  const { React, renderToStaticMarkup, api, client, contract, dialog } = await loadModules();
  const versionConflict = new client.HumanReviewApiError(new api.ApiFetchError("internal", {
    status: 409, code: "REVIEW_CASE_VERSION_CONFLICT", correlation_id: "support-conflict-13"
  }));
  const other409 = new client.HumanReviewApiError(new api.ApiFetchError("internal", {
    status: 409, code: "INCOMPATIBLE_DECISION", correlation_id: "support-other-13"
  }));
  assert.equal(contract.isOptimisticVersionConflict(versionConflict), true);
  assert.equal(contract.isOptimisticVersionConflict(other409), false);
  const html = renderToStaticMarkup(React.createElement(dialog.OptimisticConflictDialog, {
    conflict: versionConflict, isReloading: false, onCancel() {}, onReload() {}
  }));
  assert.match(html, /actualizado por otra operaci/i);
  assert.match(html, /support-conflict-13/);
  assert.doesNotMatch(html, /internal|stack|source_path|document_key/i);
});


test("conflict snapshot preserves a complete draft, preview, CAS and correlation without retry", async () => {
  const { api, client, contract } = await loadModules();
  const original = detail();
  let draft = contract.createDecisionDraft(original, "correct", "draft-correlation-13");
  draft = contract.withDecisionPayload(draft, { ...draft.payload, canonical_identity_key: "identity:ada", canonical_name: "Ada Byron Lovelace" });
  const conflict = new client.HumanReviewApiError(new api.ApiFetchError("raw error", {
    status: 409, code: "REVIEW_CASE_VERSION_CONFLICT", correlation_id: "support-conflict-13"
  }));
  const snapshot = contract.createOptimisticConflictSnapshot(original, draft, conflict, "apply");
  assert.deepEqual(snapshot.draft, draft);
  assert.deepEqual(snapshot.preview, contract.buildDecisionPreview(original, draft));
  assert.deepEqual(snapshot.cas, { expected_version: 3, expected_current_decision_id: "decision-old" });
  assert.equal(snapshot.correlation_id, "support-conflict-13");
  assert.equal(snapshot.requires_manual_confirmation, true);
  assert.equal(snapshot.command_retried, false);
  assert.equal(snapshot.awaiting_reload, true);
  assert.equal(snapshot.operation, "apply");
});

test("cancel leaves apply awaiting explicit reload, makes zero requests and keeps original CAS", async () => {
  const { api, client, contract } = await loadModules();
  let fetches = 0;
  globalThis.fetch = async () => { fetches += 1; throw new Error("cancel must not fetch"); };
  const original = detail();
  const draft = contract.createDecisionDraft(original, "correct", "draft-correlation-13");
  const conflict = new client.HumanReviewApiError(new api.ApiFetchError("raw", { status: 409, code: "REVIEW_CASE_VERSION_CONFLICT", correlation_id: "support-conflict-13" }));
  const snapshot = contract.createOptimisticConflictSnapshot(original, draft, conflict, "apply");
  const cancelled = contract.keepOptimisticConflictAwaitingReload(snapshot);
  assert.equal(fetches, 0);
  assert.equal(cancelled.awaiting_reload, true);
  assert.deepEqual(cancelled.cas, { expected_version: 3, expected_current_decision_id: "decision-old" });
  assert.equal(contract.isOptimisticConfirmationBlocked(cancelled), true);
});

test("explicit reload replaces only valid CAS, preserves draft, recalculates preview and still requires manual confirmation", async () => {
  const { api, client, contract } = await loadModules();
  const original = detail();
  let draft = contract.createDecisionDraft(original, "correct", "draft-correlation-13");
  draft = contract.withDecisionPayload(draft, { ...draft.payload, canonical_identity_key: "identity:ada", canonical_name: "Ada Byron Lovelace" });
  const conflict = new client.HumanReviewApiError(new api.ApiFetchError("raw", { status: 409, code: "REVIEW_CASE_VERSION_CONFLICT", correlation_id: "support-conflict-13" }));
  const snapshot = contract.createOptimisticConflictSnapshot(original, draft, conflict, "apply");
  const refreshed = detail({ version: 4, current_decision_id: "decision-new", canonical_value: "Ada King" });
  const afterReload = contract.applyOptimisticConflictReload(snapshot, refreshed);
  assert.deepEqual(afterReload.draft, draft);
  assert.deepEqual(afterReload.cas, { expected_version: 4, expected_current_decision_id: "decision-new" });
  assert.equal(afterReload.preview.currentValue, "Ada King");
  assert.equal(afterReload.preview.proposedValue, "Ada Byron Lovelace");
  assert.equal(afterReload.requires_manual_confirmation, true);
  assert.equal(afterReload.command_retried, false);
  assert.equal(afterReload.awaiting_reload, false);
  assert.equal(contract.isOptimisticConfirmationBlocked(afterReload), false);
});

test("a failed reload keeps the former draft and CAS tokens unchanged", async () => {
  const { api, client, contract } = await loadModules();
  const original = detail();
  const draft = contract.createDecisionDraft(original, "correct", "draft-correlation-13");
  const conflict = new client.HumanReviewApiError(new api.ApiFetchError("raw", { status: 409, code: "REVIEW_CASE_VERSION_CONFLICT", correlation_id: "support-conflict-13" }));
  const snapshot = contract.createOptimisticConflictSnapshot(original, draft, conflict, "apply");
  const reloadFailure = new client.HumanReviewApiError(new api.ApiFetchError("raw", { status: 503, correlation_id: "support-reload-13" }));
  const failed = contract.keepOptimisticConflictAfterReloadFailure(snapshot, reloadFailure);
  assert.deepEqual(failed.draft, snapshot.draft);
  assert.deepEqual(failed.cas, snapshot.cas);
  assert.equal(failed.reload_error_correlation_id, "support-reload-13");
  assert.equal(failed.command_retried, false);
  assert.equal(failed.awaiting_reload, true);
});

test("apply and discard remain incompatible outside pending after reload", async () => {
  const { api, client, contract } = await loadModules();
  const original = detail();
  const conflict = new client.HumanReviewApiError(new api.ApiFetchError("raw", { status: 409, code: "REVIEW_CASE_VERSION_CONFLICT", correlation_id: "support-conflict-13" }));
  const draft = contract.createDecisionDraft(original, "correct", "draft-correlation-13");
  for (const operation of ["apply", "discard"]) {
    const snapshot = contract.createOptimisticConflictSnapshot(original, draft, conflict, operation);
    const reloaded = contract.applyOptimisticConflictReload(snapshot, detail({ case_status: "resolved", version: 4 }));
    assert.equal(reloaded.needs_revision, true, operation);
    assert.equal(contract.isOptimisticConfirmationBlocked(reloaded), true, operation);
  }
});

test("revert preserves its original target and blocks a changed, missing or incompatible refreshed decision", async () => {
  const { api, client, contract } = await loadModules();
  const original = detail({ case_status: "resolved", current_decision_id: "decision-original" });
  const conflict = new client.HumanReviewApiError(new api.ApiFetchError("raw", { status: 409, code: "REVIEW_CASE_VERSION_CONFLICT", correlation_id: "support-conflict-13" }));
  const snapshot = contract.createOptimisticConflictSnapshot(original, null, conflict, "revert", "decision-original");
  for (const changed of [
    detail({ case_status: "resolved", current_decision_id: "decision-new", version: 4 }),
    detail({ case_status: "resolved", current_decision_id: null, version: 4 }),
    detail({ case_status: "pending", current_decision_id: "decision-original", version: 4 })
  ]) {
    const reloaded = contract.applyOptimisticConflictReload(snapshot, changed);
    assert.equal(reloaded.needs_revision, true);
    assert.equal(contract.isOptimisticConfirmationBlocked(reloaded), true);
  }
  assert.deepEqual(contract.buildRevertRequest(original, "Motivo", "corr", "decision-original").decision_id_to_revert, "decision-original");
  assert.throws(() => contract.buildRevertRequest(detail({ case_status: "resolved", current_decision_id: "decision-new" }), "Motivo", "corr", "decision-original"), /vigente/i);
});

test("a corrected incompatible draft becomes confirmable after a valid reload without losing its fields", async () => {
  const { api, client, contract } = await loadModules();
  const original = detail({ case_type: "possible_duplicate", field_path: "case", counterpart_options: [{ counterpart_ref: { target_type: "person_roles", target_id: 1 }, display_name: "A" }] });
  const draft = contract.createDecisionDraft(original, "link", "draft-correlation-13");
  const conflict = new client.HumanReviewApiError(new api.ApiFetchError("raw", { status: 409, code: "REVIEW_CASE_VERSION_CONFLICT", correlation_id: "support-conflict-13" }));
  const snapshot = contract.createOptimisticConflictSnapshot(original, draft, conflict, "apply");
  const changedOptions = detail({ case_type: "possible_duplicate", field_path: "case", counterpart_options: [{ counterpart_ref: { target_type: "person_roles", target_id: 2 }, display_name: "B" }], version: 4 });
  const incompatible = contract.applyOptimisticConflictReload(snapshot, changedOptions);
  assert.equal(incompatible.needs_revision, true);
  const corrected = contract.withDecisionPayload(incompatible.draft, { ...incompatible.draft.payload, counterpart_ref: { target_type: "person_roles", target_id: 2 } });
  const repaired = contract.updateOptimisticConflictDraft(incompatible, changedOptions, corrected);
  assert.equal(repaired.needs_revision, false);
  assert.equal(contract.isOptimisticConfirmationBlocked(repaired), false);
  assert.equal(repaired.draft.payload.counterpart_ref.target_id, 2);
});

test("CapabilityGate denies by default and exposes only actions returned by /human-review/me", async () => {
  const { React, renderToStaticMarkup, gate } = await loadModules();
  const render = (props) => renderToStaticMarkup(React.createElement(gate.CapabilityGate, props, React.createElement("button", null, "Scientific action")));
  assert.equal(render({ action: "apply_scientific", isLoading: true }), "");
  assert.equal(render({ action: "apply_scientific", error: new Error("/me failed") }), "");
  assert.equal(render({ action: "apply_scientific", capabilities: { capability: "SYSTEM_ADMIN", actions: ["view_audit"] } }), "");
  assert.match(render({ action: "view_audit", capabilities: { capability: "SYSTEM_ADMIN", actions: ["view_audit"] } }), /Scientific action/);
  assert.match(render({ action: "apply_scientific", capabilities: { capability: "RESEARCH_MANAGER", actions: ["apply_scientific"] } }), /Scientific action/);
  assert.equal(render({ action: "view_audit", capabilities: { capability: null, actions: [] } }), "");
  assert.equal(render({ action: "view_audit", capabilities: { capability: null, actions: [] } }), "");
  assert.equal(render({ action: "apply_scientific", capabilities: { capability: "SYSTEM_ADMIN", actions: ["view_audit"] } }), "");
  assert.match(render({ action: "apply_scientific", capabilities: { capability: null, actions: ["apply_scientific"] } }), /Scientific action/);
  assert.match(render({ action: "view_audit", capabilities: { capability: null, actions: ["view_audit"] } }), /Scientific action/);
  assert.equal(render({ action: "apply_scientific", capabilities: { capability: "RESEARCH_MANAGER", actions: ["apply_scientific"] }, isLoading: true }), "");
});

test("audit and protected controls stay inert while capabilities update, and conflict dialog traps Tab safely", async () => {
  const page = await readFile(path.join(frontendRoot, "components", "human-review", "ReviewSessionPage.tsx"), "utf8");
  const composer = await readFile(path.join(frontendRoot, "components", "human-review", "DecisionComposer.tsx"), "utf8");
  const dialog = await readFile(path.join(frontendRoot, "components", "human-review", "OptimisticConflictDialog.tsx"), "utf8");
  let fetches = 0;
  globalThis.fetch = async () => { fetches += 1; throw new Error("unauthorized audit must stay inert"); };
  assert.match(page, /canViewHumanReviewDetail/);
  assert.match(page, /capabilitiesBusy/);
  assert.match(composer, /capabilitiesUpdating/);
  assert.match(composer, /CapabilityGate action="apply_scientific"/);
  assert.match(composer, /CapabilityGate action="revert_scientific"/);
  assert.match(dialog, /event\.shiftKey/);
  assert.match(dialog, /button:not\(\[disabled\]\)/);
  assert.equal(fetches, 0);
});

test("reload accepts only a valid response for the active case and keeps invalid data out of CAS replacement", async () => {
  const { contract } = await loadModules();
  assert.equal(contract.isValidHumanReviewReload("case-13", detail({ version: 4, current_decision_id: "decision-new", case_status: "pending" })), true);
  assert.equal(contract.isValidHumanReviewReload("case-13", detail({ version: 1 })), true);
  for (const invalid of [
    detail({ id: "other-case", version: 4 }),
    detail({ version: -1 }),
    detail({ version: 0 }),
    detail({ version: 1.5 }),
    detail({ current_decision_id: 7 }),
    detail({ case_status: "unknown" })
  ]) assert.equal(contract.isValidHumanReviewReload("case-13", invalid), false);
});

test("composer and page contract prevent stale actions, reset obsolete conflicts and coalesce reloads", async () => {
  const composer = await readFile(path.join(frontendRoot, "components", "human-review", "DecisionComposer.tsx"), "utf8");
  const page = await readFile(path.join(frontendRoot, "components", "human-review", "ReviewSessionPage.tsx"), "utf8");
  assert.match(composer, /!editable \|\| confirmationBlocked/);
  assert.match(composer, /const revertable = detail\.case_status === "resolved"/);
  assert.match(composer, /setConflictSnapshot\(null\)/);
  assert.match(composer, /confirmationBlocked \|\| commands\.isLoading/);
  assert.match(page, /inFlightDetails\.current/);
  assert.match(page, /isValidHumanReviewReload/);
});

test("abandoning a local form preserves awaiting_reload until a valid reload, then clears the abandoned snapshot", async () => {
  const { api, client, contract } = await loadModules();
  const original = detail();
  const draft = contract.createDecisionDraft(original, "correct", "draft-correlation-13");
  const conflict = new client.HumanReviewApiError(new api.ApiFetchError("raw", { status: 409, code: "REVIEW_CASE_VERSION_CONFLICT", correlation_id: "support-conflict-13" }));
  const snapshot = contract.createOptimisticConflictSnapshot(original, draft, conflict, "apply");
  const abandoned = contract.abandonOptimisticConflictOperation(snapshot);
  assert.equal(abandoned.operation_abandoned, true);
  assert.equal(abandoned.awaiting_reload, true);
  assert.equal(abandoned.draft, null);
  assert.equal(contract.isOptimisticConfirmationBlocked(abandoned), true);
  const reloaded = contract.applyOptimisticConflictReload(abandoned, detail({ version: 4, current_decision_id: "decision-new" }));
  assert.equal(reloaded.needs_revision, false);
  assert.equal(reloaded.awaiting_reload, false);
  assert.equal(reloaded.operation_abandoned, true);
  const composer = await readFile(path.join(frontendRoot, "components", "human-review", "DecisionComposer.tsx"), "utf8");
  assert.match(composer, /abandonConflictOperation/);
  assert.match(composer, /next\.operation_abandoned/);
});

test("session conflict reload preserves a compatible draft and represents an incompatible one without rewriting it", async () => {
  const { api, client, contract } = await loadModules();
  const original = detail({ id: "case-13", current_decision_id: "decision-old" });
  let draft = contract.createDecisionDraft(original, "correct", "draft-correlation-13");
  draft = { ...contract.withDecisionPayload(draft, { ...draft.payload, canonical_identity_key: "identity:ada" }), reason: "Corrección verificada" };
  const conflictError = new client.HumanReviewApiError(new api.ApiFetchError("raw", {
    status: 409, code: "REVIEW_CASE_VERSION_CONFLICT", correlation_id: "support-conflict-13"
  }));
  const conflict = contract.createOptimisticConflictSnapshot(original, draft, conflictError, "apply");
  let state = contract.createReviewSessionState([{
    case_id: "case-13", case_type: "person_identity", case_status: "pending", scientific_status: "pending",
    version: 3, current_decision_id: "decision-old", detected_value: "Ada", normalized_value: "Ada", canonical_value: "Ada",
    possible_kpi_impact: true, allowed_actions: ["approve", "correct", "link"], evidence_summary: { available: true }
  }]);
  state = contract.reviewSessionReducer(state, { type: "draftChanged", caseId: "case-13", draft });
  state = contract.reviewSessionReducer(state, {
    type: "commandFailed", caseId: "case-13",
    result: { status: "conflict", correlationId: "support-conflict-13", message: "La revisión cambió." }, conflict
  });

  const compatible = contract.reviewSessionReducer(state, {
    type: "conflictReloaded", caseId: "case-13", detail: detail({ version: 4, current_decision_id: "decision-new" })
  });
  assert.equal(compatible.draftByCaseId["case-13"], draft);
  assert.equal(compatible.conflictByCaseId["case-13"].needs_revision, false);
  assert.deepEqual(compatible.conflictByCaseId["case-13"].cas, { expected_version: 4, expected_current_decision_id: "decision-new" });

  const incompatible = contract.reviewSessionReducer(state, {
    type: "conflictReloaded", caseId: "case-13", detail: detail({ case_type: "product", version: 4, current_decision_id: "decision-new" })
  });
  assert.equal(incompatible.draftByCaseId["case-13"], draft);
  assert.equal(incompatible.conflictByCaseId["case-13"].needs_revision, true);
  assert.match(incompatible.validationByCaseId["case-13"].join(" "), /revis/i);
});
