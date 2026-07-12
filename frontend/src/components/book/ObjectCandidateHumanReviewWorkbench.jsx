import { useEffect, useMemo, useState } from "react";
import StateMessage from "../StateMessage.jsx";

const ACTIONS = ["approve", "reject", "edit", "merge", "pending"];

export default function ObjectCandidateHumanReviewWorkbench({
  chapter,
  workbenchState,
  validationState,
  onLoad,
  onValidate,
}) {
  const data = workbenchState?.data || null;
  const candidates = data?.candidates || [];
  const [reviewItems, setReviewItems] = useState({});
  const [filters, setFilters] = useState({
    objectType: "all",
    sourceLabel: "all",
    confidence: "all",
    duplicateGroup: "",
  });

  useEffect(() => {
    if (!data?.candidates?.length) return;
    setReviewItems((current) => {
      const next = {};
      data.candidates.forEach((candidate) => {
        const id = candidate.candidate_id || candidate.candidate_temp_id;
        next[id] = current[id] || {
          candidate_id: id,
          candidate_temp_id: id,
          action: candidate.current_human_action || candidate.suggested_action || "pending",
          object_name: candidate.current_object_name || candidate.object_name || "",
          object_type: candidate.current_object_type || candidate.object_type || "",
          merge_target_candidate_id: candidate.merge_target_candidate_id || "",
          human_note: candidate.human_note || "",
          relation_generated: false,
          mechanism_generated: false,
          zotero_write_performed: false,
          vector_write_performed: false,
        };
      });
      return next;
    });
  }, [data?.review_id, data?.human_review_id, data?.candidate_count]);

  const objectTypes = useMemo(() => sortedUnique(candidates.map((candidate) => candidate.object_type)), [candidates]);
  const sourceLabels = useMemo(
    () => sortedUnique(candidates.flatMap((candidate) => candidate.source_labels || [])),
    [candidates]
  );
  const filteredCandidates = useMemo(() => {
    return candidates.filter((candidate) => {
      const confidence = Number(candidate.confidence || 0);
      if (filters.objectType !== "all" && candidate.object_type !== filters.objectType) return false;
      if (filters.sourceLabel !== "all" && !(candidate.source_labels || []).includes(filters.sourceLabel)) return false;
      if (filters.confidence === "high" && confidence < 0.58) return false;
      if (filters.confidence === "low" && confidence > 0.52) return false;
      if (filters.duplicateGroup && !String(candidate.duplicate_group_key || "").includes(filters.duplicateGroup)) return false;
      return true;
    });
  }, [candidates, filters]);
  const reviewPayload = useMemo(() => buildReviewPayload(data, reviewItems), [data, reviewItems]);
  const actionCounts = useMemo(() => countActions(Object.values(reviewItems)), [reviewItems]);
  const validation = validationState?.data || null;
  const loading = workbenchState?.status === "loading";
  const validating = validationState?.status === "loading";
  const saved = data?.status === "human_review_saved";

  function updateCandidate(candidateId, patch) {
    setReviewItems((current) => ({
      ...current,
      [candidateId]: {
        ...(current[candidateId] || { candidate_id: candidateId, candidate_temp_id: candidateId }),
        ...patch,
      },
    }));
  }

  return (
    <section className="noteCorrectionPackagePanel" aria-label="Object candidate human review workbench">
      <div className="noteCorrectionPackageHeader">
        <div>
          <span>Step 7 · Phase7F object candidate human review</span>
          <strong>Review draft object candidates before relation prep</strong>
        </div>
      </div>
      <p className="unitSourceNotice">
        人工审核只保存候选审核结果；不生成 relation/mechanism，不写 Zotero/vector，不写正式 object registry。
      </p>
      <div className="noteCorrectionCopyRow">
        <button type="button" onClick={onLoad} disabled={!chapter?.chapter_id || loading}>
          {loading ? "读取中..." : "Load object review workbench"}
        </button>
        <button
          type="button"
          onClick={() => onValidate?.(reviewPayload)}
          disabled={!data?.candidate_count || validating}
        >
          {validating ? "Validating..." : "Validate human review"}
        </button>
        <button
          type="button"
          disabled
          title="Production save uses the one-shot Phase7F gate, not the ordinary UI."
        >
          Save review
        </button>
      </div>

      {workbenchState?.status === "error" && <StateMessage title="Object review workbench failed" body={workbenchState.error} />}
      {validationState?.status === "error" && <StateMessage title="Object review validation failed" body={validationState.error} />}

      {data && (
        <div className="noteCorrectionPackageBody">
          <div className="unitProcessingMetrics">
            <MetricMini label="status" value={data.status || "unknown"} />
            <MetricMini label="draft_review_id" value={data.object_candidate_draft_review_id || "not saved"} />
            <MetricMini label="candidate_count" value={String(data.candidate_count || 0)} />
            <MetricMini label="approved" value={String(saved ? data.approved_count || 0 : actionCounts.approve + actionCounts.edit)} />
            <MetricMini label="rejected" value={String(saved ? data.rejected_count || 0 : actionCounts.reject)} />
            <MetricMini label="merged" value={String(saved ? data.merged_count || 0 : actionCounts.merge)} />
            <MetricMini label="pending" value={String(saved ? data.pending_count || 0 : actionCounts.pending)} />
            <MetricMini label="PN68 quarantined" value={data.pn68_quarantined ? "yes" : "no"} />
          </div>

          <div className="reviewGateSummary" aria-label="object candidate human review safety boundary">
            <strong>{saved ? "Object candidate human review saved" : "Object candidate human review ready"}</strong>
            <span>PN68 source candidates: {Number(data.pn68_source_candidate_count || 0)}</span>
            <span>relation_generation_locked={String(data.relation_generation_locked)}</span>
            <span>mechanism_generation_locked={String(data.mechanism_generation_locked)}</span>
            <span>ready_for_relation_dry_run={String(data.ready_for_relation_dry_run)}</span>
            <span>ordinary save UI disabled; production one-shot gate required</span>
          </div>

          {validation && (
            <div className={`reviewGateSummary ${validation.valid ? "" : "locked"}`} aria-label="object candidate human review validation result">
              <strong>Validation {validation.valid ? "valid" : "blocked"}</strong>
              <span>approved={Number(validation.approved_count || 0)}</span>
              <span>rejected={Number(validation.rejected_count || 0)}</span>
              <span>merged={Number(validation.merged_count || 0)}</span>
              <span>pending={Number(validation.pending_count || 0)}</span>
              <span>errors={(validation.errors || []).join(", ") || "none"}</span>
            </div>
          )}

          <div className="reviewGateSummary" aria-label="object candidate workbench filters">
            <strong>Filters</strong>
            <label>
              <span>object_type</span>
              <select value={filters.objectType} onChange={(event) => setFilters((value) => ({ ...value, objectType: event.target.value }))}>
                <option value="all">all</option>
                {objectTypes.map((type) => <option key={type} value={type}>{type}</option>)}
              </select>
            </label>
            <label>
              <span>source_label</span>
              <select value={filters.sourceLabel} onChange={(event) => setFilters((value) => ({ ...value, sourceLabel: event.target.value }))}>
                <option value="all">all</option>
                {sourceLabels.map((label) => <option key={label} value={label}>{label}</option>)}
              </select>
            </label>
            <label>
              <span>confidence</span>
              <select value={filters.confidence} onChange={(event) => setFilters((value) => ({ ...value, confidence: event.target.value }))}>
                <option value="all">all</option>
                <option value="high">high &gt;= 0.58</option>
                <option value="low">low &lt;= 0.52</option>
              </select>
            </label>
            <label>
              <span>duplicate_group</span>
              <input
                value={filters.duplicateGroup}
                onChange={(event) => setFilters((value) => ({ ...value, duplicateGroup: event.target.value }))}
                placeholder="duplicate group key"
              />
            </label>
          </div>

          <div className="noteCorrectionPreviewGrid objectHumanReviewGrid">
            {filteredCandidates.map((candidate) => {
              const id = candidate.candidate_id || candidate.candidate_temp_id;
              const reviewItem = reviewItems[id] || {};
              return (
                <article className="noteCorrectionCandidatePreview" key={id}>
                  <div className="noteCorrectionCandidateMeta">
                    <span>{candidate.object_type || "candidate"}</span>
                    <span>confidence={candidate.confidence ?? "n/a"}</span>
                    <span>suggested={candidate.suggested_action || "pending"}</span>
                  </div>
                  <label className="objectCandidateField">
                    <span>object_name</span>
                    <input
                      value={reviewItem.object_name || ""}
                      onChange={(event) => updateCandidate(id, { object_name: event.target.value, action: reviewItem.action === "approve" ? "edit" : reviewItem.action })}
                    />
                  </label>
                  <label className="objectCandidateField">
                    <span>object_type</span>
                    <input
                      value={reviewItem.object_type || ""}
                      onChange={(event) => updateCandidate(id, { object_type: event.target.value, action: reviewItem.action === "approve" ? "edit" : reviewItem.action })}
                    />
                  </label>
                  <div className="noteCorrectionCopyRow">
                    {ACTIONS.map((action) => (
                      <button
                        key={action}
                        type="button"
                        className={reviewItem.action === action ? "primaryButton" : "quietButton"}
                        onClick={() => updateCandidate(id, { action })}
                      >
                        {action}
                      </button>
                    ))}
                  </div>
                  {reviewItem.action === "merge" && (
                    <label className="objectCandidateField">
                      <span>merge target</span>
                      <select
                        value={reviewItem.merge_target_candidate_id || ""}
                        onChange={(event) => updateCandidate(id, { merge_target_candidate_id: event.target.value })}
                      >
                        <option value="">select target</option>
                        {candidates.filter((target) => (target.candidate_id || target.candidate_temp_id) !== id).map((target) => {
                          const targetId = target.candidate_id || target.candidate_temp_id;
                          return <option key={targetId} value={targetId}>{target.object_name}</option>;
                        })}
                      </select>
                    </label>
                  )}
                  <label className="objectCandidateField">
                    <span>human_note</span>
                    <textarea
                      rows={2}
                      value={reviewItem.human_note || ""}
                      onChange={(event) => updateCandidate(id, { human_note: event.target.value })}
                    />
                  </label>
                  <p className="unitSourceNotice">{candidate.rationale}</p>
                  <div className="reviewGateSummary">
                    <span>sources: {(candidate.source_server_note_ids || []).length}</span>
                    <span>labels: {(candidate.source_labels || []).join(", ") || "n/a"}</span>
                    <span>pages: {(candidate.page_labels || []).join(", ") || "n/a"}</span>
                    <span>chunks: {(candidate.evidence_chunk_ids || []).join(", ") || "n/a"}</span>
                    <span>{candidate.duplicate_group_key}</span>
                    <span>PN68 source: {candidate.pn68_source ? "yes" : "no"}</span>
                  </div>
                </article>
              );
            })}
            {!filteredCandidates.length && (
              <article className="noteCorrectionCandidatePreview">
                <div className="noteCorrectionCandidateMeta">
                  <span>No candidates match filters</span>
                  <span>{data.status}</span>
                </div>
                <p>No fake object/relation/mechanism result is shown.</p>
              </article>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

function buildReviewPayload(data, reviewItems) {
  return {
    schema_version: "r3_object_candidate_human_review_v1",
    document_id: data?.document_id,
    chapter_id: data?.chapter_id,
    object_candidate_draft_review_id: data?.object_candidate_draft_review_id,
    source_classification_review_id: data?.source_classification_review_id,
    items: Object.values(reviewItems),
    relation_generated: false,
    mechanism_generated: false,
    zotero_write_performed: false,
    vector_write_performed: false,
    object_candidates_generated: false,
    approved_objects_created: false,
  };
}

function countActions(items) {
  return items.reduce(
    (counts, item) => {
      const action = item.action || "pending";
      counts[action] = Number(counts[action] || 0) + 1;
      return counts;
    },
    { approve: 0, reject: 0, edit: 0, merge: 0, pending: 0 }
  );
}

function sortedUnique(values) {
  return Array.from(new Set(values.filter(Boolean).map(String))).sort((a, b) => a.localeCompare(b));
}

function MetricMini({ label, value }) {
  return (
    <span className="unitMetricMini">
      <em>{label}</em>
      <strong>{value}</strong>
    </span>
  );
}
