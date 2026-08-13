import { existsSync, mkdirSync } from "node:fs";
import { createServer } from "node:http";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { app, BrowserWindow } from "electron";
import { RendererServer } from "../../electron/runtime/rendererServer.js";

const DESKTOP_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const PROJECT_ROOT = resolve(DESKTOP_ROOT, "..", "..");
const FRONTEND_DIST = resolve(PROJECT_ROOT, "frontend", "dist");
const TEST_TMP_ROOT = resolve(process.env.SEARCH_TEST_TMP_ROOT || resolve(PROJECT_ROOT, ".codex_tmp"));
const TEST_USER_DATA = resolve(TEST_TMP_ROOT, "electron-scroll-user-data");
const TEST_CRASH_DUMPS = resolve(TEST_TMP_ROOT, "electron-scroll-crashes");
const CALLBACK_URL = String(process.env.SEARCH_SCROLL_CALLBACK_URL || "").trim();

mkdirSync(TEST_USER_DATA, { recursive: true });
mkdirSync(TEST_CRASH_DUMPS, { recursive: true });
app.setPath("userData", TEST_USER_DATA);
app.setPath("crashDumps", TEST_CRASH_DUMPS);
app.commandLine.appendSwitch("disable-gpu");

let rendererServer;
let fixtureServer;
let window;

app.whenReady().then(runProbe).catch(async (error) => {
  console.error(`SEARCH_SCROLL_PROBE_ERROR=${error?.stack || error}`);
  await sendCallback({ status: "error", error: String(error?.stack || error) }).catch(() => {});
  app.exit(1);
});

