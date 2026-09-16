import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { access, mkdir, rm, stat, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import path from "node:path";
import test, { after, afterEach, before, beforeEach } from "node:test";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const nextBin = path.join(frontendRoot, "node_modules", "next", "dist", "bin", "next");
const reportRoot = path.resolve(frontendRoot, "..", "reports", "task14-browser");
const disclosureFixtureDirectory = path.join(frontendRoot, "app", "task7-evidence-fixture");
const disclosureFixtureGeneratedTypesDirectory = path.join(frontendRoot, ".next", "types", "app", "task7-evidence-fixture");
const disclosureFixtureRoute = "/task7-evidence-fixture";
const forbidden = /document_key|stable_target_key|dropbox_path|source_path|bucket|object_key|access_token|private\/task14|postgresql:\/\//i;

let server;
let browser;
let context;
let page;
let baseUrl;
let api;

function queueItem(overrides = {}) {
  return {
    id: "e3000000-0000-0000-0000-000000000001",
    case_type: "product",
    case_status: "pending",
    scientific_status: "pending",
    document_id: 41,
    source_revision: "task14-rev-1",
    source_page: 1,
    source_section: "Scientific production",
    automatic_priority: 90,
    manual_priority: 7,
    possible_kpi_impact: true,
    version: 1,
    created_at: "2026-08-08T10:00:00Z",
    ...overrides
  };
}

function caseDetail(overrides = {}) {
  return {
    ...queueItem(),
    target_table: "scientific_productions",
    target_pk: 701,
    field_path: "product_title",
    detected_value: "Automatic Task 14 title",
    normalized_value: "automatic task 14 title",
    canonical_value: "Automatic Task 14 title",
    current_decision_id: null,
    overrides: [],
    effective_memberships: ["product_pending"],
    counterpart_options: [],
    evidence_summary: {
      available: true,
      count: 1,
      document_name: "task14-public.pdf",
      page: 1,
      section: "Scientific production",
      fragment: "Public synthetic evidence for Task 14."
    },
    ...overrides
  };
}

function relatedItem(detail) {
  return {
    case_id: detail.id,
    case_type: detail.case_type,
    case_status: detail.case_status,
    scientific_status: detail.scientific_status,
    version: detail.version,
    current_decision_id: detail.current_decision_id,
    detected_value: detail.detected_value,
    normalized_value: detail.normalized_value,
    canonical_value: detail.canonical_value,
    possible_kpi_impact: detail.possible_kpi_impact,
    allowed_actions: detail.case_type === "product" ? ["approve", "correct"] : ["approve", "correct", "link"],
    evidence_summary: detail.evidence_summary
  };
}

function initialApiState() {
  return {
    capability: {
      capability: "RESEARCH_MANAGER",
      actions: ["view_foundations", "view_audit", "apply_scientific", "propose_scientific", "revert_scientific"]
    },
    queueMode: "normal",
    detailMode: "normal",
    evidenceMode: "normal",
    evidenceRequests: [],
    detail: caseDetail(),
    detailByCaseId: {},
    detailRequestIds: [],
    relatedItems: null,
    relatedTruncated: false,
    commandFailureByCaseId: {},
    queueRequests: [],
    commandRequests: [],
    effectiveRevision: 0,
    consoleErrors: [],
    pageErrors: [],
    failedResponses: [],
    detailRequests: 0,
    effectiveReaderRequests: { participants: 0, production: 0, projects: 0 },
    conflictOnce: false,
    reloadFails: false,
    slowCommand: false
  };
}

function json(body, status = 200, headers = {}) {
  return {
    status,
    contentType: "application/json",
    headers: { "Cache-Control": "private, no-store", ...headers },
    body: JSON.stringify(body)
  };
}

function error(status, code, correlation = `task14-${status}`) {
  return json({ code, message: "Safe public error", correlation_id: correlation }, status);
}

async function freePort() {
  return await new Promise((resolve, reject) => {
    const listener = createServer();
    listener.once("error", reject);
    listener.listen(0, "127.0.0.1", () => {
      const address = listener.address();
      const port = typeof address === "object" && address ? address.port : null;
      listener.close((closeError) => closeError ? reject(closeError) : resolve(port));
    });
  });
}

async function waitForServer(url) {
  const deadline = Date.now() + 45_000;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok || response.status < 500) return;
    } catch (caught) {
      lastError = caught;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Next.js did not start: ${lastError ?? "timeout"}`);
}

async function fulfillApi(route) {
  const request = route.request();
  const url = new URL(request.url());
  const endpoint = url.pathname.replace(/^\/api\/v1/, "");
  if (endpoint === "/metadata/careers") return route.fulfill(json([]));
  if (endpoint === "/metadata/periods") return route.fulfill(json([]));
  if (endpoint === "/human-review/me") return route.fulfill(json(api.capability));
  if (endpoint === "/human-review/effective-data-revision") {
    return route.fulfill(json({ revision: api.effectiveRevision }));
  }
  if (endpoint === "/participants") {
    api.effectiveReaderRequests.participants += 1;
    return route.fulfill(json([]));
  }
  if (endpoint === "/production") {
    api.effectiveReaderRequests.production += 1;
    return route.fulfill(json([]));
  }
  if (endpoint === "/projects") {
    api.effectiveReaderRequests.projects += 1;
    return route.fulfill(json([]));
  }
  if (endpoint === "/dashboard/summary") return route.fulfill(json({
    scientific_output_detected: 0,
    scientific_output_kpi_eligible: 0,
    scientific_output_pending_review: 0,
    scientific_output_discarded: 0
  }));

  const relatedMatch = endpoint.match(/^\/human-review\/cases\/([^/]+)\/related$/);
  if (relatedMatch && request.method() === "GET") {
    const items = api.relatedItems ?? [relatedItem(api.detail)];
    return route.fulfill(json({
      entity: { public_type: "scientific_product", public_id: "product:task8", display_name: "Producto científico" },
      items,
      total_pending: items.filter((item) => item.case_status === "pending").length,
      truncated: api.relatedTruncated,
      correlation_id: "task8-related"
    }));
  }

  if (endpoint === "/human-review/cases" && request.method() === "GET") {
    api.queueRequests.push(url.toString());
    if (api.queueMode === "slow") await new Promise((resolve) => setTimeout(resolve, 350));
    if (api.queueMode === "error") return route.fulfill(error(503, "HUMAN_REVIEW_INTERNAL_ERROR", "support-queue-503"));
    const pageNumber = Number(url.searchParams.get("page") ?? 1);
    const pageSize = Number(url.searchParams.get("page_size") ?? 25);
    const items = api.queueMode === "empty" ? [] : [queueItem({ id: `e3000000-0000-0000-0000-${String(pageNumber).padStart(12, "0")}` })];
    return route.fulfill(json({
      items,
      total: api.queueMode === "empty" ? 0 : 30,
      page: pageNumber,
      page_size: pageSize,
      facets: { statuses: { pending: items.length }, case_types: { product: items.length } },
      correlation_id: "task14-queue"
    }));
  }

  const detailMatch = endpoint.match(/^\/human-review\/cases\/([^/]+)$/);
  if (detailMatch && request.method() === "GET") {
    const requestedCaseId = decodeURIComponent(detailMatch[1]);
    api.detailRequests += 1;
    api.detailRequestIds.push(requestedCaseId);
    if (api.reloadFails && api.detailRequests > 1) return route.fulfill(error(503, "HUMAN_REVIEW_INTERNAL_ERROR", "support-reload-503"));
    if (api.detailMode !== "normal") {
      const status = Number(api.detailMode);
      return route.fulfill(error(status, status === 404 ? "REVIEW_CASE_NOT_FOUND" : status === 403 ? "B2B_CAPABILITY_REQUIRED" : "HUMAN_REVIEW_INTERNAL_ERROR"));
    }
    const requestedDetail = requestedCaseId === api.detail.id ? api.detail : api.detailByCaseId[requestedCaseId];
    return requestedDetail
      ? route.fulfill(json(requestedDetail))
      : route.fulfill(error(404, "REVIEW_CASE_NOT_FOUND", "support-lazy-404"));
  }

  if (/\/human-review\/cases\/[^/]+\/audit$/.test(endpoint)) {
    return route.fulfill(json({
      items: [{
        id: "audit-task14",
        event_type: "scientific_decision_applied",
        created_at: "2026-08-08T10:30:00Z",
        correlation_id: "task14-audit",
        summary: "Scientific decision applied",
        payload: { decision_type: "corrected", previous_case_status: "pending", resulting_case_status: "resolved" }
      }],
      page: 1,
      page_size: 25,
      total: 1,
      correlation_id: "task14-audit"
    }));
  }

  if (/\/human-review\/cases\/[^/]+\/evidence$/.test(endpoint)) {
    api.evidenceRequests.push(endpoint);
    if (api.evidenceMode !== "normal") {
      const status = Number(api.evidenceMode);
      return route.fulfill(error(status, status === 404 ? "REVIEW_CASE_NOT_FOUND" : status === 403 ? "B2B_CAPABILITY_REQUIRED" : "EVIDENCE_UNAVAILABLE", `support-evidence-${status}`));
    }
    return route.fulfill({
      status: 200,
      contentType: "application/pdf",
      headers: { "Cache-Control": "private, no-store", "Content-Disposition": "inline; filename=task14-public.pdf" },
      body: Buffer.from("%PDF-1.4\n% Task 14 synthetic browser evidence\n")
    });
  }

  const commandMatch = endpoint.match(/^\/human-review\/cases\/([^/]+)\/(apply|discard|revert)$/);
  if (commandMatch && request.method() === "POST") {
    const commandCaseId = decodeURIComponent(commandMatch[1]);
    const body = request.postDataJSON();
    api.commandRequests.push({ caseId: commandCaseId, operation: commandMatch[2], body });
    if (api.slowCommand) await new Promise((resolve) => setTimeout(resolve, 400));
    if (api.conflictOnce) {
      api.conflictOnce = false;
      return route.fulfill(error(409, "REVIEW_CASE_VERSION_CONFLICT", "support-conflict-409"));
    }
    const configuredFailure = api.commandFailureByCaseId[commandCaseId];
    if (configuredFailure) {
      if (configuredFailure.once) delete api.commandFailureByCaseId[commandCaseId];
      return route.fulfill(error(configuredFailure.status, configuredFailure.code, configuredFailure.correlation));
    }
    const currentDetail = commandCaseId === api.detail.id ? api.detail : api.detailByCaseId[commandCaseId];
    const version = currentDetail.version + 1;
    if (commandMatch[2] === "revert") {
      api.detail = caseDetail({ version, case_status: "reopened", scientific_status: "pending", current_decision_id: "decision-reversal" });
    } else {
      const updated = caseDetail({
        ...currentDetail,
        version,
        case_status: "resolved",
        scientific_status: commandMatch[2] === "discard" ? "discarded" : "validated",
        current_decision_id: `decision-${commandMatch[2]}`,
        canonical_value: body.payload?.canonical_name ?? body.payload?.product_title ?? currentDetail.canonical_value
      });
      if (commandCaseId === api.detail.id) api.detail = updated;
      else api.detailByCaseId[commandCaseId] = updated;
    }
    const responseDetail = commandCaseId === api.detail.id ? api.detail : api.detailByCaseId[commandCaseId];
    api.effectiveRevision += 1;
    if (api.relatedItems) {
      api.relatedItems = api.relatedItems.map((item) => item.case_id === commandCaseId ? relatedItem(responseDetail) : item);
    }
    return route.fulfill(json({ case: responseDetail, decision_id: responseDetail.current_decision_id, kpi_effect: { affected: [] }, correlation_id: body.correlation_id }));
  }

  return route.fulfill(error(404, "REVIEW_CASE_NOT_FOUND", "support-unhandled"));
}

const persistedProfile = {
  full_name: "Task 14 Manager",
  role: "FACULTY_ADMIN",
  career_id: null
};

async function newPage(
  viewport = { width: 1440, height: 1000 },
  profile = persistedProfile
) {
  if (context) await context.close();
  context = await browser.newContext({ viewport, timezoneId: "America/Guayaquil" });
  await context.addInitScript((storedProfile) => {
    localStorage.setItem("token", "task14-synthetic-token");
    if (storedProfile) localStorage.setItem("profile", JSON.stringify(storedProfile));
    else localStorage.removeItem("profile");
  }, profile);
  page = await context.newPage();
  page.on("console", (message) => {
    if (message.type() === "error") api.consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => api.pageErrors.push(error.stack ?? error.message));
  page.on("response", (response) => {
    if (response.status() >= 400) api.failedResponses.push({ status: response.status(), url: response.url() });
  });
  await page.route("**/api/v1/**", fulfillApi);
  return page;
}

async function gotoDetail() {
  await page.goto(`${baseUrl}/human-review/cases/${api.detail.id}`);
  await page.getByRole("heading", { name: /Detalle del caso/i }).waitFor();
  await page.getByRole("heading", { name: /Informaci.n detectada/i }).waitFor();
}

async function startProductAction(label, title = "Human Task 14 title") {
  await page.getByRole("button", { name: new RegExp(label, "i") }).click();
  const titleInput = page.getByLabel(/T.tulo del producto/i);
  if (await titleInput.count()) await titleInput.fill(title);
  const reason = page.getByLabel(/Motivo .*obligatorio|Motivo cuando/i);
  await reason.fill("Verified in the Task 14 browser journey");
}

before(async () => {
  await mkdir(reportRoot, { recursive: true });
  await mkdir(disclosureFixtureDirectory, { recursive: true });
  await writeFile(path.join(disclosureFixtureDirectory, "page.tsx"), `"use client";

import { EvidenceWorkspace } from "@/components/human-review/EvidenceWorkspace";

const evidenceSummary = {
  available: true,
  count: 1,
  document_name: "fixture-public.pdf",
  page: 5,
  section: "Fixture public section",
  fragment: "Fixture public fragment"
};

export default function Task7EvidenceFixture() {
  return <main className="p-4"><EvidenceWorkspace caseId="fixture-case" evidenceSummary={evidenceSummary} /><output data-testid="active-evidence-fixture">fixture-case|fixture-public.pdf|Fixture public section</output><button id="fixture-outside" type="button">Continuar revisión</button></main>;
}
`, "utf8");
  const port = await freePort();
  baseUrl = `http://127.0.0.1:${port}`;
  server = spawn(process.execPath, [nextBin, "dev", "--hostname", "127.0.0.1", "--port", String(port)], {
    cwd: frontendRoot,
    env: { ...process.env, NEXT_PUBLIC_API_URL: `${baseUrl}/api/v1`, NEXT_TELEMETRY_DISABLED: "1" },
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true
  });
  const logs = [];
  server.stdout.on("data", (chunk) => logs.push(chunk.toString()));
  server.stderr.on("data", (chunk) => logs.push(chunk.toString()));
  server.once("exit", (code) => {
    if (code && code !== 0) process.stderr.write(logs.join(""));
  });
  await waitForServer(baseUrl);
  browser = await chromium.launch({ headless: true });
});

