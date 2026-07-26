import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

import { logToolInvocation } from "../logging.js";
import {
  discardStagedPath,
  rememberStagedImport,
  stageChatPdf,
} from "../fileTransfer.js";
import type { NotebookClient } from "../notebookClient.js";
import {
  READ_ONLY_ANNOTATIONS,
  elapsedMilliseconds,
  errorCode,
  errorToolResult,
  jsonContent,
  toolMetadata,
} from "./shared.js";

export const importPreviewInputShape = {
  source_type: z.enum(["local_pdf", "zotero_selected_book"]).default("local_pdf"),
  inbox_filename: z.string().trim().min(1).max(255).optional(),
  zotero_item_key: z.string().trim().min(1).max(64).optional(),
  zotero_attachment_key: z.string().trim().min(1).max(64).optional(),
  file: z.object({
    download_url: z.string().url(),
    file_id: z.string().min(1),
    mime_type: z.string().optional(),
    file_name: z.string().optional(),
  }).strict().optional(),
};
export const importPreviewInputSchema = z.object(importPreviewInputShape).superRefine(
  (value, context) => {
    if (value.source_type === "local_pdf") {
      if (value.zotero_item_key || value.zotero_attachment_key) {
        context.addIssue({ code: "custom", message: "local_pdf does not accept Zotero keys." });
      }
      if (value.file && value.inbox_filename) {
        context.addIssue({ code: "custom", message: "Use either a ChatGPT file or an inbox filename, not both." });
      }
      return;
    }
    if (!value.zotero_item_key) {
      context.addIssue({ code: "custom", message: "zotero_item_key is required." });
    }
    if (value.inbox_filename) {
      context.addIssue({ code: "custom", message: "zotero_selected_book does not accept inbox_filename." });
    }
    if (value.file) {
      context.addIssue({ code: "custom", message: "ChatGPT file input is only supported for local_pdf." });
    }
  },
);
export const importPreviewOutputShape = {
  status: z.literal("ok"),
  source_type: z.enum(["local_pdf", "zotero_selected_book"]),
  filename: z.string().nullable(),
  title: z.string(),
  pdf_sha256: z.string().nullable(),
  duplicate_status: z.string(),
  existing_document_id: z.number().int().positive().nullable(),
  estimated_pages: z.number().int().nonnegative().nullable(),
  estimated_chunks: z.number().int().nonnegative().nullable(),
  document_type: z.string(),
  warnings: z.array(z.string()),
  confirmation_token: z.string().nullable(),
  confirmation_expires_in_seconds: z.number().int().positive().nullable(),
  attachment_choices: z.array(z.object({
    zotero_attachment_key: z.string(),
    file_name: z.string().nullable(),
    path_exists: z.boolean(),
    path_status: z.string().nullable(),
    content_type: z.string().nullable(),
    date_modified: z.string().nullable(),
    version: z.union([z.number(), z.string()]).nullable(),
  })),
  annotation_count: z.number().int().nonnegative().nullable(),
  child_note_count: z.number().int().nonnegative().nullable(),
  note_count: z.number().int().nonnegative().optional(),
  note_files: z.array(z.string()).optional(),
};

export async function runImportPreviewTool(
  client: NotebookClient,
  rawInput: unknown,
  options: { env?: NodeJS.ProcessEnv; fetchImpl?: typeof fetch } = {},
) {
  const startedAt = performance.now();
  let stagedPath: string | null = null;
  try {
    const input = importPreviewInputSchema.parse(rawInput);
    let inboxFilename = input.inbox_filename;
    if (input.file) {
      const staged = await stageChatPdf(input.file, options);
      stagedPath = staged.path;
      inboxFilename = staged.filename;
    }
    const backendResponse = await client.importPreview({
      source_type: input.source_type,
      inbox_filename: inboxFilename,
      zotero_item_key: input.zotero_item_key,
      zotero_attachment_key: input.zotero_attachment_key,
    });
    const response = {
      ...backendResponse,
      source_type: backendResponse.source_type ?? input.source_type,
      filename: backendResponse.filename ?? null,
      pdf_sha256: backendResponse.pdf_sha256 ?? null,
      attachment_choices: backendResponse.attachment_choices ?? [],
      annotation_count: backendResponse.annotation_count ?? null,
      child_note_count: backendResponse.child_note_count ?? null,
    };
    if (stagedPath && response.confirmation_token) {
      rememberStagedImport(response.confirmation_token, stagedPath);
      stagedPath = null;
    }
    logToolInvocation({ tool: "import_preview", duration_ms: elapsedMilliseconds(startedAt), result_count: 1 });
    return { content: jsonContent(response), structuredContent: response };
  } catch (error) {
    logToolInvocation({
      tool: "import_preview",
      duration_ms: elapsedMilliseconds(startedAt),
      error_code: errorCode(error),
    });
    return errorToolResult(error);
  } finally {
    if (stagedPath) {
      await discardStagedPath(stagedPath);
    }
  }
}

export function registerImportPreviewTool(server: McpServer, client: NotebookClient): void {
  server.registerTool(
    "import_preview",
    {
      title: "Preview a local PDF or selected Zotero book",
      description:
        "Inspect a local PDF or selected Zotero book without adding it to the library. Call this before import_document and show title, duplicate status, type, and warnings to the user.",
      inputSchema: importPreviewInputShape,
      outputSchema: importPreviewOutputShape,
      annotations: READ_ONLY_ANNOTATIONS,
      _meta: {
        ...toolMetadata("Inspecting the PDF…", "Import preview ready"),
        "openai/fileParams": ["file"],
      },
    },
    async (input) => runImportPreviewTool(client, input),
  );
}
