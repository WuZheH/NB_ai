import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

import { renderToStaticMarkup } from "react-dom/server";

import { EvidenceBasket } from "./EvidenceBasket";

test("empty evidence basket explains the next action and remains keyboard-native", () => {
  const html = renderToStaticMarkup(
    <EvidenceBasket
      selected={[]}
      selectedCount={0}
      canPin={false}
      exporting={false}
      status=""
      onClear={() => undefined}
      onExport={() => undefined}
      onPin={() => undefined}
    />,
  );
  assert.match(html, /尚未选择证据/);
  assert.match(html, /加入证据/);
  assert.match(html, /disabled/);
  assert.doesNotMatch(html, /tabindex="0"/i);
});

test("responsive widget contract uses one scroll surface and visible focus states", () => {
  const css = readFileSync(resolve(process.cwd(), "web", "src", "styles.css"), "utf8");
  assert.match(css, /body\s*\{[^}]*overflow-y:\s*auto/s);
  assert.match(css, /\.widget-scroll-region\s*\{[^}]*overflow:\s*visible/s);
  assert.match(css, /\.evidence-basket\s*\{[^}]*overflow:\s*visible/s);
  assert.match(css, /button:focus-visible/);
  assert.match(css, /@media \(max-width: 600px\)/);
  assert.match(css, /max-width:\s*1024px/);
  assert.doesNotMatch(css, /\.source-filters\s*\{[^}]*max-height:/s);
});
