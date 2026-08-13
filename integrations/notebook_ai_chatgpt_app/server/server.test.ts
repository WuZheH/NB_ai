import assert from "node:assert/strict";
import { mkdir, mkdtemp, readdir, readFile, rm } from "node:fs/promises";
import { createServer } from "node:http";
import { resolve } from "node:path";
import test from "node:test";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";

import {
  actionsOpenApiDocument,
  authenticateActions,
  dispatchAction,
  handleActionsHttpRequest,
} from "./actions";
import { stageChatPdf } from "./fileTransfer";
import { createNotebookMcpServer } from "./app";
import type { NotebookFragment, NotebookResult, NotebookSearchInput } from "./contracts";
import { NotebookBackendError, NotebookClient } from "./notebookClient";
import { requireUnauthenticatedDevelopment } from "./security";
import { NOTEBOOK_TOOL_NAMES } from "./tools";
import { runImportDocumentTool } from "./tools/importDocument";
import { runImportPreviewTool } from "./tools/importPreview";
import { runExportEvidenceTool } from "./tools/exportEvidence";
import { errorCode, errorToolResult } from "./tools/shared";
import { RESOURCE_MIME_TYPE } from "./widgetResource";

function result(sourceType: NotebookResult["source_type"] = "pdf_chunk"): NotebookResult {
  return {
    fragment_id: "fragment-1",
    source_type: sourceType,
    selection_rank: 1,
    document_id: 1,
    document_title: "Paper",
    document_type: "pdf",
    pdf_page: 4,
    page_label: "4",
    heading: "Section",
    section: "Section",
    coherent_text: sourceType === "pdf_chunk" ? "PDF source" : null,
    selected_source_text: sourceType === "pdf_chunk" ? null : "Selected source",
    user_note: sourceType === "pdf_chunk" ? null : "My note",
    context_before: "Before",
    context_after: "After",
    tags: [],
    provenance: { source: "test", fragment_id: "fragment-1" },
    open_target: null,
  };
}

