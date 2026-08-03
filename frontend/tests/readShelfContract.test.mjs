import assert from "node:assert/strict";
import test from "node:test";

import {
  normalizeReadShelfPayload,
  presentReadShelfError,
} from "../src/features/library/utils/readShelfContract.js";

test("Read Shelf contract accepts status and array items without requiring message", () => {
  assert.deepEqual(normalizeReadShelfPayload({ status: "ok", items: [{ document_id: 1 }] }), {
    status: "ready",
    items: [{ document_id: 1 }],
    message: "",
  });
});

test("Read Shelf contract rejects non-array items with a stable code", () => {
  assert.throws(
    () => normalizeReadShelfPayload({ status: "ok", items: {} }),
    (error) => error.code === "read_shelf_response_invalid",
  );
});

test("Read Shelf error presentation distinguishes required failure classes", () => {
  const codes = [
    "api_connection_failed",
    "api_endpoint_not_found",
    "api_internal_error",
    "api_response_content_type_invalid",
    "read_shelf_database_read_failed",
  ];
  for (const code of codes) {
    const presentation = presentReadShelfError({ code });
    assert.equal(presentation.code, code);
    assert.ok(presentation.title.length > 0);
    assert.ok(presentation.message.length > 0);
  }
});
