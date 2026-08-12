import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

import { releaseStagedImportForOperation } from "../fileTransfer.js";
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

export const importStatusInputShape = {
  operation_id: z.string().regex(/^[0-9a-f]{32}$/),
};
export const importStatusInputSchema = z.object(importStatusInputShape).strict();

export const importStatusOutputShape = {
  status: z.enum(["accepted", "running", "committed", "failed", "orphaned"]),
  operation_id: z.string().regex(/^[0-9a-f]{32}$/),
  document_id: z.number().int().positive().nullable(),
  title: z.string().nullable(),
  document_type: z.string().nullable(),
  chunk_count: z.number().int().nonnegative().nullable(),
  terminal: z.boolean(),
  operation_in_progress: z.boolean(),
  writes_performed: z.boolean().nullable(),
  token_consumed: z.boolean().nullable(),
  safe_to_retry: z.boolean(),
  replayed_receipt: z.boolean(),
  error_code: z.string().nullable(),
  error_stage: z.string().nullable(),
  rollback_attempted: z.boolean().nullable(),
  rollback_completed: z.boolean().nullable(),
};

export async function runImportStatusTool(
  client: NotebookClient,
  rawInput: unknown,
) {
  const startedAt = performance.now();
  try {
    const input = importStatusInputSchema.parse(rawInput);
    const response = await client.importStatus(input);
    if (response.terminal) {
      await releaseStagedImportForOperation(response.operation_id);
    }
    logToolInvocation({
      tool: "import_status",
      duration_ms: elapsedMilliseconds(startedAt),
      result_count: 1,
    });
    return { content: jsonContent(response), structuredContent: response };
  } catch (error) {
    logToolInvocation({
      tool: "import_status",
      duration_ms: elapsedMilliseconds(startedAt),
      error_code: errorCode(error),
    });
    return errorToolResult(error, { tool: "import_status" });
  }
}

export function registerImportStatusTool(
  server: McpServer,
  client: NotebookClient,
): void {
  server.registerTool(
    "import_status",
    {
      title: "Check durable import status",
      description:
        "Read-only. Check one durable import operation after a long-running call, timeout, or connection loss. This never retries an import or consumes a confirmation token.",
      inputSchema: importStatusInputShape,
      outputSchema: importStatusOutputShape,
      annotations: READ_ONLY_ANNOTATIONS,
      _meta: toolMetadata("Checking import status…", "Import status ready"),
    },
    async (input) => runImportStatusTool(client, input),
  );
}
