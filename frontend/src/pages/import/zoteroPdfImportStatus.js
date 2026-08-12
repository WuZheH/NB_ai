export function zoteroPdfImportStatus(source = {}) {
  source = source || {};
  const rawStatus = String(source.import_status || "").trim();
  const matchReasons = zoteroPdfMatchReasons(source);
  let status = rawStatus || (source.already_imported || source.imported ? "exact_imported" : "not_imported");
  if (rawStatus === "imported" || rawStatus === "partially_imported" || rawStatus === "duplicate_candidate") {
    status = deriveZoteroImportStatusFromReasons(matchReasons, rawStatus);
  }
  if (rawStatus === "missing_file") {
    status = "unknown";
  }
  if (!ZOTERO_IMPORT_STATUS_LABELS[status]) {
    status = source.cache_status === "missing" || source.path_exists === false ? "unknown" : "not_imported";
  }
  const existingDocument = zoteroPdfExistingDocument(source);
  const recommendedAction = source.recommended_action
    || (status === "sibling_imported" ? "view_existing_document" : ZOTERO_IMPORT_STATUS_RECOMMENDED_ACTION[status])
    || "select_for_import";
  return {
    status,
    label: ZOTERO_IMPORT_STATUS_LABELS[status],
    imported: ["exact_imported", "sibling_imported", "path_imported", "fingerprint_imported"].includes(status),
    blocksDefaultImport: ["exact_imported", "path_imported", "fingerprint_imported"].includes(status),
    existingDocumentId: source.existing_document_id || source.primary_document_id || source.linked_document_id || existingDocument?.document_id || null,
    existingDocumentTitle: source.existing_document_title || existingDocument?.title || "",
    matchReasons,
    recommendedAction,
  };
}

export const ZOTERO_IMPORT_STATUS_LABELS = {
  exact_imported: "已入库（当前 PDF）",
  sibling_imported: "同书已有入库",
  path_imported: "路径已入库",
  fingerprint_imported: "指纹命中已入库",
  not_imported: "未入库",
  unknown: "入库状态未知",
};

export const ZOTERO_IMPORT_STATUS_RECOMMENDED_ACTION = {
  exact_imported: "open_existing_document",
  sibling_imported: "view_existing_document",
  path_imported: "open_existing_document",
  fingerprint_imported: "open_existing_document",
  not_imported: "select_for_import",
  unknown: "recheck_import_status",
};

export function deriveZoteroImportStatusFromReasons(matchReasons, fallbackStatus = "") {
  const reasons = new Set(matchReasons || []);
  if (reasons.has("same_zotero_attachment_key")) return "exact_imported";
  if (reasons.has("same_zotero_item_key")) return "sibling_imported";
  if (reasons.has("same_pdf_path")) return "path_imported";
  if (reasons.has("same_first_pages_fingerprint")) return "fingerprint_imported";
  if (fallbackStatus === "imported" || fallbackStatus === "partially_imported") return "exact_imported";
  if (fallbackStatus === "duplicate_candidate") return "unknown";
  return "not_imported";
}

export function zoteroPdfMatchReasons(source = {}) {
  source = source || {};
  const reasons = [];
  const add = reason => {
    const normalized = normalizeZoteroMatchReason(reason);
    if (normalized && !reasons.includes(normalized)) reasons.push(normalized);
  };
  (source.matching_reasons || []).forEach(add);
  if (source.match_reason) add(source.match_reason);
  (source.duplicate_check?.duplicate_reasons || []).forEach(add);
  (source.existing_documents || []).forEach(document => (document.matched_by || []).forEach(add));
  return reasons.length ? reasons : ["none"];
}

export function zoteroPdfImported(source = {}) {
  return zoteroPdfImportStatus(source).imported;
}

export function zoteroPdfDuplicate(source = {}) {
  return Boolean(source.duplicate_group_id || source.duplicate_count > 1 || source.duplicate || source.import_status === "duplicate_candidate" || source.import_status === "sibling_imported");
}

export function zoteroPdfExistingSummary(importStatus = {}) {
  if (!importStatus.existingDocumentId && !importStatus.existingDocumentTitle) return "";
  const title = importStatus.existingDocumentTitle || "未命名文档";
  return `已匹配到文档 #${importStatus.existingDocumentId || "?"}：${title}`;
}

export function zoteroPdfMatchReasonSummary(importStatus = {}) {
  const reasons = importStatus.matchReasons || ["none"];
  const primaryReason = reasons.find(reason => reason && reason !== "none") || "none";
  return `匹配原因：${zoteroPdfMatchReasonLabel(primaryReason)}`;
}

export function zoteroPdfMatchReasonLabel(reason = "none") {
  return {
    same_zotero_attachment_key: "当前 Zotero PDF 已入库",
    same_zotero_item_key: "同一 Zotero 条目已有入库",
    same_pdf_path: "PDF 路径一致",
    same_first_pages_fingerprint: "前三页指纹一致",
    none: "未发现已入库匹配",
  }[reason] || reason || "未发现已入库匹配";
}

export function recommendedActionDisplayLabel(action = "") {
  return {
    open_existing_document: "打开已有文档",
    view_existing_document: "查看已有文档",
    select_for_import: "选择该 PDF",
    recheck_import_status: "重新检查状态",
  }[action] || action || "未知";
}

function normalizeZoteroMatchReason(reason = "") {
  if (reason === "same_zotero_item_key_and_title") return "same_zotero_item_key";
  if (["same_zotero_attachment_key", "same_zotero_item_key", "same_pdf_path", "same_first_pages_fingerprint", "none"].includes(reason)) {
    return reason;
  }
  return reason || "";
}

function zoteroPdfExistingDocument(source = {}) {
  source = source || {};
  return (source.existing_documents || [])[0] || null;
}
