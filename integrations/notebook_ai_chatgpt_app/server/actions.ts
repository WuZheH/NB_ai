import { timingSafeEqual } from "node:crypto";
import type { IncomingMessage, ServerResponse } from "node:http";

import {
  NOTEBOOK_SOURCE_TYPES,
  exportContent,
  unwrapFragment,
  type NotebookResult,
  type NotebookSearchInput,
} from "./contracts.js";
import { NotebookBackendError, NotebookClient } from "./notebookClient.js";

const ACTIONS_PREFIX = "/actions/v1/";
const MAX_ACTION_BODY_BYTES = 64 * 1024;
const ACTION_NAMES = new Set([
  "search",
  "fetch",
  "export_evidence",
  "list_library",
  "import_preview",
  "import_document",
  "delete_preview",
  "delete_document",
]);

export function actionsOpenApiDocument(
  env: NodeJS.ProcessEnv = process.env,
): Record<string, unknown> {
  const paths = Object.fromEntries(
    [...ACTION_NAMES].map((name) => [
      `/actions/v1/${name}`,
      {
        post: {
          operationId: name,
          summary: actionSummary(name),
          description: actionDescription(name),
          security: [{ bearerAuth: [] }],
          requestBody: {
            required: true,
            content: {
              "application/json": {
                schema: actionInputSchema(name),
              },
            },
          },
          responses: {
            "200": {
              description: "Search tool result",
              content: { "application/json": { schema: { type: "object" } } },
            },
            "4XX": {
              description: "Stable client or confirmation error",
              content: { "application/json": { schema: errorSchema() } },
            },
            "5XX": {
              description: "Stable backend error",
              content: { "application/json": { schema: errorSchema() } },
            },
          },
        },
      },
    ]),
  );
  const publicBaseUrl = configuredActionsBaseUrl(env);
  return {
    openapi: "3.1.0",
    info: {
      title: "Search private research tools",
      version: "0.1.0",
      description:
        "Thin authenticated adapter over the local Search Core. Import and deletion require preview plus explicit confirmation.",
    },
    paths,
    ...(publicBaseUrl ? { servers: [{ url: publicBaseUrl }] } : {}),
    components: {
      securitySchemes: {
        bearerAuth: {
          type: "http",
          scheme: "bearer",
          bearerFormat: "opaque",
        },
      },
    },
  };
}

export async function handleActionsHttpRequest(
  request: IncomingMessage,
  response: ServerResponse,
  options: { env?: NodeJS.ProcessEnv; client?: NotebookClient } = {},
): Promise<boolean> {
  const url = new URL(request.url ?? "/", `http://${request.headers.host ?? "127.0.0.1"}`);
  if (request.method === "GET" && url.pathname === "/actions/openapi.json") {
    sendJson(response, 200, actionsOpenApiDocument(options.env ?? process.env));
    return true;
  }
  if (!url.pathname.startsWith(ACTIONS_PREFIX)) {
    return false;
  }
  if (request.method !== "POST") {
    response.setHeader("Allow", "POST");
    sendJson(response, 405, actionError("ACTIONS_METHOD_NOT_ALLOWED", "Method not allowed."));
    return true;
  }
  const action = url.pathname.slice(ACTIONS_PREFIX.length);
  if (!ACTION_NAMES.has(action)) {
    sendJson(response, 404, actionError("ACTIONS_TOOL_NOT_FOUND", "Search action was not found."));
    return true;
  }
  const environment = options.env ?? process.env;
  const authentication = authenticateActions(request.headers.authorization, environment);
  if (authentication !== null) {
    sendJson(response, authentication.status, actionError(authentication.errorCode, authentication.message));
    return true;
  }
  try {
    const body = await readJsonBody(request);
    const client = options.client ?? new NotebookClient({ env: environment, adapter: "actions" });
    const result = await dispatchAction(action, body, client);
    sendJson(response, 200, result);
  } catch (error) {
    const mapped = mapActionError(error);
    sendJson(response, mapped.status, actionError(mapped.errorCode, mapped.message));
  }
  return true;
}

function configuredActionsBaseUrl(env: NodeJS.ProcessEnv): string | null {
  const value = String(env.SEARCH_ACTIONS_PUBLIC_BASE_URL ?? "").trim();
  if (!value) return null;
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    return null;
  }
  if (parsed.protocol !== "https:" || parsed.username || parsed.password) return null;
  parsed.pathname = parsed.pathname.replace(/\/+$/, "");
  parsed.search = "";
  parsed.hash = "";
  return parsed.toString().replace(/\/$/, "");
}

