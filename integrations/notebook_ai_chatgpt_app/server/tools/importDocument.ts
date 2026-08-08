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
  tool: z.string().optional(),
  document_id: z.number().int().positive().nullable().optional(),
  title: z.string().optional(),
  document_type: z.string().optional(),
  chunk_count: z.number().int().nonnegative().optional(),
  duplicate_status: z.string().optional(),
  error_code: z.string().nullable().optional(),
  message: z.string().optional(),
  retryable: z.boolean().nullable().optional(),
  already_completed: z.boolean().optional(),
  replayed_receipt: z.boolean().nullable().optional(),
  operation_in_progress: z.boolean().nullable().optional(),
  token_consumed: z.boolean().nullable().optional(),
  writes_performed: z.boolean().nullable(),
  safe_to_retry: z.boolean().nullable().optional(),
  publish_substage: z.string().nullable().optional(),
  cause_type: z.string().nullable().optional(),
  cause_message: z.string().nullable().optional(),
  cause_errno: z.number().int().nullable().optional(),
  cause_winerror: z.number().int().nullable().optional(),
  cause_filename: z.string().nullable().optional(),
  cause_filename2: z.string().nullable().optional(),
  rollback_attempted: z.boolean().nullable().optional(),
  rollback_completed: z.boolean().nullable().optional(),
  error_stage: z.string().nullable().optional(),
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
    return errorToolResult(error, {
      tool: "import_document",
      writeOperation: true,
      includeStructuredContent: true,
    });
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
