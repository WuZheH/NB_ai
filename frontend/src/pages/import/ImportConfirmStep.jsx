import {
  confirmationContextLabel,
  deriveConfirmationContext,
  derivePrimaryImportAction,
  documentKindDisplayLabel,
} from "./importKindPolicy.js";
import {
  recommendedActionDisplayLabel,
  zoteroPdfExistingSummary,
  zoteroPdfMatchReasonSummary,
} from "./zoteroPdfImportStatus.js";

export default function ImportConfirmStep({
  kind,
  classification,
  sourceTitle,
  titleHint,
  sourceMode,
  selectedZoteroSource,
  selectedImportStatus,
  selectedBlocksDefaultImport,
  selectedSiblingImported,
  siblingImportConfirmed,
  busy,
  previewResult,
  onBack,
  onStartWholeImport,
  onOpenDocument,
  onSiblingImportConfirmChange,
}) {
  return (
    <section className="linearImportCard" aria-label="确认整体入库">
      <div className="sectionHeader">
        <h3>确认整体入库</h3>
        <span>Step 3 / 5</span>
      </div>
      <WholeImportConfirmationCopy kind={kind} />
      <div className="previewResultGrid">
        <PreviewField label="标题" value={classification?.title || sourceTitle || titleHint} />
        <PreviewField label="文档类型" value={documentKindDisplayLabel(kind)} />
        <PreviewField label="确认上下文" value={confirmationContextLabel(deriveConfirmationContext(kind))} />
        <PreviewField label="入库状态" value={sourceMode === "zotero_pdf" ? selectedImportStatus.label : "非 Zotero PDF"} />
        <PreviewField label="已入库文档" value={zoteroPdfExistingSummary(selectedImportStatus) || "—"} />
        <PreviewField label="推荐操作" value={recommendedActionDisplayLabel(selectedImportStatus.recommendedAction)} />
        <PreviewField label="Zotero 原生笔记" value={zoteroNativeNotesPolicy(kind, selectedZoteroSource)} />
      </div>
      <p className="linearImportCopy">对象不会在导入时生成。对象将在详情页基于笔记和证据按章/节处理。</p>
      {selectedBlocksDefaultImport && (
        <div className="duplicateImportNotice">
          <strong>{selectedImportStatus.status === "exact_imported" ? "当前 PDF 已经入库" : `${selectedImportStatus.label}，默认打开已有文档`}</strong>
          <span>{zoteroPdfExistingSummary(selectedImportStatus) || "已有文档"}</span>
          <span>{zoteroPdfMatchReasonSummary(selectedImportStatus)}</span>
          <button type="button" className="primaryButton" onClick={() => onOpenDocument?.(selectedImportStatus.existingDocumentId)} disabled={!selectedImportStatus.existingDocumentId}>
            打开已有文档
          </button>
        </div>
      )}
      {selectedSiblingImported && (
        <div className="duplicateImportNotice">
          <strong>同一 Zotero 条目已有入库文档</strong>
          <span>{zoteroPdfExistingSummary(selectedImportStatus) || "已有文档"}</span>
          <span>{zoteroPdfMatchReasonSummary(selectedImportStatus)}</span>
          <span>需要确认后才允许继续导入当前 attachment。</span>
          <label className="siblingImportConfirm">
            <input
              type="checkbox"
              checked={siblingImportConfirmed}
              onChange={event => onSiblingImportConfirmChange(event.target.checked)}
            />
            <span>我确认仍选择当前 PDF，并接受可能重复入库的风险</span>
          </label>
        </div>
      )}
      <div className="linearImportActions">
        <button type="button" onClick={onBack}>返回</button>
        {selectedBlocksDefaultImport ? null : (
          <button type="button" className="primaryButton" onClick={onStartWholeImport} disabled={busy || (!classification && !previewResult) || (selectedSiblingImported && !siblingImportConfirmed)}>
            {derivePrimaryImportAction(kind)}
          </button>
        )}
      </div>
    </section>
  );
}

function WholeImportConfirmationCopy({ kind }) {
  if (kind === "book") {
    return <p className="linearImportCopy">PDF 正文会整本入库；后续在详情页按章处理笔记、对象和机制。</p>;
  }
  if (kind === "paper") {
    return <p className="linearImportCopy">PDF 正文会整篇入库；后续在详情页按一级 section 处理笔记、对象和机制。</p>;
  }
  return <p className="linearImportCopy">PDF 正文会全文入库；导入后可在详情页调整类型和处理单元。</p>;
}

function zoteroNativeNotesPolicy(kind, selectedZoteroSource) {
  if (!selectedZoteroSource?.zotero_attachment_key) return "未发现 Zotero attachment，跳过笔记同步";
  if (kind === "book") return "只读读取 Zotero；本步骤不自动写入原生笔记";
  return "导入成功后尝试同步；只读读取 Zotero";
}

function PreviewField({ label, value }) {
  return (
    <div className="previewField">
      <span className="previewFieldLabel">{label}</span>
      <code className="previewFieldValue">{value ?? "—"}</code>
    </div>
  );
}
