import {
  NOTEBOOK_SOURCE_TYPES,
  type EvidenceFormat,
  type EvidenceExportInput,
  type EvidenceExportResponse,
  type FragmentResponse,
  type ImportDocumentInput,
  type ImportDocumentResponse,
  type ImportStatusInput,
  type ImportStatusResponse,
  type ImportPreviewInput,
  type ImportPreviewResponse,
  type IntegrityReportInput,
  type IntegrityReportResponse,
  type ListLibraryInput,
  type ListLibraryResponse,
  type DeleteDocumentInput,
  type DeleteDocumentResponse,
  type DeletePreviewResponse,
  type NotebookFragment,
  type NotebookResult,
  type NotebookSearchInput,
  type NotebookSearchResponse,
} from "./contracts.js";

export interface NotebookClientOptions {
  baseUrl?: string;
  bearerToken?: string;
  timeoutMs?: number;
  importTimeoutMs?: number;
  fetchImpl?: typeof fetch;
  env?: NodeJS.ProcessEnv;
  adapter?: "mcp" | "actions";
}

export class NotebookBackendError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown> | null;

  constructor(
    message: string,
    status: number,
    code = "NOTEBOOK_BACKEND_ERROR",
    details: Record<string, unknown> | null = null,
  ) {
    super(message);
    this.name = "NotebookBackendError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

function validateBaseUrl(value: string): URL {
  const url = new URL(value);
  const isLoopback = ["127.0.0.1", "localhost", "::1"].includes(url.hostname);
  if (url.protocol !== "https:" && !(url.protocol === "http:" && isLoopback)) {
    throw new Error("SEARCH_BACKEND_URL must use HTTPS or loopback HTTP.");
  }
  return url;
}

const OPEN_TARGET_URL_KEYS = ["url", "href", "pdf_url", "zotero_url", "open_url"] as const;

export class NotebookClient {
  private readonly baseUrl: URL;
  private readonly bearerToken?: string;
  private readonly timeoutMs: number;
  private readonly importTimeoutMs: number;
  private readonly fetchImpl: typeof fetch;
  private readonly adapter: "mcp" | "actions";

  constructor(options: NotebookClientOptions = {}) {
    const environment = options.env ?? process.env;
    this.baseUrl = validateBaseUrl(
      options.baseUrl
        ?? environment.SEARCH_BACKEND_URL
        ?? environment.NOTEBOOK_AI_BACKEND_URL
        ?? "http://127.0.0.1:8000",
    );
    this.bearerToken = options.bearerToken
      ?? environment.SEARCH_BACKEND_BEARER_TOKEN
      ?? environment.NOTEBOOK_AI_BACKEND_BEARER_TOKEN;
    this.timeoutMs = options.timeoutMs ?? 120_000;
    this.importTimeoutMs = Math.max(
      this.timeoutMs,
      options.importTimeoutMs ?? 900_000,
    );
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch;
    this.adapter = options.adapter ?? "mcp";
  }

  async search(input: NotebookSearchInput): Promise<NotebookSearchResponse> {
    const response = await this.requestJson<NotebookSearchResponse>("/api/v1/retrieval/notebook-search", {
      method: "POST",
      body: JSON.stringify({ ...input, limit: Math.min(20, input.limit) }),
    });
    if (
      !isRecord(response)
      || response.status !== "ok"
      || !Array.isArray(response.results)
      || !Array.isArray(response.warnings)
      || typeof response.query !== "string"
      || typeof response.mode !== "string"
      || typeof response.embedding_model !== "string"
      || typeof response.reranker_model !== "string"
      || response.results.some((result) => !isNotebookFragment(result))
    ) {
      throw new NotebookBackendError(
        "READ backend returned an invalid response.",
        502,
        "BACKEND_RESPONSE_INVALID",
      );
    }
    return {
      ...response,
      results: response.results.map((result) => this.resolveOpenTarget(result)),
    };
  }

  async fetchFragment(fragmentId: string): Promise<FragmentResponse | NotebookFragment> {
    const response = await this.requestJson<FragmentResponse | NotebookFragment>(
      `/api/v1/retrieval/fragments/${encodeURIComponent(fragmentId)}`,
      { method: "GET" },
    );
    if (!isRecord(response)) {
      throw invalidBackendResponse();
    }
    if ("fragment" in response && response.fragment) {
      if (!isNotebookFragment(response.fragment)) {
        throw invalidBackendResponse();
      }
      return { ...response, fragment: this.resolveOpenTarget(response.fragment) };
    }
    if ("result" in response && response.result) {
      if (!isNotebookFragment(response.result)) {
        throw invalidBackendResponse();
      }
      return { ...response, result: this.resolveOpenTarget(response.result) };
    }
    if (!isNotebookFragment(response)) {
      throw invalidBackendResponse();
    }
    return this.resolveOpenTarget(response as NotebookFragment);
  }

  async exportEvidence(input: EvidenceExportInput): Promise<EvidenceExportResponse | string> {
    if (input.fragment_ids.length > 50) {
      throw new Error("Evidence export is limited to 50 fragments.");
    }
    const response = await this.requestJson<EvidenceExportResponse | string>("/api/v1/retrieval/evidence/export", {
      method: "POST",
      body: JSON.stringify(input),
    });
    if (
      typeof response !== "string"
      && (
        !isRecord(response)
        || !["content", "text", "output"].some((key) => typeof response[key] === "string")
      )
    ) {
      throw invalidBackendResponse();
    }
    return this.resolveExportOpenTargets(response, input.format);
  }

  async listLibrary(input: ListLibraryInput): Promise<ListLibraryResponse> {
    return this.requestChatTool<ListLibraryResponse>("/api/v1/chat-tools/list-library", { scope: input.scope ?? "imported", ...input }, "ok");
  }

  async integrityReport(input: IntegrityReportInput): Promise<IntegrityReportResponse> {
    return this.requestChatTool<IntegrityReportResponse>(
      "/api/v1/chat-tools/integrity-report",
      input,
      "ok",
    );
  }

  async importPreview(input: ImportPreviewInput): Promise<ImportPreviewResponse> {
    const response = await this.requestChatTool<ImportPreviewResponse>(
      "/api/v1/chat-tools/import-preview",
      {
        source_type: input.source_type,
        inbox_filename: input.inbox_filename,
        zotero_item_key: input.zotero_item_key,
        zotero_attachment_key: input.zotero_attachment_key,
      },
      "ok",
    );
    const operationIdIsValid = typeof response.operation_id === "string"
      && /^[0-9a-f]{32}$/.test(response.operation_id);
    if (
      (response.operation_id !== null && !operationIdIsValid)
      || (response.confirmation_token !== null && !operationIdIsValid)
    ) {
      throw invalidBackendResponse();
    }
    return response;
  }

  async importDocument(input: ImportDocumentInput): Promise<ImportDocumentResponse> {
    return this.requestChatTool<ImportDocumentResponse>(
      "/api/v1/chat-tools/import-document",
      input,
      undefined,
      this.importTimeoutMs,
    );
  }

  async importStatus(input: ImportStatusInput): Promise<ImportStatusResponse> {
    const response = await this.requestChatTool<Record<string, unknown>>(
      "/api/v1/chat-tools/import-status",
      input,
    );
    return normalizeImportStatusResponse(response);
  }

  async deletePreview(documentId: number): Promise<DeletePreviewResponse> {
    return this.requestChatTool<DeletePreviewResponse>(
      "/api/v1/chat-tools/delete-preview",
      { document_id: documentId },
      "ok",
    );
  }

  async deleteDocument(input: DeleteDocumentInput): Promise<DeleteDocumentResponse> {
    return this.requestChatTool<DeleteDocumentResponse>("/api/v1/chat-tools/delete-document", input);
  }

  private async requestChatTool<T>(
    path: string,
    input: unknown,
    expectedStatus?: string,
    timeoutMs: number = this.timeoutMs,
  ): Promise<T> {
    const response = await this.requestJson<T>(path, {
      method: "POST",
      headers: { "X-Search-Chat-Adapter": this.adapter },
      body: JSON.stringify(input),
    }, timeoutMs);
    if (
      !isRecord(response)
      || typeof response.status !== "string"
      || (expectedStatus !== undefined && response.status !== expectedStatus)
    ) {
      throw invalidBackendResponse();
    }
    return response as T;
  }

  private async requestJson<T>(
    path: string,
    init: RequestInit,
    timeoutMs: number = this.timeoutMs,
  ): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json, text/plain;q=0.9");
    if (init.body) {
      headers.set("Content-Type", "application/json");
    }
    if (this.bearerToken) {
      headers.set("Authorization", `Bearer ${this.bearerToken}`);
    }

    try {
      const response = await this.fetchImpl(new URL(path, this.baseUrl), {
        ...init,
        headers,
        signal: controller.signal,
      });
      const raw = await response.text();
      if (!response.ok) {
        let detail = response.statusText || "Backend request failed";
        let code = "NOTEBOOK_BACKEND_ERROR";
        let backendDetails: Record<string, unknown> | null = null;
        try {
          const parsed = JSON.parse(raw) as {
            detail?: unknown;
            message?: unknown;
            code?: unknown;
            error_code?: unknown;
          };
          if (parsed.detail && typeof parsed.detail === "object" && !Array.isArray(parsed.detail)) {
            const structured = parsed.detail as Record<string, unknown>;
            detail = typeof structured.message === "string" ? structured.message : detail;
            code = String(structured.error_code ?? structured.code ?? structured.error ?? code);
            // Keep only the structured fields that the MCP import error
            // contract is allowed to propagate.  Request headers, raw
            // confirmation tokens, and arbitrary backend fields are never
            // retained on the adapter error.
            backendDetails = backendErrorDetails(structured);
          } else {
            detail = typeof parsed.detail === "string"
              ? parsed.detail
              : typeof parsed.message === "string"
                ? parsed.message
                : detail;
            code = String(parsed.error_code ?? parsed.code ?? code);
          }
        } catch {
          // Non-JSON backend failures use the generic, privacy-safe contract.
        }
        throw new NotebookBackendError(detail, response.status, code, backendDetails);
      }

      const contentType = response.headers.get("content-type") ?? "";
      if (contentType.includes("application/json")) {
        return JSON.parse(raw) as T;
      }
      return raw as T;
    } catch (error) {
      if (error instanceof NotebookBackendError) {
        throw error;
      }
      if (error instanceof Error && error.name === "AbortError") {
        throw new NotebookBackendError("READ backend request timed out.", 504, "BACKEND_TIMEOUT");
      }
      if (error instanceof SyntaxError) {
        throw invalidBackendResponse();
      }
      if (error instanceof TypeError) {
        throw new NotebookBackendError(
          "READ backend is unavailable.",
          503,
          "BACKEND_UNAVAILABLE",
        );
      }
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }

  private resolveOpenTarget<T extends NotebookFragment>(result: T): T {
    if (!result.open_target) {
      return result;
    }
    const openTarget = this.normalizeOpenTarget(result.open_target);
    return { ...result, open_target: openTarget };
  }

  private resolveExportOpenTargets(
    response: EvidenceExportResponse | string,
    format: EvidenceFormat,
  ): EvidenceExportResponse | string {
    if (typeof response === "string") {
      return this.normalizeExportContent(response, format);
    }
    const normalized = { ...response };
    for (const key of ["content", "text", "output"] as const) {
      const value = normalized[key];
      if (typeof value === "string") {
        normalized[key] = this.normalizeExportContent(value, format);
      }
    }
    return normalized;
  }

  private normalizeExportContent(content: string, format: EvidenceFormat): string {
    if (format === "markdown") {
      return content.replace(
        /\]\((\/api\/v1\/library\/documents\/[^)\s]+)\)/g,
        (_match, target: string) => `](${new URL(target, this.baseUrl).toString()})`,
      );
    }
    try {
      if (format === "json") {
        return JSON.stringify(this.normalizeExportValue(JSON.parse(content)), null, 2);
      }
      return content
        .split(/\r?\n/)
        .filter((line) => line.trim())
        .map((line) => JSON.stringify(this.normalizeExportValue(JSON.parse(line))))
        .join("\n");
    } catch {
      throw invalidBackendResponse();
    }
  }

  private normalizeExportValue(value: unknown): unknown {
    if (Array.isArray(value)) {
      return value.map((item) => this.normalizeExportValue(item));
    }
    if (!isRecord(value)) {
      return value;
    }
    const normalized: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value)) {
      normalized[key] = this.normalizeExportValue(item);
    }
    if (isRecord(normalized.open_target)) {
      normalized.open_target = this.normalizeOpenTarget(normalized.open_target);
    }
    return normalized;
  }

  private normalizeOpenTarget(value: Record<string, unknown>): Record<string, unknown> {
    const openTarget = { ...value };
    for (const key of OPEN_TARGET_URL_KEYS) {
      const target = openTarget[key];
      if (typeof target !== "string" || !target.trim()) {
        continue;
      }
      try {
        // Preserve absolute HTTPS, loopback, and custom-scheme targets such as
        // zotero:// exactly as the backend returned them.
        new URL(target);
      } catch {
        openTarget[key] = new URL(target, this.baseUrl).toString();
      }
    }

    const pdfUrl = openTarget.pdf_url;
    if (typeof pdfUrl === "string" && isLoopbackHttpUrl(pdfUrl)) {
      openTarget.can_open_pdf = false;
      openTarget.pdf_disabled_reason = "PDF opening is available in the local desktop app.";
    }
    return openTarget;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isLoopbackHttpUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return (url.protocol === "http:" || url.protocol === "https:")
      && ["127.0.0.1", "localhost", "::1", "[::1]"].includes(url.hostname.toLowerCase());
  } catch {
    return false;
  }
}

