import * as electron from "electron";
import { createSearchDesktop } from "./application.js";
import { createStartupLoggerForApp, reportStartupFailure } from "./startupLogger.js";

const { app } = electron;
app.setName("Search");

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  let desktop = null;
  let startupLogger = null;
  app.on("second-instance", () => desktop?.windowController.show());
  app.on("activate", () => desktop?.windowController.show());
  app.whenReady()
    .then(async () => {
      startupLogger = await createStartupLoggerForApp(app);
      desktop = await createSearchDesktop(electron, { startupLogger });
    })
    .catch(async (error) => {
      await reportStartupFailure({ error, startupLogger, app });
    });
}
