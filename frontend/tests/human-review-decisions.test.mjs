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

async function compileTask12Modules() {
  compiledRoot = await mkdtemp(path.join(tmpdir(), "b2b2-task12-runtime-"));
  const sources = [
    "lib/api.ts",
    "lib/types.ts",
    "lib/human-review.ts",
    "lib/human-review-api.ts",
    "lib/data-cache.tsx",
    "hooks/useHumanReview.ts",
    "components/human-review/DecisionComposer.tsx",
    "components/human-review/DecisionPreview.tsx",
    "components/human-review/IdentityLinker.tsx",
    "components/human-review/DiscardConfirmDialog.tsx",
    "components/human-review/RevertConfirmDialog.tsx",
    "components/human-review/GuidedDecisionEditor.tsx",
    "components/human-review/RelatedReviewList.tsx",
    "components/human-review/ReviewResultSummary.tsx",
    "components/human-review/SessionConfirmationBar.tsx"
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
      types: ["node"],
      typeRoots: [path.join(frontendRoot, "node_modules", "@types")],
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
  return {
    React: require("react"),
    renderToStaticMarkup: require("react-dom/server").renderToStaticMarkup,
    api: require(path.join(compiledRoot, "lib", "api.js")),
    contract: require(path.join(compiledRoot, "lib", "human-review.js")),
    client: require(path.join(compiledRoot, "lib", "human-review-api.js")),
    cache: require(path.join(compiledRoot, "lib", "data-cache.js")),
    hooks: require(path.join(compiledRoot, "hooks", "useHumanReview.js")),
    composer: require(path.join(compiledRoot, "components", "human-review", "DecisionComposer.js")),
    preview: require(path.join(compiledRoot, "components", "human-review", "DecisionPreview.js")),
    linker: require(path.join(compiledRoot, "components", "human-review", "IdentityLinker.js")),
    discard: require(path.join(compiledRoot, "components", "human-review", "DiscardConfirmDialog.js")),
    revert: require(path.join(compiledRoot, "components", "human-review", "RevertConfirmDialog.js")),
    guided: require(path.join(compiledRoot, "components", "human-review", "GuidedDecisionEditor.js")),
    confirmation: require(path.join(compiledRoot, "components", "human-review", "SessionConfirmationBar.js"))
  };
}

function loadModules() {
  modulesPromise ??= compileTask12Modules();
  return modulesPromise;
}

function detail(overrides = {}) {
  return {
    id: "00000000-0000-0000-0000-000000000111",
    case_type: "person_identity",
    case_status: "pending",
    scientific_status: "pending",
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
    normalized_value: "Ada Lovelace",
    canonical_value: "Ada Lovelace",
    current_decision_id: null,
    overrides: [],
    effective_memberships: ["Investigadora"],
    counterpart_options: [],
    evidence_summary: { available: true, document_name: "informe-publico.pdf" },
    ...overrides
  };
}

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function successfulResponse(nextDetail = detail({ case_status: "resolved", scientific_status: "validated", version: 4 })) {
  return {
    case: nextDetail,
    decision_id: "00000000-0000-0000-0000-000000000222",
    kpi_effect: { affected: [] },
    correlation_id: "00000000-0000-0000-0000-000000000333"
  };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((nextResolve, nextReject) => {
    resolve = nextResolve;
    reject = nextReject;
  });
  return { promise, resolve, reject };
}

function relatedItem(caseId, overrides = {}) {
  return {
    case_id: caseId,
    case_type: "person_identity",
    case_status: "pending",
    scientific_status: "pending",
    version: 3,
    current_decision_id: null,
    detected_value: "Ada Lovelace",
    normalized_value: "Ada Lovelace",
    canonical_value: "Ada Lovelace",
    possible_kpi_impact: true,
    allowed_actions: ["approve", "correct", "link"],
    evidence_summary: { available: true },
    ...overrides
  };
}

function validDraft(contract, source, action = "correct") {
  const initial = contract.createDecisionDraft(source, action, `correlation-${source.id}`);
  const payload = initial.payload.case_type === "person_identity"
    ? { ...initial.payload, canonical_identity_key: `identity:${source.id}`, canonical_name: `Nombre ${source.id}` }
    : initial.payload;
  return { ...contract.withDecisionPayload(initial, payload), reason: `Validación de ${source.id}` };
}

afterEach(() => {
  globalThis.fetch = originalFetch;
});

after(async () => {
  if (compiledRoot) await rm(compiledRoot, { recursive: true, force: true });
});

test("local edit, preview and cancel never fetch, persist or mutate CaseDetail", async () => {
  const { contract } = await loadModules();
  let writes = 0;
  globalThis.fetch = async () => { writes += 1; return jsonResponse({}); };
  const source = detail();
  const original = structuredClone(source);
  const draft = contract.createDecisionDraft(source, "correct", "00000000-0000-0000-0000-000000000401");
  const edited = contract.withDecisionPayload(draft, {
    ...draft.payload,
    canonical_name: "Ada Byron Lovelace",
    scientific_status: "validated"
  });
  const preview = contract.buildDecisionPreview(source, edited);
  const cancelled = contract.cancelDecisionDraft();

  assert.equal(writes, 0);
  assert.deepEqual(source, original);
  assert.equal(edited.payload.canonical_name, "Ada Byron Lovelace");
  assert.equal(preview.proposedValue, "Ada Byron Lovelace");
  assert.equal(cancelled, null);
  for (const forbidden of ["localStorage", "sessionStorage", "indexedDB", "cookie", "url"])
    assert.equal(Object.hasOwn(draft, forbidden), false);
});

function descendants(React, node) {
  if (!node || typeof node !== "object") return [];
  const children = React.Children.toArray(node.props?.children);
  return [node, ...children.flatMap((child) => descendants(React, child))];
}

test("guided editor renders the four semantic prompts in order with specific guidance for all six case types", async () => {
  const { React, renderToStaticMarkup, contract, guided } = await loadModules();
  const expectedGuidance = new Map([
    ["person_identity", /misma persona|nombre/i],
    ["author_identity", /autor|autoría/i],
    ["product", /título|producto/i],
    ["project_director_relation", /director|relación/i],
    ["external_identity", /institución|extern/i],
    ["possible_duplicate", /duplicad|separad|fusion/i]
  ]);
  for (const [caseType, guidancePattern] of expectedGuidance) {
    const source = detail({
      case_type: caseType,
      field_path: caseType === "product" ? "product_title" : "canonical_name",
      counterpart_options: caseType === "possible_duplicate"
        ? [{ counterpart_ref: { target_type: "person_roles", target_id: 22 }, display_name: "Ada alternativa" }]
        : []
    });
    const action = caseType === "possible_duplicate" ? "link" : "correct";
    const draft = contract.createDecisionDraft(source, action, `correlation-${caseType}`);
    const markup = renderToStaticMarkup(React.createElement(guided.GuidedDecisionEditor, {
      detail: source,
      draft,
      disabled: false,
      previewOpen: false,
      validationMessages: [],
      onChange: () => undefined,
      onPreviewOpenChange: () => undefined
    }));
    const headings = [
      "Qué detectó el sistema",
      "Qué debes verificar",
      "Qué dato quedará registrado",
      "Por qué se realiza el cambio"
    ];
    let cursor = -1;
    for (const heading of headings) {
      const next = markup.indexOf(heading);
      assert.ok(next > cursor, `${caseType}: ${heading} must preserve semantic order`);
      cursor = next;
    }
    assert.match(markup, guidancePattern);
    assert.match(markup, /<section/);
  }
});

test("reusable payload fields preserve edited canonical data when Corregir is selected", async () => {
  const { React, contract, composer } = await loadModules();
  const source = detail({ canonical_value: "Nombre anterior" });
  let draft = contract.createDecisionDraft(source, "approve", "field-correlation");
  draft = contract.withDecisionPayload(draft, {
    ...draft.payload,
    canonical_identity_key: "identity:edited",
    canonical_name: "Nombre editado",
    aliases: ["Alias editado"]
  });
  const corrected = contract.withDecisionAction(source, draft, "correct");
  const changes = [];
  const tree = composer.DecisionPayloadFields({ detail: source, draft: corrected, disabled: false, onChange: (payload) => changes.push(payload) });
  const nameInput = descendants(React, tree).find((node) => node.type === "input" && node.props.value === "Nombre editado");
  assert.ok(nameInput);
  nameInput.props.onChange({ target: { value: "Nombre editado final" } });
  assert.equal(changes[0].canonical_identity_key, "identity:edited");
  assert.equal(changes[0].canonical_name, "Nombre editado final");
  assert.deepEqual(changes[0].aliases, ["Alias editado"]);
});

test("optional aliases preserve spaces while required person fields are identified", async () => {
  const { React, renderToStaticMarkup, contract, composer, guided } = await loadModules();
  const source = detail();
  const initial = contract.createDecisionDraft(source, "correct", "alias-space-correlation");
  const draft = {
    ...contract.withDecisionPayload(initial, {
      ...initial.payload,
      canonical_identity_key: "identity:ada-lovelace",
      canonical_name: "Ada Lovelace",
      aliases: []
    }),
    reason: "Se validó la identidad con el documento disponible."
  };
  const changes = [];
  const tree = composer.DecisionPayloadFields({ detail: source, draft, disabled: false, onChange: (payload) => changes.push(payload) });
  const aliasInput = descendants(React, tree).find((node) => node.type === "input" && node.props.value === "");
  assert.ok(aliasInput);
  aliasInput.props.onChange({ target: { value: "Ana María, María José" } });
  assert.deepEqual(changes[0].aliases, ["Ana María", " María José"]);

  const aliasOptional = { ...draft, payload: { ...draft.payload, aliases: [] } };
  assert.deepEqual(contract.validateSessionDraft(source, aliasOptional), []);
  const markup = renderToStaticMarkup(React.createElement(guided.GuidedDecisionEditor, {
    detail: source,
    draft,
    disabled: false,
    previewOpen: false,
    validationMessages: [],
    onChange: () => undefined,
    onPreviewOpenChange: () => undefined
  }));
  const aliasLabel = markup.match(/<label[^>]*>Alias, separados por coma[\s\S]*?<\/label>/)?.[0] ?? "";
  assert.match(markup, /Nombre canónico[\s\S]*?\*/);
  assert.doesNotMatch(aliasLabel, /\*/);
  assert.match(markup, />\*<\/span> Campo obligatorio/);
});

test("person approval omits the manual reference and requests server-generated identity", async () => {
  const { React, renderToStaticMarkup, contract, composer } = await loadModules();
  const source = detail();
  const initial = contract.createDecisionDraft(source, "approve", "server-reference-correlation");
  assert.equal(initial.payload.canonical_identity_key, null);
  const draft = {
    ...contract.withDecisionPayload(initial, {
      ...initial.payload,
      canonical_name: "Ada Lovelace",
      aliases: []
    }),
    reason: "La identidad fue validada con la evidencia disponible."
  };
  const request = contract.buildApplyDecisionRequest(source, draft);
  assert.equal(request.payload.canonical_identity_key, null);
  const markup = renderToStaticMarkup(React.createElement(composer.DecisionPayloadFields, {
    detail: source, draft, disabled: false, onChange: () => undefined
  }));
  assert.doesNotMatch(markup, /Referencia can.nica p.blica|Ver identificador p.blico avanzado/i);
  assert.match(markup, /referencia p.blica de identidad se asignar. autom.ticamente al confirmar/i);
});

test("person link omits the manual identity reference and shows automatic assignment", async () => {
  const { React, renderToStaticMarkup, contract, linker } = await loadModules();
  const source = detail({ case_type: "author_identity" });
  const draft = contract.createDecisionDraft(source, "link", "auto-link-reference-correlation");
  assert.equal(draft.payload.canonical_identity_key, null);

  const markup = renderToStaticMarkup(React.createElement(linker.IdentityLinker, {
    payload: draft.payload,
    disabled: false,
    onChange: () => undefined
  }));

  assert.match(markup, /Identificador can.nico p.blico/i);
  assert.match(markup, /Se asignar. autom.ticamente al confirmar/i);
  assert.match(markup, /Nombre can.nico/);
  assert.doesNotMatch(markup, /placeholder="Ej\. identity:persona-publica"/);
  assert.doesNotMatch(markup, /Usa solamente el identificador p.blico autorizado/i);
  assert.equal((markup.match(/<input/g) ?? []).length, 2);
});

test("external identity correction omits the manual reference and requests server-generated identity", async () => {
  const { React, renderToStaticMarkup, contract, composer, linker } = await loadModules();
  const source = detail({
    case_type: "external_identity",
    target_table: "external_researchers",
    field_path: "case",
    detected_value: "Jose Manuel Santos Jaen | Universidad de Murcia",
    normalized_value: "Jose Manuel Santos Jaen",
    canonical_value: "Jose Manuel Santos Jaen"
  });
  const initial = contract.createDecisionDraft(source, "correct", "external-auto-correlation");
  assert.equal(initial.payload.external_identity_key, null);
  const draft = {
    ...contract.withDecisionPayload(initial, {
      ...initial.payload,
      external_institution: "Universidad de Murcia"
    }),
    reason: "Se verificó la institución externa con la evidencia disponible."
  };
  const request = contract.buildApplyDecisionRequest(source, draft);
  assert.equal(request.payload.external_identity_key, null);

  const fieldsMarkup = renderToStaticMarkup(React.createElement(composer.DecisionPayloadFields, {
    detail: source, draft, disabled: false, onChange: () => undefined
  }));
  assert.doesNotMatch(fieldsMarkup, /Referencia can.nica p.blica|Ver identificador p.blico avanzado/i);
  assert.match(fieldsMarkup, /referencia p.blica externa se asignar. autom.ticamente al confirmar/i);

  const linkDraft = contract.createDecisionDraft(source, "link", "external-link-auto-correlation");
  const linkMarkup = renderToStaticMarkup(React.createElement(linker.IdentityLinker, {
    payload: linkDraft.payload,
    disabled: false,
    onChange: () => undefined
  }));
  assert.match(linkMarkup, /Identificador can.nico p.blico/i);
  assert.match(linkMarkup, /Se asignar. autom.ticamente al confirmar/i);
  assert.doesNotMatch(linkMarkup, /placeholder="Ej\. identity:persona-publica"/);
});

test("real payload and reason validation failures describe only their matching guided controls", async () => {
  const { React, renderToStaticMarkup, contract, guided } = await loadModules();
  const source = detail();
  const valid = validDraft(contract, source);
  const invalidNameDraft = {
    ...valid,
    payload: { ...valid.payload, canonical_name: "" }
  };
  const nameMessages = contract.validateSessionDraft(source, invalidNameDraft);
  assert.deepEqual(nameMessages, ["Ingresa un nombre canónico válido de hasta 500 caracteres."]);
  const nameMarkup = renderToStaticMarkup(React.createElement(guided.GuidedDecisionEditor, {
    detail: source,
    draft: invalidNameDraft,
    disabled: false,
    previewOpen: false,
    validationMessages: nameMessages,
    onChange: () => undefined,
    onPreviewOpenChange: () => undefined
  }));
  const nameInput = nameMarkup.match(/<input(?=[^>]*value="")[^>]*>/)?.[0] ?? "";
  const reasonWithNameFailure = nameMarkup.match(/<textarea[^>]*>/)?.[0] ?? "";
  const nameErrorId = nameInput.match(/aria-describedby="([^"]+)"/)?.[1] ?? "";
  assert.match(nameInput, /aria-invalid="true"/);
  assert.ok(nameErrorId);
  assert.match(nameMarkup, new RegExp(`id="${nameErrorId}"[^>]*role="alert"`));
  assert.doesNotMatch(reasonWithNameFailure, /aria-invalid="true"/);
  assert.doesNotMatch(reasonWithNameFailure, new RegExp(nameErrorId));

  const invalidReasonDraft = { ...valid, reason: "" };
  const reasonMessages = contract.validateSessionDraft(source, invalidReasonDraft);
  assert.deepEqual(reasonMessages, ["El motivo es obligatorio para esta decisión."]);
  const reasonMarkup = renderToStaticMarkup(React.createElement(guided.GuidedDecisionEditor, {
    detail: source,
    draft: invalidReasonDraft,
    disabled: false,
    previewOpen: false,
    validationMessages: reasonMessages,
    onChange: () => undefined,
    onPreviewOpenChange: () => undefined
  }));
  const nameWithReasonFailure = reasonMarkup.match(/<input(?=[^>]*value="Nombre )[^>]*>/)?.[0] ?? "";
  const reasonTextarea = reasonMarkup.match(/<textarea[^>]*>/)?.[0] ?? "";
  const reasonDescriptionIds = reasonTextarea.match(/aria-describedby="([^"]+)"/)?.[1].split(" ") ?? [];
  assert.doesNotMatch(nameWithReasonFailure, /aria-invalid="true"/);
  assert.match(reasonTextarea, /aria-invalid="true"/);
  assert.ok(reasonDescriptionIds.length >= 2);
  for (const id of reasonDescriptionIds) assert.match(reasonMarkup, new RegExp(`id="${id}"`));
  const reasonErrorId = reasonDescriptionIds.at(-1);
  assert.match(reasonMarkup, new RegExp(`id="${reasonErrorId}"[^>]*role="alert"`));
  assert.doesNotMatch(nameWithReasonFailure, new RegExp(reasonErrorId));
});

test("a real link payload failure describes only its matching IdentityLinker control", async () => {
  const { React, renderToStaticMarkup, contract, guided } = await loadModules();
  const source = detail();
  const initial = contract.createDecisionDraft(source, "link", "link-field-correlation");
  const draft = {
    ...contract.withDecisionPayload(initial, {
      ...initial.payload,
      canonical_identity_key: "identity:link-person",
      canonical_name: "",
      resolution: "linked"
    }),
    reason: "La evidencia vincula ambas identidades."
  };
  const validationMessages = contract.validateSessionDraft(source, draft);
  assert.deepEqual(validationMessages, ["Ingresa un nombre canónico válido de hasta 500 caracteres."]);

  const markup = renderToStaticMarkup(React.createElement(guided.GuidedDecisionEditor, {
    detail: source,
    draft,
    disabled: false,
    previewOpen: false,
    validationMessages,
    onChange: () => undefined,
    onPreviewOpenChange: () => undefined
  }));
  const referenceInput = markup.match(/<input(?=[^>]*value="identity:link-person")[^>]*>/)?.[0] ?? "";
  const nameInput = markup.match(/<input(?=[^>]*value="")[^>]*>/)?.[0] ?? "";
  const resolutionSelect = markup.match(/<select[^>]*>/)?.[0] ?? "";
  const reasonTextarea = markup.match(/<textarea[^>]*>/)?.[0] ?? "";
  const nameErrorId = nameInput.match(/aria-describedby="([^"]+)"/)?.[1] ?? "";
  assert.match(nameInput, /aria-invalid="true"/);
  assert.ok(nameErrorId);
  const nameInputIndex = markup.indexOf(nameInput);
  const nameErrorIndex = markup.indexOf(`id="${nameErrorId}" role="alert"`);
  assert.ok(nameErrorIndex > nameInputIndex);
  assert.doesNotMatch(markup.slice(nameInputIndex + nameInput.length, nameErrorIndex), /<input|<select|<textarea|<\/label>/);
  assert.match(markup, /Ingresa un nombre canónico válido de hasta 500 caracteres\./);
  for (const unrelated of [referenceInput, resolutionSelect, reasonTextarea]) {
    assert.doesNotMatch(unrelated, /aria-invalid="true"/);
    assert.doesNotMatch(unrelated, new RegExp(nameErrorId));
  }
});

