import assert from "node:assert/strict";
import test from "node:test";

import {
  NOTEBOOK_SOURCE_TYPES,
  buildEvidenceCopyText,
  buildKeywordSearchRequest,
  buildNotebookSearchRequest,
  fragmentFromResponse,
  notebookSourceLabel,
  normalizeRetrievalResult,
  openTargetActions,
} from "../src/features/retrieval/utils/notebookSearch.js";

test("notebook search defaults to the four approved sources and clamps limit", () => {
  const request = buildNotebookSearchRequest({
    query: "  foot   sliding  ",
    limit: 200,
    filters: { includeContext: true, documentId: "17", sourceType: "" },
  });
  assert.deepEqual(request, {
    query: "foot sliding",
    limit: 50,
    source_types: [...NOTEBOOK_SOURCE_TYPES],
    document_ids: [17],
    include_context: true,
  });
  assert.deepEqual(NOTEBOOK_SOURCE_TYPES, [
    "pdf_chunk",
    "zotero_annotation_comment",
    "zotero_child_note",
    "zotero_inspiration_note",
  ]);
});

test("source and document filters map to notebook array fields", () => {
  const request = buildNotebookSearchRequest({
    query: "VAE",
    limit: 12,
    filters: {
      sourceType: "zotero_child_note",
      documentId: "not-a-number",
      includeContext: false,
    },
  });
  assert.deepEqual(request.source_types, ["zotero_child_note"]);
  assert.deepEqual(request.document_ids, []);
  assert.equal(request.include_context, false);
});

test("keyword request preserves the legacy FTS mode while restricting sources", () => {
  const request = buildKeywordSearchRequest({
    query: "EDSR",
    mode: "coverage",
    limit: 20,
    filters: { includeContext: true, collapseDuplicates: true },
  });
  assert.equal(request.mode, "coverage");
  assert.equal(request.offset, 0);
  assert.equal(request.collapse_duplicates, true);
  assert.deepEqual(request.filters.source_type, [...NOTEBOOK_SOURCE_TYPES]);
});

test("PDF and user-note fields remain separate", () => {
  const pdf = normalizeRetrievalResult({
    fragment_id: "pdf-1",
    source_type: "pdf_chunk",
    text: "PDF source text",
    note_text: null,
    selected_text: null,
  });
  const note = normalizeRetrievalResult({
    fragment_id: "note-1",
    source_type: "zotero_annotation_comment",
    text: null,
    note_text: "My reading note",
    selected_text: "Quoted paper text",
  });
  assert.equal(pdf.text, "PDF source text");
  assert.equal(pdf.note_text, null);
  assert.equal(note.text, null);
  assert.equal(note.note_text, "My reading note");
  assert.equal(note.selected_text, "Quoted paper text");
  const copy = buildEvidenceCopyText(note);
  assert.match(copy, /User note:\nMy reading note/);
  assert.match(copy, /Selected source text:\nQuoted paper text/);
  assert.doesNotMatch(copy, /PDF text:/);
});

test("fragment wrapper and exact source labels are stable", () => {
  const fragment = fragmentFromResponse({
    status: "ok",
    fragment: {
      fragment_id: "fragment-1",
      source_type: "zotero_inspiration_note",
      document_title: "Paper",
      pdf_page: 8,
    },
  });
  assert.equal(fragment.title, "Paper");
  assert.equal(fragment.page_number, 8);
  assert.equal(notebookSourceLabel("pdf_chunk"), "PDF 原文");
  assert.equal(notebookSourceLabel("zotero_annotation_comment"), "Zotero 批注");
  assert.equal(notebookSourceLabel("zotero_child_note"), "Zotero 笔记");
  assert.equal(notebookSourceLabel("zotero_inspiration_note"), "灵感笔记");
});

test("open_target is authoritative and unsafe URLs stay disabled", () => {
  const enabled = openTargetActions({
    open_target: {
      can_open_pdf: true,
      pdf_url: "/api/v1/library/documents/17/pdf#page=8",
      can_open_zotero: true,
      zotero_url: "zotero://select/library/items/ABCDEFGH",
    },
  }, "http://127.0.0.1:8000");
  assert.equal(enabled.pdf.href, "http://127.0.0.1:8000/api/v1/library/documents/17/pdf#page=8");
  assert.equal(enabled.zotero.href, "zotero://select/library/items/ABCDEFGH");

  const disabled = openTargetActions({
    open_target: {
      can_open_pdf: true,
      pdf_url: "javascript:alert(1)",
      can_open_zotero: false,
      zotero_url: null,
      zotero_disabled_reason: "No Zotero item is available.",
    },
  });
  assert.equal(disabled.pdf.enabled, false);
  assert.match(disabled.pdf.reason, /安全检查/);
  assert.equal(disabled.zotero.reason, "No Zotero item is available.");
});
