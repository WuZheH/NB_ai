from __future__ import annotations

from collections import defaultdict

from app.main import app


EXPECTED_MAJOR_ROUTES = {
    "/health": "GET",
    "/api/v1/system/boundary": "GET",
    "/api/v1/library/read-shelf": "GET",
    "/api/v1/library/search": "GET",
    "/api/v1/library/search/high-quality": "GET",
    "/api/v1/library/documents/{document_id}": "GET",
    "/api/v1/search/database": "GET",
    "/api/v1/retrieval/search": "POST",
    "/api/v1/retrieval/index/status": "GET",
    "/api/v1/retrieval/evidence/export": "POST",
    "/api/v1/imports/preview": "POST",
    "/api/v1/zotero/pdf-sources": "GET",
}


def test_major_api_routes_keep_current_urls_and_methods() -> None:
    methods_by_path: dict[str, set[str]] = defaultdict(set)
    for route in app.routes:
        methods_by_path[route.path].update(route.methods or set())

    for path, method in EXPECTED_MAJOR_ROUTES.items():
        assert path in methods_by_path
        assert method in methods_by_path[path]