function fragment(sourceType: NotebookFragment["source_type"] = "zotero_child_note"): NotebookFragment {
  return { ...result(sourceType), selection_rank: null };
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
        source: "search_library" as const,
      }],
      truncated: false,
      scope: "imported" as const,
    };
  }

  override async integrityReport(input: { document_id: number }) {
    this.calls.push({ tool: "integrity_report", input });
    return {
      status: "ok" as const,
      read_only: true as const,
      verdict: "warn" as const,
      warnings: ["historical_events_not_recorded"],
      document_id: input.document_id,
      pdf_sha256: "a".repeat(64),
      document: { title: "Paper" },
      source: { recorded: true },
      database: {
        document_count: 1,
        chunk_count: 2,
        chapter_count: 0,
        source_binding_count: 1,
        personal_note_count: 1,
        evidence_link_count: 1,
        integrity_check: "ok",
        foreign_key_issue_count: 0,
      },
      fts: {
        status: "ready",
        ready: true,
        expected_pdf_chunk_count: 2,
        indexed_pdf_chunk_count: 2,
        missing_pdf_chunk_count: 0,
        orphan_pdf_chunk_count: 0,
        eligible_personal_note_count: 1,
        indexed_personal_note_count: 1,
        missing_personal_note_count: 0,
        orphan_personal_note_count: 0,
        excluded_personal_note_count: 0,
        exclusion_reasons: {},
        fragment_count: 3,
        source_type_counts: {
          pdf_chunk: 2,
          personal_note: 1,
        },
        reasons: [],
      },
      vectors: {
        status: "ready",
        passage_expected_count: 2,
        passage_indexed_count: 2,
        passage_missing_count: 0,
        passage_orphan_count: "not_available" as const,
        note_expected_count: 1,
        note_indexed_count: 1,
        note_missing_count: 0,
        note_orphan_count: "not_available" as const,
        reasons: ["passage_schema_document_id_unavailable"],
      },
      history: {
        confirmation_token_fingerprint: "not_recorded",
        previewed_at: "not_recorded",
        confirmed_at: "not_recorded",
        transaction_fingerprint: "not_recorded",
        source_revision_fingerprint: "not_recorded",
        lifecycle_events: "not_recorded",
        terminal_status: "not_recorded",
        terminal_stage: "not_recorded",
        journal_operation_id: "not_recorded",
        journal_revision: "not_recorded" as const,
        receipt_recorded: "not_recorded" as const,
        journal_updated_at: "not_recorded",
        journal_terminal_events: "not_recorded",
      },
      writes_performed: {
        production_db: false as const,
        fts: false as const,
        vector_store: false as const,
        zotero: false as const,
      },
    };
  }

  override async importPreview(input: { inbox_filename?: string }) {
    this.calls.push({ tool: "import_preview", input });
    return {
      status: "ok" as const,
      operation_id: "a".repeat(32),
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
      operation_id: "a".repeat(32),
      terminal: true,
      document_id: 3,
      title: "Fixture",
      document_type: "paper",
      chunk_count: 6,
      duplicate_status: "not_detected",
      error_code: null,
      already_completed: false,
      replayed_receipt: false,
      operation_in_progress: false,
      token_consumed: true,
      writes_performed: true,
      safe_to_retry: false,
    };
  }

  override async importStatus(input: { operation_id: string }) {
    this.calls.push({ tool: "import_status", input });
    return {
      status: "committed" as const,
      operation_id: input.operation_id,
      document_id: 3,
      title: "Fixture",
      document_type: "paper",
      chunk_count: 6,
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

test("tools/list exposes ten annotated tools and widget resource", async () => {
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
    assert.ok(importPreview?.outputSchema?.properties?.operation_id);
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
    const importStatus = listed.tools.find((tool) => tool.name === "import_status");
    assert.deepEqual(importStatus?.annotations, {
      readOnlyHint: true,
      destructiveHint: false,
      idempotentHint: true,
      openWorldHint: false,
    });
    assert.deepEqual(importStatus?.inputSchema?.required, ["operation_id"]);
    assert.equal(
      "confirmation_token" in (importStatus?.inputSchema?.properties ?? {}),
      false,
    );
    for (const field of [
      "operation_id",
      "document_id",
      "title",
      "document_type",
      "chunk_count",
      "terminal",
      "operation_in_progress",
      "writes_performed",
      "token_consumed",
      "safe_to_retry",
      "replayed_receipt",
      "error_code",
      "error_stage",
      "rollback_attempted",
      "rollback_completed",
    ]) {
      assert.ok(importStatus?.outputSchema?.properties?.[field], `import_status declares ${field}`);
    }
    const listLibrary = listed.tools.find((tool) => tool.name === "list_library");
    assert.deepEqual(
      listLibrary?.inputSchema?.properties?.status?.enum,
      ["active", "archived", "available", "imported", "all"],
    );
    assert.ok(listLibrary?.outputSchema?.properties?.zotero_source_revision);
    assert.ok(importPreview?.inputSchema?.properties?.zotero_source_revision);

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
    assert.equal(resourceMeta?.ui?.domain, "https://read-library-widget.openaiusercontent.com");
    assert.equal(resourceMeta?.["notebookAi/widgetDomainMode"], "configured");
  } finally {
    await client.close();
    await server.close();
  }
});

test("widget domain and CSP are fixed to the unique production contract", async () => {
  const backend = new MockNotebookClient();
  const server = createNotebookMcpServer({
    client: backend,
    widget: { html: "<html></html>" },
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
    assert.equal(meta?.ui?.domain, "https://read-library-widget.openaiusercontent.com");
    assert.equal(meta?.["openai/widgetDomain"], "https://read-library-widget.openaiusercontent.com");
    assert.equal(meta?.["notebookAi/widgetDomainMode"], "configured");
  } finally {
    await client.close();
    await server.close();
  }
});

test("all ten tools call only the backend adapter", async () => {
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
    assert.equal(modelResults[1].user_note, "My note");
    assert.equal(modelResults[1].selected_source_text, "Selected source");
    assert.equal("chunk_id" in modelResults[0], false);
    assert.equal("content_hash" in modelResults[0], false);
    assert.equal("reranker_score" in modelResults[0], false);

    const fetched = await client.callTool({ name: "fetch", arguments: { fragment_id: "fragment-1" } });
    const fetchedFragment = (fetched.structuredContent as { fragment: NotebookFragment }).fragment;
    assert.deepEqual(fetchedFragment.provenance, { source: "test", fragment_id: "fragment-1" });
    assert.equal(fetchedFragment.selection_rank, null, "fetch accepts the public unranked fragment contract");

    const exported = await client.callTool({
      name: "export_evidence",
      arguments: { fragment_ids: ["fragment-1"], format: "markdown", query: "foot skating" },
    });
    assert.equal((exported.structuredContent as { item_count: number }).item_count, 1);
    assert.equal((exported.structuredContent as { content: string }).content, "# Evidence");
    assert.equal((exported.structuredContent as { content_truncated: boolean }).content_truncated, false);
    const library = await client.callTool({ name: "list_library", arguments: { query: "motion" } });
    assert.equal((library.structuredContent as { count: number }).count, 1);
    const integrity = await client.callTool({ name: "integrity_report", arguments: { document_id: 1 } });
    assert.equal((integrity.structuredContent as { read_only: boolean }).read_only, true);
    const importPreview = await client.callTool({ name: "import_preview", arguments: { inbox_filename: "fixture.pdf" } });
    assert.equal((importPreview.structuredContent as { title: string }).title, "Fixture");
    const imported = await client.callTool({
      name: "import_document",
      arguments: { confirmation_token: "i".repeat(40), confirmed: true },
    });
    const importedPayload = imported.structuredContent as {
      operation_id: string;
      terminal: boolean;
      document_id: number;
      already_completed: boolean;
      replayed_receipt: boolean;
    };
    assert.equal(importedPayload.document_id, 3);
    assert.equal(importedPayload.operation_id, "a".repeat(32));
    assert.equal(importedPayload.terminal, true);
    assert.equal(importedPayload.already_completed, false);
    assert.equal(importedPayload.replayed_receipt, false);
    const importStatus = await client.callTool({
      name: "import_status",
      arguments: { operation_id: "a".repeat(32) },
    });
    assert.equal((importStatus.structuredContent as { status: string }).status, "committed");
    assert.deepEqual(
      JSON.parse(String(importStatus.content[0]?.text)),
      importStatus.structuredContent,
    );
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
        "integrity_report",
        "import_preview",
        "import_document",
        "import_status",
        "delete_preview",
        "delete_document",
      ],
    );
  } finally {
    await client.close();
    await server.close();
  }
});

