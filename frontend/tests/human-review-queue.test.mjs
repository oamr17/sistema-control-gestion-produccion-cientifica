import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import test, { after, afterEach } from "node:test";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const tsc = path.join(frontendRoot, "node_modules", "typescript", "bin", "tsc");
const originalFetch = globalThis.fetch;
let compiledRoot;
let modulesPromise;

async function compileTask10Modules() {
  compiledRoot = await mkdtemp(path.join(tmpdir(), "b2b2-task10-runtime-"));
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
    "components/human-review/ReviewQueueFilters.tsx",
    "components/human-review/ReviewQueueTable.tsx",
    "components/human-review/ReviewPagination.tsx",
    "app/human-review/page.tsx"
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
  const result = spawnSync(
    process.execPath,
    [tsc, "--project", configPath],
    { cwd: frontendRoot, encoding: "utf8" }
  );
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);

  process.env.NODE_PATH = path.join(frontendRoot, "node_modules");
  require("node:module")._initPaths();
  const Module = require("node:module");
  const originalResolve = Module._resolveFilename;
  Module._resolveFilename = function resolveTask10Alias(request, parent, isMain, options) {
    if (request.startsWith("@/")) {
      request = path.join(compiledRoot, request.slice(2));
    }
    return originalResolve.call(this, request, parent, isMain, options);
  };
  try {
    return {
      React: require("react"),
      renderToStaticMarkup: require("react-dom/server").renderToStaticMarkup,
      api: require(path.join(compiledRoot, "lib", "api.js")),
      humanReviewApi: require(path.join(compiledRoot, "lib", "human-review-api.js")),
      cache: require(path.join(compiledRoot, "lib", "data-cache.js")),
      filters: require(path.join(compiledRoot, "components", "human-review", "ReviewQueueFilters.js")),
      table: require(path.join(compiledRoot, "components", "human-review", "ReviewQueueTable.js")),
      pagination: require(path.join(compiledRoot, "components", "human-review", "ReviewPagination.js")),
      shell: require(path.join(compiledRoot, "components", "AppShell.js"))
    };
  } finally {
    Module._resolveFilename = originalResolve;
  }
}

function loadModules() {
  modulesPromise ??= compileTask10Modules();
  return modulesPromise;
}

function queueItem(overrides = {}) {
  return {
    id: "00000000-0000-0000-0000-000000000101",
    case_type: "product",
    case_status: "pending",
    scientific_status: "pending",
    document_id: 41,
    source_revision: "rev-2",
    source_page: 7,
    source_section: "Producci\u00f3n cient\u00edfica",
    document_name: "reporte-publico.pdf",
    detected_value: "Título detectado para revisar",
    normalized_value: "titulo detectado para revisar",
    canonical_value: null,
    automatic_priority: 8,
    manual_priority: null,
    possible_kpi_impact: true,
    version: 1,
    created_at: "2026-08-01T10:00:00Z",
    ...overrides
  };
}

function queueQuery(overrides = {}) {
  return {
    page: 2,
    page_size: 25,
    statuses: [],
    case_types: [],
    sort: "priority_oldest",
    ...overrides
  };
}

afterEach(() => {
  globalThis.fetch = originalFetch;
});

after(async () => {
  if (compiledRoot) await rm(compiledRoot, { recursive: true, force: true });
});

test("queue renders initial loading, empty, safe error and API results", async () => {
  const { React, renderToStaticMarkup, api, humanReviewApi, table } = await loadModules();
  const render = (props) => renderToStaticMarkup(React.createElement(table.ReviewQueueTable, props));

  assert.match(render({ isInitialLoading: true, isUpdating: false }), /Cargando casos/i);
  assert.match(render({ data: { items: [], total: 0 }, isInitialLoading: false, isUpdating: false }), /No hay casos/i);
  assert.match(render({ data: { items: [], total: 0 }, isInitialLoading: false, isUpdating: false, hasActiveFilters: true }), /No se encontraron/i);

  const denied = new humanReviewApi.HumanReviewApiError(
    new api.ApiFetchError("dropbox_path:/private/report.pdf", {
      status: 403,
      code: "B2B_CAPABILITY_REQUIRED",
      correlation_id: "support-403",
      details: { source_path: "C:/private/report.pdf" }
    })
  );
  const deniedHtml = render({ error: denied, isInitialLoading: false, isUpdating: false });
  assert.match(deniedHtml, /No tienes acceso/i);
  assert.match(deniedHtml, /support-403/);
  assert.doesNotMatch(deniedHtml, /dropbox_path|source_path|private\/report/i);

  const unavailable = new humanReviewApi.HumanReviewApiError(
    new api.ApiFetchError("internal", { status: 503, correlation_id: "support-503" })
  );
  assert.match(render({ error: unavailable, isInitialLoading: false, isUpdating: false }), /no est\u00e1 disponible/i);
  const general = new humanReviewApi.HumanReviewApiError(
    new api.ApiFetchError("internal", { status: 500, correlation_id: "support-500" })
  );
  assert.match(render({ error: general, isInitialLoading: false, isUpdating: false }), /No pudimos cargar/i);

  const resultHtml = render({
    data: { items: [queueItem()], total: 1 },
    isInitialLoading: false,
    isUpdating: true
  });
  assert.match(resultHtml, /Actualizando casos/i);
  assert.match(resultHtml, /Producci\u00f3n cient\u00edfica/);
  assert.match(resultHtml, /reporte-publico\.pdf/);
  assert.match(resultHtml, /p\u00e1g\. 7/);
  assert.match(resultHtml, /T\u00edtulo detectado para revisar/);
  assert.match(resultHtml, /Normalizado/);
  assert.match(resultHtml, /Validar los datos de la producci\u00f3n cient\u00edfica/);
  assert.match(resultHtml, /Impacto potencial/);
  assert.match(resultHtml, /<details[^>]*>[\s\S]*Ver referencia p\u00fablica del caso[\s\S]*>00000000-0000-0000-0000-000000000101<\/span>[\s\S]*<\/details>/);
  assert.doesNotMatch(resultHtml, />ID 00000000/);
  assert.match(resultHtml, /Acciones/);
  assert.match(resultHtml, /href="\/human-review\/cases\/00000000-0000-0000-0000-000000000101"/);
  assert.match(resultHtml, /aria-label="Revisar producci\u00f3n cient\u00edfica: T\u00edtulo detectado para revisar"/);
  assert.doesNotMatch(resultHtml, /aria-label="[^"]*00000000/);
  assert.match(resultHtml, /Revisar caso/);
  assert.doesNotMatch(resultHtml, /Documento 41|rev-2|target="_blank"|dropbox_path|document_key/);
});

