import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import test from "node:test";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";

import { FORMAL_MCP_TOOL_NAMES } from "../scripts/formal-mcp-tool-contract.mjs";
import { createNotebookMcpServer } from "./app";

const packageRoot = resolve(process.cwd());

test("real widget builder emits the READ document title", async () => {
  const built = spawnSync(
    process.execPath,
    [resolve(packageRoot, "scripts", "build-widget.mjs")],
    { cwd: packageRoot, encoding: "utf8" },
  );
  assert.equal(built.status, 0, built.stderr || built.stdout);

  const html = await readFile(
    resolve(packageRoot, "web", "dist", "widget.html"),
    "utf8",
  );
  assert.match(html, /<title>READ<\/title>/);
  assert.doesNotMatch(html, /<title>Search<\/title>|Cread Secure|翻书/i);
});

test("formal smoke contract exactly matches the ten registered READ tools", async () => {
  assert.equal(FORMAL_MCP_TOOL_NAMES.length, 10);
  assert.equal(new Set(FORMAL_MCP_TOOL_NAMES).size, 10);
  assert.ok(FORMAL_MCP_TOOL_NAMES.includes("import_status"));

  const server = createNotebookMcpServer({
    client: {} as never,
    widget: { html: "<!doctype html><title>READ</title>" },
  });
  const client = new Client({ name: "formal-release-contract-test", version: "0.1.0" });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  await server.connect(serverTransport);
  await client.connect(clientTransport);
  try {
    assert.equal(client.getServerVersion()?.name, "READ");
    const registered = (await client.listTools()).tools.map(({ name }) => name).sort();
    assert.deepEqual(registered, [...FORMAL_MCP_TOOL_NAMES]);

    const resources = await client.listResources();
    assert.equal(resources.resources[0]?.title, "READ");
  } finally {
    await client.close();
    await server.close();
  }
});
