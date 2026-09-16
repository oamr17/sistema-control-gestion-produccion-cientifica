import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("projects screen consumes the effective project reader and renders its participants", async () => {
  const [page, api] = await Promise.all([
    readFile(new URL("../app/projects/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../lib/api.ts", import.meta.url), "utf8")
  ]);

  assert.match(api, /projects:\s*\(query = ""\).*`\/projects\$\{query\}`/s);
  assert.match(page, /api\.projects\(query\)/);
  assert.match(page, /project\.teachers/);
  assert.match(page, /teacher\.teacher_name/);
  const legacyProjectsPage = page.slice(
    page.indexOf("function LegacyProjectsPage"),
    page.indexOf("export default function ProjectsPage")
  );
  assert.match(legacyProjectsPage, /api\.projects\(query\)/);
  assert.doesNotMatch(legacyProjectsPage, /api\.researchEntities/);
});
