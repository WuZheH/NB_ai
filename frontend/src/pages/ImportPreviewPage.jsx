import { useEffect, useMemo, useState } from "react";
import { getJson, postJson } from "../api/client.js";
import {
  basename,
  cacheStatusLabel,
  confidenceLabel,
  deviceLabel,
  documentTypeLabel,
  importModeLabel,
  nativeNotesSummary,
  stageLabel,
} from "../features/importing/utils/importPreviewFormatters.js";
import {
  ESPCN_SEED_APPLY_COMMAND,
  ChapteredImportDeviceNotice,
  IDLE_CHAPTERED_COMMIT_STATE,
  IDLE_FULL_DOCUMENT_COMMIT_STATE,
  IDLE_MARKDOWN_CONVERSION_STATE,
  ImportLinearWizard,
  MainImportDecisionPanel,
  PdfBackendFallbackPanel,
  PreviewField,
  PreviewWorkspace,
  SourceSummary,
  buildImportReadiness,
  buildPreviewQualitySummary,
  chapterSafetyBlockerText,
  evaluateConvertedMdIdentity,
  filteredDeviceWarnings,
  hasChapterSafetyWarnings,
  inferTitleFromPath,
  isChapterSafetyBlocked,
  isEspcnTitle,
  pdfBackendUnavailablePayload,
  resetForNewImportSource,
  titleFromZoteroSource,
} from "../features/importing/components/ImportPreviewContent.jsx";
import RepairPreviewPanel from "../components/import/RepairPreviewPanel.jsx";
import StateMessage from "../components/StateMessage.jsx";
import AdvancedDiagnostics from "./import/ImportDiagnosticsPanel.jsx";
import {
  deriveConfirmationContext,
  deriveDocumentKindForImport,
} from "./import/importKindPolicy.js";
import {
  buildClassifyPayload as createClassifyPayload,
  buildPreviewGatePayload as createPreviewGatePayload,
  buildPreviewPayload as createPreviewPayload,
} from "./import/importRequestBuilders.js";
import { zoteroPdfImportStatus } from "./import/zoteroPdfImportStatus.js";

const FULL_DOCUMENT_COMMIT_UNAVAILABLE_MESSAGE = "全文入库后端尚未启用，不能使用论文 commit 路径。";
const NO_WRITE_SAFETY_FLAGS = {
  status: "BLOCKED",
  db_write_performed: false,
  core_db_write_performed: false,
  vector_store_write_performed: false,
  zotero_db_write_performed: false,
  llm_called: false,
  external_llm_called: false,
  mechanism_generated: false,
  mechanism_draft_written: false,
  seed_apply_performed: false,
  ocr_or_marker_performed: false,
};

