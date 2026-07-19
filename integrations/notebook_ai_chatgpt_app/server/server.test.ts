import assert from "node:assert/strict";
import test from "node:test";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";

import { createNotebookMcpServer } from "./app";
import type { NotebookFragment, NotebookResult, NotebookSearchInput } from "./contracts";
import { NotebookBackendError, NotebookClient } from "./notebookClient";
import { requireUnauthenticatedDevelopment } from "./security";
import { NOTEBOOK_TOOL_NAMES } from "./tools";
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
}

test("tools/list exposes the three read-only tools and widget resource", async () => {
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
      assert.deepEqual(tool.annotations, {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      });
      assert.equal(tool.outputSchema?.type, "object", `${tool.name} declares an object outputSchema`);
      assert.ok(tool.outputSchema?.properties?.status, `${tool.name} outputSchema declares status`);
      const meta = tool._meta as { ui?: { resourceUri?: string; visibility?: string[] } } | undefined;
      assert.deepEqual(meta?.ui?.visibility, ["model", "app"]);
      assert.equal(
        meta?.ui?.resourceUri,
        tool.name === "search" ? "ui://notebook-ai/research-search-v1.html" : undefined,
        "only search mounts the results widget",
      );
    }

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

test("search, fetch, and export_evidence call only the backend adapter", async () => {
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
    assert.deepEqual(backend.calls.map((call) => call.tool), ["search", "fetch", "export_evidence"]);
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
  assert.equal(result.structuredContent.error_code, "config_missing");
  assert.equal(
    result.structuredContent.message,
    "Search high-quality search configuration is unavailable.",
  );
  assert.doesNotMatch(JSON.stringify(result), /D:\\\\private/);
});
