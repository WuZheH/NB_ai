from __future__ import annotations

import inspect

from fastapi.testclient import TestClient

from app.api import retrieval_api
from app.domains.retrieval import notebook_search_service
from app.domains.retrieval.result_contracts import (
    NotebookFragment,
    NotebookFragmentLocator,
    OpenTarget,
)
from app.main import app


def _fragment() -> NotebookFragment:
    return NotebookFragment(
        fragment_id="11111111-1111-5111-8111-111111111111",
        source_type="zotero_child_note",
        document_id=1,
        document_title="Paper",
        note_text="My note",
        tags=[],
        content_hash="a" * 64,
        provenance=[{"store": "fixture"}],
        open_target=OpenTarget(zotero_disabled_reason="fixture"),
    )


def test_notebook_routes_and_response_models_are_registered() -> None:
    routes = {
        (route.path, method): route
        for route in app.routes
        for method in getattr(route, "methods", set())
    }
    search = routes[("/api/v1/retrieval/notebook-search", "POST")]
    fetch = routes[("/api/v1/retrieval/fragments/{fragment_id}", "GET")]
    locator = routes[("/api/v1/retrieval/fragments/{fragment_id}/locator", "GET")]
    assert search.response_model.__name__ == "NotebookSearchResponse"
    assert fetch.response_model is NotebookFragment
    assert locator.response_model is NotebookFragmentLocator
    assert ("/api/v1/retrieval/search", "POST") in routes
    assert ("/api/v1/library/search/high-quality", "GET") in routes


def test_api_limit_and_read_only_fields(monkeypatch) -> None:
    fragment = _fragment()
    monkeypatch.setattr(
        retrieval_api,
        "search_notebook",
        lambda request: {
            "status": "ok",
            "query": request.query,
            "mode": "high_quality_notebook_search_v1",
            "embedding_model": "Qwen3-Embedding-0.6B",
            "reranker_model": "Qwen3-Reranker-0.6B",
            "backend": "fixture",
            "result_count": 0,
            "results": [],
            "warnings": [],
            "latency": {},
            "db_write_performed": False,
            "production_db_write_performed": False,
            "zotero_db_write_performed": False,
            "vector_write_performed": False,
            "llm_called": False,
        },
    )
    monkeypatch.setattr(retrieval_api, "get_notebook_fragment", lambda _value: fragment)
    monkeypatch.setattr(
        retrieval_api,
        "get_notebook_fragment_locator",
        lambda _value: NotebookFragmentLocator(
            fragment_id=fragment.fragment_id,
            source_type=fragment.source_type,
            locator_strategy="none",
            pdf_available=False,
        ),
    )
    client = TestClient(app)

    response = client.post(
        "/api/v1/retrieval/notebook-search",
        json={"query": "VAE", "limit": 12},
    )
    assert response.status_code == 200
    assert response.json()["production_db_write_performed"] is False
    assert client.post(
        "/api/v1/retrieval/notebook-search",
        json={"query": "VAE", "limit": 51},
    ).status_code == 422
    fetched = client.get(f"/api/v1/retrieval/fragments/{fragment.fragment_id}")
    assert fetched.status_code == 200
    assert fetched.json()["note_text"] == "My note"
    locator = client.get(f"/api/v1/retrieval/fragments/{fragment.fragment_id}/locator")
    assert locator.status_code == 200
    assert locator.json()["pdf_available"] is False


def test_notebook_high_quality_service_has_no_fts_or_bm25_fallback() -> None:
    source = inspect.getsource(notebook_search_service)
    lowered = source.lower()
    assert "fts_search_service" not in source
    assert "bm25" not in lowered
    assert "search_high_quality" in source
    assert "search_zotero_note_vectors" in source
    assert "_predict_scores" in source
