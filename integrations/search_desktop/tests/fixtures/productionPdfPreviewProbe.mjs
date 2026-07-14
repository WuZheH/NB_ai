import { existsSync, mkdirSync } from "node:fs";
import { createServer } from "node:http";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { app, BrowserWindow } from "electron";
import { RendererServer } from "../../electron/runtime/rendererServer.js";

const DESKTOP_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const PROJECT_ROOT = resolve(DESKTOP_ROOT, "..", "..");
const FRONTEND_DIST = resolve(PROJECT_ROOT, "frontend", "dist");
const TEST_USER_DATA = resolve(PROJECT_ROOT, ".codex_tmp", "electron-pdf-preview-user-data");
const TEST_CRASH_DUMPS = resolve(PROJECT_ROOT, ".codex_tmp", "electron-pdf-preview-crashes");
const CALLBACK_URL = String(process.env.SEARCH_PDF_PREVIEW_CALLBACK_URL || "").trim();

mkdirSync(TEST_USER_DATA, { recursive: true });
mkdirSync(TEST_CRASH_DUMPS, { recursive: true });
app.setPath("userData", TEST_USER_DATA);
app.setPath("crashDumps", TEST_CRASH_DUMPS);
app.commandLine.appendSwitch("disable-gpu");

let rendererServer;
let fixtureServer;
let window;
let remoteWorkerRequested = false;

app.whenReady().then(runProbe).catch(async (error) => {
  await sendCallback({ status: "error", error: String(error?.stack || error) }).catch(() => {});
  app.exit(1);
});

