import assert from "node:assert/strict";
import test from "node:test";
import { RuntimeCoordinator } from "../electron/runtime/runtimeCoordinator.js";
import { desktopStatus, localRuntimeReady } from "../electron/runtime/status.js";

function readyStatus(tunnelState = "tunnel_not_configured", owned = false) {
  return {
    state: tunnelState === "tunnel_ready" ? "ready" : "local_ready_tunnel_missing",
    tunnel_state: tunnelState,
    components: {
      fastapi: { state: "ready", owned },
      mcp: { state: "ready", owned },
    },
  };
}

test("secure tunnel missing does not block local readiness", () => {
  const status = readyStatus();
  assert.equal(localRuntimeReady(status), true);
  assert.equal(desktopStatus(status).code, "local_ready_tunnel_missing");
});

test("healthy pre-existing runtime is reused and never stopped by desktop", async () => {
  const calls = [];
  const client = {
    async status() { calls.push("status"); return readyStatus(); },
    async start() { calls.push("start"); },
    async stop() { calls.push("stop"); return { state: "stopped" }; },
  };
  const coordinator = new RuntimeCoordinator(client, { sleep: async () => {} });
  const status = await coordinator.ensureReady();
  assert.equal(coordinator.startedByDesktop, false);
  assert.equal(status.runtime_owner, "external");
  assert.equal(status.components.fastapi.owner, "external");
  await assert.rejects(() => coordinator.restart(), /external_runtime_restart_not_allowed/);
  assert.deepEqual(await coordinator.stopIfOwned(), { status: "reused_runtime_left_running" });
  assert.deepEqual(calls, ["status", "status"]);
});

test("runtime started by desktop is stopped on fully quit", async () => {
  const calls = [];
  let statusCall = 0;
  const client = {
    async status() {
      calls.push("status");
      statusCall += 1;
      return statusCall === 1 ? { state: "stopped", components: {} } : readyStatus("tunnel_not_configured", true);
    },
    async start() { calls.push("start"); return { state: "starting" }; },
    async stop() { calls.push("stop"); return { state: "stopped", components: {} }; },
  };
  const coordinator = new RuntimeCoordinator(client, { sleep: async () => {} });
  const status = await coordinator.ensureReady();
  assert.equal(coordinator.startedByDesktop, true);
  assert.equal(status.runtime_owner, "managed-by-search");
  assert.equal(status.components.fastapi.owner, "managed-by-search");
  await coordinator.stopIfOwned();
  assert.deepEqual(calls, ["status", "start", "status", "stop"]);
});

test("missing desktop runtime config is structured, never spawns, and remains refreshable", async () => {
  const calls = [];
  const unavailable = {
    status: "error",
    state: "unavailable",
    error_code: "desktop_runtime_config_missing",
    components: {
      fastapi: { state: "unavailable", error_code: "desktop_runtime_config_missing" },
      mcp: { state: "unavailable", error_code: "desktop_runtime_config_missing" },
    },
  };
  const client = {
    available: () => false,
    unavailableStatus: () => unavailable,
    async status() { calls.push("status"); throw new Error("must_not_spawn"); },
    async start() { calls.push("start"); throw new Error("must_not_spawn"); },
  };
  const coordinator = new RuntimeCoordinator(client);
  await assert.rejects(coordinator.ensureReady(), /desktop_runtime_config_missing/);
  assert.equal(coordinator.lastStatus.error_code, "desktop_runtime_config_missing");
  assert.equal(desktopStatus(coordinator.lastStatus).code, "failed");
  assert.equal((await coordinator.refresh()).components.fastapi.state, "unavailable");
  assert.deepEqual(calls, []);
});
