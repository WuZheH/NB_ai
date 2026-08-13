import { buildStudioCardStates } from "./workspaceStudioAdapter.js";

export const WORKSPACE_PIPELINE_STATUSES = [
  "no_zotero_notes",
  "blocked_no_notes_in_scope",
  "notes_not_imported",
  "dry_run_ready",
  "import_required",
  "already_imported",
  "correction_review_ready",
  "correction_review_in_progress",
  "correction_review_partially_saved",
  "blocked_save_gate",
  "correction_review_complete",
  "ready_for_classification",
];

export function normalizeWorkspaceState(state = {}) {
  const source = state.source_ingestion_status || {};
  const notes = state.notes_import_status || {};
  const correction = state.correction_review_status || {};
  const classification = state.classification_review_status || {};
  const readiness = state.save_readiness || {};
  const saved = state.saved_review_state || {};
  const blockers = readiness.current_blockers || [];
  const noNotes = notes.status === "blocked_no_notes_in_scope" || correction.status === "locked_no_notes_in_scope";
  const saveBlocked = !noNotes && readiness.production_review_write_allowed === false;
  const pipelineStatus = normalizePipelineStatus({ notes, correction, saved, noNotes, saveBlocked });
  const importStatusLabel = noNotes ? "NO_NOTES_IN_SCOPE" : labelizeStatus(notes.status || "notes_not_imported");
  const correctionStatusLabel = noNotes ? "未启用" : labelizeCorrectionStatus(correction.status || saved.status || "not_saved");
  const saveBlockerText = blockers.join(", ") || "write_not_allowed";
  const saveReadOnlyLine = saveBlocked ? readOnlySaveLine(saveBlockerText) : "";

  return {
    source,
    notes,
    correction,
    classification,
    readiness,
    saved,
    blockers,
    noNotes,
    saveBlocked,
    pipelineStatus,
    sourceDisplay: {
      pdfStatusLine: `PDF 来源：${source.pdf_available ? "可用" : "缺失"}`,
      chunksLine: `原文片段：${source.chunked ? `${Number(source.chunk_count || 0)} 条` : "尚未切分"}`,
      zoteroNotesLine: noNotes ? "Zotero 笔记：NO_NOTES_IN_SCOPE" : `Zotero 笔记：已关联 ${Number(notes.existing || 0)} 条`,
      userNotesLine: `用户笔记：${Number(notes.user_notes || 0)} 条`,
      evidenceOnlyLine: `仅证据笔记：${Number(notes.evidence_only || 0)} 条`,
      importStatusLine: `导入状态：${importStatusLabel}`,
      notesLayerLine: noNotes ? "笔记层不可用" : notesLayerLabel(state.search_layer_availability?.notes),
      correctionLine: `纠错审核：${correctionStatusLabel}`,
      saveBlockedLine: saveReadOnlyLine,
    },
    workflow: buildWorkflowDisplay({ notes, noNotes, correctionStatusLabel, saveBlocked, saveBlockerText }),
    searchLayers: buildSearchLayerDisplay(state, { noNotes, source, saved, classification }),
    studioCards: buildStudioCardStates(state),
  };
}

function normalizePipelineStatus({ notes, correction, saved, noNotes, saveBlocked }) {
  if (noNotes) return "blocked_no_notes_in_scope";
  if (!notes.status || notes.status === "no_zotero_notes") return "no_zotero_notes";
  if (notes.status === "notes_not_imported") return "notes_not_imported";
  if (notes.status === "dry_run_ready") return "dry_run_ready";
  if (notes.status === "import_required") return "import_required";
  if (saved.ready_for_classification) return "ready_for_classification";
  if (correction.status === "saved" || saved.status === "saved") return "correction_review_complete";
  if (correction.status === "partial" || saved.status === "partial") return "correction_review_partially_saved";
  if (correction.status === "in_progress") return "correction_review_in_progress";
  if (correction.status === "ready") return "correction_review_ready";
  if (saveBlocked) return "blocked_save_gate";
  return notes.status === "already_imported" ? "already_imported" : notes.status;
}

