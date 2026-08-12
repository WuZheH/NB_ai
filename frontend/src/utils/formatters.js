import { API_BASE_URL } from "../api/client.js";

export function buildTrace(trace = {}, fallback = {}) {
  return {
    ...fallback,
    ...(trace || {}),
    document_id: trace?.document_id || fallback.document_id,
    chunk_id: trace?.chunk_id || fallback.chunk_id,
    pdf_page: trace?.pdf_page || fallback.pdf_page,
    zotero_key: trace?.zotero_key || fallback.zotero_key,
    locator_result: trace?.locator_result || fallback.locator_result,
    ...sourceFields(trace),
    ...sourceFields(fallback)
  };
}

export function sourceFields(source = {}) {
  const fields = {};
  [
    "preferred_source_open_url",
    "preferred_source_open_label",
    "pdf_fallback_url",
    "pdf_path",
    "source_pdf_path",
    "resolved_pdf_path",
    "zotero_open_pdf_uri",
    "zotero_select_uri",
    "zotero_item_key",
    "zotero_attachment_key"
  ].forEach((key) => {
    if (source?.[key] !== undefined) fields[key] = source[key];
  });
  return fields;
}

export function sourceActionHref(source = {}) {
  const url = source?.preferred_source_open_url || source?.pdf_fallback_url;
  if (!url) return "";
  if (url.startsWith("zotero://select/")) return url;
  if (url.startsWith("zotero://open-pdf/")) return url;
  if (!isSafePdfOpenUrl(url)) return "";
  if (url.startsWith("/")) return `${API_BASE_URL}${url}`;
  return url;
}

