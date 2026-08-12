import { useState } from "react";
import { deviceLabel, qualityStatusLabel } from "../../features/importing/utils/importPreviewFormatters.js";
import { documentKindDisplayLabel } from "./importKindPolicy.js";
import { zoteroPdfImportStatus } from "./zoteroPdfImportStatus.js";

export default function ImportDiagnosticsPanel({
  children,
  className = "",
  sourceMode,
  pdfPath,
  selectedZoteroSource,
  importReadiness = {},
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
  buildPreviewQualitySummary,
}) {
  const [expanded, setExpanded] = useState(true);
  const conversionData = markdownConversionState?.data || {};
  const sourcePath = selectedZoteroSource?.resolved_pdf_path || pdfPath || "";
  const pathAvailable = sourcePath
    ? (sourceMode === "zotero_pdf" && selectedZoteroSource ? selectedZoteroSource.path_exists !== false : true)
    : false;
  const sourceImportStatus = sourceImportStatusLabel(selectedZoteroSource, duplicateCheck);
  const selectedImportStatus = zoteroPdfImportStatus(selectedZoteroSource);
  const qualitySummary = importReadiness.quality_summary || buildPreviewQualitySummary?.({ previewGate, textLayerPreview, pdfBackendUnavailable }) || { status: "not_checked" };
  const activeJob = chapteredImportJob || fullDocumentCommitState || {};
  const currentError = previewError || classifyError || zoteroError || chapteredImportJob?.error || fullDocumentCommitState?.error || "";
  const rawPayload = {
    importReadiness,
    pdfBackendUnavailable,
    previewResult,
    classification,
    previewGate,
    duplicateCheck,
    textLayerPreview,
    markdownConversionState,
    chapteredImportJob,
    fullDocumentCommitState,
  };
  return (
    <section className={`advancedDiagnostics ${className}`}>
      <header className="advancedDiagnosticsHeader">
        <div>
          <h3>高级诊断信息</h3>
          <p>默认展开为中文摘要；后端原始字段放在“开发者原始信息”里。</p>
        </div>
        <button type="button" className="diagnosticsToggleButton" onClick={() => setExpanded(value => !value)}>
          {expanded ? "收起诊断信息" : "展开诊断信息"}
        </button>
      </header>
      {expanded && (
        <div className="advancedDiagnosticsBody">
          <section className="diagnosticSummaryBlock">
            <h4>导入源状态</h4>
            <div className="previewResultGrid">
              <PreviewField label="PDF 路径" value={sourcePath || "未选择"} />
              <PreviewField label="Zotero item key" value={selectedZoteroSource?.zotero_item_key || "—"} />
              <PreviewField label="attachment key" value={selectedZoteroSource?.zotero_attachment_key || "—"} />
              <PreviewField label="路径状态" value={pathAvailable ? "可用" : "不可用"} />
              <PreviewField label="入库状态" value={sourceImportStatus} />
              <PreviewField label="import_status" value={selectedImportStatus.status} />
              <PreviewField label="existing_document_id" value={selectedImportStatus.existingDocumentId || "—"} />
              <PreviewField label="recommended_action" value={selectedImportStatus.recommendedAction || "—"} />
            </div>
          </section>

          <section className="diagnosticSummaryBlock">
            <h4>预检状态</h4>
            <div className="previewResultGrid">
              <PreviewField label="文本层质量" value={qualityStatusLabel(qualitySummary.status)} />
              <PreviewField label="页数" value={classification?.signals?.page_count ?? previewGate?.physical_page_count ?? textLayerPreview?.page_count ?? "未知"} />
              <PreviewField label="文档类型判断" value={documentKindDisplayLabel(classification?.document_type)} />
              <PreviewField label="章节识别数量" value={classification?.signals?.outline_chapter_count ?? previewResult?.chapter_count ?? "未知"} />
              <PreviewField label="是否存在警告" value={diagnosticWarningLabel({ pdfBackendUnavailable, duplicateCheck, textLayerPreview, markdownConversionState })} />
            </div>
          </section>

          <section className="diagnosticSummaryBlock">
            <h4>安全状态</h4>
            <div className="previewResultGrid">
              <PreviewField label="是否会写入知识库" value={importReadiness.can_import ? "是，需要点击最终确认" : "否"} />
              <PreviewField label="是否调用外部大模型" value="否" />
              <PreviewField label="是否生成机制" value="否" />
              <PreviewField label="是否写入向量库" value="否" />
              <PreviewField label="是否写入 Zotero" value="否" />
            </div>
          </section>

          <section className="diagnosticSummaryBlock">
            <h4>运行状态</h4>
            <div className="previewResultGrid">
              <PreviewField label="当前步骤" value={linearStepLabel(activeStep)} />
              <PreviewField label="正文解析后端" value={activeJob.worker_backend || activeJob.preview_backend || conversionData.conversion_backend || "未知"} />
              <PreviewField label="正文解析设备" value={deviceLabel(activeJob.worker_device || activeJob.parser_device || "unknown")} />
              <PreviewField label="GPU 名称" value={activeJob.worker_gpu_name || activeJob.runtime?.cuda_device_name || "—"} />
              <PreviewField label="当前错误" value={currentError || "无"} />
            </div>
          </section>

          {children && (
            <section className="diagnosticSummaryBlock diagnosticPreviewBlock">
              <h4>导入前预览</h4>
              {children}
            </section>
          )}

          <details className="developerRawInfo">
            <summary>开发者原始信息</summary>
            <div className="previewResultGrid">
              <PreviewField label="recommended_route" value={importReadiness.recommended_route || "—"} />
              <PreviewField label="primary_action" value={importReadiness.primary_action || "—"} />
              <PreviewField label="fallback_routes" value={(pdfBackendUnavailable?.fallback_routes || []).join(", ") || "—"} />
              <PreviewField label="staging job" value={previewResult?.import_job_id || "—"} />
              <PreviewField label="classification" value={classification ? `${classification.document_type}/${classification.object_import_mode}` : "—"} />
              <PreviewField label="preview_gate" value={previewGate?.recommended_route || "—"} />
              <PreviewField label="duplicate" value={duplicateCheck ? `${duplicateCheck.duplicate_found ? "found" : "none"} / ${duplicateCheck.duplicate_confidence}` : "—"} />
              <PreviewField label="text_preview_backend" value={textLayerPreview?.parser_backend || "—"} />
              <PreviewField label="conversion_backend" value={conversionData.conversion_backend || "—"} />
              <PreviewField label="identity_match" value={conversionData.identity_match === undefined ? "—" : String(Boolean(conversionData.identity_match))} />
              <PreviewField label="converted_md_path" value={conversionData.converted_md_path || "—"} />
            </div>
            <pre>{JSON.stringify(rawPayload, null, 2)}</pre>
          </details>
        </div>
      )}
    </section>
  );
}

function PreviewField({ label, value }) {
  return (
    <div className="previewField">
      <span className="previewFieldLabel">{label}</span>
      <code className="previewFieldValue">{value ?? "—"}</code>
    </div>
  );
}

function sourceImportStatusLabel(selectedZoteroSource, duplicateCheck) {
  const status = zoteroPdfImportStatus(selectedZoteroSource);
  if (status.status !== "not_imported") return status.label;
  if (duplicateCheck?.duplicate_found) return "同书已有入库";
  return "未入库";
}

function diagnosticWarningLabel({ pdfBackendUnavailable, duplicateCheck, textLayerPreview, markdownConversionState }) {
  if (pdfBackendUnavailable) return "存在警告：预检后端不可用";
  if (duplicateCheck?.duplicate_found) return "存在警告：可能重复导入";
  if (textLayerPreview?.status === "BLOCKED") return "存在警告：文本层预览不可用";
  if (markdownConversionState?.status === "markdown_failed") return "存在警告：Markdown 生成失败";
  return "无";
}

function linearStepLabel(step) {
  return {
    select: "选择 PDF",
    preflight: "导入前预检",
    confirm: "确认整体入库",
    progress: "导入进度",
    complete: "完成",
  }[step] || "未知";
}
