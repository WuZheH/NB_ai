import assert from "node:assert/strict";
import { createServer, request } from "node:http";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { RendererServer } from "../electron/runtime/rendererServer.js";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

test("missing renderer build produces a bounded local diagnostic without touching the filesystem", async (context) => {
  const server = new RendererServer({
    frontendDist: resolve(ROOT, "does-not-exist"),
    fallbackFile: resolve(ROOT, "renderer", "missing-build.html"),
    designSystemRoot: resolve(ROOT, "..", "..", "packages", "search-design-system", "src"),
    rendererAssets: resolve(ROOT, "renderer"),
    backendUrl: "http://127.0.0.1:8000",
    port: 0,
  });
  context.after(() => server.stop());
  const origin = await server.start();
  assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
  const response = await get(`${origin}/retrieval`);
  assert.equal(response.status, 503);
  assert.match(response.body, /renderer_build_missing/);
  assert.match(response.headers["content-security-policy"], /default-src 'self'/);
  assert.match(response.headers["content-security-policy"], /connect-src 'self' http:\/\/127\.0\.0\.1:8000/);
  const tokens = await get(`${origin}/__search_design__/tokens.css`);
  assert.equal(tokens.status, 200);
  assert.match(tokens.body, /--search-brand:\s*#4f9ff8/);
  assert.match(tokens.body, /--search-primary:\s*var\(--search-brand\)/);
});

test("established renderer port conflict fails closed instead of loading another process", async (context) => {
  const occupied = createServer((_request, response) => response.end("untrusted"));
  await new Promise((resolvePromise) => occupied.listen(0, "127.0.0.1", resolvePromise));
  context.after(() => new Promise((resolvePromise) => occupied.close(resolvePromise)));
  const address = occupied.address();
  assert.ok(address && typeof address !== "string");
  const server = new RendererServer({
    frontendDist: resolve(ROOT, "does-not-exist"),
    fallbackFile: resolve(ROOT, "renderer", "missing-build.html"),
    backendUrl: "http://127.0.0.1:8000",
    port: address.port,
  });
  await assert.rejects(server.start(), /renderer_port_conflict/);
  assert.equal(server.origin, null);
});

function get(url) {
  return new Promise((resolvePromise, reject) => {
    const operation = request(url, (response) => {
      let body = "";
      response.on("data", (chunk) => { body += String(chunk); });
      response.on("end", () => resolvePromise({ status: response.statusCode, headers: response.headers, body }));
    });
    operation.on("error", reject);
    operation.end();
  });
}
