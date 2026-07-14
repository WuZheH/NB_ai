import { existsSync, mkdirSync } from "node:fs";
import { createServer } from "node:http";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { app, BrowserWindow } from "electron";
import { RendererServer } from "../../electron/runtime/rendererServer.js";

const DESKTOP_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const PROJECT_ROOT = resolve(DESKTOP_ROOT, "..", "..");
const FRONTEND_DIST = resolve(PROJECT_ROOT, "frontend", "dist");
const TEST_USER_DATA = resolve(PROJECT_ROOT, ".codex_tmp", "electron-route-styles-user-data");
const TEST_CRASH_DUMPS = resolve(PROJECT_ROOT, ".codex_tmp", "electron-route-styles-crashes");
const CALLBACK_URL = String(process.env.SEARCH_ROUTE_STYLES_CALLBACK_URL || "").trim();

mkdirSync(TEST_USER_DATA, { recursive: true });
mkdirSync(TEST_CRASH_DUMPS, { recursive: true });
app.setPath("userData", TEST_USER_DATA);
app.setPath("crashDumps", TEST_CRASH_DUMPS);
app.commandLine.appendSwitch("disable-gpu");

let rendererServer;
let fixtureServer;
let window;

app.whenReady().then(runProbe).catch(async (error) => {
  console.error(`SEARCH_ROUTE_STYLES_ERROR=${error?.stack || error}`);
  await sendCallback({ status: "error", error: String(error?.stack || error) }).catch(() => {});
  app.exit(1);
});

async function runProbe() {
  try {
    if (!existsSync(resolve(FRONTEND_DIST, "index.html"))) {
      throw new Error("frontend_production_build_missing");
    }
    fixtureServer = await startFixtureServer();
    const fixtureAddress = fixtureServer.address();
    if (!fixtureAddress || typeof fixtureAddress === "string") throw new Error("fixture_api_address_invalid");
    const fixtureOrigin = `http://127.0.0.1:${fixtureAddress.port}`;
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
      backgroundColor: "#f5f7fa",
      webPreferences: {
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        offscreen: true,
        backgroundThrottling: false,
      },
    });
    installFixtureRequestRouting(window, rendererOrigin, fixtureOrigin);
    const routes = {};
    for (const spec of routeSpecs()) {
      console.log(`SEARCH_ROUTE_STYLES_PATH=${spec.path}`);
      routes[spec.path] = await inspectRoute(window, rendererOrigin, spec);
    }
    console.log(`SEARCH_ROUTE_STYLES_METRICS=${JSON.stringify(routes)}`);
    await sendCallback({ status: "ok", routes });
  } catch (error) {
    console.error(`SEARCH_ROUTE_STYLES_ERROR=${error?.stack || error}`);
    await sendCallback({ status: "error", error: String(error?.stack || error) }).catch(() => {});
    process.exitCode = 1;
  } finally {
    await delay(150);
    if (window && !window.isDestroyed()) window.destroy();
    if (rendererServer) await rendererServer.stop();
    if (fixtureServer) await new Promise((resolvePromise) => fixtureServer.close(resolvePromise));
    app.quit();
  }
}

function routeSpecs() {
  return [
    {
      path: "/read-shelf",
      ready: ".documentCard",
      content: ".mainPanel",
      last: ".documentCard:last-child",
      interaction: { type: "click", selector: ".pageHeaderActions button:last-child" },
      registered: true,
    },
    {
      path: "/import",
      ready: ".importPreviewPage",
      content: ".mainPanel",
      last: ".importPreviewPage > :last-child",
      interaction: { type: "input", selector: ".importPreviewPage input:not([type=\"radio\"]):not([type=\"checkbox\"])" },
      registered: true,
    },
    {
      path: "/workspace",
      ready: ".notebookHomePage",
      content: ".notebookWorkspaceSurface",
      last: ".notebookHomeSection:last-of-type .notebookCard",
      interaction: { type: "focus", selector: ".notebookHomeTabs button" },
      registered: true,
    },
    {
      path: "/settings",
      ready: ".notebookHomePage",
      content: ".notebookWorkspaceSurface",
      last: ".notebookHomeSection:last-of-type .notebookCard",
      interaction: { type: "focus", selector: ".notebookHomeTabs button" },
      registered: false,
    },
    {
      path: "/system-status",
      ready: ".notebookHomePage",
      content: ".notebookWorkspaceSurface",
      last: ".notebookHomeSection:last-of-type .notebookCard",
      interaction: { type: "focus", selector: ".notebookHomeTabs button" },
      registered: false,
    },
  ];
}

async function inspectRoute(browserWindow, rendererOrigin, spec) {
  await browserWindow.loadURL(`${rendererOrigin}${spec.path}`);
  await delay(100);
  await waitFor(browserWindow.webContents, `document.querySelector(${JSON.stringify(spec.ready)})`);
  await delay(120);
  const interaction = await exerciseInteraction(browserWindow.webContents, spec.interaction);
  const metrics = await browserWindow.webContents.executeJavaScript(`(${collectRouteMetrics.toString()})(${JSON.stringify(spec)})`);
  return { ...metrics, interaction };
}

async function exerciseInteraction(webContents, interaction) {
  const focused = await webContents.executeJavaScript(`(() => {
    const element = document.querySelector(${JSON.stringify(interaction.selector)});
    if (!element || element.disabled) return false;
    element.focus();
    if (${JSON.stringify(interaction.type)} === "click") element.click();
    return document.activeElement === element;
  })()`);
  if (interaction.type === "input" && focused) {
    await webContents.insertText("route-style-probe");
  }
  await delay(80);
  const valueChanged = interaction.type !== "input" || await webContents.executeJavaScript(`(() => {
    const element = document.querySelector(${JSON.stringify(interaction.selector)});
    return String(element?.value || "").includes("route-style-probe");
  })()`);
  return { focused, valueChanged };
}

