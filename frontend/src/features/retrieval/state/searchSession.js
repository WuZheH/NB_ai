let currentSearchSession = null;
let captureSearchSession = null;

export function readSearchSession() {
  return currentSearchSession;
}

export function writeSearchSession(session) {
  currentSearchSession = session || null;
}

export function summarizeSearchSession(session = currentSearchSession) {
  const value = session && typeof session === "object" ? session : {};
  const searchData = value.searchState?.data && typeof value.searchState.data === "object"
    ? value.searchState.data
    : {};
  const results = Array.isArray(searchData.results) ? searchData.results : [];
  const basket = Array.isArray(value.basket) ? value.basket : [];
  const preview = value.previewState?.data && typeof value.previewState.data === "object"
    ? value.previewState.data
    : null;
  const explicitTotal = Number(searchData.total ?? searchData.total_count);
  const resultCount = Number.isFinite(explicitTotal) && explicitTotal >= 0
    ? explicitTotal
    : results.length;
  const query = String(value.query || "").trim();
  const searchStatus = String(value.searchState?.status || "idle");

  return {
    hasSession: Boolean(
      query
      || searchStatus !== "idle"
      || results.length
      || basket.length
      || preview
    ),
    query,
    searchKind: value.searchKind || "high_quality",
    ftsMode: value.ftsMode || "precision",
    filters: value.filters && typeof value.filters === "object" ? value.filters : {},
    searchStatus,
    results,
    resultCount,
    preview,
    previewStatus: String(value.previewState?.status || "idle"),
    basket,
  };
}

export function registerSearchSessionCapture(capture) {
  captureSearchSession = typeof capture === "function" ? capture : null;
  return () => {
    if (captureSearchSession === capture) captureSearchSession = null;
  };
}

export function captureSearchSessionBeforeNavigation() {
  captureSearchSession?.();
}

export function clearSearchSessionForTests() {
  currentSearchSession = null;
  captureSearchSession = null;
}
