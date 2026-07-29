import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

import { renderToStaticMarkup } from "react-dom/server";

import type { SearchResult } from "../types";
import { EvidenceBasket } from "./EvidenceBasket";
import { WaitingState } from "./StatePanel";

const selectedResult: SearchResult = {
  fragment_id: "fragment-1",
  source_type: "pdf_chunk",
  selection_rank: 1,
  document_id: 1,
  document_title: "Fixture document",
  document_type: "book",
  pdf_page: 1,
  page_label: "1",
  heading: "Section",
  section: "Section",
  coherent_text: "Evidence",
  selected_source_text: null,
  user_note: null,
  context_before: null,
  context_after: null,
  tags: [],
  provenance: { source: "pdf", fragment_id: "fragment-1" },
  open_target: null,
};

const handlers = {
  onClear: () => undefined,
  onExport: () => undefined,
  onPin: () => undefined,
};

test("empty evidence basket is absent from the DOM", () => {
  const html = renderToStaticMarkup(
    <EvidenceBasket
      selected={[]}
      selectedCount={0}
      canPin={false}
      exporting={false}
      status=""
      {...handlers}
    />,
  );
  assert.equal(html, "");
  assert.doesNotMatch(html, /证据篮子|尚未选择证据|固定选择到聊天|复制 Markdown|更多/);
});

test("selected evidence basket keeps count, pin, copy, and export controls", () => {
  const html = renderToStaticMarkup(
    <EvidenceBasket
      selected={[selectedResult]}
      selectedCount={1}
      canPin
      exporting={false}
      status=""
      {...handlers}
    />,
  );
  assert.match(html, /证据篮子/);
  assert.match(html, /1 条/);
  assert.match(html, /固定选择到聊天/);
  assert.match(html, /复制 Markdown/);
  assert.match(html, /导出 JSONL/);
  assert.match(html, /导出 JSON/);
  assert.doesNotMatch(html, /尚未选择证据/);
});

test("waiting state appears exactly once", () => {
  const html = renderToStaticMarkup(<WaitingState />);
  assert.equal(
    html.match(/等待 Search 检索结果/g)?.length,
    1,
  );
  const appSource = readFileSync(
    resolve(process.cwd(), "web", "src", "App.tsx"),
    "utf8",
  );
  assert.equal(
    appSource.match(/<WaitingState \/>/g)?.length,
    1,
  );
  assert.doesNotMatch(appSource, /等待检索问题|正在等待 Search 检索结果/);
});

test("390 and 1024 px responsive contract uses one scroll surface and visible focus states", () => {
  const css = readFileSync(
    resolve(process.cwd(), "web", "src", "styles.css"),
    "utf8",
  );
  assert.match(css, /body\s*\{[^}]*overflow-x:\s*hidden[^}]*overflow-y:\s*auto/s);
  assert.match(css, /\.widget-scroll-region\s*\{[^}]*overflow:\s*visible/s);
  assert.match(css, /\.evidence-basket\s*\{[^}]*overflow:\s*visible/s);
  assert.match(css, /button:focus-visible/);
  assert.match(css, /@media \(max-width: 600px\)/);
  assert.match(css, /max-width:\s*1024px/);
  assert.doesNotMatch(css, /\.source-filters\s*\{[^}]*max-height:/s);
});
