import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const endpoint = new URL(process.argv[2] ?? "http://127.0.0.1:8787/mcp");
const query = process.argv[3] ?? "避免脚步滑动";
const sourceTypes = (
  process.env.SEARCH_SMOKE_SOURCE_TYPES
  ?? process.env.NOTEBOOK_AI_SMOKE_SOURCE_TYPES
  ?? "zotero_inspiration_note"
)
  .split(",")
  .map((value) => value.trim())
  .filter(Boolean);

const client = new Client({ name: "notebook-ai-local-smoke", version: "0.1.0" });
const transport = new StreamableHTTPClientTransport(endpoint);

try {
  await client.connect(transport);
  const listed = await client.listTools();
  const names = listed.tools.map((tool) => tool.name).sort();
  if (names.join(",") !== "delete_document,delete_preview,export_evidence,fetch,import_document,import_preview,integrity_report,list_library,search") {
    throw new Error(`Unexpected MCP tools: ${names.join(", ")}`);
  }

  const searched = await client.callTool({
    name: "search",
    arguments: {
      query,
      limit: 3,
      source_types: sourceTypes,
      document_ids: [],
      include_context: false,
    },
  });
  if (searched.isError) throw new Error("search returned an MCP error");
  const searchContent = searched.structuredContent ?? {};
  const results = Array.isArray(searchContent.results) ? searchContent.results : [];
  const first = results[0];
  if (!first?.fragment_id) throw new Error("search returned no fragment_id");

  const fetched = await client.callTool({
    name: "fetch",
    arguments: { fragment_id: first.fragment_id },
  });
  if (fetched.isError || !fetched.structuredContent?.fragment?.provenance) {
    throw new Error("fetch did not return complete provenance");
  }

  const exported = await client.callTool({
    name: "export_evidence",
    arguments: {
      fragment_ids: [first.fragment_id],
      format: "markdown",
      query,
    },
  });
  if (exported.isError || Number(exported.structuredContent?.content_length ?? 0) < 1) {
    throw new Error("export_evidence returned no content");
  }

  const library = await client.callTool({
    name: "list_library",
    arguments: { status: "active", limit: 5 },
  });
  if (library.isError || !Array.isArray(library.structuredContent?.items)) {
    throw new Error("list_library returned an invalid response");
  }
  const firstDocumentId = library.structuredContent?.items?.[0]?.document_id;
  const integrity = Number.isInteger(firstDocumentId)
    ? await client.callTool({
        name: "integrity_report",
        arguments: { document_id: firstDocumentId },
      })
    : null;
  if (integrity?.isError || (integrity && integrity.structuredContent?.read_only !== true)) {
    throw new Error("integrity_report returned an invalid response");
  }

  console.log(JSON.stringify({
    status: "ok",
    endpoint: endpoint.toString(),
    tools: names,
    search_result_count: results.length,
    fetch_has_provenance: Boolean(fetched.structuredContent?.fragment?.provenance),
    fetched_source_type: first.source_type,
    export_format: exported.structuredContent?.format,
    export_content_length: exported.structuredContent?.content_length,
    library_count: library.structuredContent?.count,
    integrity_report_read_only: integrity?.structuredContent?.read_only ?? null,
  }, null, 2));
} finally {
  await client.close();
}
