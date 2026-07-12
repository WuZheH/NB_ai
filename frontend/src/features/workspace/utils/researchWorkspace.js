import { getJson } from "../../../api/client.js";
import { buildNoteSourceTarget, buildPassageSourceTarget } from "../../../components/workspace/sourceTargets.js";

export const DEFAULT_HOME_WORKFLOW_TARGET = {
  documentId: 10,
  chapterId: 69,
  reason: "latest R3 reviewed workflow",
};

export const MACHINE_LEARNING_NOTEBOOK = {
  id: "machine-learning",
  title: "机器学习",
  subtitle: "概率机器学习与相关资料",
  coverMark: "ML",
  fallbackSourceCountLabel: "来源数量暂不可用",
};

export const ROUTE_FALLBACK_NOTICE = "部分数据暂不可用，本地 API 恢复后自动更新。";
export function buildDeterministicWorkspaceFallbackState({ documentId = 10, chapterId = 69, reason = "workspace_state_fallback" } = {}) {
  const safeDocumentId = Number(documentId) || 10;
  const safeChapterId = Number(chapterId) || 69;
  const safetyFlags = {
    db_write_performed: false,
    core_db_write_performed: false,
    llm_called: false,
    external_llm_called: false,
    relation_generated: false,
    relation_candidates_generated: false,
    mechanism_generated: false,
    mechanism_draft_written: false,
    zotero_write_performed: false,
    zotero_db_write_performed: false,
    vector_write_performed: false,
    vector_store_write_performed: false,
    object_candidates_generated: false,
    seed_apply_performed: false,
    ocr_or_marker_performed: false,
  };
  return {
    document_id: safeDocumentId,
    chapter_id: safeChapterId,
    notebook_title: "机器学习",
    document_title: "Probabilistic machine learning: an introduction",
    chapter_title: "8 Optimization",
    source_count: 10,
    document: {
      document_id: safeDocumentId,
      title: "Probabilistic machine learning: an introduction",
      zotero_item_key: "DW8Q4DWN",
      zotero_attachment_key: "EHB9L2P8",
    },
    current_chapter: {
      chapter_id: safeChapterId,
      chapter_index: 8,
      title: "8 Optimization",
      page_start: 305,
      page_end: 352,
    },
    source_ingestion_status: {
      pdf_available: true,
      chunked: true,
      chunk_count: 261,
      zotero_source_available: true,
    },
    notes_import_status: {
      status: "already_imported",
      existing: 68,
      user_notes: 67,
      evidence_only: 1,
      would_insert: 0,
      would_skip_existing: 68,
      would_block: 0,
      blocked_reason: null,
    },
    correction_review_status: {
      status: "saved",
      expected_items: 67,
      saved_items: 67,
      validated_items: 67,
      saved_sections: ["section_8_2", "section_8_3", "section_8_4", "section_8_5", "section_8_6", "section_8_7"],
      partial_saved_sections: ["section_8_2", "section_8_3", "section_8_4", "section_8_5", "section_8_6", "section_8_7"],
      missing_sections: [],
      ready_for_classification: true,
      classification_package_ready: true,
      classification_package_status: "ready_for_dry_run_preview",
      classification_review_saved: true,
      classification_review_status: "saved",
      classification_saved_item_count: 67,
      pn68_status: "saved",
      pn68_warning_preserved: true,
      pn68_reviewer_warning: "alignment_uncertain unmatched risk preserved; PN68 remains excluded from relation dry-run.",
      pn68_classification_label: "needs_manual_review",
      pn68_classification_confidence: "medium",
    },
    saved_review_state: {
      status: "saved",
      saved_item_count: 67,
      validated_item_count: 67,
      confirmed_count: 67,
      needs_followup_count: 0,
      final_note_text_count: 67,
      source_section_ids: ["section_8_2", "section_8_3", "section_8_4", "section_8_5", "section_8_6", "section_8_7"],
      partial_saved_sections: ["section_8_2", "section_8_3", "section_8_4", "section_8_5", "section_8_6", "section_8_7"],
      missing_sections: [],
      pn68_status: "saved",
      pn68_warning_preserved: true,
      ready_for_classification: true,
      classification_package_ready: true,
      classification_package_status: "ready_for_dry_run_preview",
      safety_flags: safetyFlags,
      ...safetyFlags,
    },
    classification_review_status: {
      status: "saved",
      saved_item_count: 67,
      source_item_count: 67,
      validation_status: "valid",
      ready_for_object_candidate_generation: true,
      object_candidate_generation_status: "requires_explicit_phase7d_gate",
      pn68_classification_label: "needs_manual_review",
      pn68_confidence: "medium",
      pn68_warning_preserved: true,
      ...safetyFlags,
    },
    save_readiness: {
      status: "ok",
      production_review_write_allowed: false,
      production_db_write_enabled: false,
      write_available: false,
      current_blockers: ["production_db_write_disabled"],
      safety_flags: safetyFlags,
      ...safetyFlags,
    },
    search_layer_availability: {
      passages: "available",
      notes: "reviewed",
      objects: "reviewed",
      relations: "locked",
      mechanisms: "locked",
    },
    object_candidate_dry_run_summary: {
      ready: true,
      candidate_count: 37,
      quarantined_count: 2,
      object_candidate_draft_review_status: "pending_human_review",
      object_candidate_draft_saved_count: 37,
      object_candidate_human_review_status: "saved",
      object_candidate_human_review_saved_count: 37,
      approved_candidate_count: 18,
      rejected_candidate_count: 9,
      pending_candidate_count: 10,
      relation_candidate_package_ready: true,
      relation_candidate_dry_run_status: "relation_candidate_dry_run_ready",
      relation_candidate_count: 73,
      relation_validator_valid: true,
      pn68_quarantined: true,
      pn68_excluded: true,
      phase7h_status: "locked_not_entered",
      relation_generated: false,
      mechanism_generated: false,
    },
    graph_preview: {
      status: "fallback_read_only_shell",
      node_counts: {
        evidence_chunks: 261,
        chapter_notes: 68,
        zotero_inspiration_notes: 104,
        approved_object_candidates: 18,
        rejected_object_candidates: 9,
        pending_object_candidates: 10,
        relation_dry_run_candidates: 73,
        mechanism_draft_candidates: 1,
        knowledge_relations: 0,
      },
      nodes: [],
      edges: [
        { id: "fallback-evidence-note", source: "evidence_overview", target: "note_overview", type: "supports" },
        { id: "fallback-note-object", source: "note_overview", target: "approved_objects", type: "reviewed_into" },
        { id: "fallback-object-relation", source: "approved_objects", target: "relation_dry_run", type: "dry_run_source" },
        { id: "fallback-pn68-relation", source: "pn68_quarantine", target: "relation_dry_run", type: "excluded_from" },
        { id: "fallback-relation-mechanism", source: "relation_dry_run", target: "mechanism_readiness", type: "locked_before" },
      ],
      pn68: {
        quarantined: true,
        excluded_from_relation_dry_run: true,
        positive_relation_source: false,
      },
      positive_relation_sources: {
        approved_object_candidates_only: true,
        rejected_candidates_included: false,
        pending_candidates_included: false,
        pn68_included: false,
      },
      phase7h_entered: false,
      relation_saved: false,
      mechanism_generated: false,
    },
    workflow: {
      correction: "67/67",
      classification: "67/67",
      object_drafts: 37,
      human_review: "18/9/10",
      relation_dry_run: 73,
      phase7h: "locked",
      mechanism: "locked",
      pn68: "excluded",
    },
    workspace_resilience_fallback: {
      active: true,
      reason,
      warning: ROUTE_FALLBACK_NOTICE,
      scope: "ui_shell_only",
    },
    safety_flags: safetyFlags,
    ...safetyFlags,
  };
}

