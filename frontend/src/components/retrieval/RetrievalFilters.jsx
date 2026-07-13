const SOURCE_TYPES = [
  ["", "全部来源"],
  ["pdf_chunk", "PDF 片段"],
  ["zotero_annotation_comment", "Zotero 批注笔记"],
  ["zotero_child_note", "Zotero 子笔记"],
  ["zotero_inspiration_note", "灵感笔记"],
];

export default function RetrievalFilters({ value, searchKind, onChange }) {
  function update(key, nextValue) {
    onChange({ ...value, [key]: nextValue });
  }

  return (
    <div className="localRetrievalFilters" aria-label="检索过滤器">
      <label>
        <span>来源</span>
        <select value={value.sourceType} onChange={(event) => update("sourceType", event.target.value)}>
          {SOURCE_TYPES.map(([sourceType, label]) => (
            <option key={sourceType || "all"} value={sourceType}>{label}</option>
          ))}
        </select>
      </label>
      <label>
        <span>文档 ID</span>
        <input
          type="number"
          min="1"
          value={value.documentId}
          onChange={(event) => update("documentId", event.target.value)}
          placeholder="全部"
        />
      </label>
      {searchKind === "keyword" && (
        <label>
          <span>年份</span>
          <input
            type="number"
            min="1900"
            max="2100"
            value={value.year}
            onChange={(event) => update("year", event.target.value)}
            placeholder="全部"
          />
        </label>
      )}
      <label className="localRetrievalCheck">
        <input
          type="checkbox"
          checked={value.includeContext}
          onChange={(event) => update("includeContext", event.target.checked)}
        />
        <span>加载上下文</span>
      </label>
      {searchKind === "keyword" && (
        <label className="localRetrievalCheck">
          <input
            type="checkbox"
            checked={value.collapseDuplicates}
            onChange={(event) => update("collapseDuplicates", event.target.checked)}
          />
          <span>折叠重复来源</span>
        </label>
      )}
    </div>
  );
}
