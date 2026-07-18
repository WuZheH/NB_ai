import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { IPC_CHANNELS } from "../electron/ipc/channels.js";
import { normalizeSettingsPatch } from "../electron/ipc/settingsStore.js";
import { resolveRendererPort, validateLoopbackUrl } from "../electron/main/config.js";
import { resolveWindowMode } from "../electron/main/window.js";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const productMetadata = JSON.parse(await readFile(join(ROOT, "electron", "product-metadata.json"), "utf8"));

test("product brand is Search while NOTEBOOK_AI runtime compatibility remains", async () => {
  const packageJson = JSON.parse(await readFile(join(ROOT, "package.json"), "utf8"));
  assert.equal(packageJson.productName, "Search");
  assert.equal(packageJson.version, "0.1.4");
  assert.deepEqual(productMetadata, {
    productName: "Search",
    version: "0.1.4",
    buildId: "20260717-search-0.1.4-github-release-convergence",
    rendererAssetVersion: "0.1.4-github-release-convergence",
  });
  assert.equal(packageJson.devDependencies.electron, "37.2.6");
  const config = await readFile(join(ROOT, "electron", "main", "config.js"), "utf8");
  assert.match(config, /SEARCH_PYTHON/);
  assert.match(config, /SEARCH_NODE/);
  assert.match(config, /SEARCH_DATA_DIR/);
  assert.match(config, /SEARCH_CLOUDFLARED/);
  assert.match(config, /NOTEBOOK_AI_PYTHON_EXE/);
  assert.match(config, /NOTEBOOK_AI_NODE_EXE/);
  assert.match(config, /notebook_ai_launcher\.py/);
  assert.match(config, /SEARCH_RENDERER_PORT/);
});

