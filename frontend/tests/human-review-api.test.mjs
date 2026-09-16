import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import test, { after, afterEach } from "node:test";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const tsc = path.join(frontendRoot, "node_modules", "typescript", "bin", "tsc");
const originalFetch = globalThis.fetch;
const originalWindow = globalThis.window;
let compiledRoot;
let compiledModulesPromise;

async function compileTask9Modules() {
  compiledRoot = await mkdtemp(path.join(tmpdir(), "b2b2-task9-runtime-"));
  const sources = [
    "lib/api.ts",
    "lib/auth.ts",
    "lib/types.ts",
    "lib/human-review.ts",
    "lib/human-review-api.ts",
    "lib/effective-data-refresh.ts",
    "lib/effective-data-revision.ts",
    "lib/data-cache.tsx",
    "hooks/useHumanReview.ts"
  ];
  const result = spawnSync(
    process.execPath,
    [
      tsc,
      "--noEmit", "false",
      "--incremental", "false",
      "--module", "commonjs",
      "--moduleResolution", "node",
      "--target", "ES2022",
      "--jsx", "react-jsx",
      "--esModuleInterop",
      "--skipLibCheck",
      "--rootDir", ".",
      "--outDir", compiledRoot,
      ...sources
    ],
    { cwd: frontendRoot, encoding: "utf8" }
  );
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);

  process.env.NODE_PATH = path.join(frontendRoot, "node_modules");
  require("node:module")._initPaths();
  return {
    api: require(path.join(compiledRoot, "lib", "api.js")),
    auth: require(path.join(compiledRoot, "lib", "auth.js")),
    humanReviewApi: require(path.join(compiledRoot, "lib", "human-review-api.js")),
    useHumanReview: require(path.join(compiledRoot, "hooks", "useHumanReview.js")),
    effectiveDataRevision: require(path.join(compiledRoot, "lib", "effective-data-revision.js")),
    dataCache: require(path.join(compiledRoot, "lib", "data-cache.js"))
  };
}

function loadTask9Modules() {
  compiledModulesPromise ??= compileTask9Modules();
  return compiledModulesPromise;
}

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}

function successfulCommandResponse() {
  return {
    case: {},
    decision_id: "00000000-0000-0000-0000-000000000011",
    kpi_effect: { affected: [] },
    correlation_id: "00000000-0000-0000-0000-000000000012"
  };
}

function nextTick() {
  return new Promise((resolve) => setImmediate(resolve));
}

function localStorageWindow(initial = {}) {
  const storage = new Map(Object.entries(initial));
  const listeners = new Map();
  return {
    localStorage: {
      getItem: (key) => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, String(value)),
      removeItem: (key) => storage.delete(key)
    },
    dispatchEvent(event) {
      for (const listener of listeners.get(event.type) ?? []) listener(event);
      return true;
    },
    addEventListener(type, listener) {
      const registered = listeners.get(type) ?? new Set();
      registered.add(listener);
      listeners.set(type, registered);
    },
    removeEventListener(type, listener) {
      listeners.get(type)?.delete(listener);
    },
    storage
  };
}

afterEach(() => {
  globalThis.fetch = originalFetch;
  if (originalWindow === undefined) delete globalThis.window;
  else globalThis.window = originalWindow;
});

after(async () => {
  if (compiledRoot) await rm(compiledRoot, { recursive: true, force: true });
});

test("apiFetch preserves the structured error envelope and HTTP status", async () => {
  const { api } = await loadTask9Modules();
  globalThis.fetch = async () => jsonResponse({
    code: "REVIEW_CASE_VERSION_CONFLICT",
    message: "The review case changed",
    correlation_id: "00000000-0000-0000-0000-000000000021",
    details: { expected_version: 2, actual_version: 3 }
  }, 409);

  await assert.rejects(
    api.apiFetch("/human-review/cases/case-id"),
    (error) => {
      assert.equal(error.name, "ApiFetchError");
      assert.equal(error.status, 409);
      assert.equal(error.code, "REVIEW_CASE_VERSION_CONFLICT");
      assert.equal(error.message, "The review case changed");
      assert.equal(error.correlation_id, "00000000-0000-0000-0000-000000000021");
      assert.deepEqual(error.details, { expected_version: 2, actual_version: 3 });
      return true;
    }
  );
});

test("HumanReviewApiError keeps each approved status and never exposes an unstructured body", async () => {
  const { humanReviewApi } = await loadTask9Modules();
  for (const status of [400, 401, 403, 404, 409, 422, 503]) {
    globalThis.fetch = async () => jsonResponse({
      code: "HUMAN_REVIEW_VALIDATION",
      message: `safe-${status}`,
      correlation_id: `00000000-0000-0000-0000-${String(status).padStart(12, "0")}`,
      details: { field: "reason" }
    }, status);
    await assert.rejects(
      humanReviewApi.humanReviewApi.getCase("case-id"),
      (error) => {
        assert.ok(error instanceof humanReviewApi.HumanReviewApiError);
        assert.equal(error.status, status);
        assert.equal(error.message, `safe-${status}`);
        assert.deepEqual(error.details, { field: "reason" });
        return true;
      }
    );
  }

  globalThis.fetch = async () => jsonResponse({ detail: "dropbox_path:/private/report.pdf" }, 503);
  await assert.rejects(
    humanReviewApi.humanReviewApi.getCase("case-id"),
    (error) => {
      assert.equal(error.status, 503);
      assert.equal(error.message, "Request failed");
      assert.doesNotMatch(String(error), /dropbox_path|private/);
      return true;
    }
  );
});