export async function dispatchAction(
  action: string,
  input: Record<string, unknown>,
  client: NotebookClient,
): Promise<Record<string, unknown>> {
  if (action === "search") {
    const request: NotebookSearchInput = {
      query: requiredString(input.query, "query", 2_000),
      limit: boundedInteger(input.limit, 12, 1, 20),
      source_types: Array.isArray(input.source_types)
        ? input.source_types as NotebookSearchInput["source_types"]
        : [...NOTEBOOK_SOURCE_TYPES],
      document_ids: Array.isArray(input.document_ids) ? input.document_ids as number[] : [],
      include_context: input.include_context !== false,
    };
    const result = await client.search(request);
    return {
      status: "ok",
      query: result.query,
      result_count: result.results.length,
      results: result.results.map((item) => compactSearchResult(item, request.include_context)),
      warnings: result.warnings,
    };
  }
  if (action === "fetch") {
    const fragment = unwrapFragment(await client.fetchFragment(requiredString(input.fragment_id, "fragment_id", 500)));
    return { status: "ok", fragment };
  }
  if (action === "export_evidence") {
    const fragmentIds = requiredStringArray(input.fragment_ids, "fragment_ids", 50, 500);
    const format = ["markdown", "jsonl", "json"].includes(String(input.format ?? "markdown"))
      ? String(input.format ?? "markdown") as "markdown" | "jsonl" | "json"
      : "markdown";
    const content = exportContent(await client.exportEvidence({
      fragment_ids: fragmentIds,
      format,
      query: typeof input.query === "string" ? input.query.slice(0, 2_000) : undefined,
    }));
    return { status: "ok", format, item_count: fragmentIds.length, content };
  }
  if (action === "list_library") {
    return await client.listLibrary({
      scope: ["imported", "catalog"].includes(String(input.scope)) ? String(input.scope) as "imported" | "catalog" : "imported",
      query: optionalString(input.query, 256),
      document_type: optionalString(input.document_type, 64),
      status: ["active", "archived", "all"].includes(String(input.status))
        ? String(input.status) as "active" | "archived" | "all"
        : "active",
      limit: boundedInteger(input.limit, 20, 1, 50),
    });
  }
  if (action === "import_preview") {
    const sourceType = input.source_type === undefined
      ? "local_pdf"
      : requiredImportSourceType(input.source_type);
    const inboxFilename = optionalString(input.inbox_filename, 255);
    const zoteroItemKey = optionalString(input.zotero_item_key, 64);
    const zoteroAttachmentKey = optionalString(input.zotero_attachment_key, 64);
    if (sourceType === "local_pdf" && (zoteroItemKey || zoteroAttachmentKey)) {
      throw new ActionRequestError("ACTIONS_INVALID_ARGUMENT", "local_pdf does not accept Zotero keys.");
    }
    if (sourceType === "zotero_selected_book" && (!zoteroItemKey || inboxFilename)) {
      throw new ActionRequestError(
        "ACTIONS_INVALID_ARGUMENT",
        "zotero_selected_book requires zotero_item_key and does not accept inbox_filename.",
      );
    }
    return await client.importPreview({
      source_type: sourceType,
      inbox_filename: inboxFilename,
      zotero_item_key: zoteroItemKey,
      zotero_attachment_key: zoteroAttachmentKey,
    });
  }
  if (action === "import_document") {
    requireConfirmed(input.confirmed, "ACTIONS_IMPORT_CONFIRMATION_REQUIRED");
    return await client.importDocument({
      confirmation_token: requiredString(input.confirmation_token, "confirmation_token", 256, 32),
      confirmed: true,
    });
  }
  if (action === "delete_preview") {
    return await client.deletePreview(boundedInteger(input.document_id, 0, 1, Number.MAX_SAFE_INTEGER));
  }
  if (action === "delete_document") {
    requireConfirmed(input.confirmed, "ACTIONS_DELETE_CONFIRMATION_REQUIRED");
    return await client.deleteDocument({
      confirmation_token: requiredString(input.confirmation_token, "confirmation_token", 256, 32),
      confirmed: true,
    });
  }
  throw new ActionRequestError("ACTIONS_TOOL_NOT_FOUND", "Search action was not found.", 404);
}

