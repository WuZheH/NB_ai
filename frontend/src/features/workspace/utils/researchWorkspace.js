import { getJson } from "../../../api/client.js";

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
