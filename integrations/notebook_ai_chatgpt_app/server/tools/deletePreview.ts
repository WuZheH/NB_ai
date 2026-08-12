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

export const deletePreviewInputShape = {
  document_id: z.number().int().positive(),
};
export const deletePreviewInputSchema = z.object(deletePreviewInputShape);
export const deletePreviewOutputShape = {
  status: z.literal("ok"),
  document_id: z.number().int().positive(),
  title: z.string(),
  safe_to_delete: z.boolean(),
  pdf_preserved: z.boolean(),
  notes_preserved: z.boolean(),
  blockers: z.array(z.string()),
  confirmation_token: z.string(),
  confirmation_expires_in_seconds: z.number().int().positive(),
};

export async function runDeletePreviewTool(client: NotebookClient, rawInput: unknown) {
  const startedAt = performance.now();
  try {
    const input = deletePreviewInputSchema.parse(rawInput);
    const response = await client.deletePreview(input.document_id);
    logToolInvocation({ tool: "delete_preview", duration_ms: elapsedMilliseconds(startedAt), result_count: 1 });
    return { content: jsonContent(response), structuredContent: response };
  } catch (error) {
    logToolInvocation({
      tool: "delete_preview",
      duration_ms: elapsedMilliseconds(startedAt),
      error_code: errorCode(error),
    });
    return errorToolResult(error, { tool: "delete_preview" });
  }
}

export function registerDeletePreviewTool(server: McpServer, client: NotebookClient): void {
  server.registerTool(
    "delete_preview",
    {
      title: "Preview safe deletion of a READ document",
      description:
        "Read-only safety preview. Call only after resolving exactly one document_id. Explain that READ data will be removed while the original PDF and protected notes are preserved, then ask the user to confirm.",
      inputSchema: deletePreviewInputShape,
      outputSchema: deletePreviewOutputShape,
      annotations: READ_ONLY_ANNOTATIONS,
      _meta: toolMetadata("Checking deletion impact…", "Deletion preview ready"),
    },
    async (input) => runDeletePreviewTool(client, input),
  );
}
