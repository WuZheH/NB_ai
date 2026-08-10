import assert from "node:assert/strict";
import test from "node:test";

import { actionsOpenApiDocument, dispatchAction } from "./actions";
import { NotebookClient } from "./notebookClient";

test("Actions forwards Zotero import_preview and keeps exactly ten actions", async () => {
  const calls: unknown[] = [];
  const client = {
    importPreview: async (input: unknown) => {
      calls.push(input);
      return { status: "ok" };
    },
  } as NotebookClient;
  await dispatchAction(
    "import_preview",
    {
      source_type: "zotero_selected_book",
      zotero_item_key: "ABCD1234",
      zotero_attachment_key: "EFGH5678",
    },
    client,
  );
  assert.deepEqual(calls, [{
    source_type: "zotero_selected_book",
    inbox_filename: undefined,
    zotero_item_key: "ABCD1234",
    zotero_attachment_key: "EFGH5678",
  }]);

  const document = actionsOpenApiDocument() as {
    paths: Record<string, {
      post: { requestBody: { content: { "application/json": { schema: Record<string, unknown> } } } };
    }>;
  };
  assert.equal(Object.keys(document.paths).length, 10);
  const schema = document.paths["/actions/v1/import_preview"].post
    .requestBody.content["application/json"].schema;
  assert.deepEqual(
    Object.keys(schema.properties as Record<string, unknown>).sort(),
    ["inbox_filename", "source_type", "zotero_attachment_key", "zotero_item_key"],
  );
  assert.equal(Array.isArray(schema.oneOf), true);
});

test("Actions rejects mixed local and Zotero import_preview inputs", async () => {
  const client = {
    importPreview: async () => {
      throw new Error("backend must not be called");
    },
  } as NotebookClient;
  await assert.rejects(
    dispatchAction(
      "import_preview",
      {
        source_type: "local_pdf",
        inbox_filename: "fixture.pdf",
        zotero_item_key: "ABCD1234",
      },
      client,
    ),
    /local_pdf does not accept Zotero keys/,
  );
});
