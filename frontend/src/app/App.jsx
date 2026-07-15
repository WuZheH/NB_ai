import { useEffect, useState } from "react";
import { buildTrace } from "../utils/formatters.js";
import { useAppNavigation } from "./navigation.js";
import {
  buildAdvancedWorkflowPath,
  buildLegacyPath,
  buildWorkspacePath,
  normalizeLegacyView,
  numericId,
  parseAppRouteFromLocation,
} from "./routes.js";
import { useLibraryData } from "../hooks/useLibraryData.js";
import { usePdfLocator } from "../hooks/usePdfLocator.js";
import { useSelectionInspector } from "../hooks/useSelectionInspector.js";
import Sidebar from "../components/Sidebar.jsx";
import RightInspector from "../components/RightInspector.jsx";
import { ReadShelfPage, DocumentDetailPage, EvidenceDetailPage } from "../features/library/index.js";
import { SearchPage } from "../features/search/index.js";
import { LocalRetrievalPage } from "../features/retrieval/index.js";
import { captureSearchSessionBeforeNavigation } from "../features/retrieval/state/searchSession.js";
import { ObjectDetailPage } from "../features/objects/index.js";
import { ImportPreviewPage, ImportReviewPage } from "../features/importing/index.js";
import { NotebookWorkspaceShell, ResearchWorkspacePage } from "../features/workspace/index.js";

export {
  ADVANCED_WORKFLOW_ROUTE_TEMPLATE,
  DEFAULT_HOME_PATH,
  IMPORT_PATH,
  LEGACY_HOME_PATH,
  LIBRARY_SEARCH_PATH,
  LOCAL_RETRIEVAL_PATH,
  OBJECT_REVIEW_PATH,
  READ_SHELF_PATH,
  WORKSPACE_BASE_PATH,
  WORKSPACE_BOOK_ROUTE_TEMPLATE,
  WORKSPACE_CHAPTER_ROUTE_TEMPLATE,
  buildAdvancedWorkflowPath,
  buildLegacyPath,
  buildWorkspacePath,
  parseAppRouteFromLocation,
} from "./routes.js";

const initialBrowserRoute = parseAppRouteFromLocation();

