import { existsSync, mkdirSync } from "node:fs";
import { createServer } from "node:http";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { app, BrowserWindow, ipcMain } from "electron";

const PACKAGED_ROOT = resolve(process.env.SEARCH_R3_PACKAGED_ROOT || "");
const USER_DATA = resolve(process.env.SEARCH_STATUS_USER_DATA || "");
const CRASH_DUMPS = resolve(process.env.SEARCH_STATUS_CRASH_DUMPS || "");
const FRONTEND_DIST = join(PACKAGED_ROOT, "resources", "search-assets", "frontend");
const DESIGN_SYSTEM = join(PACKAGED_ROOT, "resources", "search-assets", "design-system");
const APP_ROOT = join(PACKAGED_ROOT, "resources", "app");
const RENDERER_ASSETS = join(APP_ROOT, "renderer");
const PRELOAD = join(APP_ROOT, "electron", "preload", "index.cjs");
const RENDERER_SERVER = join(APP_ROOT, "electron", "runtime", "rendererServer.js");

for (const required of [
  join(FRONTEND_DIST, "index.html"),
  PRELOAD,
  RENDERER_SERVER,
]) {
  if (!existsSync(required)) throw new Error(`search_r3_packaged_probe_missing:${required}`);
}
mkdirSync(USER_DATA, { recursive: true });
mkdirSync(CRASH_DUMPS, { recursive: true });
mkdirSync(process.env.TEMP, { recursive: true });
app.setPath("userData", USER_DATA);
app.setPath("crashDumps", CRASH_DUMPS);
app.commandLine.appendSwitch("disable-gpu");

let renderer;
let backend;
let window;
let runtimeCalls = 0;

app.whenReady().then(run).catch(fail);

async function run() {
  try {
    registerIpcFixtures();
    backend = await startBackendFixture();
    const address = backend.address();
    const backendUrl = `http://127.0.0.1:${address.port}`;
    const { RendererServer } = await import(pathToFileURL(RENDERER_SERVER));
    renderer = new RendererServer({
      frontendDist: FRONTEND_DIST,
      fallbackFile: join(RENDERER_ASSETS, "missing-build.html"),
      designSystemRoot: DESIGN_SYSTEM,
      rendererAssets: RENDERER_ASSETS,
      backendUrl,
      port: 0,
    });
    const origin = await renderer.start();
    window = new BrowserWindow({
      width: 1200,
      height: 820,
      show: false,
      webPreferences: {
        preload: PRELOAD,
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        offscreen: true,
        backgroundThrottling: false,
      },
    });
    window.webContents.on("console-message", (_event, level, message) => {
      process.stderr.write(`R3_STATUS_RENDERER_CONSOLE=${level}:${message}\n`);
    });
    await window.loadURL(`${origin}/retrieval`);
    await waitFor("[...document.querySelectorAll('.navLabel')].some((node) => node.textContent.trim() === '设置')");
    const settingsBefore = await window.webContents.executeJavaScript(`(() => {
      const label = [...document.querySelectorAll('.navLabel')].find((node) => node.textContent.trim() === '设置');
      const button = label.closest('button');
      return { disabled: button.disabled, soonVisible: Boolean(button.querySelector('.navSoon')) };
    })()`);
    await window.webContents.executeJavaScript(`(() => {
      const label = [...document.querySelectorAll('.navLabel')].find((node) => node.textContent.trim() === '设置');
      label.closest('button').click();
    })()`);
    await waitFor("location.pathname === '/settings' && document.querySelectorAll('.desktopServiceRow').length === 5");
    await waitFor("document.querySelector('.desktopSettingsFooter button') && !document.querySelector('.desktopSettingsFooter button').disabled");

    const runtimeCallsBefore = runtimeCalls;
    const statusData = await window.webContents.executeJavaScript(`(() => {
      const details = document.querySelector('.desktopTechnicalDetails');
      details.querySelector('summary').click();
      const refresh = document.querySelector('.desktopSettingsFooter button');
      const labels = [...document.querySelectorAll('.desktopServiceRow > span')].map((node) => node.textContent.trim());
      const heading = document.querySelector('.desktopSettingsPage h1').textContent.trim();
      const buildId = window.searchDesktop.buildId;
      const rendererAssetVersion = window.searchDesktop.rendererAssetVersion;
      const loadedAssets = performance.getEntriesByType('resource')
        .map((entry) => {
          const pathname = new URL(entry.name).pathname;
          return pathname.startsWith('/') ? pathname.slice(1) : pathname;
        })
        .filter((path) => path.startsWith('assets/'));
      refresh.click();
      return {
        labels,
        heading,
        detailsOpen: details.open,
        refreshLabel: refresh.textContent.trim(),
        buildId,
        rendererAssetVersion,
        loadedAssets,
      };
    })()`);
    await waitUntil(() => runtimeCalls > runtimeCallsBefore);
    const result = {
      status: "ok",
      buildId: statusData.buildId,
      rendererAssetVersion: statusData.rendererAssetVersion,
      settings: { ...settingsBefore, heading: statusData.heading },
      statusLabels: statusData.labels,
      technicalDetailsExpanded: statusData.detailsOpen,
      refresh: {
        label: statusData.refreshLabel,
        operational: runtimeCalls > runtimeCallsBefore,
        runtimeCallsBefore,
        runtimeCallsAfter: runtimeCalls,
      },
      loadedAssets: statusData.loadedAssets,
    };
    process.stdout.write(`R3_STATUS_DOM_RESULT=${JSON.stringify(result)}\n`);
    await shutdown(0);
  } catch (error) {
    await fail(error);
  }
}

