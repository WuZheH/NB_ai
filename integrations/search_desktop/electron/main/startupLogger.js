import { createHash } from "node:crypto";
import { appendFile, mkdir } from "node:fs/promises";
import { basename, dirname, join, normalize, resolve } from "node:path";
import { loadBuildIdentityForApp } from "./buildIdentity.js";

export const STARTUP_STAGE = Object.freeze({
  CONFIG_RESOLVED: "config_resolved",
  DESIGN_TOKENS_LOADED: "design_tokens_loaded",
  RENDERER_STARTED: "renderer_started",
  RUNTIME_CHECKED: "runtime_checked",
  WINDOW_CREATED: "window_created",
  TRAY_CREATED: "tray_created",
  READY: "ready",
});

export function createStartupLogger({
  userDataPath,
  version,
  buildId,
  isPackaged,
  resourcesPath,
  now = () => new Date(),
  makeDirectory = mkdir,
  append = appendFile,
}) {
  const logPath = join(userDataPath, "logs", "search-startup.log");
  let currentStage = "startup";
  let lastSuccessfulStage = null;

  async function write(event, details = {}) {
    const entry = {
      timestamp: now().toISOString(),
      version,
      buildId,
      isPackaged: Boolean(isPackaged),
      resourcesDirectory: basename(resourcesPath || "resources"),
      resourcesPathHash: hashPath(resourcesPath || "resources"),
      event,
      stage: currentStage,
      lastSuccessfulStage,
      ...sanitizeDetails(details),
    };
    try {
      await makeDirectory(dirname(logPath), { recursive: true });
      await append(logPath, `${JSON.stringify(entry)}\n`, "utf8");
      return true;
    } catch {
      return false;
    }
  }

  return Object.freeze({
    logPath,
    async startStage(stage, details = {}) {
      currentStage = stage;
      await write("stage_started", details);
    },
    async completeStage(stage, details = {}) {
      currentStage = stage;
      lastSuccessfulStage = stage;
      await write("stage_completed", details);
    },
    async failStage(stage, errorCode, details = {}) {
      currentStage = stage;
      await write("stage_failed", {
        ...details,
        result: "failed",
        error_code: stableErrorCode(errorCode),
      });
    },
    async recordFailure(error) {
      const normalized = normalizeError(error);
      await write("startup_failed", {
        errorName: normalized.name,
        error_code: normalized.errorCode,
      });
    },
    state() {
      return Object.freeze({ currentStage, lastSuccessfulStage });
    },
  });
}

export async function createStartupLoggerForApp(app, {
  buildIdentity,
  resourcesPath = process.resourcesPath,
} = {}) {
  const identity = buildIdentity || await loadBuildIdentityForApp(app);
  return createStartupLogger({
    userDataPath: app.getPath("userData"),
    version: identity.version,
    buildId: identity.build_id,
    isPackaged: app.isPackaged,
    resourcesPath,
  });
}

export async function reportStartupFailure({ error, startupLogger, app, consoleObject = console }) {
  try {
    consoleObject.error("[Search] startup failed", error);
  } catch {
    // stderr failures must not replace the original startup failure.
  }
  try {
    await startupLogger?.recordFailure(error);
  } catch {
    // Startup logging is best-effort and must never mask the original error.
  }
  app.exit(1);
}

function normalizeError(error) {
  if (error instanceof Error) {
    return {
      name: safeString(error.name, "Error"),
      errorCode: stableErrorCode(error.message),
    };
  }
  return {
    name: "Error",
    errorCode: stableErrorCode(error),
  };
}

function stableErrorCode(value) {
  const text = String(value || "desktop_startup_failed").trim();
  return /^[A-Za-z0-9_.:-]{1,128}$/.test(text) ? text : "desktop_startup_failed";
}

function hashPath(path) {
  const normalized = normalize(resolve(path)).toLowerCase();
  return createHash("sha256").update(normalized).digest("hex");
}

const SAFE_DETAIL_KEYS = new Set([
  "errorName",
  "result",
  "error_code",
  "config_source",
  "config_schema",
  "desktop_runtime_status",
  "runtime_available",
  "data_available",
  "missing_prerequisites",
  "legacy_sidecar_used",
  "data_path_hash",
  "python_basename",
  "node_basename",
  "launcher_spawned",
  "desktop_started_runtime",
  "runtime_owner",
]);

function sanitizeDetails(details) {
  if (!details || typeof details !== "object" || Array.isArray(details)) return {};
  return Object.fromEntries(
    Object.entries(details)
      .filter(([key]) => SAFE_DETAIL_KEYS.has(key))
      .map(([key, value]) => [key, sanitizeDetailValue(key, value)]),
  );
}

function sanitizeDetailValue(key, value) {
  if (key === "error_code") return stableErrorCode(value);
  if (key === "missing_prerequisites") {
    return Array.isArray(value)
      ? value.map((item) => stableErrorCode(item)).slice(0, 32)
      : [];
  }
  if (typeof value === "boolean" || value === null) return value;
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  const text = String(value ?? "").trim();
  if (key.endsWith("_hash")) return /^[0-9a-f]{64}$/i.test(text) ? text.toLowerCase() : null;
  if (key.endsWith("_basename")) return basename(text).slice(0, 255);
  return /^[A-Za-z0-9_.:-]{0,128}$/.test(text) ? text : "redacted";
}

function safeString(value, fallback) {
  const text = String(value ?? "").trim();
  return (text || fallback).slice(0, 32_768);
}
