import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

import { logToolInvocation } from "../logging.js";
import { releaseStagedImport } from "../fileTransfer.js";
import type { NotebookClient } from "../notebookClient.js";
import {
  WRITE_ANNOTATIONS,
  elapsedMilliseconds,
  errorCode,
  errorToolResult,
  jsonContent,
  toolMetadata,
} from "./shared.js";

export const importDocumentInputShape = {
  confirmation_token: z.string().min(32).max(256),
  confirmed: z.literal(true).describe("Set true only after the user explicitly confirms this import in the current conversation."),
};
export const importDocumentInputSchema = z.object(importDocumentInputShape);
export const importDocumentOutputShape = {
  status: z.string(),
  document_id: z.number().int().positive().nullable(),
  title: z.string(),
  document_type: z.string(),
  chunk_count: z.number().int().nonnegative(),
  duplicate_status: z.string(),
  error_code: z.string().nullable(),
  already_completed: z.boolean(),
  replayed_receipt: z.boolean(),
};

export async function runImportDocumentTool(client: NotebookClient, rawInput: unknown) {
  const startedAt = performance.now();
  let confirmationToken: string | null = null;
  try {
    const input = importDocumentInputSchema.parse(rawInput);
    confirmationToken = input.confirmation_token;
    const response = await client.importDocument(input);
    logToolInvocation({ tool: "import_document", duration_ms: elapsedMilliseconds(startedAt), result_count: 1 });
    return { content: jsonContent(response), structuredContent: response };
  } catch (error) {
    logToolInvocation({
      tool: "import_document",
      duration_ms: elapsedMilliseconds(startedAt),
      error_code: errorCode(error),
    });
    return errorToolResult(error, { tool: "import_document", writeOperation: true });
  } finally {
    if (confirmationToken) {
      await releaseStagedImport(confirmationToken);
    }
  }
}

export function registerImportDocumentTool(server: McpServer, client: NotebookClient): void {
  server.registerTool(
    "import_document",
    {
      title: "Import a confirmed PDF into Search",
      description:
        "Write action. Use only after import_preview and an explicit user confirmation in the current conversation. Never infer confirmation from vague intent.",
      inputSchema: importDocumentInputShape,
      outputSchema: importDocumentOutputShape,
      annotations: WRITE_ANNOTATIONS,
      _meta: toolMetadata("Importing the confirmed PDF…", "Import finished"),
    },
    async (input) => runImportDocumentTool(client, input),
  );
}
