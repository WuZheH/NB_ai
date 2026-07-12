import StateMessage from "../../components/StateMessage.jsx";
import ZoteroPdfResultCard, {
  buildZoteroPdfDisplaySources,
  sameZoteroPdfSource,
  zoteroPdfSourceIdentity,
} from "./ZoteroPdfResultCard.jsx";

export default function ZoteroPdfPickerStep({
  state,
  setState,
  sourceMode,
  pdfPath,
  titleHint,
  selectedZoteroSource,
  showZoteroBrowse,
  zoteroQuery,
  zoteroSources,
  zoteroLoading,
  zoteroError,
  searchZoteroSources,
  selectZoteroSource,
  resetZoteroSelection,
  onOpenDocument,
  onSelectSourceMode,
  onLocalPdfPathChange,
  onNext,
}) {
  return (
    <section className="linearImportCard" aria-label="选择 PDF">
      <div className="sectionHeader">
        <h3>选择 PDF</h3>
        <span>Step 1 / 5</span>
      </div>
      <p className="linearImportCopy">PDF 入库阶段只负责整本书 / 整篇论文正文入库；章节、对象和机制在详情页处理。</p>
      <div className="sourceModeTabs" role="tablist" aria-label="导入来源">
        <button type="button" className={sourceMode === "local_pdf" ? "active" : ""} onClick={() => onSelectSourceMode("local_pdf")}>
          本地 PDF
        </button>
        <button type="button" className={sourceMode === "zotero_pdf" ? "active" : ""} onClick={() => onSelectSourceMode("zotero_pdf")}>
          Zotero PDF
        </button>
        <button type="button" className={sourceMode === "converted_md" ? "active" : ""} onClick={() => onSelectSourceMode("converted_md")}>
          converted Markdown
        </button>
      </div>

      {sourceMode === "zotero_pdf" && showZoteroBrowse && (
        <ZoteroPdfBrowserPanel
          state={state}
          setState={setState}
          zoteroQuery={zoteroQuery}
          zoteroSources={zoteroSources}
          selectedZoteroSource={selectedZoteroSource}
          zoteroLoading={zoteroLoading}
          zoteroError={zoteroError}
          searchZoteroSources={searchZoteroSources}
          selectZoteroSource={selectZoteroSource}
          onOpenDocument={onOpenDocument}
        />
      )}

      {selectedZoteroSource && (
        <div className="zoteroSelectedTrace">
          <strong>已选择 Zotero PDF</strong>
          <span>{selectedZoteroSource.title}</span>
          <code>{selectedZoteroSource.resolved_pdf_path}</code>
          <p>同时导入 Zotero 原生笔记：只读取 Zotero annotation notes；不写 Zotero 原库；不调用 LLM。书籍整本入库默认不自动写入 notes。</p>
          <button type="button" onClick={resetZoteroSelection}>重新选择 PDF</button>
        </div>
      )}

      {sourceMode === "zotero_pdf" ? (
        <div className="zoteroCurrentPath">
          <span>当前 Zotero PDF 路径</span>
          <strong>{selectedZoteroSource?.resolved_pdf_path || "请先从上方 Zotero PDF 列表选择一个 attachment"}</strong>
        </div>
      ) : (
        <div className="formField">
          <label htmlFor="linearPdfPath">{sourceMode === "converted_md" ? "Markdown 路径" : "PDF 路径"}</label>
          <input
            id="linearPdfPath"
            value={pdfPath}
            onChange={event => onLocalPdfPathChange(event.target.value)}
            placeholder={sourceMode === "converted_md" ? "选择 converted Markdown 文件路径" : "选择本地 PDF 文件路径"}
          />
        </div>
      )}
      <div className="formField">
        <label htmlFor="linearTitleHint">标题提示（可选）</label>
        <input id="linearTitleHint" value={titleHint} onChange={e => setState(s => ({ ...s, titleHint: e.target.value }))} />
      </div>
      <div className="linearImportActions">
        <button type="button" className="primaryButton" onClick={onNext} disabled={!pdfPath && !selectedZoteroSource}>
          下一步
        </button>
      </div>
    </section>
  );
}