test("export_evidence returns small content completely and paginates only above the safe limit", async () => {
  class LargeExportClient extends MockNotebookClient {
    override async exportEvidence(input: { fragment_ids: string[]; format: "markdown" | "jsonl" | "json"; query?: string }) {
      this.calls.push({ tool: "export_evidence", input });
      return { status: "ok", content: "x".repeat(40_000) };
    }
  }
  const backend = new LargeExportClient();
  const first = await runExportEvidenceTool(backend, {
    fragment_ids: ["fragment-1"],
    format: "json",
    content_limit: 32_000,
  });
  const firstPayload = first.structuredContent as {
    content: string | null;
    content_preview: string | null;
    content_truncated: boolean;
    next_content_offset: number | null;
    full_content_retrieval: { arguments: { content_offset: number } } | null;
    offset_unit: string;
    requires_concatenation: boolean;
    content_sha256: string;
    requested_offset: number;
    offset_out_of_range: boolean;
  };
  assert.equal(firstPayload.content, null);
  assert.equal(firstPayload.content_preview?.length, 32_000);
  assert.equal(firstPayload.content_truncated, true);
  assert.equal(firstPayload.next_content_offset, 32_000);
  assert.equal(firstPayload.full_content_retrieval?.arguments.content_offset, 32_000);
  assert.equal(firstPayload.offset_unit, "utf16_code_units");
  assert.equal(firstPayload.requires_concatenation, true);
  assert.match(firstPayload.content_sha256, /^[0-9a-f]{64}$/);
  assert.equal(firstPayload.requested_offset, 0);
  assert.equal(firstPayload.offset_out_of_range, false);

  const second = await runExportEvidenceTool(backend, {
    fragment_ids: ["fragment-1"],
    format: "json",
    content_offset: 32_000,
    content_limit: 32_000,
  });
  const secondPayload = second.structuredContent as {
    content: string | null;
    content_truncated: boolean;
  };
  assert.equal(secondPayload.content?.length, 8_000);
  assert.equal(secondPayload.content_truncated, false);
});

