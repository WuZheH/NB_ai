import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

import { exportContent } from "../contracts.js";
import { logToolInvocation } from "../logging.js";
import type { NotebookClient } from "../notebookClient.js";
import {
  READ_ONLY_ANNOTATIONS,
  elapsedMilliseconds,
  errorCode,
  errorToolResult,
  jsonContent,
  toolMetadata,
  truncate,
} from "./shared.js";

export const exportEvidenceInputShape = {
  fragment_ids: z.array(z.string().trim().min(1).max(500)).min(1).max(50),
  format: z.enum(["markdown", "jsonl", "json"]).default("markdown"),
  query: z.string().max(2_000).optional(),
};

export const exportEvidenceInputSchema = z.object(exportEvidenceInputShape);

export const exportEvidenceOutputShape = {
  status: z.string(),
  format: z.enum(["markdown", "jsonl", "json"]),
  item_count: z.number().int().nonnegative(),
  content_length: z.number().int().nonnegative(),
  content_preview: z.string().nullable(),
};

export async function runExportEvidenceTool(client: NotebookClient, rawInput: unknown) {
  const startedAt = performance.now();
  try {
    const input = exportEvidenceInputSchema.parse(rawInput);
    const response = await client.exportEvidence(input);
    const content = exportContent(response);
    const structuredContent = {
      status: typeof response === "object" && response.status ? response.status : "ok",
      format: input.format,
      item_count: input.fragment_ids.length,
      content_length: content.length,
      content_preview: truncate(content, 4_000),
    };
    logToolInvocation({
      tool: "export_evidence",
      duration_ms: elapsedMilliseconds(startedAt),
      result_count: input.fragment_ids.length,
    });
    return {
      content: jsonContent(structuredContent),
      structuredContent,
      _meta: {
        "notebookAi/exportContent": content,
        "notebookAi/exportFormat": input.format,
        "notebookAi/fragmentIds": input.fragment_ids,
      },
    };
  } catch (error) {
    logToolInvocation({
      tool: "export_evidence",
      duration_ms: elapsedMilliseconds(startedAt),
      error_code: errorCode(error),
    });
    return errorToolResult(error);
  }
}

export function registerExportEvidenceTool(server: McpServer, client: NotebookClient): void {
  server.registerTool(
    "export_evidence",
    {
      title: "Export selected Search evidence",
      description:
        "Use this when the user asks to organize, copy, or export selected Search evidence. It returns Markdown, JSONL, or JSON without writing files or changing the collection.",
      inputSchema: exportEvidenceInputShape,
      outputSchema: exportEvidenceOutputShape,
      annotations: READ_ONLY_ANNOTATIONS,
      _meta: toolMetadata("Preparing evidence export…", "Evidence export ready"),
    },
    async (input) => runExportEvidenceTool(client, input),
  );
}