function ZoteroPdfBrowserPanel({
  state,
  setState,
  zoteroQuery,
  zoteroSources,
  selectedZoteroSource,
  zoteroLoading,
  zoteroError,
  searchZoteroSources,
  selectZoteroSource,
  onOpenDocument,
}) {
  const sortMode = state.zoteroBrowseSort || "recommended";
  const filterMode = state.zoteroBrowseFilter || "all";
  const viewMode = state.zoteroBrowseView || "list";
  const submittedQuery = state.zoteroSearchSubmittedQuery || "";
  const displaySources = buildZoteroPdfDisplaySources(zoteroSources || [], sortMode, filterMode);
  const visibleCount = Math.min(displaySources.length, 40);

  function clearSearch() {
    setState(s => ({ ...s, zoteroQuery: "", zoteroSearchSubmittedQuery: "" }));
    searchZoteroSources(null, "");
  }

  return (
    <section className="zoteroPickerPanel zoteroPdfBrowser">
      <div className="sectionHeader">
        <h4>{submittedQuery ? "搜索结果" : "浏览 Zotero PDF"}</h4>
        <span>{visibleCount} / {displaySources.length} 个 PDF</span>
      </div>

      <form className="zoteroPdfToolbar zoteroPdfToolbarSearch" onSubmit={searchZoteroSources}>
        <input
          value={zoteroQuery}
          onChange={e => setState(s => ({ ...s, zoteroQuery: e.target.value }))}
          placeholder="搜索 title / author / year / key / path"
          aria-label="搜索 title / author / year / key / path"
        />
        <button type="submit" disabled={zoteroLoading}>{zoteroLoading ? "搜索中..." : "搜索"}</button>
        <button type="button" className="quietButton" onClick={clearSearch} disabled={zoteroLoading && !submittedQuery}>清空搜索</button>
      </form>

      <div className="zoteroPdfToolbar zoteroPdfControls">
        <label>
          <span>排序</span>
          <select
            value={sortMode}
            onChange={e => setState(s => ({ ...s, zoteroBrowseSort: e.target.value }))}
            aria-label="排序"
          >
            <option value="recommended">推荐优先</option>
            <option value="recent">最近添加</option>
            <option value="title">标题 A-Z</option>
            <option value="annotations">annotations 多到少</option>
            <option value="unimported">未入库优先</option>
          </select>
        </label>
        <label>
          <span>筛选</span>
          <select
            value={filterMode}
            onChange={e => setState(s => ({ ...s, zoteroBrowseFilter: e.target.value }))}
            aria-label="筛选"
          >
            <option value="all">全部</option>
            <option value="with_annotations">有 annotations</option>
            <option value="with_user_notes">有 user notes</option>
            <option value="unimported">未入库</option>
            <option value="imported">已入库</option>
            <option value="duplicates">重复项</option>
          </select>
        </label>
        <div className="zoteroPdfViewSwitch" role="group" aria-label="视图切换">
          <button
            type="button"
            className={viewMode === "list" ? "active" : ""}
            onClick={() => setState(s => ({ ...s, zoteroBrowseView: "list" }))}
          >
            列表
          </button>
          <button
            type="button"
            className={viewMode === "compact" ? "active" : ""}
            onClick={() => setState(s => ({ ...s, zoteroBrowseView: "compact" }))}
          >
            紧凑
          </button>
        </div>
      </div>

      <p className="zoteroPdfPickerHint">
        PML 多附件示例：同一 Zotero item 下的 EHB9L2P8 和 89ZZFRK5 会显示为两个独立卡片；同一 Zotero item、不同 attachment、annotation 数量不同时，推荐选择有 annotations / user notes 的 attachment。
      </p>

      {zoteroError && <StateMessage title="Zotero PDF 暂不可用" body={zoteroError} />}
      <div className={`zoteroPdfResultGrid ${viewMode}`}>
        {displaySources.slice(0, 40).map(source => (
          <ZoteroPdfResultCard
            key={zoteroPdfSourceIdentity(source)}
            source={source}
            selected={sameZoteroPdfSource(source, selectedZoteroSource)}
            compact={viewMode === "compact"}
            onSelect={selectZoteroSource}
            onOpenDocument={onOpenDocument}
            onRecheck={() => searchZoteroSources(null, submittedQuery)}
          />
        ))}
        {!zoteroLoading && displaySources.length === 0 && (
          <div className="zoteroPdfEmptyState">
            {submittedQuery ? "没有匹配的搜索结果。清空搜索后可继续浏览 Zotero PDF。" : "暂无可浏览的 Zotero PDF 缓存。可先同步 Zotero PDF 缓存。"}
          </div>
        )}
      </div>
    </section>
  );
}
