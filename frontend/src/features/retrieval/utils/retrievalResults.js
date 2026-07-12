import { normalizeSourceIds, normalizeSourceRefs } from "../../../components/workspace/sourceTargets.js";

export function normalizePacketResults(results) {
  return asArray(results).filter((result) => result?.stable_id && (
    result.source_type === "pdf_chunk" || result.source_type === "zotero_note"
  ));
}

export function normalizeRelatedKeywords(keywords) {
  return asArray(keywords)
    .map((item) => (typeof item === "string" ? { keyword: item } : item))
    .filter((item) => item?.keyword)
    .slice(0, 8);
}

export function buildPacketQualitySummary(selectedResults, fallback = {}) {
  const pdfChunks = selectedResults.filter((result) => result.source_type === "pdf_chunk").length;
  const notes = selectedResults.filter((result) => result.source_type === "zotero_note").length;
  const documentIds = new Set(selectedResults.map((result) => result.document_id).filter(Boolean));
  const chapterKeys = new Set(
    selectedResults
      .map((result) => result.chapter_id || result.heading_path || result.section)
      .filter(Boolean)
  );
  return {
    pdf_chunks: pdfChunks,
    zotero_notes: notes,
    documents: documentIds.size,
    chapters: chapterKeys.size,
    selected_results: selectedResults.length,
    results_concentrated_in_single_document: documentIds.size === 1 && selectedResults.length > 1,
    missing_zotero_notes: notes === 0,
    missing_pdf_chunks: pdfChunks === 0,
    score_highest: selectedResults.length ? Math.max(...selectedResults.map((result) => Number(result.score || 0))) : null,
    score_lowest: selectedResults.length ? Math.min(...selectedResults.map((result) => Number(result.score || 0))) : null,
    risks: selectedResults.length ? selectedPacketRisks({ pdfChunks, notes, documentIds, selectedCount: selectedResults.length }) : asArray(fallback?.risks),
  };
}

export function selectedPacketRisks({ pdfChunks, notes, documentIds, selectedCount }) {
  const risks = [];
  if (documentIds.size === 1 && selectedCount > 1) risks.push("results_concentrated_in_single_document");
  if (notes === 0) risks.push("missing_zotero_notes");
  if (pdfChunks === 0) risks.push("missing_pdf_chunks");
  return risks;
}

export function buildEvidencePacketText(query, selectedResults, qualitySummary, relatedKeywords = []) {
  if (!selectedResults.length) return "";
  const blocks = [
    "# Research Evidence Packet",
    "",
    "查询词：",
    query || "",
    "",
    "检索范围：",
    `- PDF chunks: ${qualitySummary.pdf_chunks}`,
    `- user notes: ${qualitySummary.zotero_notes}`,
    `- documents: ${qualitySummary.documents}`,
    `- selected results: ${qualitySummary.selected_results}`,
    "",
    "召回质量摘要：",
    `- chapters: ${qualitySummary.chapters}`,
    `- result concentrated in one document: ${String(qualitySummary.results_concentrated_in_single_document)}`,
    `- missing notes: ${String(qualitySummary.missing_zotero_notes)}`,
    `- missing PDF chunks: ${String(qualitySummary.missing_pdf_chunks)}`,
    "",
  ];
  if (relatedKeywords.length) {
    blocks.push(
      "## Related keywords",
      ...relatedKeywords.map((item) => `- ${item.keyword}`),
      "",
    );
  }
  blocks.push(
    "## 证据包",
    "",
  );
  selectedResults.forEach((result) => {
    blocks.push(
      `### [${result.stable_id}]`,
      `source_type: ${result.source_type}`,
      `source: ${result.source || result.document_title || result.title || ""}`,
      `location: ${packetLocation(result)}`,
      `score: ${result.score}`,
      "content:",
      compactPacketText(result.content || result.note_text || result.selected_text || result.snippet || ""),
      "",
    );
  });
  blocks.push(
    "## 请完成",
    ...packetTaskInstructions(),
  );
  return blocks.join("\n").trim();
}

export function buildEvidencePacketJson(query, selectedResults, qualitySummary, relatedKeywords = []) {
  return {
    stage: "ResearchEvidencePacket-B",
    base_stage: "ResearchEvidencePacket-A",
    query: query || "",
    retrieval_scope: {
      pdf_chunks: qualitySummary.pdf_chunks,
      user_notes: qualitySummary.zotero_notes,
      documents: qualitySummary.documents,
      selected_results: qualitySummary.selected_results,
    },
    quality_summary: qualitySummary,
    related_keywords: relatedKeywords,
    results: selectedResults.map(packetJsonResult),
    task_instructions: packetTaskInstructions(),
    safety_flags: {
      db_write_performed: false,
      llm_called: false,
      relation_generated: false,
      mechanism_generated: false,
    },
  };
}

