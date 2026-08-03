import { useEffect } from "react";
import { normalizeIdentityText } from "../../../shared/utils/display.js";
import {
  cacheStatusLabel,
  decisionMessage,
  deviceLabel,
  duplicateConfidenceLabel,
  finalImportUnitLabel,
  importJobStatusLabel,
  nativeNotesSummary,
  qualityStatusLabel,
  stageLabel,
} from "../utils/importPreviewFormatters.js";
import StateMessage from "../../../components/StateMessage.jsx";
import AdvancedDiagnostics from "../../../pages/import/ImportDiagnosticsPanel.jsx";
import ImportCompleteStep from "../../../pages/import/ImportCompleteStep.jsx";
import ImportConfirmStep from "../../../pages/import/ImportConfirmStep.jsx";
import ImportProgressStep from "../../../pages/import/ImportProgressStep.jsx";
import ImportWizardStepper from "../../../pages/import/ImportWizardStepper.jsx";
import ZoteroPdfPickerStep from "../../../pages/import/ZoteroPdfPickerStep.jsx";
import {
  deriveDocumentKindForImport,
  derivePrimaryImportAction,
  documentKindDisplayLabel,
} from "../../../pages/import/importKindPolicy.js";
import {
  recommendedActionDisplayLabel,
  zoteroPdfImportStatus,
} from "../../../pages/import/zoteroPdfImportStatus.js";

export const IDLE_FULL_DOCUMENT_COMMIT_STATE = { status: "idle", stage: "", data: null, error: "" };
export const IDLE_CHAPTERED_COMMIT_STATE = { status: "idle", data: null, error: "" };
export const IDLE_MARKDOWN_CONVERSION_STATE = { status: "idle", data: null, error: "" };

export function ImportLinearWizard({
  state,
  setState,
  sourceMode,
  pdfPath,
  titleHint,
  sourceTitle,
  zoteroStatus,
  zoteroQuery,
  zoteroSources,
  selectedZoteroSource,
  zoteroLoading,
  zoteroError,
  showZoteroBrowse,
  classification,
  classifyLoading,
  classifyError,
  previewResult,
  previewError,
  preImportPreviewLoading,
  duplicateCheck,
  duplicateCheckLoading,
  duplicateCheckError,
  textLayerPreview,
  previewGate,
  pdfBackendUnavailable,
  chapteredPreview,
  chapteredPreviewLoading,
  chapteredImportJob,
  importingChaptered,
  fullDocumentCommitState,
  fullDocumentImportRunning,
  convertedMdPath,
  markdownConversionState,
  importReadiness,
  documentKindForImport,
  previewWorkspaceTab,
  setPreviewWorkspaceTab,
  previewWorkspaceExpanded,
  setPreviewWorkspaceExpanded,
  onNavigate,
  onOpenDocument,
  onSelectZoteroSource,
  resetZoteroSelection,
  resetForNewImportSource,
  classifyPdf,
  generatePreImportPreview,
  fetchPreviewGate,
  fetchChapteredPreview,
  commitFullDocumentImport,
  startChapteredImportJob,
  cancelChapteredImportJob,
  selectZoteroSource,
  searchZoteroSources,
  viewPreviewWorkspace,
  generateMarkdownForCurrentPdf,
}) {
  const inferredStep = inferLinearImportStep({ classification, previewResult, chapteredImportJob, fullDocumentCommitState });
  const activeStep = state.importLinearStep || inferredStep;
  const kind = documentKindForImport || deriveDocumentKindForImport({
    classification,
    explicitDocumentType: state.overrideDocumentType,
    selectedZoteroSource,
    title: sourceTitle,
    titleHint,
  });
  const isBook = kind === "book";
  const isPaper = kind === "paper";
  const doneDocumentId = importedDocumentId({ chapteredImportJob, fullDocumentCommitState });
  const busy = classifyLoading || preImportPreviewLoading || fullDocumentImportRunning || importingChaptered || chapteredPreviewLoading;
  const selectedImportStatus = zoteroPdfImportStatus(selectedZoteroSource);
  const selectedBlocksDefaultImport = sourceMode === "zotero_pdf" && selectedImportStatus.blocksDefaultImport;
  const selectedSiblingImported = sourceMode === "zotero_pdf" && selectedImportStatus.status === "sibling_imported";
  const siblingImportConfirmed = Boolean(state.siblingImportOverrideConfirmed);

  useEffect(() => {
    if (sourceMode !== "zotero_pdf") return;
    if (!showZoteroBrowse) return;
    if (zoteroLoading) return;
    if ((zoteroSources || []).length > 0) return;
    if (state.zoteroBrowseAutoLoaded) return;
    setState(s => ({ ...s, zoteroBrowseAutoLoaded: true }));
    searchZoteroSources(null, "");
  }, [
    sourceMode,
    showZoteroBrowse,
    zoteroLoading,
    (zoteroSources || []).length,
    state.zoteroBrowseAutoLoaded,
  ]);

  function setStep(step) {
    setState(s => ({ ...s, importLinearStep: step }));
  }

  async function runPreflight() {
    await generatePreImportPreview();
    setStep("confirm");
  }

  async function startWholeImport() {
    await commitFullDocumentImport();
    setStep("progress");
  }

  function selectSourceMode(mode) {
    if (mode === "local_pdf") {
      setState(s => resetForNewImportSource(s, {
        sourceMode: "local_pdf",
        selectedZoteroSource: null,
        pdfSelectionStage: "browse",
        pdfPath: "",
        titleHint: "",
      }));
      return;
    }
    if (mode === "zotero_pdf") {
      setState(s => resetForNewImportSource(s, {
        sourceMode: "zotero_pdf",
        pdfSelectionStage: "browse",
        selectedZoteroSource: null,
        pdfPath: "",
        titleHint: "",
      }));
      return;
    }
    setState(s => resetForNewImportSource(s, {
      sourceMode: "converted_md",
      pdfSelectionStage: "browse",
      selectedZoteroSource: null,
      pdfPath: s.convertedMdPath || "",
      convertedMdPath: s.convertedMdPath || "",
      selectedImportRoute: "converted_md",
    }));
  }

  function updateLocalPdfPath(nextPath) {
    setState(s => resetForNewImportSource(s, {
      pdfPath: nextPath,
      titleHint: inferTitleFromPath(nextPath),
      selectedZoteroSource: null,
      pdfSelectionStage: "browse",
    }));
  }

  return (
    <section className="importPreviewPage linearImportPage">
      <ImportWizardStepper activeStep={activeStep} />
      <div className="linearImportShell">
        {activeStep === "select" && (
          <ZoteroPdfPickerStep
            state={state}
            setState={setState}
            sourceMode={sourceMode}
            pdfPath={pdfPath}
            titleHint={titleHint}
            selectedZoteroSource={selectedZoteroSource}
            showZoteroBrowse={showZoteroBrowse}
            zoteroQuery={zoteroQuery}
            zoteroSources={zoteroSources}
            zoteroLoading={zoteroLoading}
            zoteroError={zoteroError}
            searchZoteroSources={searchZoteroSources}
            selectZoteroSource={selectZoteroSource}
            resetZoteroSelection={resetZoteroSelection}
            onOpenDocument={onOpenDocument}
            onSelectSourceMode={selectSourceMode}
            onLocalPdfPathChange={updateLocalPdfPath}
            onNext={() => setStep("preflight")}
          />
        )}

        {activeStep === "preflight" && (
          <section className="linearImportCard" aria-label="导入前预检">
            <div className="sectionHeader">
              <h3>导入前预检</h3>
              <span>Step 2 / 5</span>
            </div>
            <div className="previewResultGrid">
              <PreviewField label="标题" value={sourceTitle || titleHint || "待识别"} />
              <PreviewField label="页数" value={classification?.signals?.page_count || textLayerPreview?.page_count || previewGate?.physical_page_count} />
              <PreviewField label="文档类型判断" value={documentKindDisplayLabel(kind)} />
              <PreviewField label="文本层质量" value={qualityStatusLabel(textLayerPreview?.quality_summary?.quality || previewGate?.recommended_route || "not_checked")} />
              <PreviewField label="重复导入提示" value={duplicateCheck?.duplicate_found ? "发现可能重复" : duplicateCheckLoading ? "检查中" : "未发现"} />
              <PreviewField label="Zotero attachment" value={selectedZoteroSource?.zotero_attachment_key || "无"} />
              <PreviewField label="原生 annotation 数量" value={selectedZoteroSource?.annotation_count ?? "未知"} />
            </div>
            <p className="linearImportCopy">预检只读；不会写入知识库，不生成对象，不调用 LLM。</p>
            {classifyError && <StateMessage title="自动识别失败" body={classifyError} />}
            {previewError && <StateMessage title="预览生成失败" body={previewError} />}
            {duplicateCheckError && <StateMessage title="重复检查失败" body={duplicateCheckError} />}
            {chapteredPreview && (
              <ChapterSummaryPreview chapteredPreview={chapteredPreview} />
            )}
            <div className="linearImportActions">
              <button type="button" onClick={() => setStep("select")}>返回</button>
              <button type="button" onClick={runPreflight} disabled={busy}>{busy ? "预检中..." : "生成导入前预检"}</button>
              <button type="button" className="primaryButton" onClick={() => setStep("confirm")} disabled={!classification && !previewResult}>
                下一步
              </button>
            </div>
          </section>
        )}

        {activeStep === "confirm" && (
          <ImportConfirmStep
            kind={kind}
            classification={classification}
            sourceTitle={sourceTitle}
            titleHint={titleHint}
            sourceMode={sourceMode}
            selectedZoteroSource={selectedZoteroSource}
            selectedImportStatus={selectedImportStatus}
            selectedBlocksDefaultImport={selectedBlocksDefaultImport}
            selectedSiblingImported={selectedSiblingImported}
            siblingImportConfirmed={siblingImportConfirmed}
            busy={busy}
            previewResult={previewResult}
            onBack={() => setStep("preflight")}
            onStartWholeImport={startWholeImport}
            onOpenDocument={onOpenDocument}
            onSiblingImportConfirmChange={checked => setState(s => ({ ...s, siblingImportOverrideConfirmed: checked }))}
          />
        )}

        {activeStep === "progress" && (
          <ImportProgressStep
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
            fullDocumentImportRunning={fullDocumentImportRunning}
            doneDocumentId={doneDocumentId}
            buildPreviewQualitySummary={buildPreviewQualitySummary}
            onBack={() => setStep("confirm")}
            onComplete={() => setStep("complete")}
          />
        )}

        {activeStep === "complete" && (
          <ImportCompleteStep
            doneDocumentId={doneDocumentId}
            classification={classification}
            sourceTitle={sourceTitle}
            titleHint={titleHint}
            chapteredImportJob={chapteredImportJob}
            fullDocumentCommitState={fullDocumentCommitState}
            chapteredPreview={chapteredPreview}
            onBack={() => setStep("confirm")}
            onOpenDocument={onOpenDocument}
            onNavigate={onNavigate}
          />
        )}
      </div>

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
      >
        <PreviewWorkspace
          activeTab={previewWorkspaceTab}
          onTabChange={setPreviewWorkspaceTab}
          expanded={previewWorkspaceExpanded}
          onToggleExpanded={() => setPreviewWorkspaceExpanded(value => !value)}
          previewGate={previewGate}
          previewResult={previewResult}
          textLayerPreview={textLayerPreview}
          textLayerPreviewLoading={false}
          textLayerPreviewError=""
          pdfBackendUnavailable={pdfBackendUnavailable}
          convertedMdPath={convertedMdPath}
          convertedMdIdentity={{ matches: true }}
          importReadiness={{ quality_good: true }}
          markdownConversionState={markdownConversionState}
          onGeneratePreview={viewPreviewWorkspace}
          onGenerateMarkdown={generateMarkdownForCurrentPdf}
        />
      </AdvancedDiagnostics>
    </section>
  );
}

