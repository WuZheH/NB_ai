import { useMemo, useState } from "react";
import { getJson } from "../../api/client.js";
import FiveLayerSearchResults from "./FiveLayerSearchResults.jsx";
import WorkspaceStatusPill from "./WorkspaceStatusPill.jsx";
import { normalizeWorkspaceState } from "../../utils/workspaceStateAdapter.js";

export default function SearchWorkflowPanel({ state, sourceSamples = { status: "idle", targets: [] }, onViewSource, homeMode = false }) {
  const [query, setQuery] = useState("");
  const [searchState, setSearchState] = useState({ status: "idle", query: "", data: null, error: "" });
  const documentId = state?.document?.document_id;
  const chapterId = state?.current_chapter?.chapter_id;
  const resilienceFallbackActive = Boolean(state?.workspace_resilience_fallback?.active);
  const showSearchDetails = searchState.status !== "idle";
  const legacyFix9SearchHint = "输入问题后检索数据库证据";

  const layerRows = useMemo(() => {
    return normalizeWorkspaceState(state || {}).searchLayers;
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
          <span>ResearchEvidencePacket-A：从原文片段、用户笔记、对象和机制来源中检索；PDF chunks 和用户笔记用于 evidence packet。</span>
        </div>
        <div className="workspaceSearchChipRow" aria-label="search safety chips">
          <WorkspaceStatusPill status="read_only">只读</WorkspaceStatusPill>
          <WorkspaceStatusPill status="reviewed">68 条笔记</WorkspaceStatusPill>
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

        {!homeMode && showSearchDetails && (
          <details className="workspaceDisclosure sourceSampleDisclosure" data-disclosure-layout="in-flow">
            <summary>来源样本</summary>
            <SourceSampleResults
              state={state}
              sourceSamples={sourceSamples}
              onViewSource={onViewSource}
            />
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

function SourceSampleResults({ state, sourceSamples, onViewSource }) {
  const display = normalizeWorkspaceState(state);
  const source = state?.source_ingestion_status || {};
  const targets = sourceSamples?.targets || [];
  const noteTargets = targets.filter((target) => target.sourceKind === "note");
  const passageTargets = targets.filter((target) => target.sourceKind === "passage");
  const noNotes = display.noNotes;
  return (
    <section className="workspaceSourceSamples" aria-label="source sample results">
      <div className="workspacePanelHeader">
        <div>
          <p className="workspaceKicker">来源样本</p>
          <h3>笔记 / 原文定位预览</h3>
        </div>
        <WorkspaceStatusPill status={sourceSamples?.status === "ready" ? "available" : "planned"}>
          {sourceSampleSourceLabel(sourceSamples?.source)}
        </WorkspaceStatusPill>
      </div>
      {sourceSamples?.status === "loading" && <p className="workspaceSampleNotice">正在读取只读来源样本...</p>}
      {sourceSamples?.status === "error" && <p className="workspaceSampleNotice warning">{sourceSamples.error}</p>}
      {noNotes && (
        <p className="workspaceSampleNotice">NO_NOTES_IN_SCOPE · 本章没有可用笔记来源样本。</p>
      )}
      {!noNotes && noteTargets.length === 0 && sourceSamples?.status !== "loading" && (
        <p className="workspaceSampleNotice">只读 API 暂未返回笔记来源样本。</p>
      )}
      {noteTargets.map((target) => (
        <SourceSampleCard
          key={`note-${target.serverNoteId || target.clientNoteId || target.zoteroAnnotationKey}`}
          target={target}
          label="笔记来源样本"
          onViewSource={onViewSource}
        />
      ))}
      {passageTargets.length > 0 ? (
        passageTargets.map((target) => (
          <SourceSampleCard
            key={`passage-${target.matchedChunkId || target.zoteroAnnotationKey}`}
            target={target}
            label="原文来源样本"
            onViewSource={onViewSource}
          />
        ))
      ) : (
        <p className="workspaceSampleNotice">
          {source.chunked ? "原文来源样本暂不可用；原文证据层仍保持可检索。" : "原文来源受阻：PDF chunks 不可用。"}
        </p>
      )}
    </section>
  );
}

function SourceSampleCard({ target, label, onViewSource }) {
  const text = target.noteText || target.selectedText || target.chunkEvidenceText || "来源文本不可用。";
  return (
    <article className={`workspaceSourceSampleCard ${target.sourceKind}`}>
      <div>
        <strong>{label}</strong>
        <span>
          {target.pageLabel || (target.page ? `p.${target.page}` : "页码不可用")}
          {target.matchedChunkId ? ` · chunk ${target.matchedChunkId}` : ""}
        </span>
      </div>
      <p>{text}</p>
      <button type="button" className="workspacePillButton" onClick={() => onViewSource?.(target)}>
        定位到 PDF
      </button>
    </article>
  );
}

function sourceSampleSourceLabel(source) {
  if (!source) return "只读";
  if (source === "real_api") return "真实只读 API";
  if (source === "fixture") return "测试 fixture";
  return source;
}
