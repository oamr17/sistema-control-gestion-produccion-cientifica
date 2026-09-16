import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:net";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const nextBin = path.join(frontendRoot, "node_modules", "next", "dist", "bin", "next");

async function freePort() {
  return await new Promise((resolve, reject) => {
    const listener = createServer();
    listener.once("error", reject);
    listener.listen(0, "127.0.0.1", () => {
      const address = listener.address();
      const port = typeof address === "object" && address ? address.port : null;
      listener.close((error) => error ? reject(error) : resolve(port));
    });
  });
}

async function waitForServer(url) {
  const deadline = Date.now() + 45_000;
  while (Date.now() < deadline) {
    try {
      if ((await fetch(url)).status < 500) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("Next.js did not start for the login page test");
}

test("login keeps its form centered without the editorial image", { timeout: 60_000 }, async () => {
  const port = await freePort();
  const baseUrl = `http://127.0.0.1:${port}`;
  const server = spawn(process.execPath, [nextBin, "dev", "--hostname", "127.0.0.1", "--port", String(port)], {
    cwd: frontendRoot,
    env: { ...process.env, NEXT_TELEMETRY_DISABLED: "1" },
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true
  });
  const logs = [];
  server.stdout.on("data", (chunk) => logs.push(chunk.toString()));
  server.stderr.on("data", (chunk) => logs.push(chunk.toString()));
  const browser = await chromium.launch({ headless: true });
  try {
    await waitForServer(baseUrl);
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    await page.goto(baseUrl, { waitUntil: "domcontentloaded" });

    await page.locator('input[type="email"]').waitFor({ timeout: 45_000 }).catch(async (error) => {
      throw new Error(`${error.message}\n${logs.join("")}\n${(await page.locator("body").innerText()).slice(0, 500)}`);
    });
    await page.locator('input[type="password"]').waitFor();
    const renderedImageUrls = await page.locator("img").evaluateAll((images) =>
      images.map((image) => `${image.currentSrc}|${image.getAttribute("src") ?? ""}`)
    );
    assert.equal(renderedImageUrls.some((url) => url.includes("images.unsplash.com")), false);

    const formBox = await page.locator("form").boundingBox();
    assert.ok(formBox);
    const formCenter = formBox.x + formBox.width / 2;
    assert.ok(Math.abs(formCenter - 640) < 80, `Expected centered login form, got x=${formCenter}`);

    const logoBox = await page.getByRole("img", { name: /Universidad de Guayaquil/i }).boundingBox();
    assert.ok(logoBox);
    const logoCenter = logoBox.x + logoBox.width / 2;
    assert.ok(Math.abs(logoCenter - formCenter) < 4, `Expected centered logo, got x=${logoCenter}`);

    const bodyText = await page.locator("body").innerText();
    assert.doesNotMatch(bodyText, /Ingreso institucional/i);
    assert.doesNotMatch(bodyText, /Gesti[oó]n de KPI, evidencias, POA y reportes por carrera/i);
    assert.equal(await page.title(), "Control científico — FCA");
  } finally {
    await browser.close();
    if (!server.killed) {
      const exited = new Promise((resolve) => server.once("exit", resolve));
      server.kill();
      await Promise.race([exited, new Promise((resolve) => setTimeout(resolve, 5_000))]);
    }
  }
});
