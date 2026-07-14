import assert from "node:assert/strict";
import { mkdir, rm, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import test from "node:test";
import { resolveDesktopConfig } from "../electron/main/config.js";

const ROOT = resolve(import.meta.dirname, "..");
const FIXTURE = join(ROOT, ".test-work-packaged-config");

test("packaged Search resolves runtime from its local sidecar and UI from resources", async () => {
  const projectRoot = join(FIXTURE, "project");
  const executablePath = join(FIXTURE, "Search.exe");
  const resourcesPath = join(FIXTURE, "resources");
  const pythonExe = join(FIXTURE, "python.exe");
  try {
    await mkdir(join(projectRoot, "app"), { recursive: true });
    await mkdir(join(projectRoot, "scripts", "runtime"), { recursive: true });
    await mkdir(join(resourcesPath, "search-assets", "frontend"), { recursive: true });
    await mkdir(join(resourcesPath, "search-assets", "design-system"), { recursive: true });
    await writeFile(join(projectRoot, "app", "main.py"), "", "utf8");
    await writeFile(join(projectRoot, "scripts", "runtime", "notebook_ai_launcher.py"), "", "utf8");
    await writeFile(executablePath, "", "utf8");
    await writeFile(pythonExe, "", "utf8");
    await writeFile(
      join(FIXTURE, "search-desktop.local.json"),
      JSON.stringify({ schemaVersion: 1, projectRoot, pythonExe }),
      "utf8",
    );

    const config = resolveDesktopConfig({
      env: {},
      executablePath,
      resourcesPath,
      isPackaged: true,
    });
    assert.equal(config.projectRoot, projectRoot);
    assert.equal(config.pythonExe, pythonExe);
    assert.equal(config.frontendDist, join(resourcesPath, "search-assets", "frontend"));
    assert.equal(config.designSystemRoot, join(resourcesPath, "search-assets", "design-system"));
  } finally {
    await rm(FIXTURE, { recursive: true, force: true });
  }
});