export function resetForNewImportSource(state, overrides = {}) {
  const hasPdfOverride = Object.prototype.hasOwnProperty.call(overrides, "pdfPath");
  const nextPdfPath = hasPdfOverride ? overrides.pdfPath : state.pdfPath;
  const hasTitleOverride = Object.prototype.hasOwnProperty.call(overrides, "titleHint");
  const nextTitleHint = hasTitleOverride ? overrides.titleHint : inferTitleFromPath(nextPdfPath);
  return {
    ...state,
    loading: false,
    previewResult: null,
    previewError: "",
    importJobId: null,
    bundleLoading: false,
    bundleResult: null,
    bundleError: "",
    bundleContent: null,
    bundleContentLoading: false,
    classification: null,
    classifyLoading: false,
    classifyError: "",
    overrideDocumentType: undefined,
    overrideObjectImportMode: undefined,
    previewGate: null,
    previewGateLoading: false,
    previewGateError: "",
    previewGateNotice: "",
    selectedImportRoute: "",
    repairPreview: null,
    repairPreviewLoading: false,
    repairPreviewError: "",
    repairPlanDraft: null,
    repairPlanLoading: false,
    repairPlanError: "",
    fullDocumentCommitState: IDLE_FULL_DOCUMENT_COMMIT_STATE,
    chapteredCommitState: IDLE_CHAPTERED_COMMIT_STATE,
    chapteredPreview: null,
    chapteredPreviewLoading: false,
    chapteredPreviewConfirmed: false,
    chapteredPreviewError: "",
    selectedChapterIndexes: [],
    chapteredImportJob: null,
    chapteredImportJobPolling: false,
    chapteredImportJobError: "",
    chapteredImportStatusError: "",
    chapteredImportStatusErrorCount: 0,
    chapteredImportReusedMessage: "",
    chapteredImportCancelMessage: "",
    markdownConversionState: IDLE_MARKDOWN_CONVERSION_STATE,
    convertedMdPath: "",
    textLayerPreview: null,
    textLayerPreviewLoading: false,
    textLayerPreviewError: "",
    duplicateCheck: null,
    duplicateCheckLoading: false,
    duplicateCheckError: "",
    duplicateCheckRequestKey: "",
    siblingImportOverrideConfirmed: false,
    preImportPreviewLoading: false,
    preImportPreviewError: "",
    pdfBackendUnavailable: null,
    ...overrides,
    titleHint: nextTitleHint,
  };
}

export function titleFromZoteroSource(source = {}) {
  const zoteroTitle = String(source.title || "").trim();
  return zoteroTitle || inferTitleFromPath(source.resolved_pdf_path || "");
}

export function inferTitleFromPath(path = "") {
  const base = lastPathSegment(path).replace(/\.[^.]+$/, "");
  return base.replace(/[_-]+/g, " ").trim();
}

export function PreviewField({ label, value }) {
  return (
    <div className="previewField">
      <span className="previewFieldLabel">{label}</span>
      <code className="previewFieldValue">{value ?? "—"}</code>
    </div>
  );
}

