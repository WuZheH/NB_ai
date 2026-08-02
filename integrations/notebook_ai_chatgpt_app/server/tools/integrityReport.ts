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

export const integrityReportInputShape = {
  document_id: z.number().int().positive().describe("Exact Search document_id to inspect."),
};
export const integrityReportInputSchema = z.object(integrityReportInputShape);

const statusRecord = z.record(z.string(), z.unknown());
const nonNegativeInteger = z.number().int().nonnegative();
const orphanCount = z.union([nonNegativeInteger, z.literal("not_available")]);
export const integrityReportOutputShape = {
  status: z.literal("ok"),
  read_only: z.literal(true),
  verdict: z.enum(["pass", "warn", "fail"]),
  warnings: z.array(z.string()),
  document_id: z.number().int().positive(),
  pdf_sha256: z.string(),
  document: statusRecord,
  source: statusRecord,
  database: z.object({
    document_count: nonNegativeInteger,
    chunk_count: nonNegativeInteger,
    chapter_count: nonNegativeInteger,
    source_binding_count: nonNegativeInteger,
    personal_note_count: nonNegativeInteger,
    evidence_link_count: nonNegativeInteger,
    integrity_check: z.string(),
    foreign_key_issue_count: nonNegativeInteger,
  }),
  fts: z.object({
    status: z.string(),
    ready: z.boolean(),
    expected_pdf_chunk_count: nonNegativeInteger,
    indexed_pdf_chunk_count: nonNegativeInteger,
    missing_pdf_chunk_count: nonNegativeInteger,
    orphan_pdf_chunk_count: nonNegativeInteger,
    eligible_personal_note_count: nonNegativeInteger,
    indexed_personal_note_count: nonNegativeInteger,
    missing_personal_note_count: nonNegativeInteger,
    orphan_personal_note_count: nonNegativeInteger,
    excluded_personal_note_count: nonNegativeInteger,
    exclusion_reasons: z.record(z.string(), nonNegativeInteger),
    fragment_count: nonNegativeInteger,
    source_type_counts: z.record(z.string(), nonNegativeInteger),
    reasons: z.array(z.string()),
  }),
  vectors: z.object({
    status: z.string(),
    passage_expected_count: nonNegativeInteger,
    passage_indexed_count: nonNegativeInteger,
    passage_missing_count: nonNegativeInteger,
    passage_orphan_count: orphanCount,
    note_expected_count: nonNegativeInteger,
    note_indexed_count: nonNegativeInteger,
    note_missing_count: nonNegativeInteger,
    note_orphan_count: orphanCount,
  }),
  history: z.object({
    confirmation_token_fingerprint: z.string(),
    previewed_at: z.string(),
    confirmed_at: z.string(),
    transaction_fingerprint: z.string(),
    source_revision_fingerprint: z.string(),
    lifecycle_events: z.string(),
    terminal_status: z.string(),
    terminal_stage: z.string(),
    journal_operation_id: z.string(),
    journal_revision: z.union([nonNegativeInteger, z.literal("not_recorded")]),
    receipt_recorded: z.union([z.boolean(), z.literal("not_recorded")]),
    journal_updated_at: z.string(),
    journal_terminal_events: z.string(),
  }),
  writes_performed: z.object({
    production_db: z.literal(false),
    fts: z.literal(false),
    vector_store: z.literal(false),
    zotero: z.literal(false),
  }),
};

export async function runIntegrityReportTool(client: NotebookClient, rawInput: unknown) {
  const startedAt = performance.now();
  try {
    const input = integrityReportInputSchema.parse(rawInput);
    const response = await client.integrityReport(input);
    logToolInvocation({
      tool: "integrity_report",
      duration_ms: elapsedMilliseconds(startedAt),
      result_count: 1,
    });
    return { content: jsonContent(response), structuredContent: response };
  } catch (error) {
    logToolInvocation({
      tool: "integrity_report",
      duration_ms: elapsedMilliseconds(startedAt),
      error_code: errorCode(error),
    });
    return errorToolResult(error, { tool: "integrity_report" });
  }
}

export function registerIntegrityReportTool(server: McpServer, client: NotebookClient): void {
  server.registerTool(
    "integrity_report",
    {
      title: "Inspect document integrity",
      description:
        "Read a path-free integrity report for one exact Search document_id across database, FTS, and vector state. Never writes or replays historical events.",
      inputSchema: integrityReportInputShape,
      outputSchema: integrityReportOutputShape,
      annotations: READ_ONLY_ANNOTATIONS,
      _meta: toolMetadata("Checking document integrity…", "Integrity report ready"),
    },
    async (input) => runIntegrityReportTool(client, input),
  );
}
