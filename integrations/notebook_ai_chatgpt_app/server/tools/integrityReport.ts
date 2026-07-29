import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

import { logToolInvocation } from "../logging.js";
import type { NotebookClient } from "../notebookClient.js";
import {
  READ_ONLY_ANNOTATIONS,
  elapsedMilliseconds,
  errorCode,
  errorToolResult,
  jsonContent,
  toolMetadata,
} from "./shared.js";

export const integrityReportInputShape = {
  document_id: z.number().int().positive().describe("Exact Search document_id to inspect."),
};
export const integrityReportInputSchema = z.object(integrityReportInputShape);

const statusRecord = z.record(z.string(), z.unknown());
export const integrityReportOutputShape = {
  status: z.literal("ok"),
  read_only: z.literal(true),
  document_id: z.number().int().positive(),
  document: statusRecord,
  source: statusRecord,
  database: statusRecord,
  fts: statusRecord,
  vectors: statusRecord,
  history: statusRecord,
  writes_performed: statusRecord,
};

export async function runIntegrityReportTool(client: NotebookClient, rawInput: unknown) {
  const startedAt = performance.now();
  try {
    const input = integrityReportInputSchema.parse(rawInput);
    const response = await client.integrityReport(input);
    logToolInvocation({
      tool: "integrity_report",
      duration_ms: elapsedMilliseconds(startedAt),
      result_count: 1,
    });
    return { content: jsonContent(response), structuredContent: response };
  } catch (error) {
    logToolInvocation({
      tool: "integrity_report",
      duration_ms: elapsedMilliseconds(startedAt),
      error_code: errorCode(error),
    });
    return errorToolResult(error);
  }
}

export function registerIntegrityReportTool(server: McpServer, client: NotebookClient): void {
  server.registerTool(
    "integrity_report",
    {
      title: "Inspect document integrity",
      description:
        "Read a path-free integrity report for one exact Search document_id across database, FTS, and vector state. Never writes or replays historical events.",
      inputSchema: integrityReportInputShape,
      outputSchema: integrityReportOutputShape,
      annotations: READ_ONLY_ANNOTATIONS,
      _meta: toolMetadata("Checking document integrity…", "Integrity report ready"),
    },
    async (input) => runIntegrityReportTool(client, input),
  );
}
