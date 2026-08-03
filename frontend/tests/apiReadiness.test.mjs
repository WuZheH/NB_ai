import assert from "node:assert/strict";
import test from "node:test";

import {
  STARTUP_MAX_HEALTH_ATTEMPTS,
  isRetryableStartupError,
  runtimeFastApiReady,
  runtimeTerminallyUnavailable,
  startupRetryDelayMs,
} from "../src/hooks/useLocalApiStatus.js";

test("API readiness uses bounded exponential startup checks", () => {
  assert.equal(STARTUP_MAX_HEALTH_ATTEMPTS, 6);
  assert.deepEqual(
    Array.from({ length: STARTUP_MAX_HEALTH_ATTEMPTS - 1 }, (_, attempt) => startupRetryDelayMs(attempt)),
    [250, 500, 1000, 2000, 4000],
  );
  assert.equal(isRetryableStartupError({ code: "api_connection_failed" }), true);
  assert.equal(isRetryableStartupError({ code: "api_endpoint_not_found" }), false);
});

test("API readiness follows the real FastAPI component state", () => {
  assert.equal(runtimeFastApiReady({ components: { fastapi: { state: "ready" } } }), true);
  assert.equal(runtimeFastApiReady({ components: { fastapi: { state: "starting" } } }), false);
  assert.equal(runtimeTerminallyUnavailable({ components: { fastapi: { state: "unavailable" } } }), true);
  assert.equal(runtimeTerminallyUnavailable({ state: "unavailable" }), true);
});