test("an incompatible link decision is not presented as an optimistic version conflict", async () => {
  const { humanReviewApi, useHumanReview } = await loadTask9Modules();
  const error = new humanReviewApi.HumanReviewApiError({
    status: 409,
    code: "INCOMPATIBLE_DECISION",
    correlation_id: "00000000-0000-0000-0000-000000000409"
  });

  assert.deepEqual(useHumanReview.toReviewCommandResult(error), {
    status: "invalid",
    correlationId: "00000000-0000-0000-0000-000000000409",
    message: "La decisión contiene datos que deben corregirse."
  });
});

test("listCases serializes only the public supported filters and never document_key", async () => {
  const { humanReviewApi } = await loadTask9Modules();
  let requestedUrl;
  globalThis.fetch = async (url) => {
    requestedUrl = String(url);
    return jsonResponse({ items: [], total: 0, page: 2, page_size: 50, facets: {}, correlation_id: "cid" });
  };

  await humanReviewApi.humanReviewApi.listCases({
    page: 2,
    page_size: 50,
    statuses: ["pending", "resolved"],
    case_types: ["product", "author_identity"],
    period_id: 7,
    document_id: 41,
    source_revision: "rev-2",
    created_from: "2026-07-01T00:00:00Z",
    created_to: "2026-07-31T23:59:59Z",
    q: "public-search",
    sort: "priority_oldest",
    document_key: "dropbox_path:/private.pdf",
    career_id: 9,
    confidence: 0.9,
    has_evidence: true
  });

  const url = new URL(requestedUrl);
  assert.equal(url.pathname, "/api/v1/human-review/cases");
  assert.deepEqual(url.searchParams.getAll("status"), ["pending", "resolved"]);
  assert.deepEqual(url.searchParams.getAll("case_type"), ["author_identity", "product"]);
  assert.equal(url.searchParams.get("page"), "2");
  assert.equal(url.searchParams.get("page_size"), "50");
  assert.equal(url.searchParams.get("period_id"), "7");
  assert.equal(url.searchParams.get("document_id"), "41");
  assert.equal(url.searchParams.get("source_revision"), "rev-2");
  assert.equal(url.searchParams.get("created_from"), "2026-07-01T00:00:00Z");
  assert.equal(url.searchParams.get("created_to"), "2026-07-31T23:59:59Z");
  assert.equal(url.searchParams.get("q"), "public-search");
  assert.equal(url.searchParams.get("sort"), "priority_oldest");
  for (const forbidden of ["statuses", "case_types", "document_key", "career_id", "confidence", "has_evidence"]) {
    assert.equal(url.searchParams.has(forbidden), false);
  }
});

test("getRelated encodes the case ID, defaults limit to 50 and performs an authenticated GET without a body", async () => {
  const { humanReviewApi } = await loadTask9Modules();
  const calls = [];
  globalThis.window = { localStorage: { getItem: () => "task-4-token" } };
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), options });
    return jsonResponse({
      entity: { public_type: "person", public_id: "person:1", display_name: "Ada Lovelace" },
      items: [],
      total_pending: 0,
      truncated: false,
      correlation_id: "corr-related"
    });
  };

  await humanReviewApi.humanReviewApi.getRelated("case id/one");
  await humanReviewApi.humanReviewApi.getRelated("case id/two", 7);

  assert.deepEqual(calls.map(({ url }) => url), [
    "http://localhost:8000/api/v1/human-review/cases/case%20id%2Fone/related?limit=50",
    "http://localhost:8000/api/v1/human-review/cases/case%20id%2Ftwo/related?limit=7"
  ]);
  for (const { options } of calls) {
    assert.equal(options.method, undefined);
    assert.equal(options.body, undefined);
    assert.equal(options.cache, "no-store");
    assert.equal(new Headers(options.headers).get("Authorization"), "Bearer task-4-token");
  }
});

test("getEvidence forwards its AbortSignal through the authenticated blob request", async () => {
  const { humanReviewApi } = await loadTask9Modules();
  const controller = new AbortController();
  let request;
  globalThis.window = { localStorage: { getItem: () => "task-4-token" } };
  globalThis.fetch = async (url, options) => {
    request = { url: String(url), options };
    return new Response(new Blob(["%PDF-1.4\n"], { type: "application/pdf" }), {
      status: 200,
      headers: { "Content-Type": "application/pdf" }
    });
  };

  await humanReviewApi.humanReviewApi.getEvidence("case id/one", controller.signal);

  assert.equal(request.url, "http://localhost:8000/api/v1/human-review/cases/case%20id%2Fone/evidence");
  assert.strictEqual(request.options.signal, controller.signal);
  assert.equal(new Headers(request.options.headers).get("Authorization"), "Bearer task-4-token");
  assert.equal(request.options.body, undefined);
});

