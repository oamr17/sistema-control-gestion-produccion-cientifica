import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { chromium } from "playwright";

const baseUrl = process.env.B2A_FRONTEND_URL ?? "http://localhost:3000";
const outputDir = path.resolve("../backend/reports/canonical_identity_b2a1_20260713/visual");
await mkdir(outputDir, { recursive: true });

const results = [];
const browser = await chromium.launch({ headless: true });

async function assertNoOverflow(page, viewportName, pageName, overflows) {
  const overflow = await page.evaluate(() => ({
    body: document.body.scrollWidth - window.innerWidth,
    html: document.documentElement.scrollWidth - window.innerWidth
  }));
  assert.ok(
    overflow.body <= 1 && overflow.html <= 1,
    `horizontal overflow at ${viewportName}/${pageName}: ${JSON.stringify(overflow)}`
  );
  overflows[pageName] = overflow;
}

async function verifyViewport(name, viewport) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  const consoleErrors = [];
  const pageConsoleErrors = {};
  const overflows = {};
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  await page.goto(baseUrl, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "Entrar" }).click();
  await page.waitForURL("**/dashboard");

  await page.goto(`${baseUrl}/teachers`, { waitUntil: "networkidle" });
  consoleErrors.length = 0;
  await page.getByRole("heading", { name: "Personas únicas" }).waitFor();
  const anibalRow = page.getByRole("row").filter({ hasText: "Anibal Quintanilla Bonilla" }).first();
  await anibalRow.getByRole("button").click();
  await page.getByRole("heading", { name: "Posibles coincidencias informativas" }).waitFor();
  assert.ok(await anibalRow.getByText("Posible coincidencia con otro registro").count() >= 1);
  await assertNoOverflow(page, name, "participants", overflows);
  await page.screenshot({ path: path.join(outputDir, `${name}-participants-possible-match.png`), fullPage: true });
  await page.waitForTimeout(300);
  pageConsoleErrors.participants = [...consoleErrors];
  assert.deepEqual(pageConsoleErrors.participants, []);

  await page.goto(`${baseUrl}/projects`, { waitUntil: "networkidle" });
  consoleErrors.length = 0;
  await page.getByRole("heading", { name: "Proyectos" }).waitFor();
  assert.ok(await page.getByText("Identidad validada / relación como director pendiente", { exact: true }).count() >= 3);
  const zambranoProject = page.getByRole("row").filter({ hasText: "VARIABLES EXPLICATIVAS" }).first();
  await zambranoProject.getByRole("button").click();
  await page.getByText("Estado de la identidad: validated").waitFor();
  await page.getByText("Estado de la relación como director: pending_review").waitFor();
  await assertNoOverflow(page, name, "projects", overflows);
  await page.screenshot({ path: path.join(outputDir, `${name}-projects-director-states.png`), fullPage: true });
  await page.waitForTimeout(300);
  pageConsoleErrors.projects = [...consoleErrors];
  assert.deepEqual(pageConsoleErrors.projects, []);

  await page.goto(`${baseUrl}/dashboard`, { waitUntil: "networkidle" });
  consoleErrors.length = 0;
  await page.getByText("Investigadores externos validados por universidad").waitFor();
  await page.getByText(/1 validado de 5 detectados;\s*4 pendientes/).waitFor();
  await assertNoOverflow(page, name, "dashboard", overflows);
  await page.screenshot({ path: path.join(outputDir, `${name}-dashboard-validated-externals.png`), fullPage: true });
  await page.waitForTimeout(300);
  pageConsoleErrors.dashboard = [...consoleErrors];
  assert.deepEqual(pageConsoleErrors.dashboard, []);

  results.push({ name, viewport, overflows, pageConsoleErrors });
  await context.close();
}

try {
  await verifyViewport("desktop-1440x900", { width: 1440, height: 900 });
  await verifyViewport("mobile-390x844", { width: 390, height: 844 });
} finally {
  await browser.close();
}

await writeFile(path.join(outputDir, "visual-results.json"), JSON.stringify(results, null, 2));
console.log(JSON.stringify(results, null, 2));