async function runProbe() {
try {
  console.log("SEARCH_SCROLL_PROBE_STAGE=validate-build");
  if (!existsSync(resolve(FRONTEND_DIST, "index.html"))) {
    throw new Error("frontend_production_build_missing");
  }
  console.log("SEARCH_SCROLL_PROBE_STAGE=app-ready");
  fixtureServer = await startFixtureServer();
  const fixtureAddress = fixtureServer.address();
  if (!fixtureAddress || typeof fixtureAddress === "string") {
    throw new Error("fixture_api_address_invalid");
  }
  const fixtureOrigin = `http://127.0.0.1:${fixtureAddress.port}`;
  console.log("SEARCH_SCROLL_PROBE_STAGE=fixture-ready");
  rendererServer = new RendererServer({
    frontendDist: FRONTEND_DIST,
    fallbackFile: resolve(DESKTOP_ROOT, "renderer", "missing-build.html"),
    designSystemRoot: resolve(PROJECT_ROOT, "packages", "search-design-system", "src"),
    rendererAssets: resolve(DESKTOP_ROOT, "renderer"),
    backendUrl: fixtureOrigin,
    port: 0,
  });
  const rendererOrigin = await rendererServer.start();
  console.log("SEARCH_SCROLL_PROBE_STAGE=renderer-ready");
  window = new BrowserWindow({
    width: 1023,
    height: 767,
    useContentSize: true,
    show: false,
    skipTaskbar: true,
    backgroundColor: "#f5f7fa",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      offscreen: true,
      backgroundThrottling: false,
    },
  });
  const fixtureCsp = [
    "default-src 'self'",
    "img-src 'self' data: blob:",
    "style-src 'self' 'unsafe-inline'",
    "script-src 'self'",
    `connect-src 'self' http://127.0.0.1:8000 ${fixtureOrigin}`,
    "font-src 'self' data:",
    "object-src 'none'",
    "frame-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
  ].join("; ");
  window.webContents.session.webRequest.onHeadersReceived(
    { urls: [`${rendererOrigin}/*`] },
    (details, callback) => {
      const responseHeaders = { ...details.responseHeaders };
      for (const name of Object.keys(responseHeaders)) {
        if (name.toLowerCase() === "content-security-policy") delete responseHeaders[name];
      }
      responseHeaders["Content-Security-Policy"] = [fixtureCsp];
      callback({ responseHeaders });
    },
  );
  window.webContents.session.webRequest.onBeforeRequest(
    { urls: ["http://127.0.0.1:8000/api/*"] },
    (details, callback) => {
      const requested = new URL(details.url);
      callback({ redirectURL: `${rendererOrigin}${requested.pathname}${requested.search}` });
    },
  );
  window.webContents.on("console-message", (_event, level, message) => {
    console.log(`SEARCH_SCROLL_RENDERER_CONSOLE=${level}:${message}`);
  });

  await window.loadURL(`${rendererOrigin}/retrieval`);
  console.log("SEARCH_SCROLL_PROBE_STAGE=page-loaded");
  console.log(`SEARCH_SCROLL_BODY=${JSON.stringify(await window.webContents.executeJavaScript("document.body.innerText.slice(0, 500)"))}`);
  await waitFor(window.webContents, "document.querySelector('[data-testid=\"retrieval-results-scroll\"]')");
  console.log("SEARCH_SCROLL_PROBE_STAGE=search-dom-ready");
  const unifiedSearch = await window.webContents.executeJavaScript(`(() => {
    const labels = [...document.querySelectorAll('.navItem')].map((item) => item.textContent.trim());
    return {
      searchEntryCount: labels.filter((label) => label === '搜索').length,
      forbiddenEntryVisible: labels.some((label) => ['资料库搜索', '本地证据检索', '资料库高级搜索'].includes(label)),
      unifiedPagePresent: Boolean(document.querySelector('.localRetrievalPage')),
      heading: document.querySelector('.localRetrievalHeader h1')?.textContent.trim(),
    };
  })()`);
  await window.webContents.executeJavaScript(`(() => {
    const filters = document.querySelector('.localRetrievalFilters');
    const source = filters.querySelector('select');
    const documentId = filters.querySelector('input[type="number"]');
    const setSelect = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set;
    const setInput = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    setSelect.call(source, 'pdf_chunk');
    source.dispatchEvent(new Event('change', { bubbles: true }));
    setInput.call(documentId, '1');
    documentId.dispatchEvent(new Event('input', { bubbles: true }));
    documentId.dispatchEvent(new Event('change', { bubbles: true }));
  })()`);
  await waitFor(window.webContents, "document.querySelector('.localRetrievalFilters select')?.value === 'pdf_chunk' && document.querySelector('.localRetrievalFilters input[type=number]')?.value === '1'");
  await setInputAndSearch(window.webContents, "滚动测试");
  console.log("SEARCH_SCROLL_PROBE_STAGE=search-submitted");
  await waitFor(window.webContents, "document.querySelectorAll('[data-result-index]').length === 12 || document.querySelector('.localRetrievalState.error')");
  const resultCount = await window.webContents.executeJavaScript("document.querySelectorAll('[data-result-index]').length");
  if (resultCount !== 12) {
    const errorText = await window.webContents.executeJavaScript("document.querySelector('.localRetrievalState.error')?.textContent || 'unknown_search_error'");
    throw new Error(`search_fixture_failed:${errorText}`);
  }

  const resultStateBeforeBasket = await window.webContents.executeJavaScript(`(() => {
    const pane = document.querySelector('[data-testid="retrieval-results-scroll"]');
    pane.scrollTop = Math.min(420, pane.scrollHeight - pane.clientHeight);
    globalThis.__searchScrollProbeResultsPane = pane;
    return { scrollTop: pane.scrollTop };
  })()`);
  await clickButton(window.webContents, "当前页全选");
  await waitFor(window.webContents, "document.querySelectorAll('.localEvidenceBasketItem').length === 12");
  const resultStateAfterBasket = await window.webContents.executeJavaScript(`(() => {
    const pane = document.querySelector('[data-testid="retrieval-results-scroll"]');
    return {
      sameNode: globalThis.__searchScrollProbeResultsPane === pane,
      scrollTop: pane.scrollTop,
    };
  })()`);
  const resultStateBeforePreview = await window.webContents.executeJavaScript(`(() => {
    const pane = document.querySelector('[data-testid="retrieval-results-scroll"]');
    pane.scrollTop = Math.min(620, pane.scrollHeight - pane.clientHeight);
    return { scrollTop: pane.scrollTop };
  })()`);
  await clickButton(window.webContents, "预览");
  await waitFor(window.webContents, "document.querySelector('[data-testid=\"search-preview-scroll\"]')");
  await clickButton(window.webContents, "文本");
  await waitFor(window.webContents, "document.querySelector('[role=\"tab\"][aria-selected=\"true\"]')?.textContent.trim() === '文本'");
  const resultStateAfterPreview = await window.webContents.executeJavaScript(`(() => {
    const pane = document.querySelector('[data-testid="retrieval-results-scroll"]');
    return {
      sameNode: globalThis.__searchScrollProbeResultsPane === pane,
      scrollTop: pane.scrollTop,
    };
  })()`);

  const navigationScroll = await window.webContents.executeJavaScript(`(() => {
    const results = document.querySelector('[data-testid="retrieval-results-scroll"]');
    const preview = document.querySelector('[data-testid="search-preview-scroll"]');
    const basket = document.querySelector('[data-testid="evidence-basket-scroll"]');
    results.scrollTop = Math.min(480, results.scrollHeight - results.clientHeight);
    preview.scrollTop = Math.min(100, preview.scrollHeight - preview.clientHeight);
    basket.scrollTop = Math.min(50, basket.scrollHeight - basket.clientHeight);
    results.dispatchEvent(new Event('scroll'));
    preview.dispatchEvent(new Event('scroll'));
    basket.dispatchEvent(new Event('scroll'));
    return { results: results.scrollTop, preview: preview.scrollTop, basket: basket.scrollTop };
  })()`);
  await window.webContents.executeJavaScript(`(() => {
    window.history.pushState({}, '', '/workspace');
    window.dispatchEvent(new PopStateEvent('popstate'));
  })()`);
  await waitFor(window.webContents, "[...document.querySelectorAll('button')].some((item) => item.textContent.trim() === '← 返回搜索')");
  await clickButton(window.webContents, "← 返回搜索");
  await waitFor(window.webContents, "document.querySelector('.localRetrievalQueryField input')?.value === '滚动测试' && document.querySelectorAll('[data-result-index]').length === 12 && document.querySelectorAll('.localEvidenceBasketItem').length === 12");
  await waitFor(window.webContents, `Math.abs(document.querySelector('[data-testid="retrieval-results-scroll"]').scrollTop - ${navigationScroll.results}) <= 1`);
  const navigationRestore = await window.webContents.executeJavaScript(`(() => ({
    query: document.querySelector('.localRetrievalQueryField input')?.value,
    resultCount: document.querySelectorAll('[data-result-index]').length,
    basketCount: document.querySelectorAll('.localEvidenceBasketItem').length,
    previewTitle: document.querySelector('[data-testid="search-preview-panel"] h2')?.textContent,
    searchMode: document.querySelector('.localRetrievalMode [aria-pressed="true"]')?.textContent.trim(),
    sourceFilter: document.querySelector('.localRetrievalFilters select')?.value,
    documentFilter: document.querySelector('.localRetrievalFilters input[type="number"]')?.value,
    includeContext: document.querySelector('.localRetrievalFilters input[type="checkbox"]')?.checked,
    previewView: document.querySelector('[role="tab"][aria-selected="true"]')?.textContent.trim(),
    resultsScroll: document.querySelector('[data-testid="retrieval-results-scroll"]')?.scrollTop,
    previewScroll: document.querySelector('[data-testid="search-preview-scroll"]')?.scrollTop,
    basketScroll: document.querySelector('[data-testid="evidence-basket-scroll"]')?.scrollTop,
    expectedScroll: ${JSON.stringify(navigationScroll)},
  }))()`);
  await window.webContents.executeJavaScript(`(() => {
    window.history.pushState({}, '', '/library-search');
    window.dispatchEvent(new PopStateEvent('popstate'));
  })()`);
  await waitFor(window.webContents, "window.location.pathname === '/retrieval' && Boolean(document.querySelector('.localRetrievalPage'))");
  const legacyRoute = await window.webContents.executeJavaScript(`(() => ({
    path: window.location.pathname,
    unifiedPagePresent: Boolean(document.querySelector('.localRetrievalPage')),
    query: document.querySelector('.localRetrievalQueryField input')?.value,
    searchEntryCount: [...document.querySelectorAll('.navItem')].filter((item) => item.textContent.trim() === '搜索').length,
  }))()`);

  const interactionMetrics = await exerciseNativeScrolling(window.webContents);
  console.log("SEARCH_SCROLL_PROBE_STAGE=interactions-complete");
  const metrics = await window.webContents.executeJavaScript(`(() => {
    const root = document.scrollingElement;
    const results = document.querySelector('[data-testid="retrieval-results-scroll"]');
    const preview = document.querySelector('[data-testid="search-preview-scroll"]');
    const basket = document.querySelector('[data-testid="evidence-basket-scroll"]');
    const workspace = document.querySelector('[data-testid="retrieval-workspace"]');
    const lastResult = results.lastElementChild;
    const lastPreview = preview.lastElementChild;
    const lastBasket = basket.lastElementChild;
    results.scrollTop = results.scrollHeight;
    preview.scrollTop = preview.scrollHeight;
    basket.scrollTop = basket.scrollHeight;
    const visibleAtEnd = (item, pane) => {
      const itemRect = item.getBoundingClientRect();
      const paneRect = pane.getBoundingClientRect();
      return itemRect.bottom <= paneRect.bottom + 1 && itemRect.top >= paneRect.top - 1;
    };
    const describe = (element) => ({
      clientHeight: element.clientHeight,
      scrollHeight: element.scrollHeight,
      scrollTop: element.scrollTop,
      overflowY: getComputedStyle(element).overflowY,
    });
    return {
      viewport: { width: innerWidth, height: innerHeight },
      root: describe(root),
      rootOverflowY: getComputedStyle(document.body).overflowY,
      workspace: describe(workspace),
      results: { ...describe(results), lastVisible: visibleAtEnd(lastResult, results), count: results.children.length },
      preview: { ...describe(preview), lastVisible: visibleAtEnd(lastPreview, preview) },
      basket: { ...describe(basket), lastVisible: visibleAtEnd(lastBasket, basket), count: basket.children.length },
      railPosition: getComputedStyle(document.querySelector('.searchResultRail')).position,
      navigationOutlined: [...document.querySelectorAll('.navItem')].some((item) => getComputedStyle(item).borderTopColor !== 'rgba(0, 0, 0, 0)' && getComputedStyle(item).borderTopStyle !== 'none'),
      evidenceBasketEnglishVisible: document.body.innerText.includes('Evidence Basket'),
      resultState: {
        basketSameNode: ${JSON.stringify(resultStateAfterBasket.sameNode)},
        basketScrollBefore: ${JSON.stringify(resultStateBeforeBasket.scrollTop)},
        basketScrollAfter: ${JSON.stringify(resultStateAfterBasket.scrollTop)},
        previewSameNode: ${JSON.stringify(resultStateAfterPreview.sameNode)},
        previewScrollBefore: ${JSON.stringify(resultStateBeforePreview.scrollTop)},
        previewScrollAfter: ${JSON.stringify(resultStateAfterPreview.scrollTop)},
      },
    };
  })()`);
  metrics.interactions = interactionMetrics;
  metrics.navigationRestore = navigationRestore;
  metrics.unifiedSearch = unifiedSearch;
  metrics.legacyRoute = legacyRoute;
  console.log(`SEARCH_SCROLL_METRICS=${JSON.stringify(metrics)}`);
  await sendCallback({ status: "ok", metrics });
} catch (error) {
  console.error(`SEARCH_SCROLL_PROBE_ERROR=${error?.stack || error}`);
  await sendCallback({ status: "error", error: String(error?.stack || error) }).catch(() => {});
  process.exitCode = 1;
} finally {
  await delay(200);
  if (window && !window.isDestroyed()) window.destroy();
  if (rendererServer) await rendererServer.stop();
  if (fixtureServer) await new Promise((resolvePromise) => fixtureServer.close(resolvePromise));
  app.quit();
}
}

