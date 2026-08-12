from fastapi import FastAPI

from app.main import app


CORE_API_PREFIXES = (
    "/api/v1/library",
    "/api/v1/retrieval",
    "/api/v1/imports",
    "/api/v1/zotero",
)

CANONICAL_API_ROUTES = (
    "/health",
    "/api/v1/system/boundary",
    "/api/v1/retrieval/search",
    "/api/v1/retrieval/index/status",
    "/api/v1/zotero/inspiration-notes/sync-status",
)

LEGACY_SEARCH_PREFIXES = (
    "/api/v1/search",
    "/api/v1/search/database",
)


def test_app_imports_with_registered_routes() -> None:
    assert isinstance(app, FastAPI)
    assert len(app.routes) > 0

    route_paths = {route.path for route in app.routes}
    for prefix in CORE_API_PREFIXES:
        assert any(path == prefix or path.startswith(f"{prefix}/") for path in route_paths), prefix
    assert set(CANONICAL_API_ROUTES).issubset(route_paths)
    for prefix in LEGACY_SEARCH_PREFIXES:
        assert not any(
            path == prefix or path.startswith(f"{prefix}/") for path in route_paths
        ), prefix