test("two guided editors give each reason control an explicit name and instance-safe relationships", async () => {
  const { React, renderToStaticMarkup, contract, guided } = await loadModules();
  const firstDetail = detail({ id: "case-first" });
  const secondDetail = detail({ id: "case-second" });
  const firstDraft = validDraft(contract, firstDetail);
  const secondDraft = validDraft(contract, secondDetail);
  const markup = renderToStaticMarkup(React.createElement("div", null,
    React.createElement(guided.GuidedDecisionEditor, {
      detail: firstDetail, draft: firstDraft, disabled: false, previewOpen: false,
      validationMessages: [], onChange: () => undefined, onPreviewOpenChange: () => undefined
    }),
    React.createElement(guided.GuidedDecisionEditor, {
      detail: secondDetail, draft: secondDraft, disabled: false, previewOpen: false,
      validationMessages: ["El motivo es obligatorio para esta decisión."],
      onChange: () => undefined, onPreviewOpenChange: () => undefined
    })
  ));
  const textareas = [...markup.matchAll(/<textarea[^>]*>/g)].map((match) => match[0]);
  assert.equal(textareas.length, 2);
  const ids = textareas.map((tag) => tag.match(/ id="([^"]+)"/)?.[1] ?? "");
  const labelledBy = textareas.map((tag) => tag.match(/aria-labelledby="([^"]+)"/)?.[1] ?? "");
  assert.ok(ids.every(Boolean));
  assert.ok(labelledBy.every(Boolean));
  assert.equal(new Set(ids).size, 2);
  assert.equal(new Set(labelledBy).size, 2);
  for (const labelId of labelledBy) assert.match(markup, new RegExp(`id="${labelId}"`));
  for (const tag of textareas) {
    const describedBy = tag.match(/aria-describedby="([^"]+)"/)?.[1].split(" ") ?? [];
    assert.ok(describedBy.length >= 1);
    for (const id of describedBy) assert.match(markup, new RegExp(`id="${id}"`));
  }
});

