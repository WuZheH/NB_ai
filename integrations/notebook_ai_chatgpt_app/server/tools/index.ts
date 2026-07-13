import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";

import type { NotebookClient } from "../notebookClient.js";
import { registerExportEvidenceTool } from "./exportEvidence.js";
import { registerFetchTool } from "./fetch.js";
import { registerSearchTool } from "./search.js";

export const NOTEBOOK_TOOL_NAMES = ["search", "fetch", "export_evidence"] as const;

export function registerNotebookTools(server: McpServer, client: NotebookClient): void {
  registerSearchTool(server, client);
  registerFetchTool(server, client);
  registerExportEvidenceTool(server, client);
}
