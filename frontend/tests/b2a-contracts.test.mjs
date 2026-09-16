import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test, { before } from "node:test";

const API_URL = process.env.B2A_TEST_API_URL ?? "http://localhost:8000/api/v1";
let headers;

before(async () => {
  const response = await fetch(`${API_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: "admin@university.edu", password: "Admin123*" })
  });
  assert.equal(response.status, 200);
  const login = await response.json();
  headers = { Authorization: `Bearer ${login.access_token}` };
});

async function get(path) {
  const response = await fetch(`${API_URL}${path}`, { headers });
  assert.equal(response.status, 200, `${path} returned ${response.status}`);
  return response.json();
}

test("participants contract exposes the deterministic demo identities and explicit authorships", async () => {
  const participants = await get("/participants");
  // Protect the current academic bootstrap: only the two scoped demo identities are projected.
  assert.equal(participants.length, 2);
  assert.equal(new Set(participants.map((item) => item.canonical_identity_key)).size, participants.length);

  const byName = new Map(participants.map((item) => [item.canonical_name, item]));
  assert.deepEqual([...byName.keys()].sort(), ["Ana Torres", "Carlos Vera"]);
  for (const canonicalName of ["Ana Torres", "Carlos Vera"]) {
    const participant = byName.get(canonicalName);
    assert.equal(participant.authorship_count, 1);
    assert.equal(participant.overall_status, "pending_review");
    assert.ok(participant.products.every((item) => Number.isInteger(item.id)));
    assert.deepEqual(
      new Set(participant.appearances.map((item) => item.role_type)),
      new Set(["author", "autor_producto"])
    );
    assert.ok(participant.appearances.every((item) => item.source_file === "human_review_scope_demo.pdf"));
  }
});

test("production contract exposes all filters without changing KPI", async () => {
  const [all, eligible, pending, discarded] = await Promise.all([
    get("/production?visibility=all"),
    get("/production?visibility=eligible"),
    get("/production?visibility=pending"),
    get("/production?visibility=discarded")
  ]);
  // Protect the five seeded demo outputs and their fail-closed KPI state before human review.
  assert.equal(all.length, 5);
  assert.equal(eligible.length, 0);
  assert.equal(pending.length, 5);
  assert.equal(discarded.length, 0);
  assert.deepEqual(
    new Set(all.map((item) => item.title)),
    new Set([
      "Capacidades de innovacion en universidades publicas",
      "Transformacion digital en pequenas y medianas empresas",
      "Conferencia de analitica para auditoria",
      "Comportamiento del consumidor y analisis de datos",
      "Linea base del ciclo anterior"
    ])
  );
  assert.ok(all.every((item) => Array.isArray(item.authors)));
  assert.ok(pending.every((item) => item.kpi_eligible === false));
});

test("dashboard and projects expose current demo KPI and effective identity projections", async () => {
  const [dashboard, projects] = await Promise.all([
    get("/dashboard/summary?year=2025-2026&cycle=2"),
    get("/projects")
  ]);
  // Protect fail-closed KPI semantics while the seeded Human Review cases remain pending.
  assert.equal(dashboard.scientific_output_detected, 4);
  assert.equal(dashboard.scientific_output_kpi_eligible, 0);
  assert.equal(dashboard.scientific_output_pending_review, 4);
  assert.equal(dashboard.canonical_identities_count, 2);
  assert.equal(dashboard.authorships_count, 2);

  // Protect the supported Projects reader and its effective participant projection.
  assert.equal(projects.length, 1);
  assert.equal(projects[0].name, "Observatorio de productividad e innovacion FCA");
  assert.equal(projects[0].status, "vigente");
  assert.deepEqual(
    projects[0].teachers.map((teacher) => [teacher.teacher_name, teacher.role]),
    [["Ana Torres", "DIRECTOR"], ["Carlos Vera", "RESEARCHER"], ["Sofia Paz", "RESEARCHER"]]
  );
});

test("screens consume canonical endpoints and expose B2A controls", async () => {
  const [participantsPage, productionPage, projectsPage, dashboardPage] = await Promise.all([
    readFile(new URL("../app/teachers/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/production/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/projects/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/dashboard/page.tsx", import.meta.url), "utf8")
  ]);
  assert.match(participantsPage, /api\.participants/);
  assert.match(participantsPage, /canonical_identity_key/);
  assert.doesNotMatch(participantsPage.slice(participantsPage.indexOf("function CanonicalParticipantsPage")), /buildPdfTeacherRows/);
  for (const label of ["Todos", "Elegibles", "Pendientes", "Descartados"]) assert.match(productionPage, new RegExp(label));
  // Protect the current effective Projects surface rather than the removed research-entity field.
  assert.match(projectsPage, /api\.projects\(query\)/);
  assert.match(projectsPage, /project\.teachers/);
  assert.doesNotMatch(projectsPage, /director_validation_status/);
  assert.match(dashboardPage, /Participaciones por carrera/);
  assert.match(dashboardPage, /Investigadores externos validados por universidad/);
  assert.match(dashboardPage, /external_researchers_kpi_eligible/);
  assert.match(dashboardPage, /external_researchers_detected/);
  assert.match(dashboardPage, /external_researchers_pending_review/);
});
