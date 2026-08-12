import AdvancedDiagnostics from "./ImportDiagnosticsPanel.jsx";
import { importJobStatusLabel, stageLabel } from "../../features/importing/utils/importPreviewFormatters.js";

export default function ImportProgressStep({
  sourceMode,
  pdfPath,
  selectedZoteroSource,
  importReadiness,
  pdfBackendUnavailable,
  previewResult,
  classification,
  previewGate,
  duplicateCheck,
  textLayerPreview,
  markdownConversionState,
  activeStep,
  chapteredImportJob,
  fullDocumentCommitState,
  previewError,
  classifyError,
  zoteroError,
  fullDocumentImportRunning,
  doneDocumentId,
  buildPreviewQualitySummary,
  onBack,
  onComplete,
}) {
  return (
    <section className="linearImportCard" aria-label="导入进度">
      <div className="sectionHeader">
        <h3>导入进度</h3>
        <span>Step 4 / 5</span>
      </div>
      <FullDocumentLinearProgress commitState={fullDocumentCommitState} running={fullDocumentImportRunning} />
      <AdvancedDiagnostics
        className="linearDiagnostics"
        sourceMode={sourceMode}
        pdfPath={pdfPath}
        selectedZoteroSource={selectedZoteroSource}
        importReadiness={importReadiness}
        pdfBackendUnavailable={pdfBackendUnavailable}
        previewResult={previewResult}
        classification={classification}
        previewGate={previewGate}
        duplicateCheck={duplicateCheck}
        textLayerPreview={textLayerPreview}
        markdownConversionState={markdownConversionState}
        activeStep={activeStep}
        chapteredImportJob={chapteredImportJob}
        fullDocumentCommitState={fullDocumentCommitState}
        previewError={previewError}
        classifyError={classifyError}
        zoteroError={zoteroError}
        buildPreviewQualitySummary={buildPreviewQualitySummary}
      />
      <div className="linearImportActions">
        <button type="button" onClick={onBack}>返回</button>
        <button type="button" className="primaryButton" onClick={onComplete} disabled={!doneDocumentId}>
          查看完成结果
        </button>
      </div>
    </section>
  );
}

function FullDocumentLinearProgress({ commitState, running }) {
  return (
    <div className="linearProgressCard">
      <div className="sectionHeader">
        <h4>{importJobStatusLabel(commitState.status || (running ? "running" : "idle"))}</h4>
        <span>{stageLabel(commitState.stage || "commit-paper")}</span>
      </div>
      <div className="linearProgressBar"><span style={{ width: running ? "60%" : commitState.status === "success" ? "100%" : "0%" }} /></div>
      <p>{commitState.error || "整篇/全文入库状态会在这里显示。"}</p>
    </div>
  );
}
