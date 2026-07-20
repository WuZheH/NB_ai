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
const TEST_TMP_ROOT = resolve(process.env.SEARCH_TEST_TMP_ROOT || resolve(ROOT, "..", "..", ".codex_tmp"));
const PROJECT_TMP = resolve(TEST_TMP_ROOT, "electron-pdf-preview-process");
const USER_DATA_TMP = resolve(TEST_TMP_ROOT, "electron-pdf-preview-user-data");
const CRASH_DUMPS_TMP = resolve(TEST_TMP_ROOT, "electron-pdf-preview-crashes");

test("production renderer renders a readable local PDF preview at supported desktop sizes", { timeout: 90000 }, async () => {
  for (const viewport of [{ width: 1440, height: 900 }, { width: 1600, height: 900 }, { width: 1920, height: 1080 }]) {
    const { payload, stdout, stderr, code } = await runProbe(viewport);
    const diagnostic = JSON.stringify({ metrics: payload.metrics, timeline: compactTimeline(payload.timeline) }, null, 2);
    assert.equal(code, 0, `production PDF probe failed\n${stdout}\n${stderr}`);
    assert.equal(payload.status, "ok", `${payload.error || "production PDF metrics missing"}\n${stdout}\n${stderr}`);
    assert.equal(payload.metrics.canvasPresent, true, diagnostic);
    assert.equal(payload.metrics.first.strategy, "exact", diagnostic);
    assert.ok(payload.metrics.first.highlightCount > 0, JSON.stringify(payload.metrics));
    assert.equal(payload.metrics.first.pageNumber, 2, diagnostic);
    assert.equal(payload.metrics.first.chunkId, 1, diagnostic);
    assert.ok(payload.metrics.first.canvasRectWidth > 0 && payload.metrics.first.canvasRectHeight > 0, diagnostic);
    assert.equal(payload.metrics.textInitial.allInside, true, JSON.stringify(payload.metrics.textInitial));
    assert.equal(payload.metrics.textInitial.targetIntersected, true, JSON.stringify(payload.metrics.textInitial));
    assert.equal(payload.metrics.second.strategy, "exact", diagnostic);
    assert.equal(payload.metrics.second.highlightCount, 2);
    assert.equal(payload.metrics.second.pageNumber, 2, diagnostic);
    assert.equal(payload.metrics.second.chunkId, 2, diagnostic);
    assert.ok(payload.metrics.zoomed.scale > payload.metrics.second.scale, diagnostic);
    assert.equal(payload.metrics.bboxInitial.allInside, true, JSON.stringify(payload.metrics.bboxInitial));
    assert.equal(payload.metrics.bboxInitial.targetIntersected, true, JSON.stringify(payload.metrics.bboxInitial));
    assert.equal(payload.metrics.bboxZoomed.allInside, true, JSON.stringify(payload.metrics.bboxZoomed));
    assert.equal(payload.metrics.bboxZoomed.targetIntersected, true, JSON.stringify(payload.metrics.bboxZoomed));
    assert.equal(payload.metrics.roundTrips.iterations.length, viewport.width === 1600 ? 5 : 1, diagnostic);
    for (const roundTrip of payload.metrics.roundTrips.iterations) {
      assert.equal(roundTrip.ready.ready, true, diagnostic);
      assert.equal(roundTrip.ready.pageNumber, 2, diagnostic);
      assert.equal(roundTrip.ready.highlightCount, 2, diagnostic);
      assert.equal(roundTrip.restored.restoreStatus, "restored", diagnostic);
      assert.ok(roundTrip.restored.scrollTop > 0, diagnostic);
      assert.equal(roundTrip.restored.basketCount, 1, diagnostic);
      assert.equal(roundTrip.geometry.allInside, true, diagnostic);
      assert.equal(roundTrip.geometry.targetIntersected, true, diagnostic);
    }
    assert.equal(payload.metrics.resultScroll.sameNode, true);
    assert.ok(payload.metrics.resultScroll.before > 0);
    assert.ok(Math.abs(payload.metrics.resultScroll.after - payload.metrics.resultScroll.before) <= 1);
    assert.equal(payload.metrics.remoteWorkerRequested, false);
    assert.ok(payload.metrics.viewport.width >= Math.min(viewport.width - 1, 1440), JSON.stringify(payload.metrics.viewport));
    assert.ok(payload.metrics.viewport.height >= Math.min(viewport.height - 1, 899), JSON.stringify(payload.metrics.viewport));
    assert.ok(payload.metrics.layout.previewWidth >= 520, JSON.stringify(payload.metrics.layout));
    assert.ok(payload.metrics.layout.previewShare >= 0.43 && payload.metrics.layout.previewShare <= 0.49, JSON.stringify(payload.metrics.layout));
    assert.equal(payload.metrics.layout.rootScrollable, false);
    assert.equal(payload.metrics.layout.previewOuterScrollable, false);
    assert.equal(payload.metrics.layout.previewOuterOverflowY, "hidden");
    assert.equal(payload.metrics.layout.pdfOverflowX, "hidden");
    assert.equal(payload.metrics.layout.pdfOverflowY, "auto");
    assert.equal(payload.metrics.layout.resultTechnicalOpen, false);
    assert.equal(payload.metrics.layout.previewTechnicalOpen, false);
    assert.equal(payload.metrics.layout.title, "搜索");
    console.log("production PDF layout metrics:", JSON.stringify({ requested: viewport, actual: payload.metrics.viewport, layout: payload.metrics.layout }));
  }
});

