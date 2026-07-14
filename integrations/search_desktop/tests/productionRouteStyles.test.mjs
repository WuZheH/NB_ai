import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir, rm } from "node:fs/promises";
import { createServer } from "node:http";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { parseAppRouteFromLocation } from "../../../frontend/src/app/routes.js";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const ELECTRON_EXE = resolve(ROOT, "node_modules", "electron", "dist", "electron.exe");
const PROBE = resolve(ROOT, "tests", "fixtures", "productionRouteStylesProbe.mjs");
const PROJECT_TMP = resolve(ROOT, "..", "..", ".codex_tmp", "electron-route-styles-process");
const USER_DATA_TMP = resolve(ROOT, "..", "..", ".codex_tmp", "electron-route-styles-user-data");
const CRASH_DUMPS_TMP = resolve(ROOT, "..", "..", ".codex_tmp", "electron-route-styles-crashes");

test("global Search styles preserve supported non-retrieval production routes", { timeout: 30000 }, async () => {
  const { payload, stdout, stderr, code } = await runProbe();
  assert.equal(code, 0, `route style DOM probe failed\n${stdout}\n${stderr}`);
  assert.equal(payload.status, "ok", `${payload.error || "route style metrics missing"}\n${stdout}\n${stderr}`);

  for (const path of ["/read-shelf", "/import", "/workspace"]) {
    const route = payload.routes[path];
    assert.equal(route.registered, true, `${path}: ${JSON.stringify(route)}`);
    assert.equal(route.pathname, path);
    assert.equal(route.contentPresent, true, `${path}: ${JSON.stringify(route)}`);
    assert.ok(route.bodyTextLength > 40, `${path}: ${JSON.stringify(route)}`);
    assert.ok(route.interactiveCount > 0, `${path}: ${JSON.stringify(route)}`);
    assert.equal(route.interaction.focused, true, `${path}: ${JSON.stringify(route)}`);
    assert.equal(route.interaction.valueChanged, true, `${path}: ${JSON.stringify(route)}`);
  }

  for (const path of ["/read-shelf", "/import"]) {
    const route = payload.routes[path];
    assert.ok(route.scrollHost, `${path}: ${JSON.stringify(route)}`);
    assert.ok(route.scrollHost.scrollHeight > route.scrollHost.clientHeight, `${path}: ${JSON.stringify(route)}`);
    assert.ok(route.scrollHost.scrollTop > 0, `${path}: ${JSON.stringify(route)}`);
    assert.equal(route.maxScrollReached, true, `${path}: ${JSON.stringify(route)}`);
    assert.equal(route.lastVisible, true, `${path}: ${JSON.stringify(route)}`);
    assert.equal(route.clippedWithoutScrollHost, false, `${path}: ${JSON.stringify(route)}`);
  }

  const workspace = payload.routes["/workspace"];
  if (workspace.needsScroll && !workspace.scrollHost) {
    assert.equal(workspace.clippedWithoutScrollHost, true, JSON.stringify(workspace));
    assert.equal(workspace.blockingOverflowAncestor, ".notebookWorkspaceSurface", JSON.stringify(workspace));
    assert.equal(workspace.root.scrollHeight, workspace.root.clientHeight, JSON.stringify(workspace));
  } else if (workspace.needsScroll) {
    assert.equal(workspace.clippedWithoutScrollHost, false, JSON.stringify(workspace));
    assert.ok(workspace.scrollHost, JSON.stringify(workspace));
    assert.equal(workspace.lastVisible, true, JSON.stringify(workspace));
  }

  for (const path of ["/settings", "/system-status"]) {
    const route = payload.routes[path];
    assert.equal(route.registered, false, `${path}: ${JSON.stringify(route)}`);
    assert.equal(route.workspaceFallback, true, `${path}: ${JSON.stringify(route)}`);
    assert.ok(route.bodyTextLength > 40, `${path}: ${JSON.stringify(route)}`);
  }
  console.log(`production route style metrics: ${JSON.stringify(payload.routes)}`);
});

test("clean baseline does not misrepresent settings and system-status as registered routes", () => {
  assert.equal(parseAppRouteFromLocation({ pathname: "/settings", search: "" }), null);
  assert.equal(parseAppRouteFromLocation({ pathname: "/system-status", search: "" }), null);
});

async function runProbe() {
  await mkdir(PROJECT_TMP, { recursive: true });
  let resolvePayload;
  let rejectPayload;
  const payloadPromise = new Promise((resolvePromise, reject) => {
    resolvePayload = resolvePromise;
    rejectPayload = reject;
  });
  const callbackServer = createServer((request, response) => {
    if (request.method !== "POST") {
      response.writeHead(405).end();
      return;
    }
    let body = "";
    request.on("data", (chunk) => { body += chunk; });
    request.on("end", () => {
      try {
        resolvePayload(JSON.parse(body));
        response.writeHead(204).end();
      } catch (error) {
        rejectPayload(error);
        response.writeHead(400).end();
      }
    });
  });
  await new Promise((resolvePromise, reject) => {
    callbackServer.once("error", reject);
    callbackServer.listen(0, "127.0.0.1", resolvePromise);
  });
  const address = callbackServer.address();
  assert.ok(address && typeof address !== "string");

  return new Promise((resolvePromise, reject) => {
    const child = spawn(ELECTRON_EXE, [PROBE], {
      cwd: ROOT,
      windowsHide: true,
      env: {
        ...process.env,
        ELECTRON_DISABLE_SECURITY_WARNINGS: "true",
        TEMP: PROJECT_TMP,
        TMP: PROJECT_TMP,
        SEARCH_ROUTE_STYLES_CALLBACK_URL: `http://127.0.0.1:${address.port}/result`,
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.once("error", reject);
    const timeout = setTimeout(() => {
      child.kill();
      callbackServer.close();
      reject(new Error(`route style DOM probe timed out\n${stdout}\n${stderr}`));
    }, 25000);
    payloadPromise.then((payload) => {
      child.once("close", async (code) => {
        clearTimeout(timeout);
        callbackServer.close();
        await cleanupProbeTemp();
        resolvePromise({ payload, code, stdout, stderr });
      });
    }, (error) => {
      clearTimeout(timeout);
      child.kill();
      callbackServer.close();
      reject(error);
    });
  });
}

function cleanupProbeTemp() {
  return Promise.all([
    rm(PROJECT_TMP, { recursive: true, force: true }),
    rm(USER_DATA_TMP, { recursive: true, force: true }),
    rm(CRASH_DUMPS_TMP, { recursive: true, force: true }),
  ]);
}
