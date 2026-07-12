from __future__ import annotations

import inspect

from app.api import library_api, retrieval_api
from app.main import app
from app.services import high_quality_search_service


def test_legacy_high_quality_implementation_has_no_fts_or_bm25_call() -> None:
    service_source = inspect.getsource(high_quality_search_service).casefold()
    route_source = inspect.getsource(library_api.search_high_quality).casefold()

    for forbidden_token in ("fts", "bm25", "search_retrieval"):
        assert forbidden_token not in service_source
        assert forbidden_token not in route_source


def test_fts_and_high_quality_routes_remain_independent() -> None:
    endpoint_by_path = {route.path: route.endpoint for route in app.routes}
    high_quality_endpoint = endpoint_by_path["/api/v1/library/search/high-quality"]
    fts_endpoint = endpoint_by_path["/api/v1/retrieval/search"]

    assert high_quality_endpoint is library_api.search_high_quality
    assert fts_endpoint is retrieval_api.search_retrieval
    assert high_quality_endpoint is not fts_endpoint
    assert high_quality_endpoint.__module__ != fts_endpoint.__module__