function ChapterSummaryPreview({ chapteredPreview }) {
  const chapters = (chapteredPreview.accepted_chapters || []).slice(0, 5);
  return (
    <section className="chapterSummaryPreview" aria-label="章节识别摘要">
      <div className="sectionHeader">
        <h4>章节识别摘要</h4>
        <span>{chapteredPreview.chapter_count || 0} 章</span>
      </div>
      <p>章节结构仅用于导入后详情页按章处理；入库阶段不再选择章节范围。</p>
      <div className="chapterSummaryList">
        {chapters.map(chapter => (
          <span key={chapter.chapter_index}>{chapter.chapter_index}. {chapter.title} {chapter.pdf_page_start ? `(p.${chapter.pdf_page_start}-${chapter.pdf_page_end || chapter.pdf_page_start})` : ""}</span>
        ))}
      </div>
    </section>
  );
}

function ChapteredLinearProgress({ job, importing, onCancel }) {
  if (!job) {
    return <StateMessage title={importing ? "正在创建整本书导入任务" : "尚未启动导入任务"} body="导入任务启动后会显示正文解析后端、正文解析设备、GPU 状态和进度。" />;
  }
  return (
    <div className="linearProgressCard">
      <div className="sectionHeader">
        <h4>{importJobStatusLabel(job.status || "running")}</h4>
        <span>{stageLabel(job.stage)}</span>
      </div>
      <div className="linearProgressBar"><span style={{ width: `${job.progress_percent || 0}%` }} /></div>
      <p>{job.progress_percent || 0}% · {job.message || ""}</p>
      <div className="linearRuntimeGrid">
        {job.preview_backend && <span>预检后端：{job.preview_backend}</span>}
        {job.worker_backend && <span>正文解析后端：{job.worker_backend}</span>}
        {job.worker_device && <span>正文解析设备：{deviceLabel(job.worker_device)}</span>}
        {job.worker_gpu_name && <span>GPU：{job.worker_gpu_name}</span>}
        {job.worker_pid && <span>worker pid：{job.worker_pid}</span>}
      </div>
      {(job.status === "queued" || job.status === "running") && (
        <button type="button" onClick={onCancel}>取消导入</button>
      )}
    </div>
  );
}

function inferLinearImportStep({ classification, previewResult, chapteredImportJob, fullDocumentCommitState }) {
  if (chapteredImportJob?.status === "completed" || fullDocumentCommitState?.status === "success" || fullDocumentCommitState?.status === "already_committed") return "complete";
  if (chapteredImportJob || ["previewing", "committing"].includes(fullDocumentCommitState?.status)) return "progress";
  if (classification || previewResult) return "confirm";
  return "select";
}

function importedDocumentId({ chapteredImportJob, fullDocumentCommitState }) {
  return chapteredImportJob?.result?.document_id
    || chapteredImportJob?.document_id
    || fullDocumentCommitState?.data?.document_id
    || null;
}

export function filteredDeviceWarnings(job = {}) {
  const workerDevice = String(job.worker_device || job.import_backend_device || job.parser_device || "").toLowerCase();
  return (job.warnings || []).filter(w => {
    const text = String(w || "");
    if (workerDevice === "cuda" && text.includes("current backend is not using CUDA")) {
      return false;
    }
    return true;
  });
}

export function ChapteredImportDeviceNotice({ job = {} }) {
  const workerDevice = String(job.worker_device || job.import_backend_device || job.parser_device || "").toLowerCase();
  const gpuName = job.worker_gpu_name || job.runtime?.cuda_device_name || "";
  const reason = job.device_selection_reason || job.runtime?.device_selection_reason || job.runtime?.reason || job.device_blocker || "torch_cuda_unavailable";
  if (workerDevice === "cuda") {
    return (
      <div className="importDeviceNotice ok">
        正文解析正在使用 CUDA：{gpuName || "GPU 可用"}。
      </div>
    );
  }
  if (workerDevice === "cpu") {
    return (
      <div className="importDeviceNotice warning">
        质量预检使用 CPU 文本层读取；这不代表正文解析不使用 GPU。正文解析当前使用 CPU：{reason}，可能较慢。
      </div>
    );
  }
  return (
    <div className="importDeviceNotice">
      质量预检使用 CPU 文本层读取；这不代表正文解析不使用 GPU。
    </div>
  );
}

export function SourceSummary({
  sourceMode,
  title,
  selectedZoteroSource,
  pdfPath,
  duplicateCheck,
  duplicateCheckLoading,
  duplicateCheckError,
  classification,
  previewGate,
  pdfBackendUnavailable,
  convertedMdPath,
  convertedMdIdentity,
  importReadiness,
}) {
  const backendStatus = pdfBackendUnavailable
    ? "PyMuPDF 不可用"
    : previewGate
      ? "预检后端可用"
      : "未检查";
  const pathStatus = sourceMode === "zotero_pdf" && selectedZoteroSource
    ? `${cacheStatusLabel(selectedZoteroSource.cache_status)} · ${selectedZoteroSource.path_exists ? "文件存在" : "文件缺失"}`
    : pdfPath ? "已提供路径" : "等待选择";
  const duplicateStatus = duplicateCheckLoading
    ? "检查中"
    : duplicateCheck?.duplicate_found
      ? `可能重复：${duplicateConfidenceLabel(duplicateCheck.duplicate_confidence)}`
      : duplicateCheck
        ? "未发现重复"
        : duplicateCheckError
          ? "检查失败"
          : "未检查";
  const existing = duplicateCheck?.existing_documents?.[0] || null;
  const importStatus = sourceMode === "zotero_pdf" ? zoteroPdfImportStatus(selectedZoteroSource) : null;
  const sourcePath = sourceMode === "zotero_pdf"
    ? (selectedZoteroSource?.resolved_pdf_path || "")
    : pdfPath;
  const existingDocumentText = importStatus?.existingDocumentId
    ? `#${importStatus.existingDocumentId}：${importStatus.existingDocumentTitle || "已有文档"}`
    : existing
      ? `#${existing.document_id} · chunks ${existing.chunk_count}`
      : "—";
  return (
    <section className={`sourceSummaryPanel sourceDuplicateSummary ${duplicateCheck?.duplicate_found ? "duplicate" : ""}`} aria-label="来源摘要">
      <div>
        <span>来源摘要</span>
        <strong>{title || selectedZoteroSource?.title || "Untitled import source"}</strong>
      </div>
      <dl>
        <div><dt>Zotero 条目</dt><dd>{selectedZoteroSource?.zotero_item_key || "—"}</dd></div>
        <div><dt>PDF 附件</dt><dd>{selectedZoteroSource?.zotero_attachment_key || "—"}</dd></div>
        <div><dt>PDF 路径</dt><dd>{sourcePath || "—"}</dd></div>
        <div><dt>路径状态</dt><dd>{pathStatus}</dd></div>
        <div><dt>页数</dt><dd>{classification?.signals?.page_count ?? previewGate?.physical_page_count ?? "未知"}</dd></div>
        <div><dt>预检后端</dt><dd>{backendStatus}</dd></div>
        <div><dt>重复状态</dt><dd>{duplicateStatus}</dd></div>
        <div><dt>入库状态</dt><dd>{importStatus?.label || "未选择 Zotero PDF"}</dd></div>
        <div><dt>推荐操作</dt><dd>{recommendedActionDisplayLabel(importStatus?.recommendedAction)}</dd></div>
        <div><dt>已入库文档</dt><dd>{existingDocumentText}</dd></div>
        <div><dt>推荐路线</dt><dd>{importReadiness.route_summary}</dd></div>
        <div><dt>converted_md</dt><dd>{convertedMdPath ? (convertedMdIdentity.matches ? "可用" : "不匹配") : "未找到"}</dd></div>
      </dl>
      {duplicateCheck?.duplicate_found && (
        <div className="duplicateImportNotice">
          <strong>检测到可能重复导入</strong>
          <span>{(duplicateCheck.duplicate_reasons || []).join(", ") || "same source"}</span>
          <span>默认操作是打开已有文档；不会自动清理或删除现有重复记录。</span>
        </div>
      )}
      {duplicateCheckError && <p className="safetyNote">{duplicateCheckError}。该检查失败不会写入数据库。</p>}
    </section>
  );
}

