import { createHash } from "node:crypto";
import { readFile, readdir, stat } from "node:fs/promises";
import { dirname, extname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { validateBuildIdentity } from "../electron/main/buildIdentity.js";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
export const DESKTOP_ROOT = resolve(SCRIPT_DIR, "..");
export const DEFAULT_PACKAGED_ROOT = join(DESKTOP_ROOT, "dist", "win-unpacked");

const PACKAGED_PYTHON_RUNTIME_MODULES = Object.freeze([
  "scripts/__init__.py",
  "scripts/import_book_ocr_layout_first.py",
  "scripts/importing/import_book_ocr_layout_first.py",
  "scripts/phase110k_p_b_alignment_writeback_plan.py",
  "scripts/phase110k_p_c_import_time_alignment_batch_dry_run.py",
  "scripts/phase110k_p_d_import_alignment_hook_dry_run.py",
  "scripts/phase110k_p_f_batch_alignment_writeback_apply.py",
  "scripts/phase110k_p_inspiration_match_readiness_dry_run.py",
  "scripts/zotero/phase110k_p_b_alignment_writeback_plan.py",
  "scripts/zotero/phase110k_p_c_import_time_alignment_batch_dry_run.py",
  "scripts/zotero/phase110k_p_d_import_alignment_hook_dry_run.py",
  "scripts/zotero/phase110k_p_f_batch_alignment_writeback_apply.py",
  "scripts/zotero/phase110k_p_inspiration_match_readiness_dry_run.py",
]);

export const PACKAGED_RESOURCE_CONTRACT = Object.freeze([
  "resources/search-assets/frontend/index.html",
  "resources/search-assets/design-system/tokens.css",
  "resources/search-assets/design-system/base.css",
  "resources/search-assets/design-system/components.css",
  "resources/app/electron/main/index.js",
  "resources/app/electron/preload/index.cjs",
  "resources/app/package.json",
  "resources/app/assets/search.ico",
  "resources/app/runtime-project/app/main.py",
  "resources/app/runtime-project/app/runtime/config.py",
  "resources/app/runtime-project/app/runtime/build_identity.py",
  "resources/app/runtime-project/app/models/__init__.py",
  "resources/app/runtime-project/scripts/runtime/notebook_ai_launcher.py",
  "resources/app/runtime-project/scripts/index/status_zotero_note_vectors.py",
  "resources/app/runtime-project/scripts/index/sync_zotero_note_vectors.py",
  ...PACKAGED_PYTHON_RUNTIME_MODULES.map((path) => `resources/app/runtime-project/${path}`),
  "resources/app/runtime-project/config/retrieval_query_aliases.json",
  "resources/app/runtime-project/integrations/notebook_ai_chatgpt_app/package.json",
  "resources/app/runtime-project/integrations/notebook_ai_chatgpt_app/dist/server/index.js",
  "resources/app/runtime-project/integrations/notebook_ai_chatgpt_app/dist/server/build-manifest.json",
  "resources/app/runtime-project/integrations/notebook_ai_chatgpt_app/web/dist/widget.html",
]);

const SOURCE_RESOURCE_CONTRACT = Object.freeze(new Map([
  ["resources/search-assets/frontend/index.html", resolve(DESKTOP_ROOT, "../../frontend/dist/index.html")],
  ["resources/search-assets/design-system/tokens.css", resolve(DESKTOP_ROOT, "../../packages/search-design-system/src/tokens.css")],
  ["resources/search-assets/design-system/base.css", resolve(DESKTOP_ROOT, "../../packages/search-design-system/src/base.css")],
  ["resources/search-assets/design-system/components.css", resolve(DESKTOP_ROOT, "../../packages/search-design-system/src/components.css")],
  ["resources/app/electron/main/index.js", join(DESKTOP_ROOT, "electron/main/index.js")],
  ["resources/app/electron/preload/index.cjs", join(DESKTOP_ROOT, "electron/preload/index.cjs")],
  ["resources/app/package.json", join(DESKTOP_ROOT, "package.json")],
  ["resources/app/assets/search.ico", join(DESKTOP_ROOT, "assets/search.ico")],
  ["resources/app/runtime-project/app/main.py", resolve(DESKTOP_ROOT, "../../app/main.py")],
  ["resources/app/runtime-project/app/runtime/config.py", resolve(DESKTOP_ROOT, "../../app/runtime/config.py")],
  ["resources/app/runtime-project/app/runtime/build_identity.py", resolve(DESKTOP_ROOT, "../../app/runtime/build_identity.py")],
  ["resources/app/runtime-project/app/models/__init__.py", resolve(DESKTOP_ROOT, "../../app/models/__init__.py")],
  ["resources/app/runtime-project/scripts/runtime/notebook_ai_launcher.py", resolve(DESKTOP_ROOT, "../../scripts/runtime/notebook_ai_launcher.py")],
  ["resources/app/runtime-project/scripts/index/status_zotero_note_vectors.py", resolve(DESKTOP_ROOT, "../../scripts/index/status_zotero_note_vectors.py")],
  ["resources/app/runtime-project/scripts/index/sync_zotero_note_vectors.py", resolve(DESKTOP_ROOT, "../../scripts/index/sync_zotero_note_vectors.py")],
  ...PACKAGED_PYTHON_RUNTIME_MODULES.map((path) => [
    `resources/app/runtime-project/${path}`,
    resolve(DESKTOP_ROOT, "../..", path),
  ]),
  ["resources/app/runtime-project/config/retrieval_query_aliases.json", resolve(DESKTOP_ROOT, "../../config/retrieval_query_aliases.json")],
  ["resources/app/runtime-project/integrations/notebook_ai_chatgpt_app/package.json", resolve(DESKTOP_ROOT, "../notebook_ai_chatgpt_app/package.json")],
  ["resources/app/runtime-project/integrations/notebook_ai_chatgpt_app/dist/server/index.js", resolve(DESKTOP_ROOT, "../notebook_ai_chatgpt_app/dist/server/index.js")],
  ["resources/app/runtime-project/integrations/notebook_ai_chatgpt_app/dist/server/build-manifest.json", resolve(DESKTOP_ROOT, "../notebook_ai_chatgpt_app/dist/server/build-manifest.json")],
  ["resources/app/runtime-project/integrations/notebook_ai_chatgpt_app/web/dist/widget.html", resolve(DESKTOP_ROOT, "../notebook_ai_chatgpt_app/web/dist/widget.html")],
]));

export async function verifySourceResources() {
  for (const [packagedPath, sourcePath] of SOURCE_RESOURCE_CONTRACT) {
    await requireNonEmptyFile(sourcePath, packagedPath);
  }
  await verifyRequiredTokens(SOURCE_RESOURCE_CONTRACT.get("resources/search-assets/design-system/tokens.css"));
  await verifySelfContainedMcp(
    SOURCE_RESOURCE_CONTRACT.get(
      "resources/app/runtime-project/integrations/notebook_ai_chatgpt_app/dist/server/index.js",
    ),
    SOURCE_RESOURCE_CONTRACT.get(
      "resources/app/runtime-project/integrations/notebook_ai_chatgpt_app/dist/server/build-manifest.json",
    ),
  );
  return Object.freeze({ status: "ready", scope: "source", count: SOURCE_RESOURCE_CONTRACT.size });
}

export async function verifyPackagedResources(packagedRoot = DEFAULT_PACKAGED_ROOT) {
  const root = resolve(packagedRoot);
  for (const relativePath of PACKAGED_RESOURCE_CONTRACT) {
    await requireNonEmptyFile(join(root, ...relativePath.split("/")), relativePath);
  }
  await verifyRequiredTokens(join(root, "resources", "search-assets", "design-system", "tokens.css"));
  await verifySelfContainedMcp(join(
    root,
    "resources",
    "app",
    "runtime-project",
    "integrations",
    "notebook_ai_chatgpt_app",
    "dist",
    "server",
    "index.js",
  ), join(
    root,
    "resources",
    "app",
    "runtime-project",
    "integrations",
    "notebook_ai_chatgpt_app",
    "dist",
    "server",
    "build-manifest.json",
  ));
  for (const forbidden of [
    "resources/app/runtime-project/data",
    "resources/app/runtime-project/model_cache",
    "resources/app/runtime-project/integrations/notebook_ai_chatgpt_app/node_modules",
  ]) {
    await requireMissingPath(join(root, ...forbidden.split("/")), forbidden);
  }
  await verifyForbiddenRuntimePayload(join(root, "resources", "app", "runtime-project"));
  const packagedPackage = JSON.parse(await readFile(
    join(root, "resources", "app", "package.json"),
    "utf8",
  ));
  const buildIdentity = validateBuildIdentity(packagedPackage.searchBuildIdentity, {
    expectedVersion: packagedPackage.version,
    expectedMode: "packaged",
  });
  await requireMissingPath(
    join(root, "resources", "app", "electron", "product-metadata.json"),
    "resources/app/electron/product-metadata.json",
  );
  return Object.freeze({
    status: "ready",
    scope: "packaged",
    count: PACKAGED_RESOURCE_CONTRACT.length,
    build_identity: buildIdentity,
  });
}

async function verifySelfContainedMcp(serverPath, manifestPath) {
  const serverBytes = await readFile(serverPath);
  let manifest;
  try {
    manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  } catch {
    throw new Error("search_packaging_mcp_bundle_manifest_invalid");
  }
  const actualHash = createHash("sha256").update(serverBytes).digest("hex").toUpperCase();
  if (
    manifest?.schemaVersion !== 1
    || manifest?.serverSha256 !== actualHash
    || !Array.isArray(manifest?.externalPackages)
  ) {
    throw new Error("search_packaging_mcp_bundle_manifest_invalid");
  }
  if (manifest.externalPackages.length) {
    throw new Error("search_packaging_mcp_external_dependency_detected");
  }
}

async function verifyForbiddenRuntimePayload(runtimeRoot) {
  const forbiddenDirectories = new Set([
    ".git",
    "credentials",
    "data",
    "model_cache",
    "node_modules",
  ]);
  const forbiddenExtensions = new Set([
    ".arrow",
    ".bin",
    ".db",
    ".gguf",
    ".key",
    ".lance",
    ".npy",
    ".npz",
    ".onnx",
    ".p12",
    ".parquet",
    ".pem",
    ".pfx",
    ".pt",
    ".pth",
    ".safetensors",
    ".sqlite",
    ".sqlite3",
  ]);
  for (const path of await listRuntimeEntries(runtimeRoot)) {
    const segments = path.toLowerCase().split("/");
    if (segments.some((segment) => forbiddenDirectories.has(segment))) {
      throw new Error(`search_packaging_forbidden_payload:${path}`);
    }
    const name = segments.at(-1);
    if (
      name === ".env"
      || name?.includes("credential")
      || forbiddenExtensions.has(extname(name || ""))
    ) {
      throw new Error(`search_packaging_forbidden_payload:${path}`);
    }
  }
}

async function listRuntimeEntries(root) {
  const entries = [];
  async function visit(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const absolute = join(directory, entry.name);
      const path = relative(root, absolute).replaceAll("\\", "/");
      entries.push(path);
      if (entry.isDirectory()) await visit(absolute);
    }
  }
  await visit(root);
  return entries;
}

