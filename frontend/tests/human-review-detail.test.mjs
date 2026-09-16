import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import test, { after, afterEach } from "node:test";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const require = createRequire(import.meta.url);
const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const tsc = path.join(frontendRoot, "node_modules", "typescript", "bin", "tsc");
const originalFetch = globalThis.fetch;
const originalWindow = globalThis.window;
const originalCreateObjectURL = URL.createObjectURL;
let compiledRoot;
let modulesPromise;

async function compileTask11Modules() {
  compiledRoot = await mkdtemp(path.join(tmpdir(), "b2b2-task11-runtime-"));
  const sources = [
    "lib/api.ts",
    "lib/auth.ts",
    "lib/types.ts",
    "lib/filters.tsx",
    "lib/human-review.ts",
    "lib/human-review-api.ts",
    "lib/data-cache.tsx",
    "hooks/useHumanReview.ts",
    "components/AppShell.tsx",
    "components/human-review/DetectedDataPanel.tsx",
    "components/human-review/EvidencePanel.tsx",
    "components/human-review/EvidenceWorkspace.tsx",
    "components/human-review/AuditTimeline.tsx",
    "components/human-review/RelatedReviewList.tsx",
    "components/human-review/ReviewResultSummary.tsx",
    "components/human-review/SessionConfirmationBar.tsx",
    "components/human-review/ReviewSessionPage.tsx",
    "app/human-review/cases/[id]/page.tsx"
  ];
  const configPath = path.join(compiledRoot, "tsconfig.runtime.json");
  await writeFile(configPath, JSON.stringify({
    compilerOptions: {
      noEmit: false,
      incremental: false,
      module: "commonjs",
      moduleResolution: "node",
      target: "ES2022",
      jsx: "react-jsx",
      esModuleInterop: true,
      skipLibCheck: true,
      baseUrl: frontendRoot,
      paths: { "@/*": ["*"] },
      rootDir: frontendRoot,
      outDir: compiledRoot
    },
    files: sources.map((source) => path.join(frontendRoot, source))
  }), "utf8");
  const result = spawnSync(process.execPath, [tsc, "--project", configPath], {
    cwd: frontendRoot,
    encoding: "utf8"
  });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);

  process.env.NODE_PATH = path.join(frontendRoot, "node_modules");
  require("node:module")._initPaths();
  const Module = require("node:module");
  const originalResolve = Module._resolveFilename;
  Module._resolveFilename = function resolveTask11Alias(request, parent, isMain, options) {
    if (request.startsWith("@/")) request = path.join(compiledRoot, request.slice(2));
    return originalResolve.call(this, request, parent, isMain, options);
  };
  try {
    return {
      React: require("react"),
      renderToStaticMarkup: require("react-dom/server").renderToStaticMarkup,
      api: require(path.join(compiledRoot, "lib", "api.js")),
      humanReviewApi: require(path.join(compiledRoot, "lib", "human-review-api.js")),
      cache: require(path.join(compiledRoot, "lib", "data-cache.js")),
      detail: require(path.join(compiledRoot, "components", "human-review", "DetectedDataPanel.js")),
      evidence: require(path.join(compiledRoot, "components", "human-review", "EvidencePanel.js")),
      workspace: require(path.join(compiledRoot, "components", "human-review", "EvidenceWorkspace.js")),
      audit: require(path.join(compiledRoot, "components", "human-review", "AuditTimeline.js")),
      related: require(path.join(compiledRoot, "components", "human-review", "RelatedReviewList.js")),
      result: require(path.join(compiledRoot, "components", "human-review", "ReviewResultSummary.js")),
      confirmation: require(path.join(compiledRoot, "components", "human-review", "SessionConfirmationBar.js")),
      session: require(path.join(compiledRoot, "components", "human-review", "ReviewSessionPage.js"))
    };
  } finally {
    Module._resolveFilename = originalResolve;
  }
}

function findNodes(node, predicate, found = []) {
  if (!node || typeof node !== "object") return found;
  if (predicate(node)) found.push(node);
  for (const child of Array.isArray(node.props?.children) ? node.props.children : [node.props?.children]) {
    findNodes(child, predicate, found);
  }
  return found;
}

function toReactElement(React, node) {
  if (node === null || node === undefined || typeof node !== "object") return node;
  const children = Array.isArray(node.props?.children) ? node.props.children : [node.props?.children];
  const props = { ...node.props };
  delete props.children;
  return React.createElement(node.type, props, ...children.map((child) => toReactElement(React, child)));
}

function createComposedEvidenceRuntime(targets, initialProps) {
  const slots = [];
  const pendingEffects = [];
  let cursor = 0;
  let dirty = false;
  let mounted = true;
  let props = initialProps;
  let tree;
  const allocate = () => cursor++;
  const react = {
    useState(initialValue) {
      const index = allocate();
      if (!slots[index]) slots[index] = { kind: "state", value: typeof initialValue === "function" ? initialValue() : initialValue };
      return [slots[index].value, (nextValue) => {
        if (!mounted) return;
        const current = slots[index].value;
        const next = typeof nextValue === "function" ? nextValue(current) : nextValue;
        if (!Object.is(current, next)) { slots[index].value = next; dirty = true; }
      }];
    },
    useRef(initialValue) {
      const index = allocate();
      if (!slots[index]) slots[index] = { kind: "ref", value: { current: initialValue } };
      return slots[index].value;
    },
    useMemo(factory, dependencies) {
      const index = allocate();
      const current = slots[index];
      if (!current || !sameDependencies(current.dependencies, dependencies)) slots[index] = { kind: "memo", dependencies, value: factory() };
      return slots[index].value;
    },
    useCallback(callback, dependencies) { return react.useMemo(() => callback, dependencies); },
    useEffect(effect, dependencies) {
      const index = allocate();
      const current = slots[index];
      if (!current || !sameDependencies(current.dependencies, dependencies)) pendingEffects.push({ index, effect, dependencies });
    },
    createContext(defaultValue) { return { defaultValue }; },
    useContext(context) { return context.defaultValue; },
    useSyncExternalStore() { throw new Error("not used by active evidence"); }
  };
  const jsx = (type, nextProps) => ({ type, props: nextProps ?? {} });
  const execute = (node) => {
    if (!node || typeof node !== "object") return node;
    if (node.type === targets.Workspace || node.type === targets.EvidencePanel) return execute(node.type(node.props));
    const children = Array.isArray(node.props?.children) ? node.props.children : [node.props?.children];
    if (node.props && children.length) return { ...node, props: { ...node.props, children: Array.isArray(node.props.children) ? children.map(execute) : execute(children[0]) } };
    return node;
  };
  const render = (nextProps = props) => {
    props = nextProps;
    cursor = 0;
    dirty = false;
    tree = execute(targets.Workspace(props));
    return tree;
  };
  const flushEffects = () => {
    let passes = 0;
    while (pendingEffects.length || dirty) {
      const effects = pendingEffects.splice(0);
      for (const pending of effects) {
        const current = slots[pending.index];
        if (current?.cleanup) current.cleanup();
        const cleanup = pending.effect();
        slots[pending.index] = { kind: "effect", dependencies: pending.dependencies, cleanup: typeof cleanup === "function" ? cleanup : undefined };
      }
      if (dirty) render();
      assert.ok(++passes < 30, "composed evidence runtime failed to settle");
    }
    return tree;
  };
  return {
    react,
    jsx,
    mount() { render(); return flushEffects(); },
    render,
    flushEffects,
    flush() { if (dirty) render(); return flushEffects(); },
    unmount() { mounted = false; for (const slot of slots) if (slot?.kind === "effect" && slot.cleanup) slot.cleanup(); },
    get tree() { return tree; }
  };
}

