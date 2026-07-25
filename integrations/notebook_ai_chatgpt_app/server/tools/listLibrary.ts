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

export const listLibraryInputShape = {
  query: z.string().trim().max(256).optional(),
  document_type: z.string().trim().max(64).optional(),
  status: z.enum(["active", "archived", "all"]).default("active"),
  limit: z.number().int().min(1).max(50).default(20),
};
export const listLibraryInputSchema = z.object(listLibraryInputShape);

const libraryItemSchema = z.object({
  document_id: z.number().int().positive(),
  title: z.string(),
  type: z.string(),
  imported_at: z.string(),
  chunk_count: z.number().int().nonnegative(),
  has_pdf: z.boolean(),
  duplicate_status: z.string(),
  status: z.enum(["active", "archived"]),
});

export const listLibraryOutputShape = {
  status: z.literal("ok"),
  count: z.number().int().nonnegative(),
  items: z.array(libraryItemSchema),
  truncated: z.boolean(),
};

export async function runListLibraryTool(client: NotebookClient, rawInput: unknown) {
  const startedAt = performance.now();
  try {
    const input = listLibraryInputSchema.parse(rawInput);
    const response = await client.listLibrary(input);
    const structuredContent = {
      status: "ok" as const,
      count: response.items.length,
      items: response.items,
      truncated: response.truncated,
    };
    logToolInvocation({
      tool: "list_library",
      duration_ms: elapsedMilliseconds(startedAt),
      result_count: response.items.length,
    });
    return { content: jsonContent(structuredContent), structuredContent };
  } catch (error) {
    logToolInvocation({
      tool: "list_library",
      duration_ms: elapsedMilliseconds(startedAt),
      error_code: errorCode(error),
    });
    return errorToolResult(error);
  }
}

export function registerListLibraryTool(server: McpServer, client: NotebookClient): void {
  server.registerTool(
    "list_library",
    {
      title: "List the private Search library",
      description:
        "List or title-filter the user's local Search library. Returns compact metadata only and never exposes local paths.",
      inputSchema: listLibraryInputShape,
      outputSchema: listLibraryOutputShape,
      annotations: READ_ONLY_ANNOTATIONS,
      _meta: toolMetadata("Reading the Search library…", "Library ready"),
    },
    async (input) => runListLibraryTool(client, input),
  );
}
