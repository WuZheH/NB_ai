import assert from "node:assert/strict";
import test from "node:test";

import { NOTEBOOK_SOURCE_TYPES } from "./contracts";
import { NotebookClient } from "./notebookClient";

test("NotebookClient uses the fixed backend paths and caps search at 20", async () => {
  const requests: Array<{ url: string; init: RequestInit }> = [];
  const fetchImpl: typeof fetch = async (input, init = {}) => {
    requests.push({ url: String(input), init });
    if (String(input).includes("/fragments/")) {
      return Response.json({ fragment_id: "fragment/one" });
    }
    return Response.json({ status: "ok", results: [] });
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

test("NotebookClient makes backend-relative open targets absolute without rewriting Zotero URIs", async () => {
  const fragment = {
    fragment_id: "fragment-1",
    open_target: {
      pdf_url: "/api/v1/library/documents/7/pdf#page=12",
      zotero_url: "zotero://select/library/items/ABC123",
    },
  };
  const fetchImpl: typeof fetch = async (input) => {
    if (String(input).includes("/notebook-search")) {
      return Response.json({ status: "ok", results: [fragment] });
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

  const fetched = await client.fetchFragment("fragment-1");
  assert.equal(
    "fragment" in fetched ? fetched.fragment?.open_target?.pdf_url : null,
    "http://127.0.0.1:8123/api/v1/library/documents/7/pdf#page=12",
  );
});
