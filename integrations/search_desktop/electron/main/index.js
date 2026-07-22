import * as electron from "electron";
import { createSearchDesktop } from "./application.js";
import { loadBuildIdentityForApp } from "./buildIdentity.js";
import { resolveSecondInstanceAction, resolveWindowMode } from "./window.js";
import { createStartupLoggerForApp, reportStartupFailure } from "./startupLogger.js";

const { app } = electron;
app.setName("Search");
const windowMode = resolveWindowMode();

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  let desktop = null;
  let startupLogger = null;
  app.on("second-instance", (_event, argv = []) => {
    if (resolveSecondInstanceAction({ windowMode, argv }) === "fully_quit") {
      void desktop?.fullyQuit();
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
    })
    .catch(async (error) => {
      await reportStartupFailure({ error, startupLogger, app });
    });
}
