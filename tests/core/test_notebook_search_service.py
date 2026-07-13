from __future__ import annotations

from typing import Any

import pytest

from app.domains.retrieval import notebook_search_service as service
from app.domains.retrieval.result_contracts import NotebookFragment, OpenTarget
from app.services.retrieval.fragment_id import canonical_source_locator, fragment_uuid


def _fragment(
    fragment_id: str,
    *,
    source_type: str,
    document_id: int = 1,
    text: str | None = None,
    note_text: str | None = None,
    selected_text: str | None = None,
) -> NotebookFragment:
    return NotebookFragment(
        fragment_id=fragment_id,
        source_type=source_type,
        document_id=document_id,
        document_title=f"Document {document_id}",
        document_type="paper",
        chunk_id=101 if source_type == "pdf_chunk" else None,
        pdf_page=3,
        page_label="3",
        text=text,
        note_text=note_text,
        selected_text=selected_text,
        context_before="before",
        context_after="after",
        tags=["tag"],
        content_hash="a" * 64,
        provenance=[{"store": "fixture"}],
        open_target=OpenTarget(
            pdf_url="/api/v1/library/documents/1/pdf#page=3",
            can_open_pdf=True,
            zotero_disabled_reason="No Zotero URI",
        ),
    )


def _pdf_payload() -> dict[str, Any]:
    return {
        "retrieval_backend": "fallback_in_memory",
        "fallback_reason": "vector_store_source_drift",
        "papers": [
            {
                "document_id": 1,
                "title": "Document 1",
                "document_type": "paper",
                "top_passages": [
                    {
                        "chunk_id": 101,
                        "passage_text": "legacy first passage",
                        "embedding_score": 0.7,
                        "rerank_score": 0.9,
                        "pdf_page": 3,
                        "source_trace": {"document_id": 1, "chunk_id": 101},
                    }
                ],
            },
            {
                "document_id": 2,
                "title": "Document 2",
                "document_type": "paper",
                "top_passages": [
                    {
                        "chunk_id": 202,
                        "passage_text": "legacy second passage",
                        "embedding_score": 0.6,
                        "rerank_score": 0.8,
                        "pdf_page": 4,
                    }
                ],
            },
        ],
    }


def test_pdf_only_uses_legacy_order_scores_and_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_high_quality(query: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((query, kwargs))
        return _pdf_payload()

    def fake_details(ids: Any) -> list[NotebookFragment]:
        result = []
        for value in ids:
            document_id = 1 if value == _pdf_id(1, 101) else 2
            result.append(
                _fragment(
                    value,
                    source_type="pdf_chunk",
                    document_id=document_id,
                    text="full source chunk",
                )
            )
        return result

    monkeypatch.setattr(service.high_quality_search_service, "search_high_quality", fake_high_quality)
    monkeypatch.setattr(service, "get_notebook_fragments", fake_details)
    monkeypatch.setattr(
        service,
        "_rerank_unified",
        lambda *_args, **_kwargs: pytest.fail("PDF-only must not be re-ranked"),
    )

    response = service.search_notebook(
        {"query": "EDSR", "limit": 10, "source_types": ["pdf_chunk"]}
    )

    assert calls == [("EDSR", {})]
    assert [item["fragment_id"] for item in response["results"]] == [
        _pdf_id(1, 101),
        _pdf_id(2, 202),
    ]
    assert [item["final_score"] for item in response["results"]] == [0.9, 0.8]
    assert response["results"][0]["text"] == "legacy first passage"
    assert response["warnings"] == ["legacy_pdf_fallback:vector_store_source_drift"]


def test_mixed_search_preserves_note_roles_filters_and_unified_reranker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    note = _fragment(
        "11111111-1111-5111-8111-111111111111",
        source_type="zotero_annotation_comment",
        note_text="my interpretation",
        selected_text="quoted paper text",
    )
    observed: dict[str, Any] = {}
    monkeypatch.setattr(service.high_quality_search_service, "search_high_quality", lambda _q: _pdf_payload())
    monkeypatch.setattr(
        service,
        "get_notebook_fragments",
        lambda ids: [
            _fragment(value, source_type="pdf_chunk", text="full source chunk") for value in ids
        ],
    )

    def fake_note_search(query: str, **kwargs: Any) -> dict[str, Any]:
        observed.update({"query": query, **kwargs})
        return {
            "backend": "derived_json_vector_index",
            "results": [
                {
                    "fragment": note.model_dump(mode="json"),
                    "passage_text": "[User note]\nmy interpretation\n\n[Selected source text]\nquoted paper text",
                    "semantic_score": 0.75,
                }
            ],
        }

    monkeypatch.setattr(service, "search_zotero_note_vectors", fake_note_search)
    monkeypatch.setattr(service.local_reranker_service, "_load_reranker", lambda _timings: object())
    monkeypatch.setattr(
        service.local_reranker_service,
        "_predict_scores",
        lambda _model, pairs: [0.1, 0.95][: len(pairs)],
    )

    response = service.search_notebook(
        {
            "query": "foot sliding",
            "limit": 2,
            "source_types": ["pdf_chunk", "zotero_annotation_comment"],
            "document_ids": [1],
            "include_context": False,
        }
    )

    assert observed["source_types"] == ["zotero_annotation_comment"]
    assert observed["document_ids"] == [1]
    assert observed["limit"] == 30
    assert response["results"][0]["source_type"] == "zotero_annotation_comment"
    assert response["results"][0]["note_text"] == "my interpretation"
    assert response["results"][0]["selected_text"] == "quoted paper text"
    assert response["results"][0]["context_before"] is None
    assert response["results"][0]["final_rank"] == 1
    assert response["results"][0]["reranker_score"] == 0.95
    assert len(response["results"]) == 2
    assert response["llm_called"] is False
    assert response["production_db_write_performed"] is False


def test_request_limit_and_source_contract() -> None:
    from app.schemas.notebook_search import NotebookSearchRequest

    assert NotebookSearchRequest(query="x").limit == 12
    with pytest.raises(ValueError):
        NotebookSearchRequest(query="x", limit=51)
    with pytest.raises(ValueError):
        NotebookSearchRequest(query="x", source_types=[])
    with pytest.raises(ValueError):
        NotebookSearchRequest(query="x", source_types=["personal_note"])


def _pdf_id(document_id: int, chunk_id: int) -> str:
    return fragment_uuid(
        canonical_source_locator(
            "pdf_chunk", document_id=document_id, chunk_id=chunk_id
        )
    )
