export const SOURCE_TARGET_FIELD_NAMES = [
  "sourceKind",
  "documentId",
  "chapterId",
  "documentTitle",
  "page",
  "pageLabel",
  "selectedText",
  "noteText",
  "chunkEvidenceText",
  "matchedChunkId",
  "chunkHeadingPath",
  "zoteroAnnotationKey",
  "serverNoteId",
  "clientNoteId",
  "objectCandidateId",
  "objectCandidateIds",
  "reviewedObjectRefs",
  "bbox",
  "alignmentStatus",
  "alignmentConfidence",
  "warnings",
  "developerMeta",
];

export function buildChapterSourceTarget(workspaceState = {}) {
  const document = workspaceState.document || {};
  const chapter = workspaceState.current_chapter || {};
  return {
    sourceKind: "chapter",
    documentId: document.document_id || null,
    chapterId: chapter.chapter_id || null,
    documentTitle: document.title || "",
    page: chapter.page_start || null,
    pageLabel: chapter.page_start ? `p.${chapter.page_start}-${chapter.page_end || chapter.page_start}` : "",
    selectedText: "",
    noteText: "",
    chunkEvidenceText: "",
    matchedChunkId: null,
    chunkHeadingPath: chapter.title || "",
    zoteroAnnotationKey: "",
    serverNoteId: "",
    clientNoteId: "",
    objectCandidateId: null,
    objectCandidateIds: [],
    reviewedObjectRefs: [],
    bbox: null,
    alignmentStatus: "",
    alignmentConfidence: "",
    warnings: [],
    developerMeta: {
      source: "workspace_state",
      current_chapter: chapter,
      source_ingestion_status: workspaceState.source_ingestion_status || {},
    },
  };
}

export function buildNoteSourceTarget(candidate = {}, workspaceState = {}, meta = {}) {
  const document = workspaceState.document || {};
  const chapter = workspaceState.current_chapter || {};
  return {
    sourceKind: "note",
    documentId: candidate.matched_document_id || document.document_id || null,
    chapterId: chapter.chapter_id || null,
    documentTitle: document.title || "",
    page: candidate.page || candidate.chunk_page_start || null,
    pageLabel: candidate.page_label || (candidate.page ? `p.${candidate.page}` : ""),
    selectedText: candidate.selected_text || candidate.selected_text_preview || "",
    noteText: candidate.note_text || "",
    chunkEvidenceText: candidate.chunk_evidence_text || "",
    matchedChunkId: candidate.matched_chunk_id || null,
    chunkHeadingPath: candidate.chunk_heading_path || "",
    zoteroAnnotationKey: candidate.zotero_annotation_key || "",
    serverNoteId: candidate.server_note_id || candidate.note_id || "",
    clientNoteId: candidate.client_note_id || "",
    objectCandidateId: candidate.object_candidate_id || null,
    objectCandidateIds: normalizeSourceIds(
      candidate.matched_object_ids
      || candidate.object_candidate_ids
      || candidate.matched_object_ids_json
    ),
    reviewedObjectRefs: normalizeSourceRefs(
      candidate.reviewed_object_refs
      || candidate.reviewedObjectRefs
      || candidate.candidate_temp_id
    ),
    bbox: normalizeSourceBbox(candidate.bbox || candidate.bbox_json || candidate.position || candidate.position_json),
    alignmentStatus: candidate.evidence_alignment_status || "",
    alignmentConfidence: candidate.alignment_confidence || "",
    warnings: candidate.warnings || [],
    developerMeta: {
      source: meta.source || "real_api",
      apiRoute: meta.apiRoute || "",
      reviewMode: meta.reviewMode || "",
      scopeId: meta.scopeId || "",
      sourceKindNote: candidate.source || "",
      matched_chunk_ids: candidate.matched_chunk_ids || [],
      anchor_method: candidate.anchor_method || "",
      reviewer_warning: candidate.reviewer_warning || "",
    },
  };
}

