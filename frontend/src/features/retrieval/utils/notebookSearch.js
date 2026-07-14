export const HIGH_QUALITY_SEARCH_KIND = "high_quality";
export const KEYWORD_SEARCH_KIND = "keyword";

export const NOTEBOOK_SOURCE_TYPES = Object.freeze([
  "pdf_chunk",
  "zotero_annotation_comment",
  "zotero_child_note",
  "zotero_inspiration_note",
]);

export const NOTEBOOK_NOTE_SOURCE_TYPES = Object.freeze(
  NOTEBOOK_SOURCE_TYPES.filter((sourceType) => sourceType !== "pdf_chunk")
);

const SOURCE_LABELS = Object.freeze({
  pdf_chunk: "PDF 原文",
  zotero_annotation_comment: "Zotero 批注",
  zotero_child_note: "Zotero 笔记",
  zotero_inspiration_note: "灵感笔记",
});

export function notebookSourceLabel(sourceType) {
  return SOURCE_LABELS[sourceType] || sourceType || "未知来源";
}

export function buildNotebookSearchRequest({ query, limit, filters = {} }) {
  const sourceTypes = filters.sourceType
    ? [filters.sourceType]
    : [...NOTEBOOK_SOURCE_TYPES];
  const documentId = positiveInteger(filters.documentId);
  return {
    query: compactText(query),
    limit: notebookLimit(limit),
    source_types: sourceTypes,
    document_ids: documentId ? [documentId] : [],
    include_context: Boolean(filters.includeContext),
  };
}

export function buildKeywordSearchRequest({ query, mode, limit, filters = {} }) {
  const request = buildLegacyFtsSearchRequest({ query, mode, limit, filters });
  return {
    ...request,
    filters: {
      ...request.filters,
      source_type: filters.sourceType
        ? filters.sourceType
        : [...NOTEBOOK_SOURCE_TYPES],
    },
  };
}

export function buildLegacyFtsSearchRequest({ query, mode, limit, filters = {} }) {
  const structuredFilters = {};
  if (filters.sourceType) structuredFilters.source_type = filters.sourceType;
  const documentId = positiveInteger(filters.documentId);
  const year = positiveInteger(filters.year);
  if (documentId) structuredFilters.document_id = documentId;
  if (year) structuredFilters.year = year;
  return {
    query: compactText(query),
    mode,
    limit,
    offset: 0,
    collapse_duplicates: Boolean(filters.collapseDuplicates),
    include_context: Boolean(filters.includeContext),
    filters: structuredFilters,
  };
}

export function normalizeRetrievalResponse(response = {}) {
  return {
    ...response,
    results: Array.isArray(response.results)
      ? response.results.map(normalizeRetrievalResult)
      : [],
  };
}

export function normalizeRetrievalResult(result = {}) {
  const pdfPage = result.pdf_page ?? result.page_number ?? null;
  const documentTitle = result.document_title ?? result.title ?? null;
  return {
    ...result,
    fragment_id: String(result.fragment_id || ""),
    display_id: result.display_id || result.fragment_id || "",
    document_title: documentTitle,
    title: documentTitle,
    pdf_page: pdfPage,
    page_number: pdfPage,
    final_score: finiteNumberOrNull(result.final_score ?? result.score),
    reranker_score: finiteNumberOrNull(result.reranker_score),
    semantic_score: finiteNumberOrNull(result.semantic_score),
    final_rank: positiveInteger(result.final_rank),
    provenance: Array.isArray(result.provenance) ? result.provenance : [],
    tags: Array.isArray(result.tags) ? result.tags : [],
    warnings: Array.isArray(result.warnings) ? result.warnings : [],
  };
}

export function fragmentFromResponse(response = {}) {
  const fragment = response.fragment || response.result || response;
  return normalizeRetrievalResult(fragment);
}

export function openTargetActions(result = {}, apiBaseUrl = "") {
  const target = result.open_target || {};
  const pdfHref = target.can_open_pdf === true
    ? safePdfHref(target.pdf_url, apiBaseUrl)
    : "";
  const zoteroHref = target.can_open_zotero === true
    ? safeZoteroHref(target.zotero_url)
    : "";
  return {
    pdf: {
      href: pdfHref,
      enabled: Boolean(pdfHref),
      reason: pdfHref
        ? ""
        : target.pdf_disabled_reason || (
          target.can_open_pdf === true
            ? "PDF 打开地址未通过安全检查。"
            : "当前结果没有可用的 PDF 打开目标。"
        ),
    },
    zotero: {
      href: zoteroHref,
      enabled: Boolean(zoteroHref),
      reason: zoteroHref
        ? ""
        : target.zotero_disabled_reason || (
          target.can_open_zotero === true
            ? "Zotero 打开地址未通过安全检查。"
            : "当前结果没有可用的 Zotero 条目。"
        ),
    },
  };
}

export function buildEvidenceCopyText(result = {}) {
  const normalized = normalizeRetrievalResult(result);
  const lines = [
    `Source type: ${notebookSourceLabel(normalized.source_type)}`,
    `Document: ${normalized.document_title || "未命名来源"}`,
    `Page: ${pageLabel(normalized)}`,
    `Fragment ID: ${normalized.fragment_id || "n/a"}`,
  ];
  if (normalized.final_rank) lines.push(`Final rank: ${normalized.final_rank}`);
  if (normalized.reranker_score !== null) {
    lines.push(`Reranker score: ${formatScore(normalized.reranker_score)}`);
  }
  if (normalized.source_type === "pdf_chunk") {
    lines.push("", "PDF text:", normalized.text || "");
  } else {
    lines.push("", "User note:", normalized.note_text || "");
    lines.push("", "Selected source text:", normalized.selected_text || "");
  }
  if (normalized.context_before || normalized.context_after) {
    lines.push("", "Context:");
    if (normalized.context_before) lines.push(normalized.context_before);
    if (normalized.context_after) lines.push(normalized.context_after);
  }
  return lines.join("\n").trim();
}

export function pageLabel(result = {}) {
  const physicalPage = result.pdf_page ?? result.page_number;
  return [
    physicalPage ? `p.${physicalPage}` : null,
    result.page_label || null,
  ].filter(Boolean).join(" · ") || "页码未标注";
}

export function formatScore(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(6) : "n/a";
}

function notebookLimit(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return 12;
  return Math.min(50, Math.max(1, Math.trunc(numeric)));
}

function positiveInteger(value) {
  const numeric = Number(value);
  return Number.isInteger(numeric) && numeric > 0 ? numeric : null;
}

function finiteNumberOrNull(value) {
  if (value === null || value === undefined || value === "") return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function compactText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function safePdfHref(value, apiBaseUrl) {
  const href = String(value || "").trim();
  if (!href) return "";
  if (/^\/api\/v1\/library\/documents\/[^/]+\/pdf(?:#page=\d+)?$/i.test(href)) {
    return `${String(apiBaseUrl || "").replace(/\/$/, "")}${href}`;
  }
  if (/^https?:\/\/(?:127\.0\.0\.1|localhost)(?::\d+)?\/api\/v1\/library\/documents\/[^/]+\/pdf(?:#page=\d+)?$/i.test(href)) {
    return href;
  }
  return "";
}

function safeZoteroHref(value) {
  const href = String(value || "").trim();
  if (href.startsWith("zotero://select/") || href.startsWith("zotero://open-pdf/")) {
    return href;
  }
  return "";
}
