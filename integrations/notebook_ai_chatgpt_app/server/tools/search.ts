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
  warnings: z.array(z.string()),
};

function compactResult(result: NotebookResult, includeContext: boolean): Record<string, unknown> {
  return {
    fragment_id: result.fragment_id,
    source_type: result.source_type,
    final_rank: result.final_rank,
    final_score: result.final_score,
    reranker_score: result.reranker_score,
    semantic_score: result.semantic_score,
    document_id: result.document_id,
    document_title: result.document_title,
    pdf_page: result.pdf_page,
    page_label: result.page_label,
    text: truncate(result.text),
    note_text: truncate(result.note_text),
    selected_text: truncate(result.selected_text),
    context_before: includeContext ? truncate(result.context_before, 600) : null,
    context_after: includeContext ? truncate(result.context_after, 600) : null,
    tags: result.tags,
    provenance: result.provenance,
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
        "notebookAi/results": results,
        "notebookAi/backend": response.backend,
        "notebookAi/latency": response.latency,
        "notebookAi/sourceTypes": input.source_types,
      },
    };
  } catch (error) {
    logToolInvocation({ tool: "search", duration_ms: elapsedMilliseconds(startedAt), error_code: errorCode(error) });
    return errorToolResult(error);
  }
}

export function registerSearchTool(server: McpServer, client: NotebookClient): void {
  server.registerTool(
    "search",
    {
      title: "Search private research evidence",
      description:
        "Use this when the user asks about their papers, literature, research methods, PDF evidence, Zotero reading notes, or ideas recorded while reading. Use Search before answering claims about the user's corpus. Distinguish PDF source text from the user's Zotero notes, cite document title, page, and fragment_id, call fetch for full context, and do not claim the collection has no relevant material until this search returns no results.",
      inputSchema: searchInputShape,
      outputSchema: searchOutputShape,
      annotations: READ_ONLY_ANNOTATIONS,
      _meta: toolMetadata("Searching private research evidence…", "Search complete", { rendersWidget: true }),
    },
    async (input) => runSearchTool(client, input),
  );
}