test("runtime launcher is a direct hidden child with controlled output pipes", async () => {
  const launcher = await readFile(join(ROOT, "electron", "runtime", "launcherClient.js"), "utf8");
  assert.match(launcher, /windowsHide:\s*true/);
  assert.match(launcher, /shell:\s*false/);
  assert.match(launcher, /stdio:\s*\["ignore",\s*"pipe",\s*"pipe"\]/);
  assert.match(launcher, /NOTEBOOK_AI_PYTHON_EXE:\s*this\.config\.pythonExe/);
  assert.match(launcher, /NOTEBOOK_AI_NODE_EXE:\s*this\.config\.nodeExe/);
  assert.match(launcher, /NOTEBOOK_AI_RUNTIME_ROOT:\s*this\.config\.runtimeRoot/);
  assert.match(launcher, /NOTEBOOK_AI_DATA_PROJECT_ROOT:\s*this\.config\.dataProjectRoot/);
  assert.match(launcher, /SEARCH_DATA_DIR:\s*this\.config\.dataDir/);
  assert.match(launcher, /SEARCH_PYTHON:\s*this\.config\.pythonExe/);
  assert.match(launcher, /runtime_prerequisites_missing/);
  assert.match(launcher, /cwd:\s*this\.config\.runtimeRoot/);
  assert.match(launcher, /delete environment\.PYTHONPATH/);
  assert.match(launcher, /delete environment\.NODE_PATH/);
  assert.match(launcher, /delete environment\.NOTEBOOK_AI_PROJECT_ROOT/);
  assert.doesNotMatch(launcher, /powershell\.exe|cmd\.exe|\.cmd["']|\.bat["']/i);
});

test("Electron security and lifecycle contracts are explicit", async () => {
  const [entry, windowSource, tray] = await Promise.all([
    readFile(join(ROOT, "electron", "main", "index.js"), "utf8"),
    readFile(join(ROOT, "electron", "main", "window.js"), "utf8"),
    readFile(join(ROOT, "electron", "tray", "createTray.js"), "utf8"),
  ]);
  assert.match(entry, /requestSingleInstanceLock/);
  assert.match(windowSource, /contextIsolation:\s*true/);
  assert.match(windowSource, /nodeIntegration:\s*false/);
  assert.match(windowSource, /sandbox:\s*true/);
  assert.match(windowSource, /skipTaskbar:\s*windowMode\.hidden/);
  assert.match(windowSource, /if \(!windowMode\.hidden\) window\.show\(\)/);
  assert.match(windowSource, /event\.preventDefault\(\)/);
  assert.match(tray, /打开 Search/);
  assert.match(tray, /完全退出/);
  assert.match(tray, /重新检查/);
  assert.doesNotMatch(tray, /重新启动后台|暂停 ChatGPT 连接|恢复 ChatGPT 连接/);
  const application = await readFile(join(ROOT, "electron", "main", "application.js"), "utf8");
  assert.ok(application.indexOf("await coordinator.ensureReady()") < application.indexOf("await windowController.create()"));
});

test("automated Electron mode stays hidden while normal and acceptance launches stay visible", () => {
  assert.deepEqual(resolveWindowMode({ env: {}, argv: ["Search.exe"] }), {
    testMode: false,
    finalUserAcceptance: false,
    hidden: false,
  });
  assert.equal(resolveWindowMode({ env: { SEARCH_ELECTRON_TEST_MODE: "1" }, argv: [] }).hidden, true);
  assert.equal(resolveWindowMode({ env: {}, argv: ["--search-test-mode"] }).hidden, true);
  assert.equal(resolveWindowMode({
    env: { SEARCH_ELECTRON_TEST_MODE: "1" },
    argv: ["--final-user-acceptance"],
  }).hidden, false);
});

test("desktop navigation exposes real status and settings routes", async () => {
  const handlers = await readFile(join(ROOT, "electron", "ipc", "registerHandlers.js"), "utf8");
  assert.match(handlers, /"\/system-status"/);
  assert.match(handlers, /"\/settings"/);
  assert.match(handlers, /autostartStatus/);
  assert.match(handlers, /settingsUpdate/);
  assert.match(handlers, /assertTrustedIpcSender/);
  assert.match(handlers, /await windowController\.openRoute\(route\)/);
});

test("renderer and API URLs are loopback-only", () => {
  assert.equal(validateLoopbackUrl("http://127.0.0.1:8000"), "http://127.0.0.1:8000");
  assert.throws(() => validateLoopbackUrl("https://example.com"), /loopback/);
  assert.throws(() => validateLoopbackUrl("http://user:pass@127.0.0.1:8000"), /loopback/);
});

test("desktop renderer port keeps a stable default and validates explicit overrides", () => {
  assert.equal(resolveRendererPort(undefined), 5173);
  assert.equal(resolveRendererPort(" 55173 "), 55173);
  for (const value of ["0", "1023", "65536", "5173.5", "not-a-port"]) {
    assert.throws(() => resolveRendererPort(value), /SEARCH_RENDERER_PORT_invalid/);
  }
});

test("preload surface is allowlisted and contains no raw process or filesystem bridge", async () => {
  const preload = await readFile(join(ROOT, "electron", "preload", "index.cjs"), "utf8");
  for (const channel of Object.values(IPC_CHANNELS)) assert.match(preload, new RegExp(channel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  assert.doesNotMatch(preload, /require\(["'](?:node:)?(?:fs|child_process)["']\)/);
  assert.doesNotMatch(preload, /ipcRenderer\.send\(/);
  assert.match(preload, /productVersion/);
  assert.match(preload, /rendererAssetVersion/);
  assert.match(preload, new RegExp(productMetadata.buildId));
  assert.match(preload, new RegExp(productMetadata.rendererAssetVersion));
  assert.doesNotMatch(preload, /require\(["']\.\.\/product-metadata\.json["']\)/);
});

test("desktop startup never installs, builds, or rebuilds indexes", async () => {
  const files = [
    "electron/main/index.js",
    "electron/main/application.js",
    "electron/runtime/launcherClient.js",
  ];
  const source = (await Promise.all(files.map((path) => readFile(join(ROOT, path), "utf8")))).join("\n");
  assert.doesNotMatch(source, /npm\s+(?:install|ci|run\s+build)/i);
  assert.doesNotMatch(source, /build_zotero_note_vectors|build_vector/i);
  assert.doesNotMatch(source, /pause[_-]tunnel|resume[_-]tunnel|tunnel-status/);
});

test("desktop renderer consumes the shared Search design system", async () => {
  const html = await readFile(join(ROOT, "renderer", "missing-build.html"), "utf8");
  const css = await readFile(join(ROOT, "renderer", "missing-build.css"), "utf8");
  assert.match(html, /__search_design__\/tokens\.css/);
  assert.match(html, /__search_design__\/components\.css/);
  assert.doesNotMatch(css, /#[0-9a-f]{3,8}/i);
  assert.match(css, /var\(--search-primary/);
  const [windowSource, traySource, tokenLoader] = await Promise.all([
    readFile(join(ROOT, "electron", "main", "window.js"), "utf8"),
    readFile(join(ROOT, "electron", "tray", "createTray.js"), "utf8"),
    readFile(join(ROOT, "electron", "main", "designTokens.js"), "utf8"),
  ]);
  assert.match(windowSource, /designTokens\.background/);
  assert.match(traySource, /designTokens\.primary/);
  assert.doesNotMatch(`${windowSource}\n${traySource}`, /#[0-9a-f]{6}/i);
  assert.match(tokenLoader, /tokens\.css/);
});

test("desktop settings cannot persist secrets or private content", () => {
  assert.deepEqual(normalizeSettingsPatch({ minimizeToTray: false }), { minimizeToTray: false });
  assert.throws(() => normalizeSettingsPatch({ apiKey: "secret" }), /not_allowed/);
  assert.throws(() => normalizeSettingsPatch({ fragmentId: "private" }), /not_allowed/);
});
