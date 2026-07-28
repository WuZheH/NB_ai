import assert from "node:assert/strict";
import { mkdir, mkdtemp, readdir, readFile, rm } from "node:fs/promises";
import { resolve } from "node:path";
import test from "node:test";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";

import { actionsOpenApiDocument, authenticateActions, dispatchAction } from "./actions";
import { stageChatPdf } from "./fileTransfer";
import { createNotebookMcpServer } from "./app";
import type { NotebookFragment, NotebookResult, NotebookSearchInput } from "./contracts";
import { NotebookBackendError, NotebookClient } from "./notebookClient";
import { requireUnauthenticatedDevelopment } from "./security";
import { NOTEBOOK_TOOL_NAMES } from "./tools";
import { runImportDocumentTool } from "./tools/importDocument";
import { runImportPreviewTool } from "./tools/importPreview";
import { errorCode, errorToolResult } from "./tools/shared";
import { RESOURCE_MIME_TYPE } from "./widgetResource";

function result(sourceType: NotebookResult["source_type"] = "pdf_chunk"): NotebookResult {
  return {
    fragment_id: "fragment-1",
    source_type: sourceType,
    final_rank: 1,
    final_score: 0.9,
    reranker_score: 0.8,
    semantic_score: 0.7,
    document_id: 1,
    document_title: "Paper",
    document_type: "pdf",
    chunk_id: 1,
    pdf_page: 4,
    page_label: "4",
    text: sourceType === "pdf_chunk" ? "PDF source" : null,
    selected_text: sourceType === "pdf_chunk" ? null : "Selected source",
    note_text: sourceType === "pdf_chunk" ? null : "My note",
    context_before: "Before",
    context_after: "After",
    tags: [],
    provenance: [{ source: "test" }],
    open_target: null,
  };
}

function fragment(sourceType: NotebookFragment["source_type"] = "zotero_child_note"): NotebookFragment {
  const { final_rank: _rank, final_score: _score, reranker_score: _reranker, semantic_score: _semantic, ...value } =
    result(sourceType);
  return value;
}

class MockNotebookClient extends NotebookClient {
  readonly calls: Array<{ tool: string; input: unknown }> = [];

  constructor() {
    super({ baseUrl: "http://127.0.0.1:8000", fetchImpl: async () => new Response("{}") });
  }

  override async search(input: NotebookSearchInput) {
    this.calls.push({ tool: "search", input });
    return {
      status: "ok",
      query: input.query,
      mode: "high_quality_notebook_search_v1",
      embedding_model: "Qwen3-Embedding-0.6B",
      reranker_model: "Qwen3-Reranker-0.6B",
      backend: "test",
      result_count: 2,
      results: [result(), result("zotero_annotation_comment")],
      warnings: [],
      latency: { total_ms: 1 },
    };
  }

  override async fetchFragment(fragmentId: string) {
    this.calls.push({ tool: "fetch", input: fragmentId });
    return { status: "ok", fragment: fragment() };
  }

  override async exportEvidence(input: { fragment_ids: string[]; format: "markdown" | "jsonl" | "json"; query?: string }) {
    this.calls.push({ tool: "export_evidence", input });
    return { status: "ok", format: input.format, item_count: input.fragment_ids.length, content: "# Evidence" };
  }

  override async listLibrary(input: { query?: string; document_type?: string; status: "active" | "archived" | "all"; limit: number }) {
    this.calls.push({ tool: "list_library", input });
    return {
      status: "ok" as const,
      count: 1,
      items: [{
        document_id: 1,
        title: "Paper",
        type: "paper",
        imported_at: "2026-07-24T00:00:00Z",
        chunk_count: 2,
        has_pdf: true,
        duplicate_status: "not_evaluated",
        status: "active" as const,
      }],
      truncated: false,
    };
  }

