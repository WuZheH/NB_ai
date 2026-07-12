export const DEFAULT_HOME_PATH = "/";
export const WORKSPACE_BASE_PATH = "/workspace";
export const WORKSPACE_BOOK_ROUTE_TEMPLATE = "/workspace/books/:documentId";
export const WORKSPACE_CHAPTER_ROUTE_TEMPLATE = "/workspace/books/:documentId/chapters/:chapterId";
export const ADVANCED_WORKFLOW_ROUTE_TEMPLATE = "/library/books/:documentId";
export const READ_SHELF_PATH = "/read-shelf";
export const LIBRARY_SEARCH_PATH = "/library-search";
export const LOCAL_RETRIEVAL_PATH = "/retrieval";
export const IMPORT_PATH = "/import";
export const OBJECT_REVIEW_PATH = "/object-review";
export const LEGACY_HOME_PATH = "/legacy";

export function buildWorkspacePath(route = {}) {
  const documentId = numericId(route.documentId);
  const chapterId = numericId(route.chapterId);
  if (documentId && chapterId) return `/workspace/books/${documentId}/chapters/${chapterId}`;
  if (documentId) return `/workspace/books/${documentId}`;
  return WORKSPACE_BASE_PATH;
}

export function buildLegacyPath(view) {
  if (view === "readShelf") return READ_SHELF_PATH;
  if (view === "search") return LIBRARY_SEARCH_PATH;
  if (view === "retrieval") return LOCAL_RETRIEVAL_PATH;
  if (view === "importPreview") return IMPORT_PATH;
  if (view === "importReview") return OBJECT_REVIEW_PATH;
  return LEGACY_HOME_PATH;
}

export function buildAdvancedWorkflowPath(documentId, chapterId = null) {
  const safeDocumentId = numericId(documentId);
  const safeChapterId = numericId(chapterId);
  const query = safeChapterId ? `?chapter=${safeChapterId}&workflow=notes-import` : "?workflow=notes-import";
  return `/library/books/${safeDocumentId}${query}`;
}

export function parseAppRouteFromLocation(location = typeof window !== "undefined" ? window.location : null) {
  if (!location) return null;
  const pathname = String(location.pathname || "").replace(/\/+$/, "") || "/";
  const searchParams = new URLSearchParams(location.search || "");
  if (pathname === DEFAULT_HOME_PATH || pathname === WORKSPACE_BASE_PATH) {
    return { view: "workspace", workspaceRoute: {} };
  }
  if (pathname === READ_SHELF_PATH || pathname === LEGACY_HOME_PATH) {
    return { view: "readShelf" };
  }
  if (pathname === LIBRARY_SEARCH_PATH) {
    return { view: "search" };
  }
  if (pathname === LOCAL_RETRIEVAL_PATH) {
    return { view: "retrieval" };
  }
  if (pathname === IMPORT_PATH) {
    return { view: "importPreview" };
  }
  if (pathname === OBJECT_REVIEW_PATH) {
    return { view: "importReview" };
  }
  const workspaceChapterMatch = pathname.match(/^\/workspace\/books\/(\d+)\/chapters\/(\d+)$/);
  if (workspaceChapterMatch) {
    return {
      view: "workspace",
      workspaceRoute: {
        documentId: Number(workspaceChapterMatch[1]),
        chapterId: Number(workspaceChapterMatch[2]),
      },
    };
  }
  const workspaceBookMatch = pathname.match(/^\/workspace\/books\/(\d+)$/);
  if (workspaceBookMatch) {
    return {
      view: "workspace",
      workspaceRoute: { documentId: Number(workspaceBookMatch[1]), chapterId: null },
    };
  }
  const advancedWorkflowMatch = pathname.match(/^\/library\/books\/(\d+)$/);
  if (advancedWorkflowMatch) {
    return {
      view: "document",
      advancedWorkflow: {
        documentId: Number(advancedWorkflowMatch[1]),
        chapterId: numericId(searchParams.get("chapter")),
        workflow: searchParams.get("workflow") || "",
      },
    };
  }
  return null;
}

export function normalizeLegacyView(view) {
  if (view === "readShelf" || view === "search" || view === "retrieval" || view === "importPreview" || view === "importReview") {
    return view;
  }
  return null;
}

export function numericId(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : null;
}