test("production renderer recomputes first-preview focus after a zero-sized viewport becomes visible", { timeout: 60000 }, async () => {
  const { payload, stdout, stderr, code } = await runProbe({ width: 1600, height: 900, delayedLayout: true });
  const diagnostic = JSON.stringify({ metrics: payload.metrics, timeline: compactTimeline(payload.timeline) }, null, 2);
  assert.equal(code, 0, `production PDF resize probe failed\n${stdout}\n${stderr}`);
  assert.equal(payload.status, "ok", `${payload.error || "production PDF resize metrics missing"}\n${diagnostic}`);
  const recovery = payload.metrics.layoutRecovery;
  assert.equal(recovery.before.status, "ready", diagnostic);
  assert.equal(recovery.before.ready, false, diagnostic);
  assert.equal(recovery.before.viewportWidth, 0, diagnostic);
  assert.equal(recovery.before.viewportHeight, 0, diagnostic);
  assert.ok(recovery.before.focusWaitingCount > 0, diagnostic);
  assert.ok(recovery.afterResize.clientWidth > 0 && recovery.afterResize.clientHeight > 0, diagnostic);
  assert.equal(recovery.ready.ready, true, diagnostic);
  assert.ok(recovery.ready.viewportWidth > 0 && recovery.ready.viewportHeight > 0, diagnostic);
  assert.ok(recovery.ready.focusPendingCount > 0, diagnostic);
  assert.ok(recovery.ready.focusCommittedCount > 0, diagnostic);
  assert.equal(recovery.ready.lastStage.stage, "preview_ready_committed", diagnostic);
});

function compactTimeline(timeline = []) {
  return timeline
    .filter((entry) => entry.kind !== "dom_snapshot" || entry.previewStatus || entry.strategy)
    .map((entry) => ({
      sequence: entry.sequence,
      atMs: entry.atMs,
      kind: entry.kind,
      stage: entry.stage,
      attempt: entry.attempt,
      previewStatus: entry.previewStatus,
      chunkId: entry.chunkId,
      pageNumber: entry.pageNumber,
      scale: entry.scale,
      canvas: entry.canvasWidth && entry.canvasHeight ? `${entry.canvasWidth}x${entry.canvasHeight}` : undefined,
      strategy: entry.strategy,
      highlightCount: entry.highlightCount,
    }));
}

async function runProbe({ width, height, delayedLayout = false, frontendDist = "" }) {
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
        SEARCH_PDF_PREVIEW_WIDTH: String(width),
        SEARCH_PDF_PREVIEW_HEIGHT: String(height),
        SEARCH_PDF_PREVIEW_DELAY_LAYOUT: delayedLayout ? "1" : "0",
        ...(frontendDist ? { SEARCH_PDF_PREVIEW_FRONTEND_DIST: frontendDist } : {}),
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
