import assert from "node:assert/strict";
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import test from "node:test";
import {
  DESKTOP_RUNTIME_STATUS,
  backupDesktopRuntimeConfig,
  loadDesktopRuntimeConfig,
  migrateLegacyDesktopRuntimeConfig,
  publicDesktopRuntimeConfig,
  validateDesktopRuntimePayload,
  writeDesktopRuntimeConfig,
} from "../electron/main/desktopRuntimeConfig.js";

const ROOT = resolve(import.meta.dirname, "..");
const FIXTURE = join(ROOT, ".test-work-desktop-runtime-config");
const probeExecutable = () => true;

test("desktop runtime validation returns every stable prerequisite status", async () => {
  const paths = await validFixture("status matrix");
  const valid = payload(paths);
  assert.equal(validate(valid).status, DESKTOP_RUNTIME_STATUS.READY);
  assert.equal(validate({ ...valid, extra: "secret" }).status, DESKTOP_RUNTIME_STATUS.INVALID_JSON);
  assert.equal(validate({ ...valid, schemaVersion: 99 }).status, DESKTOP_RUNTIME_STATUS.SCHEMA_UNSUPPORTED);
  assert.equal(validate({ schemaVersion: 1 }).status, DESKTOP_RUNTIME_STATUS.REQUIRED_FIELD_MISSING);
  assert.equal(validate({ ...valid, dataDir: "relative\\data" }).status, DESKTOP_RUNTIME_STATUS.PATH_NOT_ABSOLUTE);
  assert.equal(validate({ ...valid, pythonExe: "relative\\python.exe" }).status, DESKTOP_RUNTIME_STATUS.PATH_NOT_ABSOLUTE);
  assert.equal(validate({ ...valid, nodeExe: "relative\\node.exe" }).status, DESKTOP_RUNTIME_STATUS.PATH_NOT_ABSOLUTE);
  assert.equal(validate({ ...valid, dataDir: join(FIXTURE, "missing-data") }).status, DESKTOP_RUNTIME_STATUS.DATA_DIR_MISSING);
  assert.equal(validate({ ...valid, pythonExe: join(FIXTURE, "missing-python.exe") }).status, DESKTOP_RUNTIME_STATUS.PYTHON_MISSING);
  assert.equal(validate({ ...valid, nodeExe: join(FIXTURE, "missing-node.exe") }).status, DESKTOP_RUNTIME_STATUS.NODE_MISSING);
  assert.deepEqual(validate({ ...valid, schemaVersion: 99 }).missingPrerequisites, ["desktop_runtime_config"]);
  assert.equal(
    validateDesktopRuntimePayload(valid, { probeExecutable: (_path, role) => role !== "python" }).status,
    DESKTOP_RUNTIME_STATUS.PYTHON_MISSING,
  );
  assert.equal(
    validateDesktopRuntimePayload(valid, { probeExecutable: (_path, role) => role !== "node" }).status,
    DESKTOP_RUNTIME_STATUS.NODE_MISSING,
  );
});

test("production data cannot point into legacy, Candidate, smoke, or packaged output roots", async () => {
  const paths = await validFixture("forbidden roots");
  for (const forbidden of [
    resolve("D:/LEARNING/Tools/notebook_ai/data"),
    resolve("D:/LEARNING/Tools/search/dist-candidates/Candidate5/data"),
    resolve("D:/LEARNING/Tools/SearchPackageSmoke/Candidate5/data"),
    resolve("D:/LEARNING/Tools/search/dist/formal/Candidate5/data"),
    resolve("D:/LEARNING/Tools/search/integrations/search_desktop/dist/win-unpacked/data"),
  ]) {
    assert.equal(validate({ ...payload(paths), dataDir: forbidden }).status, DESKTOP_RUNTIME_STATUS.DATA_DIR_MISSING);
  }
});

test("valid config supports Unicode and spaces while public status never exposes absolute paths", async () => {
  const paths = await validFixture("中文 与 空格");
  const config = validate(payload(paths));
  assert.equal(config.ready, true);
  assert.equal(config.dataDir, resolve(paths.dataDir));
  const publicStatus = publicDesktopRuntimeConfig(config);
  const serialized = JSON.stringify(publicStatus);
  assert.equal(serialized.includes(resolve(FIXTURE)), false);
  assert.match(publicStatus.data.path_hash, /^[0-9a-f]{64}$/);
  assert.equal(publicStatus.python.basename, "python.exe");
  assert.equal(publicStatus.node.basename, "node.exe");
});

