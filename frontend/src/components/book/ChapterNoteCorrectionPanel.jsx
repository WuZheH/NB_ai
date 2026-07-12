import { useEffect } from "react";
import StateMessage from "../StateMessage.jsx";
import NoteCorrectionReviewValidationResult, {
  CompletenessNoteIdList,
  MetricMini,
} from "../../features/library/components/NoteCorrectionReviewWorkbench.jsx";
import { filterNoteCorrectionReviewItems } from "../../features/library/components/noteCorrectionReview.js";

export { noteCorrectionSaveBlockedReasonCode } from "../../features/library/components/NoteCorrectionReviewWorkbench.jsx";
export {
  applyHumanAuditAction,
  buildHumanAuditRows,
  buildHumanAuditSavePayload,
  buildHumanAuditSummary,
  buildOriginalNoteIndex,
  buildZoteroWritebackDraft,
  confirmHumanAuditItem,
  isNoteCorrectionAuditSaveEnabled,
  updateHumanAuditFinalNoteText,
} from "../../features/library/components/noteCorrectionReview.js";

export default function ChapterNoteCorrectionPanel({
  gate,
  chapter,
  planState,
  selectedMode,
  batchSize = 15,
  selectedSectionId,
  selectedBatchIndex = 0,
  packageStates = {},
  copiedPackageKey = "",
  reviewTexts = {},
  validationStates = {},
  reviewFilters = {},
  saveStates = {},
  saveReadinessStates = {},
  savedReviewState = null,
  onLoadPlan,
  onSelectMode,
  onSelectSection,
  onSelectBatchSize,
  onSelectBatch,
  onPreview,
  onCopy,
  onReviewTextChange,
  onValidateReview,
  onSetReviewFilter,
  onLoadSaveReadiness,
  onSaveReview,
}) {
  const plan = planState?.data || null;
  const effectiveMode = selectedMode || plan?.recommended_mode || "full_chapter";
  const activeScope = buildActiveReviewScope(chapter, effectiveMode, plan, selectedSectionId, batchSize, selectedBatchIndex);
  const activeKey = noteCorrectionPanelScopeKey(chapter, activeScope);
  const packageState = packageStates[activeKey] || {};
  const data = packageState?.data || null;
  const loading = packageState?.status === "loading";
  const copied = copiedPackageKey === activeKey;
  const reviewText = reviewTexts[activeKey] || "";
  const validationState = validationStates[activeKey] || {};
  const saveState = saveStates[activeKey] || {};
  const saveReadinessState = saveReadinessStates[activeKey] || {};
  const validating = validationState?.status === "loading";
  const validation = validationState?.data || null;
  const reviewFilter = reviewFilters[activeKey] || "all";
  const canPreview = gate.canCorrectNotes && !!chapter?.chapter_id;
  const alignmentWarningKeys = data?.unmatched_warning_keys || [];
  const reviewItems = validation?.normalized_preview || [];
  const filteredReviewItems = filterNoteCorrectionReviewItems(reviewItems, reviewFilter);
  const mergePreview = buildChapterMergePreview(
    plan,
    validationStates,
    chapter?.chapter_id,
    effectiveMode,
    batchSize
  );
  const scopeDisplay = buildScopeDisplay(data, effectiveMode, activeScope);

  useEffect(() => {
    if (canPreview && !plan && planState?.status !== "loading") {
      onLoadPlan?.();
    }
  }, [canPreview, onLoadPlan, plan, planState?.status]);

  useEffect(() => {
    if (!canPreview || !activeKey || activeScope.review_mode === "full_chapter") return;
    if (data || packageState?.status === "loading" || packageState?.status === "error") return;
    onPreview?.(activeScope);
  }, [activeKey, canPreview, data, packageState?.status, onPreview]);

  return (
    <section className="noteCorrectionPackagePanel" aria-label="笔记纠错人工 round-trip">
      <div className="noteCorrectionPackageHeader">
        <div>
          <span>ChatGPT 笔记纠错审核</span>
          <strong>ChatGPT 笔记纠错审核</strong>
        </div>
      </div>
      <div className="noteCorrectionSafetyList">
        <span>不调用 LLM</span>
        <span>不写 Zotero</span>
        <span>不自动写数据库</span>
        <span>不生成对象</span>
        <span>不生成关系</span>
        <span>不生成机制</span>
        <span>需要手动复制给 ChatGPT</span>
      </div>
      <SavedReviewStateSummary state={savedReviewState} />
      {!canPreview && (
        <p className="unitSourceNotice">本章没有用户笔记，不能生成笔记纠错包。</p>
      )}
      {packageState?.status === "error" && <StateMessage title="笔记纠错包预览失败" body={packageState.error} />}
      <ReviewModePlanner
        planState={planState}
        plan={plan}
        selectedMode={effectiveMode}
        batchSize={batchSize}
        selectedSectionId={activeScope.section_id}
        selectedBatchIndex={activeScope.batch_index || 0}
        onUseRecommended={() => onSelectMode(plan?.recommended_mode || "full_chapter")}
        onSelectMode={onSelectMode}
        onSelectSection={onSelectSection}
        onSelectBatchSize={onSelectBatchSize}
        onSelectBatch={onSelectBatch}
      />
      <ChapterMergePreview mergePreview={mergePreview} />
      <div className="noteCorrectionRoundTripGrid">
        <div className="noteCorrectionRoundTripBlock">
          <div className="noteCorrectionRoundTripHeader">
            <span>prompt</span>
            <div>
              <strong>复制提示词</strong>
              <p>当前方式：{reviewModeLabel(effectiveMode)}。这里生成的是发给 ChatGPT 的输入，不是审核结果。ChatGPT 只做 note_correction_review，并返回 JSON。</p>
            </div>
          </div>
          <div className="noteCorrectionCopyRow">
            <button type="button" onClick={() => onPreview(activeScope)} disabled={!canPreview || loading}>
              {loading ? "生成中..." : "预览输入包"}
            </button>
            <button type="button" onClick={() => onCopy(activeScope)} disabled={!data}>
              复制当前方式提示词
            </button>
            {copied && <span>已复制</span>}
          </div>
          {data && (
            <div className="noteCorrectionPackageBody">
              <div className="unitProcessingMetrics">
                <MetricMini label="当前模式" value={scopeDisplay.modeLabel} />
                <MetricMini label="当前 scope" value={scopeDisplay.scopeLabel} />
                <MetricMini label="expected notes" value={scopeDisplay.expectedCount} />
                <MetricMini label="scoped chunks" value={scopeDisplay.scopedChunkCount} />
                <MetricMini label="estimated scoped chars" value={scopeDisplay.estimatedChars} />
                <MetricMini label="note anchors" value={data.note_anchor_count ?? 0} />
                <MetricMini label="候选纠错笔记" value={data.candidate_count ?? 0} />
                <MetricMini label="仅高亮证据" value={data.supporting_evidence_count ?? 0} />
                <MetricMini label="对齐警告" value={data.unmatched_warning_count ?? 0} />
                <MetricMini label="未匹配笔记" value={alignmentWarningKeys.join(", ") || "0"} />
              </div>
              {scopeDisplay.longFullPrompt && (
                <p className="noteCorrectionInfoNotice">完整章审核 prompt 很长，推荐改用小节或 batch 审核。</p>
              )}
              {!scopeDisplay.isFull && (
                <p className="noteCorrectionInfoNotice">当前包只包含所选 scope 的 local_context、note_anchors、correction candidates 和返回 JSON schema。</p>
              )}
              <p className="unitSourceNotice">本 prompt 包含当前 scope 的原文 local_context / selected_text / note_text / chunk evidence，不只是笔记；生成与复制 prompt 不会保存 review。</p>
              {!!alignmentWarningKeys.length && (
                <p className="noteCorrectionWarning">
                  存在 {alignmentWarningKeys.length} 条未匹配/对齐警告笔记：{alignmentWarningKeys.join(", ")}。后续纠错审核需谨慎。
                </p>
              )}
              <div className="noteCorrectionPreviewGrid">
                {(data.preview_candidates || []).map((candidate) => (
                  <NoteCorrectionCandidatePreview key={candidate.note_id || candidate.zotero_annotation_key} candidate={candidate} />
                ))}
              </div>
              {!!(data.supporting_evidence_preview || []).length && (
                <div className="supportingEvidencePreview">
                  <strong>supporting evidence · 不进入 correction candidates</strong>
                  {(data.supporting_evidence_preview || []).map((item) => (
                    <div key={item.source_note_id || item.zotero_annotation_key} className="supportingEvidenceItem">
                      <span>p.{item.page || "?"} · matched_chunk_id={item.matched_chunk_id || "null"}</span>
                      <p>{item.selected_text_preview || "无 selected_text"}</p>
                    </div>
                  ))}
                </div>
              )}
              <details className="noteCorrectionDeveloperDetails">
                <summary>开发者详情：raw package JSON / interleaved view</summary>
                <pre>{JSON.stringify(data.package_json || data, null, 2)}</pre>
              </details>
            </div>
          )}
        </div>

        <div className="noteCorrectionRoundTripBlock">
          <div className="noteCorrectionRoundTripHeader">
            <span>json</span>
            <div>
              <strong>粘贴返回 JSON</strong>
              <p>把 ChatGPT 返回的 JSON 粘贴到这里。粘贴和校验不会写入数据库；人工审计完成后仍需显式点击保存按钮。</p>
            </div>
          </div>
          <label className="noteCorrectionReviewPaste">
            <span>ChatGPT 返回的 note_correction_review JSON</span>
            <textarea
              value={reviewText}
              onChange={(event) => onReviewTextChange(event.target.value, activeScope)}
              placeholder='粘贴 {"review_type":"note_correction_review", ...} JSON'
              rows={8}
            />
          </label>
          <div className="noteCorrectionCopyRow">
            <button type="button" onClick={() => onValidateReview(activeScope)} disabled={!reviewText.trim() || validating}>
              {validating ? "校验中..." : "校验返回 JSON"}
            </button>
            <button type="button" className="noteCorrectionDisabledAction" disabled>
              保存审核结果：需先完成人工审计
            </button>
          </div>
          <p className="unitSourceNotice">校验只生成归一化预览，不会自动保存；进入人工审计并显式确认后才可保存 correction review。</p>
          {validationState?.status === "error" && <StateMessage title="返回 JSON 校验失败" body={validationState.error} />}
          {validation && (
            <NoteCorrectionReviewValidationResult
              validation={validation}
              packageData={data}
              filter={reviewFilter}
              filteredItems={filteredReviewItems}
              saveState={saveState}
              saveReadinessState={saveReadinessState}
              activeScope={activeScope}
              mergePreview={mergePreview}
              savedReviewState={savedReviewState}
              onSetFilter={(filter) => onSetReviewFilter(filter, activeScope)}
              onLoadSaveReadiness={() => onLoadSaveReadiness?.(activeScope)}
              onSaveReview={(payload) => onSaveReview?.(activeScope, payload)}
            />
          )}
        </div>
      </div>
    </section>
  );
}

