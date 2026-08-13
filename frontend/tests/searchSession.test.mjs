import assert from "node:assert/strict";
import test from "node:test";

import {
  captureSearchSessionBeforeNavigation,
  clearSearchSessionForTests,
  readSearchSession,
  registerSearchSessionCapture,
  summarizeSearchSession,
  writeSearchSession,
} from "../src/features/retrieval/state/searchSession.js";

test("workspace summary reads the canonical search session", () => {
  const session = {
    query: "surface reconstruction",
    searchKind: "keyword",
    ftsMode: "precision",
    filters: { sourceType: "pdf_chunk", documentId: "7" },
    searchState: {
      status: "ready",
      data: {
        total: 9,
        results: [
          { fragment_id: "fragment-1", document_title: "Example paper", pdf_page: 4 },
        ],
      },
    },
    previewState: {
      status: "ready",
      data: { fragment_id: "fragment-1", document_title: "Example paper", pdf_page: 4 },
    },
    basket: [{ fragment_id: "fragment-1" }, { fragment_id: "fragment-2" }],
  };

  writeSearchSession(session);
  const summary = summarizeSearchSession(readSearchSession());

  assert.equal(summary.hasSession, true);
  assert.equal(summary.query, "surface reconstruction");
  assert.equal(summary.searchKind, "keyword");
  assert.equal(summary.resultCount, 9);
  assert.equal(summary.results.length, 1);
  assert.equal(summary.preview.fragment_id, "fragment-1");
  assert.equal(summary.basket.length, 2);
  assert.equal(summary.filters.documentId, "7");
  clearSearchSessionForTests();
});

test("navigation capture persists the latest page snapshot", () => {
  clearSearchSessionForTests();
  const unregister = registerSearchSessionCapture(() => {
    writeSearchSession({ query: "captured query", basket: [] });
  });

  captureSearchSessionBeforeNavigation();
  assert.equal(readSearchSession().query, "captured query");

  unregister();
  clearSearchSessionForTests();
});

test("empty summary is safe for a clean start", () => {
  clearSearchSessionForTests();
  const summary = summarizeSearchSession();
  assert.equal(summary.hasSession, false);
  assert.equal(summary.resultCount, 0);
  assert.deepEqual(summary.results, []);
  assert.deepEqual(summary.basket, []);
});