test("guided preview is controlled, local and preserves the exact draft", async () => {
  const { React, renderToStaticMarkup, contract, guided } = await loadModules();
  let writes = 0;
  globalThis.fetch = async () => { writes += 1; return jsonResponse({}); };
  const source = detail();
  const draft = {
    ...validDraft(contract, source),
    reason: "Razón exacta de validación"
  };
  const original = structuredClone(draft);
  const closedMarkup = renderToStaticMarkup(React.createElement(guided.GuidedDecisionEditor, {
    detail: source, draft, disabled: false, previewOpen: false,
    validationMessages: [],
    onChange: () => undefined, onPreviewOpenChange: () => undefined
  }));
  const openedMarkup = renderToStaticMarkup(React.createElement(guided.GuidedDecisionEditor, {
    detail: source, draft, disabled: false, previewOpen: true,
    validationMessages: [],
    onChange: () => undefined, onPreviewOpenChange: () => undefined
  }));
  assert.equal(writes, 0);
  assert.deepEqual(draft, original);
  assert.match(closedMarkup, /aria-expanded="false"/);
  assert.doesNotMatch(closedMarkup, /Vista previa local/);
  assert.match(openedMarkup, /Vista previa local/);
  assert.match(openedMarkup, /aria-expanded="true"/);
  assert.match(openedMarkup, /<textarea[^>]*aria-describedby="[^"]+-reason-guidance"/);
});

