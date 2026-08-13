import TraceRow from "./TraceRow.jsx";
import SafetyStatus from "./SafetyStatus.jsx";
import PdfActionGroup from "./PdfActionGroup.jsx";
import { buildTrace, enhanceSourceWithZoteroCandidate, withSourceLocationConfidence, selectionTypeLabel, locatorTraceLabel, zoteroTraceLabel, locationStatusLabel } from "../utils/formatters.js";

export default function RightInspector({ trace, zoteroCandidateState, safety, onLocateEvidence }) {
  return (
    <aside className="evidencePanel">
      <ProvenancePanel trace={trace} zoteroCandidateState={zoteroCandidateState} onLocateEvidence={onLocateEvidence} />
      <section className="panelSection">
        <h3>安全状态</h3>
        <SafetyStatus safety={safety} />
      </section>
    </aside>
  );
}

function ProvenancePanel({ trace, zoteroCandidateState, onLocateEvidence }) {
  const normalized = buildTrace(trace);
  const selectionType = normalized.selection_type || "none";
  const source = withSourceLocationConfidence(
    enhanceSourceWithZoteroCandidate(
      normalized,
      zoteroCandidateState?.byDocumentId?.[normalized.document_id]
    ),
    normalized.locator_result
  );
  return (
    <>
      <section className="panelSection">
        <h3>当前选择</h3>
        <div className="traceRows">
          <TraceRow label="类型" value={selectionTypeLabel(selectionType)} />
          <TraceRow label="标题" value={normalized.title || "请选择一个对象、论文或证据片段"} />
        </div>
      </section>
      <section className="panelSection">
        <h3>来源追踪</h3>
        {trace && selectionType !== "none" ? (
          <div className="traceRows">
            {selectionType === "zotero_source" ? (
              <ZoteroSourceRows source={normalized} />
            ) : selectionType === "import_job" ? (
              <ImportJobRows job={normalized} />
            ) : (
              <>
            <TraceRow label="文档" value={normalized.document_id} />
            <TraceRow label="chunk" value={normalized.chunk_id} />
            <TraceRow label="PDF 页码" value={source.pdf_page} />
                <TraceRow label="PDF 定位" value={locatorDisplay(normalized)} />
            <TraceRow label="Zotero" value={zoteroTraceLabel(source)} />
            <PdfActionGroup
              source={source}
                  showPreview={selectionType === "evidence"}
                  onPreview={() => source.chunk_id && onLocateEvidence?.(source.chunk_id)}
              compact
            />
              </>
            )}
          </div>
        ) : (
          <p className="muted">点击搜索结果后显示来源追踪。</p>
        )}
      </section>
    </>
  );
}

function locatorDisplay(selection) {
  if (selection.locator_result) return locatorTraceLabel(selection.locator_result);
  const status = selection.locator_status;
  if (!status) return undefined;
  if (status === "exact_text_location" || status === "chunk_aligned" || status === "partial_chunk_aligned" || status === "layout_bbox_location" || status === "layout_block_location" || status === "layout_line_location" || status === "layout_sentence_location") {
    const countLabel = status === "layout_line_location" || selection.visual_mode === "layout_line_highlight"
      ? "行定位"
      : status === "layout_sentence_location"
        ? "句定位"
        : status === "layout_bbox_location" || status === "layout_block_location" || selection.visual_mode === "layout_block_highlight"
          ? "个版面定位块"
          : "个文本高亮";
    return `${locationStatusLabel(status, selection)} · 第 ${selection.pdf_page || "n/a"} 页 · ${selection.highlight_count ?? 0} ${countLabel}`;
  }
  if (status === "fallback_term_found") {
    const countLabel = selection.visual_mode === "approximate_chunk_region" ? "个近似区域" : "个高亮框";
    return `${locationStatusLabel(status, selection)} · 第 ${selection.pdf_page || "n/a"} 页 · ${selection.highlight_count ?? 0} ${countLabel}`;
  }
  if (status === "page_level_only") {
    return `${locationStatusLabel(status)} · 第 ${selection.pdf_page || "n/a"} 页`;
  }
  return selection.locator_reason || locationStatusLabel(status);
}

function ZoteroSourceRows({ source }) {
  const importStatus = source.import_status || "unknown";
  const documentText = source.existing_document_id
    ? `#${source.existing_document_id}${source.existing_document_title ? `：${source.existing_document_title}` : ""}`
    : "—";
  return (
    <>
      <TraceRow label="Zotero 条目" value={source.zotero_item_key} />
      <TraceRow label="PDF 附件" value={source.zotero_attachment_key} />
      <TraceRow label="路径状态" value={`${source.cache_status || "unknown"} · ${source.path_exists ? "exists" : "missing"}`} />
      <TraceRow label="入库状态" value={zoteroImportStatusDisplayLabel(importStatus)} />
      <TraceRow label="推荐操作" value={recommendedActionDisplayLabel(source.recommended_action)} />
      <TraceRow label="已入库文档" value={documentText} />
      <TraceRow label="匹配原因" value={zoteroMatchReasonDisplayLabel(source.match_reason)} />
      <TraceRow label="PDF 路径" value={source.resolved_pdf_path} />
      <details className="inspectorRawDetails">
        <summary>开发者原始信息</summary>
        <div className="traceRows">
          <TraceRow label="import_status" value={source.import_status} />
          <TraceRow label="existing_document_id" value={source.existing_document_id} />
          <TraceRow label="recommended_action" value={source.recommended_action} />
        </div>
      </details>
    </>
  );
}

function zoteroImportStatusDisplayLabel(status = "") {
  return {
    exact_imported: "已入库（当前 PDF）",
    sibling_imported: "同书已有入库",
    path_imported: "路径已入库",
    fingerprint_imported: "指纹命中已入库",
    not_imported: "未入库",
    unknown: "入库状态未知",
  }[status] || status || "入库状态未知";
}

function recommendedActionDisplayLabel(action = "") {
  return {
    open_existing_document: "打开已有文档",
    view_existing_document: "查看已有文档",
    select_for_import: "选择该 PDF",
    recheck_import_status: "重新检查状态",
  }[action] || action || "未知";
}

function zoteroMatchReasonDisplayLabel(reason = "") {
  return {
    same_zotero_attachment_key: "当前 Zotero PDF 已入库",
    same_zotero_item_key: "同一 Zotero 条目已有入库",
    same_pdf_path: "PDF 路径一致",
    same_first_pages_fingerprint: "前三页指纹一致",
    none: "未发现已入库匹配",
  }[reason] || reason || "未发现已入库匹配";
}

function ImportJobRows({ job }) {
  return (
    <>
      <TraceRow label="import_job_id" value={job.import_job_id} />
      <TraceRow label="paper.md" value={job.paper_md_path} />
      <TraceRow label="source_trace" value={job.source_trace_path} />
    </>
  );
}
