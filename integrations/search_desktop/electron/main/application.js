import { resolveDesktopConfig } from "./config.js";
import { LauncherClient } from "../runtime/launcherClient.js";
import { RuntimeCoordinator } from "../runtime/runtimeCoordinator.js";
import { RendererServer } from "../runtime/rendererServer.js";
import { SettingsStore } from "../ipc/settingsStore.js";
import { AutostartService } from "../ipc/autostartService.js";
import { registerIpcHandlers } from "../ipc/registerHandlers.js";
import { createWindowController } from "./window.js";
import { createTrayController } from "../tray/createTray.js";
import { loadSearchDesignTokens } from "./designTokens.js";
import { STARTUP_STAGE } from "./startupLogger.js";

export async function createSearchDesktop(electron, { buildIdentity, startupLogger, windowMode } = {}) {
  const { app, BrowserWindow, Menu, Tray, ipcMain, nativeImage, shell } = electron;
  await startStage(startupLogger, STARTUP_STAGE.CONFIG_RESOLVED);
  const config = resolveDesktopConfig({
    userDataPath: app.getPath("userData"),
    executablePath: app.getPath("exe"),
    resourcesPath: process.resourcesPath,
    isPackaged: app.isPackaged,
  });
  await completeStage(startupLogger, STARTUP_STAGE.CONFIG_RESOLVED, config.desktopRuntimeLog);
  const settings = new SettingsStore(config.settingsPath);
  const launcherClient = new LauncherClient(config);
  const coordinator = new RuntimeCoordinator(launcherClient);
  const autostart = new AutostartService({
    desktopRoot: config.desktopRoot,
    executablePath: app.getPath("exe"),
    isPackaged: app.isPackaged,
  });
  const renderer = new RendererServer({
    frontendDist: config.frontendDist,
    fallbackFile: config.rendererFallback,
    designSystemRoot: config.designSystemRoot,
    rendererAssets: config.rendererAssets,
    backendUrl: config.backendUrl,
    port: config.rendererPort,
  });
  await startStage(startupLogger, STARTUP_STAGE.DESIGN_TOKENS_LOADED);
  const designTokens = await loadSearchDesignTokens(config.designSystemRoot);
  await completeStage(startupLogger, STARTUP_STAGE.DESIGN_TOKENS_LOADED);
  await startStage(startupLogger, STARTUP_STAGE.RENDERER_STARTED);
  const rendererOrigin = await renderer.start();
  await completeStage(startupLogger, STARTUP_STAGE.RENDERER_STARTED);
  const windowController = createWindowController({
    BrowserWindow,
    shell,
    config,
    rendererOrigin,
    settingsStore: settings,
    designTokens,
    buildIdentity,
    windowMode,
  });
  const unregisterIpc = registerIpcHandlers({
    ipcMain,
    coordinator,
    launcherClient,
    autostart,
    settings,
    windowController,
    rendererOrigin,
  });
  await startStage(startupLogger, STARTUP_STAGE.RUNTIME_CHECKED, {
    result: "started",
    ...config.desktopRuntimeLog,
  });
  const launcherSpawnCountBefore = launcherClient.spawnCount;
  const runtimeCheck = coordinator.ensureReady()
    .then(async (status) => {
      await completeStage(startupLogger, STARTUP_STAGE.RUNTIME_CHECKED, {
        result: "ready",
        error_code: null,
        launcher_spawned: launcherClient.spawnCount > launcherSpawnCountBefore,
        desktop_started_runtime: coordinator.startedByDesktop,
        runtime_owner: status?.runtime_owner || (coordinator.startedByDesktop ? "managed-by-search" : "external"),
        ...config.desktopRuntimeLog,
      });
      return status;
    })
    .catch(async (error) => {
      const errorCode = safeErrorCode(error);
      if (!coordinator.lastStatus || coordinator.lastStatus.error_code !== errorCode) {
        coordinator.update({ status: "error", state: "unavailable", error_code: errorCode });
      }
      await failStage(startupLogger, STARTUP_STAGE.RUNTIME_CHECKED, errorCode, {
        launcher_spawned: launcherClient.spawnCount > launcherSpawnCountBefore,
        desktop_started_runtime: coordinator.startedByDesktop,
        ...config.desktopRuntimeLog,
      });
      return coordinator.lastStatus;
    });
  await startStage(startupLogger, STARTUP_STAGE.WINDOW_CREATED);
  await windowController.create();
  await completeStage(startupLogger, STARTUP_STAGE.WINDOW_CREATED);

  let trayController;
  let shutdownStarted = false;
  const fullyQuit = async () => {
    if (shutdownStarted) return;
    shutdownStarted = true;
    windowController.setQuitting(true);
    try {
      await coordinator.stopIfOwned();
    } finally {
      unregisterIpc();
      trayController?.destroy();
      windowController.destroy();
      await renderer.stop();
      app.quit();
    }
  };
  await startStage(startupLogger, STARTUP_STAGE.TRAY_CREATED);
  trayController = createTrayController({
    Tray,
    Menu,
    nativeImage,
    coordinator,
    windowController,
    onFullyQuit: fullyQuit,
    iconPath: config.desktopIcon,
    designTokens,
  });
  await completeStage(startupLogger, STARTUP_STAGE.TRAY_CREATED);
  await startStage(startupLogger, STARTUP_STAGE.READY);
  await completeStage(startupLogger, STARTUP_STAGE.READY);

  // Keep backend bootstrap observable without blocking the desktop window.
  // A slow or unavailable backend must leave Search usable so the compact
  // status view can report the real failure and offer a recheck.
  void runtimeCheck;

  return { config, coordinator, renderer, windowController, fullyQuit };
}

async function startStage(startupLogger, stage, details) {
  try {
    await startupLogger?.startStage(stage, details);
  } catch {
    // Startup observability must not become a startup dependency.
  }
}

async function completeStage(startupLogger, stage, details) {
  try {
    await startupLogger?.completeStage(stage, details);
  } catch {
    // Startup observability must not become a startup dependency.
  }
}

async function failStage(startupLogger, stage, errorCode, details) {
  try {
    await startupLogger?.failStage(stage, errorCode, details);
  } catch {
    // Startup observability must not become a startup dependency.
  }
}

function safeErrorCode(error) {
  const value = String(error?.message || "desktop_runtime_start_failed");
  return /^[A-Za-z0-9_.-]{1,96}$/.test(value) ? value : "desktop_runtime_start_failed";
}
