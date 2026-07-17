import { useEffect, useState } from "react";
import { getJson } from "../api/client.js";
import AdvancedWorkflowDrawer from "../components/workspace/AdvancedWorkflowDrawer.jsx";
import ChapterWorkflowBanner from "../components/workspace/ChapterWorkflowBanner.jsx";
import MechanismRelationGraphPanel from "../components/workspace/MechanismRelationGraphPanel.jsx";
import SearchWorkflowPanel from "../components/workspace/SearchWorkflowPanel.jsx";
import SourcePdfPanel from "../components/workspace/SourcePdfPanel.jsx";
import WorkspaceLayout from "../components/workspace/WorkspaceLayout.jsx";
import { advancedWorkflowHref } from "../components/workspace/WorkspaceWorkflowLink.jsx";
import { NotebookWorkspaceHome } from "../features/workspace/components/ResearchWorkspaceHome.jsx";
import {
  ROUTE_FALLBACK_NOTICE,
  buildEmptyWorkspaceState,
  buildGraphFocusTarget,
  loadWorkspaceHome,
  loadWorkspaceSourceSamples,
} from "../features/workspace/utils/researchWorkspace.js";

export { buildEmptyWorkspaceState };

export default function ResearchWorkspacePage({
  route = {},
  onOpenWorkspace,
  onOpenImport,
  onOpenAdvancedWorkflow,
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
  const [graphFocusTarget, setGraphFocusTarget] = useState(null);
  const [sourceSamples, setSourceSamples] = useState({ status: "idle", targets: [], error: "", source: "" });
  const [workflowDrawerOpen, setWorkflowDrawerOpen] = useState(false);

  useEffect(() => {
    if (!documentId || !chapterId) {
      setWorkspaceState({ status: "idle", data: null, error: "" });
      setSourceTarget(null);
      setGraphFocusTarget(null);
      setSourceSamples({ status: "idle", targets: [], error: "", source: "" });
      setWorkflowDrawerOpen(false);
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

  useEffect(() => {
    const state = workspaceState.data;
    if (
      !documentId
      || !chapterId
      || workspaceState.status !== "ready"
      || !state
      || state.notes_import_status?.status === "blocked_no_notes_in_scope"
    ) {
      setSourceSamples({ status: "idle", targets: [], error: "", source: "" });
      setSourceTarget(null);
      setGraphFocusTarget(null);
      return;
    }
    let cancelled = false;
    setSourceSamples({ status: "loading", targets: [], error: "", source: "real_api" });
    loadWorkspaceSourceSamples(documentId, chapterId, state)
      .then((targets) => {
        if (!cancelled) setSourceSamples({ status: "ready", targets, error: "", source: "real_api" });
      })
      .catch((error) => {
        if (!cancelled) {
          setSourceSamples({
            status: "error",
            targets: [],
            error: "部分来源样本暂不可用，本地 API 恢复后自动更新。",
            source: "real_api",
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [documentId, chapterId, workspaceState.data]);

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
    setGraphFocusTarget(buildGraphFocusTarget(target));
  };
  const handleClearSourceTarget = () => {
    setSourceTarget(null);
    setGraphFocusTarget(null);
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
            onOpenAdvancedWorkflow={onOpenAdvancedWorkflow}
          />
        }
        workbenchPanel={
          <>
            <ChapterWorkflowBanner
              state={state}
              onOpenAdvancedWorkflow={onOpenAdvancedWorkflow}
              onViewWorkflowStatus={() => setWorkflowDrawerOpen(true)}
            />
            <SearchWorkflowPanel
              state={state}
              sourceSamples={sourceSamples}
              onViewSource={handleViewSource}
            />
          </>
        }
        studioPanel={
          <MechanismRelationGraphPanel
            state={state}
            focusTarget={graphFocusTarget}
            selectionTarget={sourceTarget}
            documentId={documentId}
            chapterId={chapterId}
          />
        }
      />
      <AdvancedWorkflowDrawer
        isOpen={workflowDrawerOpen}
        onClose={() => setWorkflowDrawerOpen(false)}
        workspaceState={state}
        documentId={documentId}
        chapterId={chapterId}
        advancedWorkflowHref={advancedWorkflowHref(documentId, chapterId)}
        onOpenAdvancedWorkflow={onOpenAdvancedWorkflow}
      />
    </div>
  );
}