function isNotebookFragment(value: unknown): value is NotebookFragment {
  return isRecord(value)
    && typeof value.fragment_id === "string"
    && typeof value.source_type === "string"
    && (NOTEBOOK_SOURCE_TYPES as readonly string[]).includes(value.source_type)
    && Array.isArray(value.tags)
    && value.tags.every((tag) => typeof tag === "string")
    && isRecord(value.provenance);
}

function invalidBackendResponse(): NotebookBackendError {
  return new NotebookBackendError(
    "READ backend returned an invalid response.",
    502,
    "BACKEND_RESPONSE_INVALID",
  );
}

const BACKEND_ERROR_DETAIL_FIELDS = [
  "status",
  "operation_id",
  "terminal",
  "operation_in_progress",
  "token_consumed",
  "writes_performed",
  "safe_to_retry",
  "replayed_receipt",
  "publish_substage",
  "cause_type",
  "cause_message",
  "cause_errno",
  "cause_winerror",
  "cause_filename",
  "cause_filename2",
  "rollback_attempted",
  "rollback_completed",
  "error_stage",
  "retryable",
] as const;

function backendErrorDetails(
  structured: Record<string, unknown>,
): Record<string, unknown> {
  const details: Record<string, unknown> = {};
  for (const key of BACKEND_ERROR_DETAIL_FIELDS) {
    if (Object.prototype.hasOwnProperty.call(structured, key)) {
      details[key] = structured[key];
    }
  }
  return details;
}