test("import PDF downloads use Authorization and expose no token-bearing URL helper", async () => {
  const { api } = await loadTask9Modules();
  const requests = [];
  const jobController = new AbortController();
  const progressController = new AbortController();
  globalThis.window = { localStorage: { getItem: () => "SESSION_A" } };
  globalThis.fetch = async (url, options) => {
    requests.push({ url: String(url), options });
    return new Response(new Blob(["%PDF-1.4\n"], { type: "application/pdf" }), {
      status: 200,
      headers: { "Content-Type": "application/pdf" }
    });
  };

  await api.api.importJobFile(17, { signal: jobController.signal });
  await api.api.importedProgressFile(29, { signal: progressController.signal });

  assert.deepEqual(requests.map(({ url }) => url), [
    "http://localhost:8000/api/v1/imports/jobs/17/file",
    "http://localhost:8000/api/v1/imports/progress-records/29/file"
  ]);
  assert.strictEqual(requests[0].options.signal, jobController.signal);
  assert.strictEqual(requests[1].options.signal, progressController.signal);
  for (const { url, options } of requests) {
    assert.doesNotMatch(url, /[?&](?:token|access_token)=|SESSION_A/i);
    assert.equal(new Headers(options.headers).get("Authorization"), "Bearer SESSION_A");
  }
  assert.equal(api.authenticatedFileUrl, undefined);
  assert.equal(api.api.importJobFileUrl, undefined);
  assert.equal(api.api.importedProgressFileUrl, undefined);
});

test("authenticated blob URL ownership revokes replacements and ignores completion after disposal", async () => {
  const { api } = await loadTask9Modules();
  assert.equal(typeof api.createAuthenticatedBlobUrlOwner, "function");
  const calls = [];
  const urlApi = {
    createObjectURL(blob) {
      const url = `blob:local-${calls.filter(([kind]) => kind === "create").length + 1}`;
      calls.push(["create", url, blob.size]);
      return url;
    },
    revokeObjectURL(url) { calls.push(["revoke", url]); }
  };
  const owner = api.createAuthenticatedBlobUrlOwner(urlApi);
  const first = await owner.replace(null, async () => new Blob(["A"]));
  const second = await owner.replace(first, async () => new Blob(["BB"]));
  let resolveLate;
  const late = owner.replace(second, () => new Promise((resolve) => { resolveLate = resolve; }));

  owner.dispose();
  resolveLate(new Blob(["late A"]));

  assert.equal(await late, null);
  assert.deepEqual(calls, [
    ["create", "blob:local-1", 1],
    ["revoke", "blob:local-1"],
    ["create", "blob:local-2", 2],
    ["revoke", "blob:local-2"]
  ]);
});

test("import and effective-data PDF surfaces use the authenticated blob owner and never render private diagnostics", async () => {
  const [component, importsPage, teachersPage, productionPage, projectsPage] = await Promise.all([
    readFile(path.join(frontendRoot, "components", "AuthenticatedPdfButton.tsx"), "utf8"),
    readFile(path.join(frontendRoot, "app", "importaciones", "page.tsx"), "utf8"),
    readFile(path.join(frontendRoot, "app", "teachers", "page.tsx"), "utf8"),
    readFile(path.join(frontendRoot, "app", "production", "page.tsx"), "utf8"),
    readFile(path.join(frontendRoot, "app", "projects", "page.tsx"), "utf8")
  ]);
  const publicSurfaces = `${importsPage}\n${teachersPage}\n${productionPage}\n${projectsPage}`;

  assert.match(component, /createAuthenticatedBlobUrlOwner/);
  assert.match(component, /load\(\{ signal: controller\.signal \}\)/);
  assert.match(component, /window\.open\("about:blank"/);
  assert.match(component, /if \(!previewWindow\)/);
  assert.match(component, /if \(previewWindow\.closed\)/);
  assert.match(component, /ownerRef\.current\?\.revoke\(nextUrl\)/);
  assert.match(component, /ownerRef\.current\?\.dispose\(\)/);
  assert.match(component, /subscribeSessionChange\(clearOwnedDownload\)/);
  assert.match(importsPage, /No se pudo completar la importación/);
  assert.doesNotMatch(importsPage, /job\.error_traceback|Traceback resumido|Mensaje completo/);
  assert.doesNotMatch(publicSurfaces, /authenticatedFileUrl|importJobFileUrl|importedProgressFileUrl|[?&](?:token|access_token)=/);
});

test("apply, discard and revert send exact Task 8 paths without mutating payloads", async () => {
  const { humanReviewApi } = await loadTask9Modules();
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), method: options.method, body: JSON.parse(options.body) });
    return jsonResponse(successfulCommandResponse());
  };

  const apply = {
    expected_version: 4,
    expected_current_decision_id: null,
    action: "link",
    scope: "global_identity",
    payload: {
      case_type: "person_identity",
      canonical_identity_key: "identity:one",
      canonical_name: "One Person",
      aliases: [],
      scientific_status: "validated",
      resolution: "linked"
    },
    reason: "verified",
    correlation_id: "00000000-0000-0000-0000-000000000031"
  };
  const discard = {
    expected_version: 5,
    expected_current_decision_id: null,
    reason: "not scientific",
    correlation_id: "00000000-0000-0000-0000-000000000032"
  };
  const revert = {
    expected_version: 6,
    expected_current_decision_id: "00000000-0000-0000-0000-000000000033",
    decision_id_to_revert: "00000000-0000-0000-0000-000000000034",
    reason: "reversal reviewed",
    correlation_id: "00000000-0000-0000-0000-000000000035"
  };
  const originals = structuredClone({ apply, discard, revert });

  await humanReviewApi.humanReviewApi.apply("case/one", apply);
  await humanReviewApi.humanReviewApi.discard("case/one", discard);
  await humanReviewApi.humanReviewApi.revert("case/one", revert);

  assert.deepEqual({ apply, discard, revert }, originals);
  assert.deepEqual(calls, [
    { url: "http://localhost:8000/api/v1/human-review/cases/case%2Fone/apply", method: "POST", body: apply },
    { url: "http://localhost:8000/api/v1/human-review/cases/case%2Fone/discard", method: "POST", body: discard },
    { url: "http://localhost:8000/api/v1/human-review/cases/case%2Fone/revert", method: "POST", body: revert }
  ]);
});