function App() {
  const [importPreviewState, setImportPreviewState] = useState({
    sourceMode: "local_pdf",
    pdfPath: "",
    titleHint: "",
    zoteroQuery: "",
    zoteroStatus: "available",
    zoteroSources: [],
    zoteroLoading: false,
    zoteroError: "",
    zoteroSyncResult: null,
    zoteroRefreshResult: null,
    selectedZoteroSource: null,
    loading: false,
    previewResult: null,
    previewError: "",
    bundleLoading: false,
    bundleResult: null,
    bundleError: "",
    bundleContent: null,
    bundleContentLoading: false,
    previewGate: null,
    previewGateLoading: false,
    previewGateError: "",
    previewGateNotice: "",
    selectedImportRoute: "",
  });
  const [importReviewState, setImportReviewState] = useState({
    jobId: "",
    jsonPaste: "",
    uploadLoading: false,
    uploadError: "",
    uploadResult: null,
    suggestions: null,
    suggestionsLoading: false,
    suggestionsError: "",
    reviewItems: [],
    reviewSource: "",
    sourceTraceSections: [],
    saveStatus: "",
    saveResult: null,
    remapPreview: null,
    remapLoading: false,
    commitLoading: false,
    commitPhase: {
      paper: { status: "pending" },
      remap: { status: "pending" },
      objects: { status: "pending" },
    },
    confirmRemapFailed: false,
  });

  const {
    selectedEvidenceId,
    sourceTrace,
    safety,
    setSourceTrace,
    updateSafety,
    clearSelection,
    selectEvidence,
    selectZoteroSource,
    selectImportJob,
    importPreviewSelection,
    ...selectionHandlers
  } = useSelectionInspector();

  const navigation = useAppNavigation({
    clearSelection,
    importPreviewSelection,
    importPreviewState,
    setSourceTrace,
    initialView: initialBrowserRoute?.view || "workspace",
  });
  const [workspaceRoute, setWorkspaceRoute] = useState(
    initialBrowserRoute?.view === "workspace" ? initialBrowserRoute.workspaceRoute : {}
  );
  const [advancedWorkflowRoute, setAdvancedWorkflowRoute] = useState(
    initialBrowserRoute?.view === "document" ? initialBrowserRoute.advancedWorkflow : null
  );

  const library = useLibraryData({
    updateSafety,
    clearSelection,
    setSourceTrace,
    selectEvidence,
    ...selectionHandlers,
    setView: navigation.setView,
    setReturnView: navigation.setReturnView,
    view: navigation.view,
  });

  const { locatorState, locateEvidence } = usePdfLocator({ updateSafety, setSourceTrace });

  useEffect(() => {
    library.loadReadShelf();
  }, []);

  useEffect(() => {
    applyParsedRoute(initialBrowserRoute, { push: false });
    const handlePopState = () => {
      applyParsedRoute(parseAppRouteFromLocation(), { push: false });
    };
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  function applyParsedRoute(parsed, { push = false } = {}) {
    if (!parsed) return;
    if (parsed.redirectPath && typeof window !== "undefined") {
      window.history.replaceState({}, "", parsed.redirectPath);
    }
    if (parsed.view === "workspace") {
      openWorkspaceRoute(parsed.workspaceRoute, { push });
      return;
    }
    if (parsed.view === "document" && parsed.advancedWorkflow?.documentId) {
      openAdvancedWorkflow(
        parsed.advancedWorkflow.documentId,
        parsed.advancedWorkflow.chapterId,
        { push }
      );
      return;
    }
    openLegacyView(parsed.view, { push });
  }

  function pushBrowserPath(path) {
    if (typeof window === "undefined") return;
    const current = `${window.location.pathname}${window.location.search}`;
    if (current !== path) {
      window.history.pushState({}, "", path);
    }
  }

  function openWorkspaceRoute(route = {}, { push = true } = {}) {
    const documentId = numericId(route.documentId);
    const chapterId = numericId(route.chapterId);
    const nextRoute = { documentId, chapterId };
    captureSearchSessionBeforeNavigation();
    clearSelection();
    setWorkspaceRoute(nextRoute);
    setAdvancedWorkflowRoute(null);
    navigation.setReturnView("workspace");
    navigation.setView("workspace");
    if (push) pushBrowserPath(buildWorkspacePath(nextRoute));
  }

  function openLegacyView(view, { push = true } = {}) {
    const nextView = normalizeLegacyView(view);
    if (!nextView) return;
    if (nextView !== "retrieval") captureSearchSessionBeforeNavigation();
    clearSelection();
    setWorkspaceRoute({});
    setAdvancedWorkflowRoute(null);
    navigation.setReturnView(nextView);
    navigation.selectNav({ id: nextView, status: "active" });
    if (push) pushBrowserPath(buildLegacyPath(nextView));
  }

  function openAdvancedWorkflow(documentId, chapterId = null, { push = true } = {}) {
    const nextDocumentId = numericId(documentId);
    const nextChapterId = numericId(chapterId);
    if (!nextDocumentId) return;
    captureSearchSessionBeforeNavigation();
    clearSelection();
    setAdvancedWorkflowRoute({
      documentId: nextDocumentId,
      chapterId: nextChapterId,
      workflow: "notes-import",
    });
    if (push) pushBrowserPath(buildAdvancedWorkflowPath(nextDocumentId, nextChapterId));
    void library.openDocument(nextDocumentId);
  }

  function handleSelectNav(item) {
    if (item.id === "workspace") {
      openWorkspaceRoute();
      return;
    }
    openLegacyView(item.id);
  }

  if (navigation.view === "workspace") {
    return (
      <NotebookWorkspaceShell>
        <ResearchWorkspacePage
          route={workspaceRoute}
          onOpenWorkspace={(route) => openWorkspaceRoute(route)}
          onOpenImport={() => openLegacyView("importPreview")}
          onOpenAdvancedWorkflow={openAdvancedWorkflow}
          onBackToSearch={() => openLegacyView("retrieval")}
        />
      </NotebookWorkspaceShell>
    );
  }

  if (navigation.view === "retrieval") {
    return (
      <div className="localRetrievalAppShell">
        <Sidebar view={navigation.view} onSelectNav={handleSelectNav} />
        <LocalRetrievalPage />
      </div>
    );
  }

  return (
    <div className="workspace">
      <Sidebar view={navigation.view} onSelectNav={handleSelectNav} />

      <main className="mainPanel">
        <header className="topbar">
          <div>
            <p className="eyebrow">只读产品壳</p>
            <h2>{navigation.currentTitle}</h2>
          </div>
          <div className="topbarTools" aria-hidden="true">
            <span>网格</span>
            <span>筛选</span>
          </div>
        </header>

        {navigation.view === "readShelf" && (
          <ReadShelfPage
            state={library.readShelf}
            selectedDocumentId={library.selectedDocumentId}
            onOpenDocument={library.openDocument}
            onOpenWorkspace={(documentId) => openWorkspaceRoute({ documentId })}
            onRefresh={library.loadReadShelf}
          />
        )}
        {navigation.view === "search" && (
          <SearchPage
            state={library.searchState}
            selectedEvidenceId={selectedEvidenceId}
            setState={library.setSearchQueryState}
            onSearch={library.runSearch}
            onOpenEvidence={library.openEvidence}
            onOpenDocument={library.openDocument}
            onLocateEvidence={locateEvidence}
            locatorState={locatorState}
            onOpenObject={(objectKey) => library.openObject(objectKey, "search")}
            onTrace={(trace) => setSourceTrace(buildTrace(trace, { selection_type: "search_result" }))}
          />
        )}
        {navigation.view === "document" && (
          <DocumentDetailPage
            state={library.documentState}
            locatorState={locatorState}
            zoteroCandidateState={library.zoteroCandidateState}
            advancedWorkflowRoute={advancedWorkflowRoute}
            onBack={() => {
              clearSelection();
              navigation.setView("readShelf");
            }}
            onOpenWorkspace={(documentId, chapterId) => openWorkspaceRoute({ documentId, chapterId })}
            onOpenEvidence={(chunkId, trace) => library.openEvidence(chunkId, trace, "document")}
            onOpenObject={(objectKey) => library.openObject(objectKey, "document")}
            onLocateEvidence={(chunkId, evidence) => {
              selectEvidence(evidence || null, { chunk_id: chunkId, selection_type: "evidence" });
              locateEvidence(chunkId);
            }}
            onSelectNote={(note, meta = {}) => {
              const chunkId = meta.primaryChunkId || note.matched_chunk_id || meta.chunkIds?.[0];
              setSourceTrace(buildTrace({
                selection_type: "zotero_inspiration_note",
                title: note.note_text || note.server_note_id || note.client_note_id,
                document_id: note.matched_document_id || library.documentState.data?.document?.document_id,
                chunk_id: chunkId,
                pdf_page: note.pdf_page,
                source: note.source,
                server_note_id: note.server_note_id,
                client_note_id: note.client_note_id,
                matched_chunk_ids: meta.chunkIds,
                zotero_attachment_key: note.zotero_attachment_key,
                locator_status: note.evidence_alignment_status,
                locator_reason: note.alignment_method,
                snippet: note.selected_text,
              }, { selection_type: "zotero_inspiration_note" }));
            }}
          />
        )}
        {navigation.view === "evidence" && (
          <EvidenceDetailPage
            state={library.evidenceState}
            locatorState={locatorState}
            evidenceObjectState={library.evidenceObjectState}
            zoteroCandidateState={library.zoteroCandidateState}
            onLocateEvidence={(chunkId) => locateEvidence(chunkId, sourceTrace)}
            onOpenObject={(objectKey) => library.openObject(objectKey, "evidence")}
            onBack={navigation.goBackFromDetail}
          />
        )}
        {navigation.view === "object" && (
          <ObjectDetailPage
            state={library.objectDetailState}
            locatorState={locatorState}
            zoteroCandidateState={library.zoteroCandidateState}
            onBack={navigation.goBackFromDetail}
            onOpenEvidence={(chunkId, trace) => library.openEvidence(chunkId, trace, "object")}
            onLocateEvidence={(chunkId, evidence) => {
              selectEvidence(evidence || null, { chunk_id: chunkId, selection_type: "evidence" });
              locateEvidence(chunkId);
            }}
          />
        )}
        {navigation.view === "importPreview" && (
          <ImportPreviewPage
            state={importPreviewState}
            setState={setImportPreviewState}
            updateSafety={updateSafety}
            onNavigate={navigation.setView}
            onAutoFillJobId={(v) => setImportReviewState(s => ({ ...s, jobId: v }))}
            onSelectZoteroSource={selectZoteroSource}
            onPreviewResult={selectImportJob}
            onOpenDocument={library.openDocument}
          />
        )}
        {navigation.view === "importReview" && (
          <ImportReviewPage
            state={importReviewState}
            setState={setImportReviewState}
            updateSafety={updateSafety}
            onNavigate={navigation.setView}
            onRefreshReadShelf={library.loadReadShelf}
          />
        )}
      </main>

      <RightInspector
        trace={sourceTrace}
        zoteroCandidateState={library.zoteroCandidateState}
        safety={safety}
        onLocateEvidence={locateEvidence}
      />
    </div>
  );
}

export default App;
