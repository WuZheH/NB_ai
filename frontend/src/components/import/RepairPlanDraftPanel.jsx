export default function RepairPlanDraftPanel({ result, loading, error }) {
  if (loading) {
    return (
      <section className="repairPlanDraftPanel repairPreviewLoading" aria-label="Repair plan draft loading">
        <strong>正在生成只读修复计划草案</strong>
        <span>不会运行 OCR、写入正式库、执行 batch apply 或 promote。</span>
      </section>
    );
  }
  if (error) {
    return (
      <section className="repairPlanDraftPanel repairPreviewLoading" aria-label="Repair plan draft error">
        <strong>修复计划草案暂不可用</strong>
        <span>{error}</span>
      </section>
    );
  }
  if (!result) return null;

  const quality = result.sample_quality_summary || {};
  const batch = result.next_batch_strategy || {};
  const risk = result.risk_report || {};

  return (
    <section className="repairPlanDraftPanel" aria-label="OCR repair plan draft">
      <header className="repairPlanHeader">
        <div>
          <p className="previewGateEyebrow">REPAIR PLAN DRAFT · READ ONLY</p>
          <h3>Repair plan draft</h3>
          <strong className={`repairDecision ${result.recommended_decision}`}>{result.recommended_decision}</strong>
        </div>
        <div className="repairPreviewBadges">
          <strong>No apply / no database writes</strong>
          <span>OCR execution: off</span>
          <span>Promote: off</span>
        </div>
      </header>

      <div className="repairPlanGrid">
        <PlanCard title="Decision reasons">
          <PlanList values={result.decision_reasons} />
        </PlanCard>
        <PlanCard title="Sample quality summary">
          <div className="repairPlanMetrics">
            <span>Pages sampled <strong>{quality.pages_sampled || 0}</strong></span>
            <span>Candidates <strong>{quality.candidate_count || 0}</strong></span>
            <span>Clean <strong>{quality.clean_count || 0}</strong></span>
            <span>Safe correct <strong>{quality.safe_auto_correct_count || 0}</strong></span>
            <span>Needs review <strong>{quality.needs_review_count || 0}</strong></span>
            <span>Blocked <strong>{quality.blocked_count || 0}</strong></span>
          </div>
        </PlanCard>
        <PlanCard title="Next batch strategy">
          <p>Max pages per batch: <strong>{batch.max_pages_per_batch || 0}</strong></p>
          <p>Proposed pages: <strong>{(batch.recommended_first_batch_pages || []).join(", ") || "none"}</strong></p>
          <p>{batch.why}</p>
        </PlanCard>
        <PlanCard title="Risk report">
          <p>Formula risk: <strong>{risk.formula_risk}</strong></p>
          <p>Low confidence risk: <strong>{risk.low_confidence_risk}</strong></p>
          <p>Manual review required: <strong>{risk.manual_review_required ? "yes" : "no"}</strong></p>
        </PlanCard>
        <PlanCard title="Estimated time">
          <p>{result.estimated_runtime?.next_single_page_batch}</p>
          <p>{result.estimated_runtime?.full_scope}</p>
        </PlanCard>
        <PlanCard title="Required confirmations before apply">
          <PlanList values={result.required_confirmations_before_apply} />
        </PlanCard>
        <PlanCard title="Forbidden actions">
          <PlanList values={result.forbidden_actions} />
        </PlanCard>
      </div>
      <footer className="repairPlanFooter">
        下一阶段实现：选择单页 batch。本草案不会立即 OCR 全书、写库或 promote。
      </footer>
    </section>
  );
}

function PlanCard({ title, children }) {
  return (
    <section className="repairPlanCard">
      <h4>{title}</h4>
      {children}
    </section>
  );
}

function PlanList({ values = [] }) {
  return (
    <ul className="repairPlanList">
      {values.map(value => <li key={value}>{value}</li>)}
    </ul>
  );
}
