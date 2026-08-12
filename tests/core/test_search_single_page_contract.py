from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_sidebar_exposes_exactly_one_search_entry() -> None:
    sidebar = _read("frontend/src/components/Sidebar.jsx")
    assert sidebar.count('{ id: "retrieval", label: "搜索", status: "active" }') == 1
    assert 'id: "search"' not in sidebar
    for forbidden in ("资料库搜索", "本地证据检索", "资料库高级搜索"):
        assert forbidden not in sidebar


def test_legacy_library_search_normalizes_to_the_unified_route() -> None:
    routes = _read("frontend/src/app/routes.js")
    assert 'if (view === "search") return LOCAL_RETRIEVAL_PATH;' in routes
    assert 'return { view: "retrieval", redirectPath: LOCAL_RETRIEVAL_PATH };' in routes
    assert 'if (view === "search") return "retrieval";' in routes


def test_canonical_app_has_one_search_page_and_captures_workspace_return_state() -> None:
    app = _read("frontend/src/app/App.jsx")
    assert "SearchPage" not in app
    assert app.count("<LocalRetrievalPage />") == 1
    assert "captureSearchSessionBeforeNavigation" in app
    assert 'onBackToSearch={() => openLegacyView("retrieval")}' in app
    assert "window.history.replaceState" in app


def test_obsolete_search_facades_are_removed() -> None:
    obsolete_paths = (
        "frontend/src/pages/SearchPage.jsx",
        "frontend/src/features/search/index.js",
        "frontend/src/components/search/SearchRelatedObjects.jsx",
        "frontend/src/components/search/SearchRelatedPapers.jsx",
        "frontend/src/shared/hooks/useAsyncResource.js",
    )
    for relative in obsolete_paths:
        assert not (ROOT / relative).exists()


def test_unified_page_uses_mature_preview_and_all_search_controls() -> None:
    page = _read("frontend/src/pages/LocalRetrievalPage.jsx")
    form = _read("frontend/src/components/retrieval/RetrievalSearchForm.jsx")
    filters = _read("frontend/src/components/retrieval/RetrievalFilters.jsx")
    preview = _read(
        "frontend/src/features/retrieval/components/SearchPreviewPanel.jsx"
    )
    assert "searchNotebookRetrieval" in page
    assert "searchLocalRetrieval" in page
    assert "fetchRetrievalFragmentLocator" in page
    assert "fetchEvidencePdfLocation" in page
    assert "<EvidenceBasketPanel" in page
    assert "高质量搜索" in form
    assert "关键词搜索" in form
    for label in ("来源", "文档 ID", "加载上下文"):
        assert label in filters
    assert 'import PdfLocationPreview from "../../../PdfLocationPreview.jsx";' in preview
    assert "<PdfLocationPreview {...pdfPreview.props} />" in preview
    assert "PdfFragmentPreview" not in preview


def test_search_session_covers_workspace_round_trip_state() -> None:
    page = _read("frontend/src/pages/LocalRetrievalPage.jsx")
    session = _read("frontend/src/features/retrieval/state/searchSession.js")
    workspace = _read("frontend/src/pages/ResearchWorkspacePage.jsx")
    workspace_search = _read("frontend/src/components/workspace/SearchWorkflowPanel.jsx")
    for field in (
        "query",
        "searchKind",
        "ftsMode",
        "filters",
        "searchState",
        "previewState",
        "basket",
        "scroll",
    ):
        assert field in page
    assert "registerSearchSessionCapture" in session
    assert "captureSearchSessionBeforeNavigation" in session
    assert "writeSearchSession" in session
    assert "summarizeSearchSession" in session
    assert "WorkspaceSearchSessionPanel" in workspace
    assert "readSearchSession" in workspace_search
    assert "summarizeSearchSession" in workspace_search
    assert "/api/v1/search/database" not in workspace_search
    assert "getJson" not in workspace_search
    assert "useState" not in workspace_search
    assert "<form" not in workspace_search


def test_packaged_chromium_keeps_the_legacy_pdfjs_runtime() -> None:
    preview = _read("frontend/src/PdfLocationPreview.jsx")
    assert 'import("pdfjs-dist/legacy/build/pdf.mjs")' in preview
    assert 'import("pdfjs-dist/legacy/build/pdf.worker.mjs?url")' in preview
    assert "fitWidthOnLoad" in preview