function collectRouteMetrics(spec) {
  function describeScroll(element) {
    const style = getComputedStyle(element);
    return {
      clientHeight: element.clientHeight,
      scrollHeight: element.scrollHeight,
      scrollTop: element.scrollTop,
      overflowY: style.overflowY,
    };
  }

  function findScrollableAncestor(element) {
    let current = element?.parentElement || null;
    while (current) {
      const style = getComputedStyle(current);
      if (["auto", "scroll"].includes(style.overflowY) && current.scrollHeight > current.clientHeight + 1) return current;
      current = current.parentElement;
    }
    return null;
  }

  function elementLabel(element) {
    if (element.id) return `#${element.id}`;
    const classes = String(element.className || "").trim().split(/\s+/).filter(Boolean);
    return classes.length ? `.${classes.join(".")}` : element.tagName.toLowerCase();
  }

  function findBlockingOverflowAncestor(element) {
    let current = element?.parentElement || null;
    while (current) {
      const style = getComputedStyle(current);
      if (style.overflowY === "hidden" && current.scrollHeight > current.clientHeight + 1) return current;
      current = current.parentElement;
    }
    return null;
  }

  const root = document.scrollingElement;
  const content = document.querySelector(spec.content);
  const last = document.querySelector(spec.last) || content?.lastElementChild || content;
  const lastBefore = last?.getBoundingClientRect();
  const scrollHost = findScrollableAncestor(last);
  const blockingOverflowAncestor = findBlockingOverflowAncestor(last);
  let maxScrollReached = false;
  if (scrollHost) {
    scrollHost.scrollTop = scrollHost.scrollHeight;
    const maximum = Math.max(0, scrollHost.scrollHeight - scrollHost.clientHeight);
    maxScrollReached = Math.abs(scrollHost.scrollTop - maximum) <= 2;
    last?.scrollIntoView({ block: "end", inline: "nearest" });
  }
  const lastAfter = last?.getBoundingClientRect();
  const hostRect = scrollHost?.getBoundingClientRect();
  const visibleBounds = hostRect || { top: 0, bottom: innerHeight, height: innerHeight };
  const lastFitsViewport = Boolean(lastAfter && lastAfter.height <= visibleBounds.height + 1);
  const lastVisible = Boolean(lastAfter
    && lastAfter.bottom <= visibleBounds.bottom + 1
    && lastAfter.bottom >= visibleBounds.top - 1
    && (!lastFitsViewport || lastAfter.top >= visibleBounds.top - 1));
  return {
    registered: spec.registered,
    pathname: location.pathname,
    viewport: { width: innerWidth, height: innerHeight },
    bodyTextLength: document.body.innerText.trim().length,
    contentPresent: Boolean(content),
    root: describeScroll(root),
    bodyOverflowY: getComputedStyle(document.body).overflowY,
    content: content ? describeScroll(content) : null,
    scrollHost: scrollHost ? { selector: elementLabel(scrollHost), ...describeScroll(scrollHost) } : null,
    maxScrollReached,
    needsScroll: Boolean(lastBefore && lastBefore.bottom > innerHeight + 1),
    lastVisible,
    lastRect: lastAfter ? { top: lastAfter.top, bottom: lastAfter.bottom } : null,
    hostRect: hostRect ? { top: hostRect.top, bottom: hostRect.bottom } : null,
    clippedWithoutScrollHost: Boolean(lastBefore && lastBefore.bottom > innerHeight + 1 && !scrollHost),
    blockingOverflowAncestor: blockingOverflowAncestor ? elementLabel(blockingOverflowAncestor) : null,
    interactiveCount: document.querySelectorAll("button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled)").length,
    workspaceFallback: !spec.registered && Boolean(document.querySelector(".notebookHomePage")),
  };
}

function installFixtureRequestRouting(browserWindow, rendererOrigin, fixtureOrigin) {
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
  browserWindow.webContents.session.webRequest.onHeadersReceived(
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
  browserWindow.webContents.session.webRequest.onBeforeRequest(
    { urls: ["http://127.0.0.1:8000/api/*"] },
    (details, callback) => {
      const requested = new URL(details.url);
      callback({ redirectURL: `${rendererOrigin}${requested.pathname}${requested.search}` });
    },
  );
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
    if (url.pathname === "/api/v1/library/read-shelf") {
      response.end(JSON.stringify({ status: "ok", items: readShelfFixture() }));
      return;
    }
    response.writeHead(404).end(JSON.stringify({ detail: "route_style_fixture_not_found" }));
  });
  return new Promise((resolvePromise, reject) => {
    server.once("error", () => reject(new Error("route_style_fixture_start_failed")));
    server.listen(0, "127.0.0.1", () => resolvePromise(server));
  });
}

function readShelfFixture() {
  return Array.from({ length: 24 }, (_, index) => ({
    document_id: index + 1,
    title: `样式回归书架文档 ${index + 1} — 足够长的标题`,
    document_type: index % 2 ? "paper" : "book",
    object_import_mode: index % 3 ? "full_document" : "chaptered",
    chapter_count: index % 3 ? 0 : 8,
    chunk_count: 40 + index,
    duplicate_count: 1,
  }));
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
  if (!response.ok) throw new Error(`route_style_callback_failed:${response.status}`);
}
