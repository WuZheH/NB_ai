import assert from "node:assert/strict";
import test from "node:test";

import {
  calculateHighlightScroll,
  focusToHighlightUnion,
  isRenderReadyForFocus,
} from "../src/utils/pdfFocus.js";

const rects = [{ x0: 72, y0: 190, x1: 300, y1: 212 }];

test("a non-scrollable container produces a bounded zero scroll target", () => {
  const focus = focusToHighlightUnion({
    rects,
    pageWidth: 612,
    pageHeight: 792,
    containerWidth: 700,
    containerHeight: 900,
  });
  assert.ok(focus);
  assert.deepEqual(calculateHighlightScroll({
    focus,
    renderedWidth: 612,
    renderedHeight: 792,
    pageWidth: 612,
    pageHeight: 792,
    containerWidth: 700,
    containerHeight: 900,
    scrollWidth: 612,
    scrollHeight: 792,
  }), { left: 0, top: 0 });
});

test("highlight scrolling returns null only when a required focus or page input is missing", () => {
  const focus = focusToHighlightUnion({
    rects,
    pageWidth: 612,
    pageHeight: 792,
    containerWidth: 520,
    containerHeight: 600,
  });
  assert.equal(calculateHighlightScroll({
    focus: null,
    renderedWidth: 612,
    renderedHeight: 792,
    pageWidth: 612,
    pageHeight: 792,
  }), null);
  assert.equal(calculateHighlightScroll({
    focus,
    renderedWidth: 0,
    renderedHeight: 792,
    pageWidth: 612,
    pageHeight: 792,
  }), null);
  assert.equal(isRenderReadyForFocus({
    renderState: { status: "ready", width: 0, height: 792, pageNumber: 2, scale: 1 },
    pageWidth: 612,
    pageHeight: 792,
    scale: 1,
    desiredScale: 1,
    locationPage: 2,
  }), false);
});

test("zero viewport cannot create focus and a resize to positive dimensions can", () => {
  const base = { rects, pageWidth: 612, pageHeight: 792 };
  assert.equal(focusToHighlightUnion({ ...base, containerWidth: 0, containerHeight: 0 }), null);
  const focus = focusToHighlightUnion({ ...base, containerWidth: 520, containerHeight: 600 });
  assert.ok(focus);
  assert.equal(focus.mode, "exact");
  assert.ok(focus.desiredScale > 0);
});
