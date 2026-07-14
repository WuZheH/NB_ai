import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir, rm } from "node:fs/promises";
import { createServer } from "node:http";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const ELECTRON_EXE = resolve(ROOT, "node_modules", "electron", "dist", "electron.exe");
const PROBE = resolve(ROOT, "tests", "fixtures", "productionScrollProbe.mjs");
const PROJECT_TMP = resolve(ROOT, "..", "..", ".codex_tmp", "electron-scroll-process");
const USER_DATA_TMP = resolve(ROOT, "..", "..", ".codex_tmp", "electron-scroll-user-data");
const CRASH_DUMPS_TMP = resolve(ROOT, "..", "..", ".codex_tmp", "electron-scroll-crashes");

test("production renderer keeps window fixed while results, preview, and evidence basket scroll independently", { timeout: 30000 }, async () => {
  const { payload, stdout, stderr, code } = await runProbe();
  assert.equal(code, 0, `production DOM probe failed\n${stdout}\n${stderr}`);
  assert.equal(payload.status, "ok", payload.error || `production DOM metrics missing\n${stdout}\n${stderr}`);
  const { metrics } = payload;

  assert.deepEqual(metrics.viewport, { width: 1024, height: 768 });
  assert.ok(metrics.root.scrollHeight <= metrics.root.clientHeight + 1, JSON.stringify(metrics.root));
  assert.equal(metrics.rootOverflowY, "hidden");
  assert.equal(metrics.results.count, 12);
  assert.ok(metrics.results.scrollHeight > metrics.results.clientHeight, JSON.stringify(metrics.results));
  assert.ok(metrics.preview.scrollHeight > metrics.preview.clientHeight, JSON.stringify(metrics.preview));
  assert.equal(metrics.basket.count, 12);
  assert.ok(metrics.basket.scrollHeight > metrics.basket.clientHeight, JSON.stringify(metrics.basket));
  assert.equal(metrics.results.lastVisible, true);
  assert.equal(metrics.preview.lastVisible, true);
  assert.equal(metrics.basket.lastVisible, true);
  assert.equal(metrics.railPosition, "static");
  assert.equal(metrics.navigationOutlined, false);
  assert.equal(metrics.evidenceBasketEnglishVisible, false);
  assert.ok(metrics.interactions.wheelTop > 0, JSON.stringify(metrics.interactions));
  assert.ok(metrics.interactions.pageDownTop > 0, JSON.stringify(metrics.interactions));
  assert.ok(metrics.interactions.endTop > 0, JSON.stringify(metrics.interactions));
  assert.ok(metrics.interactions.scrollbarDragTop > 0, JSON.stringify(metrics.interactions));
  console.log(`production DOM scroll metrics: ${JSON.stringify(metrics)}`);
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
        SEARCH_SCROLL_CALLBACK_URL: `http://127.0.0.1:${address.port}/result`,
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
      reject(new Error(`production DOM probe timed out\n${stdout}\n${stderr}`));
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
