import assert from "node:assert/strict";
import test from "node:test";

import type { SearchResult } from "../types";
import { pinnedEvidence, selectedEvidence, selectionContext, toggleEvidence } from "./evidenceSelection";

const pdf = {
  fragment_id: "pdf-1",
  source_type: "pdf_chunk",
  document_title: "Motion Paper",
  page_label: "12",
  text: "private PDF text",
  provenance: [{ private: true }],
} as SearchResult;

test("evidence selection toggles without changing result order", () => {
  assert.deepEqual(toggleEvidence([], "pdf-1"), ["pdf-1"]);
  assert.deepEqual(toggleEvidence(["pdf-1"], "pdf-1"), []);
  assert.deepEqual(selectedEvidence([pdf], ["missing", "pdf-1"]), [pdf]);
});

test("pinned evidence contains locator metadata and no private body or provenance", () => {
  assert.deepEqual(pinnedEvidence([pdf], ["pdf-1"]), [
    {
      fragment_id: "pdf-1",
      source_type: "pdf_chunk",
      document_title: "Motion Paper",
      page_label: "12",
    },
  ]);
  assert.doesNotMatch(JSON.stringify(pinnedEvidence([pdf], ["pdf-1"])), /private PDF text|provenance/);
});

test("selection context identifies source, document, page, and fragment", () => {
  const context = selectionContext([pdf], ["pdf-1"]);
  assert.match(context, /pdf-1/);
  assert.match(context, /pdf_chunk/);
  assert.match(context, /Motion Paper/);
  assert.match(context, /page 12/);
});
