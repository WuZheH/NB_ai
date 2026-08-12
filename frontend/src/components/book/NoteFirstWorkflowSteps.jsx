import { buildBookChapterNoteFirstWorkflow } from "../../utils/noteFirstWorkflow.js";

export default function NoteFirstWorkflowSteps({ gate, compact = false }) {
  return (
    <ol className={`noteFirstWorkflow ${compact ? "compact" : ""}`} aria-label="dual-source workflow">
      {buildBookChapterNoteFirstWorkflow(gate).map((step) => (
        <li
          key={step.number}
          className={`noteFirstWorkflowStep status-${step.status}`}
          data-step-status={step.status}
          data-contract-label={step.contractLabel}
        >
          <span className="noteFirstStepNumber">{step.number}</span>
          <div>
            <strong>{step.label}</strong>
            <small>{step.statusLabel} · {step.reason}</small>
          </div>
        </li>
      ))}
    </ol>
  );
}