function registerIpcFixtures() {
  ipcMain.handle("search:runtime-status", () => {
    runtimeCalls += 1;
    return runtimeFixture(runtimeCalls);
  });
  ipcMain.handle("search:settings-get", () => ({ minimizeToTray: true }));
  ipcMain.handle("search:autostart-status", () => ({ available: false, installed: false }));
  ipcMain.handle("search:settings-update", () => ({ minimizeToTray: true }));
  ipcMain.handle("search:autostart-set", () => ({ available: false, installed: false }));
}

function runtimeFixture(sequence) {
  return {
    state: "local_ready_tunnel_missing",
    updated_at: `2026-07-17T00:00:${String(sequence).padStart(2, "0")}+08:00`,
    tunnel_state: "quick_tunnel_online",
    tunnel_type: "quick",
    tunnel_url: "https://example.trycloudflare.com",
    components: {
      fastapi: { state: "external", port: 8000, pid: 29148, owner: "external" },
      mcp: { state: "external", port: 8787, pid: 18396, owner: "external" },
      tunnel: { state: "ready", pid: 40036, owner: "external" },
    },
  };
}

function startBackendFixture() {
  const server = createServer((request, response) => {
    response.setHeader("content-type", "application/json; charset=utf-8");
    if (request.url === "/api/v1/library/read-shelf") {
      response.end(JSON.stringify({ status: "ok", documents: [] }));
      return;
    }
    response.writeHead(404).end(JSON.stringify({ detail: "fixture_not_found" }));
  });
  return new Promise((resolvePromise, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolvePromise(server));
  });
}

async function waitFor(expression, timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await window.webContents.executeJavaScript(`Boolean(${expression})`)) return;
    await delay(100);
  }
  const diagnostics = await window.webContents.executeJavaScript(`({
    href: location.href,
    title: document.title,
    text: document.body?.innerText?.slice(0, 1000) || '',
    serviceRows: document.querySelectorAll('.desktopServiceRow').length,
    hasBridge: Boolean(window.searchDesktop),
  })`).catch((error) => ({ diagnosticsError: String(error) }));
  throw new Error(`search_r3_packaged_dom_timeout:${expression}:${JSON.stringify(diagnostics)}`);
}

async function waitUntil(predicate, timeoutMs = 5_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await delay(50);
  }
  throw new Error("search_r3_refresh_not_observed");
}

async function fail(error) {
  process.stderr.write(`R3_STATUS_DOM_ERROR=${error?.stack || error}\n`);
  await shutdown(1);
}

async function shutdown(code) {
  if (window && !window.isDestroyed()) window.destroy();
  if (renderer) await renderer.stop();
  if (backend) await new Promise((resolvePromise) => backend.close(resolvePromise));
  app.exit(code);
}

function delay(milliseconds) {
  return new Promise((resolvePromise) => setTimeout(resolvePromise, milliseconds));
}