test("session confirmation derives counts once and blocks queued or sending selections", async () => {
  const { React, renderToStaticMarkup, confirmation } = await loadModules();
  const common = {
    selectedCaseIds: ["prepared", "invalid", "sending", "confirmed"],
    draftByCaseId: { prepared: {}, invalid: {}, sending: {}, confirmed: undefined },
    validationByCaseId: { prepared: [], invalid: ["Falta motivo"], sending: [], confirmed: [] },
    resultByCaseId: { confirmed: { status: "confirmed", correlationId: "ref", message: "Confirmado" } },
    requestStateByCaseId: { prepared: "idle", invalid: "idle", sending: "sending", confirmed: "succeeded" },
    onConfirm: () => undefined
  };
  const blocked = renderToStaticMarkup(React.createElement(confirmation.SessionConfirmationBar, common));
  assert.match(blocked, /Preparados[^<]*1/);
  assert.match(blocked, /Inválidos[^<]*1/);
  assert.match(blocked, /En envío[^<]*1/);
  assert.match(blocked, /Confirmados[^<]*1/);
  const blockedTree = confirmation.SessionConfirmationBar(common);
  const blockedButton = descendants(React, blockedTree).find((node) => node.type === "button");
  assert.equal(blockedButton.props.disabled, true);

  const queuedProps = {
    ...common,
    selectedCaseIds: ["prepared"],
    requestStateByCaseId: { prepared: "queued" },
    resultByCaseId: {}
  };
  const queuedTree = confirmation.SessionConfirmationBar(queuedProps);
  const queuedButton = descendants(React, queuedTree).find((node) => node.type === "button");
  assert.equal(queuedButton.props.disabled, true);
});

test("changing a compatible action preserves edited fields, scope, reason and correlation", async () => {
  const { contract } = await loadModules();
  const source = detail({
    detected_value: "JUAN PEREZ",
    normalized_value: "JUAN PEREZ",
    canonical_value: "JUAN PEREZ"
  });
  let draft = contract.createDecisionDraft(source, "approve", "correction-correlation");
  draft = contract.withDecisionPayload(draft, {
    ...draft.payload,
    canonical_identity_key: "identity:juan-perez",
    canonical_name: "JUAN CARLOS PEREZ",
    aliases: ["JUAN PEREZ"]
  });
  draft = { ...draft, scope: "document", reason: "Nombre verificado" };

  const corrected = contract.withDecisionAction(source, draft, "correct");
  assert.equal(corrected.action, "correct");
  assert.equal(corrected.payload.canonical_name, "JUAN CARLOS PEREZ");
  assert.deepEqual(corrected.payload.aliases, ["JUAN PEREZ"]);
  assert.equal(corrected.scope, "document");
  assert.equal(corrected.reason, "Nombre verificado");
  assert.equal(corrected.correlation_id, "correction-correlation");
  assert.equal(Object.hasOwn(corrected.payload, "resolution"), false);

  const linked = contract.withDecisionAction(source, corrected, "link");
  assert.equal(linked.payload.canonical_name, "JUAN CARLOS PEREZ");
  assert.equal(linked.payload.resolution, "linked");
  const backToCorrect = contract.withDecisionAction(source, linked, "correct");
  assert.equal(backToCorrect.payload.canonical_name, "JUAN CARLOS PEREZ");
  assert.equal(Object.hasOwn(backToCorrect.payload, "resolution"), false);
  assert.equal(contract.withDecisionAction(detail({ case_type: "product" }), backToCorrect, "correct"), null);

  assert.equal(contract.cancelDecisionDraft(), null);
  const restarted = contract.createDecisionDraft(source, "correct", "new-correlation");
  assert.equal(restarted.payload.canonical_name, "JUAN PEREZ");
});

test("action matrix and exact apply payloads preserve discriminants, closed resolutions and CAS", async () => {
  const { contract } = await loadModules();
  assert.deepEqual(contract.allowedDecisionActions("product"), ["approve", "correct"]);
  assert.deepEqual(contract.allowedDecisionActions("possible_duplicate"), ["link"]);
  assert.deepEqual(contract.allowedDecisionActions("invalid_text"), []);
  assert.deepEqual(contract.allowedDecisionActions("new_evidence_conflict"), []);

  const source = detail({ version: 7, current_decision_id: "00000000-0000-0000-0000-000000000500" });
  let approve = contract.createDecisionDraft(source, "approve", "00000000-0000-0000-0000-000000000501");
  approve = contract.withDecisionPayload(approve, {
    case_type: "person_identity",
    canonical_identity_key: "identity:ada-lovelace",
    canonical_name: "Ada Lovelace",
    aliases: ["Ada Byron"],
    scientific_status: "validated"
  });
  assert.deepEqual(contract.buildApplyDecisionRequest(source, approve), {
    expected_version: 7,
    expected_current_decision_id: "00000000-0000-0000-0000-000000000500",
    action: "approve",
    scope: "global_identity",
    payload: {
      case_type: "person_identity",
      canonical_identity_key: "identity:ada-lovelace",
      canonical_name: "Ada Lovelace",
      aliases: ["Ada Byron"],
      scientific_status: "validated"
    },
    reason: null,
    correlation_id: "00000000-0000-0000-0000-000000000501"
  });

  let linked = contract.createDecisionDraft(source, "link", "00000000-0000-0000-0000-000000000502");
  linked = contract.withDecisionPayload(linked, {
    case_type: "person_identity",
    canonical_identity_key: "identity:ada-lovelace",
    canonical_name: "Ada Lovelace",
    aliases: [],
    scientific_status: "validated",
    resolution: "maintained_separate"
  });
  linked = { ...linked, reason: "Se verificaron ambas identidades" };
  const linkedRequest = contract.buildApplyDecisionRequest(source, linked);
  assert.equal(linkedRequest.payload.case_type, "person_identity");
  assert.equal(linkedRequest.payload.resolution, "maintained_separate");
  assert.equal(linkedRequest.payload.canonical_identity_key, "identity:ada-lovelace");
  assert.deepEqual(Object.keys(linkedRequest).sort(), [
    "action", "correlation_id", "expected_current_decision_id", "expected_version", "payload", "reason", "scope"
  ]);
  assert.equal(Object.hasOwn(linkedRequest, "decision_type"), false);

  const invalidStatus = contract.withDecisionPayload(linked, { ...linked.payload, scientific_status: "rejected" });
  assert.throws(() => contract.buildApplyDecisionRequest(source, invalidStatus), /validado/i);
});

