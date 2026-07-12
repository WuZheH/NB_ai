export default function ImportCompleteStep({
  doneDocumentId,
  classification,
  sourceTitle,
  titleHint,
  chapteredImportJob,
  fullDocumentCommitState,
  chapteredPreview,
  onBack,
  onOpenDocument,
  onNavigate,
}) {
  return (
    <section className="linearImportCard" aria-label="完成">
      <div className="sectionHeader">
        <h3>完成</h3>
        <span>Step 5 / 5</span>
      </div>
      <div className="previewResultGrid">
        <PreviewField label="document_id" value={doneDocumentId || "待完成"} />
        <PreviewField label="标题" value={classification?.title || sourceTitle || titleHint} />
        <PreviewField label="正文片段 / 证据" value={chapteredImportJob?.result?.inserted_chunks || fullDocumentCommitState?.data?.inserted_chunks || "见详情页"} />
        <PreviewField label="章节 / section 数量" value={chapteredImportJob?.result?.inserted_chapters || chapteredPreview?.chapter_count || "见详情页"} />
        <PreviewField label="Zotero 原生笔记" value={nativeNotesSummary(chapteredImportJob?.result?.zotero_native_notes_import || fullDocumentCommitState?.data?.zotero_native_notes_import)} />
        <PreviewField label="安全状态" value="不调用 LLM / 不写向量库 / 不生成机制" />
      </div>
      <p className="linearImportCopy">请在详情页按章/节进行笔记纠错、分类、对象审核和机制审核。</p>
      <div className="linearImportActions">
        <button type="button" onClick={onBack}>返回</button>
        <button type="button" className="primaryButton" onClick={() => doneDocumentId ? onOpenDocument?.(doneDocumentId) : onNavigate?.("readShelf")} disabled={!doneDocumentId}>
          打开文档详情页
        </button>
      </div>
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

function nativeNotesSummary(summary) {
  if (!summary) return "未同步";
  if (summary.status === "success") {
    return `已同步 ${summary.inserted_count || 0} 条原生笔记`;
  }
  if (summary.status === "skipped") {
    return summary.reason || "已跳过";
  }
  if (summary.status === "failed") {
    return summary.error || "同步失败";
  }
  return summary.status || "未知";
}
