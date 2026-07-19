import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { join, resolve } from "node:path";
import test from "node:test";
import { resolveDesktopConfig } from "../electron/main/config.js";
import { LauncherClient } from "../electron/runtime/launcherClient.js";


test("Electron derives the version-independent machine config from app userData", () => {
  const userDataPath = resolve("D:/Search Tests/用户状态");
  const config = resolveDesktopConfig({
    env: { PATH: process.env.PATH },
    userDataPath,
    isPackaged: false,
  });
  assert.equal(config.machineConfigPath, join(userDataPath, "machine-config.json"));
});


test("launcher passes machine config explicitly and removes ambient model paths", async () => {
  const calls = [];
  const spawnProcess = (executable, args, options) => {
    calls.push({ executable, args, options });
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    child.kill = () => {};
    queueMicrotask(() => {
      child.stdout.emit("data", Buffer.from('{"state":"stopped"}\n'));
      child.emit("close", 0);
    });
    return child;
  };
  const machineConfigPath = resolve("D:/Search Tests/用户状态/machine-config.json");
  const config = {
    runtimeAvailable: true,
    runtimeRoot: resolve("D:/Search Runtime"),
    runtimeScript: resolve("D:/Search Runtime/scripts/runtime/notebook_ai_launcher.py"),
    dataDir: resolve("D:/Search Data"),
    dataProjectRoot: resolve("D:/"),
    pythonExe: resolve("D:/Python/python.exe"),
    nodeExe: resolve("D:/Node/node.exe"),
    buildMode: "packaged",
    buildIdentityPath: resolve("D:/Search Runtime/package.json"),
    machineConfigPath,
  };
  process.env.SEARCH_EMBEDDING_MODEL = "D:/forbidden/embedding";
  process.env.SEARCH_RERANKER_MODEL = "D:/forbidden/reranker";
  try {
    await new LauncherClient(config, { spawnProcess }).status();
  } finally {
    delete process.env.SEARCH_EMBEDDING_MODEL;
    delete process.env.SEARCH_RERANKER_MODEL;
  }
  assert.deepEqual(calls[0].args.slice(0, 5), [
    "-B",
    config.runtimeScript,
    "--machine-config",
    machineConfigPath,
    "status",
  ]);
  assert.equal(calls[0].options.env.SEARCH_MACHINE_CONFIG_PATH, machineConfigPath);
  assert.equal("SEARCH_EMBEDDING_MODEL" in calls[0].options.env, false);
  assert.equal("SEARCH_RERANKER_MODEL" in calls[0].options.env, false);
});
