import StateMessage from "../StateMessage.jsx";
import { formatConfidence } from "../../shared/utils/display.js";

export default function ChapterNoteClassificationPanel({
  chapter,
  packageState,
  copied,
  reviewText,
  validationState,
  onPreview,
  onCopy,
  onReviewTextChange,
  onValidateReview,
}) {
  const data = packageState?.data || null;
  const loading = packageState?.status === "loading";
  const validating = validationState?.status === "loading";
  const validation = validationState?.data || null;
  const ready = !!data?.ready;
  const labels = data?.allowed_labels || (data?.classification_taxonomy || []).map((item) => item.label).filter(Boolean);
  const pn68 = data?.pn68 || data?.pn68_status || {};
  const itemCount = data?.item_count ?? data?.corrected_notes?.length ?? data?.candidate_count ?? 0;
  const classificationSaved = data?.note_classification_review_saved === true || data?.classification_review_saved === true;
  const classificationSavedItemCount = Number(data?.classification_saved_item_count || 0);

  function handleUploadJson(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => onReviewTextChange(String(reader.result || ""));
    reader.readAsText(file);
    event.target.value = "";
  }

  return (
    <section className="noteCorrectionPackagePanel" aria-label="笔记分类人工 round-trip">
      <div className="noteCorrectionPackageHeader">
        <div>
          <span>Step 4-5 · classification dry-run preview only</span>
          <strong>笔记分类 dry-run 预览</strong>
        </div>
      </div>
      <div className="noteCorrectionSafetyList">
        <span>不调用 LLM</span>
        <span>不写数据库</span>
        <span>不写 Zotero</span>
        <span>不生成对象</span>
        <span>不生成关系</span>
        <span>不生成机制</span>
      </div>
      {packageState?.status === "error" && <StateMessage title="笔记分类包检查失败" body={packageState.error} />}
      <div className="noteCorrectionRoundTripGrid">
        <div className="noteCorrectionRoundTripBlock">
          <div className="noteCorrectionRoundTripHeader">
            <span>4</span>
            <div>
              <strong>4. 生成笔记分类 dry-run package</strong>
              <p>读取 merged saved correction review；本阶段只生成 prompt preview，不调用 LLM、不保存分类结果。</p>
            </div>
          </div>
          <div className="noteCorrectionCopyRow">
            <button type="button" onClick={onPreview} disabled={!chapter?.chapter_id || loading}>
              {loading ? "检查中..." : "检查 / 预览 classification dry-run package"}
            </button>
            <button type="button" onClick={onCopy} disabled={!ready}>
              复制给 ChatGPT 的分类审核提示词（manual preview）
            </button>
            {copied && <span>已复制</span>}
          </div>
          {data && (
            <div className="noteCorrectionPackageBody">
              <div className="unitProcessingMetrics">
                <MetricMini label="ready" value={String(ready)} />
                <MetricMini label="reason" value={data.reason || "ready"} />
                <MetricMini label="dry-run" value={ready ? "Classification dry-run ready" : "blocked"} />
                <MetricMini label="notes ready" value={`${itemCount} notes ready`} />
                <MetricMini label="classification candidates" value={itemCount} />
                <MetricMini label="merged items" value={`${data.item_count ?? 0}/${data.merged_saved_review?.expected_count ?? data.candidate_count ?? 67}`} />
                <MetricMini label="unique server notes" value={data.unique_server_note_ids ?? 0} />
                <MetricMini label="source reviews" value={`${data.source_section_review_count ?? 0} section + ${data.canary_audit_count ?? 0} canary`} />
                <MetricMini label="PN68" value={pn68.warning_preserved ? "PN68 warning preserved" : pn68.status || "unknown"} />
                <MetricMini label="taxonomy" value={`${labels.length} labels`} />
                <MetricMini label="supporting evidence" value={data.supporting_evidence?.length ?? data.supporting_evidence_count ?? 0} />
                <MetricMini label="classification review" value={classificationSaved ? `saved ${classificationSavedItemCount}` : "not_saved"} />
              </div>
              {!ready && (
                <p className="noteCorrectionWarning">需要先保存笔记纠错审核结果：{data.reason || "note_correction_review_not_saved"}</p>
              )}
              {ready && (
                <>
                  <p className="unitSourceNotice">Classification dry-run ready · {itemCount} notes ready · Generate/Save classification disabled until a future explicit gate.</p>
                  {classificationSaved && <p className="noteCorrectionSuccessNotice">Classification review saved · {classificationSavedItemCount} items · review_id={data.classification_review_id}</p>}
                  {classificationSaved && <p className="noteCorrectionWarning">PN68 classification saved · {data.pn68_classification_label || "needs_manual_review"} · {data.pn68_classification_confidence || "unknown"} · alignment_uncertain preserved.</p>}
                  {classificationSaved && <p className="reviewGateSummary"><strong>Next step locked</strong><span>object candidate generation requires explicit Phase7D gate</span><span>relation_generated=false</span><span>mechanism_generated=false</span></p>}
                  <p className="unitSourceNotice">Labels: {labels.join(", ")}</p>
                  <p className="noteCorrectionWarning">PN68 warning preserved · recommended handling: {pn68.recommended_handling || "manual_review_or_unclear_classification"}</p>
                </>
              )}
              <details className="noteCorrectionDeveloperDetails">
                <summary>开发者详情：note classification package</summary>
                <pre>{JSON.stringify(data, null, 2)}</pre>
              </details>
            </div>
          )}
        </div>

        <div className="noteCorrectionRoundTripBlock">
          <div className="noteCorrectionRoundTripHeader">
            <span>5</span>
            <div>
              <strong>5. 手动粘贴 classification JSON 并校验</strong>
              <p>只调用 manual validator endpoint，返回 validation result + preview；不保存分类结果，不生成对象/关系/机制。</p>
            </div>
          </div>
          <label className="noteCorrectionReviewPaste">
            <span>Manual classification JSON paste area</span>
            <textarea
              value={reviewText}
              onChange={(event) => onReviewTextChange(event.target.value)}
              placeholder='粘贴 {"document_id":10,"chapter_id":69,"source_package_hash":"...","items":[{"server_note_id":"...","note_type":"memory_note","confidence":"medium","rationale":"...","preserve_original_note_text":true,"warnings":[]}]} JSON'
              rows={8}
              disabled={!ready}
            />
          </label>
          <label className="noteCorrectionReviewPaste">
            <span>Upload classification JSON file</span>
            <input type="file" accept="application/json,.json" onChange={handleUploadJson} disabled={!ready} />
          </label>
          <div className="noteCorrectionCopyRow">
            <button type="button" onClick={onValidateReview} disabled={!ready || !reviewText.trim() || validating}>
              {validating ? "校验中..." : "Validate manual classification JSON"}
            </button>
            <button type="button" disabled title="分类结果保存下一批次启用">
              {classificationSaved ? "Classification review saved · duplicate save disabled" : "保存分类审核结果：未启用 · Save classification disabled"}
            </button>
          </div>
          <div className="reviewGateSummary" aria-label="classification downstream locked state">
            <strong>Object / relation / mechanism remain locked</strong>
            <span>object_candidates_generated=false</span>
            <span>relation_generated=false</span>
            <span>mechanism_generated=false</span>
            <span>classification save: future phase only</span>
          </div>
          {validationState?.status === "error" && <StateMessage title="分类返回 JSON 校验失败" body={validationState.error} />}
          {validation && <ClassificationValidationResult validation={validation} />}
        </div>
      </div>
    </section>
  );
}

