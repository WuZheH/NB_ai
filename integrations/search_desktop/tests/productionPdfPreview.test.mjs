import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir, rm } from "node:fs/promises";
import { createServer } from "node:http";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const ELECTRON_EXE = resolve(ROOT, "node_modules", "electron", "dist", "electron.exe");
const PROBE = resolve(ROOT, "tests", "fixtures", "productionPdfPreviewProbe.mjs");
const PROJECT_TMP = resolve(ROOT, "..", "..", ".codex_tmp", "electron-pdf-preview-process");
const USER_DATA_TMP = resolve(ROOT, "..", "..", ".codex_tmp", "electron-pdf-preview-user-data");
const CRASH_DUMPS_TMP = resolve(ROOT, "..", "..", ".codex_tmp", "electron-pdf-preview-crashes");

test("production renderer renders a local PDF.js page and replaces stale fragment highlights", { timeout: 40000 }, async () => {
  const { payload, stdout, stderr, code } = await runProbe();
  assert.equal(code, 0, `production PDF probe failed\n${stdout}\n${stderr}`);
  assert.equal(payload.status, "ok", `${payload.error || "production PDF metrics missing"}\n${stdout}\n${stderr}`);
  assert.equal(payload.metrics.canvasPresent, true);
  assert.equal(payload.metrics.first.strategy, "text");
  assert.ok(payload.metrics.first.highlightCount > 0, JSON.stringify(payload.metrics));
  assert.equal(payload.metrics.textInitial.allInside, true, JSON.stringify(payload.metrics.textInitial));
  assert.equal(payload.metrics.textInitial.targetIntersected, true, JSON.stringify(payload.metrics.textInitial));
  assert.equal(payload.metrics.second.strategy, "bbox");
  assert.equal(payload.metrics.second.highlightCount, 2);
  assert.equal(payload.metrics.bboxInitial.allInside, true, JSON.stringify(payload.metrics.bboxInitial));
  assert.equal(payload.metrics.bboxInitial.targetIntersected, true, JSON.stringify(payload.metrics.bboxInitial));
  assert.equal(payload.metrics.bboxZoomed.allInside, true, JSON.stringify(payload.metrics.bboxZoomed));
  assert.equal(payload.metrics.bboxZoomed.targetIntersected, true, JSON.stringify(payload.metrics.bboxZoomed));
  assert.equal(payload.metrics.bboxRotated.rotation, 90, JSON.stringify(payload.metrics.bboxRotated));
  assert.equal(payload.metrics.bboxRotated.allInside, true, JSON.stringify(payload.metrics.bboxRotated));
  assert.equal(payload.metrics.bboxRotated.targetIntersected, true, JSON.stringify(payload.metrics.bboxRotated));
  assert.equal(payload.metrics.resultScroll.sameNode, true);
  assert.ok(payload.metrics.resultScroll.before > 0);
  assert.ok(Math.abs(payload.metrics.resultScroll.after - payload.metrics.resultScroll.before) <= 1);
  assert.equal(payload.metrics.remoteWorkerRequested, false);
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
    if (request.method !== "POST") return response.writeHead(405).end();
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
        SEARCH_PDF_PREVIEW_CALLBACK_URL: `http://127.0.0.1:${address.port}/result`,
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
      reject(new Error(`production PDF probe timed out\n${stdout}\n${stderr}`));
    }, 35000);
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
