import assert from "node:assert/strict";
import test from "node:test";

import { getJson } from "../src/shared/api/client.js";

const originalFetch = globalThis.fetch;
test.afterEach(() => {
  globalThis.fetch = originalFetch;
});

test("API client accepts application/json responses", async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ status: "ok" }), {
    status: 200,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
  assert.deepEqual(await getJson("/health"), { status: "ok" });
});

test("API client preserves backend stable error codes", async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({
    detail: { error_code: "read_shelf_database_read_failed" },
  }), {
    status: 500,
    headers: { "content-type": "application/json" },
  });
  await assert.rejects(
    getJson("/api/v1/library/read-shelf"),
    (error) => error.code === "read_shelf_database_read_failed"
      && error.status === 500
      && error.backendCode === "read_shelf_database_read_failed",
  );
});

test("API client distinguishes missing endpoints and response format errors", async (t) => {
  await t.test("404", async () => {
    globalThis.fetch = async () => new Response(JSON.stringify({ detail: "Not Found" }), {
      status: 404,
      headers: { "content-type": "application/json" },
    });
    await assert.rejects(getJson("/missing"), (error) => error.code === "api_endpoint_not_found");
  });

  await t.test("HTML body", async () => {
    globalThis.fetch = async () => new Response("<html>error</html>", {
      status: 200,
      headers: { "content-type": "text/html" },
    });
    await assert.rejects(
      getJson("/html"),
      (error) => error.code === "api_response_content_type_invalid",
    );
  });

  await t.test("invalid JSON", async () => {
    globalThis.fetch = async () => new Response("not-json", {
      status: 200,
      headers: { "content-type": "application/json" },
    });
    await assert.rejects(
      getJson("/invalid-json"),
      (error) => error.code === "api_response_json_invalid",
    );
  });
});

test("API client distinguishes true connection failures", async () => {
  globalThis.fetch = async () => {
    throw new TypeError("fetch failed");
  };
  await assert.rejects(getJson("/health"), (error) => error.code === "api_connection_failed");
});