async function runProbe() {
  try {
    if (!existsSync(resolve(FRONTEND_DIST, "index.html"))) throw new Error("frontend_production_build_missing");
    fixtureServer = await startFixtureServer();
    const address = fixtureServer.address();
    if (!address || typeof address === "string") throw new Error("fixture_api_address_invalid");
    const fixtureOrigin = `http://127.0.0.1:${address.port}`;
    rendererServer = new RendererServer({
      frontendDist: FRONTEND_DIST,
      fallbackFile: resolve(DESKTOP_ROOT, "renderer", "missing-build.html"),
      designSystemRoot: resolve(PROJECT_ROOT, "packages", "search-design-system", "src"),
      rendererAssets: resolve(DESKTOP_ROOT, "renderer"),
      backendUrl: fixtureOrigin,
      port: 0,
    });
    const rendererOrigin = await rendererServer.start();
    window = new BrowserWindow({
      width: 1023,
      height: 767,
      useContentSize: true,
      show: false,
      webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true, offscreen: true, backgroundThrottling: false },
    });
    const csp = ["default-src 'self'", "img-src 'self' data: blob:", "style-src 'self' 'unsafe-inline'", "script-src 'self'", `connect-src 'self' http://127.0.0.1:8000 ${fixtureOrigin}`, "font-src 'self' data:", "object-src 'none'"].join("; ");
    window.webContents.session.webRequest.onHeadersReceived({ urls: [`${rendererOrigin}/*`] }, (details, callback) => {
      const headers = { ...details.responseHeaders };
      for (const key of Object.keys(headers)) if (key.toLowerCase() === "content-security-policy") delete headers[key];
      headers["Content-Security-Policy"] = [csp];
      callback({ responseHeaders: headers });
    });
    window.webContents.session.webRequest.onBeforeRequest({ urls: ["<all_urls>"] }, (details, callback) => {
      const requested = new URL(details.url);
      if (details.url.startsWith("http://127.0.0.1:8000/api/")) {
        callback({ redirectURL: `${rendererOrigin}${requested.pathname}${requested.search}` });
        return;
      }
      if (/^https?:/u.test(details.url) && !details.url.startsWith(rendererOrigin) && !details.url.startsWith(fixtureOrigin)) remoteWorkerRequested = true;
      callback({ cancel: false });
    });
    window.webContents.on("console-message", (_event, level, message) => {
      console.log(`SEARCH_PDF_RENDERER_CONSOLE=${level}:${message}`);
    });
    await window.loadURL(`${rendererOrigin}/retrieval`);
    await waitFor("document.querySelector('[data-testid=\"retrieval-results-scroll\"]')");
    await submitSearch("PDF fixture");
    await waitFor("document.querySelectorAll('[data-result-index]').length === 12 || document.querySelector('.localRetrievalState.error')");
    const count = await window.webContents.executeJavaScript("document.querySelectorAll('[data-result-index]').length");
    if (count !== 12) {
      const error = await window.webContents.executeJavaScript("document.querySelector('.localRetrievalState.error')?.textContent || 'fixture_search_failed'");
      throw new Error(`fixture_search_failed:${error}`);
    }
    const before = await window.webContents.executeJavaScript(`(() => { const pane=document.querySelector('[data-testid="retrieval-results-scroll"]'); pane.scrollTop=Math.min(300,pane.scrollHeight-pane.clientHeight); globalThis.__pdfPreviewResultsPane=pane; return pane.scrollTop; })()`);
    await clickPreview(1);
    await waitFor("document.querySelector('[data-testid=\"pdf-page-canvas\"]') && document.querySelectorAll('[data-testid=\"pdf-highlight-rect\"]').length > 0", 16000, "pdf_first_preview");
    const first = await window.webContents.executeJavaScript(`(() => ({ strategy: document.querySelector('[data-testid="pdf-highlight-layer"]')?.dataset.strategy, highlightCount: document.querySelectorAll('[data-testid="pdf-highlight-rect"]').length }))()`);
    await clickPreview(2);
    await waitFor("document.querySelector('[data-testid=\"pdf-highlight-layer\"]')?.dataset.strategy === 'bbox' && document.querySelectorAll('[data-testid=\"pdf-highlight-rect\"]').length === 2", 16000, "pdf_second_preview");
    const metrics = await window.webContents.executeJavaScript(`(() => {
      const pane=document.querySelector('[data-testid="retrieval-results-scroll"]');
      return {
        canvasPresent: Boolean(document.querySelector('[data-testid="pdf-page-canvas"]')),
        first: ${JSON.stringify(first)},
        second: { strategy: document.querySelector('[data-testid="pdf-highlight-layer"]')?.dataset.strategy, highlightCount: document.querySelectorAll('[data-testid="pdf-highlight-rect"]').length },
        resultScroll: { before: ${JSON.stringify(before)}, after: pane.scrollTop, sameNode: globalThis.__pdfPreviewResultsPane === pane },
        remoteWorkerRequested: ${JSON.stringify(remoteWorkerRequested)},
      };
    })()`);
    await sendCallback({ status: "ok", metrics });
  } catch (error) {
    await sendCallback({ status: "error", error: String(error?.stack || error) }).catch(() => {});
    process.exitCode = 1;
  } finally {
    await delay(120);
    if (window && !window.isDestroyed()) window.destroy();
    if (rendererServer) await rendererServer.stop();
    if (fixtureServer) await new Promise((resolvePromise) => fixtureServer.close(resolvePromise));
    app.quit();
  }
}

function startFixtureServer() {
  const pdf = fixturePdf("PDF preview target text across the rendered page");
  const server = createServer((request, response) => {
    const url = new URL(request.url || "/", "http://127.0.0.1");
    response.setHeader("access-control-allow-origin", "*");
    response.setHeader("access-control-allow-methods", "GET, POST, HEAD, OPTIONS");
    response.setHeader("access-control-allow-headers", "content-type, accept, range");
    if (request.method === "OPTIONS") return response.writeHead(204).end();
    if (url.pathname === "/api/v1/retrieval/notebook-search") return json(response, searchFixture());
    if (url.pathname.endsWith("/locator")) return json(response, locatorFixture(url.pathname.includes("fixture-02") ? 2 : 1));
    if (url.pathname.startsWith("/api/v1/retrieval/fragments/")) return json(response, detailFixture(url.pathname.includes("fixture-02") ? 2 : 1));
    if (url.pathname === "/api/v1/library/documents/1/pdf") {
      response.writeHead(200, { "content-type": "application/pdf", "accept-ranges": "bytes", "content-length": pdf.length });
      response.end(pdf);
      return;
    }
    response.writeHead(404, { "content-type": "application/json" }).end(JSON.stringify({ detail: "fixture_not_found" }));
  });
  return new Promise((resolvePromise, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolvePromise(server));
  });
}

