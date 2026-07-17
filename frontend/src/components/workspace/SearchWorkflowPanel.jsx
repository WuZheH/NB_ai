import { useMemo, useState } from "react";
import { getJson } from "../../api/client.js";
import FiveLayerSearchResults from "./FiveLayerSearchResults.jsx";
import WorkspaceStatusPill from "./WorkspaceStatusPill.jsx";

export default function SearchWorkflowPanel({ state, onViewSource, homeMode = false }) {
  const [query, setQuery] = useState("");
  const [searchState, setSearchState] = useState({ status: "idle", query: "", data: null, error: "" });
  const documentId = state?.document?.document_id;
  const chapterId = state?.current_chapter?.chapter_id;
  const resilienceFallbackActive = Boolean(state?.workspace_resilience_fallback?.active);
  const showSearchDetails = searchState.status !== "idle";

  const layerRows = useMemo(() => {
    const source = state?.source_ingestion_status || {};
    const notes = state?.notes_import_status || {};
    return [
      {
        id: "passages",
        title: "PDF 原文",
        status: source.chunked ? "available" : "unavailable",
        reason: source.chunked ? `${Number(source.chunk_count || 0)} 条可检索证据` : "当前资料尚无可检索片段",
      },
      {
        id: "notes",
        title: "笔记",
        status: Number(notes.existing || 0) > 0 ? "available" : "unavailable",
        reason: Number(notes.existing || 0) > 0 ? `${Number(notes.existing || 0)} 条关联笔记` : "当前范围没有关联笔记",
      },
    ];
  }, [state]);

  async function runDatabaseSearch(cleanQuery) {
    if (!cleanQuery) {
      setSearchState({ status: "idle", query: "", data: null, error: "" });
      return;
    }
    if ((!documentId || !chapterId) && !homeMode) {
      setSearchState({ status: "error", query: cleanQuery, data: null, error: "选择来源后可限定搜索范围。" });
      return;
    }
    setSearchState({ status: "loading", query: cleanQuery, data: null, error: "" });
    try {
      const payload = await getJson(buildDatabaseSearchPath(cleanQuery, { documentId, chapterId, homeMode }));
      setSearchState({ status: "ready", query: cleanQuery, data: payload, error: "" });
    } catch (error) {
      setSearchState({
        status: "error",
        query: cleanQuery,
        data: null,
        error: safePanelErrorMessage(error, "数据暂不可用，本地 API 恢复后自动更新。"),
      });
    }
  }

  async function handleSubmit(event) {
    event.preventDefault();
    await runDatabaseSearch(query.trim());
  }

  function handleRelatedQuery(nextQuery) {
    const cleanQuery = String(nextQuery || "").trim();
    if (!cleanQuery) return;
    setQuery(cleanQuery);
    void runDatabaseSearch(cleanQuery);
  }

  return (
    <div className="workspacePanelStack searchWorkflowPanel">
      <div className="workspacePanelHeader">
        <div>
          <p className="workspaceKicker">科研检索</p>
          <h3>数据库搜索</h3>
          <span>从当前资料范围检索 PDF 原文与关联笔记。</span>
        </div>
        <div className="workspaceSearchChipRow" aria-label="search safety chips">
          <WorkspaceStatusPill status="read_only">只读</WorkspaceStatusPill>
          <WorkspaceStatusPill status="reviewed">{Number(state?.notes_import_status?.existing || 0)} 条笔记</WorkspaceStatusPill>
          <WorkspaceStatusPill status="planned">不调用 LLM</WorkspaceStatusPill>
        </div>
      </div>

      {resilienceFallbackActive && (
        <p className="workspaceSampleNotice warning">数据暂不可用，本地 API 恢复后自动更新。</p>
      )}

      <div className="workspaceSearchBody" data-scroll-region="search-results-no-overlay">
        {searchState.query && (
          <div className="workspaceSearchNotice">
            <strong>检索：{searchState.query}</strong>
            <span>证据包主线：原文片段和用户笔记可加入 evidence packet。</span>
          </div>
        )}

        <div className={`workspaceSearchConversation ${showSearchDetails ? "withResults" : "idle"}`}>
          <FiveLayerSearchResults
            searchState={searchState}
            onViewSource={onViewSource}
            onRunRelatedQuery={handleRelatedQuery}
          />
        </div>

        {showSearchDetails && (
          <details className="workspaceDisclosure searchLayerDisclosure" data-disclosure-layout="in-flow">
            <summary>检索层状态</summary>
            <section className="workspaceLayerList" aria-label="five search layers">
              {layerRows.map((layer) => (
                <article key={layer.id} className={`workspaceLayerCard ${layer.status}`}>
                  <div>
                    <h4>{layer.title}</h4>
                    <p>{layer.reason}</p>
                  </div>
                  <WorkspaceStatusPill status={layer.status} />
                </article>
              ))}
            </section>
          </details>
        )}

      </div>

      <form className="workspaceSearchBox workspaceSearchComposer" data-composer-layout="flex-footer-no-overlay" onSubmit={handleSubmit} aria-label="research search box">
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索论文、笔记、灵感、对象、PDF chunks 或用户笔记…"
        />
        <button type="submit">搜索</button>
      </form>
    </div>
  );
}

function buildDatabaseSearchPath(query, { documentId, chapterId, homeMode }) {
  const params = new URLSearchParams();
  params.set("q", query);
  params.set("limit", homeMode ? "8" : "10");
  if (documentId) params.set("document_id", String(documentId));
  if (chapterId) params.set("chapter_id", String(chapterId));
  return `/api/v1/search/database?${params.toString()}`;
}

function safePanelErrorMessage(error, fallback) {
  const detail = error?.payload?.detail;
  if (typeof detail === "string" && detail.trim() && !/failed to fetch/i.test(detail)) {
    return detail;
  }
  return fallback;
}
