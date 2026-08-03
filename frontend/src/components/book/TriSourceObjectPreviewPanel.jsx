import StateMessage from "../StateMessage.jsx";

export default function TriSourceObjectPreviewPanel({
  chapter,
  packageState,
  onPreview,
}) {
  const data = packageState?.data || null;
  const loading = packageState?.status === "loading";
  const candidates = data?.candidates || [];
  const quarantinedItems = data?.quarantined_items || [];
  const labelDistribution = data?.label_distribution || {};
  const draftsSaved = data?.object_candidate_draft_review_status === "pending_human_review"
    || Number(data?.object_candidate_draft_saved_count || 0) > 0;
  const humanReviewSaved = data?.object_candidate_human_review_status === "saved"
    || Number(data?.object_candidate_human_review_saved_count || 0) > 0;

  return (
    <section className="noteCorrectionPackagePanel" aria-label="Object candidate dry-run preview">
      <div className="noteCorrectionPackageHeader">
        <div>
          <span>Step 6 · Phase7D object candidate dry-run</span>
          <strong>Object candidate dry-run ready from saved classification review</strong>
        </div>
      </div>
      <p className="unitSourceNotice">
        本阶段只读展示对象候选 dry-run package，不保存 object_candidates，不生成关系或机制，不调用 LLM。
      </p>
      <div className="noteCorrectionCopyRow">
        <button type="button" onClick={onPreview} disabled={!chapter?.chapter_id || loading}>
          {loading ? "检查中..." : "Preview object candidate dry-run"}
        </button>
      </div>
      {packageState?.status === "error" && <StateMessage title="Object candidate dry-run failed" body={packageState.error} />}
      {data && (
        <div className="noteCorrectionPackageBody">
          <div className="unitProcessingMetrics">
            <MetricMini label="ready" value={String(data.ready)} />
            <MetricMini label="classification_review_id" value={data.source_classification_review_id || "not saved"} />
            <MetricMini label="source_item_count" value={String(data.source_item_count || 0)} />
            <MetricMini label="candidate_count" value={String(data.candidate_count || 0)} />
            <MetricMini label="quarantined_count" value={String(data.quarantined_count || 0)} />
            <MetricMini label="PN68 quarantined" value={data.pn68_quarantined ? "yes" : "no"} />
            <MetricMini label="draft_review_status" value={data.object_candidate_draft_review_status || "not_saved"} />
            <MetricMini label="draft_saved_count" value={String(data.object_candidate_draft_saved_count || 0)} />
            <MetricMini label="human_review_status" value={data.object_candidate_human_review_status || "not_saved"} />
            <MetricMini label="approved_candidates" value={String(data.approved_candidate_count || 0)} />
            <MetricMini label="object_candidates_generated" value={String(data.object_candidates_generated)} />
            <MetricMini label="relation_generated" value={String(data.relation_generated)} />
            <MetricMini label="mechanism_generated" value={String(data.mechanism_generated)} />
          </div>

          {draftsSaved && (
            <div className="reviewGateSummary" aria-label="object candidate drafts saved">
              <strong>Object candidate drafts saved</strong>
              <span>object_candidate_review_id: {data.object_candidate_draft_review_id || "saved"}</span>
              <span>saved_candidate_count: {Number(data.object_candidate_draft_saved_count || 0)}</span>
              <span>pending human review</span>
              <span>PN68 quarantined: {data.pn68_quarantined ? "yes" : "no"}</span>
              <span>approved objects created: no</span>
              <span>relation/mechanism locked</span>
            </div>
          )}

          {humanReviewSaved && (
            <div className="reviewGateSummary" aria-label="object candidate human review saved">
              <strong>Object candidate human review saved</strong>
              <span>object_candidate_human_review_id: {data.object_candidate_human_review_id || "saved"}</span>
              <span>saved_item_count: {Number(data.object_candidate_human_review_saved_count || 0)}</span>
              <span>approved: {Number(data.approved_candidate_count || 0)}</span>
              <span>rejected: {Number(data.rejected_candidate_count || 0)}</span>
              <span>pending: {Number(data.pending_candidate_count || 0)}</span>
              <span>ready_for_relation_dry_run={String(data.ready_for_relation_dry_run)}</span>
              <span>relation/mechanism still locked</span>
            </div>
          )}

          <div className="reviewGateSummary" aria-label="object candidate dry-run label distribution">
            <strong>Classification label distribution</strong>
            {Object.entries(labelDistribution).map(([label, count]) => (
              <span key={label}>{label}: {count}</span>
            ))}
          </div>

          <div className="noteCorrectionPreviewGrid">
            {candidates.slice(0, 6).map((candidate) => (
              <ObjectCandidateCard key={candidate.candidate_temp_id || candidate.duplicate_group_key} candidate={candidate} />
            ))}
            {!candidates.length && (
              <article className="noteCorrectionCandidatePreview">
                <div className="noteCorrectionCandidateMeta">
                  <span>No object candidate dry-run results</span>
                  <span>{data.reason || data.status}</span>
                </div>
                <p>没有把 locked/planned 状态伪装成 0 results；候选对象仍需后续 Phase7E gate。</p>
              </article>
            )}
          </div>

          <div className="reviewGateSummary" aria-label="object candidate quarantine summary">
            <strong>PN68 quarantine / manual review boundary</strong>
            <span>PN68 quarantined: {data.pn68_quarantined ? "yes" : "no"}</span>
            <span>quarantined_items: {quarantinedItems.length}</span>
            <span>needs_manual_review and unclear not auto-extracted</span>
            <small>{quarantinedItems[0]?.note_text_excerpt || "No quarantined note excerpt returned."}</small>
          </div>

          <div className="reviewGateSummary">
            <strong>Save/generate objects disabled</strong>
            <span>should_save=false</span>
            <span>object_review locked</span>
            <span>relation_candidates planned</span>
            <span>relation_generated=false</span>
            <span>mechanism_review locked</span>
            <span>mechanism_generated=false</span>
            <span>{draftsSaved ? "Phase7E draft save complete; Phase7F human review required" : "future Phase7E gate required"}</span>
          </div>
        </div>
      )}
    </section>
  );
}

function ObjectCandidateCard({ candidate = {} }) {
  return (
    <article className="noteCorrectionCandidatePreview">
      <div className="noteCorrectionCandidateMeta">
        <span>{candidate.object_type || "candidate"}</span>
        <span>should_save={String(candidate.should_save)}</span>
      </div>
      <p>{candidate.object_name}</p>
      <p className="unitSourceNotice">{candidate.rationale}</p>
      <div className="reviewGateSummary">
        <span>sources: {(candidate.source_server_note_ids || []).length}</span>
        <span>pages: {(candidate.page_labels || []).join(", ") || "n/a"}</span>
        <span>chunks: {(candidate.evidence_chunk_ids || []).join(", ") || "n/a"}</span>
        <span>{candidate.duplicate_group_key}</span>
      </div>
    </article>
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
