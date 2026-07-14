import assert from "node:assert/strict";
import test from "node:test";
import { RuntimeCoordinator } from "../electron/runtime/runtimeCoordinator.js";
import { desktopStatus, localRuntimeReady } from "../electron/runtime/status.js";

function readyStatus(tunnelState = "tunnel_not_configured") {
  return {
    state: tunnelState === "tunnel_ready" ? "ready" : "local_ready_tunnel_missing",
    tunnel_state: tunnelState,
    components: { fastapi: { state: "ready" }, mcp: { state: "ready" } },
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
  await coordinator.ensureReady();
  assert.equal(coordinator.startedByDesktop, false);
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
      return statusCall === 1 ? { state: "stopped", components: {} } : readyStatus();
    },
    async start() { calls.push("start"); return { state: "starting" }; },
    async stop() { calls.push("stop"); return { state: "stopped", components: {} }; },
  };
  const coordinator = new RuntimeCoordinator(client, { sleep: async () => {} });
  await coordinator.ensureReady();
  assert.equal(coordinator.startedByDesktop, true);
  await coordinator.stopIfOwned();
  assert.deepEqual(calls, ["status", "start", "status", "stop"]);
});
