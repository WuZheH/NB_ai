import assert from "node:assert/strict";
import test from "node:test";

import { NotebookClient } from "../notebookClient";
import { runImportPreviewTool } from "./importPreview";

function zoteroResponse() {
  return {
    status: "ok" as const,
    operation_id: "a".repeat(32),
    source_type: "zotero_selected_book" as const,
    filename: "book.pdf",
    title: "Selected Zotero Book",
    item_type: "book",
    pdf_sha256: "a".repeat(64),
    duplicate_status: "not_detected",
    existing_document_id: null,
    estimated_pages: 12,
    estimated_chunks: 36,
    document_type: "book",
    warnings: [],
    confirmation_token: "c".repeat(40),
    confirmation_expires_in_seconds: 600,
    attachment_choices: [],
    annotation_count: 4,
    annotation_comment_count: 2,
    child_note_count: 2,
    zotero_source_revision: "b".repeat(64),
  };
}

test("import_preview accepts legacy local PDF and Zotero key inputs", async () => {
  const calls: unknown[] = [];
  const client = {
    importPreview: async (input: unknown) => {
      calls.push(input);
      return zoteroResponse();
    },
  } as NotebookClient;

  const local = await runImportPreviewTool(client, { inbox_filename: "fixture.pdf" });
  assert.equal(local.structuredContent?.source_type, "zotero_selected_book");
  assert.equal(local.structuredContent?.item_type, "book");
  await runImportPreviewTool(client, {
    source_type: "zotero_selected_book",
    zotero_item_key: "ABCD1234",
  });
  await runImportPreviewTool(client, {
    source_type: "zotero_selected_book",
    zotero_item_key: "ABCD1234",
    zotero_attachment_key: "EFGH5678",
  });
  assert.deepEqual(calls[1], {
    source_type: "zotero_selected_book",
    inbox_filename: undefined,
    zotero_item_key: "ABCD1234",
    zotero_attachment_key: undefined,
    zotero_source_revision: undefined,
  });
  assert.deepEqual(calls[2], {
    source_type: "zotero_selected_book",
    inbox_filename: undefined,
    zotero_item_key: "ABCD1234",
    zotero_attachment_key: "EFGH5678",
    zotero_source_revision: undefined,
  });

  await runImportPreviewTool(client, {
    source_type: "zotero_selected_book",
    zotero_item_key: "ABCD1234",
    zotero_attachment_key: "EFGH5678",
    zotero_source_revision: "b".repeat(64),
  });
  assert.deepEqual(calls[3], {
    source_type: "zotero_selected_book",
    inbox_filename: undefined,
    zotero_item_key: "ABCD1234",
    zotero_attachment_key: "EFGH5678",
    zotero_source_revision: "b".repeat(64),
  });
});

test("import_preview rejects mixed sources and never stages a Zotero ChatGPT file", async () => {
  let backendCalls = 0;
  let fileFetchCalls = 0;
  const client = {
    importPreview: async () => {
      backendCalls += 1;
      return zoteroResponse();
    },
  } as NotebookClient;
  const mixed = await runImportPreviewTool(client, {
    source_type: "local_pdf",
    inbox_filename: "fixture.pdf",
    zotero_item_key: "ABCD1234",
  });
  assert.equal("isError" in mixed && mixed.isError, true);
  const file = await runImportPreviewTool(
    client,
    {
      source_type: "zotero_selected_book",
      zotero_item_key: "ABCD1234",
      file: {
        download_url: "https://example.test/book.pdf",
        file_id: "file-1",
      },
    },
    {
      fetchImpl: async () => {
        fileFetchCalls += 1;
        throw new Error("stageChatPdf must not run");
      },
    },
  );
  assert.equal("isError" in file && file.isError, true);
  assert.equal(backendCalls, 0);
  assert.equal(fileFetchCalls, 0);
});

test("NotebookClient forwards all Zotero import preview fields", async () => {
  let requestBody: Record<string, unknown> | null = null;
  const client = new NotebookClient({
    baseUrl: "http://127.0.0.1:8000",
    bearerToken: "t".repeat(32),
    fetchImpl: async (_input, init = {}) => {
      requestBody = JSON.parse(String(init.body));
      return Response.json(zoteroResponse());
    },
  });
  const response = await client.importPreview({
    source_type: "zotero_selected_book",
    zotero_item_key: "ABCD1234",
    zotero_attachment_key: "EFGH5678",
    zotero_source_revision: "b".repeat(64),
  });
  assert.deepEqual(requestBody, {
    source_type: "zotero_selected_book",
    zotero_item_key: "ABCD1234",
    zotero_attachment_key: "EFGH5678",
    zotero_source_revision: "b".repeat(64),
  });
  assert.equal(response.operation_id, "a".repeat(32));
});

test("NotebookClient rejects import preview without a canonical operation identity", async () => {
  for (const operationId of ["../journal", null]) {
    const client = new NotebookClient({
      baseUrl: "http://127.0.0.1:8000",
      fetchImpl: async () => Response.json({
        ...zoteroResponse(),
        operation_id: operationId,
      }),
    });
    await assert.rejects(
      client.importPreview({
        source_type: "zotero_selected_book",
        zotero_item_key: "ABCD1234",
      }),
      /invalid response/i,
    );
  }
});

test("NotebookClient accepts a non-importable preview with no token or operation identity", async () => {
  const client = new NotebookClient({
    baseUrl: "http://127.0.0.1:8000",
    fetchImpl: async () => Response.json({
      ...zoteroResponse(),
      duplicate_status: "duplicate",
      confirmation_token: null,
      operation_id: null,
    }),
  });
  const result = await client.importPreview({
    source_type: "zotero_selected_book",
    zotero_item_key: "ABCD1234",
  });
  assert.equal(result.confirmation_token, null);
  assert.equal(result.operation_id, null);
});