function ReviewModePlanner({
  planState,
  plan,
  selectedMode,
  batchSize,
  selectedSectionId,
  selectedBatchIndex,
  onUseRecommended,
  onSelectMode,
  onSelectSection,
  onSelectBatchSize,
  onSelectBatch,
}) {
  const loading = planState?.status === "loading";
  const sections = plan?.sections || [];
  const batches = plan?.batch_plans?.[String(batchSize)] || [];
  return (
    <div className="noteCorrectionReviewModePlanner" aria-label="选择 ChatGPT 笔记纠错审核方式">
      <div className="noteCorrectionRoundTripHeader">
        <span>mode</span>
        <div>
          <strong>选择 ChatGPT 笔记纠错审核方式</strong>
          <p>系统先推荐审核粒度，但用户可以改成整章、小节或固定数量 batch。</p>
        </div>
      </div>
      {loading && <p className="unitSourceNotice">正在生成 review mode planner...</p>}
      {planState?.status === "error" && <StateMessage title="审核方式推荐失败" body={planState.error} />}
      {plan && (
        <div className="noteCorrectionRecommendationCard">
          <div className="unitProcessingMetrics">
            <MetricMini label="推荐方式" value={reviewModeLabel(plan.recommended_mode)} />
            <MetricMini label="本章笔记" value={`${plan.total_candidate_count ?? 0} 条`} />
            <MetricMini label="小节数" value={plan.section_count ?? 0} />
            <MetricMini label="完整 prompt" value={`${plan.estimated_full_prompt_chars ?? 0} chars`} />
            <MetricMini label="PN68 warning" value={(plan.unmatched_warning_keys || []).join(", ") || "无"} />
          </div>
          <p className="noteCorrectionInfoNotice">推荐原因：{plan.recommendation_reason}</p>
          <button type="button" onClick={onUseRecommended}>
            使用推荐方式
          </button>
        </div>
      )}
      <div className="noteCorrectionModeOptions">
        <button type="button" className={selectedMode === "full_chapter" ? "active" : ""} onClick={() => onSelectMode("full_chapter")}>
          <strong>整章一次审核</strong>
          <span>上下文完整，但长输出可能截断。</span>
        </button>
        <button type="button" className={selectedMode === "section_scoped" ? "active" : ""} onClick={() => onSelectMode("section_scoped")}>
          <strong>按小节审核</strong>
          <span>保留语义结构，更稳。</span>
        </button>
        <button type="button" className={selectedMode === "fixed_size_batch" ? "active" : ""} onClick={() => onSelectMode("fixed_size_batch")}>
          <strong>按数量 batch 审核</strong>
          <span>最均匀，适合没有清晰小节结构。</span>
        </button>
      </div>
      {selectedMode === "section_scoped" && (
        <div className="noteCorrectionScopeList" aria-label="section list">
          {sections.map((section) => (
            <button
              key={section.section_id}
              type="button"
              className={selectedSectionId === section.section_id ? "active" : ""}
              onClick={() => onSelectSection(section.section_id)}
            >
              <strong>{section.section_title || section.section_label}</strong>
              <span>{section.candidate_count} notes</span>
              {section.has_pn68yptt && <em className="noteCorrectionPn68Badge">PN68YPTT warning</em>}
            </button>
          ))}
        </div>
      )}
      {selectedMode === "fixed_size_batch" && (
        <div className="noteCorrectionBatchPlanner" aria-label="batch planner">
          <label>
            <span>batch size</span>
            <select value={batchSize} onChange={(event) => onSelectBatchSize(Number(event.target.value))}>
              {[10, 15, 20].map((size) => <option key={size} value={size}>{size}</option>)}
            </select>
          </label>
          <div className="noteCorrectionScopeList">
            {batches.map((batch) => (
              <button
                key={batch.batch_id}
                type="button"
                className={selectedBatchIndex === batch.batch_index ? "active" : ""}
                onClick={() => onSelectBatch(batch.batch_index)}
              >
                <strong>batch {batch.batch_index + 1}</strong>
                <span>{batch.candidate_count} notes</span>
                {batch.has_pn68yptt && <em className="noteCorrectionPn68Badge">PN68YPTT warning</em>}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ChapterMergePreview({ mergePreview }) {
  if (!mergePreview) return null;
  const noValidatedScopes = mergePreview.validation_count === 0;
  return (
    <div className="noteCorrectionMergePreview" aria-label="章级 merge preview">
      <div className="noteCorrectionValidationSummary">
        <strong>章级 merge preview</strong>
        <span>expected_total={mergePreview.expected_total}</span>
        <span>validated_items={mergePreview.validated_items}</span>
        <span>missing={mergePreview.missing_note_ids.length}</span>
        <span>duplicate={mergePreview.duplicate_note_ids.length}</span>
        <span>unexpected={mergePreview.unexpected_note_ids.length}</span>
        <span>pn68_status={mergePreview.pn68_status}</span>
        <span>all_valid={String(mergePreview.all_valid)}</span>
      </div>
      {noValidatedScopes && (
        <p className="noteCorrectionInfoNotice">尚未校验任何小节 / batch。</p>
      )}
      {mergePreview.all_valid && (
        <p className="noteCorrectionSuccessNotice">{mergePreview.expected_total}/{mergePreview.expected_total} 条笔记纠错审核已校验通过，但尚未写入数据库。</p>
      )}
      {!noValidatedScopes && <CompletenessNoteIdList label="chapter_missing_note_ids" ids={mergePreview.missing_note_ids} />}
      {!noValidatedScopes && <CompletenessNoteIdList label="chapter_duplicate_note_ids" ids={mergePreview.duplicate_note_ids} />}
      {!noValidatedScopes && <CompletenessNoteIdList label="chapter_unexpected_note_ids" ids={mergePreview.unexpected_note_ids} />}
    </div>
  );
}

export function buildNoteCorrectionCopyPrompt(payload) {
  const packageJson = payload.package_json || payload;
  if (payload.copy_ready_prompt) return payload.copy_ready_prompt;
  if (packageJson.copy_ready_prompt) return packageJson.copy_ready_prompt;
  const schema = packageJson.output_schema || {};
  const context = packageJson.chapter_context || {};
  const promptPackage = {
    task: "note_correction_review",
    review_mode: packageJson.review_mode || "full_chapter",
    document: packageJson.document || {},
    unit: packageJson.unit || {},
    scope_metadata: packageJson.scope_metadata || packageJson.scope || {},
    local_context: {
      context_scope: context.context_scope,
      text: context.local_context || context.chapter_markdown || "",
    },
    note_anchors: slimPromptAnchors(packageJson.note_anchors || []),
    correction_candidates: slimPromptCandidates(packageJson.correction_candidates || []),
    output_rules: {
      root_allowed_keys: ["note_correction_review"],
      root_shape: "single_root_field_only",
      do_not_add_root_sibling_fields: true,
      expected_count: packageJson.expected_count || packageJson.correction_candidates?.length || 0,
    },
  };
  return [
    "# NOTEBOOK_AI 笔记纠错审核输入提示词",
    "",
    "## 审核任务说明",
    "这里生成的是发给 ChatGPT 的输入，不是审核结果。请只做 note_correction_review，并返回 JSON。",
    "本 prompt 包含当前 scope 的原文 local_context / selected_text / note_text / chunk evidence，不只是笔记。",
    "root 只能有一个字段：note_correction_review；根对象不要额外输出 sibling 字段。",
    "",
    "## 禁止事项",
    "禁止生成 classification/object/relation/mechanism。",
    "禁止写入 NOTEBOOK_AI、Zotero、PDF、tags、数据库或 vector store。",
    "",
    "## 输出 JSON schema",
    JSON.stringify(schema, null, 2),
    "",
    "## PN68YPTT unmatched warning",
    "PN68YPTT 未匹配到 chunk，后续纠错审核需谨慎。",
    "PN68YPTT 的 anchor_method 必须保持 unmatched / alignment_uncertain，不得假装已对齐正文。",
    "",
    "## 精简 ChatGPT 输入包",
    JSON.stringify(promptPackage, null, 2),
  ].join("\n");
}

function slimPromptAnchors(anchors) {
  return anchors.map((anchor) => ({
    note_anchor_id: anchor.note_anchor_id,
    server_note_id: anchor.server_note_id,
    client_note_id: anchor.client_note_id,
    zotero_annotation_key: anchor.zotero_annotation_key,
    page: anchor.page,
    matched_chunk_id: anchor.matched_chunk_id,
    anchor_method: anchor.anchor_method,
    evidence_alignment_status: anchor.evidence_alignment_status,
    alignment_confidence: anchor.alignment_confidence,
    warnings: reviewPromptWarnings(anchor),
  }));
}

function slimPromptCandidates(candidates) {
  return candidates.map((item) => ({
    note_id: item.note_id,
    server_note_id: item.server_note_id,
    client_note_id: item.client_note_id,
    zotero_annotation_key: item.zotero_annotation_key,
    page: item.page,
    note_anchor_id: item.note_anchor_id,
    selected_text: item.selected_text || "",
    note_text: item.note_text || "",
    matched_chunk_id: item.matched_chunk_id,
    chunk_heading_path: item.chunk_heading_path,
    chunk_evidence_text: promptExcerpt(item.chunk_evidence_text, 900),
    evidence_alignment_status: item.evidence_alignment_status,
    alignment_confidence: item.alignment_confidence,
    warnings: reviewPromptWarnings(item),
    reviewer_warning: item.reviewer_warning || null,
  }));
}

function reviewPromptWarnings(item) {
  const allowed = new Set([
    "unmatched_user_note",
    "alignment_uncertain",
    "document_resolved_but_no_page_text_match",
  ]);
  const warnings = (item.warnings || []).filter((warning) => allowed.has(String(warning)));
  if (item.evidence_alignment_status === "unmatched") warnings.push("evidence_alignment_status=unmatched");
  if (Number(item.alignment_confidence) < 0.5) warnings.push("low confidence alignment");
  return Array.from(new Set(warnings));
}

function promptExcerpt(value, limit) {
  const text = String(value || "").trim();
  if (text.length <= limit) return text;
  return `${text.slice(0, Math.max(0, limit - 16)).trim()}\n[truncated]`;
}

function NoteCorrectionCandidatePreview({ candidate }) {
  return (
    <article className="noteCorrectionCandidatePreview">
      <div className="noteCorrectionCandidateMeta">
        <span>p.{candidate.page || "?"}</span>
        <span>matched_chunk_id={candidate.matched_chunk_id || "null"}</span>
        <span>{candidate.zotero_annotation_key || "annotation_key_unknown"}</span>
      </div>
      <div className="noteCorrectionCandidateText">
        <span>note_text</span>
        <p>{candidate.note_text_preview || "无笔记文本"}</p>
      </div>
      <div className="noteCorrectionCandidateText">
        <span>selected_text</span>
        <p>{candidate.selected_text_preview || "无选中文本"}</p>
      </div>
      {!!(candidate.warnings || []).length && (
        <div className="noteCorrectionWarnings">
          {(candidate.warnings || []).map((warning) => (
            <span key={warning}>{warning}</span>
          ))}
        </div>
      )}
    </article>
  );
}

function buildScopeDisplay(data, mode, activeScope) {
  const packageJson = data?.package_json || {};
  const metadata = data?.scope_metadata || packageJson.scope_metadata || {};
  const context = data?.chapter_context_summary || {};
  const strategy = data?.prompt_size_strategy || packageJson.prompt_size_strategy || {};
  const reviewMode = data?.review_mode || metadata.mode || mode || activeScope?.review_mode || "full_chapter";
  const isFull = reviewMode === "full_chapter";
  const scopeLabel = metadata.scope_title
    || metadata.scope_id
    || packageJson.scope_title
    || activeScope?.section_id
    || (activeScope?.review_mode === "fixed_size_batch" ? `batch ${Number(activeScope.batch_index || 0) + 1}` : "full chapter");
  const expectedCount = metadata.expected_count ?? data?.expected_count ?? data?.candidate_count ?? 0;
  const scopedChunkCount = metadata.scoped_chunk_count ?? data?.scoped_chunk_count ?? context.scoped_chunk_count ?? context.chunk_count ?? 0;
  const estimatedChars = metadata.estimated_scoped_prompt_chars
    ?? data?.estimated_scoped_prompt_chars
    ?? strategy.estimated_scoped_prompt_chars
    ?? strategy.estimated_prompt_chars_without_schema
    ?? 0;
  return {
    isFull,
    modeLabel: reviewModeLabel(reviewMode),
    scopeLabel,
    expectedCount,
    scopedChunkCount,
    estimatedChars,
    longFullPrompt: isFull && strategy.chunked_package_recommended,
  };
}

function reviewModeLabel(mode) {
  if (mode === "section_scoped") return "按小节审核";
  if (mode === "fixed_size_batch") return "按数量分批";
  return "整章审核";
}

function buildActiveReviewScope(chapter, mode, plan, selectedSectionId, batchSize, selectedBatchIndex) {
  if (mode === "section_scoped") {
    const sectionId = selectedSectionId || plan?.sections?.[0]?.section_id || "";
    return { review_mode: "section_scoped", section_id: sectionId };
  }
  if (mode === "fixed_size_batch") {
    return { review_mode: "fixed_size_batch", batch_size: batchSize || 15, batch_index: selectedBatchIndex || 0 };
  }
  return { review_mode: "full_chapter", chapter_id: chapter?.chapter_id };
}

function noteCorrectionPanelScopeKey(chapter, scope) {
  const chapterId = String(chapter?.chapter_id || "");
  if (!chapterId) return "";
  if (scope?.review_mode === "section_scoped") return `${chapterId}:section:${scope.section_id || ""}`;
  if (scope?.review_mode === "fixed_size_batch") return `${chapterId}:batch:${scope.batch_size || 15}:${scope.batch_index || 0}`;
  return chapterId;
}

function buildChapterMergePreview(plan, validationStates, chapterId, mode, batchSize) {
  if (!plan) return null;
  const expectedIds = expectedNoteIdsForMode(plan, mode, batchSize);
  const validatedIds = new Set();
  const duplicateIds = new Set();
  const unexpectedIds = new Set();
  let pn68Status = "missing";
  let validationCount = 0;
  for (const [key, state] of Object.entries(validationStates || {})) {
    if (!scopeKeyBelongsToMode(key, chapterId, mode, batchSize)) continue;
    const validation = state?.data;
    if (!validation) continue;
    validationCount += 1;
    for (const id of validation.completeness?.duplicate_note_ids || []) duplicateIds.add(id);
    for (const id of validation.completeness?.unexpected_note_ids || []) unexpectedIds.add(id);
    if (validation.valid !== true) continue;
    for (const item of validation.normalized_preview || []) {
      const id = item.matched_expected_note_id || item.primary_note_id || item.server_note_id || item.client_note_id || item.note_id;
      if (id) validatedIds.add(id);
      if (item.zotero_annotation_key === "PN68YPTT") pn68Status = item.correction_status || "validated";
    }
  }
  const missing = expectedIds.filter((id) => !validatedIds.has(id));
  const allValid = expectedIds.length > 0 && validatedIds.size === expectedIds.length && missing.length === 0 && duplicateIds.size === 0 && unexpectedIds.size === 0;
  return {
    expected_total: plan.total_candidate_count || expectedIds.length,
    validated_items: validatedIds.size,
    missing_note_ids: missing,
    duplicate_note_ids: Array.from(duplicateIds),
    unexpected_note_ids: Array.from(unexpectedIds),
    pn68_status: pn68Status,
    all_valid: allValid,
    validation_count: validationCount,
  };
}

function expectedNoteIdsForMode(plan, mode, batchSize) {
  if (mode === "fixed_size_batch") {
    return (plan.batch_plans?.[String(batchSize || 15)] || []).flatMap((batch) => batch.note_ids || []);
  }
  return (plan.sections || []).flatMap((section) => section.note_ids || []);
}

function scopeKeyBelongsToMode(key, chapterId, mode, batchSize) {
  const chapterKey = String(chapterId || "");
  if (!chapterKey) return false;
  if (mode === "section_scoped") return key.startsWith(`${chapterKey}:section:`);
  if (mode === "fixed_size_batch") return key.startsWith(`${chapterKey}:batch:${batchSize || 15}:`);
  return key === chapterKey;
}