export function authenticateActions(
  authorization: string | undefined,
  env: NodeJS.ProcessEnv = process.env,
): { status: number; errorCode: string; message: string } | null {
  const expected = String(env.SEARCH_ACTIONS_BEARER_TOKEN ?? "").trim();
  if (expected.length < 32) {
    return {
      status: 503,
      errorCode: "ACTIONS_AUTH_NOT_CONFIGURED",
      message: "Search Actions authentication is not configured.",
    };
  }
  const supplied = String(authorization ?? "").replace(/^Bearer\s+/i, "").trim();
  if (!supplied || !safeEqual(supplied, expected)) {
    return {
      status: 401,
      errorCode: "ACTIONS_AUTHENTICATION_FAILED",
      message: "Search Actions authentication failed.",
    };
  }
  return null;
}

class ActionRequestError extends Error {
  readonly errorCode: string;
  readonly status: number;

  constructor(errorCode: string, message: string, status = 422) {
    super(message);
    this.errorCode = errorCode;
    this.status = status;
  }
}

function mapActionError(error: unknown): { status: number; errorCode: string; message: string } {
  if (error instanceof ActionRequestError) {
    return { status: error.status, errorCode: error.errorCode, message: error.message };
  }
  if (error instanceof NotebookBackendError) {
    return {
      status: error.status >= 400 && error.status <= 599 ? error.status : 502,
      errorCode: /^[A-Za-z0-9_.-]{1,96}$/.test(error.code) ? error.code : "ACTIONS_BACKEND_ERROR",
      message: "Search backend request failed.",
    };
  }
  return { status: 500, errorCode: "ACTIONS_INTERNAL_ERROR", message: "Search action failed." };
}

async function readJsonBody(request: IncomingMessage): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = [];
  let total = 0;
  for await (const chunk of request) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    total += buffer.length;
    if (total > MAX_ACTION_BODY_BYTES) {
      throw new ActionRequestError("ACTIONS_REQUEST_TOO_LARGE", "Request exceeds 64 KB.", 413);
    }
    chunks.push(buffer);
  }
  try {
    const parsed = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      throw new TypeError();
    }
    return parsed as Record<string, unknown>;
  } catch {
    throw new ActionRequestError("ACTIONS_INVALID_JSON", "Request body must be a JSON object.", 400);
  }
}

function compactSearchResult(result: NotebookResult, includeContext: boolean): Record<string, unknown> {
  return {
    fragment_id: result.fragment_id,
    source_type: result.source_type,
    document_id: result.document_id,
    document_title: result.document_title,
    pdf_page: result.pdf_page,
    page_label: result.page_label,
    final_rank: result.final_rank,
    final_score: result.final_score,
    snippet: truncate(result.text ?? result.selected_text ?? result.note_text, 1_200),
    context_before: includeContext ? truncate(result.context_before, 600) : null,
    context_after: includeContext ? truncate(result.context_after, 600) : null,
  };
}

function requiredString(value: unknown, name: string, maximum: number, minimum = 1): string {
  const text = typeof value === "string" ? value.trim() : "";
  if (text.length < minimum || text.length > maximum) {
    throw new ActionRequestError("ACTIONS_INVALID_ARGUMENT", `${name} is invalid.`);
  }
  return text;
}

function optionalString(value: unknown, maximum: number): string | undefined {
  if (value === undefined || value === null || value === "") return undefined;
  return requiredString(value, "value", maximum);
}

function requiredImportSourceType(value: unknown): "local_pdf" | "zotero_selected_book" {
  if (value === "local_pdf" || value === "zotero_selected_book") return value;
  throw new ActionRequestError("ACTIONS_INVALID_ARGUMENT", "source_type is invalid.");
}

function requiredStringArray(value: unknown, name: string, maximumItems: number, maximumLength: number): string[] {
  if (!Array.isArray(value) || value.length < 1 || value.length > maximumItems) {
    throw new ActionRequestError("ACTIONS_INVALID_ARGUMENT", `${name} is invalid.`);
  }
  return value.map((item) => requiredString(item, name, maximumLength));
}