test("unsupported case types do not render discard or apply actions", async () => {
  const { React, renderToStaticMarkup, cache, composer } = await loadModules();
  for (const caseType of ["invalid_text", "new_evidence_conflict"]) {
    const html = renderToStaticMarkup(React.createElement(cache.DataCacheProvider, null,
      React.createElement(composer.DecisionComposer, {
        detail: detail({ case_type: caseType, field_path: "case" }),
        capabilities: { capability: "faculty_gestor", actions: ["apply_scientific", "revert_scientific"] },
        capabilitiesLoading: false,
        capabilitiesUpdating: false,
        capabilitiesError: null,
        onReloadCase: async () => detail(),
        onSuccess: () => undefined
      })
    ));
    assert.doesNotMatch(html, /Aprobar|Corregir|Vincular|Descartar/i);
    assert.match(html, /Este tipo de caso no admite una decisión guiada desde esta pantalla/i);
  }
});

test("possible duplicate uses only public CaseDetail options for preview, cancel and confirmation", async () => {
  const { contract } = await loadModules();
  assert.equal(contract.isPublicIdentityReference("identity:ada-lovelace"), true);
  for (const unsafe of ["b2b:v1:person_identity:aaaaaaaa", "dropbox_path:/private.pdf", "C:\\private\\id", "../secret"])
    assert.equal(contract.isPublicIdentityReference(unsafe), false);

  let fetches = 0;
  globalThis.fetch = async () => { fetches += 1; return jsonResponse({}); };
  const duplicate = detail({
    case_type: "possible_duplicate",
    field_path: "case",
    canonical_value: null,
    counterpart_options: [
      {
        counterpart_ref: { target_type: "person_roles", target_id: 920 },
        display_name: "Ada Lovelace (registro B)",
        source_label: "Ada B. Lovelace",
        document_name: "informe-b.pdf"
      },
      {
        counterpart_ref: { target_type: "scientific_production_authors", target_id: 44 },
        display_name: "Ada Lovelace (autorÃ­a)",
        source_label: "A. Lovelace",
        document_name: "articulo.pdf"
      }
    ]
  });
  let draft = contract.createDecisionDraft(duplicate, "link", "00000000-0000-0000-0000-000000000503");
  assert.deepEqual(draft.payload.counterpart_ref, { target_type: "person_roles", target_id: 920 });
  draft = contract.withDecisionPayload(draft, {
    ...draft.payload,
    counterpart_ref: { target_type: "scientific_production_authors", target_id: 44 },
    resolution: "merged"
  });
  draft = { ...draft, reason: "Coincidencia verificada" };
  const preview = contract.buildDecisionPreview(duplicate, draft);
  const request = contract.buildApplyDecisionRequest(duplicate, draft);
  assert.equal(preview.selectedLink, "Ada Lovelace (autorÃ­a)");
  assert.deepEqual(request.payload.counterpart_ref, {
    target_type: "scientific_production_authors",
    target_id: 44
  });
  assert.equal(JSON.stringify(request).includes("stable_target"), false);
  assert.equal(contract.cancelDecisionDraft(), null);
  assert.equal(fetches, 0);

  const unavailable = detail({ case_type: "possible_duplicate", field_path: "case", canonical_value: null, counterpart_options: [] });
  assert.equal(contract.createDecisionDraft(unavailable, "link", "00000000-0000-0000-0000-000000000504"), null);
});

test("reason validation rejects blank and oversized values and builds exact discard/revert requests", async () => {
  const { contract } = await loadModules();
  const source = detail({ version: 9, current_decision_id: "00000000-0000-0000-0000-000000000601" });
  assert.throws(() => contract.buildDiscardRequest(source, "   ", "00000000-0000-0000-0000-000000000602"), /motivo/i);
  assert.throws(() => contract.buildDiscardRequest(source, "x".repeat(4001), "00000000-0000-0000-0000-000000000602"), /4000/);
  assert.deepEqual(contract.buildDiscardRequest(source, " Fuera del corpus ", "00000000-0000-0000-0000-000000000602"), {
    expected_version: 9,
    expected_current_decision_id: "00000000-0000-0000-0000-000000000601",
    reason: "Fuera del corpus",
    correlation_id: "00000000-0000-0000-0000-000000000602"
  });
  assert.deepEqual(contract.buildRevertRequest(source, " Restaurar proyección ", "00000000-0000-0000-0000-000000000603"), {
    expected_version: 9,
    expected_current_decision_id: "00000000-0000-0000-0000-000000000601",
    decision_id_to_revert: "00000000-0000-0000-0000-000000000601",
    reason: "Restaurar proyección",
    correlation_id: "00000000-0000-0000-0000-000000000603"
  });
  assert.throws(() => contract.buildRevertRequest(detail(), "motivo", "00000000-0000-0000-0000-000000000603"), /decisión vigente/i);
});

test("confirmations are explicit, render safely and preview performs no command", async () => {
  const { React, renderToStaticMarkup, contract, preview, linker, discard, revert } = await loadModules();
  const source = detail();
  let draft = contract.createDecisionDraft(source, "link", "00000000-0000-0000-0000-000000000701");
  draft = contract.withDecisionPayload(draft, {
    case_type: "person_identity",
    canonical_identity_key: "identity:ada-lovelace",
    canonical_name: "Ada Lovelace",
    aliases: [],
    scientific_status: "validated",
    resolution: "linked"
  });
  let fetches = 0;
  globalThis.fetch = async () => { fetches += 1; return jsonResponse(successfulResponse()); };
  const previewHtml = renderToStaticMarkup(React.createElement(preview.DecisionPreview, { detail: source, draft }));
  const linkerHtml = renderToStaticMarkup(React.createElement(linker.IdentityLinker, { payload: draft.payload, disabled: false, onChange() {} }));
  const discardClosed = renderToStaticMarkup(React.createElement(discard.DiscardConfirmDialog, { open: false, reason: "", pending: false, onReasonChange() {}, onCancel() {}, onConfirm() {} }));
  const discardOpen = renderToStaticMarkup(React.createElement(discard.DiscardConfirmDialog, { open: true, reason: "", pending: false, onReasonChange() {}, onCancel() {}, onConfirm() {} }));
  const revertOpen = renderToStaticMarkup(React.createElement(revert.RevertConfirmDialog, { open: true, reason: "", pending: false, onReasonChange() {}, onCancel() {}, onConfirm() {} }));

  assert.equal(fetches, 0);
  assert.match(previewHtml, /Vista previa local/i);
  assert.match(previewHtml, /Ada Lovelace/);
  assert.match(previewHtml, /identity:ada-lovelace/);
  assert.match(previewHtml, /Vinculado/);
  assert.match(previewHtml, /Referencia de identidad/);
  assert.match(previewHtml, /Ámbito/i);
  assert.match(linkerHtml, /identificador canónico público/i);
  assert.equal(discardClosed, "");
  assert.match(discardOpen, /Confirmar descarte/i);
  assert.match(revertOpen, /Confirmar reversión/i);
  assert.match(discardOpen, /aria-describedby="discard-dialog-description"/);
  assert.match(revertOpen, /aria-describedby="revert-dialog-description"/);
  assert.match(discardOpen, /Motivo obligatorio[\s\S]*aria-required="true"/);
  assert.match(revertOpen, /Motivo obligatorio[\s\S]*aria-required="true"/);
  assert.match(discardOpen, /aria-hidden="true"[^>]*> \*<\/span>/);
  assert.match(revertOpen, /aria-hidden="true"[^>]*> \*<\/span>/);
  assert.doesNotMatch(`${previewHtml}${linkerHtml}${discardOpen}${revertOpen}`, /document_key|source_path|bucket|object_key|dropbox_path|b2b:v1/i);
});

