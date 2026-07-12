from fastapi import FastAPI

from app.main import app


CORE_API_PREFIXES = (
    "/api/v1/library",
    "/api/v1/search",
    "/api/v1/retrieval",
    "/api/v1/imports",
    "/api/v1/zotero",
)


def test_app_imports_with_registered_routes() -> None:
    assert isinstance(app, FastAPI)
    assert len(app.routes) > 0

    route_paths = {route.path for route in app.routes}
    for prefix in CORE_API_PREFIXES:
        assert any(path == prefix or path.startswith(f"{prefix}/") for path in route_paths), prefix