async function loadComposedEvidenceWorkspace(runtime) {
  const loaded = await loadModules();
  const workspacePath = path.join(compiledRoot, "components", "human-review", "EvidenceWorkspace.js");
  const panelPath = path.join(compiledRoot, "components", "human-review", "EvidencePanel.js");
  const hookPath = path.join(compiledRoot, "hooks", "useHumanReview.js");
  const Module = require("node:module");
  const originalLoad = Module._load;
  Module._load = function loadComposedEvidenceDependency(request, parent, isMain) {
    if (parent?.filename?.startsWith(compiledRoot) && request === "react") return runtime.react;
    if (parent?.filename?.startsWith(compiledRoot) && request === "react/jsx-runtime") return { jsx: runtime.jsx, jsxs: runtime.jsx };
    return originalLoad.call(this, request, parent, isMain);
  };
  try {
    for (const modulePath of [workspacePath, panelPath, hookPath]) delete require.cache[require.resolve(modulePath)];
    return { Workspace: require(workspacePath).EvidenceWorkspace, EvidencePanel: require(panelPath).EvidencePanel, api: loaded.humanReviewApi.humanReviewApi };
  } finally {
    Module._load = originalLoad;
  }
}

function loadModules() {
  modulesPromise ??= compileTask11Modules();
  return modulesPromise;
}

async function loadEvidenceWorkspace() {
  const workspaceRoot = await mkdtemp(path.join(tmpdir(), "human-review-evidence-workspace-"));
  const configPath = path.join(workspaceRoot, "tsconfig.workspace.json");
  await writeFile(configPath, JSON.stringify({
    compilerOptions: {
      noEmit: false, incremental: false, module: "commonjs", moduleResolution: "node", target: "ES2022",
      jsx: "react-jsx", esModuleInterop: true, skipLibCheck: true, types: ["node"],
      typeRoots: [path.join(frontendRoot, "node_modules", "@types")], baseUrl: frontendRoot,
      paths: { "@/*": ["*"] }, rootDir: frontendRoot, outDir: workspaceRoot
    },
    files: [path.join(frontendRoot, "components/human-review/EvidenceWorkspace.tsx")]
  }), "utf8");
  const result = spawnSync(process.execPath, [tsc, "--project", configPath], { cwd: frontendRoot, encoding: "utf8" });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  return { workspaceRoot, workspacePath: path.join(workspaceRoot, "components", "human-review", "EvidenceWorkspace.js") };
}

function createWorkspaceRuntime(initialEvidence) {
  const slots = [];
  let cursor = 0;
  let evidence = initialEvidence;
  let result;
  const pendingEffects = [];
  const allocate = () => cursor++;
  const react = {
    useState(initialValue) {
      const index = allocate();
      if (!slots[index]) slots[index] = { kind: "state", value: typeof initialValue === "function" ? initialValue() : initialValue };
      return [slots[index].value, (nextValue) => {
        const current = slots[index].value;
        slots[index].value = typeof nextValue === "function" ? nextValue(current) : nextValue;
      }];
    },
    useRef(initialValue) {
      const index = allocate();
      if (!slots[index]) slots[index] = { kind: "ref", value: { current: initialValue } };
      return slots[index].value;
    },
    useEffect(effect, dependencies) {
      const index = allocate();
      const current = slots[index];
      if (!current || !sameDependencies(current.dependencies, dependencies)) pendingEffects.push({ index, effect, dependencies });
    }
  };
  return {
    react,
    setEvidence(nextEvidence) { evidence = nextEvidence; },
    useEvidence() { return evidence; },
    render(Component, props) { cursor = 0; pendingEffects.length = 0; result = Component(props); return result; },
    flushEffects() {
      for (const pending of pendingEffects.splice(0)) {
        const current = slots[pending.index];
        if (current?.cleanup) current.cleanup();
        const cleanup = pending.effect();
        slots[pending.index] = { kind: "effect", dependencies: pending.dependencies, cleanup: typeof cleanup === "function" ? cleanup : undefined };
      }
    },
    unmount() { for (const slot of slots) if (slot?.kind === "effect" && slot.cleanup) slot.cleanup(); },
    get result() { return result; }
  };
}

async function loadEvidenceWorkspaceWithRuntime(runtime) {
  const { workspaceRoot, workspacePath } = await loadEvidenceWorkspace();
  const Module = require("node:module");
  const originalLoad = Module._load;
  Module._load = function loadWorkspaceDependency(request, parent, isMain) {
    if (parent?.filename === workspacePath && request === "react") return runtime.react;
    if (parent?.filename === workspacePath && request === "../../hooks/useHumanReview") return { useActiveHumanReviewEvidence: () => runtime.useEvidence() };
    if (request === "react/jsx-runtime") return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) };
    return originalLoad.call(this, request, parent, isMain);
  };
  try {
    delete require.cache[require.resolve(workspacePath)];
    return { workspace: require(workspacePath), workspaceRoot };
  } finally {
    Module._load = originalLoad;
  }
}

function sameDependencies(left, right) {
  return Array.isArray(left)
    && Array.isArray(right)
    && left.length === right.length
    && left.every((value, index) => Object.is(value, right[index]));
}

