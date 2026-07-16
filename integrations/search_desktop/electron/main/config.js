import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const MODULE_DIR = dirname(fileURLToPath(import.meta.url));
export const DESKTOP_ROOT = resolve(MODULE_DIR, "../..");
export const SOURCE_PROJECT_ROOT = resolve(DESKTOP_ROOT, "../..");
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
  const projectRoot = resolveProjectRoot({ env, executablePath, isPackaged, localConfig });
  const pythonExe = resolve(env.NOTEBOOK_AI_PYTHON_EXE || localConfig.pythonExe || DEFAULT_PYTHON_EXE);
  const nodeExe = resolve(env.NOTEBOOK_AI_NODE_EXE || localConfig.nodeExe || DEFAULT_NODE_EXE);
  const runtimeScript = join(projectRoot, "scripts", "runtime", "notebook_ai_launcher.py");
  const packagedAssets = isPackaged ? join(resolve(resourcesPath), "search-assets") : null;
  const frontendDist = isPackaged
    ? join(packagedAssets, "frontend")
    : join(projectRoot, "frontend", "dist");
  const designSystemRoot = isPackaged
    ? join(packagedAssets, "design-system")
    : join(projectRoot, "packages", "search-design-system", "src");
  const backendUrl = validateLoopbackUrl(
    env.NOTEBOOK_AI_BACKEND_URL || "http://127.0.0.1:8000",
    "backend URL",
  );
  const settingsPath = userDataPath ? join(userDataPath, "search-desktop-settings.json") : null;
  return Object.freeze({
    productName: "Search",
    projectRoot,
    desktopRoot: DESKTOP_ROOT,
    pythonExe,
    nodeExe,
    runtimeScript,
    frontendDist,
    designSystemRoot,
    desktopIcon: join(DESKTOP_ROOT, "assets", "search.ico"),
    rendererAssets: join(DESKTOP_ROOT, "renderer"),
    rendererFallback: join(DESKTOP_ROOT, "renderer", "missing-build.html"),
    backendUrl,
    rendererPort: 5173,
    defaultRoute: "/retrieval",
    settingsPath,
    runtimeAvailable: existsSync(pythonExe) && existsSync(nodeExe) && existsSync(runtimeScript),
  });
}

function resolveProjectRoot({ env, executablePath, isPackaged, localConfig }) {
  const configured = String(env.NOTEBOOK_AI_PROJECT_ROOT || localConfig.projectRoot || "").trim();
  if (configured) return requireProjectRoot(resolve(configured));
  if (!isPackaged) return requireProjectRoot(SOURCE_PROJECT_ROOT);

  let candidate = dirname(resolve(executablePath));
  for (let depth = 0; depth < 8; depth += 1) {
    if (isProjectRoot(candidate)) return candidate;
    const parent = dirname(candidate);
    if (parent === candidate) break;
    candidate = parent;
  }
  throw new Error("search_project_root_unavailable");
}

function readLocalConfig(executablePath) {
  const path = join(dirname(resolve(executablePath)), "search-desktop.local.json");
  if (!existsSync(path)) return {};
  try {
    const value = JSON.parse(readFileSync(path, "utf8"));
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error();
    return {
      projectRoot: typeof value.projectRoot === "string" ? value.projectRoot : "",
      pythonExe: typeof value.pythonExe === "string" ? value.pythonExe : "",
      nodeExe: typeof value.nodeExe === "string" ? value.nodeExe : "",
    };
  } catch {
    throw new Error("search_desktop_local_config_invalid");
  }
}

function requireProjectRoot(path) {
  if (!isProjectRoot(path)) throw new Error("search_project_root_unavailable");
  return path;
}

function isProjectRoot(path) {
  return existsSync(join(path, "app", "main.py")) &&
    existsSync(join(path, "scripts", "runtime", "notebook_ai_launcher.py"));
}

export function validateLoopbackUrl(value, label = "URL") {
  const parsed = new URL(String(value));
  if (
    parsed.protocol !== "http:" ||
    !["127.0.0.1", "localhost"].includes(parsed.hostname) ||
    parsed.username ||
    parsed.password ||
    parsed.hash
  ) {
    throw new Error(`${label} must be an explicit loopback HTTP URL`);
  }
  return parsed.toString().replace(/\/$/, "");
}
