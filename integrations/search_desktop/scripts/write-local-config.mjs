import { existsSync } from "node:fs";
import { writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const DESKTOP_ROOT = resolve(SCRIPT_DIR, "..");
const PACKAGED_ROOT = resolve(commandArgument("--packaged-root") || join(DESKTOP_ROOT, "dist", "win-unpacked"));
const OUTPUT = join(PACKAGED_ROOT, "search-desktop.local.json");
const DEFAULT_DATA_PROJECT_ROOT = "D:\\LEARNING\\Tools\\notebook_ai";
const DEFAULT_PYTHON_EXE = "D:\\LEARNING\\Tools\\ANACONDA\\envs\\NOTEBOOK_AI\\python.exe";
const DEFAULT_NODE_EXE = "D:\\LEARNING\\Tools\\node.js\\node.exe";
const dataProjectRoot = resolve(
  commandArgument("--data-project-root")
    || process.env.NOTEBOOK_AI_DATA_PROJECT_ROOT
    || DEFAULT_DATA_PROJECT_ROOT,
);
const pythonExe = resolve(process.env.NOTEBOOK_AI_PYTHON_EXE || DEFAULT_PYTHON_EXE);
const nodeExe = resolve(process.env.NOTEBOOK_AI_NODE_EXE || DEFAULT_NODE_EXE);

for (const required of [
  resolve(dataProjectRoot, "data"),
  resolve(PACKAGED_ROOT, "resources", "app", "runtime-project", "app", "main.py"),
  resolve(
    PACKAGED_ROOT,
    "resources",
    "app",
    "runtime-project",
    "scripts",
    "runtime",
    "notebook_ai_launcher.py",
  ),
  pythonExe,
  nodeExe,
]) {
  if (!existsSync(required)) throw new Error("search_desktop_local_config_target_unavailable");
}
if (dataProjectRoot.toLowerCase().split(/[\\/]+/).includes("notebook_ai_worktrees")) {
  throw new Error("search_desktop_data_project_root_must_be_stable");
}

await writeFile(
  OUTPUT,
  `${JSON.stringify({ schemaVersion: 2, dataProjectRoot, pythonExe, nodeExe }, null, 2)}\n`,
  { encoding: "utf8", flag: "w" },
);
console.log("Wrote Search Desktop local runtime configuration.");

function commandArgument(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : null;
}