export function isApiPdfEndpoint(url = "") {
  const text = String(url || "");
  return /^(?:https?:\/\/(?:127\.0\.0\.1|localhost):\d+)?\/api\/v1\/library\/documents\/[^/]+\/pdf(?:#page=\d+)?$/i.test(text);
}

export function isSafePdfOpenUrl(url = "") {
  const text = String(url || "").trim();
  if (!text) return false;
  if (text.startsWith("zotero://select/") || text.startsWith("zotero://open-pdf/")) return true;
  if (isApiPdfEndpoint(text)) return true;
  return /\.pdf(?:[#?].*)?$/i.test(text);
}

export function sourcePdfSafety(source = {}) {
  const url = source?.preferred_source_open_url || source?.pdf_fallback_url || "";
  const path = source?.resolved_pdf_path || source?.source_pdf_path || source?.pdf_path || "";
  if (isSafePdfOpenUrl(url)) return { safe: true, reason: "", url };
  if (!url && path && /\.pdf$/i.test(String(path))) return { safe: true, reason: "", url: path };
  if (url || path) return { safe: false, reason: "源文件不是 PDF", url: url || path };
  return { safe: false, reason: "PDF 暂不可用", url: "" };
}

export function getPreviewAction(source = {}) {
  const status = source.locator_status || source.locator_result?.locator_status || source.locator_result?.status;
  const pdfPage = source.pdf_page_start ?? source.pdf_page ?? source.pdfPage ?? source.locator_result?.pdf_page;
  const documentId = source.document_id;
  if (!documentId || !pdfPage) return null;
  if (["no_page", "no_text", "metadata_non_locatable", "pdf_missing", "not_found"].includes(status)) {
    return null;
  }
  const exactStatuses = ["exact_text_location", "chunk_aligned", "partial_chunk_aligned", "fallback_term_found", "exact_phrase", "exact_sentence", "located"];
  if (exactStatuses.includes(status) || (source.locator_result?.status && exactStatuses.includes(source.locator_result.status))) {
    return { kind: "preview", label: "预览", document_id: documentId, chunk_id: source.chunk_id, pdf_page: pdfPage };
  }
  // Concrete evidence of text location trumps page_level_only
  const lr = source.locator_result;
  const hasRects = (source.rects?.length || lr?.rects?.length || 0) > 0;
  const hasHighlightCount = (source.highlight_count ?? lr?.highlight_count ?? 0) > 0;
  const hasExactMatch = (source.match_method === "exact_search" || lr?.match_method === "exact_search"
    || source.match_method === "phrase_search" || lr?.match_method === "phrase_search"
    || source.match_method === "sentence_search" || lr?.match_method === "sentence_search");
  if (hasRects || hasHighlightCount || hasExactMatch) {
    return { kind: "preview", label: "预览", document_id: documentId, chunk_id: source.chunk_id, pdf_page: pdfPage };
  }
  if (source.is_locatable === true && !status) {
    return { kind: "preview", label: "预览", document_id: documentId, chunk_id: source.chunk_id, pdf_page: pdfPage };
  }
  return { kind: "preview", label: "预览", document_id: documentId, chunk_id: source.chunk_id, pdf_page: pdfPage };
}

export function getZoteroReadAction(source = {}) {
  const pdfPage = source.pdf_page_start ?? source.pdf_page ?? source.pdfPage;
  const attachmentKey = source.zotero_attachment_key;
  const itemKey = source.zotero_item_key || source.zotero_key;
  const explicitPdf = source.zotero_open_pdf_uri || source.zotero_open_url;
  if (explicitPdf && explicitPdf.startsWith("zotero://open-pdf/")) {
    return {
      kind: "zotero",
      href: explicitPdf,
      label: "Zotero 阅读",
      pdf_page: pdfPage || undefined,
    };
  }
  if (attachmentKey) {
    const pageQuery = pdfPage ? `?page=${pdfPage}` : "";
    return {
      kind: "zotero",
      href: `zotero://open-pdf/library/items/${attachmentKey}${pageQuery}`,
      label: "Zotero 阅读",
      pdf_page: pdfPage || undefined,
    };
  }
  const explicitSelect = source.zotero_select_uri;
  if (explicitSelect && explicitSelect.startsWith("zotero://select/")) {
    return { kind: "zotero", href: explicitSelect, label: "在 Zotero 中定位条目" };
  }
  if (itemKey) {
    return { kind: "zotero", href: `zotero://select/library/items/${itemKey}`, label: "在 Zotero 中定位条目" };
  }
  return null;
}

export function getPdfFallbackAction(source = {}) {
  if (getZoteroReadAction(source)) return null;
  const pdfPage = source.pdf_page_start ?? source.pdf_page ?? source.pdfPage;
  const documentId = source.document_id;
  const explicitUrl = source.pdf_fallback_url || source.preferred_source_open_url || "";
  const endpoint = documentId ? `/api/v1/library/documents/${encodeURIComponent(documentId)}/pdf${pdfPage ? `#page=${pdfPage}` : ""}` : "";
  const url = isSafePdfOpenUrl(endpoint) ? endpoint : explicitUrl;
  if (!isSafePdfOpenUrl(url)) return null;
  return {
    kind: "pdf_fallback",
    href: url.startsWith("/") ? `${API_BASE_URL}${url}` : url,
    label: "打开 PDF",
    pdf_page: pdfPage || undefined,
  };
}

export function pdfActionUnavailableReason(source = {}) {
  const status = source.locator_status || source.locator_result?.locator_status;
  if (status === "no_page") return "缺少 PDF 页码，无法预览";
  if (status === "no_text") return "缺少正文文本，无法精确定位";
  if (status === "metadata_non_locatable") return "抽取元信息，不支持预览";
  if (status === "pdf_missing") return "PDF 文件不可用";
  const safety = sourcePdfSafety(source);
  return safety.reason || "Zotero 未绑定";
}

export function getActionPageInfo(source = {}) {
  const page = source.pdf_page_start ?? source.pdf_page ?? source.pdfPage;
  if (!page) return null;
  const locatorStatus = source.locator_status || source.locator_result?.locator_status;
  const isLocated = locatorStatus === "exact_text_location" || locatorStatus === "chunk_aligned"
    || locatorStatus === "partial_chunk_aligned" || locatorStatus === "exact_phrase"
    || locatorStatus === "exact_sentence" || locatorStatus === "located"
    || source.locator_result?.status === "located";
  if (isLocated) return `定位：第 ${page} 页`;
  return `来源页码：p.${page}`;
}

export function zoteroTraceLabel(source = {}) {
  if (source.zotero_attachment_key) return `PDF 附件 ${source.zotero_attachment_key}`;
  if (source.zotero_item_key) return `条目 ${source.zotero_item_key}`;
  return source.zotero_binding_status || "暂不可用";
}

export function enhanceSourceWithZoteroCandidate(source = {}, candidateState = {}) {
  const candidate = candidateState?.candidate;
  if (!candidate) return { ...source, zotero_binding_status: candidateState?.message || "Zotero 未绑定" };

  const pdfPage = source?.pdf_page || source?.pdfPage;
  if (candidate.zotero_attachment_key) {
    const pageQuery = pdfPage ? `?page=${pdfPage}` : "";
    return {
      ...source,
      zotero_item_key: candidate.zotero_item_key,
      zotero_attachment_key: candidate.zotero_attachment_key,
      zotero_select_uri: candidate.zotero_select_uri,
      zotero_open_pdf_uri: `zotero://open-pdf/library/items/${candidate.zotero_attachment_key}${pageQuery}`,
      preferred_source_open_url: `zotero://open-pdf/library/items/${candidate.zotero_attachment_key}${pageQuery}`,
      preferred_source_open_label: pdfPage ? `在 Zotero 中打开第 ${pdfPage} 页` : "在 Zotero 中打开",
      zotero_binding_status: "Zotero 候选已匹配"
    };
  }
  if (candidate.zotero_select_uri) {
    return {
      ...source,
      zotero_item_key: candidate.zotero_item_key,
      zotero_attachment_key: null,
      zotero_select_uri: candidate.zotero_select_uri,
      zotero_open_pdf_uri: null,
      preferred_source_open_url: candidate.zotero_select_uri,
      preferred_source_open_label: "在 Zotero 中定位条目",
      zotero_binding_status: "Zotero 候选已匹配"
    };
  }
  return { ...source, zotero_binding_status: "Zotero 未绑定" };
}

export function withSourceLocationConfidence(source = {}, location) {
  const preciseStatus = location
    ? location.locator_status === "exact_text_location" || location.status === "located"
      ? "located"
      : location.locator_status === "page_level_only"
        ? "page_level_only"
        : "not_confirmed"
    : "unknown";
  return { ...source, precise_location_status: preciseStatus };
}

export function selectTrustedZoteroCandidate(candidates = []) {
  if (candidates.length !== 1) return null;
  const [candidate] = candidates;
  const warnings = candidate.warnings || [];
  if (warnings.includes("ambiguous_multiple_matches")) return null;
  if (candidate.candidate_status !== "suggested") return null;
  if (candidate.confidence !== "high") return null;
  if (candidate.zotero_attachment_key || candidate.zotero_select_uri) return candidate;
  return null;
}

export function zoteroCandidateMessage(candidates = []) {
  if (!candidates.length) return "Zotero 未绑定";
  if (candidates.length > 1 || candidates.some((candidate) => (candidate.warnings || []).includes("ambiguous_multiple_matches"))) {
    return "Zotero 候选需确认";
  }
  return "Zotero 未绑定";
}

export function compactReasons(reasons = []) {
  const mapped = reasons.map((reason) => {
    if (reason.includes("标题")) return "标题相关";
    if (reason.includes("正文")) return "正文相关";
    if (reason.includes("标签")) return "标签相关";
    if (reason.includes("关系")) return "关系相关";
    if (reason.includes("扩展词")) return "扩展词相关";
    return reason.replace("命中", "相关");
  });
  return Array.from(new Set(mapped)).slice(0, 3);
}

export function sectionDisplayLabel(chunk = {}) {
  if (chunk.section_path?.length > 1) {
    const page = chunk.pdf_page ? ` · p.${chunk.pdf_page}` : "";
    return `${chunk.section_path.join(" › ")}${page}`;
  }
  if (chunk.section_label) return chunk.section_label;
  if (chunk.heading_path) {
    const page = chunk.pdf_page ? ` · p.${chunk.pdf_page}` : "";
    return `${chunk.heading_path}${page}`;
  }
  return chunk.pdf_page ? `p.${chunk.pdf_page} · 未识别章节` : "未识别章节";
}

export function scorePercent(score) {
  const numericScore = Number(score);
  if (!Number.isFinite(numericScore)) return "相关度 --";
  return `相关度 ${Math.round(Math.max(0, Math.min(1, numericScore)) * 100)}%`;
}

export function sourceActionLabel(source = {}, variant = "default") {
  // Regression markers for older Phase 17D source scans: "Open in Zotero at page", "Show in Zotero", "Open local PDF / 打开本地 PDF", "Page hint:".
  const url = source?.preferred_source_open_url || "";
  const pdfPage = source?.pdf_page || source?.pdfPage;
  const preciseStatus = source?.precise_location_status;
  const safety = sourcePdfSafety(source);
  const needsConfidenceLabel = variant === "evidence" || variant === "trace";
  if (url.startsWith("zotero://open-pdf/")) {
    if (variant === "search") return pdfPage ? `Zotero p.${pdfPage}` : "在 Zotero 中打开 PDF";
    return pdfPage ? `Zotero 阅读 p.${pdfPage}` : "Zotero 阅读";
  }
  if (url.startsWith("zotero://select/")) {
    return "在 Zotero 中定位条目";
  }
  if ((source?.pdf_fallback_url || url.startsWith("/api/")) && safety.safe) {
    if (variant === "search") return "本地 PDF";
    if (needsConfidenceLabel && pdfPage && preciseStatus === "located") return `预览 p.${pdfPage}`;
    if (pdfPage) return `打开 PDF p.${pdfPage}`;
    return "打开 PDF";
  }
  if (!safety.safe && safety.reason) return safety.reason;
  return source?.preferred_source_open_label || "PDF 暂不可用";
}

export function locationStatusLabel(status, location = {}) {
  if (status === "exact_text_location") return "已定位文本位置";
  if (status === "layout_line_location") return "已按行定位";
  if (status === "layout_sentence_location") return "已按句定位";
  if (status === "layout_block_location") return "已按版面块定位";
  if (status === "layout_bbox_location" || location?.visual_mode === "layout_block_highlight") return "已按版面块定位";
  if (status === "chunk_aligned") return "已定位到证据片段";
  if (status === "partial_chunk_aligned") return "已定位到证据片段附近";
  if (status === "fallback_term_found" && (
    location?.visual_mode === "approximate_chunk_region"
    || (location?.confidence === "low" && location?.match_method === "fallback_chunk_text_anchor")
  )) {
    return "近似页内提示，非精确文字高亮";
  }
  if (status === "fallback_term_found") return "已根据搜索词定位";
  if (status === "page_level_only") return "页码级定位";
  if (status === "no_page") return "缺少 PDF 页码";
  if (status === "no_text") return "缺少正文文本";
  if (status === "metadata_non_locatable") return "抽取元信息";
  if (status === "pdf_missing") return "PDF 文件不可用";
  if (status === "located") return "已定位";
  if (status === "not_found") return "未找到";
  if (status === "pdf_unavailable") return "PDF 暂不可用";
  if (status === "page_unavailable") return "页面暂不可用";
  if (status === "dependency_unavailable") return "依赖暂不可用";
  return status || "暂不可用";
}

export function locatorTraceLabel(location) {
  if (!location) return undefined;
  const status = location.locator_status || location.status;
  const count = location.highlight_count ?? location.rects?.length ?? 0;
  const countLabel = location.visual_mode === "approximate_chunk_region"
    ? `${count} 个近似区域`
    : location.visual_mode === "layout_line_highlight"
      ? `${count} 行定位`
    : location.visual_mode === "layout_block_highlight"
      ? `${count} 个版面定位块`
      : `${count} 个文本高亮`;
  return `${locationStatusLabel(status, location)} · 第 ${location.pdf_page || "n/a"} 页 · ${countLabel}`;
}

export function relationEvidenceLabel(relation = {}) {
  const chunk = relation.evidence_chunk_id;
  const page = relation.evidence_pdf_page;
  if (chunk && page) return `chunk ${chunk} · p.${page}`;
  if (chunk) return `chunk ${chunk}`;
  if (page) return `p.${page}`;
  return "暂不可用";
}

export function relationEntityFallback(type, id) {
  return `${type || "unknown"}:${id ?? "unknown"}`;
}

export function selectionTypeLabel(type) {
  if (type === "none") return "未选择";
  if (type === "document") return "文档";
  if (type === "evidence") return "证据";
  if (type === "object") return "对象";
  if (type === "zotero_source") return "Zotero PDF";
  if (type === "zotero_inspiration_note") return "Zotero inspiration note";
  if (type === "import_job") return "导入预览";
  if (type === "search_result") return "搜索结果";
  return type || "无";
}

export function isLocalPdfFallback(source = {}) {
  const preferredUrl = source?.preferred_source_open_url || "";
  if (preferredUrl.startsWith("zotero://")) return false;
  if (isApiPdfEndpoint(preferredUrl)) return true;
  return Boolean(!preferredUrl && isSafePdfOpenUrl(source?.pdf_fallback_url));
}

export function navIcon(id) {
  if (id === "readShelf") return "▦";
  if (id === "search") return "⌕";
  if (id === "importPreview") return "⇧";
  if (id === "importReview") return "☑";
  if (id === "research") return "○";
  if (id === "review") return "☷";
  if (id === "settings") return "⚙";
  return "•";
}

export const EMPTY_SAFETY = {
  production_write_enabled: false,
  external_llm_called: false,
  db_write_performed: false,
  mechanism_generated: false
};
