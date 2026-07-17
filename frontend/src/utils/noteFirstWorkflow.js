export function buildBookNoteFirstGate(chapter = {}, unitLabel = "本章") {
  const annotationCount = Number(chapter.annotation_count ?? chapter.synced_note_count ?? 0);
  const syncedNoteCount = Number(chapter.synced_note_count ?? annotationCount);
  const userNoteCount = Number(chapter.user_note_count ?? 0);
  const evidenceOnlyCount = Number(chapter.evidence_only_count ?? 0);
  const objectCount = Number(chapter.object_count || 0);
  const reviewedObjectCount = ["committed", "accepted", "edited", "approved"].includes(chapter.object_import_status)
    ? objectCount
    : 0;
  return buildNoteFirstGateFromCounts({
    annotationCount,
    syncedNoteCount,
    userNoteCount,
    evidenceOnlyCount,
    objectCount,
    reviewedObjectCount,
    unitLabel,
  });
}

export function buildDocumentNoteFirstGate(noteSummary = {}, unitObjects = [], unitLabel = "本节") {
  const annotationCount = Number(noteSummary.annotationCount || 0);
  const userNoteCount = Number(noteSummary.userNoteCount || 0);
  const evidenceOnlyCount = Number(noteSummary.evidenceOnlyCount || 0);
  const reviewedObjectCount = unitObjects.filter((object) => ["accepted", "edited", "approved"].includes(object.review_status)).length;
  return buildNoteFirstGateFromCounts({
    annotationCount,
    syncedNoteCount: annotationCount,
    userNoteCount,
    evidenceOnlyCount,
    objectCount: unitObjects.length,
    reviewedObjectCount,
    unitLabel,
  });
}

export function buildBookChapterNoteFirstWorkflow(gate) {
  return [
    {
      number: 1,
      label: "读取/导入本章 Zotero notes 到 Search",
      contractLabel: "1 读取/导入本章 Zotero notes 到 Search",
      status: gate.hasSyncedNotes ? "done" : "locked",
      statusLabel: gate.hasSyncedNotes ? "done" : "locked",
      reason: gate.syncReason,
    },
    {
      number: 2,
      label: "生成本章笔记纠错包",
      contractLabel: "2 生成本章笔记纠错包",
      status: gate.canCorrectNotes ? "ready" : "locked",
      statusLabel: gate.canCorrectNotes ? "ready" : "locked",
      reason: gate.noteCorrectionReason,
    },
    {
      number: 3,
      label: "note_correction_review：笔记纠错审核",
      contractLabel: "3 note_correction_review：笔记纠错审核",
      status: gate.canCorrectNotes ? "pending" : "locked",
      statusLabel: gate.canCorrectNotes ? "pending" : "locked",
      reason: gate.noteCorrectionReviewReason,
    },
    { number: 4, label: "生成本章笔记分类包", contractLabel: "4 生成本章笔记分类包", status: "locked", statusLabel: "locked", reason: gate.noteClassificationReason },
    { number: 5, label: "note_classification_review：笔记分类审核", contractLabel: "5 note_classification_review：笔记分类审核", status: "locked", statusLabel: "locked", reason: gate.noteClassificationReviewReason },
    { number: 6, label: "生成对象候选：笔记 / 高光 / 全文章节", contractLabel: "6 生成三路对象候选包", status: "locked", statusLabel: "planned / not_implemented", reason: gate.objectCandidateReason },
    { number: 7, label: "object_review：对象审核", contractLabel: "7 object_review：对象审核", status: "locked", statusLabel: "locked", reason: gate.objectReviewReason },
    { number: 8, label: "生成双源机制候选包", contractLabel: "8 生成双源机制候选包", status: "locked", statusLabel: "locked", reason: gate.mechanismCandidateReason },
    { number: 9, label: "mechanism_review：机制审核", contractLabel: "9 mechanism_review：机制审核", status: "locked", statusLabel: "locked", reason: gate.mechanismReviewReason },
  ];
}

export function objectCandidateBlockReason({ hasSyncedNotes, userNoteCount, evidenceOnlyCount }) {
  const noteGate = userNoteCount > 0 ? "note_anchored_waits_note_correction_and_classification" : "note_anchored_no_user_notes";
  const highlightGate = evidenceOnlyCount > 0 || hasSyncedNotes
    ? "highlight_anchored_planned_not_implemented"
    : "highlight_anchored_no_highlight_evidence";
  const chapterGate = "chapter_global_planned_not_implemented";
  return `${noteGate}; ${highlightGate}; ${chapterGate}; highlight_and_chapter_global_sources_planned_not_implemented; unified_object_review_required`;
}

function buildNoteFirstGateFromCounts({
  annotationCount,
  syncedNoteCount,
  userNoteCount,
  evidenceOnlyCount,
  objectCount,
  reviewedObjectCount,
  unitLabel,
}) {
  const hasSyncedNotes = syncedNoteCount > 0 || annotationCount > 0;
  const canCorrectNotes = userNoteCount > 0;
  const canGenerateObjects = false;
  const objectCandidateReason = objectCandidateBlockReason({ hasSyncedNotes, userNoteCount, evidenceOnlyCount });
  return {
    annotationCount,
    syncedNoteCount,
    userNoteCount,
    evidenceOnlyCount,
    reviewedObjectCount,
    hasSyncedNotes,
    canCorrectNotes,
    canGenerateObjects,
    syncReason: hasSyncedNotes ? "notes_imported_or_existing_in_notebook_ai" : "no_zotero_notes",
    noteCorrectionReason: canCorrectNotes ? "ready_for_note_correction_package" : "no_user_notes_for_note_review",
    noteCorrectionReviewReason: canCorrectNotes ? "note_correction_review_pending_save" : "no_user_notes_for_note_review",
    noteClassificationReason: canCorrectNotes ? "note_correction_review_not_saved; notes_not_corrected" : "no_user_notes_for_note_review",
    noteClassificationReviewReason: canCorrectNotes ? "note_correction_review_not_saved; notes_not_corrected" : "no_user_notes_for_note_review",
    objectCandidateReason,
    objectReviewReason: objectCount ? "object_review_required" : "object_schema_ready_but_no_candidates",
    mechanismCandidateReason: reviewedObjectCount ? "mechanism_source_pack_required" : "mechanism_blocked_until_objects_reviewed",
    mechanismReviewReason: reviewedObjectCount ? "mechanism_review_required" : "mechanism_blocked_until_objects_reviewed",
    unitLabel,
  };
}