  override async importPreview(input: { inbox_filename?: string }) {
    this.calls.push({ tool: "import_preview", input });
    return {
      status: "ok" as const,
      filename: input.inbox_filename ?? "fixture.pdf",
      title: "Fixture",
      pdf_sha256: "a".repeat(64),
      duplicate_status: "not_detected",
      existing_document_id: null,
      estimated_pages: 2,
      estimated_chunks: 6,
      document_type: "paper",
      warnings: [],
      confirmation_token: "i".repeat(40),
      confirmation_expires_in_seconds: 600,
    };
  }

  override async importDocument(input: { confirmation_token: string; confirmed: true }) {
    this.calls.push({ tool: "import_document", input });
    return {
      status: "committed",
      document_id: 3,
      title: "Fixture",
      document_type: "paper",
      chunk_count: 6,
      duplicate_status: "not_detected",
      error_code: null,
      already_completed: false,
      replayed_receipt: false,
    };
  }

  override async deletePreview(documentId: number) {
    this.calls.push({ tool: "delete_preview", input: documentId });
    return {
      status: "ok" as const,
      document_id: documentId,
      title: "Fixture",
      safe_to_delete: true,
      pdf_preserved: true,
      notes_preserved: true,
      blockers: [],
      confirmation_token: "d".repeat(40),
      confirmation_expires_in_seconds: 600,
    };
  }

  override async deleteDocument(input: { confirmation_token: string; confirmed: true }) {
    this.calls.push({ tool: "delete_document", input });
    return {
      status: "completed",
      document_id: 3,
      title: "Fixture",
      recovery_created: true,
      cleanup_complete: true,
      error_code: null,
    };
  }
}

test("tools/list exposes eight annotated tools and widget resource", async () => {
  const backend = new MockNotebookClient();
  const server = createNotebookMcpServer({ client: backend, widget: { html: "<html><body>widget</body></html>" } });
  const client = new Client({ name: "notebook-ai-test", version: "0.1.0" });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  await server.connect(serverTransport);
  await client.connect(clientTransport);
  try {
    const listed = await client.listTools();
    assert.deepEqual(
      listed.tools.map((tool) => tool.name).sort(),
      [...NOTEBOOK_TOOL_NAMES].sort(),
    );
    for (const tool of listed.tools) {
      const expectedAnnotations = tool.name === "delete_document"
        ? { readOnlyHint: false, destructiveHint: true, idempotentHint: false, openWorldHint: false }
        : tool.name === "import_document"
          ? { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false }
          : { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false };
      assert.deepEqual(tool.annotations, expectedAnnotations);
      assert.equal(tool.outputSchema?.type, "object", `${tool.name} declares an object outputSchema`);
      assert.ok(tool.outputSchema?.properties?.status, `${tool.name} outputSchema declares status`);
      const meta = tool._meta as {
        ui?: { resourceUri?: string; visibility?: string[] };
        "notebookAi/errorContract"?: string;
      } | undefined;
      assert.equal(meta?.["notebookAi/errorContract"], "isError-content-v1");
      assert.deepEqual(meta?.ui?.visibility, ["model", "app"]);
      assert.equal(
        meta?.ui?.resourceUri,
        tool.name === "search" ? "ui://notebook-ai/research-search-v1.html" : undefined,
        "only search mounts the results widget",
      );
    }
    const importPreview = listed.tools.find((tool) => tool.name === "import_preview");
    assert.deepEqual(importPreview?._meta?.["openai/fileParams"], ["file"]);
    const fileSchema = (
      importPreview?.inputSchema?.properties?.file as {
        properties?: Record<string, unknown>;
        required?: string[];
      } | undefined
    );
    assert.deepEqual(
      Object.keys(fileSchema?.properties ?? {}).sort(),
      ["download_url", "file_id", "file_name", "mime_type"],
    );
    assert.deepEqual([...(fileSchema?.required ?? [])].sort(), ["download_url", "file_id"]);

    const resource = await client.readResource({ uri: "ui://notebook-ai/research-search-v1.html" });
    assert.equal(resource.contents[0]?.mimeType, RESOURCE_MIME_TYPE);
    assert.equal(resource.contents[0]?.mimeType, "text/html;profile=mcp-app");
    const resourceMeta = resource.contents[0]?._meta as
      | {
          ui?: {
            permissions?: { clipboardWrite?: Record<string, never> };
            csp?: { connectDomains?: string[]; resourceDomains?: string[] };
            domain?: string;
          };
          "openai/widgetCSP"?: { connect_domains?: string[]; resource_domains?: string[] };
          "notebookAi/widgetDomainMode"?: string;
        }
      | undefined;
    assert.deepEqual(resourceMeta?.ui?.permissions, { clipboardWrite: {} });
    assert.deepEqual(resourceMeta?.ui?.csp, { connectDomains: [], resourceDomains: [] });
    assert.deepEqual(resourceMeta?.["openai/widgetCSP"], { connect_domains: [], resource_domains: [] });
    assert.equal(resourceMeta?.ui?.domain, undefined);
    assert.equal(resourceMeta?.["notebookAi/widgetDomainMode"], "development-only");
  } finally {
    await client.close();
    await server.close();
  }
});

