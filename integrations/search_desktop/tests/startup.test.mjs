import assert from "node:assert/strict";
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import test from "node:test";
import { loadSearchDesignTokens } from "../electron/main/designTokens.js";
import {
  STARTUP_STAGE,
  createStartupLogger,
  createStartupLoggerForApp,
  reportStartupFailure,
} from "../electron/main/startupLogger.js";

const ROOT = resolve(import.meta.dirname, "..");
const PROJECT_ROOT = resolve(ROOT, "../..");
const TEST_ROOT = join(process.env.SEARCH_TEST_TMP_ROOT || join(PROJECT_ROOT, ".codex_tmp"), `search-desktop-startup-${process.pid}`);
const TEST_IDENTITY = Object.freeze({
  schema_version: "search.build-identity.v1",
  build_mode: "packaged",
  product: "Search",
  version: "0.1.4",
  build_id: "test-search-package",
  source_commit: "0123456789abcdef0123456789abcdef01234567",
  source_branch: "codex/test-build-identity",
  build_timestamp_utc: "2026-07-19T00:00:00.000Z",
});

test("desktop native colors use the formal Search brand token", async () => {
  const tokens = await loadSearchDesignTokens(
    join(PROJECT_ROOT, "packages", "search-design-system", "src"),
  );
  assert.deepEqual(tokens, {
    primary: "#4f9ff8",
    background: "#f5f7fa",
  });
});

test("missing Search brand token fails explicitly", async () => {
  await withTokens(":root { --search-primary: #123456; --search-bg: #f5f7fa; }", async (root) => {
    await assert.rejects(loadSearchDesignTokens(root), /search_design_token_missing:--search-brand/);
  });
});

test("missing Search background token fails explicitly", async () => {
  await withTokens(":root { --search-brand: #4f9ff8; }", async (root) => {
    await assert.rejects(loadSearchDesignTokens(root), /search_design_token_missing:--search-bg/);
  });
});

test("startup logger preserves stage and a stable error code without stack path leakage", async () => {
  const userDataPath = join(TEST_ROOT, "user-data");
  const logger = createStartupLogger({
    userDataPath,
    version: "0.1.4",
    buildId: TEST_IDENTITY.build_id,
    isPackaged: true,
    resourcesPath: "D:\\Search\\resources",
    now: () => new Date("2026-07-14T10:00:00.000Z"),
  });
  await logger.startStage(STARTUP_STAGE.DESIGN_TOKENS_LOADED);
  const error = new Error("search_design_token_missing:--search-brand");
  await logger.recordFailure(error);

  const entries = (await readFile(logger.logPath, "utf8"))
    .trim()
    .split(/\r?\n/)
    .map((line) => JSON.parse(line));
  assert.equal(entries.length, 2);
  assert.equal(entries[0].stage, "design_tokens_loaded");
  assert.equal(entries[1].event, "startup_failed");
  assert.equal(entries[1].stage, "design_tokens_loaded");
  assert.equal(entries[1].lastSuccessfulStage, null);
  assert.equal(entries[1].errorName, "Error");
  assert.equal(entries[1].error_code, error.message);
  assert.equal(Object.hasOwn(entries[1], "errorMessage"), false);
  assert.equal(Object.hasOwn(entries[1], "errorStack"), false);
  assert.equal(entries[1].version, "0.1.4");
  assert.equal(entries[1].buildId, TEST_IDENTITY.build_id);
  assert.equal(entries[1].isPackaged, true);
});

test("app startup logger reads the packaged build identity", async () => {
  const userDataPath = join(TEST_ROOT, "app-identity");
  const logger = await createStartupLoggerForApp({
    isPackaged: true,
    getPath: (name) => name === "userData" ? userDataPath : "",
  }, {
    buildIdentity: TEST_IDENTITY,
    resourcesPath: "D:\\Search\\resources",
  });
  await logger.startStage(STARTUP_STAGE.CONFIG_RESOLVED);
  const entry = JSON.parse((await readFile(logger.logPath, "utf8")).trim());
  assert.equal(entry.version, "0.1.4");
  assert.equal(entry.buildId, TEST_IDENTITY.build_id);
  assert.equal(entry.isPackaged, true);
  assert.equal(entry.resourcesDirectory, "resources");
  assert.match(entry.resourcesPathHash, /^[0-9a-f]{64}$/);
  assert.equal(Object.hasOwn(entry, "resourcesPath"), false);
  assert.equal(JSON.stringify(entry).includes("D:\\Search"), false);
  assert.match(logger.logPath, /logs[\\/]search-startup\.log$/);
});

test("stage completion records the last successful startup stage", async () => {
  const logger = createStartupLogger({
    userDataPath: join(TEST_ROOT, "completed-stage"),
    version: "0.1.4",
    buildId: TEST_IDENTITY.build_id,
    isPackaged: false,
    resourcesPath: "D:\\Search\\resources",
  });
  await logger.startStage(STARTUP_STAGE.CONFIG_RESOLVED);
  await logger.completeStage(STARTUP_STAGE.CONFIG_RESOLVED);
  await logger.startStage(STARTUP_STAGE.DESIGN_TOKENS_LOADED);
  assert.deepEqual(logger.state(), {
    currentStage: "design_tokens_loaded",
    lastSuccessfulStage: "config_resolved",
  });
});

