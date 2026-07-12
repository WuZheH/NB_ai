export const STUDIO_SAFETY_BADGES = [
  "不调用 LLM",
  "不写 DB",
  "不写 Zotero",
  "不生成",
];

export const STUDIO_CARD_DEFINITIONS = [
  {
    id: "object_graph",
    title: "对象图谱",
    description: "查看概念、方法、数据集与问题对象的审核状态。",
    preconditions: [
      "笔记已导入",
      "纠错审核已保存",
      "笔记分类已审核",
      "对象候选已审核",
    ],
    nextAction: "打开高级流程",
    safetyBoundary: "本步骤不生成对象",
  },
  {
    id: "relation_graph",
    title: "关系图谱",
    description: "查看已审核对象之间的关系候选状态。",
    preconditions: [
      "纠错审核已保存",
      "对象候选已审核",
      "关系已审核",
    ],
    nextAction: "先完成人工对象审核",
    safetyBoundary: "本步骤不生成关系",
  },
  {
    id: "evidence_stance",
    title: "证据立场",
    description: "整理支持、对照与缺失证据的后续入口。",
    preconditions: [
      "原文片段可用",
      "笔记已审核或显式 passage-only",
      "证据立场审核待规划",
    ],
    nextAction: "先审核证据层",
    safetyBoundary: "本步骤不生成立场摘要",
  },
  {
    id: "mechanism_cards",
    title: "机制卡",
    description: "关系审核完成后再组织机制假设。",
    preconditions: [
      "对象候选已审核",
      "关系已审核",
      "机制草稿已审核",
    ],
    nextAction: "先审核对象与关系",
    safetyBoundary: "本步骤不生成机制",
  },
  {
    id: "discussion_pack",
    title: "Discussion 证据包",
    description: "准备后续 discussion 可用的证据包。",
    preconditions: [
      "笔记已审核",
      "对象已审核",
      "证据层已审核",
    ],
    nextAction: "先补齐已审核证据层",
    safetyBoundary: "本步骤不生成 discussion 包",
  },
  {
    id: "review_outline",
    title: "综述框架",
    description: "从已审核证据层整理综述框架。",
    preconditions: [
      "关系已审核",
      "机制已审核",
      "综述框架待规划",
    ],
    nextAction: "先审核关系和机制",
    safetyBoundary: "本步骤不生成综述框架",
  },
  {
    id: "research_gaps",
    title: "研究空白",
    description: "关系与机制审核后再识别研究空白。",
    preconditions: [
      "关系图已审核",
      "机制已审核",
      "研究空白待规划",
    ],
    nextAction: "先完成关系与机制层",
    safetyBoundary: "本步骤不生成研究空白",
  },
  {
    id: "flashcards",
    title: "复习卡片",
    description: "后续从已审核笔记生成复习卡片。",
    preconditions: [
      "原文片段可用",
      "笔记已审核或 passage-only 模式",
      "后续显式启用复习卡片生成",
    ],
    nextAction: "先审核笔记",
    safetyBoundary: "本步骤不生成复习卡片",
  },
];

const DEFAULT_PRODUCTION_SAVE_BLOCKER = "production_db_write_disabled";

export function buildStudioCardStates(state = {}) {
  const context = buildStudioContext(state);
  return STUDIO_CARD_DEFINITIONS.map((definition) => ({
    ...definition,
    ...statusForCard(definition.id, context),
    safetyBadges: STUDIO_SAFETY_BADGES,
    currentFacts: context.currentFacts,
    availableLayers: context.availableLayers,
    unavailableLayers: context.unavailableLayers,
  }));
}