function buildWorkflowDisplay({ notes, noNotes, correctionStatusLabel, saveBlocked, saveBlockerText }) {
  if (noNotes) {
    return {
      status: "locked",
      headline: "本章没有 Zotero 笔记",
      body: "当前章节没有可审核笔记，笔记纠错流程未启用。",
      cta: "打开高级流程",
    };
  }
  return {
    status: saveBlocked ? "blocked" : "available",
    headline: `已关联 ${Number(notes.existing || 0)} 条笔记 · 纠错审核${correctionStatusLabel}`,
    body: saveBlocked ? readOnlySaveLine(saveBlockerText) : "可在高级流程中继续审核笔记纠错。",
    cta: "继续高级流程",
  };
}

function readOnlySaveLine(blockerText) {
  if (blockerText === "production_db_write_disabled") return "只读模式：未写入数据库";
  return `保存未启用：${blockerText}`;
}

function buildSearchLayerDisplay(state, { noNotes, source, saved, classification }) {
  const readyForClassification = Boolean(saved.ready_for_classification);
  const classificationSaved = Boolean(classification?.status === "saved");
  const objectDryRunReady = Boolean(state.object_candidate_dry_run_summary?.ready);
  const objectDraftSaved = state.object_candidate_dry_run_summary?.object_candidate_draft_review_status === "pending_human_review"
    || Number(state.object_candidate_dry_run_summary?.object_candidate_draft_saved_count || 0) > 0;
  const objectHumanReviewSaved = state.object_candidate_dry_run_summary?.object_candidate_human_review_status === "saved"
    || Number(state.object_candidate_dry_run_summary?.object_candidate_human_review_saved_count || 0) > 0;
  const relationDryRunReady = state.object_candidate_dry_run_summary?.relation_candidate_package_ready === true
    || state.object_candidate_dry_run_summary?.relation_candidate_dry_run_status === "relation_candidate_dry_run_ready"
    || Number(state.object_candidate_dry_run_summary?.relation_candidate_count || 0) > 0;
  return [
    {
      id: "objects",
      title: "对象",
      status: objectHumanReviewSaved ? "reviewed" : "locked",
      reason: noNotes
        ? "当前范围没有笔记"
        : !readyForClassification
          ? "需要已保存纠错审核"
          : objectHumanReviewSaved
            ? relationDryRunReady
              ? "关系候选 dry-run 已就绪，Phase7H 未进入"
              : "对象人工审核已保存，关系保存未启用"
          : objectDraftSaved
            ? "对象候选草稿等待人工审核"
          : objectDryRunReady
            ? "对象候选 dry-run 已就绪，需要显式保存 gate"
          : classificationSaved
            ? "需要显式对象候选 gate"
            : "笔记分类尚未完成",
    },
    {
      id: "relations",
      title: "关系",
      status: "locked",
      reason: noNotes ? "当前范围没有笔记" : relationDryRunReady ? "关系候选 dry-run 已就绪，Phase7H 未进入" : objectHumanReviewSaved ? "关系 dry-run 尚未开始" : "需要已审核对象",
    },
    {
      id: "notes",
      title: "笔记",
      status: noNotes ? "unavailable" : (state.search_layer_availability?.notes || "raw_unreviewed"),
      reason: noNotes ? "不可用：本章没有笔记，不显示为假 0 结果" : "审核已保存，保留原始笔记证据",
    },
    {
      id: "mechanisms",
      title: "机制 / insight",
      status: "locked",
      reason: noNotes ? "当前范围没有笔记" : relationDryRunReady ? "关系候选仅 dry-run，Phase7H 尚未进入" : objectHumanReviewSaved ? "需要已审核关系" : "需要已审核对象和关系",
    },
    {
      id: "passages",
      title: "片段",
      status: source.chunked ? "available" : "unavailable",
      reason: source.chunked ? "来自已切分 PDF，可定位到页面/片段" : "需要已切分 PDF",
    },
  ];
}

function labelizeStatus(status) {
  if (status === "already_imported") return "已导入";
  return String(status || "unknown").replace(/_/g, " ");
}

function labelizeCorrectionStatus(status) {
  if (status === "not_saved") return "未保存";
  if (status === "locked_no_notes_in_scope") return "未启用";
  if (status === "partial") return "部分保存";
  if (status === "saved") return "已保存";
  return labelizeStatus(status);
}

function notesLayerLabel(status) {
  if (status === "raw_unreviewed") return "笔记层：原始未审核";
  if (status === "partial_reviewed") return "笔记层：部分审核";
  if (status === "reviewed") return "笔记层：已审核";
  return `笔记层：${status || "unknown"}`;
}