function startFixtureServer() {
  const server = createServer((request, response) => {
    const url = new URL(request.url || "/", "http://127.0.0.1");
    response.setHeader("access-control-allow-origin", "*");
    response.setHeader("access-control-allow-methods", "GET,POST,OPTIONS");
    response.setHeader("access-control-allow-headers", "content-type,accept");
    response.setHeader("content-type", "application/json; charset=utf-8");
    if (request.method === "OPTIONS") {
      response.writeHead(204).end();
      return;
    }
    if (url.pathname === "/api/v1/retrieval/notebook-search") {
      response.end(JSON.stringify(searchFixture()));
      return;
    }
    if (url.pathname.startsWith("/api/v1/retrieval/fragments/")) {
      const fragmentId = decodeURIComponent(url.pathname.split("/").at(-1));
      response.end(JSON.stringify({ fragment: detailFixture(fragmentId) }));
      return;
    }
    if (url.pathname === "/api/v1/library/read-shelf") {
      response.end(JSON.stringify({ status: "ok", documents: [] }));
      return;
    }
    response.writeHead(404).end(JSON.stringify({ detail: "fixture_not_found" }));
  });
  return new Promise((resolvePromise, reject) => {
    server.once("error", () => reject(new Error("fixture_api_start_failed")));
    server.listen(0, "127.0.0.1", () => resolvePromise(server));
  });
}

