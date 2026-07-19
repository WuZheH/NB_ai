import { cp, mkdir, readFile, stat, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

throw new Error("search_legacy_candidate_packager_disabled_use_build_windows_ps1");

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const DESKTOP_ROOT = resolve(SCRIPT_DIR, "..");
const PROJECT_ROOT = resolve(DESKTOP_ROOT, "../..");

const packageJson = JSON.parse(await readFile(join(DESKTOP_ROOT, "package.json"), "utf8"));
const metadata = JSON.parse(await readFile(join(DESKTOP_ROOT, "electron", "product-metadata.json"), "utf8"));
const base = commandArgument("--base");
if (!base) throw new Error("search_candidate_base_runtime_required");
const candidateSuffix = commandArgument("--candidate-suffix") || "";
if (candidateSuffix && !/^[A-Za-z0-9.-]{1,32}$/.test(candidateSuffix)) {
  throw new Error("search_candidate_suffix_invalid");
}
const baseRoot = resolve(base);
const output = resolve(
  DESKTOP_ROOT,
  "dist-candidates",
  `Search-${packageJson.version}-${metadata.buildId}${candidateSuffix ? `-${candidateSuffix}` : ""}`,
  "win-unpacked",
);
const allowedRoot = resolve(DESKTOP_ROOT, "dist-candidates");
if (!output.startsWith(`${allowedRoot}\\`)) throw new Error("search_candidate_output_outside_scope");
if (baseRoot === output) throw new Error("search_candidate_base_equals_output");
await requireFile(join(baseRoot, "Search.exe"), "search_candidate_base_executable_missing");
if (await exists(join(baseRoot, "search-desktop.local.json"))) {
  throw new Error("search_candidate_base_contains_machine_local_config");
}
if (await exists(output)) throw new Error(`search_candidate_output_already_exists:${output}`);

await mkdir(dirname(output), { recursive: true });
await cp(baseRoot, output, { recursive: true, force: false, errorOnExist: true });

const appRoot = join(output, "resources", "app");
for (const directory of ["electron", "renderer", "scripts", "assets"]) {
  await cp(join(DESKTOP_ROOT, directory), join(appRoot, directory), {
    recursive: true,
    force: true,
  });
}
await writeFile(
  join(appRoot, "package.json"),
  `${JSON.stringify({
    name: packageJson.name,
    productName: packageJson.productName,
    version: packageJson.version,
    private: true,
    type: "module",
    main: packageJson.main,
    engines: packageJson.engines,
  }, null, 2)}\n`,
  "utf8",
);
for (const required of [
  "Search.exe",
  "resources/app/electron/main/index.js",
  "resources/app/electron/product-metadata.json",
  "resources/app/renderer/desktop-shell.html",
  "resources/app/renderer/desktop-shell.js",
  "resources/app/renderer/desktop-route-bridge.js",
  "resources/search-assets/frontend/index.html",
]) {
  await requireFile(join(output, ...required.split("/")), `search_candidate_resource_missing:${required}`);
}

process.stdout.write(`${JSON.stringify({
  status: "ready",
  output,
  executable: join(output, "Search.exe"),
  productName: metadata.productName,
  version: packageJson.version,
  buildId: metadata.buildId,
  sourceRuntime: "existing-runtime-dev-only",
  reusedRuntime: baseRoot,
})}\n`);

function commandArgument(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : null;
}

async function exists(path) {
  try {
    await stat(path);
    return true;
  } catch (error) {
    if (error?.code === "ENOENT") return false;
    throw error;
  }
}

async function requireFile(path, errorCode) {
  let value;
  try {
    value = await stat(path);
  } catch (error) {
    if (error?.code === "ENOENT") throw new Error(errorCode);
    throw error;
  }
  if (!value.isFile() || value.size <= 0) throw new Error(errorCode);
}
