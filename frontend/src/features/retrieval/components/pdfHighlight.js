const HYPHEN_BREAK = /[-\u2010\u2011\u00ad]\s*$/u;

export function safePdfEndpoint(value) {
  const url = String(value || "").split("#")[0];
  return /^\/api\/v1\/library\/documents\/\d+\/pdf$/u.test(url) ? url : "";
}

export function normalizeTextForPdfMatch(value) {
  return normalizeString(value).text;
}

export function matchPdfTextItems(items, selectedText) {
  const source = buildNormalizedPdfText(items);
  const target = normalizeString(selectedText).text;
  if (!source.text || !target) return { matched: false, itemIndexes: [], range: null };
  const start = source.text.indexOf(target);
  if (start < 0) return { matched: false, itemIndexes: [], range: null };
  const end = start + target.length;
  const indexes = new Set();
  for (let index = start; index < end; index += 1) {
    const itemIndex = source.charToItem[index];
    if (Number.isInteger(itemIndex) && itemIndex >= 0) indexes.add(itemIndex);
  }
  return {
    matched: indexes.size > 0,
    itemIndexes: [...indexes],
    range: { start, end },
  };
}

export function buildNormalizedPdfText(items = []) {
  let text = "";
  const charToItem = [];
  const append = (value, itemIndex) => {
    const normalizedValue = normalizeItemValue(value);
    for (const normalizedCharacter of normalizedValue) {
      if (/\s/u.test(normalizedCharacter)) {
        if (!text || /\s$/u.test(text)) continue;
        text += " ";
        charToItem.push(itemIndex);
      } else {
        text += normalizedCharacter;
        charToItem.push(itemIndex);
      }
    }
  };

  items.forEach((item, itemIndex) => {
    const raw = String(item?.str || "");
    const next = String(items[itemIndex + 1]?.str || "");
    const joinsHyphenatedWord = HYPHEN_BREAK.test(raw) && /^\p{Ll}/u.test(next.trim());
    append(joinsHyphenatedWord ? raw.replace(HYPHEN_BREAK, "") : raw, itemIndex);
    if (!joinsHyphenatedWord && (item?.hasEOL || raw && next && !/\s$/u.test(raw))) {
      append(" ", itemIndex);
    }
  });
  return { text: text.trim(), charToItem: charToItem.slice(0, text.trimEnd().length) };
}

export function pdfTextItemRects(items, itemIndexes, viewport, pdfjsLib) {
  if (!viewport || !pdfjsLib?.Util) return [];
  const selected = new Set(itemIndexes || []);
  const rects = [];
  items.forEach((item, index) => {
    if (!selected.has(index) || !Array.isArray(item?.transform)) return;
    const tx = pdfjsLib.Util.transform(viewport.transform, item.transform);
    const fontHeight = Math.max(1, Math.hypot(tx[2], tx[3]));
    const width = Math.max(1, Math.abs(Number(item.width || 0) * viewport.scale));
    rects.push({
      left: Number(tx[4]),
      top: Number(tx[5]) - fontHeight,
      width,
      height: fontHeight,
    });
  });
  return mergeAdjacentRects(rects);
}

export function viewportRectFromPdfRect(viewport, rect) {
  const raw = rectValues(rect);
  if (!viewport?.convertToViewportRectangle || !raw) return null;
  // Retrieval bbox values are copied from Zotero's PDF annotation position.
  // The real annotation characterization confirms these are PDF user-space
  // rectangles (bottom-left origin), exactly what PDF.js expects here.  Do
  // not invert Y a second time: PDF.js applies that inversion, scale, and
  // rotation in convertToViewportRectangle.
  const values = viewport.convertToViewportRectangle(raw);
  const left = Math.min(values[0], values[2]);
  const top = Math.min(values[1], values[3]);
  const width = Math.abs(values[2] - values[0]);
  const height = Math.abs(values[3] - values[1]);
  if (!Number.isFinite(left) || !Number.isFinite(top) || width < 0.5 || height < 0.5) return null;
  return { left, top, width, height };
}

export function mergeAdjacentRects(rects = []) {
  const ordered = rects
    .filter((rect) => Number.isFinite(rect.left) && Number.isFinite(rect.top) && rect.width > 0 && rect.height > 0)
    .sort((left, right) => left.top - right.top || left.left - right.left);
  const merged = [];
  for (const rect of ordered) {
    const previous = merged[merged.length - 1];
    const sameLine = previous && Math.abs(previous.top - rect.top) <= Math.max(previous.height, rect.height) * 0.4;
    const close = previous && rect.left - (previous.left + previous.width) <= 8;
    if (sameLine && close) {
      previous.width = Math.max(previous.left + previous.width, rect.left + rect.width) - previous.left;
      previous.height = Math.max(previous.height, rect.height);
    } else {
      merged.push({ ...rect });
    }
  }
  return merged;
}

function normalizeString(value) {
  let text = String(value || "").normalize("NFKC");
  text = text
    .replace(/\u00ad/gu, "")
    .replace(/[\u2018\u2019\u201a\u201b]/gu, "'")
    .replace(/[\u201c\u201d\u201e\u201f]/gu, '"')
    .replace(/[\u2010-\u2015]/gu, "-")
    .replace(/[-]\s*\n\s*(?=\p{Ll})/gu, "")
    .replace(/[\u00a0\s]+/gu, " ");
  return { text: text.trim() };
}

function normalizeItemValue(value) {
  return String(value || "").normalize("NFKC")
    .replace(/[\u2018\u2019\u201a\u201b]/gu, "'")
    .replace(/[\u201c\u201d\u201e\u201f]/gu, '"')
    .replace(/[\u2010-\u2015]/gu, "-")
    .replace(/\u00ad/gu, "");
}

function rectValues(rect) {
  const raw = Array.isArray(rect)
    ? rect
    : [rect?.x0, rect?.y0, rect?.x1, rect?.y1];
  if (raw.length !== 4) return null;
  const values = raw.map(Number);
  return values.every(Number.isFinite) ? values : null;
}