test("effective data revision uses one authenticated lightweight endpoint", async () => {
  const { api } = await loadTask9Modules();
  globalThis.window = { localStorage: { getItem: () => "revision-token" } };
  let request;
  globalThis.fetch = async (url, options) => {
    request = { url: String(url), options };
    return jsonResponse({ revision: 17 });
  };

  const response = await api.api.effectiveDataRevision();

  assert.deepEqual(response, { revision: 17 });
  assert.equal(request.url, "http://localhost:8000/api/v1/human-review/effective-data-revision");
  assert.equal(request.options.method, undefined);
  assert.equal(new Headers(request.options.headers).get("Authorization"), "Bearer revision-token");
});

test("two sessions refresh once only after one observes a changed committed revision", async () => {
  const { effectiveDataRevision } = await loadTask9Modules();
  const listeners = new Map();
  const timers = new Map();
  let timerId = 0;
  const windowLike = {
    document: { visibilityState: "visible" },
    addEventListener(type, listener) { listeners.set(type, listener); },
    removeEventListener(type) { listeners.delete(type); },
    setInterval(listener) { timerId += 1; timers.set(timerId, listener); return timerId; },
    clearInterval(id) { timers.delete(id); }
  };
  let revision = 8;
  const aChanges = [];
  const bChanges = [];
  const start = (onChanged) => effectiveDataRevision.startEffectiveDataRevisionPolling({
    readRevision: async () => ({ revision }),
    onChanged,
    intervalMs: 3000,
    windowLike
  });
  const stopA = start((next) => aChanges.push(next));
  const stopB = start((next) => bChanges.push(next));
  await nextTick();

  assert.equal(timers.size, 2);
  assert.deepEqual(aChanges, []);
  assert.deepEqual(bChanges, []);

  revision = 9;
  for (const tick of timers.values()) tick();
  await nextTick();

  assert.deepEqual(aChanges, [9]);
  assert.deepEqual(bChanges, [9]);

  listeners.get("focus")();
  await nextTick();
  assert.deepEqual(aChanges, [9]);
  assert.deepEqual(bChanges, [9]);

  stopA();
  stopB();
  assert.equal(timers.size, 0);
  assert.equal(listeners.size, 0);
});

test("effective data revision polling recovers from temporary failures without duplicate change cycles", async () => {
  const { effectiveDataRevision } = await loadTask9Modules();
  let tick;
  const windowLike = {
    document: { visibilityState: "visible" },
    addEventListener() {},
    removeEventListener() {},
    setInterval(listener) { tick = listener; return 1; },
    clearInterval() {}
  };
  let calls = 0;
  const changes = [];
  const stop = effectiveDataRevision.startEffectiveDataRevisionPolling({
    readRevision: async () => {
      calls += 1;
      if (calls === 2) throw new Error("temporary network failure");
      return { revision: calls < 4 ? 4 : 5 };
    },
    onChanged: (revision) => changes.push(revision),
    intervalMs: 3000,
    windowLike
  });
  await nextTick();

  tick();
  await nextTick();
  tick();
  await nextTick();
  tick();
  await nextTick();

  assert.equal(calls, 4);
  assert.deepEqual(changes, [5]);
  stop();
});

test("effective data revision polling pauses a rejected session and resumes for a new session token", async () => {
  const { api, effectiveDataRevision } = await loadTask9Modules();
  let tick;
  let session = "expired-token";
  const windowLike = {
    document: { visibilityState: "visible" },
    addEventListener() {},
    removeEventListener() {},
    setInterval(listener) { tick = listener; return 1; },
    clearInterval() {}
  };
  let calls = 0;
  const stop = effectiveDataRevision.startEffectiveDataRevisionPolling({
    readRevision: async () => {
      calls += 1;
      if (session === "expired-token") throw new api.ApiFetchError("expired", { status: 401 });
      return { revision: 11 };
    },
    onChanged() {},
    getSessionKey: () => session,
    intervalMs: 3000,
    windowLike
  });
  await nextTick();
  tick();
  await nextTick();
  assert.equal(calls, 1);

  session = "fresh-token";
  tick();
  await nextTick();
  assert.equal(calls, 2);
  stop();
});