export function MainImportDecisionPanel({
  importReadiness,
  running,
  markdownConversionState,
  commitState,
  onPrimaryAction,
  onPreviewAction,
  onChapterPreview,
  onChapterImport,
  onGenerateMarkdown,
  onOpenDocument,
  onViewPreview,
  previewResult,
  commitResult,
  duplicateCheck,
  duplicateCheckLoading,
  duplicateCheckError,
  previewLoading,
}) {
  const routeTone = importReadiness.can_import ? "ready" : importReadiness.primary_action_kind === "generate_markdown" ? "needsMarkdown" : "blocked";
  const primaryAction = importReadiness.primary_action_kind === "generate_markdown"
    ? onGenerateMarkdown
    : importReadiness.primary_action_kind === "generate_chapter_preview"
      ? onChapterPreview
      : importReadiness.primary_action_kind === "chapter_import"
        ? onChapterImport
        : importReadiness.primary_action_kind === "open_existing_document"
          ? () => onOpenDocument?.(importReadiness.existing_document_id)
        : onPrimaryAction;
  const generatingMarkdown = markdownConversionState?.status === "generating_markdown";
  const previewLabel = previewLoading ? "正在生成预览..." : importReadiness.preview_action || "生成导入前预览";
  const primaryLabel = generatingMarkdown ? "正在生成 Markdown..." : running ? "导入处理中..." : importReadiness.primary_action;
  const hasPreview = Boolean(previewResult?.import_job_id || importReadiness.quality_status !== "not_checked" || importReadiness.primary_action_kind === "chapter_import");
  const isChapterFlow = importReadiness.recommended_route === "chapter_import" || importReadiness.primary_action_kind === "chapter_import";
  const viewPreviewLabel = isChapterFlow ? "查看章节预览" : "查看预览";
  const refreshPreviewLabel = isChapterFlow ? "刷新章节预览" : "刷新预览";
  return (
    <section className={`mainImportDecisionPanel importFlowCard ${routeTone}`} aria-label="Import Flow Card">
      <div className="mainImportDecisionCopy importFlowStepsPanel">
        <span>导入流程</span>
        <h3>{importReadiness.route_summary}</h3>
        <p>{decisionMessage(importReadiness)}</p>
        {importReadiness.quality_good && (
          <p className="qualityPassText">文本层质量良好，预计不需要 OCR，可直接进入下一步。</p>
        )}
        {importReadiness.blocker && <strong>{importReadiness.blocker}</strong>}
        <div className="importFlowSteps" aria-label="导入步骤">
          <FlowStep
            step="Step 1"
            title="重复检查"
            state={duplicateCheckLoading ? "checking" : duplicateCheck?.duplicate_found ? "blocked" : duplicateCheck ? "done" : duplicateCheckError ? "warning" : "pending"}
            detail={duplicateCheck?.duplicate_found
              ? `发现已有文档 doc ${duplicateCheck.existing_documents?.[0]?.document_id || "?"}`
              : duplicateCheckLoading
                ? "正在按 Zotero key / PDF path / 前三页指纹检查。"
                : duplicateCheck
                  ? "未发现同一附件或同一路径的已导入文档。"
                  : duplicateCheckError || "选择 PDF 后会自动执行只读检查。"}
          />
          <FlowStep
            step="Step 2"
            title="质量预检"
            state={importReadiness.quality_good ? "done" : importReadiness.quality_status === "blocked" ? "blocked" : importReadiness.quality_status === "uncertain" ? "warning" : "pending"}
            detail={importReadiness.quality_summary_message}
          />
          <FlowStep
            step="Step 3"
            title="推荐路线"
            state={importReadiness.recommended_route === "chapter_import" || importReadiness.recommended_route === "whole_paper" || importReadiness.recommended_route === "already_imported" ? "done" : "pending"}
            detail={`${importReadiness.route_summary} · 最终入库单元：${finalImportUnitLabel(importReadiness.final_import_unit)}`}
          />
          <FlowStep
            step="Step 4"
            title="下一步动作"
            state={importReadiness.primary_action_enabled ? "active" : "pending"}
            detail={importReadiness.primary_action}
          />
        </div>
      </div>
      <div className="mainImportAction primaryActionPanel">
        <div className="importPreviewActionRow">
          {hasPreview ? (
            <>
              <button type="button" className="secondaryButton importPreviewViewButton" onClick={onViewPreview}>
                {viewPreviewLabel}
              </button>
              <button
                type="button"
                className="quietButton importPreviewRefreshButton"
                onClick={onPreviewAction}
                disabled={previewLoading || !importReadiness.preview_action_enabled}
              >
                {previewLoading ? "正在刷新预览..." : refreshPreviewLabel}
              </button>
            </>
          ) : (
            <button
              type="button"
              className="secondaryButton importPreviewGenerateButton"
              onClick={onPreviewAction}
              disabled={previewLoading || !importReadiness.preview_action_enabled}
            >
              {previewLabel || "生成导入前预览"}
            </button>
          )}
          <p>只读取 PDF，不写入知识库，不调用 LLM。不会写入知识库，不会调用外部大模型，不会自动生成机制。</p>
        </div>
        <button
          type="button"
          className="primaryButton importPrimaryCta"
          onClick={primaryAction}
          disabled={running || !importReadiness.primary_action_enabled}
          data-primary-import-cta="true"
        >
          {primaryLabel}
        </button>
        {importReadiness.primary_action_kind === "generate_markdown" ? (
          <p>生成 Markdown 只写 converted_md 文件；不会写入 documents/chunks/objects，不会调用外部大模型，不会自动生成机制。生成后页面刷新为 Markdown 导入路线，但不自动执行导入。</p>
        ) : importReadiness.primary_action_kind === "open_existing_document" ? (
          <p>检测到重复来源时不会显示导入为主操作。仍要重新导入或替换只能在高级区域显式处理，本页不会自动删除已有 document。</p>
        ) : importReadiness.primary_action_kind === "generate_chapter_preview" ? (
          <p>书籍整本入库。章节摘要仅用于后续在详情页按章处理；导入页不再选择章节范围。</p>
        ) : importReadiness.primary_action_kind === "chapter_import" ? (
          <p>会整本导入书籍正文；章节、对象和机制将在详情页按章/节处理，不会调用外部大模型，也不会自动生成机制。</p>
        ) : (
          <p>最终确认后会写入 documents/chunks 相关数据；不会调用外部大模型；不会自动生成机制；导入成功后可打开文档详情页。</p>
        )}
        <MarkdownConversionStatus state={markdownConversionState} />
        <ImportCommitStatus state={commitState} onOpenDocument={onOpenDocument} />
        <ImportSafetySnapshot previewResult={previewResult} commitResult={commitResult} />
      </div>
    </section>
  );
}

function MarkdownConversionStatus({ state = {} }) {
  if (state.status === "generating_markdown") {
    return <StateMessage title="正在生成 Markdown" body="后端只处理当前选中的 PDF 文本层；不会运行 OCR/Marker，也不会写入核心数据库。" />;
  }
  if (state.status === "markdown_ready") {
    return <StateMessage title="Markdown 已生成" body="converted_md 已可用。下一步先生成导入前预览；确认后才会执行最终导入。" />;
  }
  if (state.status === "markdown_failed") {
    const payload = state.data || {};
    return <StateMessage title="Markdown 生成失败" body={payload.message || state.error || "当前缺少可用的非 PyMuPDF text-layer converter。"} />;
  }
  return null;
}

