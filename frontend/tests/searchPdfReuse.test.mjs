import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  adaptFragmentLocator,
  buildSearchPdfLocationPreview,
} from "../src/features/retrieval/adapters/searchPdfLocationPreview.js";
import {
  clearSearchSessionForTests,
  readSearchSession,
  writeSearchSession,
} from "../src/features/retrieval/state/searchSession.js";
import {
  LOCAL_RETRIEVAL_PATH,
  parseAppRouteFromLocation,
} from "../src/app/routes.js";

test("Search preview reuses the established PdfLocationPreview component", async () => {
  const panel = await readFile(new URL("../src/features/retrieval/components/SearchPreviewPanel.jsx", import.meta.url), "utf8");
  assert.match(panel, /import PdfLocationPreview from "\.\.\/\.\.\/\.\.\/PdfLocationPreview\.jsx"/);
  assert.match(panel, /<PdfLocationPreview \{\.\.\.pdfPreview\.props\} \/>/);
  assert.doesNotMatch(panel, /PdfFragmentPreview/);
});

test("legacy pdf-location payload remains authoritative for Search PDF props", () => {
  const legacyLocation = {
    locator_status: "exact_text_location",
    pdf_page: 13,
    page_width: 612,
    page_height: 792,
    rects: [{ x0: 72, y0: 80, x1: 300, y1: 102 }],
  };
  const preview = buildSearchPdfLocationPreview({
    document_id: 5,
    chunk_id: 5561,
    pdf_page: 13,
    text: "ordinary search result",
    open_target: { pdf_url: "/api/v1/library/documents/5/pdf#page=13" },
    pdf_location: { location: legacyLocation },
    locator: { pdf_page: 12, locator_strategy: "page" },
  });
  assert.equal(preview.available, true);
  assert.equal(preview.props.location, legacyLocation);
  assert.equal(preview.props.page, 13);
  assert.equal(preview.props.pdfUrl, "/api/v1/library/documents/5/pdf#page=13");
});

test("fragment annotation coordinates are only adapted into the legacy coordinate contract", () => {
  const location = adaptFragmentLocator({
    document_id: 10,
    pdf_page: 314,
    locator_strategy: "annotation",
    bbox: { pageIndex: 313, rects: [[77.31, 120.969, 378.101, 130.145]] },
    selected_text: "annotation text",
  });
  assert.equal(location.locator_status, "exact_text_location");
  assert.equal(location.coordinate_origin, "pdf_bottom_left");
  assert.equal(location.highlight_count, 1);
  assert.deepEqual(location.rects[0], { x0: 77.31, y0: 120.969, x1: 378.101, y1: 130.145 });
});

test("Search preview rejects non-document PDF URLs and falls back to the registered document endpoint", () => {
  const preview = buildSearchPdfLocationPreview({
    document_id: 1,
    pdf_page: 1,
    open_target: { pdf_url: "file:///private/source.pdf" },
  });
  assert.equal(preview.available, true);
  assert.equal(preview.props.pdfUrl, "/api/v1/library/documents/1/pdf");
});

test("legacy library search route redirects to the single retrieval page", () => {
  const parsed = parseAppRouteFromLocation({ pathname: "/library-search", search: "" });
  assert.equal(parsed.view, "retrieval");
  assert.equal(parsed.redirectPath, LOCAL_RETRIEVAL_PATH);
});

test("renderer-memory Search session retains query, results, selection, preview, and scroll", () => {
  clearSearchSessionForTests();
  const session = {
    query: "ordinary query",
    filters: { sourceType: "pdf_chunk" },
    searchState: { status: "ready", data: { results: [{ fragment_id: "fixture" }] }, error: "" },
    basket: [{ fragment_id: "fixture" }],
    previewState: { status: "ready", data: { fragment_id: "fixture" }, error: "" },
    scroll: { results: 480, preview: 120, basket: 60 },
  };
  writeSearchSession(session);
  assert.equal(readSearchSession(), session);
  clearSearchSessionForTests();
});
