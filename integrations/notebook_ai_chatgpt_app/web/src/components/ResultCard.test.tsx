import assert from "node:assert/strict";
import test from "node:test";

import { Children, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import type { SearchResult } from "../types";
import { ResultCard } from "./ResultCard";

function render(result: SearchResult, onOpenTarget?: (href: string) => void): string {
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
      onOpenTarget={onOpenTarget}
    />,
  );
}

function findButton(node: ReactNode, label: string): ReactElement<{ children?: ReactNode; onClick?: () => void }> | null {
  if (!isValidElement(node)) return null;
  const element = node as ReactElement<{ children?: ReactNode; onClick?: () => void }>;
  const children = Children.toArray(element.props.children);
  if (
    element.type === "button"
    && children.some((child) => typeof child === "string" && child.includes(label))
  ) {
    return element;
  }
  for (const child of children) {
    const match = findButton(child, label);
    if (match) return match;
  }
  return null;
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

test("card exposes preview, fragment copy, and ID copy without unsafe direct navigation", () => {
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

test("card exposes host-mediated PDF opening only for an explicitly openable target", () => {
  const openable = render(
    {
      ...base,
      source_type: "pdf_chunk",
      coherent_text: "Original PDF",
      selected_source_text: null,
      user_note: null,
      open_target: {
        can_open_pdf: true,
        pdf_url: "https://search.example/api/v1/library/documents/1/pdf#page=3",
      },
    },
    () => undefined,
  );
  assert.match(openable, /打开 PDF/);

  const blocked = render(
    {
      ...base,
      source_type: "pdf_chunk",
      coherent_text: "Original PDF",
      selected_source_text: null,
      user_note: null,
      open_target: {
        can_open_pdf: false,
        pdf_url: "https://search.example/api/v1/library/documents/1/pdf#page=3",
      },
    },
    () => undefined,
  );
  assert.doesNotMatch(blocked, /打开 PDF/);
});

test("PDF open button forwards the exact safe target to the host callback", () => {
  const href = "https://search.example/api/v1/library/documents/1/pdf#page=3";
  const opened: string[] = [];
  const card = ResultCard({
    result: {
      ...base,
      source_type: "pdf_chunk",
      coherent_text: "Original PDF",
      selected_source_text: null,
      user_note: null,
      open_target: { can_open_pdf: true, pdf_url: href },
    },
    selected: false,
    expanded: false,
    loadingDetail: false,
    onSelect: () => undefined,
    onExpand: () => undefined,
    onCopyFragment: () => undefined,
    onCopyId: () => undefined,
    onOpenTarget: (target) => opened.push(target),
  });
  const button = findButton(card, "打开 PDF");
  assert.ok(button, "open PDF button is rendered");
  button.props.onClick?.();
  assert.deepEqual(opened, [href]);
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