test("widget domain is emitted only for an explicitly configured HTTPS origin", async () => {
  const backend = new MockNotebookClient();
  const server = createNotebookMcpServer({
    client: backend,
    widget: { html: "<html></html>", widgetDomain: "https://widget.example/some/path" },
  });
  const client = new Client({ name: "notebook-ai-test", version: "0.1.0" });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  await server.connect(serverTransport);
  await client.connect(clientTransport);
  try {
    const resource = await client.readResource({ uri: "ui://notebook-ai/research-search-v1.html" });
    const meta = resource.contents[0]?._meta as
      | { ui?: { domain?: string }; "openai/widgetDomain"?: string; "notebookAi/widgetDomainMode"?: string }
      | undefined;
    assert.equal(meta?.ui?.domain, "https://widget.example");
    assert.equal(meta?.["openai/widgetDomain"], "https://widget.example");
    assert.equal(meta?.["notebookAi/widgetDomainMode"], "configured");
  } finally {
    await client.close();
    await server.close();
  }

  assert.throws(
    () => createNotebookMcpServer({ client: backend, widget: { html: "<html></html>", widgetDomain: "http://widget.example" } }),
    /must use HTTPS/,
  );
});

test("all eight tools call only the backend adapter", async () => {
  const backend = new MockNotebookClient();
  const server = createNotebookMcpServer({ client: backend, widget: { html: "<html></html>" } });
  const client = new Client({ name: "notebook-ai-test", version: "0.1.0" });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  await server.connect(serverTransport);
  await client.connect(clientTransport);
  try {
    const search = await client.callTool({ name: "search", arguments: { query: "foot skating", limit: 2 } });
    assert.equal(search.isError, undefined);
    assert.equal((search.structuredContent as { result_count: number }).result_count, 2);
    const modelResults = (search.structuredContent as { results: NotebookResult[] }).results;
    assert.equal(modelResults[1].note_text, "My note");
    assert.equal(modelResults[1].selected_text, "Selected source");

    const fetched = await client.callTool({ name: "fetch", arguments: { fragment_id: "fragment-1" } });
    const fetchedFragment = (fetched.structuredContent as { fragment: NotebookFragment }).fragment;
    assert.deepEqual(fetchedFragment.provenance, [{ source: "test" }]);
    assert.equal("final_rank" in fetchedFragment, false, "fetch accepts the real unranked fragment contract");

    const exported = await client.callTool({
      name: "export_evidence",
      arguments: { fragment_ids: ["fragment-1"], format: "markdown", query: "foot skating" },
    });
    assert.equal((exported.structuredContent as { item_count: number }).item_count, 1);
    const library = await client.callTool({ name: "list_library", arguments: { query: "motion" } });
    assert.equal((library.structuredContent as { count: number }).count, 1);
    const importPreview = await client.callTool({ name: "import_preview", arguments: { inbox_filename: "fixture.pdf" } });
    assert.equal((importPreview.structuredContent as { title: string }).title, "Fixture");
    const imported = await client.callTool({
      name: "import_document",
      arguments: { confirmation_token: "i".repeat(40), confirmed: true },
    });
    const importedPayload = imported.structuredContent as {
      document_id: number;
      already_completed: boolean;
      replayed_receipt: boolean;
    };
    assert.equal(importedPayload.document_id, 3);
    assert.equal(importedPayload.already_completed, false);
    assert.equal(importedPayload.replayed_receipt, false);
    const deletePreview = await client.callTool({ name: "delete_preview", arguments: { document_id: 3 } });
    assert.equal((deletePreview.structuredContent as { safe_to_delete: boolean }).safe_to_delete, true);
    const deleted = await client.callTool({
      name: "delete_document",
      arguments: { confirmation_token: "d".repeat(40), confirmed: true },
    });
    assert.equal((deleted.structuredContent as { cleanup_complete: boolean }).cleanup_complete, true);
    assert.deepEqual(
      backend.calls.map((call) => call.tool),
      [
        "search",
        "fetch",
        "export_evidence",
        "list_library",
        "import_preview",
        "import_document",
        "delete_preview",
        "delete_document",
      ],
    );
  } finally {
    await client.close();
    await server.close();
  }
});

