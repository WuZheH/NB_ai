import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

import { logToolInvocation } from "../logging.js";
import type { NotebookClient } from "../notebookClient.js";
import {
  DESTRUCTIVE_ANNOTATIONS,
  elapsedMilliseconds,
  errorCode,
  errorToolResult,
  jsonContent,
  toolMetadata,
} from "./shared.js";

export const deleteDocumentInputShape = {
  confirmation_token: z.string().min(32).max(256),
  confirmed: z.literal(true).describe("Set true only after explicit user confirmation in the current conversation."),
};
export const deleteDocumentInputSchema = z.object(deleteDocumentInputShape);
export const deleteDocumentOutputShape = {
  status: z.string(),
  document_id: z.number().int().positive(),
  title: z.string(),
  recovery_created: z.boolean(),
  cleanup_complete: z.boolean(),
  error_code: z.string().nullable(),
};

export async function runDeleteDocumentTool(client: NotebookClient, rawInput: unknown) {
  const startedAt = performance.now();
  try {
    const input = deleteDocumentInputSchema.parse(rawInput);
    const response = await client.deleteDocument(input);
    logToolInvocation({ tool: "delete_document", duration_ms: elapsedMilliseconds(startedAt), result_count: 1 });
    return { content: jsonContent(response), structuredContent: response };
  } catch (error) {
    logToolInvocation({
      tool: "delete_document",
      duration_ms: elapsedMilliseconds(startedAt),
      error_code: errorCode(error),
    });
    return errorToolResult(error, { tool: "delete_document", writeOperation: true });
  }
}

export function registerDeleteDocumentTool(server: McpServer, client: NotebookClient): void {
  server.registerTool(
    "delete_document",
    {
      title: "Permanently delete confirmed Search document data",
      description:
        "Destructive write action. Call only after delete_preview and a separate explicit user confirmation in the current conversation. Never call from vague intent, never choose a document by fuzzy title, and never reuse an old confirmation.",
      inputSchema: deleteDocumentInputShape,
      outputSchema: deleteDocumentOutputShape,
      annotations: DESTRUCTIVE_ANNOTATIONS,
      _meta: toolMetadata("Deleting the confirmed Search data…", "Deletion finished"),
    },
    async (input) => runDeleteDocumentTool(client, input),
  );
}