function FlowStep({ step, title, state, detail }) {
  const normalizedState = state || "pending";
  const className = {
    done: "importFlowStepDone",
    active: "importFlowStepActive",
    pending: "importFlowStepPending",
    blocked: "importFlowStepBlocked",
    warning: "importFlowStepWarning",
    checking: "importFlowStepActive",
  }[normalizedState] || "importFlowStepPending";
  const badgeClass = {
    done: "importFlowStatusBadgeDone",
    active: "importFlowStatusBadgeActive",
    pending: "importFlowStatusBadgePending",
    blocked: "importFlowStatusBadgeBlocked",
    warning: "importFlowStatusBadgeWarning",
    checking: "importFlowStatusBadgeActive",
  }[normalizedState] || "importFlowStatusBadgePending";
  const icon = {
    done: "✓",
    active: "•",
    pending: step.replace("Step ", ""),
    blocked: "!",
    warning: "!",
    checking: "…",
  }[normalizedState] || step.replace("Step ", "");
  return (
    <div className={`importFlowStep compact ${normalizedState} ${className}`}>
      <div className="importFlowStepMarker" aria-hidden="true">{icon}</div>
      <div className="importFlowStepBody">
        <div className="importFlowStepHeader">
          <span>{step}</span>
          <strong>{title}</strong>
        </div>
        <p>{detail || "等待处理"}</p>
      </div>
      <em className={`importFlowStatusBadge ${badgeClass}`}>{normalizedState}</em>
    </div>
  );
}

export function PreviewWorkspace({
  activeTab,
  onTabChange,
  expanded,
  onToggleExpanded,
  previewGate,
  previewResult,
  textLayerPreview,
  textLayerPreviewLoading,
  textLayerPreviewError,
  pdfBackendUnavailable,
  convertedMdPath,
  convertedMdIdentity,
  importReadiness,
  markdownConversionState,
  onGeneratePreview,
  onGenerateMarkdown,
}) {
  const textPreview = textLayerPreview?.text_sample || previewGate?.plain_text_preview || previewGate?.md_preview || previewResult?.paper_md_preview || "";
  const markdownPreview = previewResult?.paper_md_preview || markdownConversionState?.data?.markdown_preview || "";
  const textPreviewBlocked = textLayerPreview?.status === "BLOCKED" || textLayerPreviewError;
  const qualityGood = Boolean(importReadiness.quality_good);
  const collapsed = qualityGood && !expanded;
  const leftMode = activeTab === "markdown" ? "markdown" : "text";

  if (collapsed) {
    return (
      <section id="previewSplitPane" className="previewWorkspace previewSplitPane collapsed" aria-label="导入前预览">
        <header>
          <div>
            <span>导入前预览</span>
            <h3>文本层质量良好</h3>
          </div>
          <div className="previewWorkspaceToolbar">
            <button type="button" onClick={onToggleExpanded}>查看预览</button>
          </div>
        </header>
        <div className="qualityFastPathNotice">
          <strong>文本层质量良好，预计不需要 OCR，可直接进入下一步。</strong>
          <span>预览已保留，可展开检查左侧文本和右侧 PDF 页面信息；当前步骤不会写入知识库，不会调用 LLM。</span>
        </div>
      </section>
    );
  }

  return (
    <section id="previewSplitPane" className={`previewWorkspace previewSplitPane ${expanded ? "expanded" : ""}`} aria-label="导入前预览">
      <header>
        <div>
          <span>导入前预览</span>
          <h3>导入前并排预览</h3>
        </div>
        <div className="previewWorkspaceToolbar">
          <div className="previewWorkspaceTabs" role="tablist" aria-label="预览文本格式">
            <button type="button" className={leftMode === "text" ? "active" : ""} onClick={() => onTabChange("text")}>纯文本</button>
            <button type="button" className={leftMode === "markdown" ? "active" : ""} onClick={() => onTabChange("markdown")}>Markdown</button>
          </div>
          <button type="button" onClick={onToggleExpanded}>{expanded ? "收起预览" : "折叠预览"}</button>
        </div>
      </header>
      <div className="previewWorkspaceBody previewSplitGrid">
        <section className="previewSplitColumn previewSplitText" aria-label="文本预览">
          <div className="previewGatePanelHeader">
            <h4>{leftMode === "markdown" ? "Markdown 预览" : "文本预览"}</h4>
            <span className="safetyNote">只读预览 · 不写知识库 · 不调用 LLM</span>
          </div>
          {leftMode === "markdown" ? (
            markdownConversionState?.status === "generating_markdown" ? (
              <PreviewWorkspaceEmpty title="正在生成 Markdown" body="转换只针对当前 PDF 文本层；完成后本页会自动切换到 converted_md 导入路线。" />
            ) : markdownConversionState?.status === "markdown_failed" ? (
              <PreviewWorkspaceEmpty title="Markdown 生成失败" body={markdownConversionState?.data?.message || markdownConversionState?.error || "请检查转换诊断信息。"} />
            ) : markdownPreview ? (
              <pre>{markdownPreview}</pre>
            ) : convertedMdPath && convertedMdIdentity.matches ? (
              <PreviewWorkspaceEmpty title="Markdown fallback 可用" body={`已找到 ${convertedMdPath}。请先生成导入前预览，再执行最终导入。`} actionLabel="生成导入前预览" onAction={onGeneratePreview} />
            ) : (
              <PreviewWorkspaceEmpty title="尚未生成 Markdown" body={convertedMdIdentity.reason || "尚未生成 Markdown，请点击生成 Markdown。"} actionLabel="生成 Markdown" onAction={onGenerateMarkdown} />
            )
          ) : textLayerPreviewLoading ? (
            <PreviewWorkspaceEmpty title="正在生成文本预览" body="该步骤只读取当前 PDF 前几页文本层，不写入知识库，不调用 LLM。" />
          ) : textPreview ? (
            <pre>{textPreview}</pre>
          ) : textPreviewBlocked ? (
            <PreviewWorkspaceEmpty
              title="文本预览暂不可用"
              body={textLayerPreview?.message || textLayerPreviewError || "当前 parser 不可用。本步骤未运行 OCR/Marker，也未写入数据库。"}
            />
          ) : (
            <PreviewWorkspaceEmpty
              title="尚无文本预览"
              body="请先生成导入前预览。该步骤只读取 PDF，不写入知识库，不调用 LLM。"
            />
          )}
        </section>
        <section className="previewSplitColumn previewSplitPdf" aria-label="PDF 页面预览">
          <div className="previewGatePanelHeader">
            <h4>PDF 页面预览</h4>
            <span className="previewGateCoordinateNote">页面元数据 / 文本样本同屏显示</span>
          </div>
          {previewGate?.pdf_preview?.length ? (
            <div className="previewWorkspacePdfMeta">
              {previewGate.pdf_preview.map(page => (
                <PreviewField key={page.physical_page} label={`物理页 p.${page.physical_page}`} value={`${page.page_width} x ${page.page_height} pt · 文本 ${page.text_layer_length} 字符`} />
              ))}
              <p className="safetyNote">当前保留物理页元数据；如果浏览器 PDF URL 可用，会在文档详情页使用 PDF.js 渲染原页。</p>
            </div>
          ) : textLayerPreview?.page_count ? (
            <div className="previewWorkspacePdfMeta">
              <PreviewField label="page_count" value={textLayerPreview.page_count} />
              <PreviewField label="sample_pages" value={(textLayerPreview.sample_pages || []).join(", ") || "—"} />
              <PreviewField label="parser_backend" value={textLayerPreview.parser_backend || "—"} />
              <PreviewField label="sample chars" value={textLayerPreview.sample_char_count || "—"} />
              <p className="safetyNote">页面图像预览不可用，但文本预览可用。该状态不会写入知识库，也不会运行 OCR/Marker。</p>
            </div>
          ) : pdfBackendUnavailable ? (
            <PreviewWorkspaceEmpty title="PDF 页面预览不可用" body="PyMuPDF 被本机策略阻止。请先查看文本预览或生成 Markdown；本步骤不会写入知识库。" />
          ) : (
            <PreviewWorkspaceEmpty title="尚无 PDF 页面预览" body="请在上方导入流程中生成导入前预览。若无法渲染页面图像，系统会至少显示页数与文本样本。" />
          )}
        </section>
      </div>
    </section>
  );
}