test("filter submit is disabled only while the queue request is active", async () => {
  const { React, renderToStaticMarkup, filters } = await loadModules();
  const props = { query: queueQuery({ page: 1 }), onApply: () => undefined };
  const idle = renderToStaticMarkup(React.createElement(filters.ReviewQueueFilters, props));
  const loading = renderToStaticMarkup(React.createElement(filters.ReviewQueueFilters, { ...props, isApplying: true }));

  assert.match(idle, /<button type="submit"[^>]*>Aplicar filtros<\/button>/);
  assert.doesNotMatch(idle, /<button type="submit" disabled=""/);
  assert.match(loading, /<button type="submit" disabled=""[^>]*>Aplicar filtros<\/button>/);
  assert.doesNotMatch(loading, /<button type="button" disabled=""[^>]*>Limpiar filtros<\/button>/);
});

test("actionable defaults keep compatibility-only case types queryable without putting them in the work queue", async () => {
  const { filters } = await loadModules();

  assert.deepEqual(filters.actionableQueueCaseTypes, [
    "person_identity",
    "author_identity",
    "product",
    "project_director_relation",
    "external_identity",
    "possible_duplicate"
  ]);
  assert.equal(filters.actionableQueueCaseTypes.includes("invalid_text"), false);
  assert.equal(filters.actionableQueueCaseTypes.includes("new_evidence_conflict"), false);
});

test("filter changes and page-size changes reset page and retain only the public contract", async () => {
  const { React, renderToStaticMarkup, filters } = await loadModules();
  const current = queueQuery({ page: 4, q: " identidad ", document_id: 41 });
  const changed = filters.withQueueFilters(current, {
    statuses: ["pending"],
    case_types: ["product"],
    period_id: 7,
    document_id: 42,
    source_revision: " rev-3 ",
    created_from: "2026-07-01T00:00:00Z",
    created_to: "2026-07-31T23:59:59Z",
    q: " documento p\u00fablico ",
    career_id: 9,
    confidence: 0.9,
    has_evidence: true,
    document_key: "dropbox_path:/private.pdf"
  });
  assert.equal(changed.page, 1);
  assert.equal(changed.document_id, 42);
  assert.equal(changed.source_revision, "rev-3");
  assert.equal(changed.q, "documento p\u00fablico");
  assert.deepEqual(Object.keys(changed).sort(), [
    "case_types", "created_from", "created_to", "document_id", "page", "page_size",
    "period_id", "q", "sort", "source_revision", "statuses"
  ]);
  assert.equal(filters.withQueuePageSize(current, 50).page, 1);
  assert.equal(filters.withQueuePageSize(current, 50).page_size, 50);
  assert.equal(filters.normalizeQueueQuery(queueQuery({ q: "ab" })).q, undefined);
  assert.equal(filters.normalizeQueueQuery(queueQuery({ q: "x".repeat(129) })).q, undefined);
  assert.equal(filters.normalizeQueueQuery(queueQuery({ source_revision: "x".repeat(121) })).source_revision, undefined);

  const html = renderToStaticMarkup(React.createElement(filters.ReviewQueueFilters, {
    query: current,
    onApply: () => undefined
  }));
  assert.match(html, /Documento/);
  assert.match(html, /maxLength="120"/);
  assert.doesNotMatch(html, /Carrera|career_id|confidence|has_evidence|document_key/i);
  assert.doesNotMatch(html, />Periodo</i);
  assert.doesNotMatch(html, /\u00c3|\u00c2/);

  assert.equal(filters.periodIdForSelection([
    { id: 7, year_label: "2025-2026", cycle: 1 },
    { id: 8, year_label: "2025-2026", cycle: 2 }
  ], "2025-2026", 2), 8);
  assert.equal(filters.periodIdForSelection([], "2025-2026", 1), undefined);
  const reset = filters.resetQueueFilters(queueQuery({ period_id: 8, q: "documento", statuses: ["resolved", "superseded"] }));
  assert.equal(reset.period_id, 8);
  assert.deepEqual(reset.statuses, []);
  assert.equal(Object.hasOwn(reset, "q"), false);
  assert.equal(Object.hasOwn(reset, "case_types"), true);
});