test("IdentityLinker renders public duplicate options without a manual technical-key field", async () => {
  const { React, renderToStaticMarkup, linker } = await loadModules();
  const payload = {
    case_type: "possible_duplicate",
    counterpart_ref: { target_type: "person_roles", target_id: 920 },
    resolution: "merged",
    scientific_status: "validated"
  };
  const html = renderToStaticMarkup(React.createElement(linker.IdentityLinker, {
    payload,
    counterpartOptions: [{
      counterpart_ref: { target_type: "person_roles", target_id: 920 },
      display_name: "Ada Lovelace (registro B)",
      source_label: "Ada B. Lovelace",
      document_name: "informe-b.pdf"
    }],
    disabled: false,
    onChange() {}
  }));
  assert.match(html, /Seleccionar contraparte/i);
  assert.match(html, /Ada Lovelace \(registro B\)/);
  assert.match(html, /informe-b\.pdf/);
  assert.doesNotMatch(html, /counterpart_stable_target_key|stable target|clave tÃ©cnica/i);
  assert.equal((html.match(/<input/g) ?? []).length, 0);
});

test("pending gate ignores a second confirmation, has no retry and preserves the same API error", async () => {
  const { api, client, hooks } = await loadModules();
  const gate = { current: null };
  let calls = 0;
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  const first = hooks.runSinglePendingCommand(gate, async () => { calls += 1; await pending; return "ok"; });
  const second = hooks.runSinglePendingCommand(gate, async () => { calls += 1; return "duplicate"; });
  assert.equal(calls, 1);
  const secondOutcome = await Promise.race([
    second,
    new Promise((resolve) => setTimeout(() => resolve("still-pending"), 25))
  ]);
  release();
  assert.equal(await first, "ok");
  assert.equal(secondOutcome, undefined);

  const error = new client.HumanReviewApiError(new api.ApiFetchError("Conflicto seguro", {
    status: 409,
    code: "REVIEW_CASE_VERSION_CONFLICT",
    correlation_id: "00000000-0000-0000-0000-000000000702"
  }));
  let failures = 0;
  await assert.rejects(
    hooks.runSinglePendingCommand(gate, async () => { failures += 1; throw error; }),
    (caught) => caught === error
  );
  assert.equal(failures, 1);
});

test("success invalidates all affected public cache prefixes; failure performs no retry", async () => {
  const { client } = await loadModules();
  const invalidated = [];
  let calls = 0;
  globalThis.fetch = async () => { calls += 1; return jsonResponse(successfulResponse()); };
  const apiClient = client.createHumanReviewApi((prefix) => invalidated.push(prefix));
  await apiClient.discard("case-one", {
    expected_version: 3,
    expected_current_decision_id: null,
    reason: "Fuera del corpus",
    correlation_id: "00000000-0000-0000-0000-000000000703"
  });
  assert.equal(calls, 1);
  assert.deepEqual(invalidated, [
    "human-review:case:case-one",
    "human-review:audit:case-one:",
    "human-review:queue:",
    "dashboard:",
    "canonical-participants:",
    "human-review:related:"
  ]);

  globalThis.fetch = async () => { calls += 1; return jsonResponse({
    code: "REVIEW_CASE_VERSION_CONFLICT",
    message: "El caso cambió",
    correlation_id: "00000000-0000-0000-0000-000000000704"
  }, 409); };
  await assert.rejects(apiClient.discard("case-one", {
    expected_version: 3,
    expected_current_decision_id: null,
    reason: "Fuera del corpus",
    correlation_id: "00000000-0000-0000-0000-000000000704"
  }), client.HumanReviewApiError);
  assert.equal(calls, 2);
});

