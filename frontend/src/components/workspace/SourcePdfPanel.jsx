import { useEffect, useState } from "react";
import { getJson } from "../../api/client.js";
import DeveloperEvidenceDetails from "./DeveloperEvidenceDetails.jsx";
import PdfPagePreview from "./PdfPagePreview.jsx";
import SourceIngestionStatusCard from "./SourceIngestionStatusCard.jsx";
import SourceEvidenceCard from "./SourceEvidenceCard.jsx";
import { buildChapterSourceTarget } from "./sourceTargets.js";

export default function SourcePdfPanel({ workspaceState, sourceTarget, onClearSourceTarget, onOpenAdvancedWorkflow }) {
  const [locatorState, setLocatorState] = useState({ status: "idle", payload: null, error: "" });
  const effectiveSourceTarget = sourceTarget || buildChapterSourceTarget(workspaceState || {});

  useEffect(() => {
    const chunkId = effectiveSourceTarget?.matchedChunkId;
    if (!chunkId) {
      setLocatorState({ status: "idle", payload: null, error: "" });
      return;
    }
    let cancelled = false;
    setLocatorState({ status: "loading", payload: null, error: "" });
    getJson(`/api/v1/library/evidence/${chunkId}/pdf-location`)
      .then((payload) => {
        if (!cancelled) setLocatorState({ status: "ready", payload, error: "" });
      })
      .catch((error) => {
        if (!cancelled) {
          setLocatorState({
            status: "error",
            payload: null,
            error: error?.payload?.detail || "PDF 定位暂不可用。",
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [effectiveSourceTarget?.matchedChunkId]);

  if (!effectiveSourceTarget?.documentId || !effectiveSourceTarget?.page) {
    return (
      <DefaultSourceSummary
        workspaceState={workspaceState}
        onOpenAdvancedWorkflow={onOpenAdvancedWorkflow}
      />
    );
  }

  if (!sourceTarget) {
    return (
      <div className="sourcePdfPanel workspacePanelStack compactSourcePanel" aria-label="来源与 PDF 证据摘要">
        <SourceIngestionStatusCard
          state={workspaceState}
          onOpenAdvancedWorkflow={onOpenAdvancedWorkflow}
        />
        <details className="workspaceDisclosure sourcePdfPreviewDisclosure">
          <summary>打开 PDF 预览</summary>
          <PdfPagePreview sourceTarget={effectiveSourceTarget} locatorState={locatorState} />
        </details>
        <details className="workspaceDisclosure sourceEvidenceDisclosure">
          <summary>展开证据</summary>
          <SourceEvidenceCard sourceTarget={effectiveSourceTarget} />
        </details>
      </div>
    );
  }

  return (
    <div className="sourcePdfPanel workspacePanelStack" aria-label="PDF 预览与证据定位">
      <div className="workspacePanelHeader">
        <div>
          <p className="workspaceKicker">来源</p>
          <h3>PDF 预览</h3>
        </div>
        <button className="quietButton sourceBackButton" type="button" onClick={onClearSourceTarget}>
          来源摘要
        </button>
      </div>
      <PdfPagePreview sourceTarget={effectiveSourceTarget} locatorState={locatorState} />
      {locatorState.status === "error" && (
        <p className="sourceLocatorError">{locatorState.error}</p>
      )}
      <SourceEvidenceCard sourceTarget={effectiveSourceTarget} />
      <DeveloperEvidenceDetails sourceTarget={effectiveSourceTarget} locatorState={locatorState} />
    </div>
  );
}

function DefaultSourceSummary({ workspaceState, onOpenAdvancedWorkflow }) {
  return (
    <SourceIngestionStatusCard
      state={workspaceState}
      onOpenAdvancedWorkflow={onOpenAdvancedWorkflow}
    />
  );
}

export function SourceSelectionHint() {
  return (
      <div className="sourceSelectionHint">
        <strong>选择一条笔记或片段以查看 PDF 来源</strong>
        <span>PDF 预览 · 证据定位 · 页码 / chunk / bbox fallback · 选中文本 · 笔记 · 原文片段</span>
      </div>
  );
}
