export function decisionMessage(importReadiness) {
  if (importReadiness.recommended_route === "already_imported") {
    return "当前来源已经绑定到已有文档，默认打开已有文档，阻止重复写入 documents/chunks。";
  }
  if (importReadiness.recommended_route === "converted_md") {
    return "PyMuPDF 轻量预检不可用时，主流程自动切换到匹配的 converted_md / Marker Markdown。";
  }
  if (importReadiness.recommended_route === "chapter_import") {
    return "当前识别为书籍或章节化文档。书籍必须先做章节预览，再选择章节导入。";
  }
  if (importReadiness.recommended_route === "whole_paper") {
    return importReadiness.quality_good
      ? "论文按整篇导入。文本层质量通过时可直接确认导入，预览仍可展开检查。"
      : "论文按整篇导入。质量不确定时请先查看并排预览或生成 Markdown。";
  }
  if (importReadiness.recommended_route === "generate_markdown_first") {
    return "当前没有可用 Markdown fallback。点击“生成 Markdown”会只针对当前 PDF 生成 converted_md，成功后不会自动导入。";
  }
  if (importReadiness.recommended_route === "normal_text_layer") {
    return "当前推荐使用 PDF 文本层导入。请先生成导入前预览，再执行最终导入。";
  }
  return "请根据阻塞原因完成下一步。";
}

export function qualityStatusLabel(status) {
  return {
    good: "良好",
    uncertain: "较差",
    blocked: "需要 OCR",
    not_checked: "未预检",
  }[status] || "未知";
}

export function deviceLabel(device) {
  const value = String(device || "").toLowerCase();
  if (value === "cuda" || value === "gpu") return "CUDA";
  if (value === "cpu") return "CPU";
  return "未知";
}

export function importJobStatusLabel(status) {
  return {
    idle: "等待开始",
    running: "运行中",
    queued: "等待开始",
    previewing: "正在生成预览",
    committing: "正在写入知识库",
    completed: "完成",
    success: "完成",
    already_committed: "已入库",
    failed: "失败",
    cancelled: "已取消",
  }[status] || "未知";
}

export function cacheStatusLabel(status) {
  return {
    available: "可用",
    exists: "文件存在",
    missing: "不可用",
    imported: "已入库",
    duplicate: "可能重复",
    unknown: "未知",
  }[status] || status || "未知";
}

export function duplicateConfidenceLabel(value) {
  return confidenceLabel(value);
}

export function finalImportUnitLabel(value) {
  return {
    whole_book: "整本书",
    whole_paper: "整篇论文",
    full_document: "全文",
    already_imported: "已有文档",
    selected_chapters: "整本书",
  }[value] || "未知";
}

export function nativeNotesSummary(summary) {
  if (!summary) return "未返回同步摘要";
  if (summary.attempted === false) {
    return summary.message || "未发现 Zotero attachment，跳过笔记同步";
  }
  const imported = Number(summary.imported_count || 0);
  const skipped = Number(summary.skipped_existing_count || 0);
  const blocked = Number(summary.blocked_count || 0);
  const wouldImport = Number(summary.would_import_count || 0);
  if (summary.apply === false && wouldImport > 0) {
    return `dry-run：可导入 ${wouldImport} 条，已存在 ${skipped} 条，blocked ${blocked} 条`;
  }
  return `已导入 ${imported} 条，已存在 ${skipped} 条，blocked ${blocked} 条`;
}

export function documentTypeLabel(value) {
  return {
    paper: "论文",
    book: "书籍",
    thesis: "学位论文",
    report: "报告",
    other: "其他",
  }[value] || value || "未知";
}

export function importModeLabel(value) {
  return {
    full_document: "整篇导入",
    chaptered: "入库后按章/节处理",
  }[value] || value || "未知";
}

export function confidenceLabel(value) {
  return {
    high: "高置信度",
    medium: "中置信度",
    low: "低置信度",
  }[value] || "未知置信度";
}

export function stageLabel(stage) {
  return {
    queued: "等待开始",
    classifying: "正在识别文献类型",
    parsing_pdf: "正在解析 PDF 与识别章节",
    detecting_chapters: "已完成章节检测",
    writing_db: "正在写入资料库",
    verifying: "正在校验导入结果",
    cancelling: "正在取消",
    completed: "导入完成",
    failed: "导入失败",
    cancelled: "已取消",
    preflight: "导入前预检",
    "commit-paper": "整篇写入",
    "commit-book": "整本书写入",
    "full-document-commit-blocked": "全文写入阻断",
    preview: "导入前预览",
    committing: "正在写入知识库",
  }[stage] || stage || "未知阶段";
}

export function basename(path) {
  if (!path) return "—";
  const parts = path.replace(/\\/g, "/").split("/");
  return parts[parts.length - 1] || path;
}
