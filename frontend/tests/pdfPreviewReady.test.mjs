import assert from "node:assert/strict";
import test from "node:test";

import {
  isPdfPreviewSemanticallyReady,
  resolvePdfPageRequest,
  resolveScaleTransition,
} from "../src/utils/pdfPreviewReady.js";

test("scale transitions settle on the scale that was actually applied", () => {
  assert.deepEqual(
    resolveScaleTransition({ currentScale: 1, targetScale: 1.04 }),
    { shouldUpdate: false, settledScale: 1 },
  );
  assert.deepEqual(
    resolveScaleTransition({ currentScale: 1, targetScale: 1.06 }),
    { shouldUpdate: true, settledScale: 1.06 },
  );
});

test("page requests use an explicit last-page fallback instead of waiting forever", () => {
  assert.deepEqual(resolvePdfPageRequest(2, 3), {
    pageNumber: 2,
    fallback: false,
    fallbackReason: "",
  });
  assert.deepEqual(resolvePdfPageRequest(9, 3), {
    pageNumber: 3,
    fallback: true,
    fallbackReason: "requested_page_out_of_range",
  });
  assert.equal(resolvePdfPageRequest(1, 0).pageNumber, null);
});

test("semantic ready requires the current document render and completed restore contract", () => {
  const readyState = {
    status: "ready",
    requestedPageNumber: 2,
    pageNumber: 2,
    scale: 1,
    width: 612,
    height: 792,
    backingWidth: 1224,
    backingHeight: 1584,
    errorTitle: "",
    errorMessage: "",
  };
  const base = {
    resolvedPdfUrl: "/api/v1/library/documents/1/pdf",
    requestedPage: 2,
    renderState: readyState,
    currentScale: 1,
    autoFitSettled: true,
    restoreSettled: true,
    overlaySettled: true,
  };
  assert.equal(isPdfPreviewSemanticallyReady(base), true);
  assert.equal(isPdfPreviewSemanticallyReady({ ...base, autoFitSettled: false }), false);
  assert.equal(isPdfPreviewSemanticallyReady({ ...base, restoreSettled: false }), false);
  assert.equal(isPdfPreviewSemanticallyReady({ ...base, overlaySettled: false }), false);
  assert.equal(isPdfPreviewSemanticallyReady({ ...base, renderState: { ...readyState, status: "loading" } }), false);
  assert.equal(isPdfPreviewSemanticallyReady({ ...base, renderState: { ...readyState, errorMessage: "load failed" } }), false);
  assert.equal(isPdfPreviewSemanticallyReady({ ...base, requestedPage: 3 }), false);
});

test("formal-entry degradation can complete the overlay contract without a scroll target", async () => {
  const { readFile } = await import("node:fs/promises");
  const source = await readFile(new URL("../src/PdfLocationPreview.jsx", import.meta.url), "utf8");
  assert.match(source, /preview_focus_degraded/);
  assert.match(source, /highlight_scroll_unavailable/);
  assert.match(source, /setCompletedFocusKey\(focusKey\)/);
});