export function buildNoteClassificationCopyPrompt(payload) {
  if (payload?.copy_ready_prompt) return payload.copy_ready_prompt;
  return [
    "# Search 笔记分类审核输入提示词",
    "",
    "请只执行 note_classification_review。禁止生成 object/relation/mechanism。",
    "",
    "## 完整 note_classification package JSON",
    JSON.stringify(payload || {}, null, 2),
  ].join("\n");
}

function ClassificationValidationResult({ validation }) {
  const stats = validation.stats || {};
  const errors = validation.errors || [];
  const invalidItems = validation.invalid_items || [];
  const previewItems = validation.preview_items || validation.normalized_preview || [];
  const labelCounts = validation.label_counts || stats.primary_type_counts || {};
  const confidenceCounts = validation.confidence_counts || {};
  const pn68 = validation.pn68_validation || {};
  return (
    <div className={`noteCorrectionValidationResult ${validation.valid ? "valid" : "invalid"}`} aria-label="分类返回 JSON 校验结果">
      <div className="noteCorrectionValidationSummary">
        <strong>{validation.valid ? "Manual classification JSON valid" : "Manual classification JSON invalid"}</strong>
        <span>items={stats.item_count ?? validation.item_count ?? 0} / expected={stats.expected_item_count ?? validation.expected_item_count ?? 0}</span>
        <span>missing={stats.missing_count ?? validation.missing_count ?? 0}</span>
        <span>duplicate={stats.duplicate_count ?? validation.duplicate_count ?? 0}</span>
        <span>unexpected={stats.unexpected_count ?? validation.unexpected_count ?? 0}</span>
        <span>invalid labels={stats.invalid_label_count ?? validation.invalid_label_count ?? 0}</span>
      </div>
      {validation.valid && <p className="noteCorrectionSuccessNotice">校验通过，但本阶段不会保存笔记分类审核结果。</p>}
      <div className="reviewGateSummary" aria-label="classification validator distribution">
        <strong>Label counts</strong>
        {objectEntries(labelCounts).map(([label, count]) => <span key={label}>{label}={count}</span>)}
        {!objectEntries(labelCounts).length && <span>none</span>}
      </div>
      <div className="reviewGateSummary" aria-label="classification confidence distribution">
        <strong>Confidence counts</strong>
        {objectEntries(confidenceCounts).map(([label, count]) => <span key={label}>{label}={count}</span>)}
        {!objectEntries(confidenceCounts).length && <span>none</span>}
      </div>
      <p className={pn68.valid ? "noteCorrectionSuccessNotice" : "noteCorrectionWarning"}>
        PN68 validation: {pn68.present ? (pn68.valid ? "valid warning handling" : "invalid warning handling") : "missing"}
      </p>
      {!!errors.length && (
        <div className="noteCorrectionValidationErrors">
          {errors.map((error) => <span key={error}>{error}</span>)}
        </div>
      )}
      {!!invalidItems.length && (
        <div className="noteCorrectionValidationErrors" aria-label="invalid classification item list">
          <strong>Invalid items</strong>
          {invalidItems.slice(0, 12).map((item) => (
            <span key={`${item.server_note_id || item.index}-${(item.errors || []).join("|")}`}>
              {item.server_note_id || `row ${item.index}`} · {(item.errors || []).join("; ")}
            </span>
          ))}
          {invalidItems.length > 12 && <span>... {invalidItems.length - 12} more invalid items</span>}
        </div>
      )}
      <div className="noteCorrectionReviewPreviewList" aria-label="分类审核意见预览列表">
        {previewItems.slice(0, 24).map((item) => (
          <article key={`${item.zotero_annotation_key || item.server_note_id || item.note_id}-${item.note_type || item.primary_type}`} className="noteCorrectionReviewPreviewItem">
            <div className="noteCorrectionCandidateMeta">
              <span>{item.note_id || item.server_note_id || item.client_note_id || "note_id_unknown"}</span>
              <span>{item.zotero_annotation_key || "annotation_key_unknown"}</span>
              <span>{item.note_type || item.primary_type || "type_unknown"}</span>
              <span>confidence={formatConfidence(item.confidence)}</span>
              <span>{item.valid === false ? "invalid" : "preview"}</span>
            </div>
            <p>{item.rationale || item.classification_rationale || "无 rationale"}</p>
          </article>
        ))}
        {previewItems.length > 24 && <p className="unitSourceNotice">Preview shows first 24 / {previewItems.length} items.</p>}
      </div>
    </div>
  );
}

function MetricMini({ label, value }) {
  return (
    <span className="unitMetricMini">
      <em>{label}</em>
      <strong>{value}</strong>
    </span>
  );
}

function objectEntries(value) {
  if (!value || typeof value !== "object") return [];
  return Object.entries(value);
}
