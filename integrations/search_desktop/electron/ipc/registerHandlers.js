import { IPC_CHANNELS } from "./channels.js";

const ALLOWED_ROUTES = new Set([
  "/retrieval",
  "/import",
  "/read-shelf",
  "/object-review",
  "/workspace",
  "/system-status",
  "/settings",
]);

export function registerIpcHandlers({
  ipcMain,
  coordinator,
  launcherClient,
  autostart,
  settings,
  windowController,
  rendererOrigin,
}) {
  const trusted = (handler) => (event, ...argumentsList) => {
    assertTrustedIpcSender(event, {
      expectedWebContents: windowController.getWebContents(),
      rendererOrigin,
    });
    return handler(...argumentsList);
  };
  const handles = {
    [IPC_CHANNELS.runtimeStatus]: trusted(() => coordinator.refresh()),
    [IPC_CHANNELS.runtimeRestart]: trusted(() => coordinator.restart()),
    [IPC_CHANNELS.openLogs]: trusted(async () => {
      const result = await launcherClient.logs();
      return windowController.openLogsDirectory(result.logs_dir);
    }),
    [IPC_CHANNELS.autostartStatus]: trusted(() => autostart.status()),
    [IPC_CHANNELS.autostartSet]: trusted((enabled) => autostart.setEnabled(enabled)),
    [IPC_CHANNELS.settingsGet]: trusted(() => settings.read()),
    [IPC_CHANNELS.settingsUpdate]: trusted(async (patch) => {
      if (Object.hasOwn(patch || {}, "minimizeToTray")) {
        await windowController.setMinimizeToTray(patch.minimizeToTray);
      }
      return settings.read();
    }),
    [IPC_CHANNELS.openRoute]: trusted(async (route) => {
      if (!ALLOWED_ROUTES.has(route)) throw new Error("desktop_route_not_allowed");
      await windowController.openRoute(route);
      return { status: "opened", route };
    }),
    [IPC_CHANNELS.chatgptPause]: trusted(async (paused) => {
      if (typeof paused !== "boolean") throw new Error("invalid_chatgpt_pause_value");
      if (!windowController.tunnelPauseSupported) {
        return { status: "unsupported", error_code: "tunnel_pause_not_supported" };
      }
      return paused ? launcherClient.pauseTunnel() : launcherClient.resumeTunnel();
    }),
  };
  for (const [channel, handler] of Object.entries(handles)) ipcMain.handle(channel, handler);
  const onStatus = (status) => windowController.send(IPC_CHANNELS.runtimeSubscribe, status);
  coordinator.on("status", onStatus);
  return () => {
    coordinator.off("status", onStatus);
    for (const channel of Object.keys(handles)) ipcMain.removeHandler(channel);
  };
}

export function assertTrustedIpcSender(event, { expectedWebContents, rendererOrigin }) {
  if (!expectedWebContents || event?.sender !== expectedWebContents) {
    throw new Error("desktop_ipc_sender_not_allowed");
  }
  if (typeof expectedWebContents.isDestroyed === "function" && expectedWebContents.isDestroyed()) {
    throw new Error("desktop_ipc_sender_not_allowed");
  }
  if (!event.senderFrame || (
    expectedWebContents.mainFrame && event.senderFrame !== expectedWebContents.mainFrame
  )) {
    throw new Error("desktop_ipc_sender_not_allowed");
  }
  let expected;
  let actual;
  try {
    expected = new URL(rendererOrigin);
    actual = new URL(event.senderFrame.url);
  } catch {
    throw new Error("desktop_ipc_sender_not_allowed");
  }
  if (
    expected.protocol !== "http:" ||
    !["127.0.0.1", "localhost"].includes(expected.hostname) ||
    expected.username ||
    expected.password ||
    actual.protocol !== "http:" ||
    !["127.0.0.1", "localhost"].includes(actual.hostname) ||
    actual.username ||
    actual.password ||
    actual.origin !== expected.origin
  ) {
    throw new Error("desktop_ipc_sender_not_allowed");
  }
}
