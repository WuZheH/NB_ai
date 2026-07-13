import { useState } from "react";
import { API_BASE_URL } from "../../api/client.js";
import {
  formatScore,
  fragmentFromResponse,
  notebookSourceLabel,
  normalizeRetrievalResult,
  openTargetActions,
  pageLabel,
} from "../../features/retrieval/utils/notebookSearch.js";

export default function RetrievalResultCard({
  result,
  selected,
  onToggle,
  onFetch,
  onCopy,
  onAddDocument,
  onAddDocumentNotes,
}) {
  const [detailState, setDetailState] = useState({ status: "idle", data: null, error: "" });
  const detail = detailState.data;
  const displayResult = normalizeRetrievalResult(detail ? {
    ...result,
    ...detail,
    final_rank: result.final_rank,
    final_score: result.final_score,
    reranker_score: result.reranker_score,
    semantic_score: result.semantic_score,
  } : result);
  const authors = Array.isArray(displayResult.authors) ? displayResult.authors.join(", ") : "";
  const reasons = Array.isArray(displayResult.match_reasons) ? displayResult.match_reasons : [];
  const provenance = Array.isArray(displayResult.provenance) ? displayResult.provenance : [];
  const tags = Array.isArray(displayResult.tags) ? displayResult.tags : [];
  const numericDocumentId = Number(displayResult.document_id);
  const canSelectDocument = Number.isInteger(numericDocumentId) && numericDocumentId > 0;
  const isPdf = displayResult.source_type === "pdf_chunk";
  const openActions = openTargetActions(displayResult, API_BASE_URL);
  const disabledReasons = [openActions.pdf.reason, openActions.zotero.reason].filter(Boolean);

  async function loadFullFragment() {
    if (!onFetch || detailState.status === "loading") return;
    setDetailState({ status: "loading", data: null, error: "" });
    try {
      const response = await onFetch(displayResult.fragment_id);
      setDetailState({ status: "ready", data: fragmentFromResponse(response), error: "" });
    } catch (error) {
      setDetailState({ status: "error", data: null, error: requestErrorMessage(error) });
    }
  }

  return (
    <article className={`localRetrievalResult ${selected ? "selected" : ""}`}>
      <div className="localRetrievalResultTop">
        <label className="localRetrievalSelect">
          <input type="checkbox" checked={selected} onChange={() => onToggle(displayResult)} />
          <span className="localRetrievalDisplayId">{displayResult.display_id}</span>
          <span className="localRetrievalSelectLabel">{selected ? "已加入证据篮子" : "加入证据篮子"}</span>
        </label>
        <span className={`localRetrievalSourceType source-${displayResult.source_type}`}>
          {notebookSourceLabel(displayResult.source_type)}
        </span>
      </div>

      <div className="localRetrievalResultHeading">
        <h3>{displayResult.document_title || "未命名来源"}</h3>
        <p>{[authors, displayResult.year].filter(Boolean).join(" · ") || "来源元数据未标注"}</p>
      </div>

      <div className="localRetrievalLocation">{pageLabel(displayResult)}</div>
      <code className="localRetrievalFragmentId">{displayResult.fragment_id}</code>

      {isPdf ? (
        <section className="localRetrievalEvidenceBlock pdfText" aria-label="PDF 原文">
          <strong>PDF 原文</strong>
          <p>{displayResult.text || "PDF 原文暂不可用。"}</p>
        </section>
      ) : (
        <>
          <section className="localRetrievalEvidenceBlock userNote" aria-label="用户笔记">
            <strong>用户笔记</strong>
            <p>{displayResult.note_text || "读取完整片段后显示用户笔记。"}</p>
          </section>
          <section className="localRetrievalEvidenceBlock selectedText" aria-label="对应选中文本">
            <strong>对应选中文本</strong>
            <p>{displayResult.selected_text || "该笔记没有可用的对应选中文本。"}</p>
          </section>
        </>
      )}

      <details className="localRetrievalContext">
        <summary>展开上下文</summary>
        {displayResult.context_before && <p><strong>前文</strong>{displayResult.context_before}</p>}
        {displayResult.context_after && <p><strong>后文</strong>{displayResult.context_after}</p>}
        {!displayResult.context_before && !displayResult.context_after && (
          <p>当前结果没有返回额外上下文；可读取完整片段再次确认。</p>
        )}
      </details>

      {tags.length > 0 && (
        <div className="localRetrievalReasons" aria-label="标签">
          {tags.map((tag) => <span key={tag}>{tag}</span>)}
        </div>
      )}
      {reasons.length > 0 && (
        <div className="localRetrievalReasons" aria-label="匹配原因">
          {reasons.map((reason) => <span key={reason}>{reason}</span>)}
        </div>
      )}

      <div className="localRetrievalResultMeta">
        {displayResult.base_bm25_rank && <span>BM25 rank {displayResult.base_bm25_rank}</span>}
        {displayResult.final_rank && <span>最终 rank {displayResult.final_rank}</span>}
        {displayResult.final_score !== null && <span>最终 score {formatScore(displayResult.final_score)}</span>}
        {displayResult.reranker_score !== null && <span>reranker {formatScore(displayResult.reranker_score)}</span>}
        {displayResult.semantic_score !== null && <span>semantic {formatScore(displayResult.semantic_score)}</span>}
        {(displayResult.duplicate_count || 1) > 1 && <span>重复来源 {displayResult.duplicate_count}</span>}
      </div>

      <details className="localRetrievalProvenance">
        <summary>查看 provenance</summary>
        {provenance.length > 0
          ? <pre>{JSON.stringify(provenance, null, 2)}</pre>
          : <p>当前结果未返回 provenance；可读取完整片段再次确认。</p>}
      </details>

      <div className="localRetrievalResultActions">
        <button type="button" onClick={() => onCopy(displayResult)}>复制单条</button>
        <button
          type="button"
          disabled={!onFetch || detailState.status === "loading" || detailState.status === "ready"}
          onClick={loadFullFragment}
        >
          {detailState.status === "loading" ? "读取中" : detailState.status === "ready" ? "已读取完整片段" : "读取完整片段"}
        </button>
        {openActions.pdf.enabled ? (
          <a href={openActions.pdf.href} target="_blank" rel="noreferrer">打开 PDF 页</a>
        ) : (
          <button type="button" disabled title={openActions.pdf.reason}>打开 PDF 页</button>
        )}
        {openActions.zotero.enabled ? (
          <a href={openActions.zotero.href}>打开 Zotero 条目</a>
        ) : (
          <button type="button" disabled title={openActions.zotero.reason}>打开 Zotero 条目</button>
        )}
        <button type="button" disabled={!canSelectDocument} onClick={() => onAddDocument(displayResult.document_id, false)}>
          文献全部
        </button>
        <button type="button" disabled={!canSelectDocument} onClick={() => onAddDocumentNotes(displayResult.document_id)}>
          文献笔记
        </button>
      </div>
      {disabledReasons.length > 0 && (
        <small className="localRetrievalOpenReasons">{disabledReasons.join(" · ")}</small>
      )}
      {detailState.error && <p className="localRetrievalInlineError">{detailState.error}</p>}
    </article>
  );
}

function requestErrorMessage(error) {
  const detail = error?.payload?.detail;
  if (typeof detail === "string") return detail;
  return detail?.message || detail?.error || error?.message || "完整片段暂不可用。";
}
