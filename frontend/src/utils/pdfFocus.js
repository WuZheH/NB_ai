const DEFAULT_MIN_SCALE = 1;
const DEFAULT_MAX_SCALE = 3.5;
const SMALL_UNION_AREA = 0.2;
const LARGE_UNION_AREA = 0.25;

export function unionRects(rects = []) {
  const valid = rects.filter(isValidRect);
  if (!valid.length) return null;
  return valid.reduce(
    (acc, rect) => ({
      x0: Math.min(acc.x0, Number(rect.x0)),
      y0: Math.min(acc.y0, Number(rect.y0)),
      x1: Math.max(acc.x1, Number(rect.x1)),
      y1: Math.max(acc.y1, Number(rect.y1))
    }),
    {
      x0: Number(valid[0].x0),
      y0: Number(valid[0].y0),
      x1: Number(valid[0].x1),
      y1: Number(valid[0].y1)
    }
  );
}

export function selectFocusRects(rects = [], pageHeight = 0) {
  const valid = rects.filter(isValidRect).sort((left, right) => Number(left.y0) - Number(right.y0));
  if (valid.length <= 1) return valid;
  const page = Math.max(1, Number(pageHeight) || unionRects(valid)?.y1 || 1);
  const groups = [];
  let current = [valid[0]];
  let currentUnion = unionRects(current);
  for (const rect of valid.slice(1)) {
    const rectHeight = Math.max(1, Number(rect.y1) - Number(rect.y0));
    const groupHeight = Math.max(1, (currentUnion?.y1 || 0) - (currentUnion?.y0 || 0));
    const gap = Number(rect.y0) - Number(currentUnion?.y1 || 0);
    const threshold = Math.max(18, Math.min(page * 0.06, Math.max(rectHeight, groupHeight) * 1.8));
    if (gap > threshold) {
      groups.push(current);
      current = [rect];
    } else {
      current.push(rect);
    }
    currentUnion = unionRects(current);
  }
  groups.push(current);
  return groups.sort(compareFocusGroups)[0] || valid;
}

export function focusToHighlightUnion({
  rects = [],
  pageWidth,
  pageHeight,
  containerWidth,
  containerHeight,
  mode = "exact",
  minScale = DEFAULT_MIN_SCALE,
  maxScale = DEFAULT_MAX_SCALE
} = {}) {
  const width = Number(pageWidth);
  const height = Number(pageHeight);
  const viewportWidth = Number(containerWidth);
  const viewportHeight = Number(containerHeight);
  if (!width || !height || !viewportWidth || !viewportHeight) {
    return null;
  }

  const focusRects = selectFocusRects(rects, height);
  const unionRect = unionRects(focusRects);
  if (!unionRect) return null;

  const unionWidth = Math.max(1, unionRect.x1 - unionRect.x0);
  const unionHeight = Math.max(1, unionRect.y1 - unionRect.y0);
  const unionAreaRatio = (unionWidth * unionHeight) / Math.max(1, width * height);
  const widthFit = unionAreaRatio > LARGE_UNION_AREA ? 0.76 : 0.82;
  const heightFit = unionAreaRatio <= SMALL_UNION_AREA ? 0.7 : 0.62;
  const desiredByWidth = (viewportWidth * widthFit) / unionWidth;
  const desiredByHeight = (viewportHeight * heightFit) / unionHeight;
  const effectiveMaxScale = unionAreaRatio > LARGE_UNION_AREA ? Math.min(maxScale, 2.1) : maxScale;
  const desiredScale = clampScaleValue(Math.min(desiredByWidth, desiredByHeight), minScale, effectiveMaxScale);
  const centerX = (unionRect.x0 + unionRect.x1) / 2;
  const verticalBias = unionAreaRatio <= SMALL_UNION_AREA ? 0.46 : 0.5;
  const centerY = unionRect.y0 + unionHeight * verticalBias;

  return {
    mode,
    rects: focusRects,
    unionRect,
    unionAreaRatio,
    desiredScale,
    desiredByWidth,
    desiredByHeight,
    scrollCenter: { x: centerX, y: centerY },
    scrollBias: { x: 0.5, y: unionAreaRatio <= SMALL_UNION_AREA ? 0.44 : 0.5 }
  };
}