test("export pagination is Unicode-safe and reports out-of-range offsets", async () => {
  class UnicodeExportClient extends MockNotebookClient {
    override async exportEvidence() {
      return { status: "ok", content: `${"x".repeat(999)}😀z` };
    }
  }
  const backend = new UnicodeExportClient();
  const first = await runExportEvidenceTool(backend, {
    fragment_ids: ["fragment-1"],
    format: "markdown",
    content_limit: 1_000,
  });
  const firstPayload = first.structuredContent as {
    content_preview: string;
    next_content_offset: number;
  };
  assert.equal(firstPayload.content_preview.length, 999);
  assert.equal(firstPayload.next_content_offset, 999);

  const outside = await runExportEvidenceTool(backend, {
    fragment_ids: ["fragment-1"],
    format: "markdown",
    content_offset: 99_999,
    content_limit: 1_000,
  });
  const outsidePayload = outside.structuredContent as {
    content: string;
    offset_out_of_range: boolean;
    warnings: Array<{ code: string }>;
  };
  assert.equal(outsidePayload.content, "");
  assert.equal(outsidePayload.offset_out_of_range, true);
  assert.equal(outsidePayload.warnings[0]?.code, "content_offset_out_of_range");
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

test("Actions OpenAPI exposes the same ten operations with bearer authentication", () => {
  const document = actionsOpenApiDocument({
    SEARCH_ACTIONS_PUBLIC_BASE_URL: "https://search-actions.example/private",
  }) as {
    openapi: string;
    servers?: Array<{ url: string }>;
    paths: Record<string, {
      post?: {
        security?: unknown[];
        description?: string;
        requestBody?: {
          content?: Record<string, { schema?: Record<string, unknown> }>;
        };
        responses?: Record<string, {
          content?: Record<string, { schema?: { properties?: Record<string, unknown> } }>;
        }>;
      };
    }>;
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
  const writeErrorProperties = document.paths["/actions/v1/import_document"]
    .post?.responses?.["5XX"]
    ?.content?.["application/json"]
    ?.schema?.properties;
  assert.ok(writeErrorProperties?.safe_to_retry);
  assert.ok(writeErrorProperties?.operation_in_progress);
  assert.ok(writeErrorProperties?.token_consumed);
  assert.ok(writeErrorProperties?.writes_performed);
  const importStatusSchema = document.paths["/actions/v1/import_status"].post
    ?.requestBody?.content?.["application/json"]?.schema as {
      properties?: Record<string, unknown>;
      required?: string[];
    };
  assert.ok(importStatusSchema.properties?.operation_id);
  assert.deepEqual(importStatusSchema.required, ["operation_id"]);
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
    assert.equal(previewPayload?.operation_id, "a".repeat(32));
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
  const importStatus = await dispatchAction(
    "import_status",
    { operation_id: "a".repeat(32) },
    backend,
  );
  assert.equal(importStatus.status, "committed");
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
    "READ high-quality search configuration is unavailable.",
  );
  assert.doesNotMatch(JSON.stringify(result), /D:\\\\private/);
});

test("selected-book failures preserve a safe actionable stage", () => {
  const result = errorToolResult(
    new NotebookBackendError(
      "D:\\private\\book.pdf parser traceback",
      500,
      "zotero_direction_b_body_import_failed",
    ),
  );
  const payload = JSON.parse(result.content[0].text);
  assert.equal(payload.error_code, "zotero_direction_b_body_import_failed");
  assert.equal(
    payload.message,
    "Selected-book body extraction failed and the import was rolled back.",
  );
  assert.doesNotMatch(JSON.stringify(result), /D:\\\\private/);
});

test("public errors keep stable actionable messages and safety fields", () => {
  const cases = [
    ["notebook_fragment_not_found", "The requested fragment was not found."],
    ["integrity_report_document_not_found", "The requested document was not found."],
    ["evidence_fragment_not_found", "A selected evidence fragment was not found."],
    [
      "attachment_not_owned_by_item",
      "The selected PDF attachment does not belong to the selected Zotero item.",
    ],
    ["import_inbox_unavailable", "The local PDF import inbox is unavailable."],
  ] as const;
  for (const [code, message] of cases) {
    const result = errorToolResult(
      new NotebookBackendError("private backend detail", 404, code),
      { tool: "fetch" },
    );
    const payload = JSON.parse(result.content[0].text);
    assert.equal(payload.tool, "fetch");
    assert.equal(payload.error_code, code);
    assert.equal(payload.message, message);
    assert.equal(payload.retryable, false);
    assert.equal(payload.writes_performed, false);
    assert.doesNotMatch(JSON.stringify(payload), /private backend detail/);
  }
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
    { name: "integrity_report", arguments: { document_id: 1 } },
    { name: "import_preview", arguments: {} },
    { name: "import_document", arguments: { confirmation_token: "i".repeat(40), confirmed: true } },
    { name: "import_status", arguments: { operation_id: "a".repeat(32) } },
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
      override async integrityReport(_input: Parameters<MockNotebookClient["integrityReport"]>[0]): Promise<never> {
        return this.fail();
      }
      override async importPreview(_input: Parameters<MockNotebookClient["importPreview"]>[0]): Promise<never> {
        return this.fail();
      }
      override async importDocument(_input: Parameters<MockNotebookClient["importDocument"]>[0]): Promise<never> {
        return this.fail();
      }
      override async importStatus(_input: Parameters<MockNotebookClient["importStatus"]>[0]): Promise<never> {
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
        if (call.name === "import_document") {
          assert.ok(response.structuredContent);
        } else {
          assert.equal(response.structuredContent, undefined);
        }
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

test("write transport uncertainty is non-retryable and does not invent operation state", () => {
  for (const [code, status] of [
    ["BACKEND_TIMEOUT", 504],
    ["BACKEND_UNAVAILABLE", 503],
  ] as const) {
    const result = errorToolResult(
      new NotebookBackendError("private transport detail", status, code, {
        retryable: true,
        safe_to_retry: true,
        operation_in_progress: false,
        token_consumed: true,
        writes_performed: true,
        replayed_receipt: true,
      }),
      {
        tool: "import_document",
        writeOperation: true,
        includeStructuredContent: true,
      },
    );
    const payload = JSON.parse(result.content[0].text);
    assert.equal(payload.error_code, code);
    assert.equal(payload.retryable, false);
    assert.equal(payload.safe_to_retry, false);
    assert.equal(payload.operation_in_progress, null);
    assert.equal(payload.token_consumed, null);
    assert.equal(payload.writes_performed, null);
    assert.equal(payload.replayed_receipt, null);
    assert.match(payload.message, /final state was known/);
    assert.match(payload.message, /Do not retry automatically/);
    assert.doesNotMatch(JSON.stringify(result), /private transport detail/);
  }
});

test("read-only backend timeouts remain retryable", () => {
  const result = errorToolResult(
    new NotebookBackendError("private transport detail", 504, "BACKEND_TIMEOUT"),
    { tool: "search" },
  );
  const payload = JSON.parse(result.content[0].text);
  assert.equal(payload.retryable, true);
  assert.equal(payload.writes_performed, false);
  assert.equal(payload.message, "READ backend request timed out.");
});

async function callImportDocumentAction(
  client: NotebookClient,
): Promise<{ status: number; payload: Record<string, unknown> }> {
  const secret = "a".repeat(40);
  const server = createServer((request, response) => {
    void handleActionsHttpRequest(request, response, {
      env: { SEARCH_ACTIONS_BEARER_TOKEN: secret },
      client,
    });
  });
  await new Promise<void>((resolveListen, rejectListen) => {
    server.once("error", rejectListen);
    server.listen(0, "127.0.0.1", () => {
      server.off("error", rejectListen);
      resolveListen();
    });
  });
  try {
    const address = server.address();
    assert.ok(address && typeof address === "object");
    const response = await fetch(
      `http://127.0.0.1:${address.port}/actions/v1/import_document`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${secret}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          confirmation_token: "i".repeat(40),
          confirmed: true,
        }),
      },
    );
    return {
      status: response.status,
      payload: await response.json() as Record<string, unknown>,
    };
  } finally {
    await new Promise<void>((resolveClose) => server.close(() => resolveClose()));
  }
}

test("Actions import timeout preserves the write uncertainty whitelist", async () => {
  class TimeoutImportClient extends MockNotebookClient {
    override async importDocument(
      _input: Parameters<MockNotebookClient["importDocument"]>[0],
    ): Promise<never> {
      throw new NotebookBackendError(
        "private transport detail",
        504,
        "BACKEND_TIMEOUT",
      );
    }
  }

  const { status, payload } = await callImportDocumentAction(new TimeoutImportClient());
  assert.equal(status, 504);
  assert.equal(payload.status, "error");
  assert.equal(payload.error_code, "BACKEND_TIMEOUT");
  assert.equal(payload.retryable, false);
  assert.equal(payload.safe_to_retry, false);
  assert.equal(payload.operation_in_progress, null);
  assert.equal(payload.token_consumed, null);
  assert.equal(payload.writes_performed, null);
  assert.equal(payload.replayed_receipt, null);
  assert.match(String(payload.message), /Do not retry automatically/);
  assert.doesNotMatch(JSON.stringify(payload), /private transport detail/);
});


// ============================================================================
// P0-FIX1-CLOSURE1: MCP final-response contract tests
// ============================================================================

const PUBLISH_FAILURE_DETAILS: Record<string, unknown> = {
  status: "error",
  operation_id: "b".repeat(32),
  terminal: true,
  error_code: "zotero_direction_b_production_index_publish_failed",
  message: "Direction-B derived index publish failed.",
  error_stage: "publish_started",
  publish_substage: "vector_store_retire",
  cause_type: "PermissionError",
  cause_message: "[WinError 32] The process cannot access the file",
  cause_errno: 13,
  cause_winerror: 32,
  cause_filename: "C:\\staging\\retrieval_fts_v1.db",
  cause_filename2: "C:\\production\\retrieval_fts_v1.db",
  rollback_attempted: true,
  rollback_completed: true,
  writes_performed: true,
  token_consumed: true,
  safe_to_retry: false,
  replayed_receipt: false,
  operation_in_progress: false,
};

test("Actions import errors propagate only the registered write-safety whitelist", async () => {
  class FailedImportClient extends MockNotebookClient {
    override async importDocument(
      _input: Parameters<MockNotebookClient["importDocument"]>[0],
    ): Promise<never> {
      throw new NotebookBackendError(
        "private backend exception",
        500,
        "zotero_direction_b_production_index_publish_failed",
        { ...PUBLISH_FAILURE_DETAILS, private_internal_field: "must-not-escape" },
      );
    }
  }

  const { status, payload } = await callImportDocumentAction(new FailedImportClient());
  assert.equal(status, 500);
  for (const key of [
    "operation_id",
    "terminal",
    "token_consumed",
    "writes_performed",
    "safe_to_retry",
    "replayed_receipt",
    "operation_in_progress",
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
  ]) {
    assert.deepEqual(payload[key], PUBLISH_FAILURE_DETAILS[key], key);
  }
  assert.equal(payload.private_internal_field, undefined);
  assert.doesNotMatch(JSON.stringify(payload), /private backend exception|must-not-escape/);
});

test("errorToolResult propagates token_consumed from backend details", () => {
  const err = new NotebookBackendError(
    "import failed",
    500,
    "zotero_direction_b_production_index_publish_failed",
    {
      token_consumed: true,
      writes_performed: true,
      safe_to_retry: false,
    },
  );
  const result = errorToolResult(err, {
    tool: "import_document",
    writeOperation: true,
  });
  const payload = JSON.parse(result.content[0].text);
  assert.equal(payload.token_consumed, true);
  assert.equal(payload.writes_performed, true);
  assert.equal(payload.safe_to_retry, false);
});

test("errorToolResult preserves publish_substage and cause fields", () => {
  const err = new NotebookBackendError(
    "import failed",
    500,
    "zotero_direction_b_production_index_publish_failed",
    PUBLISH_FAILURE_DETAILS,
  );
  const result = errorToolResult(err, {
    tool: "import_document",
    writeOperation: true,
  });
  const payload = JSON.parse(result.content[0].text);
  assert.equal(payload.publish_substage, "vector_store_retire");
  assert.equal(payload.cause_type, "PermissionError");
  assert.equal(payload.cause_errno, 13);
  assert.equal(payload.cause_winerror, 32);
  assert.equal(payload.cause_filename, "C:\\staging\\retrieval_fts_v1.db");
  assert.equal(payload.cause_filename2, "C:\\production\\retrieval_fts_v1.db");
  assert.equal(payload.rollback_attempted, true);
  assert.equal(payload.rollback_completed, true);
  assert.equal(payload.error_stage, "publish_started");
});

test("errorToolResult backend null fields stay null, not coerced to true", () => {
  const err = new NotebookBackendError(
    "partial failure",
    500,
    "zotero_direction_b_production_index_publish_failed",
    {
      token_consumed: null,
      writes_performed: true,
      publish_substage: null,
      cause_errno: null,
    },
  );
  const result = errorToolResult(err, {
    tool: "import_document",
    writeOperation: true,
  });
  const payload = JSON.parse(result.content[0].text);
  assert.equal(payload.token_consumed, null);
  assert.equal(payload.writes_performed, true);
  assert.equal(payload.publish_substage, null);
  assert.equal(payload.cause_errno, null);
});

test("errorToolResult defaults token_consumed to null without backend details", () => {
  // Old-style NotebookBackendError without details (backward compat)
  const err = new NotebookBackendError(
    "generic backend error",
    500,
    "BACKEND_UNAVAILABLE",
  );
  const result = errorToolResult(err, {
    tool: "import_document",
    writeOperation: true,
  });
  const payload = JSON.parse(result.content[0].text);
  assert.equal(payload.token_consumed, null);
  assert.equal(payload.writes_performed, null);
  assert.equal(payload.publish_substage, null);
});

test("errorToolResult non-write operations omit token_consumed", () => {
  const err = new NotebookBackendError("search error", 503, "index_unavailable");
  const result = errorToolResult(err, { tool: "search" });
  const payload = JSON.parse(result.content[0].text);
  assert.equal(payload.token_consumed, undefined);
  assert.equal(payload.writes_performed, false);
});

test("errorToolResult replayed_receipt propagated from backend", () => {
  const err = new NotebookBackendError(
    "replay",
    500,
    "zotero_direction_b_production_index_publish_failed",
    {
      token_consumed: true,
      writes_performed: true,
      replayed_receipt: true,
      rollback_attempted: true,
      rollback_completed: true,
    },
  );
  const result = errorToolResult(err, {
    tool: "import_document",
    writeOperation: true,
  });
  const payload = JSON.parse(result.content[0].text);
  assert.equal(payload.replayed_receipt, true);
  assert.equal(payload.token_consumed, true);
});

test("errorToolResult non-NotebookBackendError uses safe defaults", () => {
  const err = new TypeError("network error");
  const result = errorToolResult(err, {
    tool: "import_document",
    writeOperation: true,
  });
  const payload = JSON.parse(result.content[0].text);
  assert.equal(payload.token_consumed, null);
  assert.equal(payload.writes_performed, null);
  assert.equal(payload.error_code, "BACKEND_RESPONSE_INVALID");
  assert.equal(payload.publish_substage, null);
});

test("registered import_document returns the complete structured failure contract", async () => {
  class FailingImportClient extends MockNotebookClient {
    override async importDocument(
      _input: Parameters<MockNotebookClient["importDocument"]>[0],
    ): Promise<never> {
      throw new NotebookBackendError(
        "private backend failure",
        500,
        "zotero_direction_b_production_index_publish_failed",
        PUBLISH_FAILURE_DETAILS,
      );
    }
  }

  const server = createNotebookMcpServer({
    client: new FailingImportClient(),
    widget: { html: "<html></html>" },
  });
  const client = new Client({ name: "import-contract-test", version: "0.1.0" });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  await server.connect(serverTransport);
  await client.connect(clientTransport);
  try {
    const response = await client.callTool({
      name: "import_document",
      arguments: { confirmation_token: "i".repeat(40), confirmed: true },
    });
    assert.equal(response.isError, true);
    assert.ok(response.structuredContent);
    const structured = response.structuredContent as Record<string, unknown>;
    const content = response.content as Array<{ type: string; text?: string }>;
    const parsed = JSON.parse(content[0]?.text ?? "{}");
    assert.deepEqual(parsed, structured);
    assert.equal(structured.operation_id, "b".repeat(32));
    assert.equal(structured.terminal, true);
    assert.equal(structured.token_consumed, true);
    assert.equal(structured.writes_performed, true);
    assert.equal(structured.safe_to_retry, false);
    assert.equal(structured.publish_substage, "vector_store_retire");
    assert.equal(structured.cause_type, "PermissionError");
    assert.equal(structured.cause_errno, 13);
    assert.equal(structured.cause_winerror, 32);
    assert.equal(structured.cause_filename, "C:\\staging\\retrieval_fts_v1.db");
    assert.equal(structured.cause_filename2, "C:\\production\\retrieval_fts_v1.db");
    assert.equal(structured.rollback_attempted, true);
    assert.equal(structured.rollback_completed, true);
    assert.equal(structured.replayed_receipt, false);
    assert.equal(structured.operation_in_progress, false);
  } finally {
    await client.close();
    await server.close();
  }
});

test("import_document error contract preserves explicit null fields", () => {
  const result = errorToolResult(
    new NotebookBackendError("failure", 500, "import_document_failed", {
      token_consumed: null,
      writes_performed: null,
      safe_to_retry: null,
      replayed_receipt: null,
      operation_in_progress: null,
      publish_substage: null,
      cause_type: null,
      cause_message: null,
      cause_errno: null,
      cause_winerror: null,
      cause_filename: null,
      cause_filename2: null,
      rollback_attempted: null,
      rollback_completed: null,
      error_stage: null,
    }),
    { tool: "import_document", writeOperation: true },
  );
  const payload = JSON.parse(result.content[0].text);
  for (const key of [
    "token_consumed",
    "writes_performed",
    "safe_to_retry",
    "replayed_receipt",
    "operation_in_progress",
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
  ]) {
    assert.equal(payload[key], null, `${key} preserves backend null`);
  }
});

test("import_document final cause_message redacts token and bearer secrets", () => {
  const secret = "AbCdEf.gh_IJ~kl+MN/op=QR-stuvwxyz123456";
  const result = errorToolResult(
    new NotebookBackendError("failure", 500, "import_document_failed", {
      cause_message: `confirmation_token=TOP_SECRET Bearer ${secret}`,
    }),
    { tool: "import_document", writeOperation: true },
  );
  const raw = result.content[0].text;
  assert.doesNotMatch(raw, /TOP_SECRET/);
  assert.equal(raw.includes(secret), false);
  assert.match(raw, /\[REDACTED\]/);
});
