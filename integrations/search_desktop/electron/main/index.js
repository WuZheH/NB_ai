import * as electron from "electron";
import { createSearchDesktop } from "./application.js";
import { loadBuildIdentityForApp } from "./buildIdentity.js";
import {
  createSingleInstanceData,
  resolveSecondInstanceAction,
  waitForRequesterExit,
} from "./singleInstance.js";
import { resolveWindowMode } from "./window.js";
import { createStartupLoggerForApp, reportStartupFailure } from "./startupLogger.js";

const { app } = electron;
app.setName("Search");
const windowMode = resolveWindowMode();
const singleInstanceData = createSingleInstanceData({ windowMode, argv: process.argv });

if (!app.requestSingleInstanceLock(singleInstanceData)) {
  app.quit();
} else {
  let desktop = null;
  let startupLogger = null;
  let pendingFullyQuit = singleInstanceData.action === "fully_quit";
  const fullyQuitWhenReady = async () => {
    if (!desktop) {
      pendingFullyQuit = true;
      return;
    }
    await desktop.fullyQuit();
  };
  app.on("second-instance", (_event, _argv, _workingDirectory, additionalData = {}) => {
    if (resolveSecondInstanceAction({ windowMode, additionalData }) === "fully_quit") {
      void waitForRequesterExit(additionalData.requesterPid)
        .then(() => fullyQuitWhenReady())
        .catch(() => {});
      return;
    }
    desktop?.windowController.show();
  });
  app.on("activate", () => desktop?.windowController.show());
  app.whenReady()
    .then(async () => {
      const buildIdentity = await loadBuildIdentityForApp(app);
      startupLogger = await createStartupLoggerForApp(app, { buildIdentity });
      desktop = await createSearchDesktop(electron, { buildIdentity, startupLogger, windowMode });
      if (pendingFullyQuit) await desktop.fullyQuit();
    })
    .catch(async (error) => {
      await reportStartupFailure({ error, startupLogger, app });
    });
}
