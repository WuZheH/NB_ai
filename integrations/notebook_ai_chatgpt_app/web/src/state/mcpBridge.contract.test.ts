import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import test from "node:test";

test("widget bridge uses the official ext-apps handshake before receiving tool results", async () => {
  const source = await readFile(resolve(process.cwd(), "web", "src", "state", "mcpBridge.ts"), "utf8");
  assert.match(source, /import \{ App, PostMessageTransport \} from "@modelcontextprotocol\/ext-apps"/);
  const handler = source.indexOf('app.addEventListener("toolresult"');
  const connect = source.indexOf(".connect(new PostMessageTransport");
  assert.ok(handler >= 0, "toolresult handler is registered");
  assert.ok(connect > handler, "toolresult handler is registered before connect");
  assert.match(source, /app\.callServerTool\(\{ name, arguments: args \}\)/);
  assert.match(source, /app\.updateModelContext\(params\)/);
  assert.match(source, /app\.openLink\(\{ url: href \}\)/);
});
