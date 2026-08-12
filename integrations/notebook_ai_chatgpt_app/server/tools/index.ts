import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";

import type { NotebookClient } from "../notebookClient.js";
import { registerDeleteDocumentTool } from "./deleteDocument.js";
import { registerDeletePreviewTool } from "./deletePreview.js";
import { registerExportEvidenceTool } from "./exportEvidence.js";
import { registerFetchTool } from "./fetch.js";
import { registerImportDocumentTool } from "./importDocument.js";
import { registerImportPreviewTool } from "./importPreview.js";
import { registerImportStatusTool } from "./importStatus.js";
import { registerIntegrityReportTool } from "./integrityReport.js";
import { registerListLibraryTool } from "./listLibrary.js";
import { registerSearchTool } from "./search.js";

export const NOTEBOOK_TOOL_NAMES = [
  "search",
  "fetch",
  "export_evidence",
  "list_library",
  "integrity_report",
  "import_preview",
  "import_document",
  "import_status",
  "delete_preview",
  "delete_document",
] as const;

export function registerNotebookTools(server: McpServer, client: NotebookClient): void {
  registerSearchTool(server, client);
  registerFetchTool(server, client);
  registerExportEvidenceTool(server, client);
  registerListLibraryTool(server, client);
  registerIntegrityReportTool(server, client);
  registerImportPreviewTool(server, client);
  registerImportDocumentTool(server, client);
  registerImportStatusTool(server, client);
  registerDeletePreviewTool(server, client);
  registerDeleteDocumentTool(server, client);
}
