export function buildHumanAuditRows(validation, packageData) {
  const originalIndex = buildOriginalNoteIndex(packageData);
  const aiItems = validation?.normalized_preview
    || validation?.normalized_json?.note_correction_review?.items
    || [];
  return aiItems.map((item, index) => {
    const keys = primaryIdentityKeys(item);
    const original = keys.map((key) => originalIndex.get(key)).find(Boolean) || null;
    const row = {
      index,
      ai_item: item,
      original_note: original,
      primary_keys: keys,
    };
    if (!keys.length) {
      row.match_error = `AI item ${index + 1} missing server_note_id/client_note_id; zotero_annotation_key cannot be primary identity`;
    } else if (!original) {
      row.match_error = `AI item ${keys.join(" / ")} 找不到原 note，不能进入人工审计`;
    }
    return row;
  });
}

export function buildOriginalNoteIndex(packageData) {
  const packageJson = packageData?.package_json || packageData || {};
  const candidates = packageJson.correction_candidates || packageData?.correction_candidates || [];
  const anchors = packageJson.note_anchors || packageData?.note_anchors || [];
  const anchorsByPrimaryId = new Map();
  for (const anchor of anchors) {
    for (const key of primaryIdentityKeys(anchor)) {
      anchorsByPrimaryId.set(key, anchor);
    }
  }
  const index = new Map();
  for (const candidate of candidates) {
    const anchor = primaryIdentityKeys(candidate).map((key) => anchorsByPrimaryId.get(key)).find(Boolean) || {};
    const original = {
      ...anchor,
      ...candidate,
      note_text: candidate.note_text ?? anchor.note_text ?? "",
      selected_text: candidate.selected_text ?? anchor.selected_text ?? "",
      selected_text_preview: candidate.selected_text_preview ?? anchor.selected_text_preview ?? "",
    };
    for (const key of primaryIdentityKeys(original)) {
      index.set(key, original);
    }
  }
  return index;
}

export function applyHumanAuditAction(decisions, key, action, row) {
  const originalText = String(row?.original_note?.note_text || "");
  const suggestedRevision = String(row?.ai_item?.suggested_revision || "");
  const previous = decisions[key] || {};
  const next = {
    ...previous,
    action,
    confirmed: false,
    confirmed_by_user: false,
  };
  if (action === "keep_original") {
    next.final_note_text = originalText;
    next.input_visible = false;
  } else if (action === "ai_revision_accepted") {
    next.final_note_text = suggestedRevision;
    next.input_visible = true;
  } else if (action === "manually_edited") {
    next.final_note_text = previous.final_note_text || suggestedRevision || originalText;
    next.input_visible = true;
  } else if (action === "needs_followup") {
    next.final_note_text = previous.final_note_text || originalText;
    next.input_visible = false;
  }
  return { ...decisions, [key]: next };
}

export function updateHumanAuditFinalNoteText(decisions, key, finalNoteText) {
  const previous = decisions[key] || {};
  return {
    ...decisions,
    [key]: {
      ...previous,
      final_note_text: finalNoteText,
      action: previous.action || "manually_edited",
      input_visible: true,
      confirmed: false,
      confirmed_by_user: false,
    },
  };
}

export function confirmHumanAuditItem(decisions, key, row) {
  const previous = decisions[key] || {};
  if (!isHumanAuditDecisionConfirmable(previous, row)) return decisions;
  return {
    ...decisions,
    [key]: {
      ...previous,
      confirmed: true,
      confirmed_by_user: true,
    },
  };
}

export function buildHumanAuditSummary(rows, decisions) {
  const values = rows.map((row) => decisions[humanAuditRowKey(row)] || {});
  const confirmedItems = values.filter((item) => item.confirmed).length;
  const needsFollowupCount = values.filter((item) => item.action === "needs_followup").length;
  const readyForSave = rows.length > 0 && confirmedItems === rows.length && needsFollowupCount === 0;
  const readyForNoteClassification = readyForSave && needsFollowupCount === 0;
  const readyForZoteroWritebackQueue = values.some((item) => (
    item.confirmed && ["ai_revision_accepted", "manually_edited"].includes(item.action)
  ));
  return {
    total_items: rows.length,
    confirmed_items: confirmedItems,
    keep_original_count: values.filter((item) => item.action === "keep_original").length,
    ai_revision_accepted_count: values.filter((item) => item.action === "ai_revision_accepted").length,
    manually_edited_count: values.filter((item) => item.action === "manually_edited").length,
    needs_followup_count: needsFollowupCount,
    ready_for_save: readyForSave,
    ready_for_note_classification: readyForNoteClassification,
    ready_for_zotero_writeback_queue: readyForZoteroWritebackQueue,
    ready_for_zotero_writeback: false,
  };
}

export function isNoteCorrectionAuditSaveEnabled({
  validation,
  summary,
  readiness,
  saving = false,
}) {
  return validation?.valid === true
    && summary?.ready_for_save === true
    && readiness?.review_schema_ready === true
    && readiness?.production_review_write_allowed === true
    && readiness?.save_endpoint_available === true
    && !saving;
}

