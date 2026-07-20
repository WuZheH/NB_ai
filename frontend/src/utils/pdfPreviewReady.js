const DEFAULT_SCALE_EPSILON = 0.001;

export function resolveScaleTransition({ currentScale, targetScale, threshold = 0.05 } = {}) {
  const current = Number(currentScale);
  const target = Number(targetScale);
  if (!Number.isFinite(current) || !Number.isFinite(target)) {
    return { shouldUpdate: false, settledScale: null };
  }
  const shouldUpdate = Math.abs(target - current) > Number(threshold);
  return {
    shouldUpdate,
    settledScale: shouldUpdate ? target : current,
  };
}

export function resolvePdfPageRequest(requestedPage, pageCount) {
  const requested = Number(requestedPage);
  const count = Number(pageCount);
  if (!Number.isInteger(requested) || requested < 1 || !Number.isInteger(count) || count < 1) {
    return {
      pageNumber: null,
      fallback: false,
      fallbackReason: "invalid_page_request",
    };
  }
  if (requested <= count) {
    return { pageNumber: requested, fallback: false, fallbackReason: "" };
  }
  return {
    pageNumber: count,
    fallback: true,
    fallbackReason: "requested_page_out_of_range",
  };
}

export function isPdfPreviewSemanticallyReady({
  resolvedPdfUrl,
  requestedPage,
  renderState,
  currentScale,
  autoFitSettled,
  viewportSettled,
  restoreSettled,
  overlaySettled,
  scaleEpsilon = DEFAULT_SCALE_EPSILON,
} = {}) {
  if (!resolvedPdfUrl || !Number.isInteger(Number(requestedPage)) || Number(requestedPage) < 1) return false;
  if (!renderState || renderState.status !== "ready" || renderState.errorTitle || renderState.errorMessage) return false;
  if (Number(renderState.requestedPageNumber) !== Number(requestedPage)) return false;
  if (!Number.isInteger(Number(renderState.pageNumber)) || Number(renderState.pageNumber) < 1) return false;
  if (!Number.isFinite(Number(renderState.scale)) || !Number.isFinite(Number(currentScale))) return false;
  if (Math.abs(Number(renderState.scale) - Number(currentScale)) > Number(scaleEpsilon)) return false;
  if (
    Number(renderState.width) <= 0
    || Number(renderState.height) <= 0
    || Number(renderState.backingWidth) <= 0
    || Number(renderState.backingHeight) <= 0
  ) return false;
  return Boolean(autoFitSettled && viewportSettled && restoreSettled && overlaySettled);
}
