import assert from "node:assert/strict";
import test from "node:test";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";

import { actionsOpenApiDocument } from "./actions";
import { createNotebookMcpServer } from "./app";
import type { NotebookResult, NotebookSearchInput } from "./contracts";
import { NotebookBackendError, type NotebookClient } from "./notebookClient";
import { READ_PRODUCT_DESCRIPTION } from "./productIdentity";

async function connectedClient(backend: NotebookClient) {
  const server = createNotebookMcpServer({
    client: backend,
    widget: { html: "<!doctype html><title>READ</title>" },
  });
  const client = new Client({ name: "read-productization-test", version: "0.1.0" });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  await server.connect(serverTransport);
  await client.connect(clientTransport);
  return { client, server };
}

function evidence(
  sourceType: NotebookResult["source_type"],
  index: number,
): NotebookResult {
  const isPdf = sourceType === "pdf_chunk";
  const isAnnotation = sourceType === "zotero_annotation_comment";
  return {
    fragment_id: `fragment-${index}`,
    source_type: sourceType,
    selection_rank: index,
    document_id: 7,
    document_title: "ACTOR",
    document_type: "paper",
    pdf_page: isPdf || isAnnotation ? 6 : null,
    page_label: isPdf || isAnnotation ? "6" : null,
    heading: "Latent variables",
    section: "Method",
    coherent_text: isPdf ? "The model uses a variational latent variable." : null,
    selected_source_text: isAnnotation ? "Coordinates are viewpoint-sensitive." : null,
    user_note: isAnnotation
      ? "This explains why global coordinates are brittle."
      : sourceType === "zotero_child_note"
        ? "Compare interpolation with random sampling."
        : null,
    context_before: null,
    context_after: null,
    tags: [],
    provenance: { source: "isolated-fixture" },
    open_target: null,
  };
}

function selectedBookPreview() {
  return {
    status: "ok" as const,
    operation_id: "a".repeat(32),
    source_type: "zotero_selected_book" as const,
    filename: "book.pdf",
    title: "Algorithms",
    item_type: "book",
    parent_key: "ITEM1234",
    zotero_item_key: "ITEM1234",
    zotero_attachment_key: "PDF12345",
    pdf_sha256: "b".repeat(64),
    duplicate_status: "not_detected",
    existing_document_id: null,
    estimated_pages: 900,
    estimated_chunks: 2100,
    extraction_ready: true,
    blockers: [],
    document_type: "book",
    warnings: [],
    confirmation_token: "c".repeat(40),
    confirmation_expires_in_seconds: 600,
    attachment_choices: [],
    annotation_count: 5,
    annotation_comment_count: 3,
    child_note_count: 2,
  };
}

