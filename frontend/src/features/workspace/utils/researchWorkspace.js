import { getJson } from "../../../api/client.js";
import { buildNoteSourceTarget, buildPassageSourceTarget } from "../../../components/workspace/sourceTargets.js";

export const ROUTE_FALLBACK_NOTICE = "Workspace 数据暂不可用。请检查本地后端或返回搜索后重试。";

const EMPTY_WORKSPACE_SAFETY_FLAGS = Object.freeze({
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
});

export function buildEmptyWorkspaceState({
  documentId = null,
  chapterId = null,
  reason = "workspace_state_unavailable",
} = {}) {
  const safeDocumentId = positiveId(documentId);
  const safeChapterId = positiveId(chapterId);
  const safetyFlags = { ...EMPTY_WORKSPACE_SAFETY_FLAGS };
  return {
    status: "unavailable",
    document_id: safeDocumentId,
    chapter_id: safeChapterId,
    notebook_title: "Research Workspace",
    document_title: "",
    chapter_title: "",
    source_count: 0,
    document: safeDocumentId ? { document_id: safeDocumentId, title: "" } : null,
    current_chapter: safeChapterId
      ? {
          chapter_id: safeChapterId,
          chapter_index: null,
          title: "",
          page_start: null,
          page_end: null,
        }
      : null,
    source_ingestion_status: {
      pdf_available: false,
      chunked: false,
      chunk_count: 0,
      zotero_source_available: false,
    },
    notes_import_status: {
      status: "unavailable",
      existing: 0,
      user_notes: 0,
      evidence_only: 0,
    },
    search_layer_availability: {
      passages: "unavailable",
      notes: "unavailable",
      objects: "unavailable",
      relations: "unavailable",
      mechanisms: "unavailable",
    },
    graph_preview: {
      status: "unavailable",
      node_counts: {},
      nodes: [],
      edges: [],
    },
    workspace_resilience_fallback: {
      active: true,
      reason: String(reason || "workspace_state_unavailable"),
      warning: ROUTE_FALLBACK_NOTICE,
      scope: "empty_workspace",
    },
    safety_flags: safetyFlags,
    ...safetyFlags,
  };
}

export function buildWorkspaceNotebooks(sources = [], status = "ready") {
  return (Array.isArray(sources) ? sources : [])
    .filter((source) => positiveId(source?.document_id))
    .map((source) => buildWorkspaceNotebook(source, status));
}

export function buildWorkspaceNotebook(source = {}, status = "ready") {
  const title = String(source.title || "未命名资料").trim() || "未命名资料";
  const evidenceCount = Number(source.chunk_count ?? source.evidence_count ?? 0) || 0;
  const pending = status === "loading" || status === "error";
  return {
    id: `document-${positiveId(source.document_id)}`,
    title,
    subtitle: workspaceSourceTypeLabel(source.document_type),
    coverMark: workspaceCoverMark(title),
    sourceCountLabel: pending ? "资料状态暂不可用" : `${evidenceCount} 条证据`,
    warning: pending ? "本地 API 可用后自动更新" : "",
    source,
  };
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

export async function loadWorkspaceHome() {
  const readShelf = await getJson("/api/v1/library/read-shelf");
  const sources = readShelf.items || [];
  return { sources };
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
  return chapters.find((chapter) => Number(chapter.note_count || chapter.native_note_count || 0) > 0)
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
  const firstWithNotes = sections.find((section) => Number(section.candidate_count || 0) > 0);
  return firstWithNotes?.section_id || "";
}

function positiveId(value) {
  const number = Number(value);
  return Number.isSafeInteger(number) && number > 0 ? number : null;
}

function workspaceSourceTypeLabel(value) {
  return {
    book: "书籍",
    paper: "论文",
    article: "论文",
    thesis: "学位论文",
    report: "报告",
    pdf: "PDF",
    other: "PDF",
    unknown: "PDF",
  }[String(value || "unknown").toLowerCase()] || "本地资料";
}

function workspaceCoverMark(title) {
  const words = String(title || "").trim().split(/\s+/).filter(Boolean);
  if (words.length > 1 && words.every((word) => /^[A-Za-z0-9]/.test(word))) {
    return words.slice(0, 2).map((word) => word[0]).join("").toUpperCase();
  }
  return String(title || "S").trim().slice(0, 1).toUpperCase() || "S";
}