export function buildMachineLearningNotebook(sources, status) {
  const sourceCount = Number(sources?.length || 0);
  const failed = status === "error";
  const loading = status === "loading";
  const pending = failed || loading;
  return {
    ...MACHINE_LEARNING_NOTEBOOK,
    sourceCount,
    sourceCountLabel: pending ? MACHINE_LEARNING_NOTEBOOK.fallbackSourceCountLabel : `共 ${sourceCount} 个来源`,
    warning: pending ? "本地 API 可用后自动更新" : "",
  };
}

export function openMachineLearningNotebook(onOpenWorkspace) {
  onOpenWorkspace?.({
    documentId: DEFAULT_HOME_WORKFLOW_TARGET.documentId,
    chapterId: DEFAULT_HOME_WORKFLOW_TARGET.chapterId,
  });
}

export function buildGraphFocusTarget(target = {}) {
  if (!target) return null;
  const sourceType = graphSourceType(target);
  return {
    ...target,
    selected_result_id: target.serverNoteId
      || target.clientNoteId
      || target.zoteroAnnotationKey
      || target.matchedChunkId
      || `${sourceType}-${target.page || "unknown"}`,
    locator_contract: target.developerMeta?.locator || null,
    source_type: sourceType,
    source_server_note_id: target.serverNoteId || "",
    chunk_id: target.matchedChunkId || null,
    object_candidate_id: target.objectCandidateId || null,
    relation_temp_id: target.relationTempId || null,
    mechanism_id: target.mechanismId || null,
    graphFocusNodeId: graphFocusNodeId(target, sourceType),
  };
}

export function graphSourceType(target = {}) {
  if (target.objectCandidateId || target.sourceKind === "object_evidence") return "object_candidate";
  if (target.relationTempId || target.sourceKind === "relation_evidence") return "relation_candidate";
  if (target.mechanismId) return "mechanism_readiness";
  if (target.sourceKind === "note" || target.serverNoteId || target.zoteroAnnotationKey) return "note";
  if (target.sourceKind === "passage" || target.matchedChunkId) return "evidence";
  return "evidence";
}