export function calculateHighlightScroll({
  focus,
  renderedWidth,
  renderedHeight,
  pageWidth,
  pageHeight,
  containerWidth,
  containerHeight,
  scrollWidth,
  scrollHeight
} = {}) {
  if (!focus || !pageWidth || !pageHeight || !renderedWidth || !renderedHeight) return null;
  const scaleX = Number(renderedWidth) / Number(pageWidth);
  const scaleY = Number(renderedHeight) / Number(pageHeight);
  const centerX = Number(focus.scrollCenter?.x ?? 0) * scaleX;
  const centerY = Number(focus.scrollCenter?.y ?? 0) * scaleY;
  const biasX = Number(focus.scrollBias?.x ?? 0.5);
  const biasY = Number(focus.scrollBias?.y ?? 0.5);
  const maxLeft = Number.isFinite(Number(scrollWidth))
    ? Math.max(0, Number(scrollWidth) - Number(containerWidth || 0))
    : Infinity;
  const maxTop = Number.isFinite(Number(scrollHeight))
    ? Math.max(0, Number(scrollHeight) - Number(containerHeight || 0))
    : Infinity;
  return {
    left: clampNumber(centerX - Number(containerWidth || 0) * biasX, 0, maxLeft),
    top: clampNumber(centerY - Number(containerHeight || 0) * biasY, 0, maxTop)
  };
}

export function isRenderReadyForFocus({
  renderState,
  pageWidth,
  pageHeight,
  scale,
  desiredScale,
  pageNumber,
  locationPage,
  scaleEpsilon = 0.01,
  pixelEpsilon = 2
} = {}) {
  if (!renderState || renderState.status !== "ready") return false;
  const width = Number(pageWidth);
  const height = Number(pageHeight);
  const currentScale = Number(scale);
  const targetScale = Number(desiredScale);
  const renderedWidth = Number(renderState.width);
  const renderedHeight = Number(renderState.height);
  if (!width || !height || !currentScale || !targetScale || !renderedWidth || !renderedHeight) return false;
  if (Math.abs(currentScale - targetScale) > scaleEpsilon) return false;

  const renderedPage = Number(renderState.pageNumber);
  const expectedPage = Number(locationPage ?? pageNumber);
  if (Number.isFinite(renderedPage) && Number.isFinite(expectedPage) && renderedPage !== expectedPage) return false;

  const renderedScale = Number(renderState.scale);
  if (Number.isFinite(renderedScale) && Math.abs(renderedScale - targetScale) > scaleEpsilon) return false;

  return Math.abs(renderedWidth - width * targetScale) <= pixelEpsilon
    && Math.abs(renderedHeight - height * targetScale) <= pixelEpsilon;
}

export function shouldApplyAutoFocus({ focusKey, manualZoomKey, completedFocusKey } = {}) {
  return Boolean(focusKey) && manualZoomKey !== focusKey && completedFocusKey !== focusKey;
}

export function clampScaleValue(value, minScale = DEFAULT_MIN_SCALE, maxScale = DEFAULT_MAX_SCALE) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return minScale;
  return Math.max(minScale, Math.min(maxScale, Number(numeric.toFixed(2))));
}

function isValidRect(rect) {
  return rect
    && Number.isFinite(Number(rect.x0))
    && Number.isFinite(Number(rect.y0))
    && Number.isFinite(Number(rect.x1))
    && Number.isFinite(Number(rect.y1))
    && Number(rect.x1) > Number(rect.x0)
    && Number(rect.y1) > Number(rect.y0);
}

function compareFocusGroups(left, right) {
  const leftUnion = unionRects(left);
  const rightUnion = unionRects(right);
  const leftArea = rectArea(leftUnion);
  const rightArea = rectArea(rightUnion);
  if (left.length !== right.length) return right.length - left.length;
  return rightArea - leftArea;
}

function rectArea(rect) {
  if (!rect) return 0;
  return Math.max(0, rect.x1 - rect.x0) * Math.max(0, rect.y1 - rect.y0);
}

function clampNumber(value, minValue, maxValue) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return minValue;
  return Math.max(minValue, Math.min(maxValue, numeric));
}