function searchFixture() {
  return {
    status: "ok",
    mode: "high_quality_notebook_search_v1",
    backend: "lancedb",
    reranker_model: "Qwen3-Reranker-0.6B",
    counts: { coverage: { documents: 4, source_types: 4 } },
    warnings: [],
    results: Array.from({ length: 12 }, (_, index) => resultFixture(index + 1)),
  };
}

function resultFixture(rank) {
  const sourceTypes = ["pdf_chunk", "zotero_annotation_comment", "zotero_child_note", "zotero_inspiration_note"];
  const sourceType = sourceTypes[(rank - 1) % sourceTypes.length];
  const base = {
    fragment_id: `scroll-fixture-${String(rank).padStart(2, "0")}-0000-0000-000000000000`,
    source_type: sourceType,
    document_id: ((rank - 1) % 4) + 1,
    document_title: `生产构建滚动测试文档 ${rank} — 带有足够长的标题用于验证紧凑结果布局`,
    pdf_page: rank,
    page_label: String(rank),
    final_rank: rank,
    final_score: 1 - rank / 100,
    reranker_score: 0.9 - rank / 100,
    semantic_score: 0.8 - rank / 100,
    provenance: [],
    tags: [],
    warnings: [],
  };
  if (sourceType === "pdf_chunk") return { ...base, text: excerpt(rank) };
  return { ...base, note_text: `用户笔记 ${rank}。${excerpt(rank)}`, selected_text: `对应选中文本 ${rank}。` };
}

