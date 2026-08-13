import assert from "node:assert/strict";
import test from "node:test";

import {
  createWidgetState,
  normalizeWidgetState,
  persistHostWidgetState,
  reconcileWidgetState,
} from "./widgetState";

test("widget state restores compact selection, filters, and expansion state", () => {
  assert.deepEqual(
    normalizeWidgetState({
      selectedIds: ["pdf-1", "note-1", "pdf-1", 123],
      activeSources: ["pdf_chunk", "zotero_child_note", "invalid"],
      expandedIds: ["note-1"],
    }),
    {
      version: 1,
      selectedIds: ["pdf-1", "note-1"],
      activeSources: ["pdf_chunk", "zotero_child_note"],
      expandedIds: ["note-1"],
    },
  );

  assert.deepEqual(normalizeWidgetState({ selected_fragment_ids: ["legacy-1"] }).selectedIds, ["legacy-1"]);
});

test("new search results prune stale selections and expansions only", () => {
  const state = normalizeWidgetState({
    selectedIds: ["pdf-1", "gone"],
    activeSources: ["pdf_chunk"],
    expandedIds: ["gone", "pdf-1"],
  });
  assert.deepEqual(reconcileWidgetState(state, new Set(["pdf-1", "new-1"])), {
    version: 1,
    selectedIds: ["pdf-1"],
    activeSources: ["pdf_chunk"],
    expandedIds: ["pdf-1"],
  });
});

test("fetch and export result handling leave current widget selection intact", () => {
  const state = normalizeWidgetState({
    selectedIds: ["pdf-1"],
    activeSources: ["pdf_chunk", "zotero_inspiration_note"],
    expandedIds: ["pdf-1"],
  });
  assert.deepEqual(reconcileWidgetState(state, new Set(["pdf-1"])), state);
});

test("persisted widget state is bounded metadata only and unsupported hosts safely degrade", async () => {
  const privateText = "PRIVATE_PDF_OR_NOTE_BODY";
  const snapshot = createWidgetState(["pdf-1"], ["pdf_chunk"], ["pdf-1"]);
  let saved: Record<string, unknown> | undefined;
  assert.equal(
    await persistHostWidgetState(snapshot, {
      widgetState: { text: privateText, provenance: { secret: true } },
      setWidgetState: (value) => {
        saved = value;
      },
    }),
    true,
  );
  assert.deepEqual(Object.keys(saved ?? {}).sort(), ["activeSources", "expandedIds", "selectedIds", "version"]);
  assert.doesNotMatch(JSON.stringify(saved), /PRIVATE_PDF_OR_NOTE_BODY|provenance|api.?key|tunnel/i);
  assert.equal(await persistHostWidgetState(snapshot, undefined), false);
  assert.equal(
    await persistHostWidgetState(snapshot, {
      setWidgetState: () => {
        throw new Error("unsupported");
      },
    }),
    false,
  );
});

test("widget state bounds identifier arrays and preserves an intentionally empty source filter", () => {
  const manyIds = Array.from({ length: 120 }, (_, index) => `fragment-${index}`);
  const state = createWidgetState(manyIds, [], manyIds);
  assert.equal(state.selectedIds.length, 50);
  assert.equal(state.expandedIds.length, 100);
  assert.deepEqual(state.activeSources, []);
});

test("widget state writes are serialized so the newest snapshot wins", async () => {
  const writes: string[][] = [];
  const releases: Array<() => void> = [];
  let markSecondStarted: (() => void) | undefined;
  const secondStarted = new Promise<void>((resolve) => {
    markSecondStarted = resolve;
  });
  const host = {
    setWidgetState: (value: Record<string, unknown>) =>
      new Promise<void>((resolve) => {
        writes.push(value.selectedIds as string[]);
        if (writes.length === 2) markSecondStarted?.();
        releases.push(resolve);
      }),
  };
  const first = persistHostWidgetState(createWidgetState(["first"], ["pdf_chunk"], []), host);
  const second = persistHostWidgetState(createWidgetState(["second"], ["pdf_chunk"], []), host);
  assert.deepEqual(writes, [["first"]]);
  releases.shift()?.();
  await secondStarted;
  assert.deepEqual(writes, [["first"], ["second"]]);
  releases.shift()?.();
  assert.deepEqual(await Promise.all([first, second]), [true, true]);
});