test("anonymous startup is refused without the explicit development switch", () => {
  assert.throws(() => requireUnauthenticatedDevelopment({}), /Refusing to start an unauthenticated MCP server/);
  assert.deepEqual(requireUnauthenticatedDevelopment({ SEARCH_ALLOW_UNAUTHENTICATED_MCP_DEV: "1" }), {
    host: "127.0.0.1",
    port: 8787,
    unauthenticatedDevelopment: true,
  });
  assert.equal(requireUnauthenticatedDevelopment({
    NOTEBOOK_AI_ALLOW_UNAUTHENTICATED_MCP_DEV: "1",
  }).unauthenticatedDevelopment, true);
  assert.throws(() => requireUnauthenticatedDevelopment({
    SEARCH_ALLOW_UNAUTHENTICATED_MCP_DEV: "0",
    NOTEBOOK_AI_ALLOW_UNAUTHENTICATED_MCP_DEV: "1",
  }), /Refusing to start an unauthenticated MCP server/);
  assert.equal(requireUnauthenticatedDevelopment({
    SEARCH_ALLOW_UNAUTHENTICATED_MCP_DEV: "1",
    SEARCH_MCP_PORT: "9876",
    NOTEBOOK_AI_MCP_PORT: "9875",
  }).port, 9876);
});

test("Actions OpenAPI exposes the same eight operations with bearer authentication", () => {
  const document = actionsOpenApiDocument({
    SEARCH_ACTIONS_PUBLIC_BASE_URL: "https://search-actions.example/private",
  }) as {
    openapi: string;
    servers?: Array<{ url: string }>;
    paths: Record<string, { post?: { security?: unknown[]; description?: string } }>;
    components?: { securitySchemes?: Record<string, unknown> };
  };
  assert.equal(document.openapi, "3.1.0");
  assert.deepEqual(document.servers, [{ url: "https://search-actions.example/private" }]);
  assert.deepEqual(
    Object.keys(document.paths).sort(),
    NOTEBOOK_TOOL_NAMES.map((name) => `/actions/v1/${name}`).sort(),
  );
  assert.ok(document.components?.securitySchemes?.bearerAuth);
  for (const path of Object.values(document.paths)) {
    assert.deepEqual(path.post?.security, [{ bearerAuth: [] }]);
  }
  assert.match(document.paths["/actions/v1/delete_document"].post?.description ?? "", /explicit user confirmation/);
});