const IMPORT_OPERATION_STATUSES = new Set([
  "accepted",
  "running",
  "committed",
  "failed",
  "orphaned",
]);

function normalizeImportStatusResponse(
  response: Record<string, unknown>,
): ImportStatusResponse {
  const operationId = response.operation_id;
  const status = response.status;
  if (
    typeof operationId !== "string"
    || !/^[0-9a-f]{32}$/.test(operationId)
    || typeof status !== "string"
    || !IMPORT_OPERATION_STATUSES.has(status)
    || typeof response.terminal !== "boolean"
    || typeof response.operation_in_progress !== "boolean"
    || typeof response.safe_to_retry !== "boolean"
    || typeof response.replayed_receipt !== "boolean"
    || !isNullableBoolean(response.writes_performed)
    || !isNullableBoolean(response.token_consumed)
    || !isNullableBoolean(response.rollback_attempted)
    || !isNullableBoolean(response.rollback_completed)
    || !isNullablePositiveInteger(response.document_id)
    || !isNullableNonnegativeInteger(response.chunk_count)
    || !isNullableString(response.title)
    || !isNullableString(response.document_type)
    || !isNullableSafeCode(response.error_code)
    || !isNullableSafeCode(response.error_stage)
  ) {
    throw invalidBackendResponse();
  }
  if (
    response.safe_to_retry !== false
    || ((status === "accepted" || status === "running")
      && (response.terminal !== false || response.operation_in_progress !== true))
    || ((status === "committed" || status === "failed" || status === "orphaned")
      && (response.terminal !== true || response.operation_in_progress !== false))
  ) {
    throw invalidBackendResponse();
  }

  // Construct a fixed whitelist instead of forwarding the backend object.
  // This prevents confirmation tokens, digests, paths, or arbitrary journal
  // fields from crossing the public MCP/Actions boundary.
  return {
    status: status as ImportStatusResponse["status"],
    operation_id: operationId,
    document_id: response.document_id as number | null,
    title: response.title as string | null,
    document_type: response.document_type as string | null,
    chunk_count: response.chunk_count as number | null,
    terminal: response.terminal,
    operation_in_progress: response.operation_in_progress,
    writes_performed: response.writes_performed as boolean | null,
    token_consumed: response.token_consumed as boolean | null,
    safe_to_retry: false,
    replayed_receipt: response.replayed_receipt,
    error_code: response.error_code as string | null,
    error_stage: response.error_stage as string | null,
    rollback_attempted: response.rollback_attempted as boolean | null,
    rollback_completed: response.rollback_completed as boolean | null,
  };
}

function isNullableBoolean(value: unknown): value is boolean | null {
  return value === null || typeof value === "boolean";
}

function isNullablePositiveInteger(value: unknown): value is number | null {
  return value === null
    || (typeof value === "number" && Number.isSafeInteger(value) && value > 0);
}

function isNullableNonnegativeInteger(value: unknown): value is number | null {
  return value === null
    || (typeof value === "number" && Number.isSafeInteger(value) && value >= 0);
}

function isNullableString(value: unknown): value is string | null {
  return value === null || (typeof value === "string" && value.length <= 512);
}

function isNullableSafeCode(value: unknown): value is string | null {
  return value === null
    || (typeof value === "string" && /^[A-Za-z0-9_.:-]{1,128}$/.test(value));
}