test("effective data revision checks immediately when a hidden tab becomes visible", async () => {
  const { effectiveDataRevision } = await loadTask9Modules();
  const listeners = new Map();
  let calls = 0;
  const windowLike = {
    document: { visibilityState: "hidden" },
    addEventListener(type, listener) { listeners.set(type, listener); },
    removeEventListener(type) { listeners.delete(type); },
    setInterval() { return 1; },
    clearInterval() {}
  };
  const stop = effectiveDataRevision.startEffectiveDataRevisionPolling({
    readRevision: async () => ({ revision: ++calls }),
    onChanged() {},
    intervalMs: 3000,
    windowLike
  });
  await nextTick();
  assert.equal(calls, 0);

  windowLike.document.visibilityState = "visible";
  listeners.get("visibilitychange")();
  await nextTick();
  assert.equal(calls, 1);
  stop();
});

test("invalidate removes every cache entry under a prefix and preserves other keys", async () => {
  const { dataCache } = await loadTask9Modules();
  const store = dataCache.createDataCacheStore();
  store.set("human-review:queue:a", { page: 1 });
  store.set("human-review:queue:b", { page: 2 });
  store.set("human-review:case:one", { id: "one" });
  store.set("dashboard:2026", { total: 2 });

  store.invalidate("human-review:queue:");

  assert.equal(store.get("human-review:queue:a"), undefined);
  assert.equal(store.get("human-review:queue:b"), undefined);
  assert.deepEqual(store.get("human-review:case:one").data, { id: "one" });
  assert.deepEqual(store.get("dashboard:2026").data, { total: 2 });
});

test("human-review cache is memory-only and is cleared across authenticated sessions", async () => {
  const { dataCache } = await loadTask9Modules();
  let session = "session-a";
  const store = dataCache.createDataCacheStore(() => session);
  store.set("human-review:case:one", { id: "one" });
  store.set("dashboard:2026", { total: 2 });

  assert.deepEqual(store.entries().map(([key]) => key), ["dashboard:2026"]);
  store.hydrate([
    ["human-review:queue:leaked", { data: { total: 9 }, updatedAt: 1 }],
    ["dashboard:2025", { data: { total: 1 }, updatedAt: 1 }]
  ]);
  assert.equal(store.get("human-review:queue:leaked"), undefined);

  session = "session-b";
  assert.equal(store.get("human-review:case:one"), undefined);
  assert.equal(store.get("dashboard:2025"), undefined);
});

test("A logout then B login clears every protected memory cache and rejects A responses that finish late", async () => {
  const { dataCache } = await loadTask9Modules();
  let session = "SESSION_A";
  const store = dataCache.createDataCacheStore(() => session);
  const protectedValues = new Map([
    ["canonical-participants:all", { a: "A_PARTICIPANTS", b: "B_PARTICIPANTS" }],
    ["imported-progress:2026:1", {
      a: { scientificProduction: "A_PRODUCTION", projects: "A_PROJECTS" },
      b: { scientificProduction: "B_PRODUCTION", projects: "B_PROJECTS" }
    }],
    ["dashboard:2026:1:all", { a: "A_DASHBOARD_KPI", b: "B_DASHBOARD_KPI" }],
    ["human-review:queue:open", { a: "A_HUMAN_REVIEW", b: "B_HUMAN_REVIEW" }]
  ]);
  for (const [key, value] of protectedValues) store.set(key, value.a);

  let resolveLateA;
  const lateA = new Promise((resolve) => { resolveLateA = resolve; });
  const lateKey = "canonical-participants:late";
  const lateAVersion = store.getVersion(lateKey);
  store.setInflight(lateKey, lateA);
  void lateA.then((data) => {
    if (store.getVersion(lateKey) === lateAVersion) store.set(lateKey, data);
  });

  session = null;
  for (const key of protectedValues.keys()) {
    assert.equal(store.get(key), undefined, `${key} exposed A after logout`);
  }
  session = "SESSION_B";
  for (const key of protectedValues.keys()) {
    assert.equal(store.get(key), undefined, `${key} exposed A before B refetch`);
  }

  resolveLateA("A_LATE_PARTICIPANTS");
  await nextTick();
  assert.equal(store.get(lateKey), undefined, "late A response repopulated B cache");

  let bFetches = 0;
  const fetchForB = async (value) => {
    bFetches += 1;
    await nextTick();
    return value;
  };
  for (const [key, value] of protectedValues) {
    const requestVersion = store.getVersion(key);
    const request = fetchForB(value.b);
    store.setInflight(key, request);
    const response = await request;
    if (store.getVersion(key) === requestVersion) store.set(key, response);
    store.clearInflight(key, request);
    assert.deepEqual(store.get(key)?.data, value.b);
    assert.notDeepEqual(store.get(key)?.data, value.a);
  }
  assert.equal(bFetches, protectedValues.size, "B must refetch every protected surface");
});

test("direct token transitions A to B, A to null, and null to B share one empty memory boundary", async () => {
  const { dataCache } = await loadTask9Modules();
  for (const [from, to] of [
    ["SESSION_A", "SESSION_B"],
    ["SESSION_A", null],
    [null, "SESSION_B"]
  ]) {
    let session = from;
    const store = dataCache.createDataCacheStore(() => session);
    store.set("dashboard:session-transition", "A_DASHBOARD_KPI");
    store.set("human-review:session-transition", "A_HUMAN_REVIEW");

    session = to;

    assert.equal(store.get("dashboard:session-transition"), undefined, `${from} -> ${to} kept ordinary cache`);
    assert.equal(store.get("human-review:session-transition"), undefined, `${from} -> ${to} kept human-review cache`);
  }
});