test("wire types keep relationship_status closed and reject unknown command keys", async () => {
  await loadModules();
  const fixtureRoot = await mkdtemp(path.join(tmpdir(), "b2b2-task12-types-"));
  try {
    let importPath = path.relative(fixtureRoot, path.join(frontendRoot, "lib", "human-review.ts")).replaceAll("\\", "/");
    if (!importPath.startsWith(".")) importPath = `./${importPath}`;
    const fixture = `
import type { ApplyDecisionRequest, DuplicatePayload, RelationPayload } from ${JSON.stringify(importPath)};
const valid: RelationPayload = { case_type: "project_director_relation", project_director_identity_key: "identity:director", relationship_status: "separated", scientific_status: "validated" };
const duplicate: DuplicatePayload = { case_type: "possible_duplicate", counterpart_ref: { target_type: "person_roles", target_id: 7 }, resolution: "merged", scientific_status: "validated" };
// @ts-expect-error relationship_status is a closed union
const invalidStatus: RelationPayload = { ...valid, relationship_status: "merged" };
// @ts-expect-error internal stable keys are not part of the public wire type
const internalDuplicate: DuplicatePayload = { case_type: "possible_duplicate", counterpart_stable_target_key: "b2b:v1:person_identity:secret", resolution: "merged", scientific_status: "validated" };
// @ts-expect-error decision_type is derived by the server and cannot be sent
const extra: ApplyDecisionRequest = { expected_version: 1, expected_current_decision_id: null, action: "link", scope: "relationship", payload: valid, reason: "verified", correlation_id: "cid", decision_type: "linked" };
void valid; void duplicate; void invalidStatus; void internalDuplicate; void extra;
`;
    const fixturePath = path.join(fixtureRoot, "contract.ts");
    await writeFile(fixturePath, fixture, "utf8");
    const result = spawnSync(process.execPath, [tsc, "--noEmit", "--strict", "--skipLibCheck", "--target", "ES2022", "--module", "preserve", "--moduleResolution", "bundler", "--allowImportingTsExtensions", fixturePath], {
      cwd: frontendRoot,
      encoding: "utf8"
    });
    assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test("Task 12 keeps server-side draft surfaces absent", async () => {
  const { client } = await loadModules();
  for (const name of ["createDraft", "saveDraft", "createProposal", "reserve", "claimOwnership", "retry"])
    assert.equal(Object.hasOwn(client.humanReviewApi, name), false);
});

test("session initialization is keyed by case_id and case-scoped edits survive selection, cancel and success", async () => {
  const { contract } = await loadModules();
  const items = [relatedItem("case-b"), relatedItem("case-a")];
  let state = contract.createReviewSessionState(items, "case-a");
  assert.equal(state.activeCaseId, "case-a");
  for (const map of [state.draftByCaseId, state.validationByCaseId, state.resultByCaseId, state.conflictByCaseId, state.requestStateByCaseId, state.requestTokenByCaseId, state.requestDraftByCaseId]) {
    assert.deepEqual(Object.keys(map), ["case-b", "case-a"]);
  }
  assert.equal(state.requestStateByCaseId["case-a"], "idle");

  const draftA = validDraft(contract, detail({ id: "case-a" }));
  const draftB = validDraft(contract, detail({ id: "case-b" }));
  state = contract.reviewSessionReducer(state, { type: "draftChanged", caseId: "case-a", draft: draftA });
  state = contract.reviewSessionReducer(state, { type: "draftChanged", caseId: "case-b", draft: draftB });
  state = contract.reviewSessionReducer(state, { type: "select", caseId: "case-b" });
  assert.equal(state.draftByCaseId["case-a"], draftA);
  assert.equal(state.draftByCaseId["case-b"], draftB);

  state = contract.reviewSessionReducer(state, { type: "cancelDraft", caseId: "case-b" });
  assert.equal(state.draftByCaseId["case-b"], undefined);
  assert.equal(state.draftByCaseId["case-a"], draftA);
  state = contract.reviewSessionReducer(state, {
    type: "commandSucceeded",
    caseId: "case-a",
    result: { status: "confirmed", correlationId: "success-a", message: "Revisión confirmada." }
  });
  assert.equal(state.draftByCaseId["case-a"], undefined);
  assert.equal(state.resultByCaseId["case-a"].status, "confirmed");
});

test("related refresh preserves compatible drafts, represents incompatibility and clearAll is explicit", async () => {
  const { contract } = await loadModules();
  const sourceA = detail({ id: "case-a" });
  const sourceB = detail({ id: "case-b" });
  const draftA = validDraft(contract, sourceA);
  const draftB = validDraft(contract, sourceB);
  let state = contract.createReviewSessionState([relatedItem("case-a"), relatedItem("case-b")]);
  state = contract.reviewSessionReducer(state, { type: "draftChanged", caseId: "case-a", draft: draftA });
  state = contract.reviewSessionReducer(state, { type: "draftChanged", caseId: "case-b", draft: draftB });

  const refreshed = contract.reviewSessionReducer(state, {
    type: "relatedRefreshed",
    items: [
      relatedItem("case-a", { version: 4 }),
      relatedItem("case-b", { case_type: "product", allowed_actions: ["approve", "correct"] }),
      relatedItem("case-c")
    ]
  });
  assert.equal(refreshed.draftByCaseId["case-a"], draftA);
  assert.equal(refreshed.draftByCaseId["case-b"], draftB);
  assert.deepEqual(refreshed.validationByCaseId["case-a"], []);
  assert.match(refreshed.validationByCaseId["case-b"].join(" "), /compatible|revis/i);
  for (const map of [refreshed.draftByCaseId, refreshed.validationByCaseId, refreshed.resultByCaseId, refreshed.conflictByCaseId, refreshed.requestStateByCaseId, refreshed.requestTokenByCaseId, refreshed.requestDraftByCaseId]) {
    assert.equal(Object.hasOwn(map, "case-c"), true);
  }
  assert.equal(refreshed.requestStateByCaseId["case-c"], "idle");

  const selected = contract.reviewSessionReducer(refreshed, { type: "select", caseId: "case-a" });
  assert.equal(selected.draftByCaseId["case-b"], draftB);
  const cleared = contract.reviewSessionReducer(selected, { type: "clearAll" });
  assert.equal(cleared.draftByCaseId["case-a"], undefined);
  assert.equal(cleared.draftByCaseId["case-b"], undefined);
});

test("session reducer and validation never access browser storage", async () => {
  const { contract } = await loadModules();
  const formerLocalStorage = globalThis.localStorage;
  const formerSessionStorage = globalThis.sessionStorage;
  let storageReads = 0;
  const forbiddenStorage = new Proxy({}, { get() { storageReads += 1; throw new Error("storage is forbidden"); } });
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: forbiddenStorage });
  Object.defineProperty(globalThis, "sessionStorage", { configurable: true, value: forbiddenStorage });
  try {
    const source = detail({ id: "case-a" });
    const draft = validDraft(contract, source);
    let state = contract.createReviewSessionState([relatedItem("case-a")]);
    state = contract.reviewSessionReducer(state, { type: "draftChanged", caseId: "case-a", draft });
    assert.deepEqual(contract.validateSessionDraft(source, draft), []);
    assert.equal(state.draftByCaseId["case-a"], draft);
    assert.equal(storageReads, 0);
  } finally {
    if (formerLocalStorage === undefined) delete globalThis.localStorage;
    else Object.defineProperty(globalThis, "localStorage", { configurable: true, value: formerLocalStorage });
    if (formerSessionStorage === undefined) delete globalThis.sessionStorage;
    else Object.defineProperty(globalThis, "sessionStorage", { configurable: true, value: formerSessionStorage });
  }
});

test("coordinator validates all drafts, sends valid cases in stable order with at most two in flight and keeps partial results", async () => {
  const { api, client, contract, hooks } = await loadModules();
  const details = Object.fromEntries(["case-a", "case-b", "case-c", "case-d"].map((caseId) => [caseId, detail({ id: caseId })]));
  let state = contract.createReviewSessionState([relatedItem("case-c"), relatedItem("case-a"), relatedItem("case-d"), relatedItem("case-b")], "case-a");
  for (const caseId of ["case-c", "case-a", "case-b"]) {
    state = contract.reviewSessionReducer(state, { type: "draftChanged", caseId, draft: validDraft(contract, details[caseId]) });
  }
  const invalidD = { ...validDraft(contract, details["case-d"]), reason: "" };
  state = contract.reviewSessionReducer(state, { type: "draftChanged", caseId: "case-d", draft: invalidD });

  const pendingById = { "case-a": deferred(), "case-b": deferred(), "case-c": deferred() };
  const calls = [];
  let inFlight = 0;
  let maximumInFlight = 0;
  const apply = async (caseId) => {
    calls.push(caseId);
    inFlight += 1;
    maximumInFlight = Math.max(maximumInFlight, inFlight);
    try { return await pendingById[caseId].promise; }
    finally { inFlight -= 1; }
  };
  const actions = [];
  const dispatch = (action) => {
    actions.push(action);
    state = contract.reviewSessionReducer(state, action);
  };
  const confirmation = hooks.confirmValidDrafts({
    state,
    getState: () => state,
    requestArbiter: hooks.createReviewRequestArbiter(),
    detailByCaseId: details,
    apply,
    dispatch
  });
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls, ["case-a", "case-b"]);
  assert.ok(actions.slice(0, 4).every((action) => action.type === "validationChanged"));

  pendingById["case-a"].resolve(successfulResponse(detail({ id: "case-a", case_status: "resolved", scientific_status: "validated", version: 4 })));
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls, ["case-a", "case-b", "case-c"]);
  pendingById["case-b"].reject(new client.HumanReviewApiError(new api.ApiFetchError("raw server text", {
    status: 409, code: "REVIEW_CASE_VERSION_CONFLICT", correlation_id: "approved-b"
  })));
  pendingById["case-c"].reject(new client.HumanReviewApiError(new api.ApiFetchError("database host secret", {
    status: 503, code: "HUMAN_REVIEW_INTERNAL_ERROR", correlation_id: "approved-c"
  })));
  const results = await confirmation;

  assert.equal(maximumInFlight, 2);
  assert.deepEqual(calls, ["case-a", "case-b", "case-c"]);
  assert.equal(results["case-a"].status, "confirmed");
  assert.equal(results["case-b"].status, "conflict");
  assert.equal(results["case-c"].status, "unavailable");
  assert.equal(results["case-d"].status, "invalid");
  assert.equal(state.draftByCaseId["case-a"], undefined);
  for (const caseId of ["case-b", "case-c", "case-d"]) assert.ok(state.draftByCaseId[caseId]);
  for (const caseId of ["case-a", "case-b", "case-c"]) assert.equal(calls.filter((called) => called === caseId).length, 1);
  assert.equal(calls.includes("case-d"), false);
  assert.equal(state.requestStateByCaseId["case-a"], "succeeded");
  assert.equal(state.requestStateByCaseId["case-b"], "failed");
  assert.equal(state.requestStateByCaseId["case-c"], "failed");
  assert.equal(state.requestStateByCaseId["case-d"], "failed");
  assert.doesNotMatch(Object.values(results).map((result) => result.message).join(" "), /raw server|database host/i);
});

