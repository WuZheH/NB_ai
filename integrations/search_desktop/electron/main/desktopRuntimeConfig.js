import { createHash, randomUUID } from "node:crypto";
import {
  closeSync,
  copyFileSync,
  existsSync,
  fsyncSync,
  mkdirSync,
  openSync,
  readFileSync,
  realpathSync,
  renameSync,
  statSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { basename, dirname, isAbsolute, join, normalize, resolve } from "node:path";
import { spawnSync } from "node:child_process";

export const DESKTOP_RUNTIME_CONFIG_FILE = "desktop-runtime.json";
export const DESKTOP_RUNTIME_SCHEMA_VERSION = 1;
export const LEGACY_DESKTOP_RUNTIME_CONFIG_FILE = "search-desktop.local.json";

export const DESKTOP_RUNTIME_STATUS = Object.freeze({
  MISSING: "desktop_runtime_config_missing",
  INVALID_JSON: "desktop_runtime_config_invalid_json",
  SCHEMA_UNSUPPORTED: "desktop_runtime_schema_unsupported",
  REQUIRED_FIELD_MISSING: "desktop_runtime_required_field_missing",
  PATH_NOT_ABSOLUTE: "desktop_runtime_path_not_absolute",
  DATA_DIR_MISSING: "desktop_runtime_data_dir_missing",
  PYTHON_MISSING: "desktop_runtime_python_missing",
  NODE_MISSING: "desktop_runtime_node_missing",
  READY: "desktop_runtime_ready",
});

const ALLOWED_FIELDS = new Set(["schemaVersion", "dataDir", "pythonExe", "nodeExe"]);
const LEGACY_ALLOWED_FIELDS = new Set([
  "schemaVersion",
  "dataDir",
  "dataProjectRoot",
  "pythonExe",
  "nodeExe",
  "cloudflaredExe",
]);

export function resolvePackagedDesktopRuntimeConfig({
  userDataPath,
  executablePath,
  probeExecutable = defaultExecutableProbe,
} = {}) {
  if (!isNonemptyString(userDataPath)) {
    return unavailableConfig(DESKTOP_RUNTIME_STATUS.MISSING, {
      source: "none",
      configPath: "",
      missingPrerequisites: ["desktop_runtime_config"],
    });
  }
  const configPath = join(resolve(userDataPath), DESKTOP_RUNTIME_CONFIG_FILE);
  if (existsSync(configPath)) {
    return loadDesktopRuntimeConfig(configPath, {
      source: "user_data",
      probeExecutable,
    });
  }

  const legacyPath = isNonemptyString(executablePath)
    ? join(dirname(resolve(executablePath)), LEGACY_DESKTOP_RUNTIME_CONFIG_FILE)
    : "";
  if (existsSync(legacyPath)) {
    return loadLegacyDesktopRuntimeConfig(legacyPath, { probeExecutable });
  }

  return unavailableConfig(DESKTOP_RUNTIME_STATUS.MISSING, {
    source: "none",
    configPath,
    missingPrerequisites: ["desktop_runtime_config"],
  });
}

export function loadDesktopRuntimeConfig(path, {
  source = "user_data",
  probeExecutable = defaultExecutableProbe,
} = {}) {
  const configPath = resolve(path);
  if (!existsSync(configPath)) {
    return unavailableConfig(DESKTOP_RUNTIME_STATUS.MISSING, {
      source: "none",
      configPath,
      missingPrerequisites: ["desktop_runtime_config"],
    });
  }
  let value;
  try {
    value = JSON.parse(readFileSync(configPath, "utf8"));
  } catch {
    return unavailableConfig(DESKTOP_RUNTIME_STATUS.INVALID_JSON, { source, configPath });
  }
  return validateDesktopRuntimePayload(value, { source, configPath, probeExecutable });
}

export function validateDesktopRuntimePayload(value, {
  source = "user_data",
  configPath = "",
  probeExecutable = defaultExecutableProbe,
} = {}) {
  if (!isPlainObject(value) || Object.keys(value).some((key) => !ALLOWED_FIELDS.has(key))) {
    return unavailableConfig(DESKTOP_RUNTIME_STATUS.INVALID_JSON, { source, configPath });
  }
  if (value.schemaVersion !== DESKTOP_RUNTIME_SCHEMA_VERSION) {
    return unavailableConfig(DESKTOP_RUNTIME_STATUS.SCHEMA_UNSUPPORTED, {
      source,
      configPath,
      schemaVersion: Number.isInteger(value.schemaVersion) ? value.schemaVersion : null,
    });
  }
  if (![value.dataDir, value.pythonExe, value.nodeExe].every(isNonemptyString)) {
    return unavailableConfig(DESKTOP_RUNTIME_STATUS.REQUIRED_FIELD_MISSING, {
      source,
      configPath,
      schemaVersion: DESKTOP_RUNTIME_SCHEMA_VERSION,
    });
  }
  if (![value.dataDir, value.pythonExe, value.nodeExe].every((item) => isAbsolute(item.trim()))) {
    return unavailableConfig(DESKTOP_RUNTIME_STATUS.PATH_NOT_ABSOLUTE, {
      source,
      configPath,
      schemaVersion: DESKTOP_RUNTIME_SCHEMA_VERSION,
    });
  }

  const dataDir = resolve(value.dataDir.trim());
  const pythonExe = resolve(value.pythonExe.trim());
  const nodeExe = resolve(value.nodeExe.trim());
  if (
    isForbiddenDataDirectory(dataDir)
    || !isDirectory(dataDir)
    || isForbiddenDataDirectory(realPath(dataDir))
  ) {
    return unavailableConfig(DESKTOP_RUNTIME_STATUS.DATA_DIR_MISSING, {
      source,
      configPath,
      schemaVersion: DESKTOP_RUNTIME_SCHEMA_VERSION,
      missingPrerequisites: ["data_dir"],
    });
  }
  if (!isFile(pythonExe) || !probeExecutable(pythonExe, "python")) {
    return unavailableConfig(DESKTOP_RUNTIME_STATUS.PYTHON_MISSING, {
      source,
      configPath,
      schemaVersion: DESKTOP_RUNTIME_SCHEMA_VERSION,
      missingPrerequisites: ["python"],
    });
  }
  if (!isFile(nodeExe) || !probeExecutable(nodeExe, "node")) {
    return unavailableConfig(DESKTOP_RUNTIME_STATUS.NODE_MISSING, {
      source,
      configPath,
      schemaVersion: DESKTOP_RUNTIME_SCHEMA_VERSION,
      missingPrerequisites: ["node"],
    });
  }
  return Object.freeze({
    ready: true,
    status: DESKTOP_RUNTIME_STATUS.READY,
    errorCode: null,
    source,
    schemaVersion: DESKTOP_RUNTIME_SCHEMA_VERSION,
    configPath,
    dataDir,
    pythonExe,
    nodeExe,
    missingPrerequisites: Object.freeze([]),
    legacySidecarUsed: source === "legacy_sidecar",
  });
}

export function loadLegacyDesktopRuntimeConfig(path, {
  probeExecutable = defaultExecutableProbe,
} = {}) {
  const configPath = resolve(path);
  let value;
  try {
    value = JSON.parse(readFileSync(configPath, "utf8"));
  } catch {
    return unavailableConfig(DESKTOP_RUNTIME_STATUS.INVALID_JSON, {
      source: "legacy_sidecar",
      configPath,
    });
  }
  if (!isPlainObject(value) || Object.keys(value).some((key) => !LEGACY_ALLOWED_FIELDS.has(key))) {
    return unavailableConfig(DESKTOP_RUNTIME_STATUS.INVALID_JSON, {
      source: "legacy_sidecar",
      configPath,
    });
  }
  if (![2, 3].includes(value.schemaVersion)) {
    return unavailableConfig(DESKTOP_RUNTIME_STATUS.SCHEMA_UNSUPPORTED, {
      source: "legacy_sidecar",
      configPath,
      schemaVersion: Number.isInteger(value.schemaVersion) ? value.schemaVersion : null,
    });
  }
  const dataDir = isNonemptyString(value.dataDir)
    ? value.dataDir
    : isNonemptyString(value.dataProjectRoot)
      ? join(value.dataProjectRoot, "data")
      : "";
  return validateDesktopRuntimePayload({
    schemaVersion: DESKTOP_RUNTIME_SCHEMA_VERSION,
    dataDir,
    pythonExe: value.pythonExe,
    nodeExe: value.nodeExe,
  }, {
    source: "legacy_sidecar",
    configPath,
    probeExecutable,
  });
}

export function desktopRuntimeLogSummary(config) {
  return Object.freeze({
    config_source: config.source,
    config_schema: config.schemaVersion,
    desktop_runtime_status: config.status,
    runtime_available: Boolean(config.ready),
    data_available: Boolean(config.ready && isDirectory(config.dataDir)),
    missing_prerequisites: [...(config.missingPrerequisites || [])],
    legacy_sidecar_used: Boolean(config.legacySidecarUsed),
    data_path_hash: config.dataDir ? hashPath(config.dataDir) : null,
    python_basename: config.pythonExe ? basename(config.pythonExe) : null,
    node_basename: config.nodeExe ? basename(config.nodeExe) : null,
  });
}

export function publicDesktopRuntimeConfig(config) {
  return Object.freeze({
    status: config.status,
    error_code: config.errorCode,
    ready: Boolean(config.ready),
    source: config.source,
    schema_version: config.schemaVersion,
    legacy_sidecar_used: Boolean(config.legacySidecarUsed),
    missing_prerequisites: [...(config.missingPrerequisites || [])],
    data: config.dataDir ? { configured: true, directory_name: basename(config.dataDir), path_hash: hashPath(config.dataDir) } : { configured: false },
    python: config.pythonExe ? { configured: true, basename: basename(config.pythonExe), path_hash: hashPath(config.pythonExe) } : { configured: false },
    node: config.nodeExe ? { configured: true, basename: basename(config.nodeExe), path_hash: hashPath(config.nodeExe) } : { configured: false },
  });
}

export function writeDesktopRuntimeConfig(path, payload, {
  probeExecutable = defaultExecutableProbe,
} = {}) {
  const destination = requireAbsoluteConfigPath(path);
  assertExistingConfigCanBeReplaced(destination, { probeExecutable });
  const validation = validateDesktopRuntimePayload(payload, {
    source: "user_data",
    configPath: destination,
    probeExecutable,
  });
  if (!validation.ready) throw new Error(validation.errorCode);
  mkdirSync(dirname(destination), { recursive: true });
  if (existsSync(destination)) backupDesktopRuntimeConfig(destination);
  atomicWriteJson(destination, {
    schemaVersion: DESKTOP_RUNTIME_SCHEMA_VERSION,
    dataDir: validation.dataDir,
    pythonExe: validation.pythonExe,
    nodeExe: validation.nodeExe,
  });
  return loadDesktopRuntimeConfig(destination, { probeExecutable });
}

export function backupDesktopRuntimeConfig(path, { backupPath } = {}) {
  const source = requireAbsoluteConfigPath(path);
  if (!isFile(source)) throw new Error(DESKTOP_RUNTIME_STATUS.MISSING);
  const destination = resolve(backupPath || `${source}.bak`);
  mkdirSync(dirname(destination), { recursive: true });
  const temporary = join(dirname(destination), `.${basename(destination)}.${randomUUID()}.tmp`);
  try {
    copyFileSync(source, temporary);
    renameSync(temporary, destination);
  } finally {
    if (existsSync(temporary)) unlinkSync(temporary);
  }
  return destination;
}

export function migrateLegacyDesktopRuntimeConfig({
  legacyPath,
  destinationPath,
  probeExecutable = defaultExecutableProbe,
} = {}) {
  const source = requireAbsoluteConfigPath(legacyPath);
  const destination = requireAbsoluteConfigPath(destinationPath);
  const legacy = loadLegacyDesktopRuntimeConfig(source, { probeExecutable });
  if (!legacy.ready) throw new Error(legacy.errorCode);
  assertExistingConfigCanBeReplaced(destination, { probeExecutable });
  mkdirSync(dirname(destination), { recursive: true });
  const legacyBackup = join(dirname(destination), "desktop-runtime.legacy-sidecar.bak.json");
  backupDesktopRuntimeConfig(source, { backupPath: legacyBackup });
  const written = writeDesktopRuntimeConfig(destination, {
    schemaVersion: DESKTOP_RUNTIME_SCHEMA_VERSION,
    dataDir: legacy.dataDir,
    pythonExe: legacy.pythonExe,
    nodeExe: legacy.nodeExe,
  }, { probeExecutable });
  return Object.freeze({ config: written, legacyBackup });
}

export function defaultExecutableProbe(path, role) {
  try {
    const result = spawnSync(path, ["--version"], {
      encoding: "utf8",
      timeout: 10_000,
      windowsHide: true,
      shell: false,
    });
    const version = `${result.stdout || ""}${result.stderr || ""}`.trim();
    const expected = role === "python" ? /^Python\s+3\.\d+/i : /^v\d+\.\d+/;
    return !result.error && result.status === 0 && expected.test(version);
  } catch {
    return false;
  }
}

function unavailableConfig(errorCode, {
  source,
  configPath,
  schemaVersion = null,
  missingPrerequisites = ["desktop_runtime_config"],
} = {}) {
  return Object.freeze({
    ready: false,
    status: errorCode,
    errorCode,
    source,
    schemaVersion,
    configPath,
    dataDir: "",
    pythonExe: "",
    nodeExe: "",
    missingPrerequisites: Object.freeze([...missingPrerequisites]),
    legacySidecarUsed: false,
  });
}

function assertExistingConfigCanBeReplaced(path, { probeExecutable }) {
  if (!existsSync(path)) return;
  const existing = loadDesktopRuntimeConfig(path, { probeExecutable });
  if (!existing.ready) throw new Error(existing.errorCode);
}

function atomicWriteJson(path, payload) {
  const temporary = join(dirname(path), `.${basename(path)}.${randomUUID()}.tmp`);
  let descriptor;
  try {
    descriptor = openSync(temporary, "wx", 0o600);
    writeFileSync(descriptor, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
    fsyncSync(descriptor);
    closeSync(descriptor);
    descriptor = undefined;
    renameSync(temporary, path);
  } finally {
    if (descriptor !== undefined) closeSync(descriptor);
    if (existsSync(temporary)) unlinkSync(temporary);
  }
}

function isForbiddenDataDirectory(path) {
  const normalized = normalize(path).replaceAll("\\", "/").toLowerCase();
  return normalized.includes("/notebook_ai/")
    || normalized.endsWith("/notebook_ai")
    || normalized.includes("/dist-candidates/")
    || normalized.includes("/searchpackagesmoke/")
    || normalized.includes("/dist/formal/")
    || normalized.endsWith("/dist/formal")
    || normalized.includes("/integrations/search_desktop/dist/")
    || normalized.includes("/win-unpacked/");
}

function requireAbsoluteConfigPath(path) {
  if (!isNonemptyString(path) || !isAbsolute(path.trim())) {
    throw new Error(DESKTOP_RUNTIME_STATUS.PATH_NOT_ABSOLUTE);
  }
  return resolve(path.trim());
}

function isDirectory(path) {
  try { return statSync(path).isDirectory(); } catch { return false; }
}

function isFile(path) {
  try { return statSync(path).isFile(); } catch { return false; }
}

function realPath(path) {
  try { return realpathSync.native(path); } catch { return path; }
}

function isPlainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isNonemptyString(value) {
  return typeof value === "string" && Boolean(value.trim()) && !value.includes("\0");
}

function hashPath(path) {
  return createHash("sha256").update(normalize(resolve(path)).toLowerCase()).digest("hex");
}