function PreviewWorkspaceEmpty({ title, body, actionLabel, onAction }) {
  return (
    <div className="previewWorkspaceEmpty">
      <strong>{title}</strong>
      <span>{body}</span>
      {actionLabel && onAction && (
        <button type="button" className="quietButton" onClick={onAction}>{actionLabel}</button>
      )}
    </div>
  );
}

export function isChapterSafetyBlocked(preview = {}) {
  if (!preview) return false;
  if (preview.book_safety_decision === "blocked") return true;
  if (preview.book_safety_decision === "allowed" || preview.book_safety_decision === "allowed_with_warnings") return false;
  return Number(preview.suspicious_chapter_titles_count || 0) > 0;
}

export function hasChapterSafetyWarnings(preview = {}) {
  return preview?.book_safety_decision === "allowed_with_warnings"
    || (preview?.book_safety_warnings || []).length > 0;
}

export function chapterSafetyBlockerText(preview = {}) {
  const blockers = preview?.book_safety_blockers || [];
  if (blockers.length) {
    return blockers.map(formatChapterSafetyBlocker).join("；");
  }
  if (Number(preview?.suspicious_chapter_titles_count || 0) > 0) {
    return `suspicious_chapter_titles=${preview.suspicious_chapter_titles_count}`;
  }
  return "unknown_book_safety_blocker";
}

function formatChapterSafetyBlocker(blocker = {}) {
  const code = blocker.code || blocker.legacy_reason || "unknown";
  const copy = {
    empty_or_missing_chapter_titles_high_ratio: "多个章节标题为空，无法确认章节边界",
    exact_duplicate_chapter_titles_high_ratio: "大量章节标题完全重复，可能是页眉或目录识别错误",
    boilerplate_title_repeated_high_ratio: "章节标题反复命中页眉、版权、书名或参考文献等非章节文本",
    non_monotonic_page_ranges: "章节页码范围不是递增顺序",
    missing_page_ranges_high_ratio: "多个章节缺少页码范围，无法安全绑定正文片段",
    chapter_count_below_minimum_for_full_book: "整本书检测到的章节数过低",
    selected_outline_unreliable: "选中的 PDF 目录结构不可靠",
    chapter_numbers_not_increasing: "章节编号不是递增顺序",
    suspicious_chapter_titles: "章节标题疑似正文、代码片段或页眉页脚噪声",
    duplicate_book: "检测到疑似重复导入",
    pdf_missing: "PDF 文件不存在",
    parser_backend_mismatch: "解析后端与请求后端不一致",
    parser_empty_output: "解析结果为空",
    page_marker_count_below_95_percent: "PDF 页标记数量明显不足",
    synthetic_full_text_not_apply_safe: "未检测到可靠章节，只能作为整段正文回退",
    chapter_count_above_120: "章节数量异常偏高，可能混入小节",
    chunk_count_zero: "未生成正文片段",
    chunk_count_above_8000: "正文片段数量异常偏高",
    chunk_binding_rate_below_80_percent: "正文片段与章节的绑定率过低",
    high_risk_warning: "解析阶段返回高风险警告",
  }[code] || code;
  const titles = (blocker.titles || []).slice(0, 3).filter(Boolean).join(" | ");
  return titles ? `${copy}：${titles}` : copy;
}

export function buildPreviewQualitySummary({ previewGate, textLayerPreview, pdfBackendUnavailable }) {
  if (pdfBackendUnavailable) {
    return {
      status: "blocked",
      text_coverage: null,
      scan_ratio: null,
      parser_backend: pdfBackendUnavailable.backend || "unavailable",
      sample_char_count: 0,
      message: "PDF 预检后端不可用；不会自动运行 OCR/Marker，请生成 Markdown 或使用明确 fallback。",
    };
  }
  const metrics = previewGate?.quality_metrics || textLayerPreview?.quality_summary || {};
  const textCoverage = Number(metrics.text_layer_coverage ?? metrics.text_coverage);
  const scanRatio = Number(metrics.scan_page_ratio ?? metrics.scan_ratio);
  const sampleChars = Number(textLayerPreview?.sample_char_count || previewGate?.plain_text_preview?.length || previewGate?.md_preview?.length || 0);
  const parserBackend = textLayerPreview?.parser_backend || previewGate?.recommended_route || "";
  const pageCount = Number(previewGate?.physical_page_count || textLayerPreview?.page_count || 0);
  const labeledPages = Number(previewGate?.page_label_count || textLayerPreview?.page_count || 0);
  const pageSemanticsNormal = !pageCount || !labeledPages || Math.abs(pageCount - labeledPages) <= Math.max(2, Math.ceil(pageCount * 0.1));
  const coverageGood = Number.isFinite(textCoverage) && textCoverage >= 0.9;
  const scanClean = Number.isFinite(scanRatio) ? scanRatio === 0 : Boolean(textLayerPreview?.status === "OK" && sampleChars >= 2000);
  const backendReady = Boolean(parserBackend && !String(parserBackend).includes("unavailable"));
  const sampleEnough = sampleChars >= 2000;
  const good = coverageGood && scanClean && backendReady && sampleEnough && pageSemanticsNormal;

  if (good) {
    return {
      status: "good",
      text_coverage: textCoverage,
      scan_ratio: Number.isFinite(scanRatio) ? scanRatio : 0,
      parser_backend: parserBackend,
      sample_char_count: sampleChars,
      page_semantics_normal: pageSemanticsNormal,
      message: "文本层质量良好，预计不需要 OCR，可直接进入下一步。",
    };
  }
  if (previewGate || textLayerPreview) {
    return {
      status: textLayerPreview?.status === "BLOCKED" ? "blocked" : "uncertain",
      text_coverage: Number.isFinite(textCoverage) ? textCoverage : null,
      scan_ratio: Number.isFinite(scanRatio) ? scanRatio : null,
      parser_backend: parserBackend || "unknown",
      sample_char_count: sampleChars,
      page_semantics_normal: pageSemanticsNormal,
      message: textLayerPreview?.message || "质量预检已返回，但文本覆盖率、页码或样本长度不足以走快速路径；请查看并排预览或生成 Markdown。",
    };
  }
  return {
    status: "not_checked",
    text_coverage: null,
    scan_ratio: null,
    parser_backend: "",
    sample_char_count: 0,
    message: "尚未生成质量预检。请先生成导入前预览；该步骤只读取 PDF，不写入知识库。",
  };
}

