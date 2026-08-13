from __future__ import annotations

import inspect

from fastapi.testclient import TestClient

from app.api import retrieval_api
from app.domains.retrieval import notebook_search_service
from app.domains.retrieval.result_contracts import NotebookFragment, OpenTarget, PublicEvidence
from app.main import app
from app.runtime.machine_config import MachineConfigUnavailable


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
    assert search.response_model.__name__ == "NotebookSearchResponse"
    assert fetch.response_model is PublicEvidence
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
    assert fetched.json()["user_note"] == "My note"
    assert "content_hash" not in fetched.json()
    assert "chunk_id" not in fetched.json()


def test_notebook_search_rejects_invalid_document_ids_with_422() -> None:
    """bool, string, float, zero and negative document_ids must yield HTTP 422
    before any search logic runs."""
    client = TestClient(app)
    for bad in ([True], [1, True], ["1"], [1.0], [0], [-1], [0, 2]):
        response = client.post(
            "/api/v1/retrieval/notebook-search",
            json={"query": "q", "source_types": ["pdf_chunk"], "document_ids": bad},
        )
        assert response.status_code == 422, (
            f"document_ids={bad} expected 422, got {response.status_code}"
        )
    # Valid values must pass request validation (non-422; search may 503
    # without machine config, which still proves the body validated).
    response = client.post(
        "/api/v1/retrieval/notebook-search",
        json={"query": "q", "source_types": ["pdf_chunk"], "document_ids": [2, 2]},
    )
    assert response.status_code != 422
    response = client.post(
        "/api/v1/retrieval/notebook-search",
        json={"query": "q", "source_types": ["pdf_chunk"], "document_ids": []},
    )
    assert response.status_code != 422


def test_notebook_high_quality_service_has_no_fts_or_bm25_fallback() -> None:
    source = inspect.getsource(notebook_search_service)
    lowered = source.lower()
    assert "fts_search_service" not in source
    assert "bm25" not in lowered
    assert "search_high_quality" in source
    assert "search_zotero_note_vectors" in source
    assert "_predict_scores" in source


def test_notebook_api_reports_structured_private_configuration_error(monkeypatch) -> None:
    monkeypatch.setattr(
        retrieval_api,
        "search_notebook",
        lambda _request: (_ for _ in ()).throw(MachineConfigUnavailable("config_missing")),
    )
    response = TestClient(app).post(
        "/api/v1/retrieval/notebook-search",
        json={"query": "configuration probe", "limit": 1},
    )
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["error"] == "high_quality_search_configuration_unavailable"
    assert detail["error_code"] == "config_missing"
    assert "\\" not in detail["message"]
    assert ":/" not in detail["message"]
