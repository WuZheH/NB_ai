import RetrievalResultCard from "./RetrievalResultCard.jsx";

export default function RetrievalResultList({
  state,
  searchKind,
  selectedIds,
  onToggle,
  onPreview,
  onCopy,
  onCopiedId,
  onAddPage,
  onAddAll,
  onClearPage,
}) {
  const results = state.data?.results || [];

  return (
    <section className="localRetrievalResults" aria-label="检索结果">
      <div className="localRetrievalResultsToolbar">
        <div>
          <strong>{state.status === "ready" ? `${results.length} 条结果` : "检索结果"}</strong>
          {state.data?.mode === "high_quality_notebook_search_v1" && (
            <span>Qwen embedding · Qwen reranker · 高质量最终排序</span>
          )}
          {state.data?.counts?.coverage && (
            <span>
              {state.data.counts.coverage.documents} 个文档 · {state.data.counts.coverage.source_types} 类来源
            </span>
          )}
        </div>
        <div className="localRetrievalBatchActions">
          <button type="button" className="search-button search-button-transparent search-button-compact" onClick={onAddPage} disabled={!results.length}>当前页全选</button>
          <button type="button" className="search-button search-button-transparent search-button-compact" onClick={onAddAll} disabled={!results.length}>本次全部加入</button>
          <button type="button" className="search-button search-button-transparent search-button-compact" onClick={onClearPage} disabled={!results.length}>清除本页选择</button>
        </div>
      </div>

      {state.status === "idle" && <p className="localRetrievalState">输入查询后开始检索。</p>}
      {state.status === "loading" && (
        <p className="localRetrievalState">
          {searchKind === "high_quality"
            ? "正在执行高质量语义召回与重排…"
            : "正在读取本地 FTS 关键词索引…"}
        </p>
      )}
      {state.status === "error" && <p className="localRetrievalState error">{state.error}</p>}
      {state.status === "ready" && !results.length && <p className="localRetrievalState">没有命中结果。</p>}

      <div
        className="localRetrievalResultStack search-scroll-region"
        data-testid="retrieval-results-scroll"
        tabIndex={0}
        role="region"
        aria-label="可滚动的检索结果列表"
      >
        {results.map((result, index) => (
          <RetrievalResultCard
            key={result.fragment_id}
            result={result}
            resultIndex={index}
            selected={selectedIds.has(result.fragment_id)}
            onToggle={onToggle}
            onPreview={onPreview}
            onCopy={onCopy}
            onCopiedId={onCopiedId}
          />
        ))}
      </div>
    </section>
  );
}
