import { existsSync } from "node:fs";
import { writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const DESKTOP_ROOT = resolve(SCRIPT_DIR, "..");
const PROJECT_ROOT = resolve(DESKTOP_ROOT, "../..");
const OUTPUT = resolve(DESKTOP_ROOT, "dist", "win-unpacked", "search-desktop.local.json");
const DEFAULT_PYTHON_EXE = "D:\\LEARNING\\Tools\\ANACONDA\\envs\\NOTEBOOK_AI\\python.exe";
const pythonExe = resolve(process.env.NOTEBOOK_AI_PYTHON_EXE || DEFAULT_PYTHON_EXE);

for (const required of [
  resolve(PROJECT_ROOT, "app", "main.py"),
  resolve(PROJECT_ROOT, "scripts", "runtime", "notebook_ai_launcher.py"),
  pythonExe,
]) {
  if (!existsSync(required)) throw new Error("search_desktop_local_config_target_unavailable");
}

await writeFile(
  OUTPUT,
  `${JSON.stringify({ schemaVersion: 1, projectRoot: PROJECT_ROOT, pythonExe }, null, 2)}\n`,
  { encoding: "utf8", flag: "w" },
);
console.log("Wrote Search Desktop local runtime configuration.");
