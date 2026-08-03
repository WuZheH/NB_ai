from __future__ import annotations

from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "frontend" / "src"
ROUTES_PATH = SOURCE_ROOT / "app" / "routes.js"
EXPECTED_PATH_CONSTANTS = {
    "DEFAULT_HOME_PATH": "/",
    "WORKSPACE_BASE_PATH": "/workspace",
    "WORKSPACE_BOOK_ROUTE_TEMPLATE": "/workspace/books/:documentId",
    "WORKSPACE_CHAPTER_ROUTE_TEMPLATE": "/workspace/books/:documentId/chapters/:chapterId",
    "DOCUMENT_ROUTE_TEMPLATE": "/library/books/:documentId",
    "READ_SHELF_PATH": "/read-shelf",
    "LIBRARY_SEARCH_PATH": "/library-search",
    "LOCAL_RETRIEVAL_PATH": "/retrieval",
    "IMPORT_PATH": "/import",
    "OBJECT_REVIEW_PATH": "/object-review",
    "LEGACY_HOME_PATH": "/legacy",
}


def test_frontend_route_constants_preserve_public_urls() -> None:
    source = ROUTES_PATH.read_text(encoding="utf-8")
    constants = dict(re.findall(r'export const (\w+) = "([^"]+)";', source))
    assert {name: constants.get(name) for name in EXPECTED_PATH_CONSTANTS} == EXPECTED_PATH_CONSTANTS


def test_route_parser_and_builders_cover_dynamic_and_legacy_routes() -> None:
    source = ROUTES_PATH.read_text(encoding="utf-8")
    for export_name in (
        "buildWorkspacePath",
        "buildLegacyPath",
        "buildDocumentPath",
        "parseAppRouteFromLocation",
        "normalizeLegacyView",
        "numericId",
    ):
        assert f"export function {export_name}" in source
    assert r"/^\/workspace\/books\/(\d+)\/chapters\/(\d+)$/" in source
    assert r"/^\/workspace\/books\/(\d+)$/" in source
    assert r"/^\/library\/books\/(\d+)$/" in source
    assert "workflow=notes-import" not in source
    assert "advancedWorkflow" not in source


def test_canonical_app_entry_and_legacy_facade_remain_available() -> None:
    canonical = (SOURCE_ROOT / "app" / "App.jsx").read_text(encoding="utf-8")
    legacy = (SOURCE_ROOT / "App.jsx").read_text(encoding="utf-8")
    main = (SOURCE_ROOT / "main.jsx").read_text(encoding="utf-8")
    assert "export default App;" in canonical
    assert 'from "./app/App.jsx"' in legacy
    assert 'from "./app/App.jsx"' in main
    assert 'from "./routes.js"' in canonical