beforeEach(async () => {
  api = initialApiState();
  await newPage();
});

afterEach(async () => {
  if (context) await context.close();
  context = null;
  page = null;
});

after(async () => {
  if (browser) await browser.close();
  if (server && !server.killed) {
    server.kill();
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  await rm(disclosureFixtureDirectory, { recursive: true, force: true });
  await rm(disclosureFixtureGeneratedTypesDirectory, { recursive: true, force: true });
  await assert.rejects(access(disclosureFixtureDirectory), { code: "ENOENT" });
  await assert.rejects(access(disclosureFixtureGeneratedTypesDirectory), { code: "ENOENT" });
});

test("401 and 404 detail responses stay distinct, correlated and sanitized", { timeout: 60_000 }, async () => {
  for (const [status, expected] of [[401, /No pudimos cargar/i], [404, /no existe|no est. disponible/i]]) {
    api = initialApiState();
    api.detailMode = String(status);
    await newPage();
    await page.goto(`${baseUrl}/human-review/cases/${api.detail.id}`);
    const alert = page.getByRole("alert").filter({ hasText: new RegExp(`task14-${status}`, "i") });
    await alert.waitFor();
    assert.match(await alert.innerText(), expected);
    assert.doesNotMatch(await page.locator("body").innerText(), forbidden);
  }
});

test("detail route presents the guided validation session", { timeout: 60_000 }, async () => {
  await gotoDetail();
  await page.getByRole("heading", { name: /Sesi.n de validaci.n/i }).waitFor();
  await page.getByRole("region", { name: /Validaci.n activa/i }).waitFor();
});

test("mounted effective readers refetch after a human-review decision event", { timeout: 60_000 }, async () => {
  const readers = [
    { route: "/teachers", endpoint: "/participants", key: "participants" },
    { route: "/production", endpoint: "/production", key: "production" },
    { route: "/projects", endpoint: "/projects", key: "projects" }
  ];
  for (const reader of readers) {
    await newPage({ width: 1440, height: 1000 });
    const initial = page.waitForResponse((response) => new URL(response.url()).pathname.endsWith(reader.endpoint));
    await page.goto(`${baseUrl}${reader.route}`);
    await initial;
    await new Promise((resolve) => setTimeout(resolve, 250));
    const before = api.effectiveReaderRequests[reader.key];
    assert.ok(before >= 1, `${reader.key} completed its initial effective-data read`);
    const refreshed = page.waitForResponse(
      (response) => new URL(response.url()).pathname.endsWith(reader.endpoint),
      { timeout: 5_000 }
    );
    await page.evaluate(() => window.dispatchEvent(new Event("human-review:effective-data-changed")));
    await refreshed;
    assert.equal(api.effectiveReaderRequests[reader.key], before + 1);
  }
});

test("guided session retains independent drafts, lazily caches related detail, switches evidence and isolates partial conflicts", { timeout: 90_000 }, async () => {
  const anchor = caseDetail({
    detected_value: "Título ancla",
    canonical_value: "Título ancla",
    evidence_summary: { available: true, count: 1, document_name: "anchor-public.pdf", page: 1, section: "Anchor", fragment: "Anchor evidence" }
  });
  const relatedPerson = caseDetail({
    id: "e3000000-0000-0000-0000-000000000002",
    case_type: "person_identity",
    target_table: "person_roles",
    target_pk: 702,
    field_path: "canonical_name",
    detected_value: "MARIE CURIE",
    normalized_value: "MARIE CURIE",
    canonical_value: "MARIE CURIE",
    evidence_summary: { available: true, count: 1, document_name: "person-public.pdf", page: 2, section: "People", fragment: "Person evidence" }
  });
  const inaccessible = caseDetail({
    id: "e3000000-0000-0000-0000-000000000003",
    detected_value: "Caso no accesible",
    canonical_value: "Caso no accesible",
    evidence_summary: { available: false }
  });
  api.detail = anchor;
  api.detailByCaseId = { [relatedPerson.id]: relatedPerson };
  api.relatedItems = [relatedItem(anchor), relatedItem(relatedPerson), relatedItem(inaccessible)];
  api.relatedTruncated = true;

  await gotoDetail();
  await page.getByText(/primeras revisiones relacionadas/i).waitFor();
  await page.getByRole("button", { name: /Corregir/i }).first().click();
  await page.getByText("Preparados 0", { exact: true }).waitFor();
  await page.getByText("Inválidos 1", { exact: true }).waitFor();
  await page.getByLabel(/T.tulo del producto/i).fill("Título ancla corregido");
  await page.getByLabel(/Motivo obligatorio/i).fill("Evidencia pública del caso ancla");

  const personSelector = page.getByRole("button", { name: /Identidad de persona/i });
  await personSelector.focus();
  await page.keyboard.press("Enter");
  await page.getByText("MARIE CURIE", { exact: true }).first().waitFor();
  assert.equal(await page.getByRole("heading", { name: "Revisión activa" }).evaluate((element) => document.activeElement === element), true);
  await page.getByRole("button", { name: /Corregir/i }).first().click();
  await page.getByLabel(/Nombre can.nico/i).fill("MARIE SKLODOWSKA CURIE");
  await page.getByLabel(/Motivo obligatorio/i).fill("Identidad comprobada en evidencia pública");
  await page.getByTitle("Vista previa de person-public.pdf").waitFor();
  assert.equal(await page.getByTitle("Vista previa de anchor-public.pdf").count(), 0, "stale anchor blob is never shown for the person case");

  await page.getByRole("button", { name: /Producci.n cient.fica.*T.tulo ancla/i }).click();
  assert.equal(await page.getByLabel(/T.tulo del producto/i).inputValue(), "Título ancla corregido");
  await personSelector.click();
  assert.equal(await page.getByLabel(/Nombre can.nico/i).inputValue(), "MARIE SKLODOWSKA CURIE");
  assert.equal(api.detailRequestIds.filter((caseId) => caseId === relatedPerson.id).length, 1, "related detail is cached by case ID");

  await page.getByRole("button", { name: /Producci.n cient.fica.*Caso no accesible/i }).click();
  await page.getByRole("alert").filter({ hasText: /support-lazy-404/i }).waitFor();
  assert.doesNotMatch(await page.locator("body").innerText(), /source_path|dropbox_path|Request failed/i);
  await personSelector.click();
  assert.equal(await page.getByLabel(/Nombre can.nico/i).inputValue(), "MARIE SKLODOWSKA CURIE", "a per-case load failure cannot discard another draft");

  api.commandFailureByCaseId[relatedPerson.id] = {
    status: 409,
    code: "REVIEW_CASE_VERSION_CONFLICT",
    correlation: "support-session-conflict",
    once: true
  };
  await page.getByText("Preparados 2", { exact: true }).waitFor();
  await page.getByRole("button", { name: /Confirmar decisi.n/i }).click();
  await page.waitForTimeout(1_000);
  assert.equal(
    api.commandRequests.filter(({ operation }) => operation === "apply").length,
    2,
    await page.locator("body").innerText()
  );
  assert.ok(api.failedResponses.some(({ status, url }) => status === 409 && url.includes(relatedPerson.id)), JSON.stringify(api.failedResponses));
  assert.equal(
    await page.getByRole("dialog", { name: /caso fue actualizado/i }).count(),
    1,
    await page.locator("body").innerText()
  );
  await page.getByRole("dialog", { name: /caso fue actualizado/i }).waitFor();
  assert.equal(api.commandRequests.filter(({ operation }) => operation === "apply").length, 2, "valid drafts are confirmed independently");
  assert.ok(await page.getByText(/Revisi.n confirmada/i).count() >= 1, "success stays visible and collapsed in the related list");
  assert.equal(await page.getByLabel(/Nombre can.nico/i).inputValue(), "MARIE SKLODOWSKA CURIE", "the failed draft is preserved");
  await page.getByText("Preparados 0", { exact: true }).waitFor();
  assert.equal(await page.getByRole("button", { name: /Confirmar decisi.n/i }).isDisabled(), true, "a retained 409 draft is not offered as prepared");

  api.detailByCaseId[relatedPerson.id] = { ...relatedPerson, version: 2, current_decision_id: "decision-concurrent" };
  await page.getByRole("dialog").getByRole("button", { name: /Recargar caso/i }).click();
  await page.getByText(/Informaci.n actualizada/i).waitFor();
  await page.getByText("Preparados 1", { exact: true }).waitFor();
  assert.equal(api.commandRequests.filter(({ operation }) => operation === "apply").length, 2, "explicit reload never retries a command");
  assert.deepEqual(api.consoleErrors.filter((message) => !/Failed to load resource.*(?:404|409)/i.test(message)), []);
  assert.deepEqual(api.pageErrors, []);
});

test("confirmed clear-all removes a results-only session and empty related data falls back to the anchor", { timeout: 75_000 }, async () => {
  api.relatedItems = [];
  await gotoDetail();
  assert.equal(await page.getByRole("button", { name: /Producci.n cient.fica.*Automatic Task 14 title/i }).count(), 1, "the anchor is the sole fallback item");
  await startProductAction("Corregir", "Resultado local para limpiar");
  await page.getByText("Preparados 1", { exact: true }).waitFor();
  const applied = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname.endsWith(`/human-review/cases/${api.detail.id}/apply`);
  });
  await page.getByRole("button", { name: /Confirmar decisi.n/i }).click();
  assert.equal((await applied).status(), 200);
  await page.getByText(/La decisi.n fue aplicada correctamente/i).waitFor();
  await page.getByText("Confirmada · solo lectura", { exact: true }).waitFor();
  const clear = page.getByRole("button", { name: /Limpiar borradores/i });
  assert.equal(await clear.isDisabled(), false, "results-only state remains clearable");
  page.once("dialog", (dialog) => dialog.accept());
  await clear.click();
  await page.getByText(/Se limpiaron los borradores y resultados locales/i).waitFor();
  assert.equal(await page.getByText(/Revisi.n confirmada/i).count(), 0);
  assert.equal(await clear.isDisabled(), true);
});