test("ChatGPT PDF file params stream to isolated staging and are removed after confirmed import", async () => {
  const temporaryRoot = resolve(process.cwd(), "..", "..", ".codex_tmp");
  await mkdir(temporaryRoot, { recursive: true });
  const stagingDirectory = await mkdtemp(resolve(temporaryRoot, "mcp-file-transfer-"));
  const client = new MockNotebookClient();
  try {
    const preview = await runImportPreviewTool(
      client,
      {
        file: {
          download_url: "https://files.openaiusercontent.com/fixture.pdf",
          file_id: "file_fixture",
          mime_type: "application/pdf",
          file_name: "fixture.pdf",
        },
      },
      {
        env: { SEARCH_IMPORT_INBOX: stagingDirectory },
        fetchImpl: async () =>
          new Response(Buffer.from("%PDF-1.4\nisolated attachment"), {
            status: 200,
            headers: { "content-type": "application/pdf" },
          }),
      },
    );
    assert.equal("structuredContent" in preview, true);
    const filesAfterPreview = await readdir(stagingDirectory);
    assert.equal(filesAfterPreview.length, 1);
    assert.match(filesAfterPreview[0], /^chat-upload-[a-f0-9]{16}-[a-f0-9]{24}\.pdf$/);
    assert.equal(
      (await readFile(resolve(stagingDirectory, filesAfterPreview[0]))).toString("utf8"),
      "%PDF-1.4\nisolated attachment",
    );
    const previewPayload = "structuredContent" in preview ? preview.structuredContent : null;
    assert.equal(previewPayload?.confirmation_token, "i".repeat(40));
    const imported = await runImportDocumentTool(client, {
      confirmation_token: "i".repeat(40),
      confirmed: true,
    });
    assert.equal("structuredContent" in imported, true);
    assert.deepEqual(await readdir(stagingDirectory), []);
  } finally {
    await rm(stagingDirectory, { recursive: true, force: true });
  }
});

test("attachment redirect is rejected before requesting a private destination", async () => {
  const temporaryRoot = resolve(process.cwd(), "..", "..", ".codex_tmp");
  await mkdir(temporaryRoot, { recursive: true });
  const stagingDirectory = await mkdtemp(resolve(temporaryRoot, "mcp-file-redirect-"));
  const requestedUrls: string[] = [];

  try {
    await assert.rejects(
      stageChatPdf(
        {
          download_url: "https://files.openaiusercontent.com/fixture.pdf",
          file_id: "file_fixture",
          mime_type: "application/pdf",
        },
        {
          env: { SEARCH_IMPORT_INBOX: stagingDirectory },
          fetchImpl: async (input) => {
            requestedUrls.push(String(input));
            return new Response(null, {
              status: 302,
              headers: {
                location: "https://127.0.0.1/private.pdf",
              },
            });
          },
        },
      ),
      /Attachment URL is invalid/,
    );

    assert.equal(requestedUrls.length, 1);
    assert.equal(
      requestedUrls[0],
      "https://files.openaiusercontent.com/fixture.pdf",
    );
    assert.deepEqual(await readdir(stagingDirectory), []);
  } finally {
    await rm(stagingDirectory, { recursive: true, force: true });
  }
});

test("Actions authentication requires a configured 32-character secret", () => {
  const missing = authenticateActions(undefined, {});
  assert.equal(missing?.errorCode, "ACTIONS_AUTH_NOT_CONFIGURED");
  const secret = "s".repeat(40);
  assert.equal(
    authenticateActions("Bearer wrong", { SEARCH_ACTIONS_BEARER_TOKEN: secret })?.errorCode,
    "ACTIONS_AUTHENTICATION_FAILED",
  );
  assert.equal(
    authenticateActions(`Bearer ${secret}`, { SEARCH_ACTIONS_BEARER_TOKEN: secret }),
    null,
  );
});

test("Actions dispatch uses compact core calls and enforces separate confirmation", async () => {
  const backend = new MockNotebookClient();
  const library = await dispatchAction("list_library", { query: "motion", limit: 5 }, backend);
  assert.equal(library.status, "ok");
  const preview = await dispatchAction("delete_preview", { document_id: 3 }, backend);
  assert.equal(preview.safe_to_delete, true);
  await assert.rejects(
    dispatchAction("delete_document", { confirmation_token: "d".repeat(40), confirmed: false }, backend),
    /Explicit user confirmation/,
  );
  const deleted = await dispatchAction(
    "delete_document",
    { confirmation_token: "d".repeat(40), confirmed: true },
    backend,
  );
  assert.equal(deleted.cleanup_complete, true);
  await assert.rejects(
    dispatchAction("import_document", { confirmation_token: "i".repeat(40) }, backend),
    /Explicit user confirmation/,
  );
});

