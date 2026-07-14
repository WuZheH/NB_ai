import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { IPC_CHANNELS } from "../electron/ipc/channels.js";
import {
  assertTrustedIpcSender,
  registerIpcHandlers,
} from "../electron/ipc/registerHandlers.js";

const RENDERER_ORIGIN = "http://127.0.0.1:5173";

function trustedSender(url = `${RENDERER_ORIGIN}/retrieval`) {
  const mainFrame = { url };
  const webContents = { mainFrame, isDestroyed: () => false };
  return {
    event: { sender: webContents, senderFrame: mainFrame },
    webContents,
  };
}

function createFixture() {
  const handlers = new Map();
  const calls = [];
  const sender = trustedSender();
  const coordinator = new EventEmitter();
  coordinator.refresh = async () => { calls.push("refresh"); return { status: "ready" }; };
  coordinator.restart = async () => { calls.push("restart"); return { status: "ready" }; };
  const launcherClient = {
    async logs() { calls.push("logs"); return { logs_dir: "D:\\logs" }; },
    async pauseTunnel() { calls.push("pause"); return { status: "paused" }; },
    async resumeTunnel() { calls.push("resume"); return { status: "ready" }; },
  };
  const autostart = {
    async status() { calls.push("autostart-status"); return { status: "not_installed" }; },
    async setEnabled(value) { calls.push(`autostart-${value}`); return { status: "updated" }; },
  };
  const settings = {
    async read() { calls.push("settings-read"); return { minimizeToTray: true }; },
  };
  const windowController = {
    tunnelPauseSupported: true,
    getWebContents: () => sender.webContents,
    async openLogsDirectory() { calls.push("open-logs"); return { status: "opened" }; },
    async setMinimizeToTray() { calls.push("settings-update"); },
    async openRoute(route) { calls.push(`route-${route}`); },
    send() {},
  };
  const ipcMain = {
    handle(channel, handler) { handlers.set(channel, handler); },
    removeHandler(channel) { handlers.delete(channel); },
  };
  const cleanup = registerIpcHandlers({
    ipcMain,
    coordinator,
    launcherClient,
    autostart,
    settings,
    windowController,
    rendererOrigin: RENDERER_ORIGIN,
  });
  return { calls, cleanup, handlers, sender, windowController };
}

test("trusted IPC requires the expected main WebContents and exact loopback renderer origin", () => {
  const trusted = trustedSender();
  assert.doesNotThrow(() => assertTrustedIpcSender(trusted.event, {
    expectedWebContents: trusted.webContents,
    rendererOrigin: RENDERER_ORIGIN,
  }));
  assert.throws(() => assertTrustedIpcSender(
    { ...trusted.event, sender: { mainFrame: trusted.event.senderFrame } },
    { expectedWebContents: trusted.webContents, rendererOrigin: RENDERER_ORIGIN },
  ), /desktop_ipc_sender_not_allowed/);
  assert.throws(() => assertTrustedIpcSender(
    { ...trusted.event, senderFrame: { url: `${RENDERER_ORIGIN}/iframe` } },
    { expectedWebContents: trusted.webContents, rendererOrigin: RENDERER_ORIGIN },
  ), /desktop_ipc_sender_not_allowed/);
  const wrongOrigin = trustedSender("http://127.0.0.1:5174/retrieval");
  assert.throws(() => assertTrustedIpcSender(wrongOrigin.event, {
    expectedWebContents: wrongOrigin.webContents,
    rendererOrigin: RENDERER_ORIGIN,
  }), /desktop_ipc_sender_not_allowed/);
});

test("every privileged IPC handler rejects an unknown sender before side effects", async () => {
  const fixture = createFixture();
  const unknown = trustedSender().event;
  const argumentsByChannel = {
    [IPC_CHANNELS.autostartSet]: [true],
    [IPC_CHANNELS.settingsUpdate]: [{ minimizeToTray: false }],
    [IPC_CHANNELS.openRoute]: ["/retrieval"],
    [IPC_CHANNELS.chatgptPause]: [true],
  };
  for (const [channel, handler] of fixture.handlers) {
    await assert.rejects(
      async () => handler(unknown, ...(argumentsByChannel[channel] || [])),
      /desktop_ipc_sender_not_allowed/,
      channel,
    );
  }
  assert.deepEqual(fixture.calls, []);
  fixture.cleanup();
});

test("openRoute awaits renderer navigation before reporting success", async () => {
  const fixture = createFixture();
  let release;
  fixture.windowController.openRoute = async (route) => {
    fixture.calls.push(`route-start-${route}`);
    await new Promise((resolvePromise) => { release = resolvePromise; });
    fixture.calls.push(`route-finish-${route}`);
  };
  const handler = fixture.handlers.get(IPC_CHANNELS.openRoute);
  let settled = false;
  const operation = handler(fixture.sender.event, "/retrieval").then((value) => {
    settled = true;
    return value;
  });
  await new Promise((resolvePromise) => setImmediate(resolvePromise));
  assert.equal(settled, false);
  assert.deepEqual(fixture.calls, ["route-start-/retrieval"]);
  release();
  assert.deepEqual(await operation, { status: "opened", route: "/retrieval" });
  assert.deepEqual(fixture.calls, ["route-start-/retrieval", "route-finish-/retrieval"]);
  fixture.cleanup();
});
