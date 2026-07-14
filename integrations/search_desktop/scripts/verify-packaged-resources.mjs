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
]);

const SOURCE_RESOURCE_CONTRACT = Object.freeze(new Map([
  ["resources/search-assets/frontend/index.html", resolve(DESKTOP_ROOT, "../../frontend/dist/index.html")],
  ["resources/search-assets/design-system/tokens.css", resolve(DESKTOP_ROOT, "../../packages/search-design-system/src/tokens.css")],
  ["resources/search-assets/design-system/base.css", resolve(DESKTOP_ROOT, "../../packages/search-design-system/src/base.css")],
  ["resources/search-assets/design-system/components.css", resolve(DESKTOP_ROOT, "../../packages/search-design-system/src/components.css")],
  ["resources/app/electron/main/index.js", join(DESKTOP_ROOT, "electron/main/index.js")],
  ["resources/app/electron/preload/index.cjs", join(DESKTOP_ROOT, "electron/preload/index.cjs")],
  ["resources/app/assets/search.ico", join(DESKTOP_ROOT, "assets/search.ico")],
]));

export async function verifySourceResources() {
  for (const [packagedPath, sourcePath] of SOURCE_RESOURCE_CONTRACT) {
    await requireNonEmptyFile(sourcePath, packagedPath);
  }
  await verifyRequiredTokens(SOURCE_RESOURCE_CONTRACT.get("resources/search-assets/design-system/tokens.css"));
  return Object.freeze({ status: "ready", scope: "source", count: SOURCE_RESOURCE_CONTRACT.size });
}

export async function verifyPackagedResources(packagedRoot = DEFAULT_PACKAGED_ROOT) {
  const root = resolve(packagedRoot);
  for (const relativePath of PACKAGED_RESOURCE_CONTRACT) {
    await requireNonEmptyFile(join(root, ...relativePath.split("/")), relativePath);
  }
  await verifyRequiredTokens(join(root, "resources", "search-assets", "design-system", "tokens.css"));
  return Object.freeze({ status: "ready", scope: "packaged", count: PACKAGED_RESOURCE_CONTRACT.length });
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
