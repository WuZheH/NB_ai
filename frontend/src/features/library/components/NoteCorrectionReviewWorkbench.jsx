import { useEffect, useState } from "react";
import StateMessage from "../../../components/StateMessage.jsx";
import { formatConfidence } from "../../../shared/utils/display.js";
import {
  applyHumanAuditAction,
  auditRowMatchesFilter,
  buildHumanAuditRows,
  buildHumanAuditSavePayload,
  buildHumanAuditSummary,
  buildZoteroWritebackDraft,
  confirmHumanAuditItem,
  humanAuditDecisionStatus,
  humanAuditRowKey,
  isHumanAuditDecisionConfirmable,
  isNoteCorrectionAuditSaveEnabled,
  updateHumanAuditFinalNoteText,
} from "./noteCorrectionReview.js";
export default function NoteCorrectionReviewValidationResult({
  validation,
  packageData,
  filter,
  filteredItems,
  saveState,
  saveReadinessState,
  activeScope,
  mergePreview,
  savedReviewState,
  onSetFilter,
  onLoadSaveReadiness,
  onSaveReview,
}) {
  const stats = validation.stats || {};
  const errors = validation.errors || [];
  const warnings = validation.warnings || [];
  const normalizationWarnings = validation.normalization_warnings || [];
  const normalizedJson = validation.normalized_json || null;
  const completeness = validation.completeness || {};
  const missingNoteIds = completeness.missing_note_ids || stats.missing_note_ids || [];
  const duplicateNoteIds = completeness.duplicate_note_ids || stats.duplicate_note_ids || [];
  const unexpectedNoteIds = completeness.unexpected_note_ids || stats.unexpected_note_ids || [];
  const expectedCount = completeness.expected_count ?? stats.expected_item_count ?? 0;
  const actualCount = completeness.actual_count ?? stats.item_count ?? 0;
  const auditRows = buildHumanAuditRows(validation, packageData);
  const auditHardErrors = auditRows.filter((row) => row.match_error).map((row) => row.match_error);
  const canEnterAudit = validation.valid === true && auditHardErrors.length === 0;
  const [copiedNormalized, setCopiedNormalized] = useState(false);
  const [auditOpen, setAuditOpen] = useState(false);
  const [auditFilter, setAuditFilter] = useState("all");
  const [auditDecisions, setAuditDecisions] = useState({});
  const [expandedAuditItems, setExpandedAuditItems] = useState({});
  const [writebackDraft, setWritebackDraft] = useState(null);

  useEffect(() => {
    setAuditOpen(false);
    setAuditDecisions({});
    setExpandedAuditItems({});
    setWritebackDraft(null);
  }, [normalizedJson]);

  async function copyNormalizedJson() {
    if (!normalizedJson) return;
    await navigator.clipboard.writeText(JSON.stringify(normalizedJson, null, 2));
    setCopiedNormalized(true);
  }

  function enterHumanAudit() {
    if (!canEnterAudit) return;
    setAuditOpen(true);
    onLoadSaveReadiness?.();
    window.requestAnimationFrame(() => {
      document.querySelector(".noteCorrectionHumanAuditWorkbench")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  function updateAuditAction(row, action) {
    const key = humanAuditRowKey(row);
    setAuditDecisions((state) => applyHumanAuditAction(state, key, action, row));
    if (action === "ai_revision_accepted" || action === "manually_edited") {
      setExpandedAuditItems((state) => ({ ...state, [key]: true }));
    }
    setWritebackDraft(null);
  }

  function updateFinalNoteText(row, text) {
    const key = humanAuditRowKey(row);
    setAuditDecisions((state) => updateHumanAuditFinalNoteText(state, key, text));
    setWritebackDraft(null);
  }

  function confirmAuditItem(row) {
    const key = humanAuditRowKey(row);
    setAuditDecisions((state) => confirmHumanAuditItem(state, key, row));
    setWritebackDraft(null);
  }

  function toggleAuditItem(row) {
    const key = humanAuditRowKey(row);
    setExpandedAuditItems((state) => ({ ...state, [key]: !state[key] }));
  }

  function generateWritebackPreview() {
    setWritebackDraft(buildZoteroWritebackDraft(auditRows, auditDecisions));
  }

  return (
    <div className={`noteCorrectionValidationResult ${validation.valid ? "valid" : "invalid"}`} aria-label="返回 JSON 校验结果">
      <strong className="noteCorrectionLocalTitle">校验结果</strong>
      <div className="noteCorrectionValidationSummary">
        <strong>{validation.valid ? "校验通过" : "校验未通过"}</strong>
        <span>items={stats.item_count ?? 0} / expected={stats.expected_item_count ?? 0}</span>
        <span>expected={expectedCount}</span>
        <span>actual={actualCount}</span>
        <span>missing candidates={stats.missing_candidate_count ?? 0}</span>
        <span>missing={missingNoteIds.length}</span>
        <span>duplicate={duplicateNoteIds.length}</span>
        <span>unexpected={unexpectedNoteIds.length}</span>
        <span>alignment warning={stats.alignment_warning_count ?? 0}</span>
        <span>SYNPN068={stats.pn68yptt_present ? "present" : "missing"}</span>
      </div>
      {validation.valid && (
        <p className="noteCorrectionSuccessNotice">校验通过，但尚未写入。下一步需要用户确认后才能保存审核结果。</p>
      )}
      {validation.normalization_applied && (
        <div className="noteCorrectionNormalizationNotice">
          <p>{validation.valid ? "已自动归一化，校验通过。" : "已自动归一化，仍需处理校验错误。"}</p>
          <div className="noteCorrectionCopyRow">
            <button type="button" onClick={copyNormalizedJson} disabled={!normalizedJson}>
              复制归一化后的 JSON
            </button>
            <button
              type="button"
              onClick={enterHumanAudit}
              disabled={!canEnterAudit}
              title={canEnterAudit ? "进入人工审计" : "validator 未通过，不能进入人工审计"}
            >
              进入人工审计
            </button>
            {copiedNormalized && <span>已复制归一化 JSON</span>}
          </div>
          <details className="noteCorrectionDeveloperDetails">
            <summary>开发者详情：normalization warnings</summary>
            <pre>{[
              validation.root_pollution_warning ? "root_pollution_warning=true" : "",
              ...normalizationWarnings,
            ].filter(Boolean).join("\n") || "none"}</pre>
          </details>
        </div>
      )}
      {!validation.normalization_applied && (
        <div className="noteCorrectionCopyRow">
          <button
            type="button"
            onClick={enterHumanAudit}
            disabled={!canEnterAudit}
            title={canEnterAudit ? "进入人工审计" : "validator 未通过，不能进入人工审计"}
          >
            进入人工审计
          </button>
        </div>
      )}
      <div className="noteCorrectionCompletenessDetails" aria-label="completeness 结构化结果">
        <CompletenessNoteIdList label="missing_note_ids" ids={missingNoteIds} />
        <CompletenessNoteIdList label="duplicate_note_ids" ids={duplicateNoteIds} />
        <CompletenessNoteIdList label="unexpected_note_ids" ids={unexpectedNoteIds} />
      </div>
      {!!auditHardErrors.length && (
        <div className="noteCorrectionValidationErrors" aria-label="人工审计 hard errors">
          {auditHardErrors.map((error) => <span key={error}>{error}</span>)}
        </div>
      )}
      {!!errors.length && (
        <div className="noteCorrectionValidationErrors">
          {errors.map((error) => <span key={error}>{error}</span>)}
        </div>
      )}
      {!!warnings.length && (
        <div className="noteCorrectionWarnings">
          {warnings.map((warning) => <span key={warning}>{warning}</span>)}
        </div>
      )}
      <NoteCorrectionReviewFilters activeFilter={filter} stats={stats} onSetFilter={onSetFilter} />
      <div className="noteCorrectionReviewPreviewList" aria-label="审核意见预览列表">
        {filteredItems.map((item) => (
          <NoteCorrectionReviewPreviewItem key={`${item.zotero_annotation_key || item.note_id}-${item.server_note_id || item.client_note_id}`} item={item} />
        ))}
        {!filteredItems.length && (
          <p className="unitSourceNotice">当前筛选下没有审核意见。</p>
        )}
      </div>
      {auditOpen && (
        <NoteCorrectionHumanAuditWorkbench
          rows={auditRows}
          decisions={auditDecisions}
          expandedItems={expandedAuditItems}
          filter={auditFilter}
          writebackDraft={writebackDraft}
          onSetFilter={setAuditFilter}
          onToggleItem={toggleAuditItem}
          onAction={updateAuditAction}
          onFinalNoteTextChange={updateFinalNoteText}
          onConfirm={confirmAuditItem}
          onGenerateWritebackPreview={generateWritebackPreview}
          validation={validation}
          packageData={packageData}
          activeScope={activeScope}
          mergePreview={mergePreview}
          savedReviewState={savedReviewState}
          saveState={saveState}
          saveReadinessState={saveReadinessState}
          onSaveReview={onSaveReview}
        />
      )}
    </div>
  );
}

function NoteCorrectionHumanAuditWorkbench({
  rows,
  decisions,
  expandedItems,
  filter,
  writebackDraft,
  validation,
  packageData,
  activeScope,
  mergePreview,
  savedReviewState,
  saveState,
  saveReadinessState,
  onSetFilter,
  onToggleItem,
  onAction,
  onFinalNoteTextChange,
  onConfirm,
  onGenerateWritebackPreview,
  onSaveReview,
}) {
  const summary = buildHumanAuditSummary(rows, decisions);
  const visibleRows = rows.filter((row) => auditRowMatchesFilter(row, decisions[humanAuditRowKey(row)], filter));
  return (
    <section className="noteCorrectionHumanAuditWorkbench" aria-label="人工审计工作台">
      <div className="noteCorrectionAuditHeader">
        <div>
          <span>human audit workbench</span>
          <strong>人工审计工作台</strong>
        </div>
        <p>人工审计操作不会自动写数据库；只有显式点击保存按钮才写 Search review 表，且不会写 Zotero。</p>
        <p>最终新笔记是未来写回 Zotero 的候选文本。</p>
        <p>写回 Zotero 必须在下一阶段通过 Zotero 插件显式确认。</p>
      </div>
      <div className="unitProcessingMetrics">
        <MetricMini label="total_items" value={summary.total_items} />
        <MetricMini label="confirmed_items" value={summary.confirmed_items} />
        <MetricMini label="keep_original_count" value={summary.keep_original_count} />
        <MetricMini label="ai_revision_accepted_count" value={summary.ai_revision_accepted_count} />
        <MetricMini label="manually_edited_count" value={summary.manually_edited_count} />
        <MetricMini label="needs_followup_count" value={summary.needs_followup_count} />
        <MetricMini label="ready_for_save" value={String(summary.ready_for_save)} />
        <MetricMini label="ready_for_zotero_writeback" value={String(summary.ready_for_zotero_writeback)} />
      </div>
      <div className="noteCorrectionAuditFilters" aria-label="人工审计状态过滤">
        {[
          ["all", "全部"],
          ["needs_change", "需要修改"],
          ["confirmed", "已确认"],
          ["needs_followup", "需复查"],
        ].map(([value, label]) => (
          <button key={value} type="button" className={filter === value ? "active" : ""} onClick={() => onSetFilter(value)}>
            {label}
          </button>
        ))}
      </div>
      <div className="noteCorrectionAuditList">
        {visibleRows.map((row) => {
          const key = humanAuditRowKey(row);
          return (
            <HumanAuditNoteCard
              key={key}
              row={row}
              decision={decisions[key] || null}
              expanded={expandedItems[key] !== false}
              onToggle={() => onToggleItem(row)}
              onAction={(action) => onAction(row, action)}
              onFinalNoteTextChange={(text) => onFinalNoteTextChange(row, text)}
              onConfirm={() => onConfirm(row)}
            />
          );
        })}
        {!visibleRows.length && <p className="unitSourceNotice">当前过滤条件下没有人工审计条目。</p>}
      </div>
      <ZoteroWritebackPreviewPanel
        draft={writebackDraft}
        onGenerate={onGenerateWritebackPreview}
      />
      <NoteCorrectionAuditSavePanel
        summary={summary}
        rows={rows}
        decisions={decisions}
        validation={validation}
        packageData={packageData}
        activeScope={activeScope}
        mergePreview={mergePreview}
        savedReviewState={savedReviewState}
        saveState={saveState}
        saveReadinessState={saveReadinessState}
        onSaveReview={onSaveReview}
      />
    </section>
  );
}

function HumanAuditNoteCard({ row, decision, expanded, onToggle, onAction, onFinalNoteTextChange, onConfirm }) {
  const original = row.original_note || {};
  const item = row.ai_item || {};
  const status = humanAuditDecisionStatus(decision);
  const suggestedRevision = String(item.suggested_revision || "").trim();
  const finalNoteText = decision?.final_note_text ?? "";
  const showFinalInput = !!decision?.input_visible;
  const confirmable = isHumanAuditDecisionConfirmable(decision, row);
  return (
    <article className={`noteCorrectionAuditCard status-${status}`} data-audit-status={status}>
      <div className="noteCorrectionAuditCardHeader">
        <button type="button" onClick={onToggle} aria-expanded={expanded}>
          {expanded ? "收起" : "展开"}
        </button>
        <strong>{item.server_note_id || item.client_note_id || original.server_note_id || original.client_note_id || "note_id_unknown"}</strong>
        <span>{status}</span>
      </div>
      {expanded && (
        <>
          <div className="noteCorrectionAuditBlocks">
            <section className="noteCorrectionAuditBlock">
              <h4>原笔记</h4>
              <span className="noteCorrectionAuditFieldLabel">原始 note_text</span>
              <p className="noteCorrectionAuditOriginalText">{original.note_text || "无原始 note_text"}</p>
              <details>
                <summary>selected_text</summary>
                <p>{original.selected_text || original.selected_text_preview || "无 selected_text"}</p>
              </details>
              <div className="noteCorrectionAuditMeta">
                <span>page={original.page || item.page || "?"}</span>
                <span>zotero_annotation_key={original.zotero_annotation_key || item.zotero_annotation_key || "unknown"}</span>
                <span>server_note_id={original.server_note_id || item.server_note_id || "null"}</span>
                <span>client_note_id={original.client_note_id || item.client_note_id || "null"}</span>
                <span>matched_chunk_id={original.matched_chunk_id || "null"}</span>
                <span>evidence status={original.evidence_alignment_status || item.evidence_support || "unknown"}</span>
              </div>
            </section>
            <section className="noteCorrectionAuditBlock">
              <h4>AI 修改意见</h4>
              <div className="noteCorrectionAuditMeta">
                <span>correction_status={item.correction_status || "unknown"}</span>
                <span>issue_type={item.issue_type || "unknown"}</span>
                <span>evidence_support={item.evidence_support || "unknown"}</span>
                <span>confidence={formatConfidence(item.confidence)}</span>
              </div>
              <div className="noteCorrectionReviewFields">
                <div>
                  <span>explanation</span>
                  <p>{item.explanation || "无 explanation"}</p>
                </div>
                <div>
                  <span>suggested_revision</span>
                  <p>{suggestedRevision || "AI 未建议改写"}</p>
                </div>
                <div>
                  <span>reviewer_warning</span>
                  <p>{item.reviewer_warning || "无 reviewer_warning"}</p>
                </div>
              </div>
            </section>
            <section className="noteCorrectionAuditBlock">
              <h4>最终新笔记</h4>
              <p>最终新笔记是未来写回 Zotero 的候选文本。</p>
              {!showFinalInput && (
                <p className="unitSourceNotice">点击“采用 AI 建议”或“编辑最终笔记”后显示 final_note_text 输入框。</p>
              )}
              {showFinalInput && (
                <label className="noteCorrectionFinalNoteEditor">
                  <span>final_note_text</span>
                  <textarea
                    name="final_note_text"
                    value={finalNoteText}
                    onChange={(event) => onFinalNoteTextChange(event.target.value)}
                    rows={5}
                  />
                </label>
              )}
              {decision?.action && !confirmable && (
                <p className="noteCorrectionWarning">final_note_text 不能为空，除非 action=keep_original。</p>
              )}
            </section>
          </div>
          <div className="noteCorrectionAuditActions">
            <button type="button" onClick={() => onAction("keep_original")}>保留原笔记</button>
            <button type="button" onClick={() => onAction("ai_revision_accepted")}>采用 AI 建议</button>
            <button type="button" onClick={() => onAction("manually_edited")}>编辑最终笔记</button>
            <button type="button" onClick={() => onAction("needs_followup")}>标记为需复查</button>
            <button type="button" onClick={onConfirm} disabled={!confirmable}>确认本条</button>
          </div>
        </>
      )}
    </article>
  );
}

export function MetricMini({ label, value }) {
  return (
    <span className="unitMetricMini">
      <em>{label}</em>
      <strong>{value}</strong>
    </span>
  );
}

function includesAlignmentRisk(value) {
  const text = String(value || "").toLowerCase();
  return text.includes("alignment") || text.includes("unmatched");
}

function ZoteroWritebackPreviewPanel({ draft, onGenerate }) {
  return (
    <section className="noteCorrectionWritebackPreview" aria-label="Zotero 写回预览">
      <div className="noteCorrectionCopyRow">
        <button type="button" onClick={onGenerate}>
          生成 Zotero 写回预览
        </button>
        <button type="button" className="noteCorrectionDisabledAction" disabled>
          写回 Zotero：下一阶段通过 Zotero 插件启用，当前不会修改 Zotero
        </button>
      </div>
      <div className="reviewGateSummary">
        <span>writeback_target=zotero_annotation_comment</span>
        <span>writeback_requires_plugin=true</span>
        <span>zotero_db_write_performed=false</span>
        <span>ready_for_zotero_writeback=false</span>
      </div>
      {draft && (
        <details className="noteCorrectionDeveloperDetails" open>
          <summary>Zotero writeback draft summary</summary>
          <pre>{JSON.stringify(draft, null, 2)}</pre>
        </details>
      )}
    </section>
  );
}

function NoteCorrectionAuditSavePanel({
  summary,
  rows,
  decisions,
  validation,
  packageData,
  activeScope,
  mergePreview,
  savedReviewState,
  saveState,
  saveReadinessState,
  onSaveReview,
}) {
  const readiness = saveReadinessState?.data || null;
  const canaryPreflight = readiness?.production_canary_preflight || null;
  const readinessLoading = saveReadinessState?.status === "loading";
  const readinessError = saveReadinessState?.status === "error" ? saveReadinessState.error : "";
  const reviewSchemaReady = readiness?.review_schema_ready === true;
  const productionReviewWriteAllowed = readiness?.production_review_write_allowed === true;
  const productionDbWriteEnabled = readiness?.production_db_write_enabled === true;
  const saveEndpointAvailable = readiness?.save_endpoint_available === true;
  const currentBlockers = Array.isArray(readiness?.current_blockers) ? readiness.current_blockers : [];
  const blockerText = currentBlockers.length ? currentBlockers.join(", ") : "save_readiness_not_allowed";
  const mergeComplete = noteCorrectionMergeScopeComplete(activeScope, mergePreview);
  const readyForNoteClassification = summary.ready_for_note_classification && mergeComplete;
  const saving = saveState?.status === "loading";
  const saveResult = saveState?.data || null;
  const canSave = isNoteCorrectionAuditSaveEnabled({
    validation,
    summary,
    readiness,
    saving,
  });
  const blockedReason = noteCorrectionSaveBlockedReason({
    readiness,
    readinessLoading,
    readinessError,
    reviewSchemaReady,
    productionReviewWriteAllowed,
    saveEndpointAvailable,
    summary,
    blockerText,
  });
  const blockedReasonCode = noteCorrectionSaveBlockedReasonCode({
    readiness,
    readinessLoading,
    readinessError,
    reviewSchemaReady,
    productionReviewWriteAllowed,
    saveEndpointAvailable,
    summary,
    currentBlockers,
  });

  function submitSave() {
    if (!canSave) return;
    onSaveReview?.(buildHumanAuditSavePayload({
      rows,
      decisions,
      validation,
      packageData,
      activeScope,
      mergePreview,
    }));
  }

  return (
    <section className="noteCorrectionAuditSavePanel" aria-label="保存人工审计结果">
      <div className="noteCorrectionAuditHeader">
        <div>
          <span>Search persistence</span>
          <strong>保存人工审计结果到 Search</strong>
        </div>
        <p>只保存 Search 内部 correction review；不写 Zotero，不写 vector store，不生成 classification/object/relation/mechanism。</p>
      </div>
      <div className="unitProcessingMetrics">
        <MetricMini label="confirmed_items" value={`${summary.confirmed_items} / ${summary.total_items}`} />
        <MetricMini label="needs_followup_count" value={summary.needs_followup_count} />
        <MetricMini label="ready_for_save" value={String(summary.ready_for_save)} />
        <MetricMini label="ready_for_note_classification" value={String(readyForNoteClassification)} />
        <MetricMini label="ready_for_zotero_writeback_queue" value={String(summary.ready_for_zotero_writeback_queue)} />
        <MetricMini label="review_schema_ready" value={String(reviewSchemaReady)} />
        <MetricMini label="production_review_write_allowed" value={String(productionReviewWriteAllowed)} />
        <MetricMini label="production_db_write_enabled" value={String(productionDbWriteEnabled)} />
      </div>
      <SavedReviewStateSummary state={savedReviewState} compact />
      {canaryPreflight && (
        <div className="noteCorrectionCanaryPreflight" aria-label="production canary preflight">
          <strong>production canary preflight</strong>
          <div className="reviewGateSummary">
            <span>review_schema_ready={String(canaryPreflight.review_schema_ready)}</span>
            <span>save_endpoint_available={String(canaryPreflight.save_endpoint_available)}</span>
            <span>required_confirmation_context={canaryPreflight.required_confirmation_context || "unknown"}</span>
            <span>production_db_write_enabled={String(canaryPreflight.production_db_write_enabled)}</span>
            <span>production_review_write_allowed={String(canaryPreflight.production_review_write_allowed)}</span>
            <span>current_blockers={(canaryPreflight.current_blockers || []).join(", ") || "none"}</span>
          </div>
          <p className="unitSourceNotice">production canary preflight 只读检查；不会调用真实 production save API，不会写 Zotero/vector store。</p>
        </div>
      )}
      {!canSave && blockedReason && <p className="noteCorrectionWarning">{blockedReason}</p>}
      {!canSave && <p className="noteCorrectionSaveRequestState">save request not sent: {blockedReasonCode}</p>}
      {readiness && currentBlockers.length > 0 && (
        <p className="noteCorrectionWarning">current_blockers={blockerText}</p>
      )}
      {reviewSchemaReady && summary.needs_followup_count > 0 && (
        <p className="noteCorrectionWarning">存在 needs_followup_items，保存后仍不能进入 note classification。</p>
      )}
      {reviewSchemaReady && summary.needs_followup_count === 0 && summary.ready_for_save && !mergeComplete && (
        <p className="noteCorrectionWarning">当前 scope 尚未覆盖整章 review，保存后仍不能进入 note classification。</p>
      )}
      <div className="noteCorrectionCopyRow">
        <button type="button" onClick={submitSave} disabled={!canSave}>
          {saving ? "保存中..." : "保存人工审计结果到 Search"}
        </button>
        <span>点击后只保存到 Search，不写 Zotero。当前不会生成对象、关系、机制。将只保存到 Search，不写 Zotero，不生成对象/关系/机制。</span>
      </div>
      <p className="unitSourceNotice">confirmation_context=save_note_correction_review_after_user_audit</p>
      {saveState?.status === "error" && <StateMessage title="保存请求失败" body={saveState.error} />}
      {saveResult && (
        <div className={saveResult.status === "saved" ? "noteCorrectionSuccessNotice" : "noteCorrectionWarning"}>
          <span>status={saveResult.status}</span>
          <span>reason={saveResult.reason || "none"}</span>
          <span>review_id={saveResult.review_id || "not_saved"}</span>
          <span>schema_version={saveResult.schema_version || "unknown"}</span>
          <span>human_audit_schema_version={saveResult.human_audit_schema_version || "unknown"}</span>
          <span>saved_item_count={saveResult.saved_item_count ?? 0}</span>
          <span>ready_for_note_classification={String(saveResult.ready_for_note_classification)}</span>
          {!!saveResult.current_blockers?.length && <span>current_blockers={saveResult.current_blockers.join(", ")}</span>}
          {saveResult.audit_trace && (
            <details className="noteCorrectionDeveloperDetails">
              <summary>audit_trace</summary>
              <pre>{JSON.stringify(saveResult.audit_trace, null, 2)}</pre>
            </details>
          )}
        </div>
      )}
    </section>
  );
}

function noteCorrectionSaveBlockedReason({
  readiness,
  readinessLoading,
  readinessError,
  reviewSchemaReady,
  productionReviewWriteAllowed,
  saveEndpointAvailable,
  summary,
  blockerText,
}) {
  if (readinessLoading) return "正在读取后端保存 readiness。";
  if (readinessError) return readinessError;
  if (!readiness) return "尚未读取后端保存 readiness。";
  if (!reviewSchemaReady) return "review 保存表尚未启用，需先执行数据库迁移。";
  if (!productionReviewWriteAllowed) return `review 表已启用，但后端当前禁止 production review 写入：${blockerText}`;
  if (!saveEndpointAvailable) return "保存 endpoint 当前不可用。";
  if (!summary.ready_for_save) return "所有 item 确认且没有 needs_followup 后才能保存人工审计结果。";
  return "";
}

export function noteCorrectionSaveBlockedReasonCode({
  readiness,
  readinessLoading,
  readinessError,
  reviewSchemaReady,
  productionReviewWriteAllowed,
  saveEndpointAvailable,
  summary,
  currentBlockers = [],
}) {
  if (readinessLoading) return "save_readiness_loading";
  if (readinessError) return "save_readiness_error";
  if (!readiness) return "save_readiness_not_loaded";
  if (!reviewSchemaReady) return "review_schema_missing";
  if (!productionReviewWriteAllowed) return currentBlockers[0] || "production_review_write_not_allowed";
  if (!saveEndpointAvailable) return "save_endpoint_unavailable";
  if (!summary?.ready_for_save) return "human_audit_items_not_ready";
  return "unknown_blocker";
}

function SavedReviewStateSummary({ state, compact = false }) {
  if (!state) return null;
  return (
    <div className={`savedReviewStateSummary ${compact ? "compact" : ""}`} aria-label="saved review state">
      <strong>saved review state: {state.status || "not_saved"}</strong>
      <span>review_id={state.latest_review_id || state.review_id || "not_saved"}</span>
      <span>scope_id={state.scope_id || "none"}</span>
      <span>saved_item_count={state.saved_item_count ?? 0}</span>
      <span>confirmed_count={state.confirmed_count ?? 0}</span>
      <span>needs_followup_count={state.needs_followup_count ?? 0}</span>
      <span>ready_for_classification={String(state.ready_for_classification === true)}</span>
      <span>classification_package={state.classification_package_status || "blocked"}</span>
      <span>pn68_status={state.pn68_status || "not_saved"}</span>
      <span>pn68_warning_preserved={String(state.pn68_warning_preserved === true)}</span>
      {!compact && !!state.partial_saved_sections?.length && (
        <span>partial_saved_sections={state.partial_saved_sections.join(", ")}</span>
      )}
      {!compact && !!state.source_section_ids?.length && (
        <span>saved_sections={state.source_section_ids.join(", ")}</span>
      )}
      {!compact && !!state.missing_sections?.length && (
        <span>missing_sections={state.missing_sections.join(", ")}</span>
      )}
    </div>
  );
}

export function CompletenessNoteIdList({ label, ids }) {
  if (!ids.length) return null;
  const visibleIds = ids.slice(0, 10);
  const remaining = ids.length - visibleIds.length;
  return (
    <details className="noteCorrectionDeveloperDetails">
      <summary>{label}: {ids.length} total, showing {visibleIds.length}</summary>
      <pre>{visibleIds.join("\n")}{remaining > 0 ? `\n... ${remaining} more` : ""}</pre>
    </details>
  );
}

function NoteCorrectionReviewFilters({ activeFilter, stats, onSetFilter }) {
  const statusCounts = stats.correction_status_counts || {};
  const filters = [
    ["all", "全部", stats.item_count || 0],
    ["ok", "ok", statusCounts.ok || 0],
    ["needs_revision", "needs_revision", statusCounts.needs_revision || 0],
    ["misunderstood", "misunderstood", statusCounts.misunderstood || 0],
    ["unsupported", "unsupported", statusCounts.unsupported || 0],
    ["unclear", "unclear", statusCounts.unclear || 0],
    ["alignment_warning", "alignment warning", stats.alignment_warning_count || 0],
  ];
  return (
    <div className="noteCorrectionReviewFilters" aria-label="审核意见筛选">
      {filters.map(([value, label, count]) => (
        <button
          key={value}
          type="button"
          className={activeFilter === value ? "active" : ""}
          onClick={() => onSetFilter(value)}
        >
          <span>{label}</span>
          <strong>{count}</strong>
        </button>
      ))}
    </div>
  );
}

function NoteCorrectionReviewPreviewItem({ item }) {
  const hasAlignmentWarning = item.has_alignment_warning || includesAlignmentRisk(item.issue_type) || includesAlignmentRisk(item.reviewer_warning);
  return (
    <article className={`noteCorrectionReviewPreviewItem ${hasAlignmentWarning ? "alignmentWarning" : ""}`}>
      <div className="noteCorrectionCandidateMeta">
        <span>{item.note_id || item.server_note_id || item.client_note_id || "note_id_unknown"}</span>
        <span>{item.zotero_annotation_key || "annotation_key_unknown"}</span>
        <span>p.{item.page || "?"}</span>
        <span>{item.correction_status || "status_unknown"}</span>
        <span>{item.issue_type || "issue_unknown"}</span>
        <span>{item.evidence_support || "evidence_unknown"}</span>
        <span>confidence={formatConfidence(item.confidence)}</span>
      </div>
      {hasAlignmentWarning && (
        <p className="noteCorrectionWarning">该条原始对齐不可靠，人工审核时需重点检查。</p>
      )}
      <div className="noteCorrectionReviewFields">
        <div>
          <span>explanation</span>
          <p>{item.explanation || "无 explanation"}</p>
        </div>
        <div>
          <span>suggested_revision</span>
          <p>{item.suggested_revision || "无 suggested_revision"}</p>
        </div>
        <div>
          <span>reviewer_warning</span>
          <p>{item.reviewer_warning || "无 reviewer_warning"}</p>
        </div>
      </div>
    </article>
  );
}