test("unconfirmed in-memory drafts are discarded by reload and route navigation", { timeout: 75_000 }, async () => {
  await gotoDetail();
  await page.getByRole("button", { name: /Corregir/i }).first().click();
  await page.getByLabel(/T.tulo del producto/i).fill("Borrador que no debe sobrevivir recarga");
  await page.reload({ waitUntil: "networkidle" });
  await page.getByRole("heading", { name: /Sesi.n de validaci.n/i }).waitFor();
  assert.equal(await page.getByLabel(/T.tulo del producto/i).count(), 0, "reload remounts an empty in-memory session");

  await page.getByRole("button", { name: /Corregir/i }).first().click();
  await page.getByLabel(/T.tulo del producto/i).fill("Borrador que no debe sobrevivir navegación");
  await page.getByRole("link", { name: /Volver a la bandeja/i }).click();
  await page.waitForURL(/\/human-review$/);
  await page.goto(`${baseUrl}/human-review/cases/${api.detail.id}`);
  await page.getByRole("heading", { name: /Sesi.n de validaci.n/i }).waitFor();
  assert.equal(await page.getByLabel(/T.tulo del producto/i).count(), 0, "route navigation remounts an empty in-memory session");
  assert.equal(api.commandRequests.length, 0);
});

test("queue filters remain safe across single, multiple, combined, reset, date and detail flows", { timeout: 90_000 }, async () => {
  await page.goto(`${baseUrl}/human-review`);
  await page.getByText("30 resultados").waitFor();
  assert.equal(await page.getByLabel("Creado desde").inputValue(), "");
  assert.equal(await page.getByLabel("Creado hasta").inputValue(), "");

  async function applyAndReadRequest(expected) {
    const response = page.waitForResponse((candidate) => {
      const url = new URL(candidate.url());
      return url.pathname.endsWith("/human-review/cases") && expected(url.searchParams);
    });
    await page.getByRole("button", { name: /Aplicar filtros/i }).click();
    return new URL((await response).url());
  }

  const clear = async () => {
    await page.getByRole("button", { name: /Limpiar filtros/i }).click();
    assert.deepEqual(await page.getByLabel("Estados").locator("option:checked").allTextContents(), []);
    assert.deepEqual(await page.getByLabel("Tipos de caso").locator("option:checked").allTextContents(), []);
    assert.equal(await page.getByLabel("Creado desde").inputValue(), "");
    assert.equal(await page.getByLabel("Creado hasta").inputValue(), "");
  };

  // This exact sequence triggered the deferred SyntheticEvent.currentTarget crash.
  await page.getByLabel("Estados").selectOption(["pending"]);
  await page.waitForTimeout(50);
  assert.equal(
    await page.getByLabel("Tipos de caso").count(),
    1,
    `The case type filter disappeared after the status change: ${[...api.consoleErrors, ...api.pageErrors].join(" | ")}`
  );
  await page.getByLabel("Tipos de caso").selectOption(["person_identity"]);
  assert.deepEqual(api.consoleErrors, [], JSON.stringify(api.failedResponses));
  assert.deepEqual(api.pageErrors, []);
  const combined = await applyAndReadRequest((params) => params.has("status") && params.has("case_type"));
  assert.deepEqual(combined.searchParams.getAll("status"), ["pending"]);
  assert.deepEqual(combined.searchParams.getAll("case_type"), ["person_identity"]);
  assert.equal(combined.searchParams.get("page"), "1");

  await clear();
  await page.getByLabel("Estados").selectOption(["resolved"]);
  const statusOnly = await applyAndReadRequest((params) => params.get("status") === "resolved" && !params.has("case_type"));
  assert.deepEqual(statusOnly.searchParams.getAll("status"), ["resolved"]);

  await clear();
  await page.getByLabel("Tipos de caso").selectOption(["product"]);
  const typeOnly = await applyAndReadRequest((params) => params.get("case_type") === "product" && !params.has("status"));
  assert.deepEqual(typeOnly.searchParams.getAll("case_type"), ["product"]);

  await clear();
  const secondPage = page.waitForResponse((response) => new URL(response.url()).searchParams.get("page") === "2");
  await page.getByRole("button", { name: /Siguiente/i }).click();
  await secondPage;
  await page.getByLabel("Estados").selectOption(["pending", "resolved"]);
  await page.getByLabel("Tipos de caso").selectOption(["author_identity", "product"]);
  const multiple = await applyAndReadRequest((params) => params.getAll("status").length === 2 && params.getAll("case_type").length === 2);
  assert.deepEqual(multiple.searchParams.getAll("status"), ["pending", "resolved"]);
  assert.deepEqual(multiple.searchParams.getAll("case_type"), ["author_identity", "product"]);
  assert.equal(multiple.searchParams.get("page"), "1");
  assert.equal(multiple.searchParams.has("statuses"), false);
  assert.equal(multiple.searchParams.has("case_types"), false);
  assert.equal(multiple.searchParams.has("document_key"), false);

  await clear();
  await page.getByLabel("Creado desde").fill("2026-08-10T01:07");
  const dated = await applyAndReadRequest((params) => params.has("created_from"));
  assert.equal(dated.searchParams.get("created_from"), "2026-08-10T06:07:00.000Z");

  await clear();
  const requestsBeforeEmptyApply = api.queueRequests.length;
  await page.getByRole("button", { name: /Aplicar filtros/i }).click();
  await page.waitForTimeout(50);
  assert.equal(api.queueRequests.length, requestsBeforeEmptyApply, "unchanged empty filters reuse the safe cached query");
  assert.deepEqual(api.consoleErrors, [], JSON.stringify(api.failedResponses));
  assert.deepEqual(api.pageErrors, []);

  const reviewLink = page.getByRole("link", { name: /Revisar producci.n cient.fica/i }).first();
  assert.equal(await reviewLink.count(), 1, await page.locator("body").innerText());
  const expectedHref = await reviewLink.getAttribute("href");
  assert.equal(expectedHref, `/human-review/cases/${api.detail.id}`);
  await reviewLink.click();
  await page.waitForURL(new RegExp(`/human-review/cases/${api.detail.id}$`));
  await page.getByRole("heading", { name: /Preparar una decisi.n/i }).waitFor();
  assert.equal(context.pages().length, 1, "case review stays in the same tab");
  await page.getByRole("button", { name: /Corregir/i }).click();
  await page.getByText(/Vista previa local/i).waitFor();
  assert.equal(api.commandRequests.length, 0, "preview must not write");
  await page.getByRole("button", { name: /^Cancelar$/i }).click();
  assert.equal(api.commandRequests.length, 0, "cancel must not write");
  assert.deepEqual(api.consoleErrors, [], JSON.stringify(api.failedResponses));
  assert.deepEqual(api.pageErrors, []);
});

