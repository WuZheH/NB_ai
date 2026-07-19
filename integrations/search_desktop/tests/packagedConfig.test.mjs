import assert from "node:assert/strict";
import { mkdir, rm, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import test from "node:test";
import {
  resolveDesktopConfig,
} from "../electron/main/config.js";

const ROOT = resolve(import.meta.dirname, "..");
const FIXTURE = join(ROOT, ".test-work-packaged-config");

test("packaged Search resolves code from resources/app and data from the stable sidecar", async () => {
  const executablePath = join(FIXTURE, "Search.exe");
  const resourcesPath = join(FIXTURE, "resources");
  const runtimeRoot = join(resourcesPath, "app", "runtime-project");
  const dataDir = join(FIXTURE, "portable-user-data", "data");
  const pythonExe = join(FIXTURE, "python.exe");
  const nodeExe = join(FIXTURE, "node.exe");
  try {
    await writeRuntimeFixture(runtimeRoot);
    await mkdir(join(resourcesPath, "search-assets", "frontend"), { recursive: true });
    await mkdir(join(resourcesPath, "search-assets", "design-system"), { recursive: true });
    await writeFile(executablePath, "", "utf8");
    await writeFile(pythonExe, "", "utf8");
    await writeFile(nodeExe, "", "utf8");
    await writeFile(
      join(FIXTURE, "search-desktop.local.json"),
      JSON.stringify({
        schemaVersion: 3,
        dataDir,
        pythonExe,
        nodeExe,
      }),
      "utf8",
    );

    const config = resolveDesktopConfig({
      env: { PATH: "", NOTEBOOK_AI_PROJECT_ROOT: join(FIXTURE, "ignored-development-root") },
      userDataPath: join(FIXTURE, "roaming", "Search"),
      executablePath,
      resourcesPath,
      isPackaged: true,
    });
    assert.equal(config.runtimeRoot, runtimeRoot);
    assert.equal(config.machineConfigPath, join(FIXTURE, "roaming", "Search", "machine-config.json"));
    assert.equal(config.dataDir, resolve(dataDir));
    assert.equal(config.dataProjectRoot, resolve(dataDir, ".."));
    assert.equal(config.projectRoot, config.dataProjectRoot);
    assert.equal(config.pythonExe, pythonExe);
    assert.equal(config.nodeExe, nodeExe);
    assert.equal(config.frontendDist, join(resourcesPath, "search-assets", "frontend"));
    assert.equal(config.designSystemRoot, join(resourcesPath, "search-assets", "design-system"));
    assert.equal(config.runtimeAvailable, true);
    assert.equal(config.dataAvailable, false);
    assert.deepEqual(config.runtimeMissing, []);
  } finally {
    await rm(FIXTURE, { recursive: true, force: true });
  }
});

test("packaged Search starts with an empty LOCALAPPDATA data directory", async () => {
  const executablePath = join(FIXTURE, "Search.exe");
  const resourcesPath = join(FIXTURE, "resources");
  try {
    await writeRuntimeFixture(join(resourcesPath, "app", "runtime-project"));
    await writeFile(executablePath, "", "utf8");
    const localAppData = join(FIXTURE, "local-app-data");
    const config = resolveDesktopConfig({
      env: { LOCALAPPDATA: localAppData, PATH: "" },
      userDataPath: join(FIXTURE, "roaming", "Search"),
      executablePath,
      resourcesPath,
      isPackaged: true,
    });
    assert.equal(config.dataDir, join(localAppData, "Search", "data"));
    assert.equal(config.dataAvailable, false);
    assert.deepEqual(config.runtimeMissing, ["python", "node"]);
  } finally {
    await rm(FIXTURE, { recursive: true, force: true });
  }
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