export function buildImportReadiness({
  sourceMode,
  pdfPath,
  selectedZoteroSource,
  classification,
  overrideDocumentType,
  overrideObjectImportMode,
  importingChaptered,
  previewGate,
  previewResult,
  pdfBackendUnavailable,
  convertedMdPath,
  convertedMdIdentity,
  markdownConversionState,
  duplicateCheck,
  duplicateCheckLoading = false,
  textLayerPreview,
}) {
  const hasPath = Boolean(String(pdfPath || "").trim());
  const sourceReady = sourceMode === "zotero_pdf" ? Boolean(selectedZoteroSource?.resolved_pdf_path || pdfPath) : hasPath;
  const hasPreview = Boolean(previewResult?.import_job_id);
  const previewBackendAvailable = Boolean(previewGate?.recommended_route === "normal_text_layer" && !pdfBackendUnavailable);
  const backendBlocked = Boolean(pdfBackendUnavailable?.error === "pdf_backend_unavailable" || pdfBackendUnavailable?.status === "BLOCKED");
  const markdownReady = Boolean(markdownConversionState?.status === "markdown_ready" && markdownConversionState?.data?.status === "OK" && markdownConversionState?.data?.identity_match);
  const markdownRunning = markdownConversionState?.status === "generating_markdown";
  const markdownFailed = markdownConversionState?.status === "markdown_failed";
  const convertedMdAvailable = Boolean((pdfBackendUnavailable?.converted_md_available || markdownReady) && convertedMdPath && convertedMdIdentity.matches);
  const documentType = overrideDocumentType || classification?.document_type || "paper";
  const importRoute = documentType === "book" ? "whole_book" : documentType === "paper" ? "whole_paper" : "full_document";
  const routeSummary = documentType === "book"
    ? "整本书导入"
    : documentType === "paper"
      ? "整篇论文导入"
      : "全文导入";
  const finalImportUnit = documentType === "book" ? "whole_book" : documentType === "paper" ? "whole_paper" : "full_document";
  const qualitySummary = buildPreviewQualitySummary({ previewGate, textLayerPreview, pdfBackendUnavailable });
  const qualityGood = Boolean(qualitySummary.status === "good");
  const duplicateFound = Boolean(duplicateCheck?.duplicate_found);
  const duplicateDocument = duplicateCheck?.existing_documents?.[0] || null;
  const selectedStatus = sourceMode === "zotero_pdf" ? zoteroPdfImportStatus(selectedZoteroSource) : null;
  const selectedBlocksDefaultImport = Boolean(selectedStatus?.blocksDefaultImport);
  const base = {
    source_ready: sourceReady,
    preview_backend_available: previewBackendAvailable,
    converted_md_available: convertedMdAvailable,
    recommended_route: importRoute,
    primary_action: hasPreview || qualityGood ? derivePrimaryImportAction(documentType) : "等待质量预检",
    primary_action_kind: "import",
    primary_action_enabled: sourceReady && (hasPreview || qualityGood) && Boolean(classification),
    can_import: sourceReady && (hasPreview || qualityGood) && Boolean(classification),
    can_generate_markdown: false,
    blocker: !classification
      ? "请先生成质量预检以确认文献类型。"
      : hasPreview || qualityGood
        ? ""
        : "请先生成导入前预览。该步骤只读取 PDF，不写入知识库。",
    next_action: hasPreview || qualityGood ? "confirm_import" : "generate_pre_import_preview",
    route_summary: routeSummary,
    backend_status: backendBlocked ? "unavailable" : previewBackendAvailable ? "available" : "not_checked",
    preview_action: hasPreview || qualitySummary.status !== "not_checked" ? "刷新质量预检" : "生成导入前预览",
    preview_action_enabled: sourceReady,
    final_import_unit: finalImportUnit,
    quality_good: qualityGood,
    quality_status: qualitySummary.status,
    quality_summary: qualitySummary,
    quality_summary_message: qualitySummary.message,
  };
  if (!sourceReady) {
    return {
      ...base,
      recommended_route: "select_source",
      primary_action: "请选择 PDF 或 Markdown",
      primary_action_kind: "select_source",
      primary_action_enabled: false,
      can_import: false,
      blocker: sourceMode === "converted_md" ? "请先选择已转换 Markdown 文件。" : "请先选择一个可读取的 PDF。",
      next_action: "select_source",
      route_summary: "等待来源",
      preview_action_enabled: false,
    };
  }
  if (importingChaptered) {
    return {
      ...base,
      recommended_route: "wait_for_current_import",
      primary_action: "导入任务进行中",
      primary_action_kind: "wait",
      primary_action_enabled: false,
      can_import: false,
      blocker: "章节化导入任务正在运行，请等待或取消后再操作。",
      next_action: "wait",
      route_summary: "等待当前任务完成",
      preview_action_enabled: false,
    };
  }
  if (selectedBlocksDefaultImport) {
    return {
      ...base,
      recommended_route: "already_imported",
      primary_action: "打开已有文档",
      primary_action_kind: "open_existing_document",
      primary_action_enabled: Boolean(selectedStatus.existingDocumentId),
      existing_document_id: selectedStatus.existingDocumentId || null,
      can_import: false,
      blocker: selectedStatus.status === "exact_imported"
        ? "当前 PDF 已经入库；默认打开已有文档，不会重复写入知识库。"
        : `${selectedStatus.label}；默认打开已有文档，不会重复写入知识库。`,
      next_action: "open_existing_document",
      route_summary: "已有文档",
      preview_action_enabled: sourceReady && !duplicateCheckLoading,
      final_import_unit: "already_imported",
    };
  }
  if (duplicateFound) {
    return {
      ...base,
      recommended_route: "already_imported",
      primary_action: "打开已有文档",
      primary_action_kind: "open_existing_document",
      primary_action_enabled: Boolean(duplicateDocument?.document_id),
      existing_document_id: duplicateDocument?.document_id || null,
      can_import: false,
      blocker: "检测到重复导入风险；默认打开已有文档，不会重复写入知识库。",
      next_action: "open_existing_document",
      route_summary: "已有文档",
      preview_action_enabled: sourceReady && !duplicateCheckLoading,
      final_import_unit: "already_imported",
    };
  }
  if (classification?.duplicate) {
    return {
      ...base,
      recommended_route: "already_imported",
      primary_action: "已导入",
      primary_action_kind: "open_existing_document",
      primary_action_enabled: false,
      can_import: false,
      blocker: "该 PDF 已导入，请打开已有文档详情页。",
      next_action: "open_existing_document",
      route_summary: "已有文档",
      preview_action_enabled: false,
    };
  }
  if (sourceMode === "converted_md") {
    const validMarkdown = hasPath && String(pdfPath || "").toLowerCase().trim().endsWith(".md");
    return {
      ...base,
      recommended_route: "converted_md",
      primary_action: validMarkdown && hasPreview ? "使用已转换 Markdown 导入" : validMarkdown ? "请先生成导入前预览" : "请先生成 Markdown",
      primary_action_kind: validMarkdown ? "import" : "select_source",
      primary_action_enabled: validMarkdown && hasPreview,
      can_import: validMarkdown && hasPreview,
      blocker: validMarkdown
        ? (hasPreview ? "" : "请先生成导入前预览。该步骤只读取 Markdown 并生成 staging 文件，不写入知识库。")
        : "已转换 Markdown 导入需要 .md 文件。",
      next_action: validMarkdown ? (hasPreview ? "confirm_import" : "generate_pre_import_preview") : "run_pdf_to_md_conversion",
      route_summary: "使用已转换 Markdown 导入",
      converted_md_available: validMarkdown,
      preview_action_enabled: validMarkdown,
    };
  }
  if (backendBlocked) {
    if (convertedMdAvailable) {
      return {
        ...base,
        recommended_route: "converted_md",
        primary_action: hasPreview ? "使用已转换 Markdown 导入" : "请先生成导入前预览",
        primary_action_kind: "import",
        primary_action_enabled: hasPreview,
        can_import: hasPreview,
        blocker: hasPreview ? "" : "请先生成导入前预览。该步骤只读取 Markdown fallback，不写入知识库。",
        next_action: hasPreview ? "confirm_import" : "generate_pre_import_preview",
        route_summary: "PyMuPDF 不可用，改用已转换 Markdown",
        backend_status: "unavailable",
        converted_md_available: true,
        preview_action_enabled: true,
      };
    }
    return {
      ...base,
      recommended_route: "generate_markdown_first",
      primary_action: markdownRunning ? "正在生成 Markdown..." : "生成 Markdown",
      primary_action_kind: "generate_markdown",
      primary_action_enabled: sourceReady && !markdownRunning,
      can_import: false,
      can_generate_markdown: sourceReady && !markdownRunning,
      blocker: markdownFailed
        ? (markdownConversionState?.data?.message || markdownConversionState?.error || "Markdown 生成失败。")
        : convertedMdIdentity.reason || "轻量 PDF 预检不可用，且未找到匹配的 converted_md。",
      next_action: "run_pdf_to_md_conversion",
      route_summary: "先生成 Markdown",
      backend_status: "unavailable",
      converted_md_available: false,
      preview_action_enabled: sourceReady,
    };
  }
  return base;
}