test("queue renders, validates filters, paginates and keeps loading, empty and safe error states distinct", { timeout: 60_000 }, async () => {
  api.queueMode = "slow";
  await page.goto(`${baseUrl}/human-review`);
  await page.getByRole("status").filter({ hasText: /Cargando casos/i }).waitFor();
  assert.equal(await page.getByRole("button", { name: /Aplicar filtros/i }).isDisabled(), true);
  await page.getByText("30 resultados").waitFor();
  assert.equal(await page.getByRole("button", { name: /Aplicar filtros/i }).isDisabled(), false);
  api.queueMode = "normal";
  const requestsBeforeInvalid = api.queueRequests.length;
  await page.getByLabel("Buscar").fill("ab");
  await page.getByRole("button", { name: /Aplicar filtros/i }).click();
  assert.equal(await page.getByLabel("Buscar").evaluate((input) => input.validity.valid), false);
  assert.equal(api.queueRequests.length, requestsBeforeInvalid);
  const beforeValidSearch = api.queueRequests.length;
  await page.getByLabel("Buscar").fill("Task 14");
  const validSearchResponse = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname.endsWith("/human-review/cases") && url.searchParams.get("q") === "Task 14";
  });
  await page.getByRole("button", { name: /Aplicar filtros/i }).click();
  await validSearchResponse;
  assert.ok(api.queueRequests.length > beforeValidSearch);
  assert.equal(new URL(api.queueRequests.at(-1)).searchParams.get("q"), "Task 14");
  const secondPageResponse = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname.endsWith("/human-review/cases") && url.searchParams.get("page") === "2";
  });
  await page.getByRole("button", { name: /Siguiente/i }).click();
  await secondPageResponse;
  assert.equal(new URL(api.queueRequests.at(-1)).searchParams.get("page"), "2");

  api.queueMode = "empty";
  await newPage();
  await page.goto(`${baseUrl}/human-review`);
  await page.getByText(/No hay casos de revisi.n humana/i).waitFor();
  assert.deepEqual(api.consoleErrors, [], JSON.stringify(api.failedResponses));
  assert.deepEqual(api.pageErrors, []);

  api.queueMode = "error";
  await newPage();
  await page.goto(`${baseUrl}/human-review`);
  const alert = page.getByRole("alert").filter({ hasText: /support-queue-503/i });
  await alert.waitFor();
  assert.match(await alert.innerText(), /no est. disponible|no pudimos cargar/i);
  assert.match(await alert.innerText(), /support-queue-503/i);
  assert.doesNotMatch(await page.locator("body").innerText(), forbidden);
  assert.equal(await page.evaluate(() => localStorage.getItem("token")), "task14-synthetic-token");
  assert.match(await page.evaluate(() => localStorage.getItem("profile") ?? ""), /Task 14 Manager/);
  assert.deepEqual(api.consoleErrors, ["Failed to load resource: the server responded with a status of 503 (Service Unavailable)"]);
  assert.deepEqual(api.pageErrors, []);
});

