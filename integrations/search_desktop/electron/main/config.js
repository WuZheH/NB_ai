import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const MODULE_DIR = dirname(fileURLToPath(import.meta.url));
export const DESKTOP_ROOT = resolve(MODULE_DIR, "../..");
export const SOURCE_PROJECT_ROOT = resolve(DESKTOP_ROOT, "../..");
export const PACKAGED_RUNTIME_DIRECTORY = "runtime-project";
export const DEFAULT_DATA_PROJECT_ROOT = "D:\\LEARNING\\Tools\\notebook_ai";
export const DEFAULT_PYTHON_EXE =
  "D:\\LEARNING\\Tools\\ANACONDA\\envs\\NOTEBOOK_AI\\python.exe";
export const DEFAULT_NODE_EXE = "D:\\LEARNING\\Tools\\node.js\\node.exe";

export function resolveDesktopConfig({
  env = process.env,
  userDataPath,
  executablePath = process.execPath,
  resourcesPath = process.resourcesPath,
  isPackaged = false,
} = {}) {
  const localConfig = isPackaged ? readLocalConfig(executablePath) : {};
  const runtimeRoot = resolveRuntimeRoot({ env, resourcesPath, isPackaged });
  const dataProjectRoot = resolveDataProjectRoot({ env, isPackaged, localConfig });
  const pythonExe = resolve(env.NOTEBOOK_AI_PYTHON_EXE || localConfig.pythonExe || DEFAULT_PYTHON_EXE);
  const nodeExe = resolve(env.NOTEBOOK_AI_NODE_EXE || localConfig.nodeExe || DEFAULT_NODE_EXE);
  const runtimeScript = join(runtimeRoot, "scripts", "runtime", "notebook_ai_launcher.py");
  const mcpServerEntry = join(
    runtimeRoot,
    "integrations",
    "notebook_ai_chatgpt_app",
    "dist",
    "server",
    "index.js",
  );
  const mcpWidget = join(
    runtimeRoot,
    "integrations",
    "notebook_ai_chatgpt_app",
    "web",
    "dist",
    "widget.html",
  );
  const packagedAssets = isPackaged ? join(resolve(resourcesPath), "search-assets") : null;
  const frontendDist = isPackaged
    ? join(packagedAssets, "frontend")
    : join(runtimeRoot, "frontend", "dist");
  const designSystemRoot = isPackaged
    ? join(packagedAssets, "design-system")
    : join(runtimeRoot, "packages", "search-design-system", "src");
  const backendUrl = validateLoopbackUrl(
    env.NOTEBOOK_AI_BACKEND_URL || "http://127.0.0.1:8000",
    "backend URL",
  );
  const settingsPath = userDataPath ? join(userDataPath, "search-desktop-settings.json") : null;
  return Object.freeze({
    productName: "Search",
    runtimeRoot,
    dataProjectRoot,
    // Keep the public field for renderer/tests that still use the historical
    // name. It now denotes the stable data project, never the packaged code.
    projectRoot: dataProjectRoot,
    desktopRoot: DESKTOP_ROOT,
    pythonExe,
    nodeExe,
    runtimeScript,
    mcpServerEntry,
    mcpWidget,
    frontendDist,
    designSystemRoot,
    desktopIcon: join(DESKTOP_ROOT, "assets", "search.ico"),
    rendererAssets: join(DESKTOP_ROOT, "renderer"),
    rendererFallback: join(DESKTOP_ROOT, "renderer", "missing-build.html"),
    backendUrl,
    rendererPort: 5173,
    defaultRoute: "/retrieval",
    settingsPath,
    runtimeAvailable: [
      pythonExe,
      nodeExe,
      runtimeScript,
      join(runtimeRoot, "app", "main.py"),
      mcpServerEntry,
      mcpWidget,
    ].every((path) => existsSync(path)),
  });
}

function resolveRuntimeRoot({ env, resourcesPath, isPackaged }) {
  const configured = String(
    env.NOTEBOOK_AI_RUNTIME_ROOT || (!isPackaged ? env.NOTEBOOK_AI_PROJECT_ROOT : "") || "",
  ).trim();
  const candidate = isPackaged
    ? join(resolve(resourcesPath), "app", PACKAGED_RUNTIME_DIRECTORY)
    : resolve(configured || SOURCE_PROJECT_ROOT);
  return requireRuntimeRoot(candidate);
}

function resolveDataProjectRoot({ env, isPackaged, localConfig }) {
  const configured = String(
    env.NOTEBOOK_AI_DATA_PROJECT_ROOT
      || (!isPackaged ? env.NOTEBOOK_AI_PROJECT_ROOT : "")
      || localConfig.dataProjectRoot
      || (!isPackaged ? SOURCE_PROJECT_ROOT : DEFAULT_DATA_PROJECT_ROOT),
  ).trim();
  const candidate = resolve(configured);
  if (isPackaged && isWorktreePath(candidate)) {
    throw new Error("search_data_project_root_must_be_stable");
  }
  if (!existsSync(join(candidate, "data"))) {
    throw new Error("search_data_project_root_unavailable");
  }
  return candidate;
}

function readLocalConfig(executablePath) {
  const path = join(dirname(resolve(executablePath)), "search-desktop.local.json");
  if (!existsSync(path)) return {};
  try {
    const value = JSON.parse(readFileSync(path, "utf8"));
    if (
      !value
      || typeof value !== "object"
      || Array.isArray(value)
      || value.schemaVersion !== 2
    ) throw new Error();
    return {
      dataProjectRoot: typeof value.dataProjectRoot === "string" ? value.dataProjectRoot : "",
      pythonExe: typeof value.pythonExe === "string" ? value.pythonExe : "",
      nodeExe: typeof value.nodeExe === "string" ? value.nodeExe : "",
    };
  } catch {
    throw new Error("search_desktop_local_config_invalid");
  }
}

function requireRuntimeRoot(path) {
  if (!isRuntimeRoot(path)) throw new Error("search_packaged_runtime_unavailable");
  return resolve(path);
}

function isRuntimeRoot(path) {
  return [
    join(path, "app", "main.py"),
    join(path, "scripts", "runtime", "notebook_ai_launcher.py"),
    join(path, "config", "retrieval_query_aliases.json"),
    join(path, "integrations", "notebook_ai_chatgpt_app", "dist", "server", "index.js"),
    join(path, "integrations", "notebook_ai_chatgpt_app", "web", "dist", "widget.html"),
  ].every((candidate) => existsSync(candidate));
}

function isWorktreePath(path) {
  return resolve(path).toLowerCase().split(/[\\/]+/).includes("notebook_ai_worktrees");
}

export function validateLoopbackUrl(value, label = "URL") {
  const parsed = new URL(String(value));
  if (
    parsed.protocol !== "http:"
    || !["127.0.0.1", "localhost"].includes(parsed.hostname)
    || parsed.username
    || parsed.password
    || parsed.hash
  ) {
    throw new Error(`${label} must be an explicit loopback HTTP URL`);
  }
  return parsed.toString().replace(/\/$/, "");
}