function boundedInteger(value: unknown, fallback: number, minimum: number, maximum: number): number {
  const number = value === undefined ? fallback : Number(value);
  if (!Number.isInteger(number) || number < minimum || number > maximum) {
    throw new ActionRequestError("ACTIONS_INVALID_ARGUMENT", "Integer argument is invalid.");
  }
  return number;
}

function requireConfirmed(value: unknown, errorCode: string): void {
  if (value !== true) {
    throw new ActionRequestError(errorCode, "Explicit user confirmation is required.");
  }
}

function truncate(value: unknown, maximum: number): string | null {
  if (typeof value !== "string" || !value) return null;
  return value.length <= maximum ? value : `${value.slice(0, maximum - 1)}…`;
}

function safeEqual(left: string, right: string): boolean {
  const leftBytes = Buffer.from(left);
  const rightBytes = Buffer.from(right);
  return leftBytes.length === rightBytes.length && timingSafeEqual(leftBytes, rightBytes);
}

function actionError(errorCode: string, message: string): Record<string, unknown> {
  return { status: "error", error_code: errorCode, message };
}

function sendJson(response: ServerResponse, status: number, value: unknown): void {
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
  });
  response.end(JSON.stringify(value));
}

function actionSummary(name: string): string {
  return name.replaceAll("_", " ");
}

function actionDescription(name: string): string {
  if (name === "delete_document") {
    return "Destructive. Call only after delete_preview and explicit user confirmation in the current conversation.";
  }
  if (name === "import_document") {
    return "Write action. Call only after import_preview and explicit user confirmation in the current conversation.";
  }
  return `Search ${name.replaceAll("_", " ")} tool.`;
}

function actionInputSchema(name: string): Record<string, unknown> {
  const properties: Record<string, unknown> = {};
  const required: string[] = [];
  if (name === "search") {
    properties.query = { type: "string", minLength: 1, maxLength: 2_000 };
    properties.limit = { type: "integer", minimum: 1, maximum: 20, default: 12 };
    required.push("query");
  } else if (name === "fetch") {
    properties.fragment_id = { type: "string", minLength: 1, maxLength: 500 };
    required.push("fragment_id");
  } else if (name === "export_evidence") {
    properties.fragment_ids = { type: "array", minItems: 1, maxItems: 50, items: { type: "string" } };
    properties.format = { type: "string", enum: ["markdown", "jsonl", "json"], default: "markdown" };
    required.push("fragment_ids");
  } else if (name === "list_library") {
    properties.scope = { type: "string", enum: ["imported", "catalog"], default: "imported" };
    properties.query = { type: "string", maxLength: 256 };
    properties.document_type = { type: "string", maxLength: 64 };
    properties.status = { type: "string", enum: ["active", "archived", "all"], default: "active" };
    properties.limit = { type: "integer", minimum: 1, maximum: 50, default: 20 };
  } else if (name === "import_preview") {
    properties.source_type = {
      type: "string",
      enum: ["local_pdf", "zotero_selected_book"],
      default: "local_pdf",
    };
    properties.inbox_filename = { type: "string", minLength: 1, maxLength: 255 };
    properties.zotero_item_key = { type: "string", minLength: 1, maxLength: 64 };
    properties.zotero_attachment_key = { type: "string", minLength: 1, maxLength: 64 };
    return {
      type: "object",
      additionalProperties: false,
      properties,
      oneOf: [
        {
          properties: {
            source_type: { type: "string", const: "local_pdf" },
          },
          not: {
            anyOf: [
              { required: ["zotero_item_key"] },
              { required: ["zotero_attachment_key"] },
            ],
          },
        },
        {
          required: ["source_type", "zotero_item_key"],
          properties: {
            source_type: { type: "string", const: "zotero_selected_book" },
          },
          not: { required: ["inbox_filename"] },
        },
      ],
      required,
    };
  } else if (name === "delete_preview") {
    properties.document_id = { type: "integer", minimum: 1 };
    required.push("document_id");
  } else {
    properties.confirmation_token = { type: "string", minLength: 32, maxLength: 256 };
    properties.confirmed = { type: "boolean", const: true };
    required.push("confirmation_token", "confirmed");
  }
  return { type: "object", additionalProperties: false, properties, required };
}

function errorSchema(): Record<string, unknown> {
  return {
    type: "object",
    required: ["status", "error_code", "message"],
    properties: {
      status: { type: "string", const: "error" },
      error_code: { type: "string" },
      message: { type: "string" },
    },
  };
}
