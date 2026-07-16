const { contextBridge, ipcRenderer } = require("electron");
// Sandboxed preload scripts cannot require arbitrary sibling JSON files.
// These public build identifiers are verified against product-metadata.json
// by the Desktop contract suite before packaging.
const productMetadata = Object.freeze({
  version: "0.1.3",
  buildId: "20260717-formal-runtime-self-contained",
  rendererAssetVersion: "0.1.3-formal-runtime-self-contained",
});

const channels = Object.freeze({
  runtimeStatus: "search:runtime-status",
  runtimeRestart: "search:runtime-restart",
  runtimeSubscribe: "search:runtime-subscribe",
  openLogs: "search:open-logs",
  autostartStatus: "search:autostart-status",
  autostartSet: "search:autostart-set",
  settingsGet: "search:settings-get",
  settingsUpdate: "search:settings-update",
  openRoute: "search:open-route",
  chatgptPause: "search:chatgpt-pause",
});

contextBridge.exposeInMainWorld("searchDesktop", Object.freeze({
  productName: "Search",
  productVersion: productMetadata.version,
  buildId: productMetadata.buildId,
  rendererAssetVersion: productMetadata.rendererAssetVersion,
  getRuntimeStatus: () => ipcRenderer.invoke(channels.runtimeStatus),
  restartRuntime: () => ipcRenderer.invoke(channels.runtimeRestart),
  openLogs: () => ipcRenderer.invoke(channels.openLogs),
  getAutostartStatus: () => ipcRenderer.invoke(channels.autostartStatus),
  setAutostartEnabled: (enabled) => ipcRenderer.invoke(channels.autostartSet, enabled === true),
  getSettings: () => ipcRenderer.invoke(channels.settingsGet),
  updateSettings: (patch) => ipcRenderer.invoke(channels.settingsUpdate, patch),
  openRoute: (route) => ipcRenderer.invoke(channels.openRoute, route),
  setChatGptPaused: (paused) => ipcRenderer.invoke(channels.chatgptPause, paused === true),
  onRuntimeStatus: (listener) => {
    if (typeof listener !== "function") return () => {};
    const handler = (_event, status) => listener(status);
    ipcRenderer.on(channels.runtimeSubscribe, handler);
    return () => ipcRenderer.removeListener(channels.runtimeSubscribe, handler);
  },
}));
