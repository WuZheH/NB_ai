import { join } from "node:path";
import { encodeBuildIdentityArgument } from "./buildIdentity.js";

export function resolveWindowMode({ env = process.env, argv = process.argv } = {}) {
  const testMode = env.SEARCH_ELECTRON_TEST_MODE === "1" || argv.includes("--search-test-mode");
  const finalUserAcceptance = (
    env.SEARCH_FINAL_USER_ACCEPTANCE === "1"
    || argv.includes("--final-user-acceptance")
  );
  const hidden = testMode && !finalUserAcceptance;
  return Object.freeze({ testMode, finalUserAcceptance, hidden });
}

export function createWindowController({ BrowserWindow, shell, config, rendererOrigin, settingsStore, designTokens, buildIdentity, windowMode = { hidden: false } }) {
  let window = null;
  let isQuitting = false;
  let minimizeToTray = true;
  const origin = new URL(rendererOrigin).origin;

  async function create() {
    minimizeToTray = (await settingsStore.read()).minimizeToTray;
    window = new BrowserWindow({
      title: "Search",
      width: 1280,
      height: 800,
      minWidth: 900,
      minHeight: 640,
      show: false,
      skipTaskbar: windowMode.hidden,
      icon: config.desktopIcon,
      backgroundColor: designTokens.background,
      autoHideMenuBar: true,
      webPreferences: {
        preload: join(config.desktopRoot, "electron", "preload", "index.cjs"),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        webSecurity: true,
        allowRunningInsecureContent: false,
        additionalArguments: [encodeBuildIdentityArgument(buildIdentity)],
      },
    });
    window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    window.webContents.on("will-navigate", (event, target) => {
      if (new URL(target).origin !== origin) event.preventDefault();
    });
    window.on("close", (event) => {
      if (!isQuitting && minimizeToTray) {
        event.preventDefault();
        window.hide();
      }
    });
    window.once("ready-to-show", () => {
      if (!windowMode.hidden) window.show();
    });
    await openRoute(config.defaultRoute);
    return window;
  }

  async function openRoute(route) {
    const target = new URL(route, rendererOrigin).toString();
    if (new URL(target).origin !== origin) throw new Error("renderer_route_not_allowed");
    if (window) await window.loadURL(target);
  }

  return {
    create,
    openRoute,
    getWebContents() {
      return window && !window.isDestroyed() ? window.webContents : null;
    },
    show() {
      if (!window || windowMode.hidden) return;
      if (window.isMinimized()) window.restore();
      window.show();
      window.focus();
    },
    setQuitting(value) {
      isQuitting = Boolean(value);
    },
    async setMinimizeToTray(value) {
      minimizeToTray = Boolean(value);
      await settingsStore.update({ minimizeToTray });
      return { minimizeToTray };
    },
    send(channel, payload) {
      if (window && !window.isDestroyed()) window.webContents.send(channel, payload);
    },
    openLogsDirectory(path) {
      return shell.openPath(path).then((error) => ({ status: error ? "error" : "opened", error_code: error ? "logs_open_failed" : null }));
    },
    destroy() {
      if (window && !window.isDestroyed()) window.destroy();
      window = null;
    },
  };
}
