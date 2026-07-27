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
  scope: z.enum(["imported", "catalog", "zotero"]).default("imported"),
  query: z.string().trim().max(256).optional(),
  document_type: z.string().trim().max(64).optional(),
  status: z.enum(["active", "archived", "all"]).default("active"),
  limit: z.number().int().min(1).max(50).default(20),
};
export const listLibraryInputSchema = z.object(listLibraryInputShape);

const importedLibraryItemSchema = z.object({
  document_id: z.number().int().positive().nullable(),
  title: z.string(),
  type: z.string(),
  imported_at: z.string(),
  chunk_count: z.number().int().nonnegative(),
  has_pdf: z.boolean(),
  duplicate_status: z.string(),
  status: z.string(),
  kind: z.string().optional(),
  import_ref: z.string().optional(),
  relative_path: z.string().optional(),
  note_count: z.number().int().nonnegative().optional(),
  note_files: z.array(z.string()).optional(),
});
const catalogLibraryItemSchema = z.object({
  kind: z.literal("catalog"), document_id: z.null(), title: z.string(), type: z.literal("pdf"), has_pdf: z.literal(true),
  import_ref: z.string(), file_name: z.string(), relative_path: z.string(), note_count: z.number().int().nonnegative(), note_files: z.array(z.string()),
  status: z.literal("available"), duplicate_status: z.string(),
});
const zoteroLibraryItemSchema = z.object({
  kind: z.literal("zotero"), document_id: z.null(), title: z.string(), item_type: z.string(), zotero_item_key: z.string(),
  has_pdf: z.boolean(), attachment_count: z.number().int().nonnegative(), attachment_choices: z.array(z.object({ zotero_attachment_key: z.string(), file_name: z.string().nullable(), path_exists: z.boolean(), content_type: z.string().nullable() })), annotation_count: z.number().int().nonnegative(),
  child_note_count: z.number().int().nonnegative(), duplicate_status: z.string(), status: z.literal("available"),
});

export const listLibraryOutputShape = {
  status: z.literal("ok"),
  scope: z.enum(["imported", "catalog", "zotero"]),
  count: z.number().int().nonnegative(),
  items: z.array(z.union([importedLibraryItemSchema, catalogLibraryItemSchema, zoteroLibraryItemSchema])),
  truncated: z.boolean(),
};

export async function runListLibraryTool(client: NotebookClient, rawInput: unknown) {
  const startedAt = performance.now();
  try {
    const input = listLibraryInputSchema.parse(rawInput);
    const response = await client.listLibrary(input);
    const structuredContent = {
      status: "ok" as const,
      scope: response.scope ?? input.scope ?? "imported",
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
        "List imported Search documents, or scan the controlled import catalog with scope=catalog. Returns compact metadata only and never exposes absolute paths.",
      inputSchema: listLibraryInputShape,
      outputSchema: listLibraryOutputShape,
      annotations: READ_ONLY_ANNOTATIONS,
      _meta: toolMetadata("Reading the Search library…", "Library ready"),
    },
    async (input) => runListLibraryTool(client, input),
  );
}