test("pagination is derived from API total/page/page_size and reconciles stale pages", async () => {
  const { React, renderToStaticMarkup, pagination } = await loadModules();
  assert.deepEqual(pagination.getPaginationModel(51, 2, 25), {
    page: 2,
    pageSize: 25,
    total: 51,
    totalPages: 3,
    canPrevious: true,
    canNext: true
  });
  assert.equal(pagination.getPaginationModel(51, -2, 25).page, 1);
  assert.equal(pagination.getPaginationModel(51, 99, 25).page, 3);
  const staleQuery = queueQuery({ page: 3 });
  const staleResponse = { total: 8, page: 3, page_size: 25 };
  assert.equal(pagination.isQueuePageReconciling(staleQuery, staleResponse), true);
  const reconciled = pagination.reconcileQueuePage(staleQuery, { total: 8, page_size: 25 });
  assert.equal(reconciled.page, 1);
  assert.equal(pagination.isQueuePageReconciling(reconciled, staleResponse), true);
  assert.equal(pagination.isQueuePageReconciling(reconciled, { ...staleResponse, page: 1 }), false);
  assert.strictEqual(pagination.reconcileQueuePage(reconciled, { total: 8, page_size: 25 }), reconciled);

  const first = renderToStaticMarkup(React.createElement(pagination.ReviewPagination, {
    total: 8,
    page: 1,
    pageSize: 25,
    onPageChange: () => undefined,
    onPageSizeChange: () => undefined
  }));
  assert.match(first, /P\u00e1gina 1 de 1/);
  assert.match(first, /Anterior[^>]*|disabled/i);
  assert.match(first, /Siguiente/);
});

test("GET /human-review/me gates navigation deny-by-default without a forbidden flash", async () => {
  const { React, renderToStaticMarkup, humanReviewApi, shell } = await loadModules();
  let requestedUrl;
  globalThis.fetch = async (url) => {
    requestedUrl = String(url);
    return new Response(JSON.stringify({ capability: "RESEARCH_MANAGER", actions: ["view_foundations"] }), {
      status: 200,
      headers: { "Content-Type": "application/json" }
    });
  };
  const capability = await humanReviewApi.humanReviewApi.getCapabilities();
  assert.match(requestedUrl, /\/api\/v1\/human-review\/me$/);
  assert.equal(capability.capability, "RESEARCH_MANAGER");

  const renderNav = (actions) => renderToStaticMarkup(React.createElement(shell.AppNavigation, {
    pathname: "/dashboard",
    actions
  }));
  assert.doesNotMatch(renderNav(undefined), /Validaci\u00f3n de Registros/);
  assert.match(renderNav(["view_foundations"]), /Validaci\u00f3n de Registros/);
  assert.match(renderNav(["view_foundations", "view_audit"]), /Validaci\u00f3n de Registros/);
  assert.doesNotMatch(renderNav(["view_foundations"]), /Revisi\u00f3n humana/);
  assert.doesNotMatch(renderNav(null), /Validaci\u00f3n de Registros/);
  assert.doesNotMatch(renderNav(["view_audit"]), /Validaci\u00f3n de Registros/);
});

test("career filter is hidden only when requested and stable queue state deduplicates requests", async () => {
  const { cache, humanReviewApi, shell } = await loadModules();
  assert.equal(shell.shouldShowCareerFilter("FACULTY_ADMIN"), true);
  assert.equal(shell.shouldShowCareerFilter("FACULTY_ADMIN", false), true);
  assert.equal(shell.shouldShowCareerFilter("FACULTY_ADMIN", true), false);
  assert.equal(shell.shouldShowCareerFilter("CAREER_MANAGER", false), false);

  const store = cache.createDataCacheStore();
  const stableQuery = queueQuery({ page: 1, statuses: ["pending"] });
  const keyA = humanReviewApi.humanReviewQueueKey(stableQuery);
  const keyB = humanReviewApi.humanReviewQueueKey({ ...stableQuery });
  let requests = 0;
  const request = Promise.resolve().then(() => { requests += 1; return { items: [] }; });
  store.setInflight(keyA, request);
  const secondRenderRequest = store.getInflight(keyB) ?? Promise.resolve().then(() => { requests += 1; });
  await Promise.all([request, secondRenderRequest]);
  assert.equal(keyA, keyB);
  assert.strictEqual(secondRenderRequest, request);
  assert.equal(requests, 1);
});
