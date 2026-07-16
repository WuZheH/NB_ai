import { SourceBadge } from "./SourceBadge";
import { FragmentIdBlock } from "./FragmentIdBlock";
import type { SearchResult } from "../types";

interface ResultCardProps {
  result: SearchResult;
  selected: boolean;
  expanded: boolean;
  loadingDetail: boolean;
  onSelect: () => void;
  onExpand: () => void;
  onCopyFragment: () => void;
  onCopyId: () => void;
}

function score(value: number | null): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(4) : "—";
}

function resultPage(result: SearchResult): string {
  if (result.page_label) return result.page_label;
  return result.pdf_page === null ? "页码未知" : `第 ${result.pdf_page} 页`;
}

export function ResultCard({
  result,
  selected,
  expanded,
  loadingDetail,
  onSelect,
  onExpand,
  onCopyFragment,
  onCopyId,
}: ResultCardProps) {
  const isPdf = result.source_type === "pdf_chunk";
  const contextAvailable = Boolean(result.context_before || result.context_after);

  return (
    <article className={`result-card${selected ? " is-selected" : ""}`}>
      <header className="result-header">
        <div className="result-kicker">
          <SourceBadge sourceType={result.source_type} />
          <span>{resultPage(result)}</span>
        </div>
        <button
          type="button"
          className="search-button search-toggle-button search-button-compact select-evidence"
          aria-pressed={selected}
          title={selected ? "从证据篮子移除" : "加入证据篮子"}
          onClick={onSelect}
        >
          <span aria-hidden="true">{selected ? "✓" : "+"}</span>
          {selected ? "已加入" : "加入证据"}
        </button>
      </header>

      <h2>{result.document_title || "未命名文档"}</h2>
      <div className="result-meta">
        <span>最终排名 #{result.final_rank ?? "—"}</span>
        <span>reranker {score(result.reranker_score)}</span>
      </div>

      {isPdf ? (
        <section className="evidence-section">
          <h3>PDF 原文</h3>
          <p>{result.text || "此结果没有可显示的 PDF 文本。"}</p>
        </section>
      ) : (
        <>
          <section className="evidence-section user-note">
            <h3>用户笔记</h3>
            <p>{result.note_text || "此笔记没有正文。"}</p>
          </section>
          <section className="evidence-section selected-source">
            <h3>对应选中文本</h3>
            <p>{result.selected_text || "此笔记没有关联的选中文本。"}</p>
          </section>
        </>
      )}

      {expanded && (
        <section className="context-panel">
          <h3>上下文</h3>
          {loadingDetail ? (
            <p>正在读取完整片段…</p>
          ) : contextAvailable ? (
            <>
              {result.context_before && <p>{result.context_before}</p>}
              {result.context_after && <p>{result.context_after}</p>}
            </>
          ) : (
            <p>没有额外上下文。</p>
          )}
        </section>
      )}

      {expanded && (
        <details className="provenance">
          <summary>来源摘要</summary>
          <pre>{JSON.stringify(result.provenance ?? {}, null, 2)}</pre>
        </details>
      )}

      <FragmentIdBlock fragmentId={result.fragment_id} onCopy={onCopyId} />

      <footer className="result-actions">
        <button type="button" className="search-button search-button-subtle search-button-compact" onClick={onExpand}>
          {expanded ? "收起预览" : "预览"}
        </button>
        <button type="button" className="search-button search-button-transparent search-button-compact" onClick={onCopyFragment}>复制片段</button>
      </footer>
    </article>
  );
}
