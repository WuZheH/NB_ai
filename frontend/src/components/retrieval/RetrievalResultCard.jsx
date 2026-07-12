function sourceLabel(sourceType) {
  return {
    pdf_chunk: "PDF 正文",
    zotero_highlight: "Zotero 高亮",
    zotero_annotation_comment: "高亮批注",
    zotero_child_note: "Zotero 笔记",
    zotero_inspiration_note: "灵感笔记",
    personal_note: "个人笔记",
    markdown_note: "Markdown 笔记",
  }[sourceType] || sourceType;
}

function locationLabel(result) {
  return [
    result.page_number ? `物理页 ${result.page_number}` : null,
    result.page_label ? `页码 ${result.page_label}` : null,
    result.section || null,
  ].filter(Boolean).join(" · ") || "位置未标注";
}

export default function RetrievalResultCard({
  result,
  selected,
  onToggle,
  onAddDocument,
  onAddDocumentNotes,
}) {
  const authors = Array.isArray(result.authors) ? result.authors.join(", ") : "";
  const reasons = Array.isArray(result.match_reasons) ? result.match_reasons : [];
  const canSelectDocument = Number.isFinite(Number(result.document_id));

  return (
    <article className={`localRetrievalResult ${selected ? "selected" : ""}`}>
      <div className="localRetrievalResultTop">
        <label className="localRetrievalSelect">
          <input type="checkbox" checked={selected} onChange={() => onToggle(result)} />
          <span className="localRetrievalDisplayId">{result.display_id}</span>
        </label>
        <span className={`localRetrievalSourceType source-${result.source_type}`}>
          {sourceLabel(result.source_type)}
        </span>
      </div>

      <div className="localRetrievalResultHeading">
        <h3>{result.title || "未命名来源"}</h3>
        <p>{[authors, result.year].filter(Boolean).join(" · ") || "来源元数据未标注"}</p>
      </div>

      <div className="localRetrievalLocation">{locationLabel(result)}</div>
      <p className="localRetrievalText">{result.text}</p>

      {(result.context_before || result.context_after) && (
        <details className="localRetrievalContext">
          <summary>上下文</summary>
          {result.context_before && <p><strong>前文</strong>{result.context_before}</p>}
          {result.context_after && <p><strong>后文</strong>{result.context_after}</p>}
        </details>
      )}

      <div className="localRetrievalReasons" aria-label="匹配原因">
        {reasons.map((reason) => <span key={reason}>{reason}</span>)}
      </div>

      <div className="localRetrievalResultMeta">
        <span>BM25 rank {result.base_bm25_rank}</span>
        <span>最终 rank {result.final_rank}</span>
        <span>重复来源 {result.duplicate_count || 1}</span>
      </div>

      <div className="localRetrievalResultActions">
        {result.zotero_uri && <a href={result.zotero_uri}>在 Zotero 中打开</a>}
        <span className="localRetrievalFile" title={result.original_file_path || ""}>
          {result.original_file_path || "无本地文件路径"}
        </span>
        <button type="button" disabled={!canSelectDocument} onClick={() => onAddDocument(result.document_id, false)}>
          文献全部
        </button>
        <button type="button" disabled={!canSelectDocument} onClick={() => onAddDocumentNotes(result.document_id)}>
          文献笔记
        </button>
      </div>
    </article>
  );
}