export function buildPassageSourceTarget(candidate = {}, workspaceState = {}, meta = {}) {
  const noteTarget = buildNoteSourceTarget(candidate, workspaceState, meta);
  return {
    ...noteTarget,
    sourceKind: "passage",
    noteText: "",
    selectedText: candidate.selected_text || candidate.selected_text_preview || "",
    developerMeta: {
      ...noteTarget.developerMeta,
      sourceKind: "passage_from_note_candidate",
    },
  };
}

export function sourceTargetSummary(target = {}) {
  return {
    sourceKind: target.sourceKind,
    documentId: target.documentId,
    chapterId: target.chapterId,
    page: target.page,
    pageLabel: target.pageLabel,
    matchedChunkId: target.matchedChunkId,
    zoteroAnnotationKey: target.zoteroAnnotationKey,
    serverNoteId: target.serverNoteId,
    clientNoteId: target.clientNoteId,
    objectCandidateId: target.objectCandidateId,
    objectCandidateIds: target.objectCandidateIds || [],
    reviewedObjectRefs: target.reviewedObjectRefs || [],
    alignmentStatus: target.alignmentStatus,
    alignmentConfidence: target.alignmentConfidence,
    warnings: target.warnings || [],
    developerMeta: target.developerMeta || {},
  };
}

export function normalizeSourceBbox(value) {
  if (!value) return null;
  if (typeof value === "string") {
    try {
      return normalizeSourceBbox(JSON.parse(value));
    } catch {
      return null;
    }
  }
  if (Array.isArray(value)) {
    return { format: "zotero_reader_rects_v1", rects: value };
  }
  if (value.rects && Array.isArray(value.rects)) {
    return {
      format: value.format || "zotero_reader_rects_v1",
      rects: value.rects,
      pdf_page: value.pdf_page || value.page || null,
      page_label: value.page_label || "",
    };
  }
  if (value.pageIndex != null || value.position || value.rects) {
    return {
      format: "zotero_reader_rects_v1",
      rects: Array.isArray(value.rects) ? value.rects : [],
      pdf_page: value.pdf_page || value.page || (Number.isFinite(Number(value.pageIndex)) ? Number(value.pageIndex) + 1 : null),
      page_label: value.pageLabel || value.page_label || "",
      raw_position: value,
    };
  }
  return null;
}

export function normalizeSourceIds(value) {
  let items = value;
  if (typeof items === "string") {
    try {
      items = JSON.parse(items);
    } catch {
      items = [];
    }
  }
  if (items == null || items === "") return [];
  if (!Array.isArray(items)) items = [items];
  const result = [];
  items.forEach((item) => {
    const number = Number(item);
    if (Number.isFinite(number) && number > 0 && !result.includes(number)) {
      result.push(number);
    }
  });
  return result;
}


export function normalizeSourceRefs(value) {
  let items = value;
  if (typeof items === "string") {
    try {
      items = JSON.parse(items);
    } catch {
      items = [items];
    }
  }
  if (items == null || items === "") return [];
  if (!Array.isArray(items)) items = [items];
  const result = [];
  items.forEach((item) => {
    const ref = String(item || "").trim();
    if (ref && !result.includes(ref)) {
      result.push(ref);
    }
  });
  return result;
}

export function buildSourceSelectionKey(target) {
  if (!target) return "none";
  return JSON.stringify({
    sourceKind: target.sourceKind || "",
    documentId: Number(target.documentId) || null,
    chapterId: Number(target.chapterId) || null,
    page: Number(target.page) || null,
    matchedChunkId: Number(target.matchedChunkId) || null,
    serverNoteId: target.serverNoteId || "",
    clientNoteId: target.clientNoteId || "",
    objectCandidateId: target.objectCandidateId || null,
    objectCandidateIds: [...normalizeSourceIds(target.objectCandidateIds)].sort((a, b) => a - b),
    reviewedObjectRefs: [...normalizeSourceRefs(target.reviewedObjectRefs)].sort(),
  });
}
