import FragmentIdBlock from "../../shared/components/FragmentIdBlock.jsx";
import SourceBadge from "../../shared/components/SourceBadge.jsx";
import {
  normalizeRetrievalResult,
  pageLabel,
} from "../../features/retrieval/utils/notebookSearch.js";

export default function RetrievalResultCard({
  result,
  resultIndex,
  selected,
  onToggle,
  onPreview,
  onCopy,
  onCopiedId,
}) {
  const displayResult = normalizeRetrievalResult(result);
  const isPdf = displayResult.source_type === "pdf_chunk";
  const excerpt = isPdf
    ? displayResult.coherent_text
    : displayResult.user_note || displayResult.selected_source_text;

  return (
    <article
      className={`localRetrievalResult search-card ${selected ? "selected" : ""}`}
      data-result-index={resultIndex}
    >
      <div className="localRetrievalResultTop">
        <div className="localRetrievalResultSource">
          <SourceBadge sourceType={displayResult.source_type} />
          <span className="localRetrievalLocation">{pageLabel(displayResult)}</span>
        </div>
        <button
          type="button"
          className="search-icon-button search-toggle-button search-button-compact localRetrievalSelect"
          aria-pressed={selected}
          aria-label={selected ? "从证据篮子移除" : "加入证据篮子"}
          title={selected ? "从证据篮子移除" : "加入证据篮子"}
          onClick={() => onToggle(displayResult)}
        >
          <span aria-hidden="true">{selected ? "✓" : "+"}</span>
          <span className="srOnly">{selected ? "已加入证据篮子" : "加入证据篮子"}</span>
        </button>
      </div>

      <div className="localRetrievalResultHeading">
        <h3>{displayResult.document_title || "未命名来源"}</h3>
      </div>

      <section className={`localRetrievalEvidenceBlock ${isPdf ? "pdfText" : "userNote"}`}>
        <strong>{isPdf ? "PDF 原文" : "用户笔记"}</strong>
        <p>{excerpt || "该结果没有可显示的正文摘要。"}</p>
      </section>

      <div className="localRetrievalResultActions">
        <button type="button" className="search-button search-button-subtle search-button-compact" onClick={() => onPreview(displayResult)}>预览</button>
        <button type="button" className="search-button search-button-transparent search-button-compact" onClick={() => onCopy(displayResult)}>复制片段</button>
      </div>

      <details className="searchTechnicalDetails localRetrievalTechnicalDetails">
        <summary>技术详情</summary>
        <div className="localRetrievalResultMeta">
          {displayResult.selection_rank && <span>选择顺序 #{displayResult.selection_rank}</span>}
        </div>
        <FragmentIdBlock fragmentId={displayResult.fragment_id} onCopied={onCopiedId} />
      </details>
    </article>
  );
}