export function graphFocusNodeId(target = {}, sourceType = graphSourceType(target)) {
  if (target.graphFocusNodeId) return target.graphFocusNodeId;
  if (sourceType === "object_candidate") return "approved_objects";
  if (sourceType === "relation_candidate") return "relation_dry_run";
  if (sourceType === "mechanism_readiness") return "mechanism_readiness";
  if (sourceType === "note") return "note_overview";
  return "evidence_overview";
}

export async function loadDefaultWorkspaceHome({ documentId }) {
  const readShelf = await getJson("/api/v1/library/read-shelf");
  const sources = readShelf.items || [];
  const selectedSource = sources.find((item) => Number(item.document_id) === Number(documentId))
    || sources.find((item) => Number(item.document_id) === DEFAULT_HOME_WORKFLOW_TARGET.documentId)
    || sources[0]
    || null;
  const previewTarget = documentId
    ? await resolveWorkspacePreviewTarget(documentId)
    : DEFAULT_HOME_WORKFLOW_TARGET;
  let selectedBook = null;
  let workflowState = null;
  if (previewTarget?.documentId && previewTarget?.chapterId) {
    try {
      const [book, workspace] = await Promise.all([
        getJson(`/api/v1/library/books/${previewTarget.documentId}`).catch(() => null),
        getJson(`/api/v1/library/books/${previewTarget.documentId}/chapters/${previewTarget.chapterId}/workspace-state`),
      ]);
      selectedBook = book;
      workflowState = workspace;
    } catch {
      selectedBook = null;
      workflowState = null;
    }
  }
  return {
    sources,
    selectedSource,
    selectedBook,
    workflowState,
  };
}

export async function resolveWorkspacePreviewTarget(documentId) {
  try {
    const book = await getJson(`/api/v1/library/books/${documentId}`);
    const chapter = firstWorkspaceChapter(book);
    if (!chapter) return { documentId, chapterId: null, reason: "no chapter available" };
    return { documentId, chapterId: chapter.chapter_id, reason: "selected source first chapter" };
  } catch {
    return { documentId, chapterId: null, reason: "book detail unavailable" };
  }
}

export async function openSourceWorkspace(source, onOpenWorkspace) {
  if (!source?.document_id) return;
  try {
    const book = await getJson(`/api/v1/library/books/${source.document_id}`);
    const chapter = firstWorkspaceChapter(book);
    if (chapter?.chapter_id) {
      onOpenWorkspace?.({ documentId: source.document_id, chapterId: chapter.chapter_id });
      return;
    }
  } catch {
    // Non-chaptered PDFs stay visible in the source list until a chapter workspace exists.
  }
  onOpenWorkspace?.({ documentId: source.document_id, chapterId: null });
}

export function firstWorkspaceChapter(book = {}) {
  const chapters = book.chapters || [];
  return chapters.find((chapter) => Number(chapter.chapter_index) === 8)
    || chapters.find((chapter) => Number(chapter.note_count || chapter.native_note_count || 0) > 0)
    || chapters[0]
    || null;
}

export async function loadWorkspaceSourceSamples(documentId, chapterId, workspaceState) {
  const plan = await getJson(`/api/v1/library/books/${documentId}/chapters/${chapterId}/note-correction-review-plan`);
  const sectionId = selectSampleSectionId(plan);
  if (!sectionId) return [];
  const apiRoute = `/api/v1/library/books/${documentId}/chapters/${chapterId}/note-correction-package?mode=section_scoped&section_id=${encodeURIComponent(sectionId)}`;
  const packagePreview = await getJson(apiRoute);
  const packageJson = packagePreview.package_json || {};
  const candidates = (packageJson.correction_candidates || []).filter((candidate) => {
    return candidate?.selected_text || candidate?.note_text || candidate?.chunk_evidence_text || candidate?.matched_chunk_id;
  });
  const noteTargets = candidates.slice(0, 2).map((candidate) => buildNoteSourceTarget(candidate, workspaceState, {
    source: "real_api",
    apiRoute,
    reviewMode: "section_scoped",
    scopeId: sectionId,
  }));
  const passageTargets = candidates.slice(0, 1).map((candidate) => buildPassageSourceTarget(candidate, workspaceState, {
    source: "real_api",
    apiRoute,
    reviewMode: "section_scoped",
    scopeId: sectionId,
  }));
  return [...noteTargets, ...passageTargets];
}

export function selectSampleSectionId(plan = {}) {
  const sections = plan.sections || [];
  const preferred = sections.find((section) => section.section_id === "section_8_3" && Number(section.candidate_count || 0) > 0);
  if (preferred) return preferred.section_id;
  const firstWithNotes = sections.find((section) => Number(section.candidate_count || 0) > 0);
  return firstWithNotes?.section_id || "";
}
