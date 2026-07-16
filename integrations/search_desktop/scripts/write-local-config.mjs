import { existsSync } from "node:fs";
import { writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const DESKTOP_ROOT = resolve(SCRIPT_DIR, "..");
const PROJECT_ROOT = resolve(DESKTOP_ROOT, "../..");
const PACKAGED_ROOT = resolve(commandArgument("--packaged-root") || join(DESKTOP_ROOT, "dist", "win-unpacked"));
const OUTPUT = join(PACKAGED_ROOT, "search-desktop.local.json");
const DEFAULT_PYTHON_EXE = "D:\\LEARNING\\Tools\\ANACONDA\\envs\\NOTEBOOK_AI\\python.exe";
const DEFAULT_NODE_EXE = "D:\\LEARNING\\Tools\\node.js\\node.exe";
const pythonExe = resolve(process.env.NOTEBOOK_AI_PYTHON_EXE || DEFAULT_PYTHON_EXE);
const nodeExe = resolve(process.env.NOTEBOOK_AI_NODE_EXE || DEFAULT_NODE_EXE);

for (const required of [
  resolve(PROJECT_ROOT, "app", "main.py"),
  resolve(PROJECT_ROOT, "scripts", "runtime", "notebook_ai_launcher.py"),
  pythonExe,
  nodeExe,
]) {
  if (!existsSync(required)) throw new Error("search_desktop_local_config_target_unavailable");
}

await writeFile(
  OUTPUT,
  `${JSON.stringify({ schemaVersion: 1, projectRoot: PROJECT_ROOT, pythonExe, nodeExe }, null, 2)}\n`,
  { encoding: "utf8", flag: "w" },
);
console.log("Wrote Search Desktop local runtime configuration.");

function commandArgument(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : null;
}