export function buildStudioContext(state = {}) {
  const source = state.source_ingestion_status || {};
  const notes = state.notes_import_status || {};
  const correction = state.correction_review_status || {};
  const classification = state.classification_review_status || {};
  const readiness = state.save_readiness || {};
  const saved = state.saved_review_state || {};
  const objectDryRun = state.object_candidate_dry_run_summary || {};
  const layers = state.search_layer_availability || {};
  const blockers = readiness.current_blockers || [];
  const noNotes = notes.status === "blocked_no_notes_in_scope" || correction.status === "locked_no_notes_in_scope";
  const notesImported = !noNotes && Number(notes.existing || 0) > 0;
  const correctionSaved = Boolean(saved.ready_for_classification || saved.status === "saved" || correction.status === "saved");
  const classificationSaved = Boolean(classification.status === "saved" || correction.classification_review_saved);
  const objectDryRunReady = Boolean(objectDryRun.ready);
  const objectDraftSaved = objectDryRun.object_candidate_draft_review_status === "pending_human_review"
    || Number(objectDryRun.object_candidate_draft_saved_count || 0) > 0;
  const objectHumanReviewSaved = objectDryRun.object_candidate_human_review_status === "saved"
    || Number(objectDryRun.object_candidate_human_review_saved_count || 0) > 0;
  const relationDryRunReady = objectDryRun.relation_candidate_package_ready === true
    || objectDryRun.relation_candidate_dry_run_status === "relation_candidate_dry_run_ready"
    || Number(objectDryRun.relation_candidate_count || 0) > 0;
  const saveAllowed = readiness.production_review_write_allowed === true;
  const passagesAvailable = layers.passages === "available" || Boolean(source.chunked);
  const notesLayer = noNotes ? "unavailable" : (layers.notes || "raw_unreviewed");
  const blocker = noNotes
    ? "no_notes_in_scope"
    : blockers[0]
      || (readiness.production_review_write_allowed === false ? DEFAULT_PRODUCTION_SAVE_BLOCKER : "")
      || (!correctionSaved ? "correction_review_not_saved" : !classificationSaved ? "note_classification_not_completed" : relationDryRunReady ? "relation_candidate_dry_run_ready_future_phase7h_gate" : objectHumanReviewSaved ? "object_candidate_human_review_saved_relation_locked" : objectDraftSaved ? "object_candidate_drafts_pending_human_review" : "explicit_object_candidate_generation_gate_required");

  return {
    noNotes,
    notesImported,
    correctionSaved,
    classificationSaved,
    objectDryRun,
    objectDryRunReady,
    objectDraftSaved,
    objectHumanReviewSaved,
    relationDryRunReady,
    saveAllowed,
    blocker,
    passagesAvailable,
    notesLayer,
    currentFacts: [
      `笔记已导入：${notesImported ? "是" : "否"}`,
      `笔记层：${notesLayer}`,
      `原文片段可用：${passagesAvailable ? "是" : "否"}`,
      `纠错审核已保存：${correctionSaved ? "是" : "否"}`,
      `分类审核已保存：${classificationSaved ? "是" : "否"}`,
      objectDryRunReady
        ? `对象候选 dry-run 已就绪：${Number(objectDryRun.candidate_count || 0)} 个候选，${Number(objectDryRun.quarantined_count || 0)} 个隔离`
        : "对象候选 dry-run：未就绪",
      objectDryRun.pn68_quarantined ? "PN68 已隔离：是" : "PN68 已隔离：否",
      objectDraftSaved
        ? `对象候选草稿已保存：${Number(objectDryRun.object_candidate_draft_saved_count || 0)} 条待审核`
        : "对象候选草稿已保存：否",
      objectHumanReviewSaved
        ? `对象人工审核已保存：通过 ${Number(objectDryRun.approved_candidate_count || 0)}，拒绝 ${Number(objectDryRun.rejected_candidate_count || 0)}，待定 ${Number(objectDryRun.pending_candidate_count || 0)}`
        : "对象人工审核已保存：否",
      relationDryRunReady
        ? `关系候选 dry-run 已就绪：${Number(objectDryRun.relation_candidate_count || 0)} 条，来自 ${Number(objectDryRun.approved_candidate_count || 0)} 个已审核对象`
        : "关系候选 dry-run：未就绪",
      objectDryRun.pn68_excluded ? "PN68 已从关系 dry-run 排除：是" : "PN68 已从关系 dry-run 排除：否",
      `允许保存：${saveAllowed ? "是" : "否"}`,
      `当前条件：${studioBlockerLabel(blocker)}`,
      `对象已审核：${objectHumanReviewSaved ? "是" : "否"}`,
      "关系已审核：否",
      "机制已审核：否",
    ],
    availableLayers: [
      passagesAvailable ? "原文片段" : "",
      notesImported ? `笔记（${notesLayer}）` : "",
    ].filter(Boolean),
    unavailableLayers: [
      notesImported ? "" : "笔记",
      correctionSaved ? "" : "已保存纠错审核",
      classificationSaved ? "" : "已保存笔记分类审核",
      objectHumanReviewSaved ? "" : objectDraftSaved ? "对象候选人工审核" : "已审核对象",
      relationDryRunReady ? "已审核关系" : "关系 dry-run 包",
      "已审核机制",
    ].filter(Boolean),
  };
}