export function packetJsonResult(result) {
  return {
    stable_id: result.stable_id,
    source_type: result.source_type,
    source_entity_id: result.source_entity_id,
    title: result.title || "",
    source: result.source || result.document_title || result.title || "",
    document_title: result.document_title || "",
    document_id: result.document_id || null,
    linked_document_id: result.linked_document_id || null,
    chapter_id: result.chapter_id || null,
    chapter: result.chapter || "",
    section: result.section || "",
    heading_path: result.heading_path || "",
    page: result.page || null,
    score: result.score ?? null,
    location: packetLocation(result),
    snippet: result.snippet || "",
    content: result.content || result.note_text || result.selected_text || result.snippet || "",
    raw_text_ref: result.raw_text_ref || "",
    citation_token: result.citation_token || "",
    raw_metadata: result.raw_metadata || {},
  };
}

export function packetTaskInstructions() {
  return [
    "1. 按主题簇整理这些材料；",
    "2. 区分 PDF 原文证据、用户笔记理解和模型推断；",
    "3. 总结概念联系；",
    "4. 提炼可迁移机制或研究启发；",
    "5. 指出还需要继续搜索的关键词；",
    "6. 每个关键结论后引用对应 ID；",
    "7. 不要编造没有证据支持的结论。",
  ];
}

export function packetLocation(result) {
  const parts = [];
  if (result.heading_path || result.section || result.chapter) {
    parts.push(result.heading_path || result.section || result.chapter);
  }
  if (result.page) parts.push(`p.${result.page}`);
  if (result.source_type === "pdf_chunk" && result.source_entity_id) {
    parts.push(`chunk ${result.source_entity_id}`);
  }
  return parts.join(", ") || "location unavailable";
}

export function compactPacketText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

export function isPacketSelectableResult(result) {
  const type = result.retrieval_source_type || result.source_type || result.source_kind;
  return Boolean(result.stable_id && (type === "pdf_chunk" || type === "zotero_note" || type === "chunk" || type === "note" || type === "passage"));
}

export async function copyTextToClipboard(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  document.body.removeChild(textarea);
}

