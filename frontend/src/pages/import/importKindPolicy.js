import { normalizeIdentityText } from "../../shared/utils/display.js";

export function deriveDocumentKindForImport(input = {}) {
  const classification = input.classification || {};
  const signals = {
    ...(classification.signals || {}),
    ...(input.signals || {}),
  };
  const title = [
    input.title,
    input.titleHint,
    classification.title,
    input.selectedZoteroSource?.title,
  ].filter(Boolean).join(" ");
  const pageCount = Number(signals.page_count ?? input.page_count ?? input.selectedZoteroSource?.page_count ?? 0);
  const outlineChapterCount = Number(
    signals.outline_chapter_count
      ?? input.outline_chapter_count
      ?? input.selectedZoteroSource?.outline_chapter_count
      ?? 0
  );
  const zoteroItemType = String(
    input.zoteroItemType
      || input.zotero_item_type
      || signals.zotero_item_type
      || input.selectedZoteroSource?.zotero_item_type
      || input.selectedZoteroSource?.item_type
      || input.selectedZoteroSource?.itemType
      || ""
  ).trim();

  if (hasExplicitBookTitleSignal(title)) return "book";
  if (pageCount >= 120 && outlineChapterCount >= 5) return "book";
  if (isBookZoteroItemType(zoteroItemType)) return "book";

  const explicitDocumentType = normalizeDocumentKind(input.explicitDocumentType);
  if (explicitDocumentType) return explicitDocumentType;

  const classifiedKind = normalizeDocumentKind(classification.document_type);
  return classifiedKind || "paper";
}

export function derivePrimaryImportAction(kind) {
  if (kind === "book") return "导入整本书到知识库";
  if (kind === "paper") return "导入整篇论文到知识库";
  return "导入全文到知识库";
}

export function deriveConfirmationContext(kind) {
  if (kind === "book") return "import_full_book_after_preview";
  if (kind === "paper") return "import_whole_paper_after_preview";
  return "import_full_document_after_preview";
}

export function importConfirmationContext(kind) {
  return deriveConfirmationContext(kind);
}

export function documentKindDisplayLabel(kind) {
  if (kind === "book") return "书籍";
  if (kind === "paper") return "论文";
  if (kind === "thesis") return "学位论文";
  if (kind === "report") return "报告";
  if (kind === "other") return "其他";
  return "未知";
}

export function confirmationContextLabel(context) {
  return {
    import_full_book_after_preview: "整本书导入确认",
    import_whole_paper_after_preview: "整篇论文导入确认",
    import_full_document_after_preview: "全文导入确认",
  }[context] || "全文导入确认";
}

function normalizeDocumentKind(kind) {
  const value = String(kind || "").trim().toLowerCase();
  if (value === "book") return "book";
  if (value === "paper") return "paper";
  if (value === "other" || value === "thesis" || value === "report") return "other";
  return "";
}

function hasExplicitBookTitleSignal(title = "") {
  const normalized = normalizeIdentityText(title).replace(/_/g, " ");
  return normalized.includes("probabilistic machine learning")
    || normalized.includes("deep learning");
}

function isBookZoteroItemType(itemType = "") {
  const normalized = String(itemType || "").trim().toLowerCase();
  return normalized === "book" || normalized === "monograph";
}
