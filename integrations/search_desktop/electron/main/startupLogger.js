import { appendFile, mkdir, readFile } from "node:fs/promises";
import { dirname, join } from "node:path";

const METADATA_URL = new URL("../product-metadata.json", import.meta.url);

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
      resourcesPath,
      event,
      stage: currentStage,
      lastSuccessfulStage,
      ...details,
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
    async startStage(stage) {
      currentStage = stage;
      await write("stage_started");
    },
    async completeStage(stage) {
      currentStage = stage;
      lastSuccessfulStage = stage;
      await write("stage_completed");
    },
    async recordFailure(error) {
      const normalized = normalizeError(error);
      await write("startup_failed", {
        errorName: normalized.name,
        errorMessage: normalized.message,
        errorStack: normalized.stack,
      });
    },
    state() {
      return Object.freeze({ currentStage, lastSuccessfulStage });
    },
  });
}

export async function createStartupLoggerForApp(app, {
  resourcesPath = process.resourcesPath,
  metadataUrl = METADATA_URL,
} = {}) {
  const metadata = await readBuildMetadata(metadataUrl);
  return createStartupLogger({
    userDataPath: app.getPath("userData"),
    version: app.getVersion(),
    buildId: metadata.buildId,
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

async function readBuildMetadata(metadataUrl) {
  try {
    const value = JSON.parse(await readFile(metadataUrl, "utf8"));
    return {
      buildId: typeof value?.buildId === "string" && value.buildId.trim()
        ? value.buildId.trim()
        : "unknown",
    };
  } catch {
    return { buildId: "unknown" };
  }
}

function normalizeError(error) {
  if (error instanceof Error) {
    return {
      name: safeString(error.name, "Error"),
      message: safeString(error.message, "desktop_startup_failed"),
      stack: safeString(error.stack, `${error.name}: ${error.message}`),
    };
  }
  return {
    name: "Error",
    message: safeString(error, "desktop_startup_failed"),
    stack: safeString(error, "desktop_startup_failed"),
  };
}

function safeString(value, fallback) {
  const text = String(value ?? "").trim();
  return (text || fallback).slice(0, 32_768);
}