test("detail streams evidence and enforces local preview, valid apply, duplicate link, discard and revert commands", { timeout: 90_000 }, async () => {
  await gotoDetail();
  await page.getByRole("button", { name: /Cargar evidencia|Actualizar evidencia/i }).click();
  const documentLink = page.getByRole("link", { name: /Abrir documento original en una pestaña nueva/i });
  await documentLink.waitFor();
  assert.match(await documentLink.getAttribute("href"), /^blob:/);
  assert.equal(await documentLink.getAttribute("target"), "_blank");
  assert.equal(await documentLink.getAttribute("download"), null);
  const detailCopy = await page.locator("body").innerText();
  assert.match(detailCopy, /Evidencia y validación de revisiones relacionadas/i);
  assert.match(detailCopy, /Historial de auditoría/i);
  assert.doesNotMatch(detailCopy, forbidden);
  const preview = page.getByTitle(/Vista previa de task14-public\.pdf/i);
  await preview.waitFor();
  assert.match(await preview.getAttribute("src"), /^blob:.*#page=1$/);

  await page.getByRole("button", { name: /Corregir/i }).click();
  await page.getByLabel(/T.tulo del producto/i).fill("Human Task 14 corrected title");
  assert.equal(api.commandRequests.length, 0, "local preview must not write");
  await page.getByRole("button", { name: /^Cancelar$/i }).click();
  assert.equal(api.commandRequests.length, 0, "cancel must discard the local draft");
  await startProductAction("Corregir", "Human Task 14 corrected title");
  api.slowCommand = true;
  const confirm = page.getByRole("button", { name: /Confirmar decisi.n/i });
  await confirm.dblclick();
  await page.getByText(/decisi.n fue aplicada correctamente/i).waitFor();
  assert.equal(api.commandRequests.filter(({ operation }) => operation === "apply").length, 1);
  const applyBody = api.commandRequests.find(({ operation }) => operation === "apply").body;
  assert.deepEqual(Object.keys(applyBody).sort(), ["action", "correlation_id", "expected_current_decision_id", "expected_version", "payload", "reason", "scope"]);
  assert.equal(applyBody.expected_version, 1);
  assert.equal(applyBody.payload.product_title, "Human Task 14 corrected title");
  assert.doesNotMatch(JSON.stringify(applyBody), forbidden);

  api = initialApiState();
  api.detail = caseDetail({
    case_type: "possible_duplicate",
    target_table: "person_roles",
    target_pk: 801,
    field_path: "case",
    detected_value: "Ada Task 14",
    normalized_value: "ada task 14",
    canonical_value: null,
    counterpart_options: [{ counterpart_ref: { target_type: "person_roles", target_id: 802 }, display_name: "Ada Task 14 B", source_label: "Ada B", document_name: "public-b.pdf" }]
  });
  await newPage();
  await gotoDetail();
  await page.getByRole("button", { name: /Vincular/i }).click();
  assert.equal(await page.getByLabel(/Seleccionar contraparte/i).count(), 1);
  assert.equal(await page.getByLabel(/clave|stable|ruta/i).count(), 0);
  await page.getByLabel(/Motivo .*obligatorio/i).fill("Verified duplicate counterpart");
  await page.getByRole("button", { name: /Confirmar decisi.n/i }).click();
  await page.getByText(/decisi.n fue aplicada correctamente/i).waitFor();
  const linkBody = api.commandRequests.find(({ operation }) => operation === "apply").body;
  assert.deepEqual(linkBody.payload.counterpart_ref, { target_type: "person_roles", target_id: 802 });
  assert.equal(Object.hasOwn(linkBody.payload, "counterpart_stable_target_key"), false);

  api = initialApiState();
  await newPage();
  await gotoDetail();
  await page.getByRole("button", { name: /Descartar/i }).click();
  const discardDialog = page.getByRole("dialog", { name: /Confirmar descarte/i });
  assert.equal(await discardDialog.getByRole("button", { name: /Descartar caso/i }).isDisabled(), true);
  await discardDialog.getByLabel(/Motivo obligatorio/i).fill("Outside the scientific corpus");
  await discardDialog.getByRole("button", { name: /Descartar caso/i }).click();
  await assert.doesNotReject(async () => {
    for (let attempt = 0; attempt < 50 && !api.commandRequests.some(({ operation }) => operation === "discard"); attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
  });
  assert.equal(api.commandRequests.filter(({ operation }) => operation === "discard").length, 1);

  api.detail = caseDetail({ version: 2, case_status: "resolved", scientific_status: "validated", current_decision_id: "decision-current" });
  await newPage();
  await gotoDetail();
  await page.getByRole("button", { name: /Revertir/i }).click();
  const revertDialog = page.getByRole("dialog", { name: /Confirmar reversi.n/i });
  await revertDialog.getByLabel(/Motivo obligatorio/i).fill("Restore the previous projection");
  await revertDialog.getByRole("button", { name: /Revertir decisi.n/i }).click();
  for (let attempt = 0; attempt < 50 && !api.commandRequests.some(({ operation }) => operation === "revert"); attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  await page.getByText("Reabierto", { exact: true }).first().waitFor();
  const revertBody = api.commandRequests.find(({ operation }) => operation === "revert").body;
  assert.equal(revertBody.expected_version, 2);
  assert.equal(revertBody.decision_id_to_revert, "decision-current");
  assert.equal(api.detail.case_status, "reopened");
});

test("hydrated evidence workspace disclosure toggles without reloading or trapping focus", { timeout: 60_000 }, async () => {
  await newPage({ width: 390, height: 844 });
  const initialEvidence = page.waitForResponse((response) => new URL(response.url()).pathname.endsWith("/human-review/cases/fixture-case/evidence"));
  const fixtureResponse = await page.goto(`${baseUrl}${disclosureFixtureRoute}`);
  assert.equal(fixtureResponse?.ok(), true, await fixtureResponse?.text());
  await page.getByTestId("active-evidence-fixture").waitFor();
  await initialEvidence;
  const disclosure = page.locator("details");
  const summary = page.locator("summary");
  const identity = page.getByTestId("active-evidence-fixture");
  await summary.waitFor();
  assert.equal(await disclosure.evaluate((element) => element.open), false);
  assert.equal(await identity.textContent(), "fixture-case|fixture-public.pdf|Fixture public section");
  const requestsBeforeToggle = api.evidenceRequests.length;
  assert.ok(requestsBeforeToggle >= 1, "hydrated workspace made its initial evidence request");

  await summary.click();
  assert.equal(await disclosure.evaluate((element) => element.open), true);
  await page.getByText("fixture-public.pdf", { exact: true }).waitFor();
  assert.equal(await identity.textContent(), "fixture-case|fixture-public.pdf|Fixture public section");
  assert.equal(await disclosure.evaluate((element) => Array.from(element.querySelectorAll("*"))
    .some((child) => ["auto", "scroll"].includes(getComputedStyle(child).overflowY))), false);

  await summary.click();
  assert.equal(await disclosure.evaluate((element) => element.open), false);
  await summary.focus();
  await page.keyboard.press("Tab");
  assert.equal(await page.evaluate(() => document.activeElement?.id), "fixture-outside");
  assert.equal(await identity.textContent(), "fixture-case|fixture-public.pdf|Fixture public section");
  assert.equal(api.evidenceRequests.length, requestsBeforeToggle, "native toggles must not trigger an evidence reload");
});

test("person correction keeps the edited canonical name when switching actions", { timeout: 75_000 }, async () => {
  api.detail = caseDetail({
    case_type: "person_identity",
    target_table: "person_roles",
    target_pk: 919,
    field_path: "canonical_name",
    detected_value: "JUAN PEREZ",
    normalized_value: "JUAN PEREZ",
    canonical_value: "JUAN PEREZ"
  });
  await gotoDetail();

  await page.getByRole("button", { name: /Aprobar/i }).click();
  const nameInput = page.getByLabel(/Nombre can.nico/i);
  assert.equal(await nameInput.inputValue(), "JUAN PEREZ");
  await nameInput.fill("JUAN CARLOS PEREZ");
  assert.equal(await nameInput.inputValue(), "JUAN CARLOS PEREZ");

  await page.getByRole("button", { name: /Corregir/i }).click();
  assert.equal(await nameInput.inputValue(), "JUAN CARLOS PEREZ");
  assert.ok(await page.getByText("JUAN CARLOS PEREZ", { exact: true }).count() >= 1);
  assert.equal(api.commandRequests.length, 0, "switching action and preview must remain read-only");

  await page.getByRole("button", { name: /^Cancelar$/i }).click();
  assert.equal(await page.getByLabel(/Nombre can.nico/i).count(), 0);
  await page.getByRole("button", { name: /Corregir/i }).click();
  const restoredInput = page.getByLabel(/Nombre can.nico/i);
  assert.equal(await restoredInput.inputValue(), "JUAN PEREZ", "explicit cancellation restores the baseline on the next draft");
  await restoredInput.fill("JUAN CARLOS PEREZ");
  await page.getByLabel(/Motivo .*obligatorio/i).fill("Nombre verificado en evidencia p\u00fablica");
  await page.getByRole("button", { name: /Confirmar decisi.n/i }).click();
  await page.getByText(/decisi.n fue aplicada correctamente/i).waitFor();

  assert.equal(api.commandRequests.length, 1);
  const request = api.commandRequests[0];
  assert.equal(request.operation, "apply");
  assert.equal(request.body.action, "correct");
  assert.equal(request.body.payload.case_type, "person_identity");
  assert.equal(request.body.payload.canonical_identity_key, null);
  assert.equal(request.body.payload.canonical_name, "JUAN CARLOS PEREZ");
  assert.equal(api.detail.canonical_value, "JUAN CARLOS PEREZ");
  assert.equal(api.detail.case_status, "resolved");
  await page.getByText("JUAN CARLOS PEREZ", { exact: true }).first().waitFor();

  await page.reload({ waitUntil: "networkidle" });
  await page.getByRole("heading", { name: /Detalle del caso/i }).waitFor();
  await page.getByText("JUAN CARLOS PEREZ", { exact: true }).first().waitFor();
  assert.deepEqual(api.consoleErrors, [], JSON.stringify(api.failedResponses));
  assert.deepEqual(api.pageErrors, []);
});

test("person correction preserves its local name through a 409 until explicit reload", { timeout: 75_000 }, async () => {
  api.detail = caseDetail({
    case_type: "person_identity",
    target_table: "person_roles",
    target_pk: 920,
    field_path: "canonical_name",
    detected_value: "JUAN PEREZ",
    normalized_value: "JUAN PEREZ",
    canonical_value: "JUAN PEREZ"
  });
  api.conflictOnce = true;
  await gotoDetail();
  await page.getByRole("button", { name: /Aprobar/i }).click();
  const nameInput = page.getByLabel(/Nombre can.nico/i);
  await nameInput.fill("JUAN CARLOS PEREZ");
  await page.getByRole("button", { name: /Corregir/i }).click();
  await page.getByLabel(/Motivo .*obligatorio/i).fill("Nombre verificado en evidencia p\u00fablica");
  await page.getByRole("button", { name: /Confirmar decisi.n/i }).click();

  const dialog = page.getByRole("dialog", { name: /caso fue actualizado/i });
  await dialog.waitFor();
  assert.equal(await nameInput.inputValue(), "JUAN CARLOS PEREZ");
  assert.equal(api.commandRequests.length, 1);
  assert.equal(api.commandRequests[0].body.payload.canonical_identity_key, null);
  assert.equal(api.commandRequests[0].body.payload.canonical_name, "JUAN CARLOS PEREZ");

  api.detail = caseDetail({ ...api.detail, version: 2, current_decision_id: "decision-other", canonical_value: "JUAN PEREZ ACTUALIZADO" });
  await dialog.getByRole("button", { name: /Recargar caso/i }).click();
  await page.getByText(/Informaci.n actualizada/i).waitFor();
  assert.equal(await nameInput.inputValue(), "JUAN CARLOS PEREZ", "compatible explicit reload keeps the user's draft for manual review");
  assert.equal(api.commandRequests.length, 1, "reload must not retry the command");
});

test("optimistic conflict never retries, preserves CAS through failed reload and requires a fresh manual confirmation", { timeout: 75_000 }, async () => {
  api.conflictOnce = true;
  await gotoDetail();
  await startProductAction("Corregir", "Conflict-safe title");
  await page.getByRole("button", { name: /Confirmar decisi.n/i }).click();
  const dialog = page.getByRole("dialog", { name: /caso fue actualizado/i });
  await dialog.waitFor();
  assert.equal(api.commandRequests.length, 1);
  assert.match(await dialog.innerText(), /support-conflict-409/i);

  api.reloadFails = true;
  await dialog.getByRole("button", { name: /Recargar caso/i }).click();
  await dialog.getByRole("alert").waitFor();
  assert.match(await dialog.innerText(), /support-reload-503/i);
  assert.equal(api.commandRequests.length, 1, "failed reload must not retry the command");
  api.reloadFails = false;
  api.detail = caseDetail({ version: 2, current_decision_id: "decision-other", canonical_value: "Concurrent title" });
  await dialog.getByRole("button", { name: /Recargar caso/i }).click();
  await page.getByText(/Informaci.n actualizada/i).waitFor();
  assert.equal(api.commandRequests.length, 1);
  await page.getByRole("button", { name: /Confirmar decisi.n/i }).click();
  await page.getByText(/decisi.n fue aplicada correctamente/i).waitFor();
  assert.equal(api.commandRequests.length, 2);
  assert.equal(api.commandRequests[1].body.expected_version, 2);
  assert.equal(api.commandRequests[1].body.expected_current_decision_id, "decision-other");
});

test("capabilities deny by default, safe failures stay sanitized, and desktop/mobile layouts have no page overflow", { timeout: 75_000 }, async () => {
  api.capability = { capability: "SYSTEM_ADMIN", actions: ["view_foundations", "view_audit", "manage_technical_access"] };
  await gotoDetail();
  assert.equal(await page.getByRole("button", { name: /Aprobar|Corregir|Descartar|Revertir/i }).count(), 0);
  await page.getByText(/Historial|Auditor/i).first().waitFor();

  api.capability = { capability: null, actions: [] };
  await newPage();
  await page.goto(`${baseUrl}/human-review/cases/${api.detail.id}`);
  await page.getByText(/No tienes acceso/i).waitFor();
  assert.equal(api.commandRequests.length, 0);

  api = initialApiState();
  api.detailMode = "503";
  await newPage();
  await page.goto(`${baseUrl}/human-review/cases/${api.detail.id}`);
  await page.getByRole("alert").filter({ hasText: /task14-503/i }).waitFor();
  const safeBody = await page.locator("body").innerText();
  assert.match(safeBody, /task14-503/i);
  assert.doesNotMatch(safeBody, forbidden);

  api = initialApiState();
  api.evidenceMode = "503";
  await newPage({ width: 1440, height: 1000 });
  await gotoDetail();
  await page.getByRole("button", { name: /Cargar evidencia/i }).click();
  await page.getByText(/evidencia no est. disponible temporalmente/i).waitFor();
  const desktopOverflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  assert.equal(desktopOverflow, false);
  const desktopPath = path.join(reportRoot, "detail-desktop.png");
  await page.screenshot({ path: desktopPath, fullPage: true });
  assert.ok((await stat(desktopPath)).size > 10_000);

  await newPage({ width: 390, height: 844 });
  await gotoDetail();
  const mobileOverflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  assert.equal(mobileOverflow, false);
  const mobilePath = path.join(reportRoot, "detail-mobile.png");
  await page.screenshot({ path: mobilePath, fullPage: true });
  assert.ok((await stat(mobilePath)).size > 10_000);
  const body = await page.locator("body").innerText();
  assert.doesNotMatch(body, forbidden);

});

test("persisted profile hydrates from neutral SSR markup without React mismatch", { timeout: 75_000 }, async () => {
  const detailUrl = `${baseUrl}/human-review/cases/${api.detail.id}`;
  const serverResponse = await fetch(detailUrl);
  assert.equal(serverResponse.ok, true);
  const serverHtml = await serverResponse.text();
  assert.match(serverHtml, /Usuario institucional/);
  assert.doesNotMatch(serverHtml, /Task 14 Manager/);

  const persistedErrors = [];
  page.on("pageerror", (error) => persistedErrors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error" && /hydration|did not match|server html|text content/i.test(message.text())) {
      persistedErrors.push(`console: ${message.text()}`);
    }
  });
  await gotoDetail();
  await page.getByText("Task 14 Manager", { exact: true }).waitFor();
  await page.waitForLoadState("networkidle");
  assert.deepEqual(persistedErrors, []);

  await newPage({ width: 1440, height: 1000 }, null);
  const neutralErrors = [];
  page.on("pageerror", (error) => neutralErrors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error" && /hydration|did not match|server html|text content/i.test(message.text())) {
      neutralErrors.push(`console: ${message.text()}`);
    }
  });
  await gotoDetail();
  await page.getByText("Usuario institucional", { exact: true }).waitFor();
  await page.waitForLoadState("networkidle");
  assert.deepEqual(neutralErrors, []);
});

