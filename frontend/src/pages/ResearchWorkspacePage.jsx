import { useEffect, useState } from "react";
import { getJson } from "../api/client.js";
import SearchWorkflowPanel from "../components/workspace/SearchWorkflowPanel.jsx";
import SourcePdfPanel from "../components/workspace/SourcePdfPanel.jsx";
import WorkspaceLayout from "../components/workspace/WorkspaceLayout.jsx";
import { NotebookWorkspaceHome } from "../features/workspace/components/ResearchWorkspaceHome.jsx";
import {
  ROUTE_FALLBACK_NOTICE,
  buildEmptyWorkspaceState,
  loadWorkspaceHome,
} from "../features/workspace/utils/researchWorkspace.js";

export { buildEmptyWorkspaceState };

export default function ResearchWorkspacePage({
  route = {},
  onOpenWorkspace,
  onOpenImport,
  onBackToSearch,
}) {
  const documentId = route.documentId ? Number(route.documentId) : null;
  const chapterId = route.chapterId ? Number(route.chapterId) : null;
  const [workspaceState, setWorkspaceState] = useState({ status: "idle", data: null, error: "" });
  const [homeState, setHomeState] = useState({
    status: "idle",
    sources: [],
    error: "",
  });
  const [sourceTarget, setSourceTarget] = useState(null);

  useEffect(() => {
    if (!documentId || !chapterId) {
      setWorkspaceState({ status: "idle", data: null, error: "" });
      setSourceTarget(null);
      return;
    }
    let cancelled = false;
    const loadingFallback = buildEmptyWorkspaceState({
      documentId,
      chapterId,
      reason: "workspace_state_loading",
    });
    setWorkspaceState({ status: "loading", data: loadingFallback, error: "" });
    getJson(`/api/v1/library/books/${documentId}/chapters/${chapterId}/workspace-state`)
      .then((payload) => {
        if (!cancelled) setWorkspaceState({ status: "ready", data: payload, error: "" });
      })
      .catch((error) => {
        if (!cancelled) {
          const fallbackState = buildEmptyWorkspaceState({
            documentId,
            chapterId,
            reason: "workspace_state_fetch_failed",
          });
          setWorkspaceState({
            status: "fallback",
            data: fallbackState,
            error: ROUTE_FALLBACK_NOTICE,
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [documentId, chapterId]);

  useEffect(() => {
    if (documentId && chapterId) {
      setHomeState((current) => ({ ...current, status: "idle" }));
      return;
    }
    let cancelled = false;
    setHomeState({
      status: "loading",
      sources: [],
      error: "",
    });
    loadWorkspaceHome()
      .then((payload) => {
        if (!cancelled) setHomeState({ status: "ready", ...payload, error: "" });
      })
      .catch((error) => {
        if (!cancelled) {
          setHomeState({
            status: "error",
            sources: [],
            error: "来源数量暂不可用，本地 API 可用后自动更新。",
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [documentId, chapterId]);

  if (!documentId || !chapterId) {
    return (
      <NotebookWorkspaceHome
        documentId={documentId}
        homeState={homeState}
        onOpenWorkspace={onOpenWorkspace}
        onOpenImport={onOpenImport}
        onBackToSearch={onBackToSearch}
      />
    );
  }

  const state = workspaceState.data || buildEmptyWorkspaceState({
    documentId,
    chapterId,
    reason: "workspace_state_unavailable",
  });
  const routeFallbackActive = workspaceState.status !== "ready";
  const workspaceTitle = state.notebook_title || state.document?.title || state.document_title || "Research Workspace";
  const documentTitle = state.document?.title || state.document_title || "当前资料";
  const chapterTitle = state.current_chapter?.title || state.chapter_title || `章节 ${chapterId}`;
  const sourceCount = Number(state.source_count || 0);
  const handleViewSource = (target) => {
    setSourceTarget(target);
  };
  const handleClearSourceTarget = () => {
    setSourceTarget(null);
  };
  return (
    <div className="researchWorkspacePage notebookLmInspiredWorkspace">
      <header className="researchWorkspaceTopbar">
        <div>
          <p className="workspaceKicker">Search</p>
          <h2>{workspaceTitle}</h2>
          <span>
            {documentTitle}{sourceCount > 0 ? ` · 共 ${sourceCount} 个来源` : ""} · 当前章节：{chapterTitle}
          </span>
          {routeFallbackActive && (
            <p className="workspaceSampleNotice warning">{ROUTE_FALLBACK_NOTICE}</p>
          )}
        </div>
        <div className="researchWorkspaceTopActions">
          <button type="button" className="workspacePillButton secondary" onClick={onBackToSearch}>
            ← 返回搜索
          </button>
          <button type="button" className="workspacePillButton">分享</button>
          <button type="button" className="workspacePillButton">设置</button>
          <button type="button" className="workspacePillButton">导出</button>
        </div>
      </header>
      <WorkspaceLayout
        sourcePanel={
          <SourcePdfPanel
            workspaceState={state}
            sourceTarget={sourceTarget}
            onClearSourceTarget={handleClearSourceTarget}
          />
        }
        workbenchPanel={
          <>
            <SearchWorkflowPanel
              state={state}
              onViewSource={handleViewSource}
            />
          </>
        }
        studioPanel={
          <WorkspaceContextSummary
            state={state}
            onBackToSearch={onBackToSearch}
          />
        }
      />
    </div>
  );
}

function WorkspaceContextSummary({ state, onBackToSearch }) {
  const document = state?.document || {};
  const chapter = state?.current_chapter || {};
  const source = state?.source_ingestion_status || {};
  const notes = state?.notes_import_status || {};
  return (
    <section className="workspacePanelStack" aria-label="Workspace 上下文">
      <div className="workspacePanelHeader">
        <div>
          <p className="workspaceKicker">上下文</p>
          <h3>当前资料</h3>
        </div>
      </div>
      <dl className="workspacePipelineFacts">
        <div><dt>文档</dt><dd>{document.title || "未命名文档"}</dd></div>
        <div><dt>章节</dt><dd>{chapter.title || "未选择章节"}</dd></div>
        <div><dt>PDF</dt><dd>{source.pdf_available ? "可用" : "不可用"}</dd></div>
        <div><dt>证据</dt><dd>{Number(source.chunk_count || 0)} 条</dd></div>
        <div><dt>笔记</dt><dd>{Number(notes.existing || 0)} 条</dd></div>
      </dl>
      <button type="button" className="workspacePillButton secondary" onClick={onBackToSearch}>
        返回搜索
      </button>
    </section>
  );
}