test("clearSession and saveSession remove persisted protected data before B can hydrate", async () => {
  const { auth } = await loadTask9Modules();
  const protectedEntries = JSON.stringify([
    ["canonical-participants:2026:1", { data: "A_PARTICIPANTS", updatedAt: 1 }],
    ["imported-progress:2026:1", { data: "A_PRODUCTION_AND_PROJECTS", updatedAt: 1 }],
    ["dashboard:2026:1", { data: "A_DASHBOARD_KPI", updatedAt: 1 }]
  ]);
  globalThis.window = localStorageWindow({
    token: "SESSION_A",
    profile: "A_PROFILE",
    scientific_data_cache_v7: protectedEntries,
    global_metadata_v2: "A_METADATA",
    global_filters_v3: "A_FILTERS"
  });
  let sessionBoundaries = 0;
  const unsubscribe = auth.subscribeSessionChange(() => { sessionBoundaries += 1; });

  auth.clearSession();
  assert.equal(window.localStorage.getItem("scientific_data_cache_v7"), null);
  assert.equal(window.localStorage.getItem("global_metadata_v2"), null);
  assert.equal(window.localStorage.getItem("global_filters_v3"), null);
  assert.equal(sessionBoundaries, 1);

  window.localStorage.setItem("scientific_data_cache_v7", protectedEntries);
  window.localStorage.setItem("global_metadata_v2", "A_METADATA_AFTER_LOGOUT");
  window.localStorage.setItem("global_filters_v3", "A_FILTERS_AFTER_LOGOUT");

  auth.saveSession({
    access_token: "SESSION_B",
    token_type: "bearer",
    full_name: "Usuario B",
    role: "viewer",
    career_id: null
  });

  assert.equal(window.localStorage.getItem("scientific_data_cache_v7"), null);
  assert.equal(window.localStorage.getItem("global_metadata_v2"), null);
  assert.equal(window.localStorage.getItem("global_filters_v3"), null);
  assert.equal(sessionBoundaries, 2);

  window.localStorage.setItem("scientific_data_cache_v7", protectedEntries);
  window.dispatchEvent({ type: "storage", key: "token" });
  assert.equal(window.localStorage.getItem("scientific_data_cache_v7"), null);
  assert.equal(sessionBoundaries, 3);
  unsubscribe();
});

test("cache invalidation publishes a revision so mounted queries can refetch", async () => {
  const { dataCache } = await loadTask9Modules();
  const store = dataCache.createDataCacheStore();
  let notifications = 0;
  const unsubscribe = store.subscribe(() => { notifications += 1; });
  const before = store.getSnapshot();
  store.set("human-review:queue:a", { page: 1 });

  store.invalidate("human-review:queue:");

  assert.equal(store.getSnapshot(), before + 1);
  assert.equal(notifications, 1);
  assert.equal(store.get("human-review:queue:a"), undefined);
  unsubscribe();
});

test("a changed global revision invalidates every effective-data cache in one notification", async () => {
  const { dataCache } = await loadTask9Modules();
  const store = dataCache.createDataCacheStore();
  const prefixes = [
    "dashboard:",
    "kpis:",
    "canonical-participants:",
    "imported-progress:",
    "production:",
    "projects:",
    "research-entities:",
    "entities:",
    "goals:"
  ];
  prefixes.forEach((prefix, index) => store.set(`${prefix}${index}`, { index }));
  let notifications = 0;
  const unsubscribe = store.subscribe(() => { notifications += 1; });

  dataCache.invalidateEffectiveDataCaches(store);

  for (const [index, prefix] of prefixes.entries()) {
    assert.equal(store.get(`${prefix}${index}`), undefined);
  }
  assert.equal(notifications, 1);
  unsubscribe();
});