function detailFixture(fragmentId) {
  const rank = Math.max(1, Number(fragmentId.match(/fixture-(\d+)/)?.[1] || 1));
  return {
    ...resultFixture(rank),
    fragment_id: fragmentId,
    text: Array.from({ length: 80 }, (_, index) => `完整片段第 ${index + 1} 行：这是用于验证右侧预览独立滚动的生产构建内容。`).join("\n"),
    context_before: Array.from({ length: 20 }, (_, index) => `前文 ${index + 1}`).join("\n"),
    context_after: Array.from({ length: 20 }, (_, index) => `后文 ${index + 1}`).join("\n"),
    chunk_id: `chunk-${rank}`,
    content_hash: `hash-${rank}`,
  };
}

function excerpt(rank) {
  return `结果 ${rank}：这是一段固定的论文或阅读笔记摘要，用来验证十二条结果在一屏之外仍由左侧列表独立承载，而不是扩大整个 Electron 窗口。`;
}

async function setInputAndSearch(webContents, query) {
  await webContents.executeJavaScript(`(() => {
    const input = document.querySelector('.localRetrievalQueryField input');
    input.focus();
  })()`);
  await webContents.insertText(query);
  await waitFor(webContents, "!document.querySelector('.localRetrievalSubmit').disabled");
  await webContents.executeJavaScript("document.querySelector('.localRetrievalSubmit').click()");
}

async function clickButton(webContents, label) {
  const expression = `(() => {
    const button = [...document.querySelectorAll('button')].find((item) => item.textContent.trim() === ${JSON.stringify(label)});
    if (!button) throw new Error('button_not_found:${label}');
    button.click();
  })()`;
  await webContents.executeJavaScript(expression);
}

