import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

import { NOTEBOOK_SOURCE_TYPES, type NotebookResult } from "../contracts.js";
import { logToolInvocation } from "../logging.js";
import type { NotebookClient } from "../notebookClient.js";
import {
  READ_ONLY_ANNOTATIONS,
  elapsedMilliseconds,
  errorCode,
  errorToolResult,
  jsonContent,
  notebookResultOutputSchema,
  toolMetadata,
  truncate,
} from "./shared.js";

export const searchInputShape = {
  query: z.string().trim().min(1).max(2_000).describe("Research, literature, paper, method, or reading-note question."),
  limit: z.number().int().min(1).max(20).default(12),
  source_types: z.array(z.enum(NOTEBOOK_SOURCE_TYPES)).min(1).max(4).default([...NOTEBOOK_SOURCE_TYPES]),
  document_ids: z.array(z.number().int().positive()).max(100).default([]),
  include_context: z.boolean().default(true),
};

export const searchInputSchema = z.object(searchInputShape);

export const searchOutputShape = {
  status: z.literal("ok"),
  query: z.string(),
  mode: z.string(),
  embedding_model: z.string(),
  reranker_model: z.string(),
  result_count: z.number().int().nonnegative(),
  results: z.array(notebookResultOutputSchema),
  warnings: z.array(
    z.union([z.string(), z.record(z.string(), z.unknown())]),
  ),
};

// Research and literature questions search the private corpus before answering; rewriting and casual chat do not require search.

function compactResult(result: NotebookResult, includeContext: boolean): Record<string, unknown> {
  return {
    fragment_id: result.fragment_id,
    source_type: result.source_type,
    selection_rank: result.selection_rank,
    document_id: result.document_id,
    document_title: result.document_title,
    document_type: result.document_type,
    pdf_page: result.pdf_page,
    page_label: result.page_label,
    heading: result.heading,
    section: result.section,
    coherent_text: truncate(result.coherent_text, 2_400),
    user_note: truncate(result.user_note, 2_400),
    selected_source_text: truncate(result.selected_source_text, 2_400),
    context_before: includeContext ? truncate(result.context_before, 600) : null,
    context_after: includeContext ? truncate(result.context_after, 600) : null,
    tags: result.tags,
    provenance: result.provenance,
    open_target: result.open_target,
  };
}

export async function runSearchTool(client: NotebookClient, rawInput: unknown) {
  const startedAt = performance.now();
  try {
    const input = searchInputSchema.parse(rawInput);
    const response = await client.search(input);
    const results = response.results.slice(0, 20);
    const structuredContent = {
      status: "ok" as const,
      query: response.query,
      mode: response.mode,
      embedding_model: response.embedding_model,
      reranker_model: response.reranker_model,
      result_count: results.length,
      results: results.map((result) => compactResult(result, input.include_context)),
      warnings: response.warnings,
    };
    logToolInvocation({ tool: "search", duration_ms: elapsedMilliseconds(startedAt), result_count: results.length });
    return {
      content: jsonContent(structuredContent),
      structuredContent,
      _meta: {
        "notebookAi/results": structuredContent.results,
        "notebookAi/backend": response.backend,
        "notebookAi/latency": response.latency,
        "notebookAi/sourceTypes": input.source_types,
      },
    };
  } catch (error) {
    logToolInvocation({ tool: "search", duration_ms: elapsedMilliseconds(startedAt), error_code: errorCode(error) });
    return errorToolResult(error, { tool: "search" });
  }
}

export function registerSearchTool(server: McpServer, client: NotebookClient): void {
  server.registerTool(
    "search",
    {
      title: "Search private research evidence",
      description:
        "For knowledge questions, research questions, literature claims, method explanations, comparisons, or questions about ideas the user may have encountered while reading, search the private corpus before answering. Do not rely on model memory when the user's corpus could contain relevant evidence. Pure rewriting, translation, formatting, and casual conversation do not require a corpus search. Distinguish PDF source text from user notes, cite document title and page, and call fetch for full context.",
      inputSchema: searchInputShape,
      outputSchema: searchOutputShape,
      annotations: READ_ONLY_ANNOTATIONS,
      _meta: toolMetadata("Searching private research evidence…", "Search complete", { rendersWidget: true }),
    },
    async (input) => runSearchTool(client, input),
  );
}