export default function ImportPreviewPage({ state, setState, updateSafety, onNavigate, onAutoFillJobId, onSelectZoteroSource, onPreviewResult, onOpenDocument }) {
  const [previewWorkspaceTab, setPreviewWorkspaceTab] = useState("text");
  const [previewWorkspaceExpanded, setPreviewWorkspaceExpanded] = useState(false);
  const { pdfPath, titleHint, loading, previewResult, previewError, bundleLoading, bundleResult, bundleError, bundleContent, bundleContentLoading } = state;
  const classification = state.classification || null;
  const classifyLoading = Boolean(state.classifyLoading);
  const classifyError = state.classifyError || "";
  const overrideDocumentType = state.overrideDocumentType || classification?.document_type || "paper";
  const overrideObjectImportMode = state.overrideObjectImportMode || classification?.object_import_mode || "full_document";
  const chapteredCommitState = state.chapteredCommitState || { status: "idle", data: null, error: "" };
  const chapteredImportJob = state.chapteredImportJob || null;
  const chapteredImportJobPolling = Boolean(state.chapteredImportJobPolling);
  const importingChaptered = chapteredImportJobPolling || (chapteredImportJob?.status === "running" || chapteredImportJob?.status === "queued");
  const chapteredPreview = state.chapteredPreview || null;
  const chapteredPreviewLoading = Boolean(state.chapteredPreviewLoading);
  const chapteredPreviewConfirmed = Boolean(state.chapteredPreviewConfirmed);
  const selectedChapterIndexes = state.selectedChapterIndexes || [];
  const chapteredImportGranularity = state.chapteredImportGranularity || "chapter";
  const sourceMode = state.sourceMode || "local_pdf";
  const zoteroQuery = state.zoteroQuery || "";
  const zoteroStatus = state.zoteroStatus ?? "available";
  const zoteroSources = state.zoteroSources || [];
  const selectedZoteroSource = state.selectedZoteroSource;
  const pdfSelectionStage = state.pdfSelectionStage || "browse";
  const zoteroLoading = Boolean(state.zoteroLoading);
  const zoteroError = state.zoteroError || "";
  const previewGate = state.previewGate || null;
  const previewGateLoading = Boolean(state.previewGateLoading);
  const previewGateError = state.previewGateError || "";
  const selectedImportRoute = state.selectedImportRoute || "";
  const repairPreview = state.repairPreview || null;
  const repairPreviewLoading = Boolean(state.repairPreviewLoading);
  const repairPreviewError = state.repairPreviewError || "";
  const repairPlanDraft = state.repairPlanDraft || null;
  const repairPlanLoading = Boolean(state.repairPlanLoading);
  const repairPlanError = state.repairPlanError || "";
  const fullDocumentCommitState = state.fullDocumentCommitState || { status: "idle", stage: "", data: null, error: "" };
  const fullDocumentImportRunning = fullDocumentCommitState.status === "previewing" || fullDocumentCommitState.status === "committing";
  const fullDocumentCommitResult = fullDocumentCommitState.data || null;
  const fullDocumentImportSucceeded = fullDocumentCommitState.status === "success" || fullDocumentCommitState.status === "already_committed";
  const pdfBackendUnavailable = state.pdfBackendUnavailable || null;
  const markdownConversionState = state.markdownConversionState || { status: "idle", data: null, error: "" };
  const markdownConversionRunning = markdownConversionState.status === "generating_markdown";
  const textLayerPreview = state.textLayerPreview || null;
  const textLayerPreviewLoading = Boolean(state.textLayerPreviewLoading);
  const textLayerPreviewError = state.textLayerPreviewError || "";
  const duplicateCheck = state.duplicateCheck || null;
  const duplicateCheckLoading = Boolean(state.duplicateCheckLoading);
  const duplicateCheckError = state.duplicateCheckError || "";
  const preImportPreviewLoading = Boolean(state.preImportPreviewLoading);
  const convertedMdMode = sourceMode === "converted_md" || selectedImportRoute === "converted_md";
  const generatedConvertedMdPath = markdownConversionState.status === "markdown_ready" ? markdownConversionState.data?.converted_md_path : "";
  const convertedMdPath = state.convertedMdPath || generatedConvertedMdPath || pdfBackendUnavailable?.converted_md_path || "";
  const currentPdfSourcePath = sourceMode === "zotero_pdf"
    ? (selectedZoteroSource?.resolved_pdf_path || pdfPath || "")
    : sourceMode === "local_pdf"
      ? (pdfPath || "")
      : "";
  const sourceTitle = selectedZoteroSource?.title || classification?.title || titleHint || "";
  const documentKindForImport = deriveDocumentKindForImport({
    classification,
    explicitDocumentType: state.overrideDocumentType,
    selectedZoteroSource,
    title: sourceTitle,
    titleHint,
  });
  const convertedMdIdentity = evaluateConvertedMdIdentity({
    convertedMdPath,
    pdfPath,
    title: sourceTitle,
  });
  const previewGateBlocksFullDocument = Boolean(previewGate && !convertedMdMode && selectedImportRoute !== "normal_text_layer");
  const previewGateLocksImport = Boolean(previewGate && !convertedMdMode && selectedImportRoute && selectedImportRoute !== "normal_text_layer");
  const importReadiness = useMemo(() => buildImportReadiness({
    sourceMode,
    pdfPath,
    selectedZoteroSource,
    classification,
    overrideDocumentType: documentKindForImport,
    overrideObjectImportMode,
    importingChaptered,
    previewGate,
    previewResult,
    pdfBackendUnavailable,
    convertedMdPath,
    convertedMdIdentity,
    markdownConversionState,
    duplicateCheck,
    duplicateCheckLoading,
    textLayerPreview,
  }), [
    sourceMode,
    pdfPath,
    selectedZoteroSource,
    classification,
    documentKindForImport,
    overrideObjectImportMode,
    importingChaptered,
    previewGate,
    previewResult,
    pdfBackendUnavailable,
    convertedMdPath,
    convertedMdIdentity,
    markdownConversionState,
    duplicateCheck,
    duplicateCheckLoading,
    textLayerPreview,
  ]);
  const fullDocumentImportBlockedReason = importReadiness.can_import ? "" : importReadiness.blocker;

  const isZoteroSelected = sourceMode === "zotero_pdf" && pdfSelectionStage === "selected" && selectedZoteroSource;
  const showZoteroBrowse = sourceMode === "zotero_pdf" && pdfSelectionStage === "browse";

  useEffect(() => {
    if (sourceMode === "converted_md") return;
    if (!String(currentPdfSourcePath || "").trim()) return;
    const requestKey = [
      sourceMode,
      currentPdfSourcePath,
      selectedZoteroSource?.zotero_item_key || "",
      selectedZoteroSource?.zotero_attachment_key || "",
      selectedZoteroSource?.title || "",
    ].join("|");
    if (state.duplicateCheckRequestKey === requestKey && (duplicateCheck || duplicateCheckLoading)) return;
    let cancelled = false;
    runDuplicateCheck(requestKey).catch(() => {
      if (!cancelled) {
        setState(s => ({ ...s, duplicateCheckLoading: false }));
      }
    });
    return () => { cancelled = true; };
  }, [
    sourceMode,
    currentPdfSourcePath,
    selectedZoteroSource?.zotero_item_key,
    selectedZoteroSource?.zotero_attachment_key,
    selectedZoteroSource?.title,
  ]);

  async function createPreview() {
    if (previewGateBlocksFullDocument) {
      setState(s => ({ ...s, previewError: selectedImportRoute
        ? "当前选择的不是普通文本层路线；整篇导入前请切回普通文本层导入。"
        : "请先在高级诊断中选择普通文本层路线，再生成整篇导入预览。"
      }));
      return;
    }
    const path = pdfPath.trim();
    if (sourceMode === "local_pdf" && !path) {
      setState(s => ({ ...s, previewError: "请输入 PDF 路径。" }));
      return;
    }
    if (sourceMode === "zotero_pdf" && !selectedZoteroSource?.id) {
      setState(s => ({ ...s, previewError: "请先选择一个 Zotero PDF 候选。" }));
      return;
    }
    setState(s => ({
      ...s,
      loading: true,
      previewError: "",
      previewResult: null,
      bundleResult: null,
      bundleContent: null,
      fullDocumentCommitState: IDLE_FULL_DOCUMENT_COMMIT_STATE,
    }));
    try {
      const result = await postJson("/api/v1/imports/preview-only", buildPreviewPayload());
      setState(s => ({ ...s, loading: false, previewResult: result, pdfBackendUnavailable: null }));
      onPreviewResult?.(result);
      onAutoFillJobId?.(result.import_job_id);
      updateSafety(result);
      return result;
    } catch (e) {
      const blocked = pdfBackendUnavailablePayload(e);
      if (blocked) {
        setState(s => ({
          ...s,
          loading: false,
          pdfBackendUnavailable: blocked,
          convertedMdPath: blocked.converted_md_path || s.convertedMdPath || "",
          previewError: "轻量 PDF 预检不可用。请使用已转换 Markdown / Marker 结果导入，或先生成 Markdown。",
        }));
        updateSafety(blocked);
        return null;
      }
      setState(s => ({ ...s, loading: false, previewError: `导入预览失败：${e.message}` }));
      return null;
    }
  }

  async function classifyPdf() {
    const path = pdfPath.trim();
    if (sourceMode === "local_pdf" && !path) {
      setState(s => ({ ...s, classifyError: "请输入 PDF 路径。" }));
      return;
    }
    if (sourceMode === "zotero_pdf" && !selectedZoteroSource?.id) {
      setState(s => ({ ...s, classifyError: "请先选择一个 Zotero PDF 候选。" }));
      return;
    }
    setState(s => ({
      ...s,
      classifyLoading: true,
      classifyError: "",
      classification: null,
      chapteredCommitState: IDLE_CHAPTERED_COMMIT_STATE,
    }));
    try {
      const result = await postJson("/api/v1/library/import/pdf/classify", buildClassifyPayload());
      setState(s => ({
        ...s,
        classifyLoading: false,
        pdfBackendUnavailable: null,
        classification: result,
        overrideDocumentType: result.document_type,
        overrideObjectImportMode: result.object_import_mode,
      }));
      updateSafety(result);
      return result;
    } catch (e) {
      const blocked = pdfBackendUnavailablePayload(e);
      if (blocked) {
        setState(s => ({
          ...s,
          classifyLoading: false,
          classifyError: "",
          pdfBackendUnavailable: blocked,
          convertedMdPath: blocked.converted_md_path || s.convertedMdPath || "",
          selectedImportRoute: blocked.converted_md_available ? "converted_md" : s.selectedImportRoute,
        }));
        updateSafety(blocked);
        return null;
      }
      setState(s => ({ ...s, classifyLoading: false, classifyError: `自动识别失败：${e.message}` }));
      return null;
    }
  }

  async function fetchPreviewGate() {
    const path = pdfPath.trim();
    if (sourceMode === "local_pdf" && !path) {
      setState(s => ({ ...s, previewGateError: "请输入 PDF 路径。" }));
      return;
    }
    if (sourceMode === "zotero_pdf" && !selectedZoteroSource?.resolved_pdf_path) {
      setState(s => ({ ...s, previewGateError: "请先选择一个可读取的 Zotero PDF 候选。" }));
      return;
    }
    setState(s => ({
      ...s,
      previewGateLoading: true,
      previewGateError: "",
      previewGate: null,
      selectedImportRoute: "",
      previewGateNotice: "",
      repairPreview: null,
      repairPreviewLoading: false,
      repairPreviewError: "",
      repairPlanDraft: null,
      repairPlanLoading: false,
      repairPlanError: "",
    }));
    try {
      const result = await postJson("/api/v1/library/import/pdf/preview-gate", buildPreviewGatePayload());
      setState(s => ({
        ...s,
        previewGateLoading: false,
        previewGate: result,
        pdfBackendUnavailable: null,
        selectedImportRoute: result.recommended_route === "normal_text_layer" ? "normal_text_layer" : "",
      }));
      updateSafety(result);
      return result;
    } catch (e) {
      const blocked = pdfBackendUnavailablePayload(e);
      if (blocked) {
        setState(s => ({
          ...s,
          previewGateLoading: false,
          previewGate: null,
          pdfBackendUnavailable: blocked,
          convertedMdPath: blocked.converted_md_path || s.convertedMdPath || "",
          selectedImportRoute: blocked.converted_md_available ? "converted_md" : "",
          previewGateError: "",
        }));
        updateSafety(blocked);
        return null;
      }
      setState(s => ({
        ...s,
        previewGateLoading: false,
        previewGateError: `首节质量预览失败：${e.message}。可继续使用旧识别流程。`,
      }));
      return null;
    }
  }

  async function fetchTextLayerPreview() {
    const sourcePdfPath = String(currentPdfSourcePath || "").trim();
    if (sourceMode === "converted_md") return null;
    if (!sourcePdfPath) {
      setState(s => ({
        ...s,
        textLayerPreview: null,
        textLayerPreviewError: "缺少当前 PDF 路径，无法生成文本预览。",
      }));
      return null;
    }
    setState(s => ({
      ...s,
      textLayerPreviewLoading: true,
      textLayerPreviewError: "",
      textLayerPreview: null,
    }));
    try {
      const result = await postJson("/api/v1/imports/text-layer-preview", {
        pdf_path: sourcePdfPath,
        title: sourceTitle || titleHint || inferTitleFromPath(sourcePdfPath) || undefined,
        max_pages: 4,
        max_chars: 4000,
      });
      setState(s => ({ ...s, textLayerPreviewLoading: false, textLayerPreview: result, textLayerPreviewError: "" }));
      updateSafety(result);
      return result;
    } catch (e) {
      const blocked = e.payload || null;
      if (blocked?.status === "BLOCKED") {
        setState(s => ({
          ...s,
          textLayerPreviewLoading: false,
          textLayerPreview: blocked,
          textLayerPreviewError: blocked.message || e.message,
        }));
        updateSafety(blocked);
        return blocked;
      }
      setState(s => ({
        ...s,
        textLayerPreviewLoading: false,
        textLayerPreview: null,
        textLayerPreviewError: `文本预览失败：${e.message}`,
      }));
      return null;
    }
  }

  async function runDuplicateCheck(requestKey = null) {
    if (sourceMode === "converted_md") return null;
    const sourcePdfPath = String(currentPdfSourcePath || "").trim();
    if (!sourcePdfPath) {
      setState(s => ({
        ...s,
        duplicateCheck: null,
        duplicateCheckLoading: false,
        duplicateCheckError: "",
        duplicateCheckRequestKey: "",
      }));
      return null;
    }
    const key = requestKey || [
      sourceMode,
      sourcePdfPath,
      selectedZoteroSource?.zotero_item_key || "",
      selectedZoteroSource?.zotero_attachment_key || "",
      selectedZoteroSource?.title || "",
    ].join("|");
    setState(s => ({
      ...s,
      duplicateCheckLoading: true,
      duplicateCheckError: "",
      duplicateCheckRequestKey: key,
    }));
    try {
      const result = await postJson("/api/v1/imports/duplicate-check", {
        pdf_path: sourcePdfPath,
        title: selectedZoteroSource?.title || titleHint || inferTitleFromPath(sourcePdfPath) || undefined,
        zotero_item_key: selectedZoteroSource?.zotero_item_key || undefined,
        zotero_attachment_key: selectedZoteroSource?.zotero_attachment_key || undefined,
      });
      setState(s => {
        if (s.duplicateCheckRequestKey !== key) return s;
        return {
          ...s,
          duplicateCheckLoading: false,
          duplicateCheck: result,
          duplicateCheckError: "",
        };
      });
      updateSafety(result);
      return result;
    } catch (e) {
      setState(s => {
        if (s.duplicateCheckRequestKey !== key) return s;
        return {
          ...s,
          duplicateCheckLoading: false,
          duplicateCheck: null,
          duplicateCheckError: `重复导入检查失败：${e.message}`,
        };
      });
      return null;
    }
  }

  async function generatePreImportPreview() {
    setPreviewWorkspaceTab("text");
    setState(s => ({
      ...s,
      preImportPreviewLoading: true,
      preImportPreviewError: "",
      previewError: "",
      classifyError: "",
      fullDocumentCommitState: IDLE_FULL_DOCUMENT_COMMIT_STATE,
      chapteredImportJobError: "",
      bundleResult: null,
      bundleError: "",
      bundleContent: null,
    }));
    try {
      if (sourceMode === "converted_md") {
        await createPreview();
        return;
      }

      const duplicate = await runDuplicateCheck();
      const selectedStatus = zoteroPdfImportStatus(selectedZoteroSource);
      if (duplicate?.duplicate_found && selectedStatus.status !== "sibling_imported") return;
      await fetchTextLayerPreview();
      await classifyPdf();
      await fetchPreviewGate();
      await createPreview();
    } finally {
      setState(s => ({ ...s, preImportPreviewLoading: false }));
    }
  }

  function viewPreviewWorkspace() {
    setPreviewWorkspaceExpanded(true);
    setPreviewWorkspaceTab("text");
    window.requestAnimationFrame(() => {
      document.getElementById("previewSplitPane")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  function activateConvertedMdImport() {
    if (!convertedMdPath) return;
    setState(s => resetForNewImportSource(s, {
      sourceMode: "converted_md",
      pdfPath: convertedMdPath,
      titleHint: titleHint || inferTitleFromPath(convertedMdPath),
      convertedMdPath,
      selectedImportRoute: "converted_md",
    }));
  }

  async function generateMarkdownForCurrentPdf() {
    const sourcePdfPath = String(currentPdfSourcePath || "").trim();
    if (!sourcePdfPath) {
      setState(s => ({
        ...s,
        markdownConversionState: {
          status: "markdown_failed",
          data: null,
          error: "缺少当前 PDF 路径，无法生成 Markdown。",
        },
      }));
      setPreviewWorkspaceTab("markdown");
      return;
    }

    setPreviewWorkspaceTab("markdown");
    setState(s => ({
      ...s,
      markdownConversionState: { status: "generating_markdown", data: null, error: "" },
      previewError: "",
      previewResult: null,
      bundleResult: null,
      bundleContent: null,
      fullDocumentCommitState: IDLE_FULL_DOCUMENT_COMMIT_STATE,
    }));

    const payload = {
      pdf_path: sourcePdfPath,
      zotero_item_key: selectedZoteroSource?.zotero_item_key || undefined,
      zotero_attachment_key: selectedZoteroSource?.zotero_attachment_key || undefined,
      title: sourceTitle || titleHint || undefined,
    };

    try {
      const result = await postJson("/api/v1/imports/convert-pdf-to-markdown", payload);
      setState(s => ({
        ...s,
        markdownConversionState: { status: "markdown_ready", data: result, error: "" },
        convertedMdPath: result.converted_md_path || s.convertedMdPath || "",
        selectedImportRoute: "converted_md",
        pdfBackendUnavailable: {
          ...(s.pdfBackendUnavailable || {}),
          status: s.pdfBackendUnavailable?.status || "BLOCKED",
          error: s.pdfBackendUnavailable?.error || "pdf_backend_unavailable",
          backend: s.pdfBackendUnavailable?.backend || "pymupdf",
          converted_md_available: true,
          converted_md_exists: true,
          converted_md_path: result.converted_md_path,
          fallback_available: true,
          fallback_routes: s.pdfBackendUnavailable?.fallback_routes || ["converted_md", "marker_output", "manual_md_import"],
          next_action: "use_converted_md_import",
        },
        previewGateNotice: "Markdown 已生成；页面已刷新为 converted_md 导入路线。不会自动执行导入。",
        previewGate: null,
        previewGateError: "",
        previewError: "",
        previewResult: null,
        bundleResult: null,
        bundleContent: null,
        fullDocumentCommitState: IDLE_FULL_DOCUMENT_COMMIT_STATE,
      }));
      updateSafety(result);
    } catch (e) {
      const blocked = e.payload || {};
      const message = blocked.message || e.message || "Markdown 生成失败。";
      setState(s => ({
        ...s,
        markdownConversionState: { status: "markdown_failed", data: blocked, error: message },
        pdfBackendUnavailable: s.pdfBackendUnavailable ? {
          ...s.pdfBackendUnavailable,
          converted_md_available: false,
          converted_md_exists: false,
          next_action: "run_pdf_to_md_conversion",
        } : s.pdfBackendUnavailable,
        selectedImportRoute: "",
        previewGateNotice: "",
        fullDocumentCommitState: IDLE_FULL_DOCUMENT_COMMIT_STATE,
      }));
      if (blocked.status || blocked.error) {
        updateSafety(blocked);
      }
    }
  }

  async function selectImportRoute(route) {
    if (route === "cancel_or_replace_pdf") {
      setState(s => ({
        ...s,
        previewGate: null,
        selectedImportRoute: "",
        previewGateNotice: "已取消质量预览路线选择。可更换 PDF，或继续使用现有识别流程。",
        repairPreview: null,
        repairPreviewLoading: false,
        repairPreviewError: "",
        repairPlanDraft: null,
        repairPlanLoading: false,
        repairPlanError: "",
      }));
      return;
    }
    setState(s => ({
      ...s,
      selectedImportRoute: route,
      previewGateNotice: "",
      repairPreview: route === "normal_text_layer" ? null : s.repairPreview,
      repairPreviewLoading: route === "normal_text_layer" ? false : s.repairPreviewLoading,
      repairPreviewError: route === "normal_text_layer" ? "" : s.repairPreviewError,
      repairPlanDraft: route === "normal_text_layer" ? null : s.repairPlanDraft,
      repairPlanLoading: route === "normal_text_layer" ? false : s.repairPlanLoading,
      repairPlanError: route === "normal_text_layer" ? "" : s.repairPlanError,
    }));
    if (route !== "ocr_layout_first_repair") return;
    if (!previewGate?.preview_token) {
      setState(s => ({ ...s, repairPreviewError: "当前预检结果缺少安全 PDF preview token，无法运行 OCR 修复预览。" }));
      return;
    }
    setState(s => ({
      ...s,
      repairPreviewLoading: true,
      repairPreviewError: "",
      repairPreview: null,
      repairPlanDraft: null,
      repairPlanLoading: false,
      repairPlanError: "",
    }));
    try {
      const result = await postJson("/api/v1/library/import/pdf/repair-preview/start", {
        preview_token: previewGate.preview_token,
        sample_pages: previewGate.sample_pages || [],
        max_pages: 2,
        device: "auto",
      });
      setState(s => ({ ...s, repairPreviewLoading: false, repairPreview: result }));
      updateSafety(result);
    } catch (e) {
      setState(s => ({
        ...s,
        repairPreviewLoading: false,
        repairPreviewError: `OCR 修复预览失败：${e.message}`,
      }));
    }
  }

  async function buildRepairPlanDraft() {
    if (!repairPreview) return;
    setState(s => ({
      ...s,
      repairPlanLoading: true,
      repairPlanError: "",
      repairPlanDraft: null,
    }));
    try {
      const result = await postJson("/api/v1/library/import/pdf/repair-preview/plan", {
        repair_preview_result: repairPreview,
      });
      setState(s => ({ ...s, repairPlanLoading: false, repairPlanDraft: result }));
      updateSafety(result);
    } catch (e) {
      setState(s => ({
        ...s,
        repairPlanLoading: false,
        repairPlanError: `修复计划草案生成失败：${e.message}`,
      }));
    }
  }

  useEffect(() => {
    if (!chapteredImportJobPolling || !chapteredImportJob?.job_id) return;
    const jobId = chapteredImportJob.job_id;
    let cancelled = false;
    const poll = async () => {
      try {
        const res = await getJson(`/api/v1/library/import/pdf/jobs/${jobId}`);
        if (cancelled) return;
        const job = res.job;
        const stillActive = job.status === "queued" || job.status === "running";
        setState(s => ({
          ...s,
          chapteredImportJob: job,
          chapteredImportJobPolling: stillActive,
          chapteredImportStatusError: "",
          chapteredImportStatusErrorCount: 0,
        }));
        updateSafety(res);
      } catch (e) {
        if (!cancelled) {
          setState(s => ({
            ...s,
            chapteredImportStatusError: `状态查询失败，可重试：${e.message}`,
            chapteredImportStatusErrorCount: (s.chapteredImportStatusErrorCount || 0) + 1,
            chapteredImportJobPolling: (s.chapteredImportStatusErrorCount || 0) + 1 < 5,
            chapteredImportJob: {
              ...s.chapteredImportJob,
              message: (s.chapteredImportStatusErrorCount || 0) + 1 < 5
                ? "状态查询失败，可重试。系统将继续轮询。"
                : "连续多次状态查询失败，请手动重试。",
            },
          }));
        }
      }
    };
    poll();
    const interval = setInterval(poll, 2000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [chapteredImportJobPolling, chapteredImportJob?.job_id, setState, updateSafety]);

  async function startChapteredImportJob() {
    if (!classification) return;
    if (previewGateLocksImport) {
      setState(s => ({
        ...s,
        chapteredImportJobError: "已选择导入预检路线；本阶段仅记录路线，不启动全书导入任务。",
      }));
      return;
    }
    const jobPayload = {
      ...buildClassifyPayload(),
      document_type: overrideDocumentType,
      object_import_mode: overrideObjectImportMode,
      backend: "marker_surya_page_blocks",
      worker_device: "auto",
      confirm_title: titleHint.trim() || classification.title,
      confirm_page_count: classification.signals?.page_count,
      confirm_chapter_count: chapteredPreview?.chapter_count || classification.signals?.outline_chapter_count,
      import_granularity: "chapter",
      confirm_write: true,
      confirmation_context: "import_full_book_after_preview",
    };
    setState(s => ({
      ...s,
      chapteredImportJobPolling: true,
      chapteredImportJob: null,
      chapteredImportJobError: "",
      chapteredImportStatusError: "",
      chapteredImportStatusErrorCount: 0,
      chapteredImportReusedMessage: "",
    }));
    try {
      const res = await postJson("/api/v1/library/import/pdf/chaptered/jobs", jobPayload);
      const job = res.job;
      if (job.reused_existing_job) {
        setState(s => ({
          ...s,
          chapteredImportJob: job,
          chapteredImportJobPolling: job.status === "queued" || job.status === "running",
          chapteredImportReusedMessage: "已有相同 PDF 导入任务正在进行，继续跟踪已有任务。",
        }));
      } else {
        setState(s => ({
          ...s,
          chapteredImportJob: job,
          chapteredImportJobPolling: true,
        }));
      }
      updateSafety(res);
    } catch (e) {
      setState(s => ({
        ...s,
        chapteredImportJobPolling: false,
        chapteredImportJobError: `创建导入任务失败：${e.message}`,
      }));
    }
  }

  async function cancelChapteredImportJob() {
    const jobId = chapteredImportJob?.job_id;
    if (!jobId) return;
    try {
      const res = await postJson(`/api/v1/library/import/pdf/jobs/${jobId}/cancel`, {});
      const job = res.job || res;
      setState(s => ({
        ...s,
        chapteredImportJob: job,
        chapteredImportJobPolling: job.status === "queued" || job.status === "running",
        chapteredImportCancelMessage: res.cancel_allowed ? "" : (res.cancel_message || "当前阶段不能安全取消。"),
      }));
      updateSafety(res);
    } catch (e) {
      setState(s => ({
        ...s,
        chapteredImportCancelMessage: `取消请求失败：${e.message}`,
      }));
    }
  }

  async function fetchChapteredPreview(activeClassification = classification) {
    if (!activeClassification) return null;
    setState(s => ({
      ...s,
      chapteredPreviewLoading: true,
      chapteredPreview: null,
      chapteredPreviewConfirmed: false,
      selectedChapterIndexes: [],
      chapteredPreviewError: "",
    }));
    try {
      const payload = {
        ...buildClassifyPayload(),
        document_type: activeClassification.document_type || overrideDocumentType,
        object_import_mode: "chaptered",
        backend: "marker_surya_page_blocks",
      };
      const res = await postJson("/api/v1/library/import/pdf/chaptered/preview", payload);
      setState(s => ({
        ...s,
        chapteredPreviewLoading: false,
        chapteredPreview: res,
        chapteredImportGranularity: res.recommended_import_granularity || "chapter",
        selectedChapterIndexes: [],
      }));
      updateSafety(res);
      return res;
    } catch (e) {
      setState(s => ({ ...s, chapteredPreviewLoading: false, chapteredPreviewError: `章节预览失败：${e.message}` }));
      return null;
    }
  }

  function _openImportedDocument() {
    const docId = chapteredImportJob?.result?.document_id
      || chapteredImportJob?.document_id;
    if (!docId) return;
    onOpenDocument?.(docId);
  }

  async function commitFullDocumentImport() {
    if (duplicateCheck?.duplicate_found) {
      const existingId = duplicateCheck.existing_documents?.[0]?.document_id;
      if (existingId) onOpenDocument?.(existingId);
      setState(s => ({
        ...s,
        fullDocumentCommitState: {
          status: "failed",
          stage: "duplicate-check",
          data: null,
          error: "检测到已导入文档。本页不会重复写入知识库，请先打开已有文档。",
        },
      }));
      return;
    }
    if (documentKindForImport !== "book" && documentKindForImport !== "paper") {
      const blockedResult = {
        ...NO_WRITE_SAFETY_FLAGS,
        message: FULL_DOCUMENT_COMMIT_UNAVAILABLE_MESSAGE,
        confirmation_context: deriveConfirmationContext(documentKindForImport),
        blocked_reason: "full_document_commit_backend_not_enabled",
      };
      setState(s => ({
        ...s,
        loading: false,
        fullDocumentCommitState: {
          status: "failed",
          stage: "full-document-commit-blocked",
          data: blockedResult,
          error: FULL_DOCUMENT_COMMIT_UNAVAILABLE_MESSAGE,
        },
      }));
      updateSafety(blockedResult);
      return;
    }
    if (!previewResult?.import_job_id) {
      if (!importReadiness.quality_good) {
        setState(s => ({
          ...s,
          fullDocumentCommitState: {
            status: "failed",
            stage: "preflight",
            data: null,
            error: "请先生成导入前预览。该步骤只读取 PDF / 生成 staging 文件，不写入知识库。",
          },
        }));
        return;
      }
    }
    if (fullDocumentImportBlockedReason) {
      setState(s => ({
        ...s,
        fullDocumentCommitState: {
          status: "failed",
          stage: "preflight",
          data: null,
          error: fullDocumentImportBlockedReason,
        },
      }));
      return;
    }
    let preview = previewResult;
    try {
      if (!preview?.import_job_id && importReadiness.quality_good) {
        setState(s => ({
          ...s,
          fullDocumentCommitState: { status: "previewing", stage: "preview-only", data: null, error: "" },
        }));
        preview = await createPreview();
        if (!preview?.import_job_id) {
          setState(s => ({
            ...s,
            fullDocumentCommitState: {
              status: "failed",
              stage: "preview-only",
              data: null,
              error: "质量预检通过，但 staging preview 创建失败；未写入知识库。",
            },
          }));
          return;
        }
      }
      const commitRoute = documentKindForImport === "book" ? "/commit-book" : "/commit-paper";
      const commitStage = documentKindForImport === "book" ? "commit-book" : "commit-paper";
      setState(s => ({
        ...s,
        fullDocumentCommitState: { status: "committing", stage: commitStage, data: null, error: "" },
      }));

      const result = await postJson(`/api/v1/imports/${preview.import_job_id}${commitRoute}`, {
        confirm_write: true,
        confirmation_context: deriveConfirmationContext(documentKindForImport),
      });
      setState(s => ({
        ...s,
        loading: false,
        fullDocumentCommitState: {
          status: result.status === "already_committed" ? "already_committed" : "success",
          stage: commitStage,
          data: result,
          error: "",
        },
      }));
      updateSafety(result);
    } catch (e) {
      const stage = documentKindForImport === "book" ? "commit-book" : "commit-paper";
      const blockedPayload = e.payload || null;
      if (blockedPayload) updateSafety(blockedPayload);
      setState(s => ({
        ...s,
        loading: false,
        fullDocumentCommitState: {
          status: "failed",
          stage,
          data: blockedPayload,
          error: `入库失败：${e.message}`,
        },
      }));
    }
  }

  async function generateBundle() {
    const jid = previewResult?.import_job_id;
    if (!jid) return;
    setState(s => ({ ...s, bundleLoading: true, bundleError: "", bundleResult: null }));
    try {
      const result = await postJson(`/api/v1/imports/${jid}/chatgpt-object-tag-input`, {});
      setState(s => ({ ...s, bundleLoading: false, bundleResult: result }));
      updateSafety(result);
    } catch (e) {
      setState(s => ({ ...s, bundleLoading: false, bundleError: `生成失败：${e.message}` }));
    }
  }

  async function loadBundleContent() {
    const jid = previewResult?.import_job_id;
    if (!jid) return;
    setState(s => ({ ...s, bundleContentLoading: true, bundleContent: null }));
    try {
      const result = await getJson(`/api/v1/imports/${jid}/chatgpt-object-tag-input`);
      setState(s => ({ ...s, bundleContentLoading: false, bundleContent: result }));
    } catch (e) {
      setState(s => ({ ...s, bundleContentLoading: false, bundleContent: { error: e.message } }));
    }
  }

  function copyBundlePath() {
    if (bundleResult?.bundle_path) {
      navigator.clipboard.writeText(bundleResult.bundle_path).catch(() => {});
    }
  }

  function copyBundleContent() {
    if (bundleContent?.bundle_content) {
      navigator.clipboard.writeText(bundleContent.bundle_content).catch(() => {});
    }
  }

  function goToImportReview() {
    const jid = previewResult?.import_job_id;
    if (jid) {
      onAutoFillJobId(jid);
      onNavigate("importReview");
    }
  }

  async function refreshZoteroSnapshot() {
    setState(s => ({ ...s, zoteroLoading: true, zoteroError: "", zoteroRefreshResult: null }));
    try {
      const result = await postJson("/api/v1/zotero/refresh-snapshot", {});
      setState(s => ({ ...s, zoteroLoading: false, zoteroRefreshResult: result }));
      updateSafety(result);
    } catch (e) {
      setState(s => ({ ...s, zoteroLoading: false, zoteroError: `刷新失败：${e.message}` }));
    }
  }

  async function syncZoteroSources() {
    setState(s => ({ ...s, zoteroLoading: true, zoteroError: "", zoteroSyncResult: null }));
    try {
      const result = await postJson("/api/v1/zotero/sync-pdf-sources", {});
      setState(s => ({ ...s, zoteroLoading: false, zoteroSyncResult: result }));
      updateSafety(result);
      await searchZoteroSources();
    } catch (e) {
      setState(s => ({ ...s, zoteroLoading: false, zoteroError: `同步失败：${e.message}` }));
    }
  }

  async function searchZoteroSources(event, queryOverride = null) {
    event?.preventDefault();
    const submittedQuery = queryOverride == null ? zoteroQuery.trim() : String(queryOverride || "").trim();
    setState(s => ({ ...s, zoteroLoading: true, zoteroError: "" }));
    try {
      const params = new URLSearchParams();
      if (submittedQuery) params.set("q", submittedQuery);
      if (zoteroStatus) params.set("status", zoteroStatus);
      const result = await getJson(zoteroSourcesPath(params));
      setState(s => ({
        ...s,
        zoteroLoading: false,
        zoteroSources: result.items || [],
        zoteroSearchSubmittedQuery: submittedQuery,
      }));
      updateSafety(result);
    } catch (e) {
      setState(s => ({ ...s, zoteroLoading: false, zoteroError: `查询失败：${e.message}` }));
    }
  }

  function selectZoteroSource(source) {
    onSelectZoteroSource?.(source);
    setState(s => resetForNewImportSource(s, {
      sourceMode: "zotero_pdf",
      selectedZoteroSource: source,
      pdfPath: source.resolved_pdf_path || "",
      titleHint: titleFromZoteroSource(source),
      pdfSelectionStage: "selected",
    }));
  }

  function zoteroImportStatusLabel(source = {}) {
    return zoteroPdfImportStatus(source).label || cacheStatusLabel(source.cache_status || "unknown");
  }

  function resetZoteroSelection() {
    setState(s => resetForNewImportSource(s, {
      pdfSelectionStage: "browse",
      selectedZoteroSource: null,
      pdfPath: "",
      titleHint: "",
    }));
  }

  function buildPreviewPayload() {
    return createPreviewPayload({
      sourceMode,
      selectedImportRoute,
      importReadiness,
      pdfPath,
      convertedMdPath,
      titleHint,
      selectedZoteroSource,
    });
  }

  function buildClassifyPayload() {
    return createClassifyPayload({
      sourceMode,
      selectedZoteroSource,
      pdfPath,
    });
  }

  function buildPreviewGatePayload() {
    return createPreviewGatePayload({
      sourceMode,
      selectedZoteroSource,
      pdfPath,
    });
  }

  function zoteroSourcesPath(params) {
    const query = params.toString();
    return `/api/v1/zotero/pdf-sources${query ? `?${query}` : ""}`;
  }

  return (
    <ImportLinearWizard
      state={state}
      setState={setState}
      sourceMode={sourceMode}
      pdfPath={pdfPath}
      titleHint={titleHint}
      sourceTitle={sourceTitle}
      zoteroStatus={zoteroStatus}
      zoteroQuery={zoteroQuery}
      zoteroSources={zoteroSources}
      selectedZoteroSource={selectedZoteroSource}
      zoteroLoading={zoteroLoading}
      zoteroError={zoteroError}
      showZoteroBrowse={showZoteroBrowse}
      classification={classification}
      classifyLoading={classifyLoading}
      classifyError={classifyError}
      previewResult={previewResult}
      previewError={previewError}
      preImportPreviewLoading={preImportPreviewLoading}
      duplicateCheck={duplicateCheck}
      duplicateCheckLoading={duplicateCheckLoading}
      duplicateCheckError={duplicateCheckError}
      textLayerPreview={textLayerPreview}
      previewGate={previewGate}
      pdfBackendUnavailable={pdfBackendUnavailable}
      chapteredPreview={chapteredPreview}
      chapteredPreviewLoading={chapteredPreviewLoading}
      chapteredImportJob={chapteredImportJob}
      importingChaptered={importingChaptered}
      fullDocumentCommitState={fullDocumentCommitState}
      fullDocumentImportRunning={fullDocumentImportRunning}
      convertedMdPath={convertedMdPath}
      markdownConversionState={markdownConversionState}
      importReadiness={importReadiness}
      documentKindForImport={documentKindForImport}
      previewWorkspaceTab={previewWorkspaceTab}
      setPreviewWorkspaceTab={setPreviewWorkspaceTab}
      previewWorkspaceExpanded={previewWorkspaceExpanded}
      setPreviewWorkspaceExpanded={setPreviewWorkspaceExpanded}
      updateSafety={updateSafety}
      onNavigate={onNavigate}
      onOpenDocument={onOpenDocument}
      onSelectZoteroSource={onSelectZoteroSource}
      resetZoteroSelection={resetZoteroSelection}
      resetForNewImportSource={resetForNewImportSource}
      classifyPdf={classifyPdf}
      generatePreImportPreview={generatePreImportPreview}
      fetchPreviewGate={fetchPreviewGate}
      fetchChapteredPreview={fetchChapteredPreview}
      commitFullDocumentImport={commitFullDocumentImport}
      startChapteredImportJob={startChapteredImportJob}
      cancelChapteredImportJob={cancelChapteredImportJob}
      selectZoteroSource={selectZoteroSource}
      searchZoteroSources={searchZoteroSources}
      viewPreviewWorkspace={viewPreviewWorkspace}
      generateMarkdownForCurrentPdf={generateMarkdownForCurrentPdf}
    />
  );

  return (
    <section className="importPreviewPage">
      <div className="importReviewBanner">
        <p><strong>统一 PDF 导入会先自动识别文献类型和详情页处理单元。</strong></p>
        <p>普通论文走整篇导入预览；书籍走整本书入库，入库后在详情页按章处理笔记、对象和机制。</p>
        <p><strong>同时导入 Zotero 原生笔记（论文路径）。</strong> 只读取 Zotero annotation notes；不写 Zotero 原库，不调用 LLM。书籍整本入库默认不自动把 Zotero notes 写入 Search。</p>
        <p>用户确认前不会导入章节正文；系统不会自动生成对象或调用 LLM。</p>
      </div>

      <div className="importPreviewForm">
        <h3>导入 PDF</h3>

        {/* ── Zotero selected focus state ── */}
        {isZoteroSelected && (
          <div className="zoteroSelectedBanner">
            <strong>已选择 Zotero PDF</strong>
            <p className="zoteroSelectedTitle">{selectedZoteroSource.title}</p>
            <code>{selectedZoteroSource.resolved_pdf_path}</code>
            <p className="zoteroSelectedGuide">
              已选择 PDF。如需更换，请点击 <strong>「重新选择 PDF」</strong>。
            </p>
            <p className="safetyNote">
              {selectedZoteroSource.zotero_attachment_key
                ? "将随导入读取该 attachment 的 Zotero 原生 annotation notes；不写 Zotero 原库，不调用 LLM。"
                : "未发现 Zotero attachment，跳过笔记同步。"}
            </p>
            <div className="previewActions" style={{ marginTop: 6 }}>
              <button type="button" onClick={resetZoteroSelection} disabled={importingChaptered}>重新选择 PDF</button>
            </div>
          </div>
        )}

        <div className="sourceModeTabs" role="tablist" aria-label="导入来源">
          <button type="button" className={sourceMode === "local_pdf" ? "active" : ""} onClick={() => setState(s => resetForNewImportSource(s, { sourceMode: "local_pdf", selectedZoteroSource: null, pdfSelectionStage: "browse", pdfPath: "", titleHint: "" }))} disabled={importingChaptered}>
            本地 PDF 路径
          </button>
          <button type="button" className={sourceMode === "zotero_pdf" ? "active" : ""} onClick={() => setState(s => resetForNewImportSource(s, { sourceMode: "zotero_pdf", pdfSelectionStage: "browse", selectedZoteroSource: null, pdfPath: "", titleHint: "" }))} disabled={importingChaptered}>
            Zotero PDF 缓存
          </button>
          <button type="button" className={sourceMode === "converted_md" ? "active" : ""} onClick={() => setState(s => resetForNewImportSource(s, { sourceMode: "converted_md", pdfSelectionStage: "browse", selectedZoteroSource: null, pdfPath: s.convertedMdPath || "", convertedMdPath: s.convertedMdPath || "", selectedImportRoute: "converted_md" }))} disabled={importingChaptered}>
            已转换 Markdown
          </button>
        </div>

        {/* ── Zotero browse: search + list (hidden when selected) ── */}
        {showZoteroBrowse && (
          <section className="zoteroSourcePanel">
            <div className="previewActions">
              <button type="button" onClick={refreshZoteroSnapshot} disabled={zoteroLoading}>刷新 Zotero 快照</button>
              <button type="button" onClick={syncZoteroSources} disabled={zoteroLoading}>同步 Zotero PDF 缓存</button>
            </div>
            <form className="zoteroSearchForm" onSubmit={searchZoteroSources}>
              <input value={zoteroQuery} onChange={e => setState(s => ({ ...s, zoteroQuery: e.target.value }))} placeholder="搜索 title / author / year，例如 EDSR 或 SENet" />
              <select value={zoteroStatus} onChange={e => setState(s => ({ ...s, zoteroStatus: e.target.value }))}>
                <option value="available">可用</option>
                <option value="missing">不可用</option>
                <option value="duplicate">可能重复</option>
                <option value="">全部</option>
              </select>
              <button type="submit" disabled={zoteroLoading}>{zoteroLoading ? "处理中..." : "搜索缓存"}</button>
            </form>
            {zoteroError && <StateMessage title="Zotero 缓存暂不可用" body={zoteroError} />}
            {(state.zoteroRefreshResult || state.zoteroSyncResult) && (
              <div className="zoteroSyncSummary">
                {state.zoteroRefreshResult && <span>snapshot: {state.zoteroRefreshResult.status}</span>}
                {state.zoteroSyncResult && <span>PDF 来源：{state.zoteroSyncResult.source_count} · 可用 {state.zoteroSyncResult.available_count} · 缺失 {state.zoteroSyncResult.missing_count}</span>}
              </div>
            )}
            <div className="zoteroCandidateList">
              {zoteroSources.map(source => (
                <article key={source.id} className={`zoteroCandidateCard ${selectedZoteroSource?.id === source.id ? "selected" : ""}`}>
                  <div className="cardMeta">
                    <span>{source.cache_status}</span>
                    {source.year && <span>{source.year}</span>}
                    <span className={`zoteroImportStatusBadge ${source.import_status || "not_imported"}`}>{zoteroImportStatusLabel(source)}</span>
                  </div>
                  <h3>{source.title || "Untitled Zotero PDF"}</h3>
                  <p>{(source.creators || []).slice(0, 4).join(", ") || "无作者信息"}</p>
                  <div className="zoteroKeyLine">
                    <code>item {source.zotero_item_key || "n/a"}</code>
                    <code>attachment {source.zotero_attachment_key}</code>
                  </div>
                  {source.existing_documents?.length > 0 && (
                    <div className="zoteroImportedSummary">
                      {source.existing_documents.slice(0, 2).map(document => (
                        <span key={document.document_id}>
                          doc {document.document_id} · {document.document_type} · chunks {document.chunk_count}
                          {document.chapter_count ? ` · chapters ${document.chapter_count}` : ""}
                        </span>
                      ))}
                    </div>
                  )}
                  <div className="previewActions">
                    {source.import_status === "imported" || (source.imported && source.primary_document_id) ? (
                      <button type="button" className="primaryButton" onClick={() => onOpenDocument?.(source.primary_document_id || source.linked_document_id)}>打开已有文档</button>
                    ) : source.import_status === "partially_imported" ? (
                      <>
                        <button type="button" className="primaryButton" onClick={() => selectZoteroSource(source)} disabled={!source.path_exists}>继续导入章节</button>
                        <button type="button" className="quietButton" onClick={() => onOpenDocument?.(source.primary_document_id || source.linked_document_id)}>打开已有文档</button>
                      </>
                    ) : source.import_status === "missing_file" || !source.path_exists ? (
                      <button type="button" disabled>缺失路径</button>
                    ) : (
                      <button type="button" onClick={() => selectZoteroSource(source)}>选择此 PDF</button>
                    )}
                    {source.imported && (
                      <button type="button" className="quietButton" onClick={() => selectZoteroSource(source)} disabled={!source.path_exists}>高级：重新导入 / 替换</button>
                    )}
                    {source.zotero_select_uri && <a className="pdfAction" href={source.zotero_select_uri}>在 Zotero 中打开</a>}
                    {source.zotero_open_pdf_uri && <a className="pdfAction" href={source.zotero_open_pdf_uri}>打开 PDF 文件</a>}
                  </div>
                </article>
              ))}
            </div>
            {selectedZoteroSource && (
              <div className="zoteroSelectedTrace">
                <strong>已选择 Zotero PDF</strong>
                <span>{selectedZoteroSource.title}</span>
                <code>{selectedZoteroSource.resolved_pdf_path}</code>
              </div>
            )}
          </section>
        )}

        <div className="formField">
          <label htmlFor="pdfPathInput">{sourceMode === "converted_md" ? "Markdown 路径" : "PDF 路径"}</label>
          <input
            id="pdfPathInput"
            value={pdfPath}
            onChange={e => {
              const nextPath = e.target.value;
              setState(s => resetForNewImportSource(s, {
                pdfPath: nextPath,
                titleHint: inferTitleFromPath(nextPath),
                selectedZoteroSource: null,
                pdfSelectionStage: "browse",
              }));
            }}
            placeholder={sourceMode === "converted_md" ? "选择 converted Markdown 文件路径" : "选择本地 PDF 文件路径"}
            aria-label={sourceMode === "converted_md" ? "Markdown 文件路径" : "PDF 文件路径"}
            readOnly={sourceMode === "zotero_pdf"}
          />
        </div>
        <div className="formField">
          <label htmlFor="titleHintInput">标题提示（可选）</label>
          <input
            id="titleHintInput"
            value={titleHint}
            onChange={e => setState(s => ({ ...s, titleHint: e.target.value }))}
            placeholder="留空则从 PDF 文件名自动提取"
            aria-label="标题提示"
          />
        </div>
        <SourceSummary
          sourceMode={sourceMode}
          title={sourceTitle}
          selectedZoteroSource={selectedZoteroSource}
          pdfPath={pdfPath}
          duplicateCheck={duplicateCheck}
          duplicateCheckLoading={duplicateCheckLoading}
          duplicateCheckError={duplicateCheckError}
          classification={classification}
          previewGate={previewGate}
          pdfBackendUnavailable={pdfBackendUnavailable}
          convertedMdPath={convertedMdPath}
          convertedMdIdentity={convertedMdIdentity}
          importReadiness={importReadiness}
        />

        <MainImportDecisionPanel
          importReadiness={importReadiness}
          running={fullDocumentImportRunning || markdownConversionRunning || preImportPreviewLoading || loading}
          markdownConversionState={markdownConversionState}
          commitState={fullDocumentCommitState}
          onPrimaryAction={commitFullDocumentImport}
          onPreviewAction={generatePreImportPreview}
          onChapterPreview={() => fetchChapteredPreview()}
          onChapterImport={startChapteredImportJob}
          onGenerateMarkdown={generateMarkdownForCurrentPdf}
          onOpenDocument={onOpenDocument}
          onViewPreview={viewPreviewWorkspace}
          previewResult={previewResult}
          commitResult={fullDocumentCommitResult}
          duplicateCheck={duplicateCheck}
          duplicateCheckLoading={duplicateCheckLoading}
          duplicateCheckError={duplicateCheckError}
          previewLoading={preImportPreviewLoading || loading || classifyLoading || previewGateLoading || chapteredPreviewLoading || textLayerPreviewLoading || duplicateCheckLoading}
        />

        {fullDocumentImportSucceeded && isEspcnTitle(fullDocumentCommitResult?.title || classification?.title || selectedZoteroSource?.title || titleHint) && (
          <div className="seedCommandHint">
            <strong>ESPCN seed 可执行</strong>
            <code>{ESPCN_SEED_APPLY_COMMAND}</code>
            <span>前端不会自动执行 seed；请先 dry-run，再按验收计划 apply。</span>
          </div>
        )}
      </div>

      <PreviewWorkspace
        activeTab={previewWorkspaceTab}
        onTabChange={setPreviewWorkspaceTab}
        expanded={previewWorkspaceExpanded}
        onToggleExpanded={() => setPreviewWorkspaceExpanded(value => !value)}
        previewGate={previewGate}
        previewResult={previewResult}
        textLayerPreview={textLayerPreview}
        textLayerPreviewLoading={textLayerPreviewLoading}
        textLayerPreviewError={textLayerPreviewError}
        pdfBackendUnavailable={pdfBackendUnavailable}
        convertedMdPath={convertedMdPath}
        convertedMdIdentity={convertedMdIdentity}
        importReadiness={importReadiness}
        markdownConversionState={markdownConversionState}
        onGeneratePreview={generatePreImportPreview}
        onGenerateMarkdown={generateMarkdownForCurrentPdf}
      />

      <AdvancedDiagnostics
        importReadiness={importReadiness}
        pdfBackendUnavailable={pdfBackendUnavailable}
        previewResult={previewResult}
        classification={classification}
        previewGate={previewGate}
        duplicateCheck={duplicateCheck}
        textLayerPreview={textLayerPreview}
        markdownConversionState={markdownConversionState}
        buildPreviewQualitySummary={buildPreviewQualitySummary}
      />
      {classifyError && <StateMessage title="自动识别失败" body={classifyError} />}
      {previewGateError && <StateMessage title="首节质量预览暂不可用" body={previewGateError} />}
      {pdfBackendUnavailable && (
        <PdfBackendFallbackPanel
          payload={pdfBackendUnavailable}
          convertedMdPath={convertedMdPath}
          convertedMdIdentity={convertedMdIdentity}
          onUseConvertedMd={activateConvertedMdImport}
        />
      )}
      {state.previewGateNotice && !previewGate && (
        <div className="importReviewBanner"><p>{state.previewGateNotice}</p></div>
      )}
      {(repairPreviewLoading || repairPreviewError || repairPreview) && (
        <RepairPreviewPanel
          result={repairPreview}
          loading={repairPreviewLoading}
          error={repairPreviewError}
          pdfPreviewUrl={previewGate?.pdf_preview_url || ""}
          planResult={repairPlanDraft}
          planLoading={repairPlanLoading}
          planError={repairPlanError}
          onBuildPlan={buildRepairPlanDraft}
          normalImportRecommended={previewGate?.recommended_route === "normal_text_layer"}
          onBackToNormal={() => selectImportRoute("normal_text_layer")}
          onCancelReplace={() => selectImportRoute("cancel_or_replace_pdf")}
        />
      )}

      {classification && (
        <section className="pdfClassificationPanel">
          <div className="sectionHeader">
            <h3>识别结果</h3>
            <span>{confidenceLabel(classification.confidence)}</span>
          </div>
          <div className="previewResultGrid">
            <PreviewField label="标题" value={classification.title} />
            <PreviewField label="页数" value={classification.signals?.page_count} />
            <PreviewField label="Zotero itemType" value={classification.signals?.zotero_item_type || "无"} />
            <PreviewField label="outline 主章数" value={classification.signals?.outline_chapter_count} />
            <PreviewField label="系统文献类型" value={documentTypeLabel(classification.document_type)} />
            <PreviewField label="系统导入方式" value={importModeLabel(classification.object_import_mode)} />
          </div>
          <div className="classificationOverrideGrid">
            <label className="formField">
              <span>文献类型</span>
              <select
                value={overrideDocumentType}
                onChange={e => setState(s => ({
                  ...s,
                  overrideDocumentType: e.target.value,
                  overrideObjectImportMode: e.target.value === "book" ? "chaptered" : s.overrideObjectImportMode,
                  previewResult: null,
                  fullDocumentCommitState: IDLE_FULL_DOCUMENT_COMMIT_STATE,
                  chapteredPreview: null,
                  chapteredPreviewConfirmed: false,
                  selectedChapterIndexes: [],
                }))}
                disabled={importingChaptered}
              >
                <option value="paper">论文</option>
                <option value="book">书籍</option>
                <option value="thesis">学位论文</option>
                <option value="report">报告</option>
                <option value="other">其他</option>
              </select>
            </label>
            <label className="formField">
              <span>详情页处理单元</span>
              <select
                value={overrideDocumentType === "book" ? "chaptered" : overrideObjectImportMode}
                onChange={e => setState(s => ({
                  ...s,
                  overrideObjectImportMode: e.target.value,
                  previewResult: null,
                  fullDocumentCommitState: IDLE_FULL_DOCUMENT_COMMIT_STATE,
                  chapteredPreview: null,
                  chapteredPreviewConfirmed: false,
                  selectedChapterIndexes: [],
                }))}
                disabled={importingChaptered || overrideDocumentType === "book"}
              >
                <option value="full_document">整篇导入</option>
                <option value="chaptered">入库后按章/节处理</option>
              </select>
            </label>
          </div>
          <div className="classificationReasons">
            {(classification.reasons || []).map(reason => <span key={reason}>{reason}</span>)}
            {classification.requires_user_confirmation && <span>需要用户确认</span>}
          </div>
          {classification.duplicate ? (
            <div className="duplicateImportNotice">
              <strong>该 PDF 已导入</strong>
              <span>document_id={classification.existing_document_id} · {documentTypeLabel(classification.existing_document_type)} · {importModeLabel(classification.existing_object_import_mode)}</span>
              <div className="previewActions">
                <button type="button" className="primaryButton" onClick={() => onNavigate?.("readShelf")}>打开详情请从资料库选择该文档</button>
                {classification.existing_object_import_mode === "chaptered" && <span>可在详情页按双源流程处理笔记、原文片段与对象</span>}
              </div>
            </div>
          ) : overrideObjectImportMode === "chaptered" ? (
            <div>
              {/* ── Preview step ── */}
              {!chapteredPreview && !chapteredPreviewLoading && (
                <div className="previewActions" style={{ marginBottom: 10 }}>
                  <button type="button" className="primaryButton" onClick={() => fetchChapteredPreview()} disabled={previewGateLocksImport}>
                    生成章节预览
                  </button>
                  <p className="safetyNote" style={{ margin: "4px 0 0" }}>导入前请先生成章节预览。该步骤只读取 PDF 目录/书签，不写入知识库。</p>
                </div>
              )}
              {chapteredPreviewLoading && (
                <StateMessage title="正在加载章节预览..." body="正在解析 PDF 与识别章节结构，请稍候。" />
              )}
              {state.chapteredPreviewError && (
                <StateMessage title="章节预览失败" body={state.chapteredPreviewError} />
              )}

              {/* ── Preview result ── */}
              {chapteredPreview && (
                <div className="chapteredPreviewPanel" style={{ display: "grid", gap: 10, padding: 12, border: "1px solid var(--border-soft)", borderRadius: "var(--radius-md)", background: "var(--surface-2)", marginBottom: 10 }}>
                  <div className="sectionHeader">
                    <h3>章节预览</h3>
                    <span>{chapteredPreview.detection_method || "unknown"}</span>
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 16px", color: "var(--text-muted)", fontSize: 12 }}>
                    <span>章节数：{chapteredPreview.chapter_count}</span>
                    {chapteredPreview.estimated_chunk_count != null && (
                      <span>预估 chunk 数：{chapteredPreview.estimated_chunk_count}</span>
                    )}
                    {chapteredPreview.chunk_binding_rate != null && (
                      <span>chunk 绑定率：{(chapteredPreview.chunk_binding_rate * 100).toFixed(0)}%</span>
                    )}
                  </div>
                  {chapteredPreview.preview_is_outline_only && (
                    <div style={{ padding: "6px 10px", border: "1px solid var(--accent)", borderRadius: "var(--radius-sm)", background: "var(--accent-muted)", color: "var(--accent-strong)", fontSize: 11 }}>
                      当前为 PDF 目录/书签预览；正文将在确认导入后由后台任务解析。
                    </div>
                  )}
                  {chapteredPreview.detection_method === "outline_unavailable" && (
                    <div style={{ padding: "6px 10px", border: "1px solid var(--warning)", borderRadius: "var(--radius-sm)", background: "rgba(179,146,86,0.06)", color: "var(--text-muted)", fontSize: 11 }}>
                      未检测到 PDF 书签目录，暂不能快速预览章节结构。
                    </div>
                  )}
                  {isChapterSafetyBlocked(chapteredPreview) && (
                    <div style={{ padding: "6px 10px", border: "1px solid var(--danger)", borderRadius: "var(--radius-sm)", background: "rgba(201,112,112,0.06)", color: "var(--text-muted)", fontSize: 11 }}>
                      章节安全检查阻断导入：{chapterSafetyBlockerText(chapteredPreview)}
                    </div>
                  )}
                  {hasChapterSafetyWarnings(chapteredPreview) && !isChapterSafetyBlocked(chapteredPreview) && (
                    <div style={{ padding: "6px 10px", border: "1px solid var(--warning)", borderRadius: "var(--radius-sm)", background: "rgba(179,146,86,0.06)", color: "var(--text-muted)", fontSize: 11 }}>
                      检测到部分章节标题相似，但章节编号和页码范围正常，允许导入。后续对象和机制仍按章节处理。
                    </div>
                  )}
                  {chapteredPreview.chapter_count > 80 && !isChapterSafetyBlocked(chapteredPreview) && (
                    <div style={{ padding: "6px 10px", border: "1px solid var(--warning)", borderRadius: "var(--radius-sm)", background: "rgba(179,146,86,0.06)", color: "var(--text-muted)", fontSize: 11 }}>
                      章节数量较多，可能包含小节。请确认这是你想要的粒度。
                    </div>
                  )}
                  {(chapteredPreview.warnings || []).length > 0 && (
                    <div style={{ padding: "6px 10px", border: "1px solid var(--border-soft)", borderRadius: "var(--radius-sm)", background: "var(--bg)", color: "var(--text-muted)", fontSize: 11 }}>
                      {(chapteredPreview.warnings || []).map((w, i) => <div key={i}>{w}</div>)}
                    </div>
                  )}
                  <div className="previewActions">
                    <button
                      type="button"
                      onClick={() => setState(s => ({
                        ...s,
                        selectedChapterIndexes: (chapteredPreview.accepted_chapters || []).map(c => c.chapter_index),
                        chapteredPreviewConfirmed: false,
                      }))}
                    >
                      查看当前章节摘要
                    </button>
                    <button
                      type="button"
                      onClick={() => setState(s => ({ ...s, selectedChapterIndexes: [], chapteredPreviewConfirmed: false }))}
                    >
                      重置章节摘要
                    </button>
                    <span className="safetyNote">已选择 {selectedChapterIndexes.length} 个章节</span>
                  </div>
                  <div style={{ maxHeight: 280, overflow: "auto", border: "1px solid var(--border-soft)", borderRadius: "var(--radius-sm)" }}>
                    {(chapteredPreview.accepted_chapters || []).map(c => (
                      <label key={c.chapter_index} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, padding: "4px 10px", borderBottom: "1px solid var(--border-soft)", fontSize: 12, color: "var(--text-muted)", cursor: "pointer" }}>
                        <input
                          type="checkbox"
                          checked={selectedChapterIndexes.includes(c.chapter_index)}
                          onChange={e => setState(s => {
                            const current = new Set(s.selectedChapterIndexes || []);
                            if (e.target.checked) {
                              current.add(c.chapter_index);
                            } else {
                              current.delete(c.chapter_index);
                            }
                            return {
                              ...s,
                              selectedChapterIndexes: Array.from(current).sort((a, b) => a - b),
                              chapteredPreviewConfirmed: false,
                            };
                          })}
                          disabled={isChapterSafetyBlocked(chapteredPreview) || chapteredPreview.detection_method === "outline_unavailable"}
                        />
                        <span style={{ flex: "0 0 auto", color: "var(--text-subtle)", minWidth: 30 }}>{c.chapter_index}.</span>
                        <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.title}</span>
                        <span style={{ flex: "0 0 auto", color: "var(--text-subtle)", fontSize: 11 }}>{c.pdf_page_start ? `p.${c.pdf_page_start}` : ""}{c.pdf_page_end ? `–${c.pdf_page_end}` : ""}</span>
                      </label>
                    ))}
                  </div>
                  {chapteredPreview.truncated_chapters > 0 && (
                    <p className="safetyNote">还有 {chapteredPreview.truncated_chapters} 个章节未显示。</p>
                  )}

                  {/* ── Granularity selector ── */}
                  {(chapteredPreview.import_granularity_options || []).length > 0 && (
                    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                      <span style={{ color: "var(--text-muted)", fontSize: 12 }}>导入粒度：</span>
                      <select
                        value={chapteredImportGranularity}
                        onChange={e => setState(s => ({ ...s, chapteredImportGranularity: e.target.value }))}
                        style={{ minHeight: 30, padding: "0 8px", border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", background: "var(--bg)", color: "var(--text)", fontSize: 12 }}
                      >
                        {(chapteredPreview.import_granularity_options || []).map(opt => (
                          <option key={opt.value} value={opt.value}>
                            {opt.label}{opt.recommended ? "（推荐）" : ""} — {opt.description}
                          </option>
                        ))}
                      </select>
                    </div>
                  )}

                  <p style={{ margin: 0, color: "var(--text-muted)", fontSize: 12 }}>
                    将导入 {selectedChapterIndexes.length} 个所选章节
                    {chapteredImportGranularity !== chapteredPreview.recommended_import_granularity && "（非推荐粒度）"}。
                    正文导入将按后端章节检测执行。
                  </p>

                  <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, color: "var(--text)", cursor: "pointer" }}>
                    <input type="checkbox" checked={chapteredPreviewConfirmed} onChange={e => setState(s => ({ ...s, chapteredPreviewConfirmed: e.target.checked }))} disabled={!selectedChapterIndexes.length || isChapterSafetyBlocked(chapteredPreview) || chapteredPreview.detection_method === "outline_unavailable" || !chapteredPreview.auto_apply_eligible} />
                    我已确认章节摘要，继续整本入库
                  </label>
                </div>
              )}

              {/* ── Import button (only if confirmed) ── */}
              {chapteredPreviewConfirmed && chapteredPreview && !isChapterSafetyBlocked(chapteredPreview) && (
                <div className="previewActions">
                  <button type="button" className="primaryButton" onClick={startChapteredImportJob} disabled={importingChaptered || previewGateLocksImport}>
                    {importingChaptered ? "导入中..." : "导入整本书到知识库"}
                  </button>
                  {(chapteredImportJob?.status === "queued" || chapteredImportJob?.status === "running") && (
                    <button type="button" onClick={cancelChapteredImportJob}>取消导入</button>
                  )}
                </div>
              )}
              {state.chapteredImportReusedMessage && (
                <p style={{ margin: "4px 0 0", color: "var(--text-muted)", fontSize: 12 }}>{state.chapteredImportReusedMessage}</p>
              )}
              {state.chapteredImportCancelMessage && (
                <p style={{ margin: "4px 0 0", color: "var(--warning)", fontSize: 12 }}>{state.chapteredImportCancelMessage}</p>
              )}
              {state.chapteredImportStatusError && (
                <p style={{ margin: "4px 0 0", color: "var(--warning)", fontSize: 12 }}>{state.chapteredImportStatusError}</p>
              )}
              {state.chapteredImportJobError && <StateMessage title="创建任务失败" body={state.chapteredImportJobError} />}
              {chapteredImportJob && (
                <div className="chapteredImportProgressCard" style={{ marginTop: 12, padding: 14, border: "1px solid var(--border-soft)", borderRadius: "var(--radius-md)", background: "var(--surface-2)", display: "grid", gap: 10 }}>
                  <div className="sectionHeader">
                    <h3>章节化导入进度</h3>
                    <span>{stageLabel(chapteredImportJob.stage)}</span>
                  </div>
                  <div style={{ width: "100%", height: 8, borderRadius: 4, background: "var(--bg)", overflow: "hidden" }}>
                    <div style={{ width: `${chapteredImportJob.progress_percent || 0}%`, height: "100%", borderRadius: 4, background: chapteredImportJob.status === "failed" ? "var(--danger)" : chapteredImportJob.status === "cancelled" ? "var(--text-subtle)" : "var(--accent)", transition: "width 0.5s ease" }} />
                  </div>
                  <p style={{ margin: 0, color: "var(--text-muted)", fontSize: 12 }}>
                    {chapteredImportJob.progress_percent || 0}% · {chapteredImportJob.message || ""}
                  </p>
                  {chapteredImportJob.current_unit_title && (
                    <p style={{ margin: 0, color: "var(--text-muted)", fontSize: 12 }}>
                      当前章节：{chapteredImportJob.current_unit_index || "?"}/{chapteredImportJob.total_units || "?"} · {chapteredImportJob.current_unit_title}
                      {chapteredImportJob.current_page_start && ` (p.${chapteredImportJob.current_page_start}-${chapteredImportJob.current_page_end || chapteredImportJob.current_page_start})`}
                    </p>
                  )}
                  {(chapteredImportJob.parser_backend || chapteredImportJob.parser_device || chapteredImportJob.runtime) && (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 12px", color: "var(--text-subtle)", fontSize: 11 }}>
                      {chapteredImportJob.runtime?.python_executable && (
                        <span>Python：{basename(chapteredImportJob.runtime.python_executable)}</span>
                      )}
                      {chapteredImportJob.preview_backend && <span>预检后端：{chapteredImportJob.preview_backend}</span>}
                      {chapteredImportJob.worker_backend && <span>正文解析后端：{chapteredImportJob.worker_backend}</span>}
                      {chapteredImportJob.worker_device && <span>正文解析设备：{deviceLabel(chapteredImportJob.worker_device)}</span>}
                      {chapteredImportJob.worker_gpu_name && <span>GPU：{chapteredImportJob.worker_gpu_name}</span>}
                      {chapteredImportJob.device_selection_reason && <span>设备选择原因：{chapteredImportJob.device_selection_reason}</span>}
                      {chapteredImportJob.worker_pid && <span>worker pid：{chapteredImportJob.worker_pid}</span>}
                      {chapteredImportJob.elapsed_seconds != null && <span>耗时：{chapteredImportJob.elapsed_seconds}s</span>}
                    </div>
                  )}
                  <ChapteredImportDeviceNotice job={chapteredImportJob} />
                  {hasChapterSafetyWarnings(chapteredImportJob) && !isChapterSafetyBlocked(chapteredImportJob) && (
                    <div style={{ padding: "6px 10px", border: "1px solid var(--warning)", borderRadius: "var(--radius-sm)", background: "rgba(179,146,86,0.06)", color: "var(--text-muted)", fontSize: 11 }}>
                      检测到部分章节标题相似，但章节编号和页码范围正常，允许导入。后续对象和机制仍按章节处理。
                    </div>
                  )}
                  {isChapterSafetyBlocked(chapteredImportJob) && (
                    <div style={{ padding: "6px 10px", border: "1px solid var(--danger)", borderRadius: "var(--radius-sm)", background: "rgba(201,112,112,0.06)", color: "var(--text-muted)", fontSize: 11 }}>
                      章节安全检查阻断导入：{chapterSafetyBlockerText(chapteredImportJob)}
                    </div>
                  )}
                  {filteredDeviceWarnings(chapteredImportJob).length > 0 && (
                    <div style={{ padding: "6px 10px", border: "1px solid var(--warning)", borderRadius: "var(--radius-sm)", background: "rgba(179,146,86,0.06)", color: "var(--text-muted)", fontSize: 11 }}>
                      {filteredDeviceWarnings(chapteredImportJob).map((w, i) => <div key={i}>{w}</div>)}
                    </div>
                  )}
                  {chapteredImportJob.heartbeat_stale && (
                    <div style={{ padding: "8px 10px", border: "1px solid var(--warning)", borderRadius: "var(--radius-sm)", background: "rgba(179,146,86,0.08)", color: "var(--text-muted)", fontSize: 12 }}>
                      导入进程长时间没有更新心跳，可能卡住。可以尝试取消导入。
                    </div>
                  )}
                  {chapteredImportJob.worker_exit_detected && (
                    <div style={{ padding: "8px 10px", border: "1px solid var(--danger)", borderRadius: "var(--radius-sm)", background: "rgba(201,112,112,0.06)", color: "var(--text-muted)", fontSize: 12 }}>
                      导入进程已退出，未完成导入。
                    </div>
                  )}
                  {chapteredImportJob.status === "failed" && chapteredImportJob.error && (
                    <div style={{ padding: "8px 10px", border: "1px solid var(--danger)", borderRadius: "var(--radius-sm)", background: "rgba(201,112,112,0.06)", color: "var(--text-muted)", fontSize: 12, overflowWrap: "anywhere" }}>
                      {chapteredImportJob.error}
                    </div>
                  )}
                  {chapteredImportJob.traceback_tail && (
                    <details style={{ border: "1px solid var(--border-soft)", borderRadius: "var(--radius-sm)", padding: "8px 10px", background: "var(--bg)" }}>
                      <summary style={{ cursor: "pointer", color: "var(--text-muted)", fontSize: 12 }}>traceback tail</summary>
                      <pre style={{ whiteSpace: "pre-wrap", margin: "8px 0 0", fontSize: 11, color: "var(--text-muted)", overflowX: "auto" }}>{chapteredImportJob.traceback_tail}</pre>
                    </details>
                  )}
                  {chapteredImportJob.worker_log_tail && (
                    <details style={{ border: "1px solid var(--border-soft)", borderRadius: "var(--radius-sm)", padding: "8px 10px", background: "var(--bg)" }}>
                      <summary style={{ cursor: "pointer", color: "var(--text-muted)", fontSize: 12 }}>worker log tail</summary>
                      <pre style={{ whiteSpace: "pre-wrap", margin: "8px 0 0", fontSize: 11, color: "var(--text-muted)", overflowX: "auto" }}>{chapteredImportJob.worker_log_tail}</pre>
                    </details>
                  )}
                  {chapteredImportJob.status === "cancelled" && (
                    <div style={{ padding: "8px 10px", border: "1px solid var(--border-soft)", borderRadius: "var(--radius-sm)", background: "var(--surface)", color: "var(--text-muted)", fontSize: 12 }}>
                      任务已取消。{chapteredImportJob.message || ""}
                    </div>
                  )}
                  {(chapteredImportJob.status === "failed" || chapteredImportJob.status === "cancelled") && (
                    <div className="previewActions">
                      <button type="button" onClick={startChapteredImportJob} disabled={previewGateLocksImport}>重新尝试</button>
                    </div>
                  )}
                  {chapteredImportJob.status === "completed" && (() => {
                    const docId = chapteredImportJob?.result?.document_id || chapteredImportJob?.document_id;
                    return docId ? (
                      <div className="previewActions">
                        <button type="button" className="primaryButton" onClick={_openImportedDocument}>
                          打开详情
                        </button>
                        <button type="button" onClick={_openImportedDocument}>
                          进入详情处理笔记与对象
                        </button>
                        <span style={{ color: "var(--text-muted)", fontSize: 12, alignSelf: "center" }}>document_id={docId}</span>
                        <span style={{ color: "var(--text-muted)", fontSize: 12, alignSelf: "center" }}>Zotero 原生笔记：{nativeNotesSummary(chapteredImportJob?.result?.zotero_native_notes_import)}</span>
                      </div>
                    ) : (
                      <p style={{ color: "var(--text-muted)", fontSize: 12 }}>导入完成，但缺少 document_id。</p>
                    );
                  })()}
                </div>
              )}
              {!chapteredImportJob && !state.chapteredImportJobError && (
                <p className="safetyNote" style={{ marginTop: 6 }}>请确认识别结果后点击上方按钮，后台执行章节化导入。</p>
              )}
            </div>
          ) : (
            <p className="safetyNote">当前为整篇/整本导入。请先生成导入前预览；最终确认后才会按文献类型调用 commit-paper 或 commit-book 写入文档正文。</p>
          )}
          {chapteredCommitState.error && <StateMessage title="章节化导入失败" body={chapteredCommitState.error} />}
          {chapteredCommitState.data && (
            <div className="zoteroSyncSummary">
              <span>status: {chapteredCommitState.data.status}</span>
              <span>document_id: {chapteredCommitState.data.document_id || "n/a"}</span>
              <span>chapters: {chapteredCommitState.data.inserted_chapters || 0}</span>
              <span>chunks: {chapteredCommitState.data.inserted_chunks || 0}</span>
              <span>Zotero 原生笔记: {nativeNotesSummary(chapteredCommitState.data.zotero_native_notes_import)}</span>
            </div>
          )}
        </section>
      )}

      {overrideObjectImportMode !== "chaptered" && (
        <div className="importReviewBanner">
          <p><strong>整篇导入流程说明。</strong></p>
          <p>整篇/整本导入会复用已生成的导入前预览；论文调用 commit-paper，书籍调用 commit-book。预览阶段不写核心库；确认入库后写入文档正文与 chunks。</p>
          <p>Zotero 原生数据只读读取；书籍整本入库默认不把 Zotero notes 写入 Search，也不写 Zotero 原库。</p>
          <p>对象审核工作台和 ChatGPT 输入包是后续手动流程；本页不会调用外部 LLM，也不会自动生成机制。</p>
        </div>
      )}

      {previewError && overrideObjectImportMode !== "chaptered" && <StateMessage title="预览生成失败" body={previewError} />}

      {previewResult && !previewError && overrideObjectImportMode !== "chaptered" && (
        <div className="previewResultSection">
          <div className="sectionHeader">
            <h3>预览结果</h3>
            <span className="safetyNote">core_db_write_performed: {String(previewResult.core_db_write_performed)}</span>
          </div>
          <div className="previewResultGrid">
            <PreviewField label="import_job_id" value={previewResult.import_job_id} />
            <PreviewField label="paper_md_path" value={previewResult.paper_md_path} />
            <PreviewField label="notes_md_path" value={previewResult.notes_md_path} />
            <PreviewField label="manifest_path" value={previewResult.manifest_path} />
            <PreviewField label="source_trace_path" value={previewResult.source_trace_path} />
            <PreviewField label="committed_to_library" value={String(previewResult.committed_to_library)} />
            <PreviewField label="external_llm_called" value={String(previewResult.external_llm_called)} />
          </div>

          <div className="previewActions">
            <button type="button" onClick={generateBundle} disabled={bundleLoading}>
              {bundleLoading ? "生成中..." : "生成 ChatGPT 输入包"}
            </button>
            {bundleResult?.bundle_path && (
              <button type="button" className="quietButton" onClick={copyBundlePath}>
                复制输入包路径
              </button>
            )}
            <button type="button" className="quietButton" onClick={loadBundleContent} disabled={bundleContentLoading}>
              {bundleContentLoading ? "加载中..." : "复制输入包内容"}
            </button>
            <button type="button" className="primaryButton" onClick={goToImportReview}>
              前往对象审核工作台
            </button>
          </div>

          {bundleError && <StateMessage title="生成输入包失败" body={bundleError} />}

          {bundleResult && !bundleError && (
            <div className="bundleResultSection">
              <div className="bundleMeta">
                <span>输入包路径：<code>{bundleResult.bundle_path}</code></span>
                <span>大小：{bundleResult.bundle_size_chars} 字符</span>
              </div>
              <details className="bundlePreview">
                <summary>输入包预览</summary>
                <pre style={{ whiteSpace: "pre-wrap", fontSize: "0.8rem", maxHeight: 300, overflow: "auto" }}>{bundleResult.bundle_preview}</pre>
              </details>
            </div>
          )}

          {bundleContent?.bundle_content && (
            <details className="bundleFullContent">
              <summary>完整输入包内容（点击复制后粘贴到 ChatGPT）</summary>
              <div style={{ position: "relative" }}>
                <button type="button" style={{ position: "absolute", top: 4, right: 4, fontSize: "0.75rem" }} onClick={copyBundleContent}>
                  复制全文
                </button>
                <pre style={{ whiteSpace: "pre-wrap", fontSize: "0.75rem", maxHeight: 400, overflow: "auto", padding: "8px", background: "#f5f5f5", border: "1px solid #ddd" }}>
                  {bundleContent.bundle_content}
                </pre>
              </div>
            </details>
          )}
        </div>
      )}
    </section>
  );
}
