import { existsSync, readFileSync } from "node:fs";
import { delimiter, dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const MODULE_DIR = dirname(fileURLToPath(import.meta.url));
export const DESKTOP_ROOT = resolve(MODULE_DIR, "../..");
export const SOURCE_PROJECT_ROOT = resolve(DESKTOP_ROOT, "../..");
export const PACKAGED_RUNTIME_DIRECTORY = "runtime-project";
export const DEFAULT_DATA_DIRECTORY_NAME = "data";

export function resolveDesktopConfig({
  env = process.env,
  userDataPath,
  executablePath = process.execPath,
  resourcesPath = process.resourcesPath,
  isPackaged = false,
} = {}) {
  const localConfig = isPackaged ? readLocalConfig(executablePath) : {};
  const runtimeRoot = resolveRuntimeRoot({ env, resourcesPath, isPackaged });
  const dataDir = resolveDataDir({ env, isPackaged, localConfig, userDataPath });
  const dataProjectRoot = dirname(dataDir);
  const pythonExe = resolveExecutable(
    env.SEARCH_PYTHON || env.NOTEBOOK_AI_PYTHON_EXE || localConfig.pythonExe,
    ["python.exe", "python"],
    env,
  );
  const nodeExe = resolveExecutable(
    env.SEARCH_NODE || env.NOTEBOOK_AI_NODE_EXE || localConfig.nodeExe,
    ["node.exe", "node"],
    env,
  );
  const cloudflaredExe = resolveExecutable(
    env.SEARCH_CLOUDFLARED || localConfig.cloudflaredExe,
    ["cloudflared.exe", "cloudflared"],
    env,
  );
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
    env.SEARCH_BACKEND_URL || env.NOTEBOOK_AI_BACKEND_URL || "http://127.0.0.1:8000",
    "backend URL",
  );
  const rendererPort = resolveRendererPort(env.SEARCH_RENDERER_PORT);
  const settingsPath = userDataPath ? join(userDataPath, "search-desktop-settings.json") : null;
  const requiredRuntimePaths = [
    ["python", pythonExe],
    ["node", nodeExe],
    ["runtime_launcher", runtimeScript],
    ["fastapi_app", join(runtimeRoot, "app", "main.py")],
    ["mcp_server", mcpServerEntry],
    ["mcp_widget", mcpWidget],
  ];
  const runtimeMissing = requiredRuntimePaths
    .filter(([, path]) => !path || !existsSync(path))
    .map(([label]) => label);
  return Object.freeze({
    productName: "Search",
    buildMode: isPackaged ? "packaged" : "development",
    buildIdentityPath: join(DESKTOP_ROOT, "package.json"),
    runtimeRoot,
    dataDir,
    dataProjectRoot,
    // Keep the public field for renderer/tests that still use the historical
    // name. It now denotes the stable data project, never the packaged code.
    projectRoot: dataProjectRoot,
    desktopRoot: DESKTOP_ROOT,
    pythonExe,
    nodeExe,
    cloudflaredExe,
    runtimeScript,
    mcpServerEntry,
    mcpWidget,
    frontendDist,
    designSystemRoot,
    desktopIcon: join(DESKTOP_ROOT, "assets", "search.ico"),
    rendererAssets: join(DESKTOP_ROOT, "renderer"),
    rendererFallback: join(DESKTOP_ROOT, "renderer", "missing-build.html"),
    backendUrl,
    rendererPort,
    defaultRoute: "/retrieval",
    settingsPath,
    dataAvailable: existsSync(dataDir),
    runtimeMissing,
    runtimeAvailable: runtimeMissing.length === 0,
  });
}

function resolveRuntimeRoot({ env, resourcesPath, isPackaged }) {
  const configured = String(
    env.SEARCH_RUNTIME_ROOT
      || env.NOTEBOOK_AI_RUNTIME_ROOT
      || (!isPackaged ? env.NOTEBOOK_AI_PROJECT_ROOT : "")
      || "",
  ).trim();
  const candidate = isPackaged
    ? join(resolve(resourcesPath), "app", PACKAGED_RUNTIME_DIRECTORY)
    : resolve(configured || SOURCE_PROJECT_ROOT);
  return requireRuntimeRoot(candidate);
}

function resolveDataDir({ env, isPackaged, localConfig, userDataPath }) {
  const direct = String(env.SEARCH_DATA_DIR || localConfig.dataDir || "").trim();
  if (direct) return resolveSafePath(direct, "SEARCH_DATA_DIR");

  const legacyRoot = String(
    env.NOTEBOOK_AI_DATA_PROJECT_ROOT
      || (!isPackaged ? env.NOTEBOOK_AI_PROJECT_ROOT : "")
      || localConfig.dataProjectRoot
      || "",
  ).trim();
  if (legacyRoot) {
    return join(resolveSafePath(legacyRoot, "NOTEBOOK_AI_DATA_PROJECT_ROOT"), "data");
  }
  if (!isPackaged) return join(SOURCE_PROJECT_ROOT, DEFAULT_DATA_DIRECTORY_NAME);

  const localAppData = String(env.LOCALAPPDATA || "").trim();
  if (localAppData) {
    return join(resolveSafePath(localAppData, "LOCALAPPDATA"), "Search", DEFAULT_DATA_DIRECTORY_NAME);
  }
  if (userDataPath) return join(resolve(userDataPath), DEFAULT_DATA_DIRECTORY_NAME);
  throw new Error("search_user_data_directory_unavailable");
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
      || ![2, 3].includes(value.schemaVersion)
    ) throw new Error();
    return {
      dataDir: typeof value.dataDir === "string" ? value.dataDir : "",
      dataProjectRoot: typeof value.dataProjectRoot === "string" ? value.dataProjectRoot : "",
      pythonExe: typeof value.pythonExe === "string" ? value.pythonExe : "",
      nodeExe: typeof value.nodeExe === "string" ? value.nodeExe : "",
      cloudflaredExe: typeof value.cloudflaredExe === "string" ? value.cloudflaredExe : "",
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

function resolveSafePath(value, label) {
  const cleaned = String(value || "").trim();
  if (!cleaned || cleaned.includes("\0")) throw new Error(`${label}_invalid`);
  return resolve(cleaned);
}

function resolveExecutable(configured, names, env) {
  const value = String(configured || "").trim();
  if (value) {
    if (value.includes("\0")) throw new Error("search_executable_path_invalid");
    if (isAbsolute(value) || /[\\/]/.test(value)) return resolve(value);
    return findOnPath(value, env) || resolve(value);
  }
  for (const name of names) {
    const candidate = findOnPath(name, env);
    if (candidate) return candidate;
  }
  return "";
}

function findOnPath(name, env) {
  const pathValue = String(env.Path || env.PATH || "");
  for (const directory of pathValue.split(delimiter).filter(Boolean)) {
    const candidate = join(directory.replace(/^"|"$/g, ""), name);
    if (existsSync(candidate)) return resolve(candidate);
  }
  return "";
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

export function resolveRendererPort(value) {
  const configured = String(value ?? "").trim();
  if (!configured) return 5173;
  if (!/^\d+$/.test(configured)) throw new Error("SEARCH_RENDERER_PORT_invalid");
  const port = Number(configured);
  if (!Number.isSafeInteger(port) || port < 1024 || port > 65535) {
    throw new Error("SEARCH_RENDERER_PORT_invalid");
  }
  return port;
}
