import StateMessage from "../../../shared/components/StateMessage.jsx";

function mappingColor(status) {
  if (status === "mapped") return "#2e7d32";
  if (status === "partial") return "#1565c0";
  if (status === "failed") return "#c62828";
  if (status === "skipped") return "#9e9e9e";
  return "#6d6d6d";
}

function matchColor(matchType) {
  if (matchType === "exact") return "#2e7d32";
  if (matchType === "normalized") return "#1565c0";
  if (matchType === "nearby_page") return "#e65100";
  if (matchType === "fallback") return "#f9a825";
  return "#c62828";
}

export default function RemapWarningPanel({
  confirmRemapFailed,
  remapPhase,
  remapPreview,
  onContinue,
  onCancel,
}) {
  const showPreview = remapPreview
    && !remapPreview.error
    && remapPhase
    && remapPhase.status !== "pending"
    && remapPhase.status !== "running";
  return (
    <>
      {confirmRemapFailed && remapPhase?.status === "warning" && (
        <div className="remapWarningConfirm">
          <p>
            ⚠ 证据映射预览发现 <strong>{remapPhase.result?.summary?.failed || 0}</strong> 条映射失败。
            部分对象可能无法正确关联到已入库的证据片段。
          </p>
          <div className="remapWarningActions">
            <button type="button" className="primaryButton" onClick={onContinue}>
              仍然提交对象
            </button>
            <button type="button" className="quietButton" onClick={onCancel}>
              取消
            </button>
          </div>
        </div>
      )}

      {showPreview && (
        <div className="remapPreviewSection">
          <div className="sectionHeader">
            <span>document_id={remapPreview.document_id} · {remapPreview.object_count} 对象 · chunks={remapPreview.chunk_index_size}</span>
          </div>
          {remapPreview.summary && (
            <div className="remapSummaryBar">
              <span style={{ color: "#2e7d32" }}>mapped: {remapPreview.summary.mapped}</span>
              <span style={{ color: "#1565c0" }}>partial: {remapPreview.summary.partial}</span>
              <span style={{ color: "#c62828" }}>failed: {remapPreview.summary.failed}</span>
              <span style={{ color: "#6d6d6d" }}>not_mapped: {remapPreview.summary.not_mapped}</span>
              <span className="safetyNote">core_db_write_performed: {String(remapPreview.core_db_write_performed)}</span>
            </div>
          )}
          <div className="reviewObjectList">
            {(remapPreview.objects || []).map(object => (
              <article key={object.object_key} className="reviewObjectCard">
                <div className="reviewCardHeader">
                  <div className="cardMeta">
                    <span>{object.object_name}</span>
                    <span>{object.review_status}</span>
                    <span style={{ fontWeight: 600, color: mappingColor(object.mapping_status) }}>
                      {object.mapping_status}
                    </span>
                  </div>
                  {object.mapped_chunk_ids.length > 0 && (
                    <code className="objectKeyLabel">chunks: [{object.mapped_chunk_ids.join(", ")}]</code>
                  )}
                  {(object.warnings || []).length > 0 && (
                    <div className="reviewWarnings">
                      {object.warnings.map((warning, warningIndex) => (
                        <span key={warningIndex}>{typeof warning === "string" ? warning : warning.warning || warning.message || JSON.stringify(warning)}</span>
                      ))}
                    </div>
                  )}
                </div>
                {(object.evidence_ref_results || []).length > 0 && (
                  <details className="remapRefDetails">
                    <summary>证据映射详情（{object.evidence_ref_results.length} 条）</summary>
                    {object.evidence_ref_results.map((ref, refIndex) => (
                      <div
                        key={refIndex}
                        className="remapRefRow"
                        style={{
                          borderLeft: `3px solid ${matchColor(ref.match_type)}`,
                          padding: "4px 8px",
                          margin: "4px 0",
                          fontSize: "0.85rem",
                        }}
                      >
                        <div><strong>{ref.section_title || "(no section)"}</strong> · p.{ref.pdf_page} · {ref.match_type}</div>
                        <div style={{ color: "#666", fontStyle: "italic" }}>&quot;{ref.quote_text_short?.substring(0, 80)}{ref.quote_text_short?.length > 80 ? "..." : ""}&quot;</div>
                        {ref.matched_chunk_id && <div>→ chunk #{ref.matched_chunk_id}</div>}
                        {ref.warning && <div style={{ color: "#c62828" }}>⚠ {ref.warning}</div>}
                      </div>
                    ))}
                  </details>
                )}
              </article>
            ))}
          </div>
        </div>
      )}

      {remapPreview?.error && <StateMessage title="映射预览失败" body={remapPreview.error} />}
    </>
  );
}
