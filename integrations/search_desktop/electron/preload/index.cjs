const { contextBridge, ipcRenderer } = require("electron");
const buildIdentity = readBuildIdentity(process.argv);

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
});

contextBridge.exposeInMainWorld("searchDesktop", Object.freeze({
  productName: "Search",
  productVersion: buildIdentity.version,
  buildId: buildIdentity.build_id,
  buildMode: buildIdentity.build_mode,
  sourceCommit: buildIdentity.source_commit,
  sourceBranch: buildIdentity.source_branch,
  getRuntimeStatus: () => ipcRenderer.invoke(channels.runtimeStatus),
  restartRuntime: () => ipcRenderer.invoke(channels.runtimeRestart),
  openLogs: () => ipcRenderer.invoke(channels.openLogs),
  getAutostartStatus: () => ipcRenderer.invoke(channels.autostartStatus),
  setAutostartEnabled: (enabled) => ipcRenderer.invoke(channels.autostartSet, enabled === true),
  getSettings: () => ipcRenderer.invoke(channels.settingsGet),
  updateSettings: (patch) => ipcRenderer.invoke(channels.settingsUpdate, patch),
  openRoute: (route) => ipcRenderer.invoke(channels.openRoute, route),
  onRuntimeStatus: (listener) => {
    if (typeof listener !== "function") return () => {};
    const handler = (_event, status) => listener(status);
    ipcRenderer.on(channels.runtimeSubscribe, handler);
    return () => ipcRenderer.removeListener(channels.runtimeSubscribe, handler);
  },
}));

function readBuildIdentity(argv) {
  const prefix = "--search-build-identity=";
  const argument = argv.find((value) => String(value).startsWith(prefix));
  if (!argument) throw new Error("search_build_identity_argument_missing");
  let value;
  try {
    value = JSON.parse(decodeURIComponent(String(argument).slice(prefix.length)));
  } catch {
    throw new Error("search_build_identity_argument_invalid");
  }
  if (
    value?.schema_version !== "search.build-identity.v1"
    || value?.product !== "Search"
    || !["development", "packaged"].includes(value?.build_mode)
    || typeof value?.version !== "string"
    || typeof value?.build_id !== "string"
    || typeof value?.source_commit !== "string"
    || typeof value?.source_branch !== "string"
  ) {
    throw new Error("search_build_identity_argument_invalid");
  }
  if (
    value.build_mode === "packaged"
    && (
      value.build_id === "development"
      || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(value.build_id)
      || !/^[0-9a-f]{40}$/.test(value.source_commit)
    )
  ) {
    throw new Error("search_build_identity_argument_invalid");
  }
  return Object.freeze(value);
}