test("persisted role hydrates AppShell dashboard filters from neutral SSR markup", { timeout: 75_000 }, async () => {
  const dashboardUrl = `${baseUrl}/dashboard`;
  const serverResponse = await fetch(dashboardUrl);
  assert.equal(serverResponse.ok, true);
  const serverHtml = await serverResponse.text();
  assert.doesNotMatch(serverHtml, /aria-label="Carrera"/);
  assert.doesNotMatch(serverHtml, /Task 14 Manager/);

  const persistedErrors = [];
  page.on("pageerror", (error) => persistedErrors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error" && /hydration|did not match|server html|text content/i.test(message.text())) {
      persistedErrors.push(`console: ${message.text()}`);
    }
  });
  await page.goto(dashboardUrl);
  await page.getByLabel("Carrera").waitFor();
  await page.getByText("Task 14 Manager", { exact: true }).waitFor();
  await page.waitForLoadState("networkidle");
  assert.deepEqual(persistedErrors, []);

  await newPage({ width: 390, height: 844 }, null);
  const neutralErrors = [];
  page.on("pageerror", (error) => neutralErrors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error" && /hydration|did not match|server html|text content/i.test(message.text())) {
      neutralErrors.push(`console: ${message.text()}`);
    }
  });
  await page.goto(dashboardUrl);
  await page.getByText("Usuario institucional", { exact: true }).waitFor({ state: "attached" });
  await page.waitForLoadState("networkidle");
  assert.equal(await page.getByLabel("Carrera").count(), 0);
  assert.deepEqual(neutralErrors, []);
});