function createHookRuntime(hook, initialCaseId) {
  const slots = [];
  let caseId = initialCaseId;
  let contextValue = null;
  let cursor = 0;
  let dirty = false;
  let mounted = true;
  let result;
  const pendingEffects = [];

  const allocate = () => cursor++;
  const react = {
    createContext(defaultValue) {
      return { defaultValue };
    },
    useContext(context) {
      return contextValue ?? context.defaultValue;
    },
    useState(initialValue) {
      const index = allocate();
      if (!slots[index]) {
        slots[index] = {
          kind: "state",
          value: typeof initialValue === "function" ? initialValue() : initialValue
        };
      }
      const setValue = (nextValue) => {
        if (!mounted) return;
        const current = slots[index].value;
        const next = typeof nextValue === "function" ? nextValue(current) : nextValue;
        if (!Object.is(current, next)) {
          slots[index].value = next;
          dirty = true;
        }
      };
      return [slots[index].value, setValue];
    },
    useRef(initialValue) {
      const index = allocate();
      if (!slots[index]) slots[index] = { kind: "ref", value: { current: initialValue } };
      return slots[index].value;
    },
    useMemo(factory, dependencies) {
      const index = allocate();
      const current = slots[index];
      if (!current || !sameDependencies(current.dependencies, dependencies)) {
        slots[index] = { kind: "memo", dependencies, value: factory() };
      }
      return slots[index].value;
    },
    useCallback(callback, dependencies) {
      return react.useMemo(() => callback, dependencies);
    },
    useEffect(effect, dependencies) {
      const index = allocate();
      const current = slots[index];
      if (!current || !sameDependencies(current.dependencies, dependencies)) {
        pendingEffects.push({ index, effect, dependencies });
      }
    },
    useSyncExternalStore() {
      throw new Error("useSyncExternalStore is not used by the hook regression harness");
    }
  };

  const renderOnly = () => {
    cursor = 0;
    dirty = false;
    pendingEffects.length = 0;
    result = hook(caseId);
    return result;
  };

  const flushEffects = () => {
    let passes = 0;
    while (pendingEffects.length || dirty) {
      const effects = pendingEffects.splice(0);
      for (const pending of effects) {
        const current = slots[pending.index];
        if (current?.cleanup) current.cleanup();
        const cleanup = pending.effect();
        slots[pending.index] = {
          kind: "effect",
          dependencies: pending.dependencies,
          cleanup: typeof cleanup === "function" ? cleanup : undefined
        };
      }
      if (dirty) renderOnly();
      passes += 1;
      assert.ok(passes < 20, "hook failed to settle");
    }
    return result;
  };

  const flush = () => {
    if (dirty) renderOnly();
    return flushEffects();
  };

  return {
    react,
    mount() {
      renderOnly();
      return flushEffects();
    },
    render(nextCaseId) {
      caseId = nextCaseId;
      return renderOnly();
    },
    update(nextCaseId) {
      caseId = nextCaseId;
      renderOnly();
      return flushEffects();
    },
    flush,
    flushEffects,
    setContext(nextContextValue) {
      contextValue = nextContextValue;
    },
    get result() {
      return result;
    },
    unmount() {
      mounted = false;
      for (const slot of slots) {
        if (slot?.kind === "effect" && slot.cleanup) slot.cleanup();
      }
    }
  };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

async function settleHook(runtime) {
  await new Promise((resolve) => setImmediate(resolve));
  return runtime.flush();
}

function componentDescendants(React, node) {
  if (!node || typeof node !== "object") return [];
  const children = React.Children.toArray(node.props?.children);
  return [node, ...children.flatMap((child) => componentDescendants(React, child))];
}

async function loadTask4Hooks(runtime, useCachedQuery = () => ({ data: undefined, error: null, isInitialLoading: false, isUpdating: false })) {
  const loaded = await loadModules();
  const hookPath = path.join(compiledRoot, "hooks", "useHumanReview.js");
  delete require.cache[require.resolve(hookPath)];
  const Module = require("node:module");
  const originalLoad = Module._load;
  Module._load = function loadTask4Dependency(request, parent, isMain) {
    if (parent?.filename === hookPath && request === "react") return runtime.react;
    if (parent?.filename === hookPath && request === "../lib/data-cache") {
      return {
        useCachedQuery,
        useDataCache: () => ({ invalidate: () => undefined })
      };
    }
    return originalLoad.call(this, request, parent, isMain);
  };
  try {
    return {
      hooks: require(hookPath),
      humanReviewApi: loaded.humanReviewApi.humanReviewApi,
      HumanReviewApiError: loaded.humanReviewApi.HumanReviewApiError,
      ApiFetchError: loaded.api.ApiFetchError
    };
  } finally {
    Module._load = originalLoad;
  }
}

async function loadTask4HooksWithRealCache(runtime) {
  const loaded = await loadModules();
  const hookPath = path.join(compiledRoot, "hooks", "useHumanReview.js");
  const cachePath = path.join(compiledRoot, "lib", "data-cache.js");
  delete require.cache[require.resolve(hookPath)];
  delete require.cache[require.resolve(cachePath)];
  const Module = require("node:module");
  const originalLoad = Module._load;
  Module._load = function loadTask4RealCacheDependency(request, parent, isMain) {
    if ((parent?.filename === hookPath || parent?.filename === cachePath) && request === "react") {
      return runtime.react;
    }
    return originalLoad.call(this, request, parent, isMain);
  };
  try {
    const dataCache = require(cachePath);
    return {
      hooks: require(hookPath),
      dataCache,
      humanReviewApi: loaded.humanReviewApi.humanReviewApi
    };
  } finally {
    Module._load = originalLoad;
  }
}

function caseDetail(overrides = {}) {
  return {
    id: "00000000-0000-0000-0000-000000000111",
    case_type: "person_identity",
    case_status: "resolved",
    scientific_status: "validated",
    document_id: 41,
    source_revision: "rev-4",
    source_page: 8,
    source_section: "Participantes",
    automatic_priority: 7,
    manual_priority: null,
    possible_kpi_impact: true,
    version: 3,
    created_at: "2026-08-01T10:00:00Z",
    target_table: "person_roles",
    target_pk: 919,
    field_path: "canonical_name",
    detected_value: "Ada  Lovelace",
    normalized_value: "ada lovelace",
    canonical_value: "Ada Lovelace",
    current_decision_id: "00000000-0000-0000-0000-000000000222",
    overrides: [{ field: "canonical_name", value: "Ada Lovelace", scope: "record", created_at: "2026-08-01T11:00:00Z" }],
    effective_memberships: ["Investigadora", "Autora"],
    counterpart_options: [],
    evidence_summary: {
      available: true,
      count: 1,
      document_name: "informe-publico.pdf",
      page: 8,
      section: "Participantes",
      fragment: "Ada Lovelace consta como autora."
    },
    ...overrides
  };
}

afterEach(() => {
  globalThis.fetch = originalFetch;
  if (originalWindow === undefined) delete globalThis.window;
  else globalThis.window = originalWindow;
  URL.createObjectURL = originalCreateObjectURL;
});

after(async () => {
  if (compiledRoot) await rm(compiledRoot, { recursive: true, force: true });
});

test("detail states deny by default, preserve safe support references and distinguish 403, 404 and 503", async () => {
  const { React, renderToStaticMarkup, api, humanReviewApi, detail } = await loadModules();
  const renderState = (props) => renderToStaticMarkup(React.createElement(detail.DetailStateNotice, props));

  assert.match(renderState({ state: "loading" }), /Cargando detalle/i);
  assert.equal(humanReviewApi.canViewHumanReviewDetail(undefined), false);
  assert.equal(humanReviewApi.canViewHumanReviewDetail({ capability: null, actions: [] }), false);
  assert.equal(humanReviewApi.canViewHumanReviewDetail({ capability: "RESEARCH_MANAGER", actions: ["view_foundations", "view_audit"] }), true);
  assert.equal(humanReviewApi.canViewHumanReviewDetail({ capability: "SYSTEM_ADMIN", actions: ["view_foundations", "view_audit", "manage_technical_access"] }), true);

  for (const [status, expected] of [[403, /No tienes acceso/i], [404, /no existe/i], [503, /no est\u00e1 disponible/i]]) {
    const error = new humanReviewApi.HumanReviewApiError(new api.ApiFetchError(
      "dropbox_path:/private/report.pdf SELECT secret",
      { status, correlation_id: `support-${status}`, details: { source_path: "C:/private/report.pdf" } }
    ));
    const html = renderState({ state: "error", error });
    assert.match(html, expected);
    assert.match(html, new RegExp(`support-${status}`));
    assert.doesNotMatch(html, /dropbox_path|source_path|private\/report|SELECT secret/i);
  }

  const general = new humanReviewApi.HumanReviewApiError(new api.ApiFetchError("SQL trace", { status: 500 }));
  assert.match(renderState({ state: "error", error: general }), /No pudimos cargar/i);
});

test("session confirmation derives prepared state from current detail and blocks invalid or conflicted drafts", async () => {
  const { React, renderToStaticMarkup, confirmation } = await loadModules();
  const detail = caseDetail({ case_status: "pending", scientific_status: "pending", current_decision_id: null, case_type: "product", field_path: "product_title" });
  const draft = {
    action: "correct",
    scope: "record",
    payload: { case_type: "product", product_title: "Título corregido", scientific_status: "validated" },
    reason: "",
    correlation_id: "draft-current-validation"
  };
  const base = {
    selectedCaseIds: [detail.id],
    draftByCaseId: { [detail.id]: draft },
    detailByCaseId: { [detail.id]: detail },
    validationByCaseId: { [detail.id]: [] },
    resultByCaseId: {},
    requestStateByCaseId: { [detail.id]: "idle" },
    conflictByCaseId: {},
    onConfirm() {}
  };
  const invalid = renderToStaticMarkup(React.createElement(confirmation.SessionConfirmationBar, base));
  assert.match(invalid, /Preparados 0/);
  assert.match(invalid, /Inv.lidos 1/);
  assert.match(invalid, /<button[^>]*disabled/);

  const validDraft = { ...draft, reason: "Motivo científicamente verificado" };
  const blockedConflict = {
    operation: "apply", revert_decision_id: null, draft: validDraft, preview: null,
    cas: { expected_version: detail.version, expected_current_decision_id: null }, correlation_id: "support-409",
    reload_error_correlation_id: null, requires_manual_confirmation: true, command_retried: false,
    needs_revision: true, awaiting_reload: false, operation_abandoned: false
  };
  const conflicted = renderToStaticMarkup(React.createElement(confirmation.SessionConfirmationBar, {
    ...base,
    draftByCaseId: { [detail.id]: validDraft },
    conflictByCaseId: { [detail.id]: blockedConflict }
  }));
  assert.match(conflicted, /Preparados 0/);
  assert.match(conflicted, /Inv.lidos 1/);
  assert.match(conflicted, /<button[^>]*disabled/);
});


test("related reviews use stable case identity, accessible disclosure state and keep confirmed rows visible read-only", async () => {
  const { React, renderToStaticMarkup, related } = await loadModules();
  const selections = [];
  const markup = renderToStaticMarkup(React.createElement(related.RelatedReviewList, {
    items: [
      {
        case_id: "case-pending",
        case_type: "person_identity",
        case_status: "pending",
        scientific_status: "pending",
        version: 2,
        current_decision_id: null,
        detected_value: "Ada detectada",
        normalized_value: "Ada normalizada",
        canonical_value: "Ada canónica",
        possible_kpi_impact: true,
        allowed_actions: ["approve", "correct", "link"],
        evidence_summary: {
          available: true,
          document_name: "autores-publicos.pdf",
          page: 2,
          section: "Autores"
        }
      },
      {
        case_id: "case-confirmed",
        case_type: "product",
        case_status: "resolved",
        scientific_status: "validated",
        version: 4,
        current_decision_id: "internal-decision-must-not-render",
        detected_value: "Producto confirmado",
        normalized_value: "Producto confirmado",
        canonical_value: "Producto final",
        possible_kpi_impact: false,
        allowed_actions: [],
        evidence_summary: { available: true }
      }
    ],
    activeCaseId: "case-pending",
    selectedCaseIds: ["case-pending"],
    resultByCaseId: {
      "case-confirmed": { status: "confirmed", correlationId: "public-ref-17", message: "Confirmado" }
    },
    requestStateByCaseId: { "case-pending": "idle", "case-confirmed": "succeeded" },
    onSelect: (caseId) => selections.push(caseId),
    onToggleSelected: () => undefined
  }));

  assert.match(markup, /aria-current="true"/);
  assert.match(markup, /aria-expanded="true"/);
  assert.match(markup, /aria-expanded="false"/);
  assert.match(markup, /Pendiente/);
  assert.match(markup, /Confirmado/);
  assert.match(markup, /Identidad de persona/);
  assert.match(markup, /Ada detectada/);
  assert.match(markup, /autores-publicos\.pdf/);
  assert.match(markup, /p\u00e1g\. 2/);
  assert.match(markup, /Autores/);
  assert.match(markup, /Confirmar la identidad de la persona detectada/);
  assert.match(markup, /Producci\u00f3n cient\u00edfica/);
  assert.match(markup, /Producto confirmado/);
  assert.match(markup, /<details/);
  assert.match(markup, /Ver referencia p\u00fablica del caso/);
  assert.doesNotMatch(markup, /internal-decision-must-not-render|target_pk|target_table|document_key|stable_target_key/);

  const tree = related.RelatedReviewList({
    items: [{
      case_id: "case-pending", case_type: "person_identity", case_status: "pending", scientific_status: "pending",
      version: 2, current_decision_id: null, detected_value: "Ada", normalized_value: "Ada", canonical_value: "Ada",
      possible_kpi_impact: true, allowed_actions: ["approve"], evidence_summary: { available: true }
    }],
    activeCaseId: "case-pending",
    selectedCaseIds: [],
    resultByCaseId: {},
    requestStateByCaseId: { "case-pending": "idle" },
    onSelect: (caseId) => selections.push(caseId),
    onToggleSelected: () => undefined
  });
  const listItem = React.Children.toArray(tree.props.children)[1].props.children[0];
  const selectButton = React.Children.toArray(listItem.props.children).find((child) => child?.type === "button");
  selectButton.props.onClick();
  assert.deepEqual(selections, ["case-pending"]);
});

test("related selector keeps a UUID out of its collapsed label and only in the advanced reference", async () => {
  const { React, renderToStaticMarkup, related } = await loadModules();
  const caseId = "00000000-0000-0000-0000-000000000321";
  const markup = renderToStaticMarkup(React.createElement(related.RelatedReviewList, {
    items: [{
      case_id: caseId,
      case_type: "author_identity",
      case_status: "pending",
      scientific_status: "pending",
      version: 1,
      current_decision_id: null,
      detected_value: "María Pérez",
      normalized_value: "María Pérez",
      canonical_value: null,
      possible_kpi_impact: false,
      allowed_actions: ["approve", "correct", "link"],
      evidence_summary: { document_name: "autores.pdf", page: 4, section: "Autores" }
    }],
    activeCaseId: caseId,
    selectedCaseIds: [caseId],
    resultByCaseId: {},
    requestStateByCaseId: {},
    onSelect: () => undefined,
    onToggleSelected: () => undefined
  }));
  const collapsedButton = markup.match(/<button[\s\S]*?<\/button>/)?.[0] ?? "";

  assert.match(collapsedButton, /Identidad de autor[\s\S]*Mar\u00eda P\u00e9rez[\s\S]*autores\.pdf[\s\S]*p\u00e1g\. 4[\s\S]*Pendiente/);
  assert.doesNotMatch(collapsedButton, new RegExp(caseId));
  assert.match(markup, new RegExp(`<details[\\s\\S]*Ver referencia pública del caso[\\s\\S]*${caseId}[\\s\\S]*</details>`));
});

test("an active confirmed related row is forced collapsed and read-only while its result stays visible", async () => {
  const { React, renderToStaticMarkup, related } = await loadModules();
  const selections = [];
  const items = [
    {
      case_id: "case-pending", case_type: "person_identity", case_status: "pending", scientific_status: "pending",
      version: 2, current_decision_id: null, detected_value: "Pendiente editable", normalized_value: "Pendiente", canonical_value: "Valor pendiente expandido",
      possible_kpi_impact: true, allowed_actions: ["approve"], evidence_summary: { available: true }
    },
    {
      case_id: "case-confirmed", case_type: "product", case_status: "resolved", scientific_status: "validated",
      version: 4, current_decision_id: "must-not-render", detected_value: "Producto confirmado", normalized_value: "Producto", canonical_value: "Contenido editable no debe aparecer",
      possible_kpi_impact: false, allowed_actions: [], evidence_summary: { available: true }
    }
  ];
  const props = {
    items,
    activeCaseId: "case-confirmed",
    selectedCaseIds: ["case-pending", "case-confirmed"],
    resultByCaseId: {
      "case-confirmed": { status: "confirmed", correlationId: "public-result-ref", message: "Resultado público confirmado" }
    },
    requestStateByCaseId: { "case-pending": "idle", "case-confirmed": "succeeded" },
    onSelect: (caseId) => selections.push(caseId),
    onToggleSelected: () => { throw new Error("confirmed checkbox must not mutate"); }
  };
  const markup = renderToStaticMarkup(React.createElement(related.RelatedReviewList, props));
  assert.doesNotMatch(markup, /Contenido editable no debe aparecer/);
  assert.match(markup, /Resultado público confirmado/);
  assert.match(markup, /public-result-ref/);
  assert.doesNotMatch(markup, /must-not-render/);

  const tree = related.RelatedReviewList(props);
  const rows = React.Children.toArray(React.Children.toArray(tree.props.children)[1].props.children);
  const pendingButton = React.Children.toArray(rows[0].props.children).find((child) => child?.type === "button");
  const confirmedChildren = React.Children.toArray(rows[1].props.children);
  const confirmedButton = confirmedChildren.find((child) => child?.type === "button");
  const confirmedCheckbox = componentDescendants(React, rows[1]).find((node) => node.type === "input" && node.props.type === "checkbox");
  assert.equal(confirmedButton.props["aria-expanded"], false);
  assert.equal(confirmedButton.props["aria-current"], "true");
  assert.equal(confirmedCheckbox.props.disabled, true);
  pendingButton.props.onClick();
  assert.deepEqual(selections, ["case-pending"]);
});

test("review result summary accepts only typed sanitized result fields and fixed labels", async () => {
  const { React, renderToStaticMarkup, result } = await loadModules();
  const malicious = {
    status: "conflict",
    correlationId: "public-reference-42",
    message: "El caso cambió; revísalo antes de confirmar.",
    raw_response: "postgresql://secret@host/internal",
    target_table: "person_roles",
    document_key: "C:/private/report.pdf"
  };
  const markup = renderToStaticMarkup(React.createElement(result.ReviewResultSummary, { result: malicious }));

  assert.match(markup, /Revisión requiere atención/);
  assert.match(markup, /El caso cambió/);
  assert.match(markup, /public-reference-42/);
  assert.doesNotMatch(markup, /postgresql|secret@host|person_roles|private\/report|target_table|document_key/i);
});

test("detected, normalized and projected layers render without technical identifiers or Task 12 controls", async () => {
  const { React, renderToStaticMarkup, detail } = await loadModules();
  const html = renderToStaticMarkup(React.createElement(detail.DetectedDataPanel, { detail: caseDetail() }));

  assert.match(html, /Informaci\u00f3n detectada/);
  assert.match(html, /Ada  Lovelace/);
  assert.match(html, /Informaci\u00f3n normalizada/);
  assert.match(html, /ada lovelace/);
  assert.match(html, /Valor can\u00f3nico o proyectado/);
  assert.match(html, /Ada Lovelace/);
  assert.match(html, /Ajustes aplicados/);
  assert.doesNotMatch(html, /Overrides vigentes/i);
  assert.match(html, /Investigadora/);
  assert.match(html, /Autora/);
  assert.match(html, /Decisi\u00f3n vigente/);
  assert.match(html, /solo lectura/i);
  assert.match(html, /Impacto KPI potencial/);
  assert.doesNotMatch(html, /919|00000000-0000-0000-0000-000000000222/);
  assert.doesNotMatch(html, /Aprobar|Corregir|Vincular|Descartar|Revertir|propuesta|preview|conflicto/i);
});

test("null detail values remain explicitly unavailable and sensitive values are redacted", async () => {
  const { React, renderToStaticMarkup, detail } = await loadModules();
  const html = renderToStaticMarkup(React.createElement(detail.DetectedDataPanel, {
    detail: caseDetail({
      detected_value: null,
      normalized_value: "dropbox_path:/secret/file.pdf",
      canonical_value: "C:\\private\\canonical.txt",
      overrides: [{ field: "source_path", value: "s3://secret-bucket/object.pdf" }],
      effective_memberships: ["Autora", "document_key:internal"]
    })
  }));

  assert.match(html, /No disponible/);
  assert.match(html, /Autora/);
  assert.doesNotMatch(html, /dropbox_path|private\\canonical|source_path|secret-bucket|document_key/i);
});

test("audit timeline is chronological, keeps reversals and exposes only mapped safe fields", async () => {
  const { React, renderToStaticMarkup, api, humanReviewApi, audit } = await loadModules();
  const items = [
    {
      id: "00000000-0000-0000-0000-000000000007",
      event_type: "override_created",
      actor_id: 15,
      created_at: "2026-08-01T10:00:00Z",
      correlation_id: "corr-adjustment",
      summary: "Se aplicó un ajuste"
    },
    {
      id: "00000000-0000-0000-0000-000000000009",
      event_type: "functional_reversion",
      actor_id: 17,
      decision_id: "decision-private-2",
      created_at: "2026-08-01T12:00:00Z",
      correlation_id: "corr-revert",
      summary: "Se revirti\u00f3 la decisi\u00f3n",
      payload: { decision_type: "reverted", previous_case_status: "resolved", resulting_case_status: "reopened", secret: "source_path:/private" }
    },
    {
      id: "00000000-0000-0000-0000-000000000008",
      event_type: "scientific_decision_applied",
      actor_id: 16,
      decision_id: "decision-private-1",
      created_at: "2026-08-01T11:00:00Z",
      correlation_id: "corr-apply",
      summary: "Decisi\u00f3n aplicada",
      payload: { decision_type: "validated", previous_case_status: "pending", resulting_case_status: "resolved" }
    }
  ];
  const html = renderToStaticMarkup(React.createElement(audit.AuditTimeline, {
    response: { items, page: 1, page_size: 25, total: 3, correlation_id: "corr-page" },
    isInitialLoading: false,
    onPageChange: () => undefined
  }));

  assert.ok(html.indexOf("Decisi\u00f3n aplicada") < html.indexOf("Se revirti\u00f3"));
  assert.match(html, /corr-apply/);
  assert.match(html, /corr-revert/);
  assert.match(html, /Pendiente/);
  assert.match(html, /Reabierto/);
  assert.match(html, /Validaci\u00f3n/);
  assert.match(html, /Reversi\u00f3n/);
  assert.match(html, /Ajuste aplicado/);
  assert.doesNotMatch(html, /Override creado/);
  assert.doesNotMatch(html, /Evento de auditor\u00eda/);
  assert.doesNotMatch(html, /decision-private|actor_id|source_path|private/);
  assert.deepEqual(items.map((item) => item.id), ["00000000-0000-0000-0000-000000000007", "00000000-0000-0000-0000-000000000009", "00000000-0000-0000-0000-000000000008"]);

  const empty = renderToStaticMarkup(React.createElement(audit.AuditTimeline, {
    response: { items: [], page: 1, page_size: 25, total: 0, correlation_id: "corr-empty" },
    isInitialLoading: false,
    onPageChange: () => undefined
  }));
  assert.match(empty, /No hay eventos de auditor\u00eda/i);

  const unavailable = new humanReviewApi.HumanReviewApiError(new api.ApiFetchError("internal", { status: 503, correlation_id: "corr-error" }));
  const unavailableHtml = renderToStaticMarkup(React.createElement(audit.AuditTimeline, {
    error: unavailable,
    isInitialLoading: false,
    onPageChange: () => undefined
  }));
  assert.match(unavailableHtml, /no est\u00e1 disponible/i);
  assert.match(unavailableHtml, /corr-error/);
  assert.doesNotMatch(unavailableHtml, /No hay eventos de auditor\u00eda/i);
});

test("related hooks isolate cache by case ID, skip null IDs and start without detail or evidence waterfalls", async () => {
  const cacheCalls = [];
  const relatedCalls = [];
  let detailCalls = 0;
  let evidenceCalls = 0;
  const runtime = createHookRuntime(() => undefined, null);
  const useCachedQuery = (key, fetcher, options) => {
    cacheCalls.push({ key, enabled: options.enabled });
    if (options.enabled) void fetcher();
    return { data: undefined, error: null, isInitialLoading: Boolean(options.enabled), isUpdating: false };
  };
  const loaded = await loadTask4Hooks(runtime, useCachedQuery);
  const originals = {
    getRelated: loaded.humanReviewApi.getRelated,
    getCase: loaded.humanReviewApi.getCase,
    getEvidence: loaded.humanReviewApi.getEvidence
  };
  loaded.humanReviewApi.getRelated = async (caseId) => {
    relatedCalls.push(caseId);
    return { entity: {}, items: [], total_pending: 0, truncated: false, correlation_id: "corr" };
  };
  loaded.humanReviewApi.getCase = async () => { detailCalls += 1; return caseDetail(); };
  loaded.humanReviewApi.getEvidence = async () => { evidenceCalls += 1; return new Blob(); };
  try {
    loaded.hooks.useHumanReviewRelated("case/A");
    loaded.hooks.useHumanReviewRelated("case/B");
    loaded.hooks.useHumanReviewRelated(null);

    assert.deepEqual(cacheCalls, [
      { key: "human-review:related:case/A", enabled: true },
      { key: "human-review:related:case/B", enabled: true },
      { key: "human-review:related:disabled", enabled: false }
    ]);
    assert.deepEqual(relatedCalls, ["case/A", "case/B"]);
    assert.equal(detailCalls, 0);
    assert.equal(evidenceCalls, 0);
  } finally {
    Object.assign(loaded.humanReviewApi, originals);
  }
});

test("related hook masks settled A cache data during the B render before passive effects", async () => {
  const responseA = {
    entity: { public_type: "person", public_id: "person:A", display_name: "Person A" },
    items: [],
    total_pending: 0,
    truncated: false,
    correlation_id: "corr-A"
  };
  const pendingB = deferred();
  const relatedCalls = [];
  let relatedHook;
  const runtime = createHookRuntime((caseId) => relatedHook(caseId), "case-A");
  const loaded = await loadTask4HooksWithRealCache(runtime);
  relatedHook = loaded.hooks.useHumanReviewRelated;
  const store = loaded.dataCache.createDataCacheStore();
  store.set("human-review:related:case-A", responseA);
  runtime.setContext({
    get: store.get,
    set: store.set,
    getInflight: store.getInflight,
    setInflight: store.setInflight,
    clearInflight: store.clearInflight,
    getVersion: store.getVersion,
    invalidate: store.invalidate,
    revision: store.getSnapshot()
  });
  const originalGetRelated = loaded.humanReviewApi.getRelated;
  loaded.humanReviewApi.getRelated = (caseId) => {
    relatedCalls.push(caseId);
    return pendingB.promise;
  };
  try {
    const mounted = runtime.mount();
    assert.equal(mounted.data.entity.public_id, "person:A");
    assert.equal(mounted.isInitialLoading, false);

    const bRender = runtime.render("case-B");
    assert.equal(bRender.data, undefined);
    assert.equal(bRender.error, null);
    assert.equal(bRender.isInitialLoading, true);
    assert.equal(bRender.isUpdating, false);
    assert.deepEqual(relatedCalls, []);

    runtime.flushEffects();
    assert.deepEqual(relatedCalls, ["case-B"]);
  } finally {
    runtime.unmount();
    pendingB.resolve({ ...responseA, entity: { ...responseA.entity, public_id: "person:B" } });
    await Promise.resolve();
    loaded.humanReviewApi.getRelated = originalGetRelated;
  }
});

test("active evidence masks a settled A blob during the B render before passive effects", async () => {
  const requests = [];
  let activeHook;
  const runtime = createHookRuntime((caseId) => activeHook(caseId), "case-A");
  const loaded = await loadTask4Hooks(runtime);
  activeHook = loaded.hooks.useActiveHumanReviewEvidence;
  const originalGetEvidence = loaded.humanReviewApi.getEvidence;
  loaded.humanReviewApi.getEvidence = (caseId, signal) => {
    const pending = deferred();
    requests.push({ caseId, signal, pending });
    return pending.promise;
  };
  try {
    runtime.mount();
    const blobA = new Blob(["settled-A"]);
    requests[0].pending.resolve(blobA);
    await settleHook(runtime);
    assert.strictEqual(runtime.result.blob, blobA);
    assert.equal(runtime.result.isLoading, false);

    const bRender = runtime.render("case-B");
    assert.equal(bRender.blob, null);
    assert.equal(bRender.error, null);
    assert.equal(bRender.isLoading, true);
    assert.equal(requests.length, 1);

    runtime.flushEffects();
    assert.equal(requests[0].signal.aborted, true);
    assert.equal(requests[1].caseId, "case-B");
  } finally {
    runtime.unmount();
    loaded.humanReviewApi.getEvidence = originalGetEvidence;
  }
});

test("active evidence masks a settled A error during the B render before passive effects", async () => {
  const requests = [];
  let activeHook;
  const runtime = createHookRuntime((caseId) => activeHook(caseId), "case-A");
  const loaded = await loadTask4Hooks(runtime);
  activeHook = loaded.hooks.useActiveHumanReviewEvidence;
  const originalGetEvidence = loaded.humanReviewApi.getEvidence;
  loaded.humanReviewApi.getEvidence = (caseId, signal) => {
    const pending = deferred();
    requests.push({ caseId, signal, pending });
    return pending.promise;
  };
  try {
    runtime.mount();
    const errorA = new loaded.HumanReviewApiError(
      new loaded.ApiFetchError("case A unavailable", { status: 503 })
    );
    requests[0].pending.reject(errorA);
    await settleHook(runtime);
    assert.strictEqual(runtime.result.error, errorA);
    assert.equal(runtime.result.isLoading, false);

    const bRender = runtime.render("case-B");
    assert.equal(bRender.blob, null);
    assert.equal(bRender.error, null);
    assert.equal(bRender.isLoading, true);
    assert.equal(requests.length, 1);

    runtime.flushEffects();
    assert.equal(requests[0].signal.aborted, true);
    assert.equal(requests[1].caseId, "case-B");
  } finally {
    runtime.unmount();
    loaded.humanReviewApi.getEvidence = originalGetEvidence;
  }
});

test("active evidence switches A to B by aborting A and rejecting its stale completion", async () => {
  const requests = [];
  let activeHook;
  const runtime = createHookRuntime((caseId) => activeHook(caseId), "case-A");
  const loaded = await loadTask4Hooks(runtime);
  activeHook = loaded.hooks.useActiveHumanReviewEvidence;
  const originalGetEvidence = loaded.humanReviewApi.getEvidence;
  loaded.humanReviewApi.getEvidence = (caseId, signal) => {
    const pending = deferred();
    requests.push({ caseId, signal, pending });
    return pending.promise;
  };
  let objectUrlCalls = 0;
  URL.createObjectURL = () => { objectUrlCalls += 1; return "blob:forbidden"; };
  try {
    const initial = runtime.mount();
    assert.deepEqual(Object.keys(initial).sort(), ["blob", "error", "isLoading", "reload"]);
    assert.equal(initial.blob, null);
    assert.equal(initial.error, null);
    assert.equal(initial.isLoading, true);
    assert.deepEqual(requests.map(({ caseId }) => caseId), ["case-A"]);

    runtime.update("case-B");
    assert.equal(requests[0].signal.aborted, true);
    assert.equal(requests[1].caseId, "case-B");
    assert.equal(requests[1].signal.aborted, false);

    const staleBlob = new Blob(["stale-A"]);
    requests[0].pending.resolve(staleBlob);
    await settleHook(runtime);
    assert.equal(runtime.result.blob, null);
    assert.equal(runtime.result.error, null);
    assert.equal(runtime.result.isLoading, true);

    const activeBlob = new Blob(["active-B"]);
    requests[1].pending.resolve(activeBlob);
    await settleHook(runtime);
    assert.strictEqual(runtime.result.blob, activeBlob);
    assert.equal(runtime.result.error, null);
    assert.equal(runtime.result.isLoading, false);
    assert.equal(objectUrlCalls, 0);
  } finally {
    runtime.unmount();
    loaded.humanReviewApi.getEvidence = originalGetEvidence;
  }
});

test("active evidence aborts on unmount", async () => {
  const requests = [];
  let activeHook;
  const runtime = createHookRuntime((caseId) => activeHook(caseId), "case-unmount");
  const loaded = await loadTask4Hooks(runtime);
  activeHook = loaded.hooks.useActiveHumanReviewEvidence;
  const originalGetEvidence = loaded.humanReviewApi.getEvidence;
  loaded.humanReviewApi.getEvidence = (caseId, signal) => {
    const pending = deferred();
    requests.push({ caseId, signal, pending });
    return pending.promise;
  };
  try {
    runtime.mount();
    assert.equal(requests[0].signal.aborted, false);
    runtime.unmount();
    assert.equal(requests[0].signal.aborted, true);
    requests[0].pending.resolve(new Blob(["late"]));
    await Promise.resolve();
  } finally {
    loaded.humanReviewApi.getEvidence = originalGetEvidence;
  }
});

test("active evidence reload aborts the prior request and ignores its stale rejection", async () => {
  const requests = [];
  let activeHook;
  const runtime = createHookRuntime((caseId) => activeHook(caseId), "case-reload");
  const loaded = await loadTask4Hooks(runtime);
  activeHook = loaded.hooks.useActiveHumanReviewEvidence;
  const originalGetEvidence = loaded.humanReviewApi.getEvidence;
  loaded.humanReviewApi.getEvidence = (caseId, signal) => {
    const pending = deferred();
    requests.push({ caseId, signal, pending });
    return pending.promise;
  };
  try {
    runtime.mount();
    runtime.result.reload();
    runtime.flush();
    assert.equal(requests.length, 2);
    assert.equal(requests[0].signal.aborted, true);

    requests[0].pending.reject(new loaded.HumanReviewApiError(
      new loaded.ApiFetchError("stale failure", { status: 503 })
    ));
    await settleHook(runtime);
    assert.equal(runtime.result.error, null);
    assert.equal(runtime.result.isLoading, true);

    const reloadedBlob = new Blob(["reloaded"]);
    requests[1].pending.resolve(reloadedBlob);
    await settleHook(runtime);
    assert.strictEqual(runtime.result.blob, reloadedBlob);
    assert.equal(runtime.result.error, null);
    assert.equal(runtime.result.isLoading, false);
  } finally {
    runtime.unmount();
    loaded.humanReviewApi.getEvidence = originalGetEvidence;
  }
});

test("evidence uses authenticated apiFetchBlob and never places credentials in the URL", async () => {
  const { humanReviewApi } = await loadModules();
  let request;
  globalThis.window = { localStorage: { getItem: () => "token-secret" } };
  globalThis.fetch = async (url, options) => {
    request = { url: String(url), options };
    return new Response(new Blob(["%PDF-1.4\n"], { type: "application/pdf" }), {
      status: 200,
      headers: { "Content-Type": "application/pdf" }
    });
  };

  const blob = await humanReviewApi.humanReviewApi.getEvidence("case id/one");
  assert.equal(blob.type, "application/pdf");
  assert.match(request.url, /\/human-review\/cases\/case%20id%2Fone\/evidence$/);
  assert.doesNotMatch(request.url, /token|secret|\?/i);
  assert.equal(new Headers(request.options.headers).get("Authorization"), "Bearer token-secret");
  assert.equal(new Headers(request.options.headers).has("Content-Type"), false);
});

test("a named imported PDF is opened through an authenticated local preview when strict evidence is unavailable", async () => {
  const { React, renderToStaticMarkup, evidence, api } = await loadModules();
  let request;
  globalThis.window = { localStorage: { getItem: () => "token-secret" } };
  globalThis.fetch = async (url, options) => {
    request = { url: String(url), options };
    return new Response(new Blob(["%PDF-1.4\n"], { type: "application/pdf" }), {
      status: 200,
      headers: { "Content-Type": "application/pdf" }
    });
  };

  const importedPdf = await api.api.importJobFile(42);
  assert.equal(importedPdf.type, "application/pdf");
  assert.match(request.url, /\/imports\/jobs\/42\/file$/);
  assert.doesNotMatch(request.url, /token|secret|\?/i);
  assert.equal(new Headers(request.options.headers).get("Authorization"), "Bearer token-secret");

  const html = renderToStaticMarkup(React.createElement(evidence.EvidencePanel, {
    summary: { available: false, document_name: "informe-importado.pdf", page: 3 },
    blob: null,
    objectUrl: null,
    documentObjectUrl: "blob:imported-pdf",
    error: null,
    documentError: null,
    isLoading: false,
    isDocumentLoading: false,
    onLoad: () => undefined,
    onLoadDocument: () => undefined
  }));
  assert.match(html, /informe-importado\.pdf/);
  assert.match(html, /Abrir PDF del documento en una pestaña nueva/);
  assert.match(html, /href="blob:imported-pdf"/);
  assert.match(html, /<iframe[^>]+src="blob:imported-pdf#page=3"/);
  assert.doesNotMatch(html, /<(?:a|iframe)[^>]+(?:href|src)="https?:\/\//i);
});

test("evidence object URLs are replaced and revoked and unavailable evidence stays inert", async () => {
  const { React, renderToStaticMarkup, evidence } = await loadModules();
  const calls = [];
  const urlApi = {
    createObjectURL(blob) { calls.push(["create", blob.size]); return "blob:task11-local"; },
    revokeObjectURL(url) { calls.push(["revoke", url]); }
  };
  const next = evidence.replaceEvidenceObjectUrl(urlApi, "blob:previous", new Blob(["pdf"]));
  assert.equal(next, "blob:task11-local");
  evidence.revokeEvidenceObjectUrl(urlApi, next);
  assert.deepEqual(calls, [["revoke", "blob:previous"], ["create", 3], ["revoke", "blob:task11-local"]]);

  const unavailable = renderToStaticMarkup(React.createElement(evidence.EvidencePanel, {
    summary: { available: false },
    blob: null,
    error: null,
    isLoading: false,
    onLoad: () => undefined
  }));
  assert.match(unavailable, /El visor de evidencia validada no está disponible para este dato/i);
  assert.doesNotMatch(unavailable, /button|blob:/i);
});

test("evidence renders only sanitized public metadata", async () => {
  const { React, renderToStaticMarkup, evidence } = await loadModules();
  const html = renderToStaticMarkup(React.createElement(evidence.EvidencePanel, {
    summary: {
      available: true,
      document_name: "informe-publico.pdf",
      page: 8,
      section: "Participantes",
      fragment: "Fragmento cient\u00edfico verificado",
      locator: "dropbox_path:/private/report.pdf",
      stream_path: "human-review/cases/private/evidence",
      document_key: "secret-key",
      source_path: "C:/private/report.pdf",
      bucket: "secret-bucket",
      object_key: "private/object.pdf"
    },
    blob: null,
    error: null,
    isLoading: false,
    onLoad: () => undefined
  }));

  assert.match(html, /informe-publico\.pdf/);
  assert.match(html, /P\u00e1gina 8/);
  assert.match(html, /Participantes/);
  assert.match(html, /Fragmento cient\u00edfico verificado/);
  assert.doesNotMatch(html, /dropbox_path|stream_path|document_key|source_path|bucket|object_key|private\/report|secret-key/i);
  assert.doesNotMatch(html, /dangerouslySetInnerHTML/i);
});

test("available evidence always renders a safe labeled fragment fallback", async () => {
  const { React, renderToStaticMarkup, evidence } = await loadModules();
  for (const fragment of [null, "", "dropbox_path:/private/secret.pdf"]) {
    const html = renderToStaticMarkup(React.createElement(evidence.EvidencePanel, {
      summary: { available: true, document_name: "public.pdf", page: 1, section: "Resultados", fragment },
      blob: null, objectUrl: null, error: null, isLoading: false, onLoad: () => undefined
    }));
    assert.match(html, /Fragmento/);
    assert.match(html, /No disponible/);
    assert.doesNotMatch(html, /dropbox_path|private\/secret/i);
  }
});

test("active evidence workspace keeps B as the only visible evidence through late A2 completion", async () => {
  const requests = [];
  const controllers = [];
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;
  const OriginalAbortController = globalThis.AbortController;
  class CountingAbortController extends OriginalAbortController {
    constructor() { super(); this.abortCalls = 0; controllers.push(this); }
    abort(reason) { this.abortCalls += 1; return super.abort(reason); }
  }
  const urlCalls = [];
  URL.createObjectURL = (blob) => {
    const url = `blob:workspace-${urlCalls.filter(([kind]) => kind === "create").length + 1}`;
    urlCalls.push(["create", url, blob.size]);
    return url;
  };
  URL.revokeObjectURL = (url) => urlCalls.push(["revoke", url]);
  let runtime;
  let loaded;
  let originalGetEvidence;
  globalThis.AbortController = CountingAbortController;
  try {
    const targets = {};
    runtime = createComposedEvidenceRuntime(targets, {
      caseId: "case-A",
      evidenceSummary: { available: true, document_name: "public-A.pdf", page: 2, section: "A", fragment: "Fragmento A" }
    });
    loaded = await loadComposedEvidenceWorkspace(runtime);
    targets.Workspace = loaded.Workspace;
    targets.EvidencePanel = loaded.EvidencePanel;
    originalGetEvidence = loaded.api.getEvidence;
    loaded.api.getEvidence = (caseId, signal) => {
      const pending = deferred();
      requests.push({ caseId, signal, pending });
      return pending.promise;
    };
    runtime.mount();
    const blobA = new Blob(["A"]);
    requests[0].pending.resolve(blobA);
    await new Promise((resolve) => setImmediate(resolve));
    runtime.flush();
    assert.equal(findNodes(runtime.tree, (node) => node.type === "iframe")[0].props.src, "blob:workspace-1#page=2");
    findNodes(runtime.tree, (node) => node.type === "button" && typeof node.props.onClick === "function")[0].props.onClick();
    assert.equal(requests[1].caseId, "case-A", "same-case reload starts deferred A2");

    const bTree = runtime.render({
      caseId: "case-B",
      evidenceSummary: { available: true, document_name: "public-B.pdf", page: 9, section: "B", fragment: "Fragmento B" }
    });
    assert.equal(findNodes(bTree, (node) => node.type === "iframe").length, 0, "B never commits A's PDF under B metadata");
    assert.equal(findNodes(bTree, (node) => node.type === "a").length, 0, "B never exposes A's download link");
    runtime.flushEffects();
    assert.equal(controllers[1].abortCalls, 1, "case switch aborts A2 exactly once");
    assert.equal(requests[2].caseId, "case-B");
    requests[2].pending.resolve(new Blob(["B"]));
    await new Promise((resolve) => setImmediate(resolve));
    runtime.flush();
    assert.equal(findNodes(runtime.tree, (node) => node.type === "iframe")[0].props.src, "blob:workspace-2#page=9");
    requests[1].pending.resolve(new Blob(["late A2"]));
    await new Promise((resolve) => setImmediate(resolve));
    runtime.flush();
    assert.equal(findNodes(runtime.tree, (node) => node.type === "iframe")[0].props.src, "blob:workspace-2#page=9", "late A2 cannot replace B");
    assert.equal(findNodes(runtime.tree, (node) => node.type === "a")[0].props.href, "blob:workspace-2");
  } finally {
    runtime?.unmount();
    if (loaded && originalGetEvidence) loaded.api.getEvidence = originalGetEvidence;
    URL.createObjectURL = originalCreate;
    URL.revokeObjectURL = originalRevoke;
    globalThis.AbortController = OriginalAbortController;
  }
  assert.equal(controllers[0].abortCalls, 1, "same-case reload aborts settled A1 exactly once");
  assert.equal(controllers[2].abortCalls, 1, "unmount aborts B exactly once");
  assert.deepEqual(urlCalls, [
    ["create", "blob:workspace-1", 1],
    ["revoke", "blob:workspace-1"],
    ["create", "blob:workspace-2", 1],
    ["revoke", "blob:workspace-2"]
  ]);
});

test("evidence preview uses only the local blob URL and sanitizes supported evidence failures", async () => {
  const { React, renderToStaticMarkup, evidence, api, humanReviewApi } = await loadModules();
  const summary = { available: true, document_name: "informe-publico.pdf", page: 8, section: "Participantes", fragment: "Fragmento p\u00fablico" };
  const html = renderToStaticMarkup(React.createElement(evidence.EvidencePanel, {
    summary, blob: null, objectUrl: "blob:local-evidence", error: null, isLoading: false, onLoad: () => undefined
  }));
  assert.match(html, /<iframe[^>]+title="Vista previa de informe-publico\.pdf"[^>]+src="blob:local-evidence#page=8"/);
  assert.match(html, /href="blob:local-evidence"/);
  assert.match(html, /Abrir documento original en una pestaña nueva/);
  assert.match(html, /target="_blank"/);
  assert.doesNotMatch(html, /download=/);
  assert.doesNotMatch(html, /<(?:a|iframe)[^>]+(?:href|src)="https?:\/\//i);
  for (const status of [403, 404, 503]) {
    const rendered = renderToStaticMarkup(React.createElement(evidence.EvidencePanel, {
      summary, blob: null, objectUrl: null,
      error: new humanReviewApi.HumanReviewApiError(new api.ApiFetchError("internal detail", { status, correlation_id: `support-${status}`, details: { source_path: "internal detail" } })),
      isLoading: false, onLoad: () => undefined
    }));
    assert.match(rendered, new RegExp(`support-${status}`));
    assert.doesNotMatch(rendered, /internal detail|source_path/i);
  }
});

test("stable detail and audit keys deduplicate requests and remain distinct", async () => {
  const { cache, humanReviewApi } = await loadModules();
  const store = cache.createDataCacheStore();
  const caseKeyA = humanReviewApi.humanReviewCaseKey("case-11");
  const caseKeyB = humanReviewApi.humanReviewCaseKey("case-11");
  const auditKey = humanReviewApi.humanReviewAuditKey("case-11", 1, 25);
  let requests = 0;
  const request = Promise.resolve().then(() => { requests += 1; return caseDetail(); });
  store.setInflight(caseKeyA, request);
  const duplicate = store.getInflight(caseKeyB) ?? Promise.resolve().then(() => { requests += 1; });
  await Promise.all([request, duplicate]);
  assert.strictEqual(duplicate, request);
  assert.equal(requests, 1);
  assert.notEqual(auditKey, caseKeyA);
});
