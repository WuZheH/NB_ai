import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const DESKTOP_ROOT = resolve(SCRIPT_DIR, "..");
const PROJECT_ROOT = resolve(DESKTOP_ROOT, "../..");
const electronExe = resolve(DESKTOP_ROOT, "node_modules", "electron", "dist", "electron.exe");
const probe = resolve(DESKTOP_ROOT, "tests", "fixtures", "packagedStatusProbe.mjs");
const packagedRoot = resolve(commandArgument("--packaged-root") || "");
if (!commandArgument("--packaged-root")) throw new Error("search_packaged_root_required");
const manifestName = commandArgument("--manifest") || [
  "r5-build-manifest.json",
  "r4-build-manifest.json",
  "r3-build-manifest.json",
].find((candidate) => existsSync(resolve(packagedRoot, candidate)));
if (!manifestName) throw new Error("search_packaged_manifest_not_found");
const manifest = JSON.parse(await readFile(resolve(packagedRoot, manifestName), "utf8"));
const tempRoot = resolve(
  commandArgument("--temp-root")
    || resolve(PROJECT_ROOT, ".codex_tmp", "r3", "packaged-status-dom"),
);

const { code, stdout, stderr } = await runElectron();
assert.equal(code, 0, `R3 packaged status DOM probe failed\n${stdout}\n${stderr}`);
const resultLine = stdout.split(/\r?\n/).find((line) => line.startsWith("R3_STATUS_DOM_RESULT="));
assert.ok(resultLine, `R3 packaged status DOM result missing\n${stdout}\n${stderr}`);
const result = JSON.parse(resultLine.slice("R3_STATUS_DOM_RESULT=".length));
assert.equal(result.status, "ok", result.error || "R3 packaged status DOM failed");
assert.equal(result.buildId, manifest.buildId);
assert.equal(result.rendererAssetVersion, manifest.rendererAssetVersion);
assert.equal(result.settings.disabled, false);
assert.equal(result.settings.soonVisible, false);
assert.equal(result.settings.heading, "设置");
assert.deepEqual(result.statusLabels, [
  "检索后端",
  "MCP 后端",
  "Codex MCP",
  "Zotero 后端",
  "ChatGPT Tunnel",
]);
assert.equal(result.technicalDetailsExpanded, true);
assert.equal(result.refresh.label, "重新检查");
assert.equal(result.refresh.operational, true);
assert.equal(result.refresh.runtimeCallsAfter, result.refresh.runtimeCallsBefore + 1);
assert.ok(
  manifest.frontendAssets
    .filter((asset) => asset.path.endsWith(".js") && asset.path.includes("index-"))
    .some((asset) => result.loadedAssets.includes(asset.path)),
  `fresh production JS was not loaded: ${JSON.stringify(result.loadedAssets)}`,
);

process.stdout.write(`${JSON.stringify({
  status: "ready",
  packagedRoot,
  buildId: result.buildId,
  rendererAssetVersion: result.rendererAssetVersion,
  statusLabels: result.statusLabels,
  settings: result.settings,
  technicalDetailsExpanded: result.technicalDetailsExpanded,
  refresh: result.refresh,
  loadedAssets: result.loadedAssets,
})}\n`);

function runElectron() {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(electronExe, [probe], {
      cwd: DESKTOP_ROOT,
      shell: false,
      windowsHide: true,
      env: {
        ...process.env,
        ELECTRON_DISABLE_CRASH_REPORTING: "1",
        ELECTRON_DISABLE_SECURITY_WARNINGS: "true",
        SEARCH_R3_PACKAGED_ROOT: packagedRoot,
        SEARCH_STATUS_USER_DATA: resolve(tempRoot, "user-data"),
        SEARCH_STATUS_CRASH_DUMPS: resolve(tempRoot, "crash-dumps"),
        TEMP: resolve(tempRoot, "temp"),
        TMP: resolve(tempRoot, "temp"),
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.once("error", reject);
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error(`R3 packaged status DOM timed out\n${stdout}\n${stderr}`));
    }, 30_000);
    child.once("close", (code) => {
      clearTimeout(timer);
      resolvePromise({ code, stdout, stderr });
    });
  });
}

function commandArgument(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : null;
}
