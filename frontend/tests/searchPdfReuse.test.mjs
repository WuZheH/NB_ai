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
  const restoreState = {
    document_id: 5,
    chunk_id: 5561,
    requested_page_number: 13,
    scale: 1,
    scroll_top: 240,
    scroll_left: 0,
  };
  const preview = buildSearchPdfLocationPreview({
    document_id: 5,
    chunk_id: 5561,
    pdf_page: 13,
    text: "ordinary search result",
    open_target: { pdf_url: "/api/v1/library/documents/5/pdf#page=13" },
    pdf_location: { location: legacyLocation },
    pdf_preview_state: restoreState,
    locator: { pdf_page: 12, locator_strategy: "page" },
  });
  assert.equal(preview.available, true);
  assert.equal(preview.props.location, legacyLocation);
  assert.equal(preview.props.page, 13);
  assert.equal(preview.props.pdfUrl, "/api/v1/library/documents/5/pdf#page=13");
  assert.equal(preview.props.restoreState, restoreState);
  assert.equal(preview.props.fitWidthOnLoad, true);
});

test("Search keeps only public evidence identifiers inside collapsed technical details", async () => {
  const card = await readFile(new URL("../src/components/retrieval/RetrievalResultCard.jsx", import.meta.url), "utf8");
  const panel = await readFile(new URL("../src/features/retrieval/components/SearchPreviewPanel.jsx", import.meta.url), "utf8");
  assert.match(card, /<details className="searchTechnicalDetails localRetrievalTechnicalDetails">/);
  assert.match(panel, /<details className="searchTechnicalDetails searchPreviewTechnicalDetails">/);
  assert.match(panel, /<MetaRow label="document_id"/);
  assert.match(panel, /<MetaRow label="selection_rank"/);
  assert.doesNotMatch(panel, /<MetaRow label="(?:content_hash|chunk_id|reranker_score|score)"/);
  assert.doesNotMatch(panel, /<h3>来源摘要<\/h3>/);
});

test("Search layout gives the established PDF preview a readable desktop rail and one viewer scroll host", async () => {
  const styles = await readFile(new URL("../src/styles/search-product.css", import.meta.url), "utf8");
  assert.match(styles, /grid-template-columns: minmax\(360px, 54fr\) minmax\(520px, 46fr\)/);
  assert.match(styles, /\.searchPreviewContent\.isPdfView[\s\S]*?overflow: hidden/);
  assert.match(styles, /\.searchPreviewPdfStage \.pdfPreviewScroller[\s\S]*?overflow-x: hidden;[\s\S]*?overflow-y: auto/);
});

test("PDF preview readiness waits for the final rendered selection and committed overlay", async () => {
  const preview = await readFile(new URL("../src/PdfLocationPreview.jsx", import.meta.url), "utf8");
  const probe = await readFile(new URL("../../integrations/search_desktop/tests/fixtures/productionPdfPreviewProbe.mjs", import.meta.url), "utf8");
  for (const contractPart of [
    "renderMatchesSelection",
    "canvasDimensionsReady",
    "autoFitSettled",
    "focusSettled",
    "previewReady",
    'data-preview-ready={previewReady ? "true" : "false"}',
    'emitPdfPreviewStage("preview_ready_committed"',
  ]) {
    assert.match(preview, new RegExp(contractPart.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  assert.match(probe, /waitForPreviewReady\(\{ chunkId: 0, pageNumber: 2, strategy: "exact", highlightCount: 1 \}/);
  assert.match(probe, /runWorkspaceRoundTrips\(VIEWPORT_WIDTH === 1600 \? 5 : 1\)/);
  assert.match(probe, /snapshot\?\.ready/);
  assert.match(probe, /snapshot\.canvasRectWidth > 0/);
  assert.doesNotMatch(probe, /pdf_first_preview[\s\S]{0,200}document\.querySelector\('\[data-testid="pdf-highlight-layer"\]'\)/);
});

test("PDF preview retries one render task that never settles", async () => {
  const preview = await readFile(new URL("../src/PdfLocationPreview.jsx", import.meta.url), "utf8");
  assert.match(preview, /const PDF_RENDER_TIMEOUT_MS = 15_000/);
  assert.match(preview, /const PDF_RENDER_MAX_RETRIES = 1/);
  assert.match(preview, /Promise\.race\(\[/);
  assert.match(preview, /error\?\.code === "pdf_render_timeout"/);
  assert.match(preview, /reportStage\("render_retry_scheduled"/);
  assert.match(preview, /setRenderRetryEpoch\(\(current\) => current \+ 1\)/);
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

test("root route defaults to the stable retrieval page while workspace deep links remain", () => {
  const root = parseAppRouteFromLocation({ pathname: "/", search: "" });
  const workspace = parseAppRouteFromLocation({ pathname: "/workspace", search: "" });
  assert.equal(root.view, "retrieval");
  assert.equal(root.redirectPath, LOCAL_RETRIEVAL_PATH);
  assert.equal(workspace.view, "workspace");
});

test("renderer-memory Search session retains query, results, selection, preview, and scroll", () => {
  clearSearchSessionForTests();
  const session = {
    query: "ordinary query",
    searchKind: "high_quality",
    ftsMode: "precision",
    filters: { sourceType: "pdf_chunk", documentId: "1", includeContext: true },
    searchState: { status: "ready", data: { results: [{ fragment_id: "fixture" }] }, error: "" },
    basket: [{ fragment_id: "fixture" }],
    previewState: {
      status: "ready",
      data: {
        fragment_id: "fixture",
        pdf_page: 13,
        preview_view: "pdf",
        pdf_preview_state: {
          document_id: 5,
          chunk_id: 5561,
          requested_page_number: 13,
          scale: 1,
          scroll_top: 240,
          scroll_left: 0,
        },
        pdf_location: {
          location: {
            pdf_page: 13,
            locator_status: "exact_text_location",
            rects: [{ x0: 1, y0: 2, x1: 3, y1: 4 }],
          },
        },
      },
      error: "",
    },
    scroll: { results: 480, preview: 120, basket: 60 },
  };
  writeSearchSession(session);
  assert.equal(readSearchSession(), session);
  assert.equal(readSearchSession().searchKind, "high_quality");
  assert.equal(readSearchSession().filters.documentId, "1");
  assert.equal(readSearchSession().previewState.data.pdf_page, 13);
  assert.equal(readSearchSession().previewState.data.preview_view, "pdf");
  assert.equal(readSearchSession().previewState.data.pdf_location.location.rects.length, 1);
  assert.equal(readSearchSession().previewState.data.pdf_preview_state.scroll_top, 240);
  clearSearchSessionForTests();
});