test("backend error codes are metadata-safe before logging", () => {
  assert.equal(errorCode(new NotebookBackendError("failure", 500, "BACKEND_TIMEOUT")), "BACKEND_TIMEOUT");
  assert.equal(
    errorCode(new NotebookBackendError("failure", 500, "private note body\nfragment_id=secret")),
    "MCP_ADAPTER_ERROR",
  );
});

test("machine configuration failures remain structured and path-free", () => {
  const result = errorToolResult(
    new NotebookBackendError("D:\\private\\model", 503, "config_missing"),
  );
  const payload = JSON.parse(result.content[0].text);
  assert.equal(payload.error_code, "config_missing");
  assert.equal(
    payload.message,
    "Search high-quality search configuration is unavailable.",
  );
  assert.doesNotMatch(JSON.stringify(result), /D:\\\\private/);
});

test("all tool failures use isError content without output-schema mismatch", async () => {
  const scenarios = [
    ["BACKEND_UNAVAILABLE", 503],
    ["model_load_failed", 503],
    ["BACKEND_TIMEOUT", 504],
    ["BACKEND_RESPONSE_INVALID", 502],
    ["index_unavailable", 503],
    ["notebook_fragment_not_found", 404],
    ["invalid_fragment_id", 400],
  ] as const;
  const toolCalls = [
    { name: "search", arguments: { query: "probe" } },
    { name: "fetch", arguments: { fragment_id: "missing" } },
    { name: "export_evidence", arguments: { fragment_ids: ["missing"], format: "markdown" } },
    { name: "list_library", arguments: {} },
    { name: "import_preview", arguments: {} },
    { name: "import_document", arguments: { confirmation_token: "i".repeat(40), confirmed: true } },
    { name: "delete_preview", arguments: { document_id: 1 } },
    { name: "delete_document", arguments: { confirmation_token: "d".repeat(40), confirmed: true } },
  ] as const;

  for (const [code, status] of scenarios) {
    class FailingClient extends MockNotebookClient {
      private fail(): never {
        throw new NotebookBackendError("private backend detail", status, code);
      }
      override async search(_input: NotebookSearchInput): Promise<never> {
        return this.fail();
      }
      override async fetchFragment(_fragmentId: string): Promise<never> {
        return this.fail();
      }
      override async exportEvidence(
        _input: { fragment_ids: string[]; format: "markdown" | "jsonl" | "json"; query?: string },
      ): Promise<never> {
        return this.fail();
      }
      override async listLibrary(_input: Parameters<MockNotebookClient["listLibrary"]>[0]): Promise<never> {
        return this.fail();
      }
      override async importPreview(_input: Parameters<MockNotebookClient["importPreview"]>[0]): Promise<never> {
        return this.fail();
      }
      override async importDocument(_input: Parameters<MockNotebookClient["importDocument"]>[0]): Promise<never> {
        return this.fail();
      }
      override async deletePreview(_documentId: number): Promise<never> {
        return this.fail();
      }
      override async deleteDocument(_input: Parameters<MockNotebookClient["deleteDocument"]>[0]): Promise<never> {
        return this.fail();
      }
    }
    const server = createNotebookMcpServer({
      client: new FailingClient(),
      widget: { html: "<html></html>" },
    });
    const client = new Client({ name: "notebook-ai-error-contract-test", version: "0.1.0" });
    const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
    await server.connect(serverTransport);
    await client.connect(clientTransport);
    try {
      for (const call of toolCalls) {
        const response = await client.callTool(call);
        assert.equal(response.isError, true);
        assert.equal(response.structuredContent, undefined);
        const content = response.content as Array<{ type: string; text?: string }>;
        assert.equal(content[0]?.type, "text");
        const payload = JSON.parse(content[0]?.text ?? "{}");
        assert.equal(payload.status, "error");
        assert.equal(payload.error_code, code);
        assert.doesNotMatch(JSON.stringify(response), /structured_content_output_schema_mismatch|-32602|private backend detail/);
      }
    } finally {
      await client.close();
      await server.close();
    }
  }
});