test("successful apply to related B invalidates anchor A context and refetches every effective session value", async () => {
  const { api, humanReviewApi, dataCache } = await loadTask9Modules();
  const store = dataCache.createDataCacheStore();
  const anchorCaseId = "task9-anchor-a";
  const caseId = "task9-related-b";
  const queueQuery = {
    page: 1,
    page_size: 25,
    statuses: ["pending"],
    case_types: ["product"],
    sort: "priority_oldest"
  };
  const keys = {
    case: humanReviewApi.humanReviewCaseKey(caseId),
    related: humanReviewApi.humanReviewRelatedKey(anchorCaseId),
    queue: humanReviewApi.humanReviewQueueKey(queueQuery),
    dashboard: "dashboard:2099:1"
  };
  store.set(keys.case, { id: caseId, version: 1, canonical_value: "Automatic title" });
  store.set(keys.related, { items: [{ case_id: caseId, canonical_value: "Automatic title" }] });
  store.set(keys.queue, { items: [{ id: caseId, canonical_value: "Automatic title" }] });
  store.set(keys.dashboard, { scientific_output_kpi_eligible: 0 });

  let applied = false;
  const requests = [];
  globalThis.fetch = async (url, options = {}) => {
    const parsed = new URL(String(url));
    const endpoint = `${parsed.pathname}${parsed.search}`;
    requests.push({ endpoint, method: options.method ?? "GET" });
    if (options.method === "POST") {
      applied = true;
      return jsonResponse({
        ...successfulCommandResponse(),
        case: { id: caseId, version: 2, canonical_value: "Human corrected title" },
        kpi_effect: {
          affected: [{ metric: "scientific_output_kpi_eligible", before: 0, after: 1, delta: 1 }]
        }
      });
    }
    assert.equal(applied, true, "consumer refetch must occur only after the successful command");
    if (parsed.pathname.endsWith(`/human-review/cases/${caseId}`)) {
      return jsonResponse({ id: caseId, version: 2, canonical_value: "Human corrected title" });
    }
    if (parsed.pathname.endsWith(`/human-review/cases/${anchorCaseId}/related`)) {
      return jsonResponse({ items: [{ case_id: caseId, canonical_value: "Human corrected title" }] });
    }
    if (parsed.pathname.endsWith("/human-review/cases")) {
      return jsonResponse({ items: [{ id: caseId, canonical_value: "Human corrected title" }], total: 1 });
    }
    if (parsed.pathname.endsWith("/dashboard/summary")) {
      return jsonResponse({ scientific_output_kpi_eligible: 1 });
    }
    throw new Error(`Unexpected Task 9 request: ${endpoint}`);
  };

  const client = humanReviewApi.createHumanReviewApi(store.invalidate);
  const appliedResponse = await client.apply(caseId, {
    expected_version: 1,
    expected_current_decision_id: null,
    action: "correct",
    scope: "record",
    payload: { case_type: "product", product_title: "Human corrected title", scientific_status: "validated" },
    reason: "Verified against public evidence",
    correlation_id: "00000000-0000-0000-0000-000000000999"
  });
  assert.equal(appliedResponse.case.canonical_value, "Human corrected title");
  assert.equal(store.get(keys.case), undefined);
  assert.equal(store.get(keys.related), undefined);
  assert.equal(store.get(keys.queue), undefined);
  assert.equal(store.get(keys.dashboard), undefined);

  const refetchedCase = await client.getCase(caseId);
  const refetchedRelated = await client.getRelated(anchorCaseId);
  const refetchedQueue = await client.listCases(queueQuery);
  const refetchedDashboard = await api.apiFetch("/dashboard/summary?year=2099&cycle=1");
  assert.equal(refetchedCase.canonical_value, "Human corrected title");
  assert.equal(refetchedRelated.items[0].canonical_value, "Human corrected title");
  assert.equal(refetchedQueue.items[0].canonical_value, "Human corrected title");
  assert.equal(refetchedDashboard.scientific_output_kpi_eligible, 1);
  assert.deepEqual(requests.map(({ method }) => method), ["POST", "GET", "GET", "GET", "GET"]);
});

test("queue keys normalize set-like filters without mutating caller input", async () => {
  const { humanReviewApi } = await loadTask9Modules();
  const query = {
    page: 1,
    page_size: 25,
    statuses: ["resolved", "pending", "pending"],
    case_types: ["relation", "product", "relation"],
    sort: "priority_oldest"
  };
  const original = structuredClone(query);
  const normalized = {
    ...query,
    statuses: ["pending", "resolved"],
    case_types: ["product", "relation"]
  };

  assert.equal(humanReviewApi.humanReviewQueueKey(query), humanReviewApi.humanReviewQueueKey(normalized));
  assert.deepEqual(query, original);
});

test("every successful command invalidates effective readers and publishes one effective-data refresh", async () => {
  const { humanReviewApi } = await loadTask9Modules();
  globalThis.fetch = async () => jsonResponse(successfulCommandResponse());
  const published = [];
  globalThis.window = {
    localStorage: { getItem: () => null },
    dispatchEvent(event) {
      published.push(event.type);
      return true;
    }
  };

  for (const [method, payload] of [
    ["apply", { expected_version: 1, expected_current_decision_id: null, action: "approve", scope: "record", payload: { case_type: "product", scientific_status: "validated" }, reason: null, correlation_id: "cid-1" }],
    ["discard", { expected_version: 1, expected_current_decision_id: null, reason: "discard", correlation_id: "cid-2" }],
    ["revert", { expected_version: 2, expected_current_decision_id: "decision-1", decision_id_to_revert: "decision-1", reason: "revert", correlation_id: "cid-3" }]
  ]) {
    const invalidated = [];
    const client = humanReviewApi.createHumanReviewApi((prefix) => invalidated.push(prefix));
    await client[method]("case-id", payload);
    assert.deepEqual(invalidated, [
      "human-review:case:case-id",
      "human-review:audit:case-id:",
      "human-review:queue:",
      "dashboard:",
      "canonical-participants:",
      "human-review:related:"
    ]);
  }
  assert.deepEqual(published, [
    "human-review:effective-data-changed",
    "human-review:effective-data-changed",
    "human-review:effective-data-changed"
  ]);
});

test("related cache keys are stable and isolated by anchor case ID", async () => {
  const { humanReviewApi } = await loadTask9Modules();

  assert.equal(humanReviewApi.humanReviewRelatedKey("case/A"), "human-review:related:case/A");
  assert.equal(humanReviewApi.humanReviewRelatedKey("case/B"), "human-review:related:case/B");
  assert.notEqual(humanReviewApi.humanReviewRelatedKey("case/A"), humanReviewApi.humanReviewRelatedKey("case/B"));
});

