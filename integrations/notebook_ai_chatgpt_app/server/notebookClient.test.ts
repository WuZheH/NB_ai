import assert from "node:assert/strict";
import test from "node:test";

import { NOTEBOOK_SOURCE_TYPES } from "./contracts";
import { NotebookBackendError, NotebookClient } from "./notebookClient";

function validFragment(overrides: Record<string, unknown> = {}) {
  return {
    fragment_id: "fragment/one",
    source_type: "pdf_chunk",
    document_id: 1,
    document_title: "Paper",
    document_type: "pdf",
    pdf_page: 1,
    page_label: "1",
    heading: "Section",
    section: "Section",
    coherent_text: "Evidence",
    selected_source_text: null,
    user_note: null,
    context_before: null,
    context_after: null,
    tags: [],
    provenance: { source: "pdf", fragment_id: "fragment/one" },
    open_target: null,
    selection_rank: null,
    ...overrides,
  };
}

function validSearch(results: unknown[] = []) {
  return {
    status: "ok",
    query: "EDSR",
    mode: "high_quality_notebook_search_v1",
    embedding_model: "Qwen3-Embedding-0.6B",
    reranker_model: "Qwen3-Reranker-0.6B",
    backend: "local",
    result_count: results.length,
    results,
    warnings: [],
    latency: null,
  };
}

test("NotebookClient uses the fixed backend paths and caps search at 20", async () => {
  const requests: Array<{ url: string; init: RequestInit }> = [];
  const fetchImpl: typeof fetch = async (input, init = {}) => {
    requests.push({ url: String(input), init });
    if (String(input).includes("/fragments/")) {
      return Response.json(validFragment());
    }
    if (String(input).includes("/evidence/export")) {
      return Response.json({
        status: "ok",
        content: JSON.stringify({ fragment_id: "fragment/one" }),
      });
    }
    return Response.json(validSearch());
  };
  const client = new NotebookClient({ baseUrl: "http://127.0.0.1:8000", fetchImpl });

  await client.search({
    query: "EDSR",
    limit: 99,
    source_types: [...NOTEBOOK_SOURCE_TYPES],
    document_ids: [],
    include_context: true,
  });
  await client.fetchFragment("fragment/one");
  await client.exportEvidence({ fragment_ids: ["fragment/one"], format: "jsonl", query: "EDSR" });

  assert.equal(requests[0].url, "http://127.0.0.1:8000/api/v1/retrieval/notebook-search");
  assert.equal(requests[0].init.method, "POST");
  assert.equal(JSON.parse(String(requests[0].init.body)).limit, 20);
  assert.equal(requests[1].url, "http://127.0.0.1:8000/api/v1/retrieval/fragments/fragment%2Fone");
  assert.equal(requests[1].init.method, "GET");
  assert.equal(requests[2].url, "http://127.0.0.1:8000/api/v1/retrieval/evidence/export");
  assert.equal(requests[2].init.method, "POST");
});

test("NotebookClient rejects insecure non-loopback HTTP and exports over 50", async () => {
  assert.throws(() => new NotebookClient({ baseUrl: "http://example.test" }), /HTTPS or loopback HTTP/);
  const client = new NotebookClient({
    fetchImpl: async () => Response.json({}),
  });
  await assert.rejects(
    () => client.exportEvidence({ fragment_ids: Array.from({ length: 51 }, (_, index) => String(index)), format: "json" }),
    /limited to 50/,
  );
});

