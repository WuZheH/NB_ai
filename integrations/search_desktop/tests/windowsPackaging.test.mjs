import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { invalidatePackagedExecutable } from "../scripts/package-windows-unpacked.mjs";
import {
  PACKAGED_RESOURCE_CONTRACT,
  verifyPackagedResources,
  verifySourceResources,
} from "../scripts/verify-packaged-resources.mjs";

const packageJson = JSON.parse(await readFile(new URL("../package.json", import.meta.url), "utf8"));
const productMetadata = JSON.parse(await readFile(new URL("../electron/product-metadata.json", import.meta.url), "utf8"));
const finalizer = await readFile(new URL("../scripts/finalize-windows-exe.mjs", import.meta.url), "utf8");
const packager = await readFile(new URL("../scripts/package-windows-unpacked.mjs", import.meta.url), "utf8");
const r5Packager = await readFile(new URL("../scripts/package-r5-candidate.mjs", import.meta.url), "utf8");
const ROOT = resolve(import.meta.dirname, "..");
const FIXTURE_ROOT = resolve(ROOT, "../..", ".codex_tmp", "search-desktop-packaging-tests");

test("Windows packaging avoids privileged symlink extraction and applies Search resources", () => {
  assert.equal(packageJson.build.win.signAndEditExecutable, false);
  assert.equal(packageJson.version, "0.1.3");
  assert.deepEqual(productMetadata, {
    productName: "Search",
    version: "0.1.3",
    buildId: "20260717-search-runtime-self-contained-r6-final",
    rendererAssetVersion: "0.1.3-search-runtime-self-contained-r6-final",
  });
  assert.equal(packageJson.build.electronDist, "node_modules/electron/dist");
  assert.match(packageJson.scripts["package:win:unpacked"], /package-windows-unpacked\.mjs/);
  assert.match(packageJson.scripts["package:candidate:r5"], /package-r5-candidate\.mjs/);
  const extraResources = JSON.stringify(packageJson.build.extraResources);
  assert.match(extraResources, /app\/runtime-project\/app/);
  assert.match(extraResources, /notebook_ai_chatgpt_app\/dist\/server\/index\.js/);
  assert.doesNotMatch(extraResources, /(?:^|[\\/])data(?:[\\/]|$)|model_cache|node_modules/i);
  assert.match(finalizer, /FileDescription: "Search"/);
  assert.match(finalizer, /ProductName: "Search"/);
  assert.match(finalizer, /OriginalFilename: "Search\.exe"/);
  assert.match(finalizer, /icon,/);
  assert.match(packager, /invalidatePackagedExecutable/);
  assert.match(r5Packager, /frontend["'],\s*["']node_modules["'],\s*["']vite/);
  assert.match(r5Packager, /\["build"\]/);
  assert.match(r5Packager, /packagedRoot:\s*["']\.["']/);
  assert.match(r5Packager, /verifyWindowsRuntimePathLengths/);
  assert.match(r5Packager, /maximum\s*>=\s*240/);
  assert.ok(r5Packager.indexOf("await verifyNoWorktreeReferences") > r5Packager.indexOf("r5-build-manifest.json"));
});

test("packaging source preflight accepts the complete self-contained runtime contract", async () => {
  const result = await verifySourceResources();
  assert.deepEqual(result, {
    status: "ready",
    scope: "source",
    count: PACKAGED_RESOURCE_CONTRACT.length,
  });
});

test("packaged preflight rejects a missing frontend index", async () => {
  const fixture = join(FIXTURE_ROOT, "missing-index");
  try {
    await writePackagedFixture(fixture, { includeIndex: false });
    await assert.rejects(
      verifyPackagedResources(fixture),
      /search_packaging_resource_missing:resources\/search-assets\/frontend\/index\.html/,
    );
  } finally {
    await rm(fixture, { recursive: true, force: true });
  }
});

test("packaged preflight rejects a token file with the legacy alias but no brand token", async () => {
  const fixture = join(FIXTURE_ROOT, "wrong-token");
  try {
    await writePackagedFixture(fixture, {
      tokens: ":root { --search-primary: #4f9ff8; --search-bg: #f5f7fa; }",
    });
    await assert.rejects(
      verifyPackagedResources(fixture),
      /search_packaging_token_missing_or_invalid:--search-brand/,
    );
  } finally {
    await rm(fixture, { recursive: true, force: true });
  }
});

test("packaged preflight rejects a missing background token", async () => {
  const fixture = join(FIXTURE_ROOT, "missing-background");
  try {
    await writePackagedFixture(fixture, { tokens: ":root { --search-brand: #4f9ff8; }" });
    await assert.rejects(
      verifyPackagedResources(fixture),
      /search_packaging_token_missing_or_invalid:--search-bg/,
    );
  } finally {
    await rm(fixture, { recursive: true, force: true });
  }
});

test("packaged preflight rejects a missing imported Python runtime module", async () => {
  const fixture = join(FIXTURE_ROOT, "missing-python-runtime-module");
  const importedModule = join(
    fixture,
    "resources",
    "app",
    "runtime-project",
    "scripts",
    "phase110k_p_d_import_alignment_hook_dry_run.py",
  );
  try {
    await writePackagedFixture(fixture);
    await rm(importedModule);
    await assert.rejects(
      verifyPackagedResources(fixture),
      /search_packaging_resource_missing:resources\/app\/runtime-project\/scripts\/phase110k_p_d_import_alignment_hook_dry_run\.py/,
    );
  } finally {
    await rm(fixture, { recursive: true, force: true });
  }
});

test("packaged preflight rejects any external MCP package import", async () => {
  const fixture = join(FIXTURE_ROOT, "external-mcp-package");
  try {
    await writePackagedFixture(fixture, {
      serverSource: "import leftPad from 'left-pad';\nvoid leftPad;",
      externalPackages: ["left-pad"],
    });
    await assert.rejects(
      verifyPackagedResources(fixture),
      /search_packaging_mcp_external_dependency_detected/,
    );
  } finally {
    await rm(fixture, { recursive: true, force: true });
  }
});

test("packaged preflight rejects databases or indexes inside runtime resources", async () => {
  const fixture = join(FIXTURE_ROOT, "forbidden-runtime-data");
  const database = join(fixture, "resources", "app", "runtime-project", "data", "research.db");
  try {
    await writePackagedFixture(fixture);
    await mkdir(dirname(database), { recursive: true });
    await writeFile(database, "not production data");
    await assert.rejects(
      verifyPackagedResources(fixture),
      /search_packaging_forbidden_payload:.*data/,
    );
  } finally {
    await rm(fixture, { recursive: true, force: true });
  }
});

test("failed packaging invalidates only the misleading Search executable", async () => {
  const fixture = join(FIXTURE_ROOT, "invalidate-executable");
  const executable = join(fixture, "Search.exe");
  const sibling = join(fixture, "resources.keep");
  try {
    await mkdir(fixture, { recursive: true });
    await writeFile(executable, "invalid package");
    await writeFile(sibling, "keep");
    await invalidatePackagedExecutable(executable);
    await assert.rejects(readFile(executable), /ENOENT/);
    assert.equal(await readFile(sibling, "utf8"), "keep");
  } finally {
    await rm(fixture, { recursive: true, force: true });
  }
});

async function writePackagedFixture(root, {
  includeIndex = true,
  tokens = ":root { --search-brand: #4f9ff8; --search-bg: #f5f7fa; }",
  serverSource = "import { createServer } from 'node:http'; void createServer;",
  externalPackages = [],
} = {}) {
  const serverManifest = JSON.stringify({
    schemaVersion: 1,
    serverSha256: createHash("sha256").update(serverSource).digest("hex").toUpperCase(),
    externalPackages,
  });
  const files = new Map([
    ["resources/search-assets/design-system/tokens.css", tokens],
    ["resources/search-assets/design-system/base.css", "html { box-sizing: border-box; }"],
    ["resources/search-assets/design-system/components.css", ".search-button { display: inline-flex; }"],
    ["resources/app/electron/main/index.js", "export {};"],
    ["resources/app/electron/preload/index.cjs", "module.exports = {};"],
    ["resources/app/assets/search.ico", "ico"],
    ["resources/app/runtime-project/app/main.py", "app = object()"],
    ["resources/app/runtime-project/app/runtime/config.py", "RUNTIME = True"],
    ["resources/app/runtime-project/app/models/__init__.py", "MODELS = True"],
    ["resources/app/runtime-project/scripts/runtime/notebook_ai_launcher.py", "raise SystemExit(0)"],
    ["resources/app/runtime-project/scripts/index/status_zotero_note_vectors.py", "raise SystemExit(0)"],
    ["resources/app/runtime-project/scripts/index/sync_zotero_note_vectors.py", "raise SystemExit(0)"],
    ["resources/app/runtime-project/config/retrieval_query_aliases.json", "{}"],
    ["resources/app/runtime-project/integrations/notebook_ai_chatgpt_app/package.json", "{\"type\":\"module\"}"],
    ["resources/app/runtime-project/integrations/notebook_ai_chatgpt_app/dist/server/index.js", serverSource],
    ["resources/app/runtime-project/integrations/notebook_ai_chatgpt_app/dist/server/build-manifest.json", serverManifest],
    ["resources/app/runtime-project/integrations/notebook_ai_chatgpt_app/web/dist/widget.html", "<!doctype html><title>Widget</title>"],
  ]);
  for (const relativePath of PACKAGED_RESOURCE_CONTRACT) {
    if (relativePath === "resources/search-assets/frontend/index.html" && !includeIndex) continue;
    if (!files.has(relativePath)) files.set(relativePath, "runtime fixture");
  }
  if (includeIndex) files.set("resources/search-assets/frontend/index.html", "<!doctype html><title>Search</title>");
  for (const [relativePath, content] of files) {
    const path = join(root, ...relativePath.split("/"));
    await mkdir(dirname(path), { recursive: true });
    await writeFile(path, content);
  }
}
