import StateMessage from "../../../shared/components/StateMessage.jsx";
import RemapWarningPanel from "./RemapWarningPanel.jsx";

function commitPhaseRowClass(entry = {}) {
  return `commitPhaseRow commitPhaseRow--${entry.status || "pending"}`;
}

function PhaseIcon({ entry = {} }) {
  const status = entry.status || "pending";
  const icons = {
    pending: "○",
    running: "◌",
    ok: "✓",
    already_committed: "✓",
    warning: "⚠",
    error: "✗",
  };
  return <span className={`commitPhaseIcon commitPhaseIcon--${status}`}>{icons[status] || "○"}</span>;
}

function phaseLabel(entry = {}) {
  const labels = {
    pending: "等待中",
    running: "进行中...",
    ok: "已完成",
    already_committed: "已提交（无重复写入）",
    warning: "需确认",
    error: "失败",
  };
  return labels[entry.status || "pending"] || entry.status;
}

function pipelinePhaseSummary(phase = {}) {
  const completed = [phase.paper, phase.remap, phase.objects].filter(entry => (
    entry?.status === "ok" || entry?.status === "already_committed" || entry?.status === "warning"
  )).length;
  return `${completed}/3 阶段完成`;
}

function SaveReviewPanel({ saveStatus, saveResult, pipelineRunning, onSave }) {
  return (
    <div className="reviewSaveBar">
      <button type="button" className="primaryButton" onClick={onSave} disabled={saveStatus === "saving" || pipelineRunning}>
        {saveStatus === "saving" ? "保存中..." : "保存审核结果"}
      </button>
      <span className="commitNote">保存审核结果只写入 staging，不写核心数据库。</span>
      {(saveStatus === "saved" || saveStatus === "committed") && saveResult && (
        <div className="saveResultSummary">
          {saveStatus === "saved" && <span>✅ 已保存到 staging</span>}
          {saveStatus === "committed" && <span>✅ 已写入资料库</span>}
          {saveResult.document_id !== undefined && <span>文档 ID：{saveResult.document_id}</span>}
          {saveResult.status && <span>状态：{saveResult.status === "already_committed" ? "已提交（无重复写入）" : saveResult.status}</span>}
          {saveResult.message && <span>{saveResult.message}</span>}
          {saveResult.accepted_count !== undefined && <span>接受：{saveResult.accepted_count}</span>}
          {saveResult.edited_count !== undefined && <span>编辑：{saveResult.edited_count}</span>}
          {saveResult.rejected_count !== undefined && <span>拒绝：{saveResult.rejected_count}</span>}
          {(saveResult.inserted_count ?? saveResult.inserted) !== undefined && <span style={{ color: "#2e7d32" }}>新增：{saveResult.inserted_count ?? saveResult.inserted}</span>}
          {(saveResult.updated_count ?? saveResult.updated) !== undefined && <span style={{ color: "#1565c0" }}>更新：{saveResult.updated_count ?? saveResult.updated}</span>}
          {(saveResult.deprecated_count ?? saveResult.deprecated) !== undefined && <span style={{ color: "#e65100" }}>弃用：{saveResult.deprecated_count ?? saveResult.deprecated}</span>}
          {(saveResult.total_active ?? saveResult.total_active_count) !== undefined && <span>活跃对象：{saveResult.total_active ?? saveResult.total_active_count}</span>}
          {saveResult.mapping_status_counts && (
            <span>映射：mapped {saveResult.mapping_status_counts.mapped ?? 0} · partial {saveResult.mapping_status_counts.partial ?? 0} · failed {saveResult.mapping_status_counts.failed ?? 0} · not_mapped {saveResult.mapping_status_counts.not_mapped ?? 0}</span>
          )}
          {saveResult.mapping_status_counts && (saveResult.mapping_status_counts.partial ?? 0) > 0 && (
            <span className="warningPill">⚠ 部分对象证据为 fallback / partial 映射，需要人工复核。</span>
          )}
          {saveResult.mapping_status_counts && (saveResult.mapping_status_counts.failed ?? 0) > 0 && (
            <span className="errorPill" style={{ color: "#c62828", fontWeight: 600 }}>✗ {saveResult.mapping_status_counts.failed} 条对象证据映射失败，需要修复 evidence_refs。</span>
          )}
          {saveResult.status === "already_committed"
            ? <span className="safetyNote">db_write: {String(saveResult.core_db_write_performed ?? "—")}（already_committed 无需写入）</span>
            : <span className="safetyNote">db_write: {String(saveResult.core_db_write_performed ?? saveResult.db_write_performed ?? "—")}</span>}
        </div>
      )}
      {saveStatus === "error" && saveResult?.error && (
        <StateMessage title="保存/提交失败" body={saveResult.error} />
      )}
    </div>
  );
}