function noteCorrectionMergeScopeComplete(activeScope, mergePreview) {
  const reviewMode = activeScope?.review_mode || "full_chapter";
  if (reviewMode === "full_chapter") return true;
  if (!mergePreview || typeof mergePreview !== "object") return false;
  if (mergePreview.all_valid === true) return true;
  const expectedTotal = Number(mergePreview.expected_total);
  const validatedItems = Number(mergePreview.validated_items);
  const missing = Number(mergePreview.missing);
  return Number.isFinite(expectedTotal)
    && Number.isFinite(validatedItems)
    && expectedTotal > 0
    && validatedItems >= expectedTotal
    && (!Number.isFinite(missing) || missing === 0);
}

export function buildHumanAuditSavePayload({
  rows,
  decisions,
  validation,
  packageData,
  activeScope,
  mergePreview,
}) {
  const packageJson = packageData?.package_json || packageData || {};
  const reviewMode = activeScope?.review_mode || packageData?.review_mode || packageJson.review_mode || "full_chapter";
  return {
    confirm_write: true,
    confirmation_context: "save_note_correction_review_after_user_audit",
    review_mode: reviewMode,
    scope_id: activeScope?.section_id || activeScope?.batch_id || packageJson.scope_id || null,
    batch_size: activeScope?.batch_size || null,
    batch_index: activeScope?.batch_index ?? null,
    normalized_review_json: validation.normalized_json?.note_correction_review
      ? validation.normalized_json
      : { note_correction_review: validation.normalized_json },
    human_audit_items: rows.map((row) => {
      const key = humanAuditRowKey(row);
      const decision = decisions[key] || {};
      const original = row.original_note || {};
      const aiItem = row.ai_item || {};
      const action = decision.action || "pending";
      return {
        server_note_id: aiItem.server_note_id || original.server_note_id || null,
        client_note_id: aiItem.client_note_id || original.client_note_id || null,
        zotero_annotation_key: aiItem.zotero_annotation_key || original.zotero_annotation_key || null,
        human_action: action,
        final_note_text: decision.final_note_text ?? null,
        confirmed_by_user: decision.confirmed === true,
        writeback_intent: ["ai_revision_accepted", "manually_edited"].includes(action) ? "planned" : "none",
        writeback_target: ["ai_revision_accepted", "manually_edited"].includes(action) ? "zotero_annotation_comment" : null,
      };
    }),
    merge_preview: mergePreview || null,
    source_package_hash: packageJson.source_package_hash || null,
    supersede_existing: false,
  };
}

export function buildZoteroWritebackDraft(rows, decisions) {
  const writebackItems = rows
    .map((row) => {
      const key = humanAuditRowKey(row);
      const decision = decisions[key] || {};
      if (!decision.confirmed) return null;
      const original = row.original_note || {};
      return {
        zotero_annotation_key: original.zotero_annotation_key || row.ai_item?.zotero_annotation_key || null,
        original_note_text: original.note_text || "",
        final_note_text: decision.final_note_text || original.note_text || "",
        action: decision.action || "pending",
        confirmed_by_user: decision.confirmed === true,
      };
    })
    .filter(Boolean);
  return {
    zotero_writeback_planned: writebackItems.some((item) => ["ai_revision_accepted", "manually_edited"].includes(item.action)),
    writeback_target: "zotero_annotation_comment",
    writeback_requires_plugin: true,
    zotero_db_write_performed: false,
    db_write_performed: false,
    llm_called: false,
    object_candidates_generated: false,
    relation_generated: false,
    mechanism_generated: false,
    ready_for_zotero_writeback: false,
    writeback_items: writebackItems,
  };
}

export function primaryIdentityKeys(item) {
  return [item?.server_note_id, item?.client_note_id]
    .map((value) => String(value || "").trim())
    .filter(Boolean);
}

export function humanAuditRowKey(row) {
  const original = row.original_note || {};
  const item = row.ai_item || {};
  return String(
    item.server_note_id
    || item.client_note_id
    || original.server_note_id
    || original.client_note_id
    || `row-${row.index}`
  );
}

export function humanAuditDecisionStatus(decision) {
  if (decision?.confirmed) return "confirmed";
  return decision?.action || "pending";
}

export function isHumanAuditDecisionConfirmable(decision, row) {
  if (!decision?.action) return false;
  if (decision.action === "keep_original") return true;
  if (decision.action === "needs_followup") return true;
  return String(decision.final_note_text || "").trim().length > 0 && !row.match_error;
}

export function auditRowMatchesFilter(row, decision, filter) {
  const status = humanAuditDecisionStatus(decision);
  if (!filter || filter === "all") return true;
  if (filter === "confirmed") return status === "confirmed";
  if (filter === "needs_followup") return decision?.action === "needs_followup";
  if (filter === "needs_change") {
    const aiStatus = String(row.ai_item?.correction_status || "").toLowerCase();
    return !!row.ai_item?.suggested_revision
      || ["needs_revision", "misunderstood", "unsupported", "unclear"].includes(aiStatus)
      || ["ai_revision_accepted", "manually_edited"].includes(decision?.action);
  }
  return true;
}

export function filterNoteCorrectionReviewItems(items, filter) {
  if (!filter || filter === "all") return items;
  if (filter === "alignment_warning") return items.filter((item) => item.has_alignment_warning);
  return items.filter((item) => item.correction_status === filter);
}

