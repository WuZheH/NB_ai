import { readFile, stat } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
export const DESKTOP_ROOT = resolve(SCRIPT_DIR, "..");
export const DEFAULT_PACKAGED_ROOT = join(DESKTOP_ROOT, "dist", "win-unpacked");

export const PACKAGED_RESOURCE_CONTRACT = Object.freeze([
  "resources/search-assets/frontend/index.html",
  "resources/search-assets/design-system/tokens.css",
  "resources/search-assets/design-system/base.css",
  "resources/search-assets/design-system/components.css",
  "resources/app/electron/main/index.js",
  "resources/app/electron/preload/index.cjs",
  "resources/app/assets/search.ico",
  "resources/app/runtime-project/app/main.py",
  "resources/app/runtime-project/app/runtime/config.py",
  "resources/app/runtime-project/app/models/__init__.py",
  "resources/app/runtime-project/scripts/runtime/notebook_ai_launcher.py",
  "resources/app/runtime-project/scripts/index/status_zotero_note_vectors.py",
  "resources/app/runtime-project/scripts/index/sync_zotero_note_vectors.py",
  "resources/app/runtime-project/config/retrieval_query_aliases.json",
  "resources/app/runtime-project/integrations/notebook_ai_chatgpt_app/package.json",
  "resources/app/runtime-project/integrations/notebook_ai_chatgpt_app/dist/server/index.js",
  "resources/app/runtime-project/integrations/notebook_ai_chatgpt_app/web/dist/widget.html",
]);

const SOURCE_RESOURCE_CONTRACT = Object.freeze(new Map([
  ["resources/search-assets/frontend/index.html", resolve(DESKTOP_ROOT, "../../frontend/dist/index.html")],
  ["resources/search-assets/design-system/tokens.css", resolve(DESKTOP_ROOT, "../../packages/search-design-system/src/tokens.css")],
  ["resources/search-assets/design-system/base.css", resolve(DESKTOP_ROOT, "../../packages/search-design-system/src/base.css")],
  ["resources/search-assets/design-system/components.css", resolve(DESKTOP_ROOT, "../../packages/search-design-system/src/components.css")],
  ["resources/app/electron/main/index.js", join(DESKTOP_ROOT, "electron/main/index.js")],
  ["resources/app/electron/preload/index.cjs", join(DESKTOP_ROOT, "electron/preload/index.cjs")],
  ["resources/app/assets/search.ico", join(DESKTOP_ROOT, "assets/search.ico")],
  ["resources/app/runtime-project/app/main.py", resolve(DESKTOP_ROOT, "../../app/main.py")],
  ["resources/app/runtime-project/app/runtime/config.py", resolve(DESKTOP_ROOT, "../../app/runtime/config.py")],
  ["resources/app/runtime-project/app/models/__init__.py", resolve(DESKTOP_ROOT, "../../app/models/__init__.py")],
  ["resources/app/runtime-project/scripts/runtime/notebook_ai_launcher.py", resolve(DESKTOP_ROOT, "../../scripts/runtime/notebook_ai_launcher.py")],
  ["resources/app/runtime-project/scripts/index/status_zotero_note_vectors.py", resolve(DESKTOP_ROOT, "../../scripts/index/status_zotero_note_vectors.py")],
  ["resources/app/runtime-project/scripts/index/sync_zotero_note_vectors.py", resolve(DESKTOP_ROOT, "../../scripts/index/sync_zotero_note_vectors.py")],
  ["resources/app/runtime-project/config/retrieval_query_aliases.json", resolve(DESKTOP_ROOT, "../../config/retrieval_query_aliases.json")],
  ["resources/app/runtime-project/integrations/notebook_ai_chatgpt_app/package.json", resolve(DESKTOP_ROOT, "../notebook_ai_chatgpt_app/package.json")],
  ["resources/app/runtime-project/integrations/notebook_ai_chatgpt_app/dist/server/index.js", resolve(DESKTOP_ROOT, "../notebook_ai_chatgpt_app/dist/server/index.js")],
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
  ));
  for (const forbidden of [
    "resources/app/runtime-project/data",
    "resources/app/runtime-project/model_cache",
    "resources/app/runtime-project/integrations/notebook_ai_chatgpt_app/node_modules",
  ]) {
    await requireMissingPath(join(root, ...forbidden.split("/")), forbidden);
  }
  return Object.freeze({ status: "ready", scope: "packaged", count: PACKAGED_RESOURCE_CONTRACT.length });
}

async function verifySelfContainedMcp(serverPath) {
  const source = await readFile(serverPath, "utf8");
  const executableSource = source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");
  if (
    /(?:^|\n)\s*(?:import|export)\b[^\n]*from\s+["'](?:@modelcontextprotocol\/|zod["'])/.test(executableSource)
    || /(?:import|require)\(\s*["'](?:@modelcontextprotocol\/|zod["'])/.test(executableSource)
  ) {
    throw new Error("search_packaging_mcp_external_dependency_detected");
  }
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
