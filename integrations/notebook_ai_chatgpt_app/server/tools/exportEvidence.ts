import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { createHash } from "node:crypto";
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
} from "./shared.js";

export const exportEvidenceInputShape = {
  fragment_ids: z.array(z.string().trim().min(1).max(500)).min(1).max(50),
  format: z.enum(["markdown", "jsonl", "json"]).default("markdown"),
  query: z.string().max(2_000).optional(),
  content_offset: z.number().int().nonnegative().default(0),
  content_limit: z.number().int().min(1_000).max(32_000).default(32_000),
};

export const exportEvidenceInputSchema = z.object(exportEvidenceInputShape);

export const exportEvidenceOutputShape = {
  status: z.literal("ok"),
  format: z.enum(["markdown", "jsonl", "json"]),
  item_count: z.number().int().nonnegative(),
  content_length: z.number().int().nonnegative(),
  content: z.string().nullable(),
  content_preview: z.string().nullable(),
  content_truncated: z.boolean(),
  next_content_offset: z.number().int().nonnegative().nullable(),
  full_content_retrieval: z.record(z.string(), z.unknown()).nullable(),
  offset_unit: z.literal("utf16_code_units"),
  requires_concatenation: z.boolean(),
  content_sha256: z.string().regex(/^[0-9a-f]{64}$/),
  requested_offset: z.number().int().nonnegative(),
  offset_out_of_range: z.boolean(),
  warnings: z.array(z.record(z.string(), z.unknown())),
};

export async function runExportEvidenceTool(client: NotebookClient, rawInput: unknown) {
  const startedAt = performance.now();
  try {
    const input = exportEvidenceInputSchema.parse(rawInput);
    const response = await client.exportEvidence({
      fragment_ids: input.fragment_ids,
      format: input.format,
      query: input.query,
    });
    const content = exportContent(response);
    const offsetOutOfRange = input.content_offset > content.length;
    const start = offsetOutOfRange
      ? content.length
      : safeUtf16Start(content, input.content_offset);
    const end = safeUtf16End(
      content,
      Math.min(content.length, start + input.content_limit),
    );
    const contentTruncated = end < content.length;
    const selectedContent = content.slice(start, end);
    const structuredContent = {
      status: "ok" as const,
      format: input.format,
      item_count: input.fragment_ids.length,
      content_length: content.length,
      content: contentTruncated ? null : selectedContent,
      content_preview: contentTruncated ? selectedContent : null,
      content_truncated: contentTruncated,
      next_content_offset: contentTruncated ? end : null,
      full_content_retrieval: contentTruncated
        ? {
            tool: "export_evidence",
            arguments: {
              fragment_ids: input.fragment_ids,
              format: input.format,
              query: input.query,
              content_offset: end,
              content_limit: input.content_limit,
            },
          }
        : null,
      offset_unit: "utf16_code_units" as const,
      requires_concatenation: input.content_offset > 0 || contentTruncated,
      content_sha256: createHash("sha256").update(content, "utf8").digest("hex"),
      requested_offset: input.content_offset,
      offset_out_of_range: offsetOutOfRange,
      warnings: offsetOutOfRange
        ? [
            {
              code: "content_offset_out_of_range",
              requested_offset: input.content_offset,
              content_length: content.length,
            },
          ]
        : [],
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
    return errorToolResult(error, { tool: "export_evidence" });
  }
}

function safeUtf16Start(content: string, offset: number): number {
  if (
    offset > 0
    && offset < content.length
    && isLowSurrogate(content.charCodeAt(offset))
    && isHighSurrogate(content.charCodeAt(offset - 1))
  ) {
    return offset + 1;
  }
  return offset;
}

function safeUtf16End(content: string, offset: number): number {
  if (
    offset > 0
    && offset < content.length
    && isLowSurrogate(content.charCodeAt(offset))
    && isHighSurrogate(content.charCodeAt(offset - 1))
  ) {
    return offset - 1;
  }
  return offset;
}

function isHighSurrogate(value: number): boolean {
  return value >= 0xd800 && value <= 0xdbff;
}

function isLowSurrogate(value: number): boolean {
  return value >= 0xdc00 && value <= 0xdfff;
}

export function registerExportEvidenceTool(server: McpServer, client: NotebookClient): void {
  server.registerTool(
    "export_evidence",
    {
      title: "Export selected READ evidence",
      description:
        "Use this when the user asks to organize, copy, or export selected READ evidence. It returns Markdown, JSONL, or JSON without writing files or changing the library.",
      inputSchema: exportEvidenceInputShape,
      outputSchema: exportEvidenceOutputShape,
      annotations: READ_ONLY_ANNOTATIONS,
      _meta: toolMetadata("Preparing evidence export…", "Evidence export ready"),
    },
    async (input) => runExportEvidenceTool(client, input),
  );
}