export default function ImportCommitStatus({
  visible,
  saveStatus,
  saveResult,
  remapPreview,
  remapLoading,
  commitLoading,
  commitPhase = {},
  confirmRemapFailed,
  pipelineRunning,
  onSave,
  onCommit,
  onPreviewRemap,
  onContinueRemap,
  onCancelRemap,
  onNavigate,
}) {
  if (!visible) return null;
  return (
    <>
      <SaveReviewPanel
        saveStatus={saveStatus}
        saveResult={saveResult}
        pipelineRunning={pipelineRunning}
        onSave={onSave}
      />

      <div className="commitPipeline">
        <div className="sectionHeader">
          <h3>提交入库流程</h3>
          <span>{pipelinePhaseSummary(commitPhase)}</span>
        </div>

        <div className="commitPipelineStages">
          <div className={commitPhaseRowClass(commitPhase.paper)}>
            <PhaseIcon entry={commitPhase.paper} />
            <span className="commitPhaseLabel">1. 论文正文入库</span>
            <span className="commitPhaseStatus">{phaseLabel(commitPhase.paper)}</span>
            {commitPhase.paper?.status === "ok" && commitPhase.paper?.result?.document_id && (
              <code className="commitPhaseDetail">doc_id={commitPhase.paper.result.document_id}</code>
            )}
          </div>

          <div className={commitPhaseRowClass(commitPhase.remap)}>
            <PhaseIcon entry={commitPhase.remap} />
            <span className="commitPhaseLabel">2. 证据映射预览</span>
            <span className="commitPhaseStatus">{phaseLabel(commitPhase.remap)}</span>
            {(commitPhase.remap?.status === "ok" || commitPhase.remap?.status === "warning") && commitPhase.remap?.result?.summary && (
              <code className="commitPhaseDetail">
                mapped {commitPhase.remap.result.summary.mapped ?? 0} · partial {commitPhase.remap.result.summary.partial ?? 0} · failed {commitPhase.remap.result.summary.failed ?? 0}
              </code>
            )}
            {(commitPhase.remap?.status === "ok" || commitPhase.remap?.status === "warning") && (commitPhase.remap?.result?.summary?.partial ?? 0) > 0 && (
              <span className="warningPill" style={{ marginLeft: 8 }}>⚠ partial 映射需人工复核</span>
            )}
          </div>

          <div className={commitPhaseRowClass(commitPhase.objects)}>
            <PhaseIcon entry={commitPhase.objects} />
            <span className="commitPhaseLabel">3. 对象候选入库</span>
            <span className="commitPhaseStatus">{phaseLabel(commitPhase.objects)}</span>
            {(commitPhase.objects?.status === "ok" || commitPhase.objects?.status === "already_committed") && saveResult && (
              <code className="commitPhaseDetail">
                {(saveResult.inserted_count ?? saveResult.inserted) !== undefined && `new ${saveResult.inserted_count ?? saveResult.inserted} `}
                {(saveResult.updated_count ?? saveResult.updated) !== undefined && `upd ${saveResult.updated_count ?? saveResult.updated} `}
                {(saveResult.deprecated_count ?? saveResult.deprecated) !== undefined && `dep ${saveResult.deprecated_count ?? saveResult.deprecated}`}
              </code>
            )}
          </div>
        </div>

        <RemapWarningPanel
          confirmRemapFailed={confirmRemapFailed}
          remapPhase={commitPhase.remap}
          remapPreview={remapPreview}
          onContinue={onContinueRemap}
          onCancel={onCancelRemap}
        />

        <div className="reviewSaveBar">
          <button type="button" className="primaryButton" onClick={onCommit} disabled={commitLoading || pipelineRunning}>
            {pipelineRunning ? "提交中..." : commitLoading ? "载入中..." : "载入资料库"}
          </button>
          <button type="button" onClick={onPreviewRemap} disabled={remapLoading || pipelineRunning} className="quietButton">
            {remapLoading ? "映射中..." : "预览证据映射"}
          </button>
          <span className="commitNote">完整流程：论文入库 → 证据映射预览 → 对象候选入库。映射预览失败时需手动确认。</span>
        </div>

        {(commitPhase.objects?.status === "ok" || commitPhase.objects?.status === "already_committed") && (
          <div className="commitNavLinks">
            <button type="button" onClick={() => onNavigate?.("readShelf")}>查看已读书架</button>
            <button type="button" onClick={() => onNavigate?.("search")}>搜索对象</button>
          </div>
        )}
      </div>
    </>
  );
}