test("invalid JSON and missing config are structured", async () => {
  const directory = join(FIXTURE, "load-errors");
  await mkdir(directory, { recursive: true });
  const missing = loadDesktopRuntimeConfig(join(directory, "missing.json"), { probeExecutable });
  assert.equal(missing.status, DESKTOP_RUNTIME_STATUS.MISSING);
  const invalidPath = join(directory, "invalid.json");
  await writeFile(invalidPath, "{", "utf8");
  assert.equal(loadDesktopRuntimeConfig(invalidPath, { probeExecutable }).status, DESKTOP_RUNTIME_STATUS.INVALID_JSON);
});

test("set is atomic, backs up a valid config, and refuses an unknown schema", async () => {
  const paths = await validFixture("atomic");
  const configPath = join(FIXTURE, "atomic-user-data", "desktop-runtime.json");
  const first = writeDesktopRuntimeConfig(configPath, payload(paths), { probeExecutable });
  assert.equal(first.ready, true);
  const firstBytes = await readFile(configPath, "utf8");
  writeDesktopRuntimeConfig(configPath, payload(paths), { probeExecutable });
  assert.equal(await readFile(`${configPath}.bak`, "utf8"), firstBytes);
  writeDesktopRuntimeConfig(configPath, payload(paths), { probeExecutable });
  assert.equal(await readFile(`${configPath}.bak`, "utf8"), firstBytes);
  await writeFile(configPath, JSON.stringify({ schemaVersion: 999 }), "utf8");
  assert.throws(
    () => writeDesktopRuntimeConfig(configPath, payload(paths), { probeExecutable }),
    /desktop_runtime_schema_unsupported/,
  );
  assert.equal(JSON.parse(await readFile(configPath, "utf8")).schemaVersion, 999);
});

test("legacy schema 3 migration writes userData config and preserves a backup outside the package", async () => {
  const paths = await validFixture("migration");
  const packageDirectory = join(FIXTURE, "migration-package", "win-unpacked");
  const legacyPath = join(packageDirectory, "search-desktop.local.json");
  const destinationPath = join(FIXTURE, "migration-user-data", "desktop-runtime.json");
  await mkdir(packageDirectory, { recursive: true });
  await writeFile(legacyPath, JSON.stringify({ schemaVersion: 3, ...paths }), "utf8");
  const result = migrateLegacyDesktopRuntimeConfig({ legacyPath, destinationPath, probeExecutable });
  assert.equal(result.config.ready, true);
  assert.equal(result.legacyBackup, join(resolve(destinationPath, ".."), "desktop-runtime.legacy-sidecar.bak.json"));
  assert.equal(JSON.parse(await readFile(destinationPath, "utf8")).schemaVersion, 1);
  assert.deepEqual(JSON.parse(await readFile(legacyPath, "utf8")).schemaVersion, 3);
});

test("manual backup leaves the source untouched", async () => {
  const paths = await validFixture("backup");
  const configPath = join(FIXTURE, "backup-user-data", "desktop-runtime.json");
  writeDesktopRuntimeConfig(configPath, payload(paths), { probeExecutable });
  const before = await readFile(configPath, "utf8");
  const backup = backupDesktopRuntimeConfig(configPath);
  assert.equal(await readFile(configPath, "utf8"), before);
  assert.equal(await readFile(backup, "utf8"), before);
});

function validate(value) {
  return validateDesktopRuntimePayload(value, { configPath: join(FIXTURE, "desktop-runtime.json"), probeExecutable });
}

function payload(paths) {
  return { schemaVersion: 1, dataDir: paths.dataDir, pythonExe: paths.pythonExe, nodeExe: paths.nodeExe };
}

async function validFixture(name) {
  const root = join(FIXTURE, name);
  const dataDir = join(root, "canonical-data");
  const pythonExe = join(root, "Python Env", "python.exe");
  const nodeExe = join(root, "Node Env", "node.exe");
  await mkdir(dataDir, { recursive: true });
  await mkdir(resolve(pythonExe, ".."), { recursive: true });
  await mkdir(resolve(nodeExe, ".."), { recursive: true });
  await writeFile(pythonExe, "fixture", "utf8");
  await writeFile(nodeExe, "fixture", "utf8");
  return { dataDir, pythonExe, nodeExe };
}

test.after(async () => {
  await rm(FIXTURE, { recursive: true, force: true });
});