test("command result mapping is sanitized for every approved status and unknown rejections", async () => {
  const { api, client, hooks } = await loadModules();
  const expected = new Map([[403, "forbidden"], [404, "not_found"], [409, "conflict"], [422, "invalid"], [503, "unavailable"]]);
  for (const [status, resultStatus] of expected) {
    const result = hooks.toReviewCommandResult(new client.HumanReviewApiError(new api.ApiFetchError("SQL /secret/raw", {
      status,
      code: status === 409 ? "REVIEW_CASE_VERSION_CONFLICT" : "HUMAN_REVIEW_INTERNAL_ERROR",
      correlation_id: `approved-${status}`
    })));
    assert.equal(result.status, resultStatus);
    assert.equal(result.correlationId, `approved-${status}`);
    assert.doesNotMatch(result.message, /SQL|secret|raw/i);
  }
  const unknown = hooks.toReviewCommandResult(new Error("postgresql://credential@host"));
  assert.equal(unknown.status, "unavailable");
  assert.equal(unknown.correlationId, null);
  assert.doesNotMatch(unknown.message, /postgresql|credential|host/i);
});

test("late success or failure for A1 cannot overwrite the newer A2 draft or its current case state", async () => {
  const { contract } = await loadModules();
  const source = detail({ id: "case-a" });
  const draftA1 = validDraft(contract, source);
  const draftA2 = {
    ...draftA1,
    reason: "Una corrección posterior",
    payload: { ...draftA1.payload, canonical_name: "Nombre A2" }
  };
  let state = contract.createReviewSessionState([relatedItem("case-a")], "case-a");
  state = contract.reviewSessionReducer(state, { type: "draftChanged", caseId: "case-a", draft: draftA1 });
  state = contract.reviewSessionReducer(state, {
    type: "requestStateChanged", caseId: "case-a", state: "queued", requestToken: 71, requestDraft: draftA1
  });
  state = contract.reviewSessionReducer(state, {
    type: "requestStateChanged", caseId: "case-a", state: "sending", requestToken: 71, requestDraft: draftA1
  });
  state = contract.reviewSessionReducer(state, { type: "draftChanged", caseId: "case-a", draft: draftA2 });
  state = contract.reviewSessionReducer(state, {
    type: "validationChanged", caseId: "case-a", errors: ["Validación vigente de A2"]
  });
  const currentA2State = state;

  const staleSuccess = contract.reviewSessionReducer(state, {
    type: "commandSucceeded",
    caseId: "case-a",
    requestToken: 71,
    requestDraft: draftA1,
    result: { status: "confirmed", correlationId: "old-success", message: "Revisión confirmada." }
  });
  assert.equal(staleSuccess, currentA2State);
  assert.equal(staleSuccess.draftByCaseId["case-a"], draftA2);
  assert.deepEqual(staleSuccess.validationByCaseId["case-a"], ["Validación vigente de A2"]);
  assert.equal(staleSuccess.resultByCaseId["case-a"], undefined);
  assert.equal(staleSuccess.requestStateByCaseId["case-a"], "idle");

  const staleFailure = contract.reviewSessionReducer(state, {
    type: "commandFailed",
    caseId: "case-a",
    requestToken: 71,
    requestDraft: draftA1,
    result: { status: "unavailable", correlationId: "old-failure", message: "Servicio no disponible." }
  });
  assert.equal(staleFailure, currentA2State);
  assert.equal(staleFailure.draftByCaseId["case-a"], draftA2);
  assert.deepEqual(staleFailure.validationByCaseId["case-a"], ["Validación vigente de A2"]);
  assert.equal(staleFailure.resultByCaseId["case-a"], undefined);
  assert.equal(staleFailure.requestStateByCaseId["case-a"], "idle");
});

test("two concurrent coordinators share request ownership and send the same case exactly once", async () => {
  const { contract, hooks } = await loadModules();
  const source = detail({ id: "case-a" });
  const draft = validDraft(contract, source);
  let state = contract.createReviewSessionState([relatedItem("case-a")], "case-a");
  state = contract.reviewSessionReducer(state, { type: "draftChanged", caseId: "case-a", draft });
  const pending = deferred();
  let calls = 0;
  const apply = async () => {
    calls += 1;
    return pending.promise;
  };
  const dispatch = (action) => {
    state = contract.reviewSessionReducer(state, action);
  };
  const options = {
    state,
    getState: () => state,
    requestArbiter: hooks.createReviewRequestArbiter(),
    detailByCaseId: { "case-a": source },
    apply,
    dispatch
  };

  const first = hooks.confirmValidDrafts(options);
  const second = hooks.confirmValidDrafts(options);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls, 1);
  assert.equal(state.requestStateByCaseId["case-a"], "sending");

  pending.resolve(successfulResponse(detail({ id: "case-a", case_status: "resolved", scientific_status: "validated", version: 4 })));
  const [firstResults, secondResults] = await Promise.all([first, second]);
  assert.equal(calls, 1);
  assert.equal(firstResults["case-a"].status, "confirmed");
  assert.deepEqual(secondResults, {});
  assert.equal(state.draftByCaseId["case-a"], undefined);
  assert.equal(state.requestStateByCaseId["case-a"], "succeeded");
});

test("session arbiter claims once even when coordinator dispatches are batched asynchronously", async () => {
  const { contract, hooks } = await loadModules();
  const source = detail({ id: "case-a" });
  const draft = validDraft(contract, source);
  let state = contract.createReviewSessionState([relatedItem("case-a")], "case-a");
  state = contract.reviewSessionReducer(state, { type: "draftChanged", caseId: "case-a", draft });
  const initialSnapshot = state;
  const arbiter = hooks.createReviewRequestArbiter();
  const batchedActions = [];
  const pending = deferred();
  let calls = 0;
  const options = {
    state: initialSnapshot,
    getState: () => initialSnapshot,
    requestArbiter: arbiter,
    detailByCaseId: { "case-a": source },
    apply: async () => { calls += 1; return pending.promise; },
    dispatch: (action) => { batchedActions.push(action); }
  };

  const first = hooks.confirmValidDrafts(options);
  const second = hooks.confirmValidDrafts(options);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls, 1);
  assert.ok(batchedActions.some((action) => action.type === "requestStateChanged" && action.state === "sending"));

  pending.resolve(successfulResponse(detail({ id: "case-a", case_status: "resolved", scientific_status: "validated", version: 4 })));
  const [firstResults, secondResults] = await Promise.all([first, second]);
  assert.equal(calls, 1);
  assert.equal(firstResults["case-a"].status, "confirmed");
  assert.deepEqual(secondResults, {});
});