test("formal MCP and widget metadata expose READ with workflow instructions", async () => {
  const { client, server } = await connectedClient({} as NotebookClient);
  try {
    assert.equal(client.getServerVersion()?.name, "READ");
    const instructions = client.getInstructions() ?? "";
    for (const required of [
      READ_PRODUCT_DESCRIPTION,
      "use search before answering",
      "explicitly confirms",
      "never retry it automatically",
      "selected_source_text",
      "user_note",
    ]) {
      assert.match(instructions, new RegExp(required.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i"));
    }
    const resources = await client.listResources();
    assert.equal(resources.resources[0]?.name, "read-research-evidence-widget");
    assert.equal(resources.resources[0]?.title, "READ");
    const listed = await client.listTools();
    assert.equal(listed.tools.length, 10);
    const publicMetadata = JSON.stringify({
      server: client.getServerVersion(),
      instructions,
      resources: resources.resources,
      tools: listed.tools.map(({ name, title, description }) => ({ name, title, description })),
      actions: actionsOpenApiDocument(),
    });
    assert.doesNotMatch(publicMetadata, /\bCread\b|Cread Secure|翻书/i);
    assert.match(publicMetadata, /READ/);
  } finally {
    await client.close();
    await server.close();
  }
});

test("READ search returns PDF, annotation comment, and child-note evidence without fabrication", async () => {
  let searchCalls = 0;
  const backend = {
    search: async (input: NotebookSearchInput) => {
      searchCalls += 1;
      const results = input.query === "no evidence"
        ? []
        : [
            evidence("pdf_chunk", 1),
            evidence("zotero_annotation_comment", 2),
            evidence("zotero_child_note", 3),
          ];
      return {
        status: "ok",
        query: input.query,
        mode: "high_quality_notebook_search_v1",
        embedding_model: "fixture-embedding",
        reranker_model: "fixture-reranker",
        backend: "isolated-fixture",
        result_count: results.length,
        results,
        warnings: [],
        latency: { total_ms: 1 },
      };
    },
  } as NotebookClient;
  const { client, server } = await connectedClient(backend);
  try {
    const mixed = await client.callTool({
      name: "search",
      arguments: { query: "Why are global coordinates sensitive?" },
    });
    const results = (mixed.structuredContent as { results: NotebookResult[] }).results;
    assert.deepEqual(results.map((item) => item.source_type), [
      "pdf_chunk",
      "zotero_annotation_comment",
      "zotero_child_note",
    ]);
    assert.equal(results[1].selected_source_text, "Coordinates are viewpoint-sensitive.");
    assert.equal(results[1].user_note, "This explains why global coordinates are brittle.");
    assert.equal(results[2].user_note, "Compare interpolation with random sampling.");

    const empty = await client.callTool({ name: "search", arguments: { query: "no evidence" } });
    assert.equal((empty.structuredContent as { result_count: number }).result_count, 0);
    assert.deepEqual((empty.structuredContent as { results: unknown[] }).results, []);
    assert.equal(searchCalls, 2);
  } finally {
    await client.close();
    await server.close();
  }
});

test("READ selected-book preview stays read-only and explicit confirmation permits one import", async () => {
  let previewCalls = 0;
  let importCalls = 0;
  const backend = {
    importPreview: async () => {
      previewCalls += 1;
      return selectedBookPreview();
    },
    importDocument: async () => {
      importCalls += 1;
      return {
        status: "committed",
        operation_id: "a".repeat(32),
        terminal: true,
        document_id: 8,
        title: "Algorithms",
        document_type: "book",
        chunk_count: 2050,
        token_consumed: true,
        writes_performed: true,
        safe_to_retry: false,
      };
    },
  } as NotebookClient;
  const { client, server } = await connectedClient(backend);
  try {
    const preview = await client.callTool({
      name: "import_preview",
      arguments: {
        source_type: "zotero_selected_book",
        zotero_item_key: "ITEM1234",
        zotero_attachment_key: "PDF12345",
      },
    });
    const value = preview.structuredContent as ReturnType<typeof selectedBookPreview>;
    assert.equal(value.title, "Algorithms");
    assert.equal(value.estimated_pages, 900);
    assert.equal(value.estimated_chunks, 2100);
    assert.equal(value.annotation_count, 5);
    assert.equal(value.annotation_comment_count, 3);
    assert.equal(value.child_note_count, 2);
    assert.equal(value.confirmation_expires_in_seconds, 600);
    assert.equal(previewCalls, 1);
    assert.equal(importCalls, 0);

    const unconfirmed = await client.callTool({
      name: "import_document",
      arguments: { confirmation_token: "c".repeat(40), confirmed: false },
    });
    assert.equal(unconfirmed.isError, true);
    assert.equal(importCalls, 0);

    const committed = await client.callTool({
      name: "import_document",
      arguments: { confirmation_token: "c".repeat(40), confirmed: true },
    });
    assert.equal((committed.structuredContent as { status: string }).status, "committed");
    assert.equal(importCalls, 1);
  } finally {
    await client.close();
    await server.close();
  }
});

test("unknown import state uses the preview operation_id for status and never retries", async () => {
  let importCalls = 0;
  let statusCalls = 0;
  const operationId = "d".repeat(32);
  const backend = {
    importPreview: async () => ({ ...selectedBookPreview(), operation_id: operationId }),
    importDocument: async () => {
      importCalls += 1;
      throw new NotebookBackendError(
        "READ backend request timed out.",
        504,
        "BACKEND_TIMEOUT",
      );
    },
    importStatus: async (input: { operation_id: string }) => {
      statusCalls += 1;
      assert.equal(input.operation_id, operationId);
      return {
        status: "committed" as const,
        operation_id: operationId,
        document_id: 8,
        title: "Algorithms",
        document_type: "book",
        chunk_count: 2050,
        terminal: true,
        operation_in_progress: false,
        writes_performed: true,
        token_consumed: true,
        safe_to_retry: false,
        replayed_receipt: false,
        error_code: null,
        error_stage: null,
        rollback_attempted: false,
        rollback_completed: false,
      };
    },
  } as NotebookClient;
  const { client, server } = await connectedClient(backend);
  try {
    const preview = await client.callTool({
      name: "import_preview",
      arguments: { source_type: "zotero_selected_book", zotero_item_key: "ITEM1234" },
    });
    assert.equal((preview.structuredContent as { operation_id: string }).operation_id, operationId);

    const uncertain = await client.callTool({
      name: "import_document",
      arguments: { confirmation_token: "c".repeat(40), confirmed: true },
    });
    assert.equal(uncertain.isError, true);
    assert.equal(importCalls, 1);

    const status = await client.callTool({
      name: "import_status",
      arguments: { operation_id: operationId },
    });
    assert.equal((status.structuredContent as { status: string }).status, "committed");
    assert.equal(importCalls, 1);
    assert.equal(statusCalls, 1);
  } finally {
    await client.close();
    await server.close();
  }
});
