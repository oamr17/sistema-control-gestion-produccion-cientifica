import assert from "node:assert/strict";
import { createRequire } from "node:module";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const configPath = ts.findConfigFile(frontendRoot, ts.sys.fileExists, "tsconfig.json");
const config = ts.readConfigFile(configPath, ts.sys.readFile);
const parsedConfig = ts.parseJsonConfigFileContent(config.config, ts.sys, frontendRoot);
const program = ts.createProgram({ rootNames: parsedConfig.fileNames, options: parsedConfig.options });
const checker = program.getTypeChecker();

function moduleExports(relativePath) {
  const sourceFile = program.getSourceFile(path.join(frontendRoot, relativePath));
  const moduleSymbol = sourceFile && checker.getSymbolAtLocation(sourceFile);
  return moduleSymbol ? checker.getExportsOfModule(moduleSymbol) : [];
}

const apiExport = moduleExports("lib/api.ts").find((symbol) => symbol.getName() === "api");
const apiType = checker.getTypeOfSymbolAtLocation(apiExport, apiExport.valueDeclaration);
const apiMembers = new Set(checker.getPropertiesOfType(apiType).map((symbol) => symbol.getName()));
const typeExports = new Set(moduleExports("lib/types.ts").map((symbol) => symbol.getName()));
const productionPage = program.getSourceFile(path.join(frontendRoot, "app/production/page.tsx"));
const projectsPage = program.getSourceFile(path.join(frontendRoot, "app/projects/page.tsx"));
const teachersPage = program.getSourceFile(path.join(frontendRoot, "app/teachers/page.tsx"));

function assertUnsupportedApiMember(name) {
  assert.equal(apiMembers.has(name), false, `api.${name} must not be exported`);
}

function assertUnsupportedType(name) {
  assert.equal(typeExports.has(name), false, `${name} must not be exported`);
}

function renderedComponentsFromDefaultExport(sourceFile) {
  const defaultExport = sourceFile.statements.find(
    (statement) => ts.isFunctionDeclaration(statement)
      && statement.modifiers?.some((modifier) => modifier.kind === ts.SyntaxKind.DefaultKeyword)
  );
  assert.ok(defaultExport, `${sourceFile.fileName} must keep a default component export`);
  const renderedComponents = [];
  function visit(node) {
    if (ts.isJsxSelfClosingElement(node) && ts.isIdentifier(node.tagName)) {
      renderedComponents.push(node.tagName.text);
    }
    ts.forEachChild(node, visit);
  }
  visit(defaultExport);
  return renderedComponents;
}

test("unsupported api.dashboard is absent from the shared frontend API", () => {
  assertUnsupportedApiMember("dashboard");
});

test("unsupported api.ocrTraces is absent from the shared frontend API", () => {
  assertUnsupportedApiMember("ocrTraces");
});

test("unsupported api.importBatchReconciliation is absent from the shared frontend API", () => {
  assertUnsupportedApiMember("importBatchReconciliation");
});

test("unsupported api.researchEntities is absent from the shared frontend API", () => {
  assertUnsupportedApiMember("researchEntities");
});

test("unsupported OcrTrace is absent from shared frontend types", () => {
  assertUnsupportedType("OcrTrace");
});

test("unsupported ResearchEntity is absent from shared frontend types", () => {
  assertUnsupportedType("ResearchEntity");
});

test("unsupported ProductionContent is absent from the Production page surface", () => {
  let containsProductionContent = false;
  function visit(node) {
    if (ts.isIdentifier(node) && node.text === "ProductionContent") containsProductionContent = true;
    ts.forEachChild(node, visit);
  }
  visit(productionPage);
  assert.equal(containsProductionContent, false, "ProductionContent must not remain in the route module");
  assert.deepEqual(renderedComponentsFromDefaultExport(productionPage), ["LegacyProductionPage"]);
});

test("unsupported ProjectsContent is absent from the Projects page surface", () => {
  let containsProjectsContent = false;
  function visit(node) {
    if (ts.isIdentifier(node) && node.text === "ProjectsContent") containsProjectsContent = true;
    ts.forEachChild(node, visit);
  }
  visit(projectsPage);
  assert.equal(containsProjectsContent, false, "ProjectsContent must not remain in the route module");
  assert.deepEqual(renderedComponentsFromDefaultExport(projectsPage), ["LegacyProjectsPage"]);
});

test("Participants route renders only the canonical page and has no legacy participant islands", () => {
  assert.deepEqual(renderedComponentsFromDefaultExport(teachersPage), ["CanonicalParticipantsPage"]);

  const unsupportedRoots = new Set(["LegacyTeachersPage", "TeachersContent", "pdfParticipants"]);
  const remainingRoots = teachersPage.statements
    .filter((statement) => ts.isFunctionDeclaration(statement) && unsupportedRoots.has(statement.name?.text))
    .map((statement) => statement.name.text);
  assert.deepEqual(remainingRoots, [], `Unsupported participant roots remain: ${remainingRoots.join(", ")}`);
});