function searchFixture() {
  return { status: "ok", mode: "high_quality_notebook_search_v1", backend: "lancedb", reranker_model: "Qwen3-Reranker-0.6B", warnings: [], results: Array.from({ length: 12 }, (_, index) => resultFixture(index + 1)) };
}

function resultFixture(rank) {
  return { fragment_id: `fixture-${String(rank).padStart(2, "0")}`, source_type: "pdf_chunk", document_id: 1, document_title: `PDF fixture document ${rank}`, pdf_page: 1, final_rank: rank, final_score: 0.9, reranker_score: 0.8, semantic_score: 0.7, tags: [], provenance: [], text: "PDF preview target text across the rendered page" };
}

function detailFixture(rank) {
  return { ...resultFixture(rank), text: "PDF preview target text across the rendered page", context_before: "fixture before", context_after: "fixture after", content_hash: "a".repeat(64), open_target: { pdf_url: "/api/v1/library/documents/1/pdf#page=1", can_open_pdf: true, can_open_zotero: false } };
}

function locatorFixture(rank) {
  const bbox = rank === 2 ? [{ x0: 72, y0: 690, x1: 200, y1: 712 }, { x0: 205, y0: 690, x1: 300, y1: 712 }] : [];
  return { fragment_id: `fixture-${String(rank).padStart(2, "0")}`, source_type: "pdf_chunk", document_id: 1, pdf_page: 1, page_index: 0, page_label: "1", bbox: bbox.length ? { pageIndex: 0, rects: bbox.map((rect) => [rect.x0, rect.y0, rect.x1, rect.y1]) } : null, rects: bbox, selected_text: "PDF preview target text across the rendered page", locator_strategy: bbox.length ? "bbox" : "text", pdf_available: true, pdf_endpoint: "/api/v1/library/documents/1/pdf#page=1", warnings: [] };
}

async function submitSearch(query) {
  await window.webContents.executeJavaScript("document.querySelector('.localRetrievalQueryField input').focus()");
  await window.webContents.insertText(query);
  await waitFor("!document.querySelector('.localRetrievalSubmit').disabled");
  await window.webContents.executeJavaScript("document.querySelector('.localRetrievalSubmit').click()");
}

async function clickPreview(rank) {
  await window.webContents.executeJavaScript(`(() => { const card=document.querySelector('[data-result-index="${rank - 1}"]'); const button=[...card.querySelectorAll('button')].find((item)=>item.textContent.trim()==='预览'); if(!button) throw new Error('preview_button_missing'); button.click(); })()`);
}

async function waitFor(expression, timeout = 8000, label = "") {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    if (await window.webContents.executeJavaScript(`Boolean(${expression})`)) return;
    await delay(40);
  }
  const diagnostic = await window.webContents.executeJavaScript("document.querySelector('[data-testid=\\\"search-preview-panel\\\"]')?.innerText || document.body.innerText.slice(0, 800)");
  throw new Error(`dom_wait_timeout:${expression}:${label}:${diagnostic}`);
}

function json(response, value) {
  response.writeHead(200, { "content-type": "application/json; charset=utf-8" });
  response.end(JSON.stringify(value));
}

function fixturePdf(text) {
  const stream = `BT\n/F1 18 Tf\n72 700 Td\n(${text.replace(/[()\\]/g, "\\$&")}) Tj\nET\n`;
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    `<< /Length ${Buffer.byteLength(stream)} >>\nstream\n${stream}endstream`,
  ];
  let body = "%PDF-1.4\n";
  const offsets = [0];
  objects.forEach((object, index) => {
    offsets.push(Buffer.byteLength(body));
    body += `${index + 1} 0 obj\n${object}\nendobj\n`;
  });
  const xref = Buffer.byteLength(body);
  body += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  for (let index = 1; index < offsets.length; index += 1) body += `${String(offsets[index]).padStart(10, "0")} 00000 n \n`;
  body += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(body, "utf8");
}

function delay(ms) { return new Promise((resolvePromise) => setTimeout(resolvePromise, ms)); }

async function sendCallback(payload) {
  if (!CALLBACK_URL) return;
  const response = await fetch(CALLBACK_URL, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload) });
  if (!response.ok) throw new Error(`callback_failed:${response.status}`);
}
