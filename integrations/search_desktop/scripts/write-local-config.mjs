import { existsSync } from "node:fs";
import { writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const DESKTOP_ROOT = resolve(SCRIPT_DIR, "..");
const PACKAGED_ROOT = resolve(commandArgument("--packaged-root") || join(DESKTOP_ROOT, "dist", "win-unpacked"));
const OUTPUT = join(PACKAGED_ROOT, "search-desktop.local.json");
const legacyDataRoot = commandArgument("--data-project-root")
  || process.env.NOTEBOOK_AI_DATA_PROJECT_ROOT;
const dataValue = commandArgument("--data-dir")
  || process.env.SEARCH_DATA_DIR
  || (legacyDataRoot ? join(legacyDataRoot, "data") : "");
const pythonValue = commandArgument("--python")
  || process.env.SEARCH_PYTHON
  || process.env.NOTEBOOK_AI_PYTHON_EXE;
const nodeValue = commandArgument("--node")
  || process.env.SEARCH_NODE
  || process.env.NOTEBOOK_AI_NODE_EXE;
const cloudflaredValue = commandArgument("--cloudflared")
  || process.env.SEARCH_CLOUDFLARED
  || "";
if (!dataValue || !pythonValue || !nodeValue) {
  throw new Error("search_desktop_local_config_requires_data_python_and_node");
}
const dataDir = resolve(dataValue);
const pythonExe = resolve(pythonValue);
const nodeExe = resolve(nodeValue);
const cloudflaredExe = cloudflaredValue ? resolve(cloudflaredValue) : "";

for (const required of [
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
  ...(cloudflaredExe ? [cloudflaredExe] : []),
]) {
  if (!existsSync(required)) throw new Error("search_desktop_local_config_target_unavailable");
}

await writeFile(
  OUTPUT,
  `${JSON.stringify({
    schemaVersion: 3,
    dataDir,
    pythonExe,
    nodeExe,
    cloudflaredExe,
  }, null, 2)}\n`,
  { encoding: "utf8", flag: process.argv.includes("--replace") ? "w" : "wx" },
);
console.log("Wrote Search Desktop local runtime configuration.");

function commandArgument(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : null;
}
