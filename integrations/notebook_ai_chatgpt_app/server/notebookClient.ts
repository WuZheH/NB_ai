import {
  type EvidenceExportInput,
  type EvidenceExportResponse,
  type FragmentResponse,
  type NotebookFragment,
  type NotebookResult,
  type NotebookSearchInput,
  type NotebookSearchResponse,
} from "./contracts.js";

export interface NotebookClientOptions {
  baseUrl?: string;
  bearerToken?: string;
  timeoutMs?: number;
  fetchImpl?: typeof fetch;
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
    throw new Error("NOTEBOOK_AI_BACKEND_URL must use HTTPS or loopback HTTP.");
  }
  return url;
}

const OPEN_TARGET_URL_KEYS = ["url", "href", "pdf_url", "zotero_url", "open_url"] as const;

export class NotebookClient {
  private readonly baseUrl: URL;
  private readonly bearerToken?: string;
  private readonly timeoutMs: number;
  private readonly fetchImpl: typeof fetch;

  constructor(options: NotebookClientOptions = {}) {
    this.baseUrl = validateBaseUrl(options.baseUrl ?? process.env.NOTEBOOK_AI_BACKEND_URL ?? "http://127.0.0.1:8000");
    this.bearerToken = options.bearerToken ?? process.env.NOTEBOOK_AI_BACKEND_BEARER_TOKEN;
    this.timeoutMs = options.timeoutMs ?? 120_000;
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch;
  }

  async search(input: NotebookSearchInput): Promise<NotebookSearchResponse> {
    const response = await this.requestJson<NotebookSearchResponse>("/api/v1/retrieval/notebook-search", {
      method: "POST",
      body: JSON.stringify({ ...input, limit: Math.min(20, input.limit) }),
    });
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
    if ("fragment" in response && response.fragment) {
      return { ...response, fragment: this.resolveOpenTarget(response.fragment) };
    }
    if ("result" in response && response.result) {
      return { ...response, result: this.resolveOpenTarget(response.result) };
    }
    return this.resolveOpenTarget(response as NotebookFragment);
  }

  async exportEvidence(input: EvidenceExportInput): Promise<EvidenceExportResponse | string> {
    if (input.fragment_ids.length > 50) {
      throw new Error("Evidence export is limited to 50 fragments.");
    }
    return this.requestJson<EvidenceExportResponse | string>("/api/v1/retrieval/evidence/export", {
      method: "POST",
      body: JSON.stringify(input),
    });
  }

  private async requestJson<T>(path: string, init: RequestInit): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
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
        try {
          const parsed = JSON.parse(raw) as { detail?: unknown; message?: unknown; code?: unknown };
          detail = String(parsed.detail ?? parsed.message ?? detail);
          throw new NotebookBackendError(detail, response.status, String(parsed.code ?? "NOTEBOOK_BACKEND_ERROR"));
        } catch (error) {
          if (error instanceof NotebookBackendError) {
            throw error;
          }
        }
        throw new NotebookBackendError(detail, response.status);
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
