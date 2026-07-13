import { SourceBadge } from "./SourceBadge";
import type { SearchResult } from "../types";

interface ResultCardProps {
  result: SearchResult;
  selected: boolean;
  expanded: boolean;
  loadingDetail: boolean;
  onSelect: () => void;
  onExpand: () => void;
  onCopy: () => void;
  onOpen: (href: string) => void;
}

function score(value: number | null): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(4) : "—";
}

function openUrl(result: SearchResult): string | null {
  const target = result.open_target;
  if (!target) return null;
  for (const key of ["url", "href", "pdf_url", "zotero_url", "open_url"]) {
    const value = target[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return null;
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
  onCopy,
  onOpen,
}: ResultCardProps) {
  const target = openUrl(result);
  const isPdf = result.source_type === "pdf_chunk";
  const contextAvailable = Boolean(result.context_before || result.context_after);

  return (
    <article className={`result-card${selected ? " is-selected" : ""}`}>
      <header className="result-header">
        <div className="result-kicker">
          <SourceBadge sourceType={result.source_type} />
          <span>最终排名 #{result.final_rank ?? "—"}</span>
        </div>
        <label className="select-evidence">
          <input type="checkbox" checked={selected} onChange={onSelect} />
          加入证据篮子
        </label>
      </header>

      <h2>{result.document_title || "未命名文档"}</h2>
      <div className="result-meta">
        <span>{resultPage(result)}</span>
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

      <details className="provenance">
        <summary>查看 provenance</summary>
        <pre>{JSON.stringify(result.provenance ?? {}, null, 2)}</pre>
      </details>

      <footer className="result-actions">
        <button type="button" onClick={onExpand}>
          {expanded ? "收起上下文" : "展开上下文"}
        </button>
        <button type="button" onClick={onCopy}>复制单条</button>
        <button
          type="button"
          disabled={!target}
          title={target ? undefined : isPdf ? "此结果没有可打开的 PDF 页目标。" : "此结果没有可打开的 Zotero 条目目标。"}
          onClick={() => target && onOpen(target)}
        >
          {isPdf ? "打开 PDF 页" : "打开 Zotero 条目"}
        </button>
      </footer>
      <code className="fragment-id">{result.fragment_id}</code>
    </article>
  );
}
