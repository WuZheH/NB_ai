import assert from "node:assert/strict";
import test from "node:test";

import { renderToStaticMarkup } from "react-dom/server";

import type { SearchResult } from "../types";
import { ResultCard } from "./ResultCard";

function render(result: SearchResult): string {
  return renderToStaticMarkup(
    <ResultCard
      result={result}
      selected={false}
      expanded={false}
      loadingDetail={false}
      onSelect={() => undefined}
      onExpand={() => undefined}
      onCopy={() => undefined}
      onOpen={() => undefined}
    />,
  );
}

const base = {
  fragment_id: "fragment-1",
  final_rank: 1,
  final_score: 1,
  reranker_score: 0.5,
  semantic_score: 0.4,
  document_id: 1,
  document_title: "Paper",
  document_type: "pdf",
  chunk_id: 1,
  pdf_page: 3,
  page_label: "3",
  context_before: null,
  context_after: null,
  tags: [],
  provenance: [],
  open_target: null,
} as const;

test("PDF card labels its text as PDF source and never invents a user note", () => {
  const html = render({ ...base, source_type: "pdf_chunk", text: "Original PDF", selected_text: null, note_text: null });
  assert.match(html, /PDF 片段/);
  assert.match(html, /PDF 原文/);
  assert.match(html, /Original PDF/);
  assert.doesNotMatch(html, /用户笔记/);
});

test("Zotero note card keeps note_text and selected_text distinct", () => {
  const html = render({
    ...base,
    source_type: "zotero_annotation_comment",
    text: null,
    note_text: "My interpretation",
    selected_text: "Quoted paper text",
  });
  assert.match(html, /Zotero 批注笔记/);
  assert.match(html, /用户笔记/);
  assert.match(html, /My interpretation/);
  assert.match(html, /对应选中文本/);
  assert.match(html, /Quoted paper text/);
});
