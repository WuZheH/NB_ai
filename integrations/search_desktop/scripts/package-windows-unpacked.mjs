import { spawn } from "node:child_process";
import { rm } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  DEFAULT_PACKAGED_ROOT,
  verifyPackagedResources,
  verifySourceResources,
} from "./verify-packaged-resources.mjs";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const DESKTOP_ROOT = resolve(SCRIPT_DIR, "..");
const MCP_ROOT = resolve(DESKTOP_ROOT, "../notebook_ai_chatgpt_app");
const FINAL_EXECUTABLE = join(DEFAULT_PACKAGED_ROOT, "Search.exe");

export async function packageWindowsUnpacked() {
  try {
    await runNode(join(MCP_ROOT, "scripts", "build-widget.mjs"));
    await runNode(join(MCP_ROOT, "scripts", "build-server.mjs"));
    await verifySourceResources();
    await runNode(join(DESKTOP_ROOT, "node_modules", "electron-builder", "cli.js"), [
      "--win",
      "--x64",
      "--dir",
    ]);
    await verifyPackagedResources();
    await runNode(join(SCRIPT_DIR, "finalize-windows-exe.mjs"));
    await runNode(join(SCRIPT_DIR, "write-local-config.mjs"));
    await verifyPackagedResources();
  } catch (error) {
    await invalidatePackagedExecutable();
    throw error;
  }
}

export async function invalidatePackagedExecutable(executable = FINAL_EXECUTABLE) {
  await rm(resolve(executable), { force: true });
}

function runNode(script, args = []) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(process.execPath, [script, ...args], {
      cwd: DESKTOP_ROOT,
      shell: false,
      stdio: "inherit",
      windowsHide: true,
    });
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      if (code === 0) resolvePromise();
      else reject(new Error(`search_packaging_step_failed:${code ?? signal ?? "unknown"}`));
    });
  });
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  packageWindowsUnpacked().catch((error) => {
    console.error("[Search Desktop] Windows packaging failed", error);
    process.exitCode = 1;
  });
}