function statusForCard(id, context) {
  if (context.noNotes) return noNotesStatusForCard(id, context);
  return notesPresentStatusForCard(id, context);
}

function notesPresentStatusForCard(id, context) {
  if (id === "object_graph") {
    return {
      status: context.objectHumanReviewSaved ? "reviewed" : "locked",
      reason: context.objectHumanReviewSaved
        ? context.relationDryRunReady
          ? "对象人工审核已保存；关系 dry-run 包已就绪，Phase7H 未进入"
          : "对象人工审核已保存；关系 dry-run 需要显式 gate"
        : context.objectDraftSaved
        ? "对象候选草稿已保存；需要人工对象审核"
        : context.objectDryRunReady
        ? "对象候选 dry-run 已就绪；保存需要显式 gate"
        : context.classificationSaved
        ? "需要显式对象候选 gate"
        : "需要笔记分类审核",
      blocker: context.relationDryRunReady ? "relation_draft_save_future_phase7h_gate" : context.objectHumanReviewSaved ? "relation_dry_run_not_started" : context.objectDraftSaved ? "object_candidate_drafts_pending_human_review" : context.blocker,
    };
  }
  if (id === "relation_graph") {
    return {
      status: "locked",
      reason: context.relationDryRunReady ? "关系候选 dry-run 已就绪；关系保存/审核仍需 Phase7H gate" : context.objectHumanReviewSaved ? "已审核对象可用；关系 dry-run 仍由 gate 控制" : "需要已审核对象",
      blocker: context.relationDryRunReady ? "relation_draft_save_future_phase7h_gate" : context.objectHumanReviewSaved ? "relation_dry_run_not_started" : "objects_not_reviewed",
    };
  }
  if (id === "evidence_stance") {
    return {
      status: "planned",
      reason: "原文与笔记可用；已审核证据层尚未完成",
      blocker: "reviewed_evidence_not_ready",
    };
  }
  if (id === "mechanism_cards") {
    return {
      status: "locked",
      reason: context.relationDryRunReady ? "关系候选仅为 dry-run；机制需要已审核关系" : context.objectHumanReviewSaved ? "需要关系审核后才能进入机制" : "需要已审核对象和关系",
      blocker: context.relationDryRunReady ? "relations_not_reviewed_phase7h" : context.objectHumanReviewSaved ? "relations_not_reviewed" : "objects_or_relations_not_reviewed",
    };
  }
  if (id === "discussion_pack") {
    return {
      status: "planned",
      reason: "需要已审核证据层",
      blocker: "reviewed_evidence_not_ready",
    };
  }
  if (id === "review_outline") {
    return {
      status: "planned",
      reason: "需要已审核关系/机制",
      blocker: "relations_or_mechanisms_not_reviewed",
    };
  }
  if (id === "research_gaps") {
    return {
      status: "locked",
      reason: "需要已审核机制或关系图",
      blocker: "mechanisms_or_relation_map_not_reviewed",
    };
  }
  return {
    status: "planned",
    reason: "已审核笔记尚未就绪；原文片段可用",
    blocker: "reviewed_notes_not_ready",
  };
}

function noNotesStatusForCard(id, context) {
  if (id === "evidence_stance" || id === "flashcards") {
    return {
      status: "planned",
      reason: context.passagesAvailable ? "原文片段可用，笔记不可用" : "笔记层不可用",
      blocker: "notes_layer_unavailable",
    };
  }
  return {
    status: "locked",
    reason: id === "object_graph" ? "当前范围没有笔记" : "笔记层不可用",
    blocker: "no_notes_in_scope",
  };
}

function studioBlockerLabel(value) {
  const labels = {
    production_db_write_disabled: "只读模式：未写入数据库",
    relation_candidate_dry_run_ready_future_phase7h_gate: "关系候选 dry-run 已就绪，Phase7H 未进入",
    object_candidate_human_review_saved_relation_locked: "对象人工审核已保存，关系保存未启用",
    object_candidate_drafts_pending_human_review: "对象候选草稿等待人工审核",
    explicit_object_candidate_generation_gate_required: "需要显式对象候选 gate",
    correction_review_not_saved: "需要已保存纠错审核",
    note_classification_not_completed: "需要笔记分类审核",
    no_notes_in_scope: "当前范围没有笔记",
  };
  return labels[value] || value || "无";
}