async function exerciseNativeScrolling(webContents) {
  const bounds = await webContents.executeJavaScript(`(() => {
    const rail = document.querySelector('.searchResultRail');
    globalThis.__searchScrollRailDisplay = rail?.style.display || '';
    if (rail) rail.style.display = 'none';
    const pane = document.querySelector('[data-testid="retrieval-results-scroll"]');
    pane.scrollTop = 0;
    pane.focus();
    const rect = pane.getBoundingClientRect();
    return { x: Math.floor(rect.left + rect.width / 2), y: Math.floor(rect.top + rect.height / 2), right: Math.floor(rect.right - 4), top: Math.floor(rect.top + 16), bottom: Math.floor(rect.bottom - 16) };
  })()`);
  await delay(120);
  webContents.sendInputEvent({ type: "mouseMove", x: bounds.x, y: bounds.y });
  for (let attempt = 0; attempt < 3; attempt += 1) {
    webContents.sendInputEvent({ type: "mouseWheel", x: bounds.x, y: bounds.y, deltaX: 0, deltaY: -240, canScroll: true });
    await delay(100);
  }
  let wheelTop = await scrollTop(webContents);
  if (wheelTop === 0) {
    webContents.sendInputEvent({ type: "mouseWheel", x: bounds.x, y: bounds.y, deltaX: 0, deltaY: 240, canScroll: true });
    await delay(200);
    wheelTop = await scrollTop(webContents);
  }

  await resetAndFocus(webContents);
  webContents.sendInputEvent({ type: "keyDown", keyCode: "PageDown" });
  webContents.sendInputEvent({ type: "keyUp", keyCode: "PageDown" });
  await delay(100);
  const pageDownTop = await scrollTop(webContents);

  await resetAndFocus(webContents);
  webContents.sendInputEvent({ type: "keyDown", keyCode: "End" });
  webContents.sendInputEvent({ type: "keyUp", keyCode: "End" });
  await delay(100);
  const endTop = await scrollTop(webContents);

  webContents.sendInputEvent({ type: "keyDown", keyCode: "Home" });
  webContents.sendInputEvent({ type: "keyUp", keyCode: "Home" });
  await delay(500);
  const homeTop = await scrollTop(webContents);

  await resetAndFocus(webContents);
  await delay(100);
  webContents.sendInputEvent({ type: "mouseMove", x: bounds.right, y: bounds.top });
  webContents.sendInputEvent({ type: "mouseDown", x: bounds.right, y: bounds.top, button: "left", clickCount: 1 });
  for (let step = 1; step <= 6; step += 1) {
    const y = Math.round(bounds.top + ((bounds.bottom - bounds.top) * step) / 6);
    webContents.sendInputEvent({ type: "mouseMove", x: bounds.right, y, button: "left" });
    await delay(30);
  }
  webContents.sendInputEvent({ type: "mouseUp", x: bounds.right, y: bounds.bottom, button: "left", clickCount: 1 });
  await delay(200);
  const scrollbarDragTop = await scrollTop(webContents);

  await webContents.executeJavaScript(`(() => {
    const rail = document.querySelector('.searchResultRail');
    if (rail) rail.style.display = globalThis.__searchScrollRailDisplay || '';
  })()`);
  return { wheelTop, pageDownTop, endTop, homeTop, scrollbarDragTop };
}

async function resetAndFocus(webContents) {
  await webContents.executeJavaScript(`(() => {
    const pane = document.querySelector('[data-testid="retrieval-results-scroll"]');
    pane.scrollTop = 0;
    pane.focus();
  })()`);
}

function scrollTop(webContents) {
  return webContents.executeJavaScript("document.querySelector('[data-testid=\"retrieval-results-scroll\"]').scrollTop");
}

async function waitFor(webContents, expression, timeoutMs = 8000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (await webContents.executeJavaScript(`Boolean(${expression})`)) return;
    await delay(40);
  }
  throw new Error(`dom_wait_timeout:${expression}`);
}

function delay(ms) {
  return new Promise((resolvePromise) => setTimeout(resolvePromise, ms));
}

async function sendCallback(payload) {
  if (!CALLBACK_URL) return;
  const response = await fetch(CALLBACK_URL, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(`scroll_callback_failed:${response.status}`);
}
