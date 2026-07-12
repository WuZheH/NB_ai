import RetrievalResultCard from "./RetrievalResultCard.jsx";

export default function RetrievalResultList({
  state,
  selectedIds,
  onToggle,
  onAddPage,
  onAddAll,
  onClearPage,
  onAddDocument,
  onAddDocumentNotes,
}) {
  const results = state.data?.results || [];

  return (
    <section className="localRetrievalResults" aria-label="检索结果">
      <div className="localRetrievalResultsToolbar">
        <div>
          <strong>{state.status === "ready" ? `${results.length} 条结果` : "检索结果"}</strong>
          {state.data?.counts?.coverage && (
            <span>
              {state.data.counts.coverage.documents} 个文档 · {state.data.counts.coverage.source_types} 类来源
            </span>
          )}
        </div>
        <div className="localRetrievalBatchActions">
          <button type="button" onClick={onAddPage} disabled={!results.length}>当前页全选</button>
          <button type="button" onClick={onAddAll} disabled={!results.length}>本次全部加入</button>
          <button type="button" onClick={onClearPage} disabled={!results.length}>清除本页选择</button>
        </div>
      </div>

      {state.status === "idle" && <p className="localRetrievalState">输入查询后开始检索。</p>}
      {state.status === "loading" && <p className="localRetrievalState">正在读取本地派生索引…</p>}
      {state.status === "error" && <p className="localRetrievalState error">{state.error}</p>}
      {state.status === "ready" && !results.length && <p className="localRetrievalState">没有命中结果。</p>}

      <div className="localRetrievalResultStack">
        {results.map((result) => (
          <RetrievalResultCard
            key={result.fragment_id}
            result={result}
            selected={selectedIds.has(result.fragment_id)}
            onToggle={onToggle}
            onAddDocument={onAddDocument}
            onAddDocumentNotes={onAddDocumentNotes}
          />
        ))}
      </div>
    </section>
  );
}