test("runtime check failure is a failed stage with redacted structured prerequisites", async () => {
  const logger = createStartupLogger({
    userDataPath: join(TEST_ROOT, "runtime-failure"),
    version: "0.1.4",
    buildId: TEST_IDENTITY.build_id,
    isPackaged: true,
    resourcesPath: "C:\\Users\\private-user\\Candidate5\\resources",
  });
  await logger.startStage(STARTUP_STAGE.RUNTIME_CHECKED, {
    result: "started",
    config_source: "user_data",
    config_schema: 1,
    runtime_available: false,
    data_available: false,
    missing_prerequisites: ["python"],
  });
  await logger.failStage(
    STARTUP_STAGE.RUNTIME_CHECKED,
    "desktop_runtime_python_missing",
    { launcher_spawned: false, desktop_started_runtime: false },
  );
  const entries = (await readFile(logger.logPath, "utf8")).trim().split(/\r?\n/).map(JSON.parse);
  assert.equal(entries[0].event, "stage_started");
  assert.equal(entries[1].event, "stage_failed");
  assert.equal(entries[1].stage, "runtime_checked");
  assert.equal(entries[1].result, "failed");
  assert.equal(entries[1].error_code, "desktop_runtime_python_missing");
  assert.equal(entries[1].launcher_spawned, false);
  assert.equal(entries[1].desktop_started_runtime, false);
  assert.equal(JSON.stringify(entries).includes("private-user"), false);
});

test("startup logger drops unknown detail fields and sanitizes allowed values", async () => {
  const logger = createStartupLogger({
    userDataPath: join(TEST_ROOT, "detail-sanitizer"),
    version: "0.1.4",
    buildId: TEST_IDENTITY.build_id,
    isPackaged: true,
    resourcesPath: "C:\\Users\\private-user\\Candidate5\\resources",
  });
  await logger.completeStage(STARTUP_STAGE.CONFIG_RESOLVED, {
    config_source: "C:\\Users\\private-user\\desktop-runtime.json",
    python_basename: "C:\\private\\python.exe",
    unexpected_path: "C:\\Users\\private-user\\secret",
  });
  const entry = JSON.parse((await readFile(logger.logPath, "utf8")).trim());
  assert.equal(entry.config_source, "redacted");
  assert.equal(entry.python_basename, "python.exe");
  assert.equal(Object.hasOwn(entry, "unexpected_path"), false);
  assert.equal(JSON.stringify(entry).includes("private-user"), false);
});

test("application records every startup stage in lifecycle order", async () => {
  const source = await readFile(join(ROOT, "electron", "main", "application.js"), "utf8");
  const stageNames = [
    "CONFIG_RESOLVED",
    "DESIGN_TOKENS_LOADED",
    "RENDERER_STARTED",
    "RUNTIME_CHECKED",
    "WINDOW_CREATED",
    "TRAY_CREATED",
    "READY",
  ];
  let previous = -1;
  for (const name of stageNames) {
    const position = source.indexOf(`STARTUP_STAGE.${name}`, previous + 1);
    assert.ok(position > previous, `${name} must follow the previous startup stage`);
    previous = position;
  }
  assert.deepEqual(Object.values(STARTUP_STAGE), [
    "config_resolved",
    "design_tokens_loaded",
    "renderer_started",
    "runtime_checked",
    "window_created",
    "tray_created",
    "ready",
  ]);
});

test("startup failure reaches stderr, the log, and exit code 1", async () => {
  const calls = [];
  const logger = createStartupLogger({
    userDataPath: join(TEST_ROOT, "reported-failure"),
    version: "0.1.4",
    buildId: TEST_IDENTITY.build_id,
    isPackaged: true,
    resourcesPath: "D:\\Search\\resources",
  });
  await logger.startStage(STARTUP_STAGE.RENDERER_STARTED);
  const error = new TypeError("renderer_server_start_failed");
  await reportStartupFailure({
    error,
    startupLogger: logger,
    consoleObject: { error: (...args) => calls.push(args) },
    app: { exit: (code) => calls.push(["exit", code]) },
  });
  assert.equal(calls[0][0], "[Search] startup failed");
  assert.equal(calls[0][1], error);
  assert.deepEqual(calls.at(-1), ["exit", 1]);
  const log = await readFile(logger.logPath, "utf8");
  assert.match(log, /renderer_server_start_failed/);
  assert.match(log, /\"stage\":\"renderer_started\"/);
});

test("logging failure never masks the original startup error", async () => {
  const calls = [];
  const error = new Error("original_startup_error");
  await reportStartupFailure({
    error,
    startupLogger: { recordFailure: async () => { throw new Error("log_failed"); } },
    consoleObject: { error: (...args) => calls.push(args) },
    app: { exit: (code) => calls.push(["exit", code]) },
  });
  assert.equal(calls[0][1], error);
  assert.deepEqual(calls.at(-1), ["exit", 1]);
});

async function withTokens(source, action) {
  const directory = join(TEST_ROOT, `tokens-${Math.random().toString(16).slice(2)}`);
  await mkdir(directory, { recursive: true });
  await writeFile(join(directory, "tokens.css"), source, "utf8");
  try {
    await action(directory);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}

test.after(async () => {
  await rm(TEST_ROOT, { recursive: true, force: true });
});
