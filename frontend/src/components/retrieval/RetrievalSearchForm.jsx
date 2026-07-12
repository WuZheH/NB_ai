export default function RetrievalSearchForm({
  query,
  mode,
  limit,
  loading,
  onQueryChange,
  onModeChange,
  onLimitChange,
  onSubmit,
}) {
  return (
    <form className="localRetrievalSearchForm" onSubmit={onSubmit}>
      <label className="localRetrievalQueryField">
        <span className="srOnly">检索问题或关键词</span>
        <input
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder="检索本地 PDF、Zotero 标注与笔记"
          autoComplete="off"
        />
      </label>
      <div className="localRetrievalMode" role="group" aria-label="检索模式">
        {[
          ["precision", "精准"],
          ["coverage", "覆盖"],
        ].map(([value, label]) => (
          <button
            key={value}
            type="button"
            className={mode === value ? "selected" : ""}
            aria-pressed={mode === value}
            onClick={() => onModeChange(value)}
          >
            {label}
          </button>
        ))}
      </div>
      <label className="localRetrievalLimit">
        <span>返回</span>
        <select value={limit} onChange={(event) => onLimitChange(Number(event.target.value))}>
          {[20, 50, 100, 200].map((value) => (
            <option key={value} value={value}>{value}</option>
          ))}
        </select>
      </label>
      <button className="primaryButton localRetrievalSubmit" type="submit" disabled={loading || !query.trim()}>
        {loading ? "检索中" : "检索"}
      </button>
    </form>
  );
}
