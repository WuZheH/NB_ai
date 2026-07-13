import assert from "node:assert/strict";
import test from "node:test";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";

import { createNotebookMcpServer } from "./app";
import type { NotebookResult, NotebookSearchInput } from "./contracts";
import { NotebookClient } from "./notebookClient";
import { requireUnauthenticatedDevelopment } from "./security";
import { NOTEBOOK_TOOL_NAMES } from "./tools";
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
    return { status: "ok", fragment: result("zotero_child_note") };
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
    }

    const resource = await client.readResource({ uri: "ui://notebook-ai/research-search-v1.html" });
    assert.equal(resource.contents[0]?.mimeType, RESOURCE_MIME_TYPE);
    assert.equal(resource.contents[0]?.mimeType, "text/html;profile=mcp-app");
    const resourceMeta = resource.contents[0]?._meta as
      | { ui?: { permissions?: { clipboardWrite?: Record<string, never> } } }
      | undefined;
    assert.deepEqual(resourceMeta?.ui?.permissions, { clipboardWrite: {} });
  } finally {
    await client.close();
    await server.close();
  }
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
    const fragment = (fetched.structuredContent as { fragment: NotebookResult }).fragment;
    assert.deepEqual(fragment.provenance, [{ source: "test" }]);

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
  assert.deepEqual(requireUnauthenticatedDevelopment({ NOTEBOOK_AI_ALLOW_UNAUTHENTICATED_MCP_DEV: "1" }), {
    host: "127.0.0.1",
    port: 8787,
    unauthenticatedDevelopment: true,
  });
});
