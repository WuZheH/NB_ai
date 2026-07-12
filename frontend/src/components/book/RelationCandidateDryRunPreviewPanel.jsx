import StateMessage from "../StateMessage.jsx";

export default function RelationCandidateDryRunPreviewPanel({
  chapter,
  packageState,
  onPreview,
}) {
  const data = packageState?.data || null;
  const loading = packageState?.status === "loading";
  const candidates = data?.relation_candidates || [];

  return (
    <section className="noteCorrectionPackagePanel" aria-label="Relation candidate dry-run preview">
      <div className="noteCorrectionPackageHeader">
        <div>
          <span>Step 7 · Phase7G relation candidate dry-run</span>
          <strong>Relation candidate dry-run ready from approved object candidates</strong>
        </div>
      </div>
      <p className="unitSourceNotice">
        本阶段只读生成 relation candidate dry-run package；不保存 relation rows，不生成 mechanism，不写 object registry / Zotero / vector，不调用 LLM。
      </p>
      <div className="noteCorrectionCopyRow">
        <button type="button" onClick={onPreview} disabled={!chapter?.chapter_id || loading}>
          {loading ? "检查中..." : "Preview relation candidate dry-run"}
        </button>
      </div>
      {packageState?.status === "error" && <StateMessage title="Relation candidate dry-run failed" body={packageState.error} />}
      {data && (
        <div className="noteCorrectionPackageBody">
          <div className="unitProcessingMetrics">
            <MetricMini label="ready" value={String(data.ready)} />
            <MetricMini label="human_review_id" value={data.source_object_candidate_human_review_id || "not saved"} />
            <MetricMini label="approved source candidates" value={String(data.approved_source_candidate_count || 0)} />
            <MetricMini label="relation_candidate_count" value={String(data.relation_candidate_count || 0)} />
            <MetricMini label="excluded rejected" value={String(data.excluded_rejected_count || 0)} />
            <MetricMini label="excluded pending" value={String(data.excluded_pending_count || 0)} />
            <MetricMini label="PN68 excluded" value={data.pn68_excluded ? "yes" : "no"} />
            <MetricMini label="validator valid" value={String(data.validator_result?.valid)} />
          </div>

          <div className="reviewGateSummary" aria-label="relation candidate dry-run saved gate">
            <strong>Relation candidate dry-run ready</strong>
            <span>approved source candidates={Number(data.approved_source_candidate_count || 0)}</span>
            <span>relation_candidate_count={Number(data.relation_candidate_count || 0)}</span>
            <span>excluded rejected={Number(data.excluded_rejected_count || 0)}</span>
            <span>excluded pending={Number(data.excluded_pending_count || 0)}</span>
            <span>PN68 excluded: {data.pn68_excluded ? "yes" : "no"}</span>
            <span>Save relation disabled / future-gated</span>
            <span>mechanism locked</span>
          </div>

          <div className="noteCorrectionPreviewGrid">
            {candidates.slice(0, 6).map((candidate) => (
              <RelationCandidateCard key={candidate.relation_temp_id} candidate={candidate} />
            ))}
            {!candidates.length && (
              <article className="noteCorrectionCandidatePreview">
                <div className="noteCorrectionCandidateMeta">
                  <span>No relation candidate dry-run results</span>
                  <span>{data.reason || data.status}</span>
                </div>
                <p>没有把 locked/planned 状态伪装成真实关系；关系保存仍需后续 Phase7H gate。</p>
              </article>
            )}
          </div>

          <div className="reviewGateSummary">
            <strong>Safety boundary</strong>
            <span>should_save=false</span>
            <span>relation_rows_written=false</span>
            <span>relation_generated=false</span>
            <span>mechanism_generated=false</span>
            <span>object_registry_write_performed=false</span>
            <span>llm_called=false</span>
            <span>zotero_write_performed=false</span>
            <span>vector_write_performed=false</span>
          </div>
        </div>
      )}
    </section>
  );
}

function RelationCandidateCard({ candidate = {} }) {
  return (
    <article className="noteCorrectionCandidatePreview">
      <div className="noteCorrectionCandidateMeta">
        <span>{candidate.relation_type || "relation"}</span>
        <span>should_save={String(candidate.should_save)}</span>
      </div>
      <p>{candidate.subject_object_name}{" -> "}{candidate.object_object_name}</p>
      <p className="unitSourceNotice">{candidate.rationale}</p>
      <div className="reviewGateSummary">
        <span>sources: {(candidate.source_server_note_ids || []).length}</span>
        <span>chunks: {(candidate.evidence_chunk_ids || []).join(", ") || "n/a"}</span>
        <span>pages: {(candidate.page_labels || []).join(", ") || "n/a"}</span>
        <span>confidence: {candidate.confidence}</span>
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