export function downloadTextFile(filename, text, mimeType) {
  const blob = new Blob([text], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

export function packetFilename(query, extension) {
  const slug = compactPacketText(query)
    .replace(/[^\w\u4e00-\u9fff-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48);
  return `${slug || "research-evidence-packet"}.${extension}`;
}

export function resultCitationFallback(result) {
  return `${sourceKindLabel(result.source_type || result.source_kind)} · ${
    result.page_label || (result.page ? `p.${result.page}` : "页码不可用")
  }`;
}

export function evidenceLabel(label) {
  if (label === "note_text") return "笔记";
  if (label === "selected_text") return "选中文本";
  if (label === "chunk_evidence_text") return "原文片段";
  return label;
}

export function sourceTargetFromResult(result = {}) {
  const existing = result.source_target || null;
  const locator = result.locator || null;
  if (existing && (existing.documentId || existing.matchedChunkId || existing.page)) {
    return locator ? mergeLocatorIntoTarget(existing, locator, result) : existing;
  }
  if (!locator) return null;
  const documentId = locator.document_id || result.document_id || result.source_trace?.document_id || null;
  const page = locator.pdf_page || result.page || result.source_trace?.pdf_page || null;
  const chunkId = locator.chunk_id || result.chunk_id || result.source_trace?.chunk_id || null;
  if (!documentId || (!page && !chunkId)) return null;
  return {
    sourceKind: sourceKindFromLocator(locator.source_type || result.source_type || result.source_kind),
    documentId,
    chapterId: locator.chapter_id || result.chapter_id || result.source_trace?.chapter_id || null,
    documentTitle: result.document_title || "",
    page,
    pageLabel: locator.page_label || result.page_label || (page ? `p.${page}` : ""),
    selectedText: locator.selected_text || result.selected_text || "",
    noteText: result.note_text || "",
    chunkEvidenceText: result.chunk_evidence_text || result.snippet || "",
    matchedChunkId: chunkId,
    chunkHeadingPath: result.heading_path || "",
    zoteroAnnotationKey: locator.zotero_annotation_key || result.zotero_annotation_key || "",
    serverNoteId: locator.server_note_id || result.server_note_id || result.note_id || "",
    clientNoteId: locator.client_note_id || result.client_note_id || "",
    objectCandidateId: locator.object_candidate_id || result.object_candidate_id || null,
    objectCandidateIds: normalizeSourceIds(
      result.matched_object_ids
      || result.object_candidate_ids
      || result.source_object_ids
      || locator.object_candidate_id
    ),
    bbox: locator.bbox || null,
    reviewedObjectRefs: normalizeSourceRefs(
      result.reviewed_object_refs
      || result.reviewedObjectRefs
      || result.candidate_temp_id
      || locator.candidate_temp_id
    ),
    alignmentStatus: result.alignment_status || "",
    alignmentConfidence: result.alignment_confidence || "",
    warnings: result.warnings || [],
    developerMeta: {
      source: "structured_retrieval_result",
      locator,
      source_trace: result.source_trace || {},
    },
  };
}

export function mergeLocatorIntoTarget(target, locator, result) {
  return {
    ...target,
    sourceKind: target.sourceKind || sourceKindFromLocator(locator.source_type),
    documentId: target.documentId || locator.document_id || result.source_trace?.document_id || null,
    chapterId: target.chapterId || locator.chapter_id || result.source_trace?.chapter_id || null,
    page: target.page || locator.pdf_page || null,
    pageLabel: target.pageLabel || locator.page_label || "",
    matchedChunkId: target.matchedChunkId || locator.chunk_id || null,
    objectCandidateId: target.objectCandidateId || locator.object_candidate_id || result.object_candidate_id || null,
    objectCandidateIds: target.objectCandidateIds?.length
      ? target.objectCandidateIds
      : normalizeSourceIds(
        result.matched_object_ids
        || result.object_candidate_ids
        || result.source_object_ids
        || locator.object_candidate_id
      ),
    reviewedObjectRefs: target.reviewedObjectRefs?.length
      ? target.reviewedObjectRefs
      : normalizeSourceRefs(
        result.reviewed_object_refs
        || result.reviewedObjectRefs
        || result.candidate_temp_id
        || locator.candidate_temp_id
      ),
    bbox: target.bbox || locator.bbox || null,
    selectedText: target.selectedText || locator.selected_text || "",
    developerMeta: {
      ...(target.developerMeta || {}),
      locator,
      source_trace: result.source_trace || {},
    },
  };
}

export function sourceKindFromLocator(sourceType) {
  if (sourceType === "chunk") return "passage";
  if (sourceType === "inspiration_note") return "note";
  if (sourceType === "object_candidate") return "object_evidence";
  if (sourceType === "relation_candidate") return "relation_evidence";
  if (sourceType === "mechanism") return "mechanism_evidence";
  return sourceType || "passage";
}

export function sourceKindLabel(sourceType) {
  if (sourceType === "pdf_chunk") return "原文片段";
  if (sourceType === "zotero_note") return "Zotero 笔记";
  if (sourceType === "chunk" || sourceType === "passage") return "原文片段";
  if (sourceType === "note" || sourceType === "inspiration_note") return "笔记";
  if (sourceType === "object_candidate") return "对象候选";
  if (sourceType === "relation_candidate") return "关系候选";
  if (sourceType === "mechanism_evidence" || sourceType === "mechanism") return "机制来源";
  return sourceType || "未知来源";
}

export function gateStatusLabel(status) {
  if (status === "locked") return "未启用";
  if (status === "available") return "可用";
  if (status === "planned") return "规划中";
  if (status === "unavailable") return "不可用";
  if (status === "reviewed") return "已审核";
  return status || "未知";
}

export function gateReasonLabel(reason) {
  if (!reason) return "";
  const labels = {
    locked: "未启用",
    available: "可用",
    planned: "规划中",
    unavailable: "不可用",
    relations_not_reviewed_phase7h: "关系候选仅 dry-run，Phase7H 尚未进入",
    objects_not_reviewed: "需要已审核对象",
    correction_review_not_saved: "需要已保存纠错审核",
    object_candidate_human_review_saved_relation_locked: "对象人工审核已保存，关系保存未启用",
    relation_candidate_dry_run_ready_future_phase7h_gate: "关系候选 dry-run 已就绪，Phase7H 未进入",
    no_notes_in_scope: "当前范围没有笔记",
    select_source_for_pdf_scope: "选择来源后可限定 PDF 范围",
    select_source_for_note_scope: "选择来源后可限定笔记范围",
    select_source_for_object_context: "选择来源后可限定对象上下文",
    select_source_for_relation_dry_run_context: "选择来源后可查看关系 dry-run 上下文",
    select_source_and_review_relations_first: "选择来源并审核关系后可进入机制 readiness",
    "Phase7H locked / not entered": "Phase7H 未进入",
    "mechanism locked": "机制生成未启用",
  };
  return labels[reason] || reason;
}

export function objectTypeLabel(value) {
  if (value === "object_candidate") return "对象候选";
  return value || "对象候选";
}

export function reviewActionLabel(value) {
  if (value === "approved") return "已通过";
  if (value === "rejected") return "已拒绝";
  if (value === "pending") return "待定";
  return value || "已通过";
}

export function asArray(value) {
  return Array.isArray(value) ? value : [];
}
