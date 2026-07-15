export function buildSearchPdfLocationPreview(result) {
  const documentId = positiveInteger(result?.document_id);
  const page = positiveInteger(
    result?.pdf_location?.location?.pdf_page
      ?? result?.locator?.pdf_page
      ?? result?.pdf_page
      ?? result?.page_number
  );
  const location = result?.pdf_location?.location
    || adaptFragmentLocator(result?.locator, { documentId, page });
  const pdfUrl = [
    result?.open_target?.pdf_url,
    result?.locator?.pdf_endpoint,
    documentId ? `/api/v1/library/documents/${encodeURIComponent(documentId)}/pdf` : "",
  ].map(safePdfUrl).find(Boolean) || "";

  return {
    available: Boolean(documentId && page && pdfUrl),
    props: {
      documentId,
      location,
      pdfUrl,
      page,
      pageNumber: page,
      pdf_page_start: page,
      pdf_page_end: page,
      chunkId: result?.chunk_id ?? null,
      quote: result?.selected_text || result?.text || "",
      highlightText: result?.selected_text || result?.text || "",
      fitWidthOnLoad: true,
    },
  };
}

export function adaptFragmentLocator(locator, { documentId = null, page = null } = {}) {
  if (!locator) return null;
  const rawRects = Array.isArray(locator?.bbox?.rects)
    ? locator.bbox.rects
    : Array.isArray(locator?.rects)
      ? locator.rects
      : [];
  const rects = rawRects.map(normalizeRect).filter(Boolean);
  const pdfPage = positiveInteger(locator.pdf_page ?? page);
  const strategy = String(locator.locator_strategy || "");
  const hasRects = rects.length > 0;

  return {
    status: hasRects ? "located" : "not_found",
    locator_status: hasRects ? "exact_text_location" : "page_level_only",
    locator_reason: hasRects
      ? `Search fragment locator · ${strategy || "coordinates"}`
      : "已定位到对应 PDF 页面；精确高光由旧 locator 数据决定。",
    document_id: positiveInteger(locator.document_id ?? documentId),
    pdf_page: pdfPage,
    page_index: Number.isInteger(locator.page_index) ? locator.page_index : (pdfPage ? pdfPage - 1 : null),
    match_method: strategy || "page",
    confidence: hasRects ? "high" : "page_only",
    rects,
    highlight_count: rects.length,
    coordinate_origin: Array.isArray(locator?.bbox?.rects) ? "pdf_bottom_left" : "top_left",
    snippet_used: locator.selected_text || "",
    warnings: Array.isArray(locator.warnings) ? locator.warnings : [],
  };
}

function normalizeRect(value) {
  if (Array.isArray(value) && value.length >= 4) {
    return finiteRect({ x0: value[0], y0: value[1], x1: value[2], y1: value[3] });
  }
  if (value && typeof value === "object") return finiteRect(value);
  return null;
}

function finiteRect(value) {
  const rect = {
    x0: Number(value.x0),
    y0: Number(value.y0),
    x1: Number(value.x1),
    y1: Number(value.y1),
  };
  if (!Object.values(rect).every(Number.isFinite)) return null;
  if (rect.x1 <= rect.x0 || rect.y1 <= rect.y0) return null;
  return rect;
}

function safePdfUrl(value) {
  const url = String(value || "").trim();
  if (!url.startsWith("/api/v1/library/documents/")) return "";
  return url;
}

function positiveInteger(value) {
  const number = Number(value);
  return Number.isInteger(number) && number > 0 ? number : null;
}
