import assert from "node:assert/strict";
import test from "node:test";

import {
  buildNormalizedPdfText,
  matchPdfTextItems,
  mergeAdjacentRects,
  safePdfEndpoint,
  viewportRectFromPdfRect,
} from "../src/features/retrieval/components/pdfHighlight.js";

test("PDF preview accepts only the registered document endpoint", () => {
  assert.equal(safePdfEndpoint("/api/v1/library/documents/12/pdf#page=4"), "/api/v1/library/documents/12/pdf");
  assert.equal(safePdfEndpoint("file:///D:/private.pdf"), "");
  assert.equal(safePdfEndpoint("/api/v1/library/documents/12/pdf?path=C:/private.pdf"), "");
  assert.equal(safePdfEndpoint("https://example.invalid/file.pdf"), "");
});

test("text-layer matcher preserves item mapping across a hyphenated line break", () => {
  const items = [
    { str: "multi-", hasEOL: true },
    { str: "modal representation", hasEOL: false },
  ];
  const normalized = buildNormalizedPdfText(items);
  assert.equal(normalized.text, "multimodal representation");
  const match = matchPdfTextItems(items, "multimodal representation");
  assert.equal(match.matched, true);
  assert.deepEqual(match.itemIndexes, [0, 1]);
});

test("text-layer matcher handles whitespace, typography, and multiple PDF items", () => {
  const items = [
    { str: "The", hasEOL: false },
    { str: "model’s", hasEOL: false },
    { str: "output", hasEOL: true },
  ];
  const match = matchPdfTextItems(items, "The model's\noutput");
  assert.equal(match.matched, true);
  assert.deepEqual(match.itemIndexes, [0, 1, 2]);
});

test("bbox rectangles are converted with the PDF.js viewport and retain every region", () => {
  const viewport = {
    viewBox: [0, 0, 400, 400],
    convertToViewportRectangle: ([x0, y0, x1, y1]) => [x0 * 2, 800 - y0 * 2, x1 * 2, 800 - y1 * 2],
  };
  const first = viewportRectFromPdfRect(viewport, { x0: 10, y0: 20, x1: 30, y1: 40 });
  const second = viewportRectFromPdfRect(viewport, { x0: 50, y0: 60, x1: 70, y1: 80 });
  assert.deepEqual(first, { left: 20, top: 40, width: 40, height: 40 });
  assert.deepEqual(second, { left: 100, top: 120, width: 40, height: 40 });
});

test("adjacent text rectangles merge only within the same visual line", () => {
  assert.deepEqual(
    mergeAdjacentRects([
      { left: 10, top: 10, width: 20, height: 12 },
      { left: 35, top: 10.5, width: 18, height: 12 },
      { left: 10, top: 35, width: 20, height: 12 },
    ]),
    [
      { left: 10, top: 10, width: 43, height: 12 },
      { left: 10, top: 35, width: 20, height: 12 },
    ],
  );
});