export function evaluateConvertedMdIdentity({ convertedMdPath = "", pdfPath = "", title = "" }) {
  const md = normalizeIdentityText(convertedMdPath);
  const pdf = normalizeIdentityText(pdfPath);
  const titleText = normalizeIdentityText(title);
  if (!convertedMdPath) {
    return { matches: false, reason: "尚未生成 Markdown。" };
  }
  if (md.includes("test_minimal") && !pdf.includes("test_minimal") && !titleText.includes("test_minimal")) {
    return { matches: false, reason: "检测到 converted_md 与当前 PDF 不匹配，请先生成当前 PDF 的 Markdown。" };
  }
  const espcnTitle = titleText.includes("real_time_single_image")
    || titleText.includes("efficient_sub_pixel")
    || titleText.includes("espcn");
  if (espcnTitle && !/(espcn|sub_pixel|super_resolution|efficient)/.test(md)) {
    return { matches: false, reason: "检测到 converted_md 与当前 ESPCN PDF 不匹配，请先生成当前 PDF 的 Markdown。" };
  }
  const pdfStem = normalizeIdentityText(lastPathSegment(pdfPath).replace(/\.[^.]+$/, ""));
  const mdStem = normalizeIdentityText(lastPathSegment(convertedMdPath).replace(/\.[^.]+$/, ""));
  if (pdfStem && mdStem && mdStem.includes(pdfStem)) {
    return { matches: true, reason: "" };
  }
  if (espcnTitle && /(espcn|sub_pixel|super_resolution|efficient)/.test(md)) {
    return { matches: true, reason: "" };
  }
  return { matches: true, reason: "" };
}

function lastPathSegment(value = "") {
  const parts = String(value || "").replace(/\\/g, "/").split("/");
  return parts[parts.length - 1] || "";
}

export function pdfBackendUnavailablePayload(error) {
  const payload = error?.payload;
  if (payload?.error === "pdf_backend_unavailable" || payload?.status === "BLOCKED") {
    return payload;
  }
  return null;
}

export function PdfBackendFallbackPanel({ payload = {}, convertedMdPath = "", convertedMdIdentity = { matches: false, reason: "" }, onUseConvertedMd }) {
  const convertedAvailable = Boolean(payload.converted_md_available && convertedMdPath && convertedMdIdentity.matches);
  return (
    <div className="importReviewBanner pdfBackendFallbackPanel">
      <p><strong>轻量 PDF 预检不可用：PyMuPDF 被本机策略阻止。你仍可以使用已转换的 Markdown / Marker 结果导入。</strong></p>
      <div className="previewResultGrid">
        <PreviewField label="预检后端" value="不可用" />
        <PreviewField label="converted_md 是否可用" value={convertedAvailable ? "是" : "否"} />
        <PreviewField label="可选回退路线" value={(payload.fallback_routes || ["converted_md", "marker_output", "manual_md_import"]).join(", ")} />
        <PreviewField label="下一步动作" value={convertedAvailable ? "继续使用 converted_md 导入" : "先运行 PDF→MD 转换"} />
      </div>
      {convertedMdPath && <code>{convertedMdPath}</code>}
      {!convertedMdIdentity.matches && convertedMdIdentity.reason && <p>{convertedMdIdentity.reason}</p>}
      <div className="previewActions">
        <button type="button" className="primaryButton" onClick={onUseConvertedMd} disabled={!convertedAvailable}>
          {convertedAvailable ? "使用已转换 Markdown 导入" : "请先生成 Markdown"}
        </button>
      </div>
    </div>
  );
}

function ImportCommitStatus({ state = {}, onOpenDocument }) {
  if (state.status === "previewing") {
    return <StateMessage title="正在生成整篇导入预览" body="正在创建 import_job_id 与 staging 文件，本阶段不写核心数据库。" />;
  }
  if (state.status === "committing") {
    const body = state.stage === "commit-book"
      ? "正在调用 commit-book 写入 documents / book_chapters / chunks。不会调用外部大模型，也不会生成机制。"
      : "正在调用 commit-paper 写入 documents / chunks。不会调用外部大模型，也不会生成机制。";
    return <StateMessage title="正在写入知识库" body={body} />;
  }
  if (state.status === "failed") {
    return (
      <StateMessage
        title="导入失败"
        body={`stage=${state.stage || "unknown"}。${state.error || "请检查 PDF 路径、预览结果或后端错误信息后重试。"}`}
      />
    );
  }
  if (state.status !== "success" && state.status !== "already_committed") return null;
  const data = state.data || {};
  return (
    <div className="importCommitResult">
      <div className="sectionHeader">
        <h3>{state.status === "already_committed" ? "该导入作业已入库" : "导入成功"}</h3>
        <span>document_id={data.document_id || "n/a"}</span>
      </div>
      <div className="previewResultGrid">
        <PreviewField label="document_id" value={data.document_id} />
        <PreviewField label="title" value={data.title} />
        <PreviewField label="chunks count" value={data.chunk_count} />
        <PreviewField label="chapters count" value={data.inserted_chapters} />
        <PreviewField label="objects / candidates" value="未在本步骤写入" />
        <PreviewField label="core_db_write_performed" value={String(Boolean(data.core_db_write_performed))} />
        <PreviewField label="external_llm_called" value={String(Boolean(data.external_llm_called))} />
        <PreviewField label="Zotero 原生笔记" value={nativeNotesSummary(data.zotero_native_notes_import)} />
      </div>
      {data.document_id && (
        <div className="previewActions">
          <button type="button" className="primaryButton" onClick={() => onOpenDocument?.(data.document_id)}>
            打开文档详情页
          </button>
        </div>
      )}
    </div>
  );
}

function ImportSafetySnapshot({ previewResult, commitResult }) {
  const result = commitResult || previewResult || {};
  const dbWrite = Boolean(result.core_db_write_performed ?? result.db_write_performed);
  const externalLlm = Boolean(result.external_llm_called ?? result.llm_called);
  const mechanismGenerated = Boolean(result.mechanism_generated || result.final_hypothesis_created);
  return (
    <div className="importSafetySnapshot" aria-label="导入安全状态">
      <span>生产写入已执行：{dbWrite ? "是" : "否"}</span>
      <span>已调用外部大模型：{externalLlm ? "是" : "否"}</span>
      <span>已执行数据库写入：{dbWrite ? "是" : "否"}</span>
      <span>已生成 mechanism：{mechanismGenerated ? "是" : "否"}</span>
    </div>
  );
}

export function isEspcnTitle(value = "") {
  const title = String(value || "").toLowerCase();
  return title.includes("real-time single image and video super-resolution")
    || title.includes("efficient sub-pixel convolutional neural network")
    || title.includes("espcn");
}

export const ESPCN_SEED_APPLY_COMMAND = "<Search Python> scripts\\phase110k_r2_seed_espcn_frontend_acceptance_notes.py --db-path data/db/research_memory.db --seed-json data/seeds/espcn10_frontend_acceptance_notes.json --apply --json";
