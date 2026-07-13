import { pathToFileURL } from "node:url";

export { createNotebookMcpServer } from "./app.js";
export { NotebookBackendError, NotebookClient } from "./notebookClient.js";
export { requireUnauthenticatedDevelopment } from "./security.js";
export { NOTEBOOK_TOOL_NAMES } from "./tools/index.js";
export { RESOURCE_MIME_TYPE } from "./widgetResource.js";
export { startNotebookMcpHttpServer } from "./http.js";

import { startNotebookMcpHttpServer } from "./http.js";

const entryPoint = process.argv[1] ? pathToFileURL(process.argv[1]).href : "";
if (import.meta.url === entryPoint) {
  startNotebookMcpHttpServer().catch((error) => {
    const message = error instanceof Error ? error.message : "MCP server startup failed.";
    console.error(JSON.stringify({ event: "mcp_start_failed", error_code: "MCP_STARTUP_ERROR", message }));
    process.exitCode = 1;
  });
}