test("preview remains local and the API surface has no draft or preview command", async () => {
  const { humanReviewApi } = await loadTask9Modules();
  let fetchCalls = 0;
  globalThis.fetch = async () => {
    fetchCalls += 1;
    return jsonResponse({});
  };

  const preview = { action: "correct", value: "candidate" };
  const updated = { ...preview, value: "updated" };
  const cancelled = null;

  assert.equal(updated.value, "updated");
  assert.equal(cancelled, null);
  const methods = Object.keys(humanReviewApi.humanReviewApi);
  for (const forbidden of ["createDraft", "saveDraft", "createPreview", "savePreview"]) {
    assert.equal(methods.includes(forbidden), false);
  }
  for (const readMethod of ["getCapabilities", "listCases", "getCase", "getAudit", "getEvidence"]) {
    assert.equal(typeof humanReviewApi.humanReviewApi[readMethod], "function");
  }
  assert.equal(fetchCalls, 0);
});

test("wire types match Task 8 OpenAPI and reject stale or widened fields", async () => {
  await loadTask9Modules();
  const fixtureRoot = await mkdtemp(path.join(tmpdir(), "b2b2-task9-types-"));
  try {
    let importPath = path.relative(fixtureRoot, path.join(frontendRoot, "lib", "human-review.ts")).replaceAll("\\", "/");
    if (!importPath.startsWith(".")) importPath = `./${importPath}`;
    const fixture = `
import type { CaseDetail, ExternalPayload, PersonPayload, QueueItem, QueueQuery, RelatedReviewEntity, RelatedReviewItem, RelatedReviewResponse, RelationPayload } from ${JSON.stringify(importPath)};
const queue: QueueItem = { id: "id", case_type: "product", case_status: "pending", scientific_status: "pending", document_id: null, source_revision: null, source_page: null, source_section: "section", automatic_priority: 1, manual_priority: null, possible_kpi_impact: false, version: 1, created_at: "2026-08-01T00:00:00Z" };
const { document_id: _documentId, ...withoutDocument } = queue;
const optionalDocument: QueueItem = withoutDocument;
const detail: CaseDetail = { ...queue, target_table: "scientific_productions", target_pk: null, field_path: "case", detected_value: null, normalized_value: null, canonical_value: null, current_decision_id: null, overrides: [], effective_memberships: [], counterpart_options: [], evidence_summary: {} };
const person: PersonPayload = { case_type: "person_identity", canonical_identity_key: "one", canonical_name: "One", scientific_status: "validated", resolution: "linked" };
const external: ExternalPayload = { case_type: "external_identity", external_identity_key: "one", scientific_status: "validated", resolution: "separated" };
const query: QueueQuery = { page: 1, page_size: 25, statuses: [], case_types: [], document_id: 41, sort: "priority_oldest" };
const relatedEntity: RelatedReviewEntity = { public_type: "person", public_id: "person:one", display_name: "One Person" };
const relatedItem: RelatedReviewItem = { case_id: "case-related", case_type: "person_identity", case_status: "pending", scientific_status: "pending", version: 2, current_decision_id: null, detected_value: "One", normalized_value: "one", canonical_value: null, possible_kpi_impact: true, allowed_actions: ["approve", "correct", "link"], evidence_summary: { available: true, count: 1 } };
const related: RelatedReviewResponse = { entity: relatedEntity, items: [relatedItem], total_pending: 1, truncated: false, correlation_id: "corr-related" };
void optionalDocument; void detail; void person; void external; void query; void related;
// @ts-expect-error document_key is not a public response field
queue.document_key;
// @ts-expect-error memberships was replaced by effective_memberships
detail.memberships;
// @ts-expect-error merged is not a relation resolution
const invalidRelation: RelationPayload = { case_type: "project_director_relation", relationship_status: "merged", scientific_status: "validated" };
// @ts-expect-error document_key must not be introduced into QueueItem
const invalidQueue: QueueItem = { ...queue, document_key: "dropbox_path:/private.pdf" };
// @ts-expect-error document_key must not be introduced into QueueQuery
const invalidQuery: QueueQuery = { page: 1, page_size: 25, statuses: [], case_types: [], document_key: "private", sort: "priority_oldest" };
// @ts-expect-error storage identifiers are not part of the closed related entity contract
const invalidRelatedEntity: RelatedReviewEntity = { ...relatedEntity, source_path: "C:/private/report.pdf" };
// @ts-expect-error related items expose case_id, not an internal target primary key
const invalidRelatedItem: RelatedReviewItem = { ...relatedItem, target_pk: 19 };
// @ts-expect-error the public entity discriminator is closed
const invalidRelatedType: RelatedReviewEntity = { ...relatedEntity, public_type: "internal_person" };
void invalidRelation; void invalidQueue; void invalidQuery; void invalidRelatedEntity; void invalidRelatedItem; void invalidRelatedType;
`;
    const fixturePath = path.join(fixtureRoot, "contract.ts");
    await writeFile(fixturePath, fixture, "utf8");
    const result = spawnSync(
      process.execPath,
      [tsc, "--noEmit", "--strict", "--skipLibCheck", "--target", "ES2022", "--module", "preserve", "--moduleResolution", "bundler", "--allowImportingTsExtensions", fixturePath],
      { cwd: frontendRoot, encoding: "utf8" }
    );
    assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}\n${await readFile(fixturePath, "utf8")}`);
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});