test("NotebookClient prefers the public Search backend URL", async () => {
  let requestedUrl = "";
  const client = new NotebookClient({
    env: {
      SEARCH_BACKEND_URL: "http://127.0.0.1:8124",
      NOTEBOOK_AI_BACKEND_URL: "http://127.0.0.1:8125",
    },
    fetchImpl: async (input) => {
      requestedUrl = String(input);
      return Response.json(validSearch());
    },
  });
  await client.search({
    query: "portable",
    limit: 1,
    source_types: ["pdf_chunk"],
    document_ids: [],
    include_context: false,
  });
  assert.match(requestedUrl, /^http:\/\/127\.0\.0\.1:8124\//);
});

test("NotebookClient makes backend-relative open targets absolute without rewriting Zotero URIs", async () => {
  const fragment = validFragment({
    fragment_id: "fragment-1",
    open_target: {
      pdf_url: "/api/v1/library/documents/7/pdf#page=12",
      zotero_url: "zotero://select/library/items/ABC123",
      can_open_pdf: true,
    },
  });
  const fetchImpl: typeof fetch = async (input) => {
    if (String(input).includes("/notebook-search")) {
      return Response.json(validSearch([fragment]));
    }
    return Response.json({ status: "ok", fragment });
  };
  const client = new NotebookClient({ baseUrl: "http://127.0.0.1:8123", fetchImpl });

  const search = await client.search({
    query: "EDSR",
    limit: 1,
    source_types: [...NOTEBOOK_SOURCE_TYPES],
    document_ids: [],
    include_context: true,
  });
  assert.equal(search.results[0].open_target?.pdf_url, "http://127.0.0.1:8123/api/v1/library/documents/7/pdf#page=12");
  assert.equal(search.results[0].open_target?.zotero_url, "zotero://select/library/items/ABC123");
  assert.equal(search.results[0].open_target?.can_open_pdf, false);
  assert.equal(
    search.results[0].open_target?.pdf_disabled_reason,
    "PDF opening is available in the local desktop app.",
  );

  const fetched = await client.fetchFragment("fragment-1");
  assert.equal(
    "fragment" in fetched ? fetched.fragment?.open_target?.pdf_url : null,
    "http://127.0.0.1:8123/api/v1/library/documents/7/pdf#page=12",
  );
});

test("NotebookClient normalizes export open targets to the same public URL", async () => {
  const content = JSON.stringify({
    results: [
      {
        fragment_id: "fragment-1",
        open_target: {
          pdf_url: "/api/v1/library/documents/7/pdf#page=12",
          zotero_url: "zotero://select/library/items/ABC123",
          can_open_pdf: true,
        },
      },
    ],
  });
  const client = new NotebookClient({
    baseUrl: "http://127.0.0.1:8123",
    fetchImpl: async () => Response.json({ status: "ok", content }),
  });
  const response = await client.exportEvidence({
    fragment_ids: ["fragment-1"],
    format: "json",
  });
  assert.equal(typeof response, "object");
  const parsed = JSON.parse(String((response as { content: string }).content));
  assert.equal(
    parsed.results[0].open_target.pdf_url,
    "http://127.0.0.1:8123/api/v1/library/documents/7/pdf#page=12",
  );
  assert.equal(
    parsed.results[0].open_target.zotero_url,
    "zotero://select/library/items/ABC123",
  );
  assert.equal(parsed.results[0].open_target.can_open_pdf, false);
  assert.equal(
    parsed.results[0].open_target.pdf_disabled_reason,
    "PDF opening is available in the local desktop app.",
  );
});

test("NotebookClient preserves structured machine config errors without exposing paths", async () => {
  const client = new NotebookClient({
    baseUrl: "http://127.0.0.1:8123",
    fetchImpl: async () => Response.json(
      {
        detail: {
          error: "high_quality_search_configuration_unavailable",
          error_code: "config_missing",
          message: "High-quality search configuration is unavailable.",
        },
      },
      { status: 503 },
    ),
  });
  await assert.rejects(
    client.search({
      query: "probe",
      limit: 1,
      source_types: ["pdf_chunk"],
      document_ids: [],
      include_context: false,
    }),
    (error: unknown) => error instanceof NotebookBackendError
      && error.code === "config_missing"
      && !error.message.includes("D:\\"),
  );
});

test("NotebookClient retains only whitelisted import failure details", async () => {
  const rawToken = "RAW_CONFIRMATION_TOKEN_MUST_NOT_SURVIVE";
  const operationId = "a".repeat(32);
  const client = new NotebookClient({
    baseUrl: "http://127.0.0.1:8123",
    bearerToken: "request-authorization-secret",
    fetchImpl: async () => Response.json(
      {
        detail: {
          error_code: "import_document_failed",
          message: "The confirmed import failed.",
          token_consumed: true,
          writes_performed: true,
          safe_to_retry: false,
          status: "failed",
          operation_id: operationId,
          terminal: true,
          confirmation_token: rawToken,
          authorization: "Bearer request-authorization-secret",
          private_backend_field: "must not survive",
        },
      },
      { status: 500 },
    ),
  });

  await assert.rejects(
    client.importDocument({
      confirmation_token: "i".repeat(40),
      confirmed: true,
    }),
    (error: unknown) => {
      if (!(error instanceof NotebookBackendError)) return false;
      assert.deepEqual(error.details, {
        status: "failed",
        operation_id: operationId,
        terminal: true,
        token_consumed: true,
        writes_performed: true,
        safe_to_retry: false,
      });
      assert.equal(JSON.stringify(error.details).includes(rawToken), false);
      assert.equal(JSON.stringify(error.details).includes("request-authorization-secret"), false);
      return true;
    },
  );
});

test("NotebookClient classifies malformed success responses without leaking content", async () => {
  const client = new NotebookClient({
    baseUrl: "http://127.0.0.1:8123",
    fetchImpl: async () => Response.json({ status: "ok", private_note: "do not leak" }),
  });
  await assert.rejects(
    client.search({
      query: "probe",
      limit: 1,
      source_types: ["pdf_chunk"],
      document_ids: [],
      include_context: false,
    }),
    (error: unknown) => error instanceof NotebookBackendError
      && error.code === "BACKEND_RESPONSE_INVALID"
      && !error.message.includes("do not leak"),
  );
});

test("NotebookClient classifies a rejected fetch as backend unavailable", async () => {
  const client = new NotebookClient({
    baseUrl: "http://127.0.0.1:8123",
    fetchImpl: async () => {
      throw new TypeError("private connection detail");
    },
  });
  await assert.rejects(
    client.search({
      query: "probe",
      limit: 1,
      source_types: ["pdf_chunk"],
      document_ids: [],
      include_context: false,
    }),
    (error: unknown) => error instanceof NotebookBackendError
      && error.status === 503
      && error.code === "BACKEND_UNAVAILABLE"
      && !error.message.includes("private connection detail"),
  );
});

test("NotebookClient gives confirmed imports a bounded long-running window", async () => {
  const client = new NotebookClient({
    baseUrl: "http://127.0.0.1:8123",
    timeoutMs: 1,
    importTimeoutMs: 50,
    fetchImpl: async () => {
      await new Promise((resolve) => setTimeout(resolve, 15));
      return Response.json({
        status: "committed",
        document_id: 8,
        title: "Selected book",
        document_type: "book",
        chunk_count: 3_899,
        duplicate_status: "not_detected",
        error_code: null,
      });
    },
  });

  const result = await client.importDocument({
    confirmation_token: "i".repeat(40),
    confirmed: true,
  });

  assert.equal(result.status, "committed");
  assert.equal(result.document_id, 8);
  assert.equal(result.chunk_count, 3_899);
});

test("NotebookClient reports its confirmed-import deadline as a backend timeout", async () => {
  const client = new NotebookClient({
    baseUrl: "http://127.0.0.1:8123",
    timeoutMs: 1,
    importTimeoutMs: 5,
    fetchImpl: async (_input, init = {}) => await new Promise<Response>((_resolve, reject) => {
      const signal = init.signal;
      assert.ok(signal);
      signal.addEventListener("abort", () => reject(signal.reason), { once: true });
    }),
  });

  await assert.rejects(
    client.importDocument({
      confirmation_token: "i".repeat(40),
      confirmed: true,
    }),
    (error: unknown) => error instanceof NotebookBackendError
      && error.status === 504
      && error.code === "BACKEND_TIMEOUT"
      && error.details === null,
  );
});

test("NotebookClient import_status uses the durable read-only endpoint and strips private fields", async () => {
  const operationId = "a".repeat(32);
  const requests: Array<{ url: string; init: RequestInit }> = [];
  const client = new NotebookClient({
    baseUrl: "http://127.0.0.1:8123",
    fetchImpl: async (input, init = {}) => {
      requests.push({ url: String(input), init });
      return Response.json({
        status: "running",
        operation_id: operationId,
        document_id: null,
        title: "Fixture",
        document_type: "book",
        chunk_count: null,
        terminal: false,
        operation_in_progress: true,
        writes_performed: true,
        token_consumed: true,
        safe_to_retry: false,
        replayed_receipt: false,
        error_code: null,
        error_stage: null,
        rollback_attempted: null,
        rollback_completed: null,
        confirmation_token: "RAW_TOKEN_MUST_NOT_SURVIVE",
        confirmation_token_digest: "DIGEST_MUST_NOT_SURVIVE",
        journal_path: "D:\\private\\journal.json",
        staged_path: "D:\\private\\fixture.pdf",
      });
    },
  });

  const response = await client.importStatus({ operation_id: operationId });

  assert.equal(requests[0].url, "http://127.0.0.1:8123/api/v1/chat-tools/import-status");
  assert.equal(requests[0].init.method, "POST");
  assert.deepEqual(JSON.parse(String(requests[0].init.body)), { operation_id: operationId });
  assert.equal(new Headers(requests[0].init.headers).get("X-Search-Chat-Adapter"), "mcp");
  assert.deepEqual(response, {
    status: "running",
    operation_id: operationId,
    document_id: null,
    title: "Fixture",
    document_type: "book",
    chunk_count: null,
    terminal: false,
    operation_in_progress: true,
    writes_performed: true,
    token_consumed: true,
    safe_to_retry: false,
    replayed_receipt: false,
    error_code: null,
    error_stage: null,
    rollback_attempted: null,
    rollback_completed: null,
  });
  assert.doesNotMatch(JSON.stringify(response), /RAW_TOKEN|DIGEST|private|journal_path|staged_path/);
});

test("NotebookClient import_status rejects an incomplete or unknown status response", async () => {
  const client = new NotebookClient({
    baseUrl: "http://127.0.0.1:8123",
    fetchImpl: async () => Response.json({
      status: "mystery",
      operation_id: "a".repeat(32),
    }),
  });

  await assert.rejects(
    client.importStatus({ operation_id: "a".repeat(32) }),
    (error: unknown) => error instanceof NotebookBackendError
      && error.code === "BACKEND_RESPONSE_INVALID",
  );
});
