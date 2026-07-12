import StateMessage from "../components/StateMessage.jsx";
import SearchRelatedObjects from "../components/search/SearchRelatedObjects.jsx";
import SearchRelatedPapers, { buildHighQualityPaperGroups, buildPaperGroups } from "../components/search/SearchRelatedPapers.jsx";
export default function SearchPage({
  state,
  selectedEvidenceId,
  setState,
  onSearch,
  onOpenEvidence,
  onOpenDocument,
  onOpenObject
}) {
  const paperGroups = state.papers?.length
    ? buildHighQualityPaperGroups(state.papers, state.query)
    : buildPaperGroups(state.results, state.grouped, state.query);

  return (
    <section className="searchPage">
      <div className="scopeHint">仅搜索本地已读 / 已掌握资料库</div>
      <form className="searchForm" onSubmit={onSearch}>
        <input
          value={state.query}
          onChange={(event) => setState((current) => ({ ...current, query: event.target.value }))}
          placeholder="搜索已读资料中的对象、论文和命中片段..."
          aria-label="资料库搜索关键词"
        />
        <button type="submit">搜索</button>
      </form>
      {state.status === "idle" && (
        <StateMessage title="搜索本地已读书架" body="输入关键词查看对象、资料和正文片段。" />
      )}
      {state.status === "loading" && <StateMessage title="正在检索对象与论文片段..." />}
      {state.status === "empty" && (
        <>
          <StateMessage title="未找到本地结果" body="可尝试其他已读或已掌握资料中的关键词。" />
        </>
      )}
      {state.status === "error" && <StateMessage title="搜索暂不可用" body={state.error} />}
      {state.status === "ready" && (
        <>
          {state.fallbackNotice && <p className="emptyInline">{state.fallbackNotice}</p>}
          <SearchRelatedObjects
            objects={state.objects}
            semanticObjects={state.semanticObjects}
            onOpenObject={onOpenObject}
          />
          <SearchRelatedPapers
            groups={paperGroups}
            query={state.query}
            selectedEvidenceId={selectedEvidenceId}
            onOpenDocument={onOpenDocument}
            onOpenEvidence={onOpenEvidence}
          />
        </>
      )}
    </section>
  );
}
