import assert from "node:assert/strict";
import { mkdir, rm, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import test from "node:test";
import { resolveDesktopConfig } from "../electron/main/config.js";

const ROOT = resolve(import.meta.dirname, "..");
const FIXTURE = join(ROOT, ".test-work-packaged-config");
const executableProbe = () => true;

test("packaged Search gives userData desktop-runtime precedence over ambient variables and legacy sidecar", async () => {
  const fixture = join(FIXTURE, "priority");
  const executablePath = join(fixture, "Search.exe");
  const resourcesPath = join(fixture, "resources");
  const runtimeRoot = join(resourcesPath, "app", "runtime-project");
  const userDataPath = join(fixture, "roaming", "Search");
  const dataDir = join(fixture, "稳定 数据", "data");
  const pythonExe = join(fixture, "Python 环境", "python.exe");
  const nodeExe = join(fixture, "Node Runtime", "node.exe");
  await writeRuntimeFixture(runtimeRoot);
  await mkdir(dataDir, { recursive: true });
  await mkdir(resolve(pythonExe, ".."), { recursive: true });
  await mkdir(resolve(nodeExe, ".."), { recursive: true });
  await mkdir(userDataPath, { recursive: true });
  await writeFile(executablePath, "", "utf8");
  await writeFile(pythonExe, "", "utf8");
  await writeFile(nodeExe, "", "utf8");
  await writeFile(join(userDataPath, "desktop-runtime.json"), JSON.stringify({
    schemaVersion: 1,
    dataDir,
    pythonExe,
    nodeExe,
  }), "utf8");
  await writeFile(join(fixture, "search-desktop.local.json"), JSON.stringify({
    schemaVersion: 3,
    dataDir: join(fixture, "ignored-legacy-data"),
    pythonExe: join(fixture, "ignored-python.exe"),
    nodeExe: join(fixture, "ignored-node.exe"),
  }), "utf8");

  const config = resolveDesktopConfig({
    env: {
      PATH: join(fixture, "ambient-path"),
      LOCALAPPDATA: join(fixture, "local-app-data"),
      SEARCH_DATA_DIR: join(fixture, "ambient-data"),
      SEARCH_PYTHON: join(fixture, "ambient-python.exe"),
      SEARCH_NODE: join(fixture, "ambient-node.exe"),
      NOTEBOOK_AI_DATA_PROJECT_ROOT: join(fixture, "old-root"),
    },
    userDataPath,
    executablePath,
    resourcesPath,
    isPackaged: true,
    probeExecutable: executableProbe,
  });

  assert.equal(config.runtimeRoot, runtimeRoot);
  assert.equal(config.machineConfigPath, join(userDataPath, "machine-config.json"));
  assert.equal(config.desktopRuntimeConfigPath, join(userDataPath, "desktop-runtime.json"));
  assert.equal(config.desktopRuntimeConfig.source, "user_data");
  assert.equal(config.desktopRuntimeConfig.status, "desktop_runtime_ready");
  assert.equal(config.dataDir, resolve(dataDir));
  assert.equal(config.pythonExe, resolve(pythonExe));
  assert.equal(config.nodeExe, resolve(nodeExe));
  assert.equal(config.runtimeAvailable, true);
  assert.equal(config.dataAvailable, true);
  assert.deepEqual(config.runtimeMissing, []);
});

test("packaged Search reports missing config without PATH or LOCALAPPDATA data fallback", async () => {
  const fixture = join(FIXTURE, "missing");
  const executablePath = join(fixture, "Search.exe");
  const resourcesPath = join(fixture, "resources");
  const localData = join(fixture, "local-app-data", "Search", "data");
  await writeRuntimeFixture(join(resourcesPath, "app", "runtime-project"));
  await mkdir(fixture, { recursive: true });
  await writeFile(executablePath, "", "utf8");
  const config = resolveDesktopConfig({
    env: {
      LOCALAPPDATA: join(fixture, "local-app-data"),
      PATH: join(fixture, "ambient-path"),
      SEARCH_DATA_DIR: join(fixture, "ambient-data"),
      SEARCH_PYTHON: join(fixture, "ambient-python.exe"),
      SEARCH_NODE: join(fixture, "ambient-node.exe"),
    },
    userDataPath: join(fixture, "roaming", "Search"),
    executablePath,
    resourcesPath,
    isPackaged: true,
    probeExecutable: executableProbe,
  });
  assert.equal(config.dataDir, "");
  assert.equal(config.pythonExe, "");
  assert.equal(config.nodeExe, "");
  assert.equal(config.runtimeAvailable, false);
  assert.equal(config.runtimeErrorCode, "desktop_runtime_config_missing");
  assert.equal(config.desktopRuntimeConfig.source, "none");
  assert.equal(config.dataAvailable, false);
  assert.deepEqual(config.runtimeMissing, ["desktop_runtime_config"]);
  assert.equal(await pathExists(localData), false);
});

test("invalid userData config never falls through to a valid legacy sidecar", async () => {
  const fixture = join(FIXTURE, "invalid-priority");
  const executablePath = join(fixture, "Search.exe");
  const resourcesPath = join(fixture, "resources");
  const userDataPath = join(fixture, "roaming", "Search");
  const dataDir = join(fixture, "legacy-data");
  const pythonExe = join(fixture, "python.exe");
  const nodeExe = join(fixture, "node.exe");
  await writeRuntimeFixture(join(resourcesPath, "app", "runtime-project"));
  await mkdir(dataDir, { recursive: true });
  await mkdir(userDataPath, { recursive: true });
  await writeFile(executablePath, "", "utf8");
  await writeFile(pythonExe, "", "utf8");
  await writeFile(nodeExe, "", "utf8");
  await writeFile(join(userDataPath, "desktop-runtime.json"), "{", "utf8");
  await writeFile(join(fixture, "search-desktop.local.json"), JSON.stringify({
    schemaVersion: 3,
    dataDir,
    pythonExe,
    nodeExe,
  }), "utf8");

  const config = resolveDesktopConfig({
    env: { PATH: join(fixture, "ambient-path") },
    userDataPath,
    executablePath,
    resourcesPath,
    isPackaged: true,
    probeExecutable: executableProbe,
  });

  assert.equal(config.runtimeAvailable, false);
  assert.equal(config.runtimeErrorCode, "desktop_runtime_config_invalid_json");
  assert.equal(config.desktopRuntimeConfig.source, "user_data");
  assert.equal(config.desktopRuntimeConfig.legacy_sidecar_used, false);
  assert.deepEqual(config.runtimeMissing, ["desktop_runtime_config"]);
});

test("packaged Search accepts a validated adjacent schema 3 sidecar only as legacy compatibility", async () => {
  const fixture = join(FIXTURE, "legacy");
  const executablePath = join(fixture, "Search.exe");
  const resourcesPath = join(fixture, "resources");
  const dataDir = join(fixture, "legacy-data");
  const pythonExe = join(fixture, "python.exe");
  const nodeExe = join(fixture, "node.exe");
  await writeRuntimeFixture(join(resourcesPath, "app", "runtime-project"));
  await mkdir(dataDir, { recursive: true });
  await writeFile(executablePath, "", "utf8");
  await writeFile(pythonExe, "", "utf8");
  await writeFile(nodeExe, "", "utf8");
  await writeFile(join(fixture, "search-desktop.local.json"), JSON.stringify({
    schemaVersion: 3,
    dataDir,
    pythonExe,
    nodeExe,
    cloudflaredExe: "",
  }), "utf8");
  const config = resolveDesktopConfig({
    env: { PATH: "" },
    userDataPath: join(fixture, "roaming", "Search"),
    executablePath,
    resourcesPath,
    isPackaged: true,
    probeExecutable: executableProbe,
  });
  assert.equal(config.runtimeAvailable, true);
  assert.equal(config.desktopRuntimeConfig.source, "legacy_sidecar");
  assert.equal(config.desktopRuntimeConfig.legacy_sidecar_used, true);
  assert.equal(config.desktopRuntimeLog.legacy_sidecar_used, true);
});

async function writeRuntimeFixture(runtimeRoot) {
  const files = [
    "app/main.py",
    "scripts/runtime/notebook_ai_launcher.py",
    "config/retrieval_query_aliases.json",
    "integrations/notebook_ai_chatgpt_app/dist/server/index.js",
    "integrations/notebook_ai_chatgpt_app/web/dist/widget.html",
  ];
  for (const relativePath of files) {
    const path = join(runtimeRoot, ...relativePath.split("/"));
    await mkdir(resolve(path, ".."), { recursive: true });
    await writeFile(path, "fixture", "utf8");
  }
}

async function pathExists(path) {
  try { await import("node:fs/promises").then(({ access }) => access(path)); return true; } catch { return false; }
}

test.after(async () => {
  await rm(FIXTURE, { recursive: true, force: true });
});
