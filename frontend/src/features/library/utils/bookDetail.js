export async function copyTextToClipboard(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  document.body.removeChild(textarea);
}

export function apiErrorMessage(error, fallback) {
  const payload = error?.payload;
  const detail = payload?.detail || payload?.reason || payload?.message;
  const blockers = Array.isArray(payload?.current_blockers) && payload.current_blockers.length
    ? ` blockers=${payload.current_blockers.join(", ")}`
    : "";
  if (detail) return `${detail}${blockers}`;
  return error?.message || fallback;
}

export function chapterTitle(chapter) {
  return `第 ${chapter.chapter_index} 章 · ${chapter.title || "未命名章节"}`;
}

export function pageRange(chapter) {
  if (!chapter.pdf_page_start && !chapter.pdf_page_end) return "页码暂不可用";
  if (chapter.pdf_page_start === chapter.pdf_page_end || !chapter.pdf_page_end) return `p.${chapter.pdf_page_start}`;
  return `p.${chapter.pdf_page_start}-${chapter.pdf_page_end}`;
}

export function chapterListKey(documentId, chapter) {
  return chapter.chapter_id || `${documentId}-${chapter.chapter_index}-${chapter.pdf_page_start || "p"}-${chapter.title || "chapter"}`;
}

export function noteCorrectionScopeKey(chapter, scope = null) {
  const chapterId = String(chapter?.chapter_id || "");
  if (!chapterId) return "";
  const mode = scope?.review_mode || "full_chapter";
  if (mode === "section_scoped") return `${chapterId}:section:${scope.section_id || ""}`;
  if (mode === "fixed_size_batch") return `${chapterId}:batch:${scope.batch_size || 15}:${scope.batch_index || 0}`;
  return chapterId;
}

export function noteCorrectionPackageUrl(documentId, chapterId, scope = null) {
  const base = `/api/v1/library/books/${documentId}/chapters/${chapterId}/note-correction-package`;
  const mode = scope?.review_mode || "full_chapter";
  if (mode === "section_scoped") {
    return `${base}?mode=section_scoped&section_id=${encodeURIComponent(scope.section_id || "")}`;
  }
  if (mode === "fixed_size_batch") {
    return `${base}?mode=fixed_size_batch&batch_size=${scope.batch_size || 15}&batch_index=${scope.batch_index || 0}`;
  }
  return base;
}

export function noteCorrectionValidateRoute(documentId, chapterId, scope = null) {
  const mode = scope?.review_mode || "full_chapter";
  if (mode === "section_scoped") {
    return `/api/v1/library/books/${documentId}/chapters/${chapterId}/note-correction-review/validate-section`;
  }
  if (mode === "fixed_size_batch") {
    return `/api/v1/library/books/${documentId}/chapters/${chapterId}/note-correction-review/validate-batch`;
  }
  return `/api/v1/library/books/${documentId}/chapters/${chapterId}/note-correction-review/validate`;
}

export function noteCorrectionValidateBody(jsonText, scope = null) {
  const mode = scope?.review_mode || "full_chapter";
  if (mode === "section_scoped") {
    return { json_text: jsonText, section_id: scope.section_id || "" };
  }
  if (mode === "fixed_size_batch") {
    return {
      json_text: jsonText,
      batch_size: scope.batch_size || 15,
      batch_index: scope.batch_index || 0,
    };
  }
  return { json_text: jsonText };
}

export function chapteredDocumentTypeLabel(documentType, importMode) {
  if (documentType === "book") return "书籍";
  if (documentType === "thesis") return "学位论文";
  if (documentType === "report") return "报告";
  if (documentType === "paper" && importMode === "chaptered") return "论文 · 分章节导入";
  return "分章节文档";
}

export function chapterStatusLabel(status) {
  const labels = {
    not_started: "未开始",
    bundle_generated: "已生成包",
    json_pasted: "已粘贴 JSON",
    committed: "已提交",
    skipped: "已跳过",
  };
  return labels[status] || status || "未开始";
}
