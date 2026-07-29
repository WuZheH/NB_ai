import { NotebookBackendError } from "../notebookClient.js";
import { NOTEBOOK_SOURCE_TYPES } from "../contracts.js";
import { z } from "zod";

export const WIDGET_RESOURCE_URI = "ui://notebook-ai/research-search-v1.html";

export const READ_ONLY_ANNOTATIONS = Object.freeze({
  readOnlyHint: true,
  destructiveHint: false,
  idempotentHint: true,
  openWorldHint: false,
});

export const notebookFragmentOutputSchema = z
  .object({
    fragment_id: z.string(),
    source_type: z.enum(NOTEBOOK_SOURCE_TYPES),
    document_id: z.number().int().nullable(),
    document_title: z.string().nullable(),
    document_type: z.string().nullable(),
    pdf_page: z.number().int().nullable(),
    page_label: z.string().nullable(),
    heading: z.string().nullable(),
    section: z.string().nullable(),
    coherent_text: z.string().nullable(),
    user_note: z.string().nullable(),
    selected_source_text: z.string().nullable(),
    context_before: z.string().nullable(),
    context_after: z.string().nullable(),
    tags: z.array(z.string()),
    provenance: z.record(z.string(), z.unknown()),
    open_target: z.record(z.string(), z.unknown()).nullable(),
    selection_rank: z.number().int().positive().nullable(),
  })
  .strict();

export const notebookResultOutputSchema = notebookFragmentOutputSchema;

export const WRITE_ANNOTATIONS = Object.freeze({
  readOnlyHint: false,
  destructiveHint: false,
  idempotentHint: false,
  openWorldHint: false,
});

export const DESTRUCTIVE_ANNOTATIONS = Object.freeze({
  readOnlyHint: false,
  destructiveHint: true,
  idempotentHint: false,
  openWorldHint: false,
});

export function toolMetadata(
  invoking: string,
  invoked: string,
  options: { rendersWidget?: boolean } = {},
): Record<string, unknown> {
  const rendersWidget = options.rendersWidget === true;
  return {
    ui: {
      visibility: ["model", "app"],
      ...(rendersWidget ? { resourceUri: WIDGET_RESOURCE_URI } : {}),
    },
    ...(rendersWidget ? { "openai/outputTemplate": WIDGET_RESOURCE_URI } : {}),
    "openai/toolInvocation/invoking": invoking,
    "openai/toolInvocation/invoked": invoked,
    "openai/widgetAccessible": true,
    "notebookAi/errorContract": "isError-content-v1",
  };
}

export function jsonContent(value: unknown): Array<{ type: "text"; text: string }> {
  return [{ type: "text", text: JSON.stringify(value) }];
}

const PUBLIC_ERROR_MESSAGES: Readonly<Record<string, string>> = Object.freeze({
  MCP_INVALID_ARGUMENT: "The tool arguments are invalid.",
  notebook_fragment_not_found: "The requested fragment was not found.",
  integrity_report_document_not_found: "The requested document was not found.",
  evidence_fragment_not_found: "A selected evidence fragment was not found.",
  attachment_not_owned_by_item:
    "The selected PDF attachment does not belong to the selected Zotero item.",
  import_inbox_unavailable: "The local PDF import inbox is unavailable.",
  zotero_item_not_found: "The selected Zotero item was not found.",
  zotero_item_type_unsupported:
    "The selected Zotero item type is not supported for PDF import.",
  no_pdf_attachment: "The selected Zotero item has no PDF attachment.",
  pdf_file_missing: "The selected Zotero PDF is not available as a local file.",
  preview_token_expired: "The import preview expired. Run import_preview again.",
  preview_token_unknown: "The import preview is no longer available. Run import_preview again.",
});

export function errorToolResult(
  error: unknown,
  options: { tool?: string; writeOperation?: boolean } = {},
): {
  isError: true;
  content: Array<{ type: "text"; text: string }>;
} {
  const code = errorCode(error);
  const message =
    PUBLIC_ERROR_MESSAGES[code]
    ?? (code === "BACKEND_TIMEOUT"
      ? "Search backend request timed out."
      : code === "zotero_direction_b_body_import_failed"
        ? "Selected-book body extraction failed and the import was rolled back."
      : code.endsWith("_index_sync_failed") || code.endsWith("_index_publish_failed")
        ? "Selected-book index publication failed and the import was rolled back."
      : [
          "config_missing",
          "config_invalid_json",
          "schema_unsupported",
          "required_field_missing",
          "model_path_not_absolute",
          "model_path_not_found",
          "model_structure_invalid",
        ].includes(code)
        ? "Search high-quality search configuration is unavailable."
      : [
          "model_load_failed",
          "embedding_model_load_failed",
          "embedding_model_self_check_failed",
          "embedding_model_inference_failed",
          "reranker_model_load_failed",
          "reranker_model_self_check_failed",
          "reranker_model_inference_failed",
        ].includes(code)
        ? "Search high-quality retrieval model is unavailable."
      : "Search request failed.");
  const writeOperation = options.writeOperation === true;
  const structuredContent = {
    status: "error" as const,
    tool: options.tool ?? "unknown",
    error_code: code,
    message,
    retryable: code === "BACKEND_TIMEOUT" || code === "BACKEND_UNAVAILABLE",
    writes_performed: writeOperation ? null : false,
    ...(writeOperation
      ? {
          token_consumed: null,
          safe_to_retry: false,
        }
      : {}),
  };
  return { isError: true, content: jsonContent(structuredContent) };
}

export function errorCode(error: unknown): string {
  if (error instanceof z.ZodError) {
    return "MCP_INVALID_ARGUMENT";
  }
  if (!(error instanceof NotebookBackendError)) {
    return error instanceof TypeError || error instanceof SyntaxError
      ? "BACKEND_RESPONSE_INVALID"
      : "BACKEND_UNAVAILABLE";
  }
  return /^[A-Za-z0-9_.-]{1,96}$/.test(error.code) ? error.code : "MCP_ADAPTER_ERROR";
}

export function elapsedMilliseconds(startedAt: number): number {
  return Math.max(0, Math.round(performance.now() - startedAt));
}

export function truncate(value: unknown, maximum = 1200): string | null {
  if (typeof value !== "string" || !value) {
    return null;
  }
  return value.length <= maximum ? value : `${value.slice(0, maximum - 1)}…`;
}
