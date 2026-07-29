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
      onCopyFragment={() => undefined}
      onCopyId={() => undefined}
    />,
  );
}

const base = {
  fragment_id: "fragment-1",
  selection_rank: 1,
  document_id: 1,
  document_title: "Paper",
  document_type: "pdf",
  pdf_page: 3,
  page_label: "3",
  heading: "Section",
  section: "Section",
  context_before: null,
  context_after: null,
  tags: [],
  provenance: { source: "pdf", fragment_id: "fragment-1" },
  open_target: null,
} as const;

test("PDF card labels its text as PDF source and never invents a user note", () => {
  const html = render({ ...base, source_type: "pdf_chunk", coherent_text: "Original PDF", selected_source_text: null, user_note: null });
  assert.match(html, /PDF 原文/);
  assert.match(html, /PDF 原文/);
  assert.match(html, /Original PDF/);
  assert.doesNotMatch(html, /用户笔记/);
});

test("Zotero note card keeps note_text and selected_text distinct", () => {
  const html = render({
    ...base,
    source_type: "zotero_annotation_comment",
    coherent_text: null,
    user_note: "My interpretation",
    selected_source_text: "Quoted paper text",
  });
  assert.match(html, /Zotero 批注/);
  assert.match(html, /用户笔记/);
  assert.match(html, /My interpretation/);
  assert.match(html, /对应选中文本/);
  assert.match(html, /Quoted paper text/);
});

test("card exposes preview, fragment copy, and ID copy without direct app navigation", () => {
  const html = render({ ...base, source_type: "pdf_chunk", coherent_text: "Original PDF", selected_source_text: null, user_note: null });
  assert.match(html, />预览</);
  assert.match(html, /复制片段/);
  assert.match(html, /复制 ID/);
  assert.doesNotMatch(html, /打开 PDF/);
  assert.doesNotMatch(html, /打开 Zotero/);
  assert.doesNotMatch(html, /打开 ChatGPT/);
  assert.match(html, /search-button-subtle/);
  assert.match(html, /search-button-transparent/);
  assert.match(html, /search-toggle-button/);
  assert.match(html, /aria-pressed="false"/);
  assert.doesNotMatch(html, /search-button-primary/);
});

test("card leads with a visible summary and does not expose internal ranking scores", () => {
  const html = render({ ...base, source_type: "pdf_chunk", coherent_text: "First-screen summary", selected_source_text: null, user_note: null });
  assert.match(html, /result-summary/);
  assert.match(html, /First-screen summary/);
  assert.doesNotMatch(html, /reranker/);
  assert.doesNotMatch(html, /最终排名/);
  assert.doesNotMatch(html, /0\.5000/);
});

test("repeated document headings can be suppressed without hiding the source summary", () => {
  const html = renderToStaticMarkup(
    <ResultCard
      result={{ ...base, source_type: "pdf_chunk", coherent_text: "Second result", selected_source_text: null, user_note: null }}
      selected={false}
      expanded={false}
      loadingDetail={false}
      showDocumentTitle={false}
      onSelect={() => undefined}
      onExpand={() => undefined}
      onCopyFragment={() => undefined}
      onCopyId={() => undefined}
    />,
  );
  assert.doesNotMatch(html, /<h2>Paper<\/h2>/);
  assert.match(html, /Second result/);
});