async function requireMissingPath(path, contractPath) {
  try {
    await stat(path);
  } catch (error) {
    if (error?.code === "ENOENT") return;
    throw error;
  }
  throw new Error(`search_packaging_forbidden_payload:${contractPath}`);
}

export async function verifyRequiredTokens(tokensPath) {
  const source = (await readFile(tokensPath, "utf8")).replace(/\/\*[\s\S]*?\*\//g, "");
  return Object.freeze({
    brand: requiredHexToken(source, "--search-brand"),
    background: requiredHexToken(source, "--search-bg"),
  });
}

function requiredHexToken(source, name) {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = source.match(new RegExp(`${escaped}\\s*:\\s*(#[0-9a-fA-F]{6})\\s*;`));
  if (!match) throw new Error(`search_packaging_token_missing_or_invalid:${name}`);
  return match[1];
}

async function requireNonEmptyFile(path, contractPath) {
  let value;
  try {
    value = await stat(path);
  } catch (error) {
    if (error?.code === "ENOENT") throw new Error(`search_packaging_resource_missing:${contractPath}`);
    throw error;
  }
  if (!value.isFile() || value.size <= 0) {
    throw new Error(`search_packaging_resource_invalid:${contractPath}`);
  }
}

async function main() {
  const mode = process.argv[2] || "packaged";
  const result = mode === "source"
    ? await verifySourceResources()
    : mode === "packaged"
      ? await verifyPackagedResources(process.argv[3] || DEFAULT_PACKAGED_ROOT)
      : (() => { throw new Error("search_packaging_preflight_mode_invalid"); })();
  process.stdout.write(`${JSON.stringify(result)}\n`);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    console.error("[Search Desktop] packaging resource preflight failed", error);
    process.exitCode = 1;
  });
}
