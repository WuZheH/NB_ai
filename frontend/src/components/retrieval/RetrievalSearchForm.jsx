export default function RetrievalSearchForm({
  query,
  searchKind,
  ftsMode,
  limit,
  loading,
  onQueryChange,
  onSearchKindChange,
  onFtsModeChange,
  onLimitChange,
  onSubmit,
}) {
  return (
    <form className="localRetrievalSearchForm" onSubmit={onSubmit}>
      <label className="localRetrievalQueryField">
        <span className="srOnly">检索问题或关键词</span>
        <input
          className="search-input"
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder="检索本地 PDF、Zotero 标注与笔记"
          autoComplete="off"
        />
      </label>
      <div className="localRetrievalMode" role="group" aria-label="检索模式">
        {[
          ["high_quality", "高质量搜索"],
          ["keyword", "关键词搜索"],
        ].map(([value, label]) => (
          <button
            key={value}
            type="button"
            className={`search-button search-toggle-button search-button-compact${searchKind === value ? " selected" : ""}`}
            aria-pressed={searchKind === value}
            onClick={() => onSearchKindChange(value)}
          >
            {label}
          </button>
        ))}
      </div>
      {searchKind === "keyword" && (
        <label className="localRetrievalFtsMode">
          <span>关键词排序</span>
          <select value={ftsMode} onChange={(event) => onFtsModeChange(event.target.value)}>
            <option value="precision">精准</option>
            <option value="coverage">覆盖</option>
          </select>
        </label>
      )}
      <label className="localRetrievalLimit">
        <span>返回</span>
        <select value={limit} onChange={(event) => onLimitChange(Number(event.target.value))}>
          {[12, 20, 50].map((value) => (
            <option key={value} value={value}>{value}</option>
          ))}
        </select>
      </label>
      <button
        className="search-button search-button-primary search-button-prominent localRetrievalSubmit"
        type="submit"
        aria-busy={loading}
        disabled={loading || !query.trim()}
      >
        {loading ? "检索中" : "检索"}
      </button>
    </form>
  );
}
