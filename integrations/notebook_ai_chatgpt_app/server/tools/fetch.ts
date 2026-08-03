import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

import { unwrapFragment } from "../contracts.js";
import { logToolInvocation } from "../logging.js";
import type { NotebookClient } from "../notebookClient.js";
import {
  READ_ONLY_ANNOTATIONS,
  elapsedMilliseconds,
  errorCode,
  errorToolResult,
  jsonContent,
  notebookFragmentOutputSchema,
  toolMetadata,
} from "./shared.js";

export const fetchInputShape = {
  fragment_id: z.string().trim().min(1).max(500).describe("Stable fragment_id returned by Search."),
};

export const fetchInputSchema = z.object(fetchInputShape);

export const fetchOutputShape = {
  status: z.literal("ok"),
  fragment: notebookFragmentOutputSchema,
};

export async function runFetchTool(client: NotebookClient, rawInput: unknown) {
  const startedAt = performance.now();
  try {
    const input = fetchInputSchema.parse(rawInput);
    const fragment = unwrapFragment(await client.fetchFragment(input.fragment_id));
    const structuredContent = { status: "ok" as const, fragment };
    logToolInvocation({ tool: "fetch", duration_ms: elapsedMilliseconds(startedAt), result_count: 1 });
    return {
      content: jsonContent(structuredContent),
      structuredContent,
      _meta: { "notebookAi/fragment": fragment },
    };
  } catch (error) {
    logToolInvocation({ tool: "fetch", duration_ms: elapsedMilliseconds(startedAt), error_code: errorCode(error) });
    return errorToolResult(error, { tool: "fetch" });
  }
}

export function registerFetchTool(server: McpServer, client: NotebookClient): void {
  server.registerTool(
    "fetch",
    {
      title: "Fetch a Search evidence fragment",
      description:
        "Fetch one search result by fragment_id when the full PDF passage, Zotero user note, selected source text, surrounding context, or provenance is needed. This is read-only.",
      inputSchema: fetchInputShape,
      outputSchema: fetchOutputShape,
      annotations: READ_ONLY_ANNOTATIONS,
      _meta: toolMetadata("Loading evidence context…", "Evidence context loaded"),
    },
    async (input) => runFetchTool(client, input),
  );
}
