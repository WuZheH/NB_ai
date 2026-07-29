import {
  NOTEBOOK_SOURCE_TYPES,
  type EvidenceExportInput,
  type EvidenceExportResponse,
  type FragmentResponse,
  type ImportDocumentInput,
  type ImportDocumentResponse,
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

  constructor(message: string, status: number, code = "NOTEBOOK_BACKEND_ERROR") {
    super(message);
    this.name = "NotebookBackendError";
    this.status = status;
    this.code = code;
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
        "Search backend returned an invalid response.",
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
    return response;
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
    return this.requestChatTool<ImportPreviewResponse>(
      "/api/v1/chat-tools/import-preview",
      {
        source_type: input.source_type,
        inbox_filename: input.inbox_filename,
        zotero_item_key: input.zotero_item_key,
        zotero_attachment_key: input.zotero_attachment_key,
      },
      "ok",
    );
  }

  async importDocument(input: ImportDocumentInput): Promise<ImportDocumentResponse> {
    return this.requestChatTool<ImportDocumentResponse>(
      "/api/v1/chat-tools/import-document",
      input,
      undefined,
      this.importTimeoutMs,
    );
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
        try {
          const parsed = JSON.parse(raw) as {
            detail?: unknown;
            message?: unknown;
            code?: unknown;
            error_code?: unknown;
          };
          if (parsed.detail && typeof parsed.detail === "object" && !Array.isArray(parsed.detail)) {
            const structured = parsed.detail as { message?: unknown; code?: unknown; error_code?: unknown; error?: unknown };
            detail = typeof structured.message === "string" ? structured.message : detail;
            code = String(structured.error_code ?? structured.code ?? structured.error ?? code);
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
        throw new NotebookBackendError(detail, response.status, code);
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
        throw new NotebookBackendError("Search backend request timed out.", 504, "BACKEND_TIMEOUT");
      }
      if (error instanceof SyntaxError) {
        throw invalidBackendResponse();
      }
      if (error instanceof TypeError) {
        throw new NotebookBackendError(
          "Search backend is unavailable.",
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
    const openTarget = { ...result.open_target };
    for (const key of OPEN_TARGET_URL_KEYS) {
      const value = openTarget[key];
      if (typeof value !== "string" || !value.trim()) {
        continue;
      }
      try {
        // Preserve absolute HTTPS, loopback, and custom-scheme targets such as
        // zotero:// exactly as the backend returned them.
        new URL(value);
      } catch {
        openTarget[key] = new URL(value, this.baseUrl).toString();
      }
    }
    return { ...result, open_target: openTarget };
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
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
    "Search backend returned an invalid response.",
    502,
    "BACKEND_RESPONSE_INVALID",
  );
}
