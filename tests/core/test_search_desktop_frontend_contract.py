from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_search_desktop_status_and_settings_are_real_frontend_surfaces() -> None:
    page = _read("frontend/src/pages/DesktopSettingsPage.jsx")
    preload = _read("integrations/search_desktop/electron/preload/index.cjs")
    routes = _read("frontend/src/app/routes.js")
    sidebar = _read("frontend/src/components/Sidebar.jsx")
    app = _read("frontend/src/app/App.jsx")
    for method in (
        "getRuntimeStatus",
        "getAutostartStatus",
        "setAutostartEnabled",
        "getSettings",
        "updateSettings",
    ):
        assert f"bridge.{method}" in page
    for forbidden in ("bridge.restartRuntime", "bridge.openLogs", "bridge.setChatGptPaused"):
        assert forbidden not in page
    assert "setChatGptPaused" not in preload
    assert "search:chatgpt-pause" not in preload
    for label in (
        "检索后端",
        "MCP 后端",
        "Codex MCP",
        "Zotero 后端",
        "ChatGPT Tunnel",
        "Search 仅诊断 Tunnel 状态，不启动、暂停或恢复 Tunnel。",
        "重新检查",
        "技术详情",
    ):
        assert label in page
    coordinator = _read("integrations/search_desktop/electron/runtime/runtimeCoordinator.js")
    assert '"managed-by-search"' in coordinator
    assert '"external"' in coordinator
    assert 'SYSTEM_STATUS_PATH = "/system-status"' in routes
    assert 'SETTINGS_PATH = "/settings"' in routes
    assert 'id: "systemStatus"' in sidebar
    assert 'id: "settings"' in sidebar
    assert "DesktopSettingsPage" in app


def test_search_desktop_bridge_is_allowlisted_and_does_not_expose_private_data() -> None:
    preload = _read("integrations/search_desktop/electron/preload/index.cjs")
    assert 'exposeInMainWorld("searchDesktop"' in preload
    for forbidden in ("readFile", "writeFile", "fragmentId", "provenance", "query", "apiKey"):
        assert forbidden not in preload
    assert "ipcRenderer.invoke" in preload
    assert "ipcRenderer.send(" not in preload


def test_search_desktop_navigation_keeps_existing_product_routes() -> None:
    handlers = _read("integrations/search_desktop/electron/ipc/registerHandlers.js")
    for path in (
        "/retrieval",
        "/import",
        "/read-shelf",
        "/object-review",
        "/workspace",
        "/system-status",
        "/settings",
    ):
        assert f'"{path}"' in handlers


def test_frontend_uses_runtime_readiness_and_keeps_workspace_deep_link_only() -> None:
    app = _read("frontend/src/app/App.jsx")
    routes = _read("frontend/src/app/routes.js")
    sidebar = _read("frontend/src/components/Sidebar.jsx")
    readiness = _read("frontend/src/hooks/useLocalApiStatus.js")
    read_shelf = _read("frontend/src/pages/ReadShelfPage.jsx")

    assert 'return { view: "retrieval", redirectPath: LOCAL_RETRIEVAL_PATH }' in routes
    assert 'pathname === WORKSPACE_BASE_PATH' in routes
    assert 'id: "workspace"' not in sidebar
    assert "onRuntimeStatus" in readiness
    assert "getRuntimeStatus" in readiness
    assert "STARTUP_MAX_HEALTH_ATTEMPTS" in readiness
    assert "setInterval" not in readiness
    assert 'apiStatus.phase === "connected"' in app
    assert "导入书籍" in read_shelf


def test_desktop_bootstrap_status_never_opens_client_apps() -> None:
    sources = "\n".join(
        _read(path)
        for path in (
            "frontend/src/pages/DesktopSettingsPage.jsx",
            "integrations/search_desktop/electron/runtime/runtimeCoordinator.js",
            "integrations/search_desktop/electron/tray/createTray.js",
        )
    )
    for forbidden in (
        "在 Zotero 中打开",
        "打开 Zotero",
        "打开 ChatGPT",
        ".openLink(",
        "openExternal",
        "Start-Process",
    ):
        assert forbidden not in sources
