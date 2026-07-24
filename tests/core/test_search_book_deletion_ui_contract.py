from __future__ import annotations

from pathlib import Path

from app.main import app


ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_workspace_has_no_ordinary_ui_entry_but_deep_link_and_source_remain() -> None:
    ordinary_sources = {
        path: _read(path)
        for path in (
            "frontend/src/components/Sidebar.jsx",
            "frontend/src/pages/ReadShelfPage.jsx",
            "frontend/src/pages/DocumentDetailPage.jsx",
            "frontend/src/pages/BookDetailPage.jsx",
            "frontend/src/pages/import/ImportCompleteStep.jsx",
            "integrations/search_desktop/renderer/desktop-shell.html",
        )
    }
    for path, source in ordinary_sources.items():
        assert "打开 Research Workspace" not in source, path
        assert "在 Workspace 中打开" not in source, path
        assert "科研台" not in source, path
    assert 'href="/workspace"' not in ordinary_sources["integrations/search_desktop/renderer/desktop-shell.html"]
    assert 'id: "workspace"' not in ordinary_sources["frontend/src/components/Sidebar.jsx"]

    routes = _read("frontend/src/app/routes.js")
    app_source = _read("frontend/src/app/App.jsx")
    assert 'WORKSPACE_BASE_PATH = "/workspace"' in routes
    assert 'pathname === WORKSPACE_BASE_PATH' in routes
    assert 'navigation.view === "workspace"' in app_source
    assert "ResearchWorkspacePage" in app_source
    assert "NotebookWorkspaceShell" in app_source
    assert (ROOT / "frontend/src/pages/ResearchWorkspacePage.jsx").is_file()
    assert (ROOT / "frontend/src/components/workspace/NotebookWorkspaceShell.jsx").is_file()


def test_read_shelf_management_has_no_default_dangerous_selection() -> None:
    shelf = _read("frontend/src/pages/ReadShelfPage.jsx")
    dialog = _read("frontend/src/features/library/components/BookDeletionDialog.jsx")
    management_api = _read("frontend/src/features/library/api/libraryManagement.js")
    for label in (
        "导入书籍",
        "管理书架",
        "刷新书架",
        "查看删除影响",
        "移出书架",
        "删除所选",
        "取消管理",
        "查看已归档",
        "恢复到书架",
        "打开书籍",
        "同时删除 Search 管理的 PDF 副本",
    ):
        assert label in shelf
    assert "useState([])" in shelf
    assert "current.length >= 5" in shelf
    assert "全选" not in shelf
    assert "dangerButton" in shelf
    assert "!managementMode" in shelf
    for label in (
        "永久删除此书的 Search 数据",
        "原始外部 PDF 默认保留",
        "document ID",
        "恢复包",
        "阻塞项",
        "重新检查删除影响",
    ):
        assert label in dialog
    assert "idsConfirmed" in dialog
    assert 'confirmationText === "删除"' in dialog
    assert "preview_token" in management_api
    assert "expected_document_revision" in management_api
    assert "X-Search-Mutation-Token" in management_api


def test_delete_routes_are_post_only_and_preview_is_get() -> None:
    methods = {
        route.path: set(route.methods or set())
        for route in app.routes
    }
    assert methods["/api/v1/library/documents/{document_id}/deletion-preview"] == {"GET"}
    assert methods["/api/v1/library/documents/{document_id}/delete"] == {"POST"}
    assert methods["/api/v1/library/documents/delete-batch"] == {"POST"}
    assert methods["/api/v1/library/management/archive"] == {"POST"}
    assert methods["/api/v1/library/management/restore"] == {"POST"}


def test_mcp_contract_does_not_expose_delete_or_archive_tools() -> None:
    server = "\n".join(
        (
            _read("integrations/notebook_ai_chatgpt_app/server/app.ts"),
            _read("integrations/notebook_ai_chatgpt_app/server/tools/index.ts"),
        )
    )
    for forbidden in (
        'name: "delete"',
        'name: "delete_book"',
        'name: "archive"',
        "deletion-preview",
        "/documents/delete-batch",
    ):
        assert forbidden not in server
    assert 'NOTEBOOK_TOOL_NAMES = ["search", "fetch", "export_evidence"]' in server


def test_local_delete_security_rejects_forwarded_and_non_renderer_calls() -> None:
    security = _read("app/services/library/local_mutation_security.py")
    assert '"x-forwarded-for"' in security
    assert '"cf-connecting-ip"' in security
    assert "is_loopback" in security
    assert "library_mutation_renderer_origin_required" in security
    assert "MAX_MUTATION_BODY_BYTES" in security
    assert "library_mutation_rate_limited" in security
