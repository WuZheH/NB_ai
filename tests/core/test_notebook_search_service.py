from __future__ import annotations

import sqlite3
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

    def fake_details(
        ids: Any,
        *,
        document_ids: Any = None,
        registry: Any = None,
    ) -> list[NotebookFragment]:
        assert registry is None
        assert set(document_ids or []) == {1, 2}
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

    assert calls == [("EDSR", {"include_objects": False})]
    assert [item["fragment_id"] for item in response["results"]] == [
        _pdf_id(1, 101),
        _pdf_id(2, 202),
    ]
    assert [item["selection_rank"] for item in response["results"]] == [1, 2]
    assert response["results"][0]["coherent_text"]
    assert "final_score" not in response["results"][0]
    assert response["warnings"] == ["legacy_pdf_fallback:vector_store_source_drift"]


def test_mixed_search_preserves_note_roles_filters_and_unified_reranker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    note = _fragment(
        "11111111-1111-5111-8111-111111111111",
        source_type="zotero_annotation_comment",
        note_text="foot sliding interpretation",
        selected_text="quoted foot sliding artifact",
    )
    observed: dict[str, Any] = {}
    monkeypatch.setattr(
        service.high_quality_search_service,
        "search_high_quality",
        lambda _q, **_kwargs: _pdf_payload(),
    )
    def fake_filtered_details(
        ids: Any,
        *,
        document_ids: Any = None,
        registry: Any = None,
    ) -> list[NotebookFragment]:
        assert registry is None
        assert set(document_ids or []) == {1}
        return [
            _fragment(
                value,
                source_type="pdf_chunk",
                text="full source chunk",
            )
            for value in ids
        ]

    monkeypatch.setattr(
        service,
        "get_notebook_fragments",
        fake_filtered_details,
    )

    def fake_note_search(query: str, **kwargs: Any) -> dict[str, Any]:
        observed.update({"query": query, **kwargs})
        return {
            "backend": "derived_json_vector_index",
            "results": [
                {
                    "fragment": note.model_dump(mode="json"),
                    "passage_text": "[User note]\nfoot sliding interpretation\n\n[Selected source text]\nquoted foot sliding artifact",
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
    assert response["results"][0]["user_note"] == "foot sliding interpretation"
    assert response["results"][0]["selected_source_text"] == "quoted foot sliding artifact"
    assert response["results"][0]["context_before"] is None
    assert response["results"][0]["selection_rank"] == 1
    assert "reranker_score" not in response["results"][0]
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


def test_note_relevance_filters_single_word_noise_without_forced_fill() -> None:
    weak = _fragment(
        "33333333-3333-5333-8333-333333333333",
        source_type="zotero_child_note",
        note_text="gradient",
    )
    relevant = _fragment(
        "44444444-4444-5444-8444-444444444444",
        source_type="zotero_child_note",
        note_text="Posterior predictive variance combines observation noise and parameter uncertainty.",
    )
    ranked = [
        {
            "kind": "note",
            "fragment": weak,
            "reranker_score": 0.1,
            "semantic_score": 0.2,
        },
        {
            "kind": "note",
            "fragment": relevant,
            "reranker_score": 0.8,
            "semantic_score": 0.8,
        },
    ]

    filtered = service._filter_relevant_notes_and_duplicates(
        "How does posterior predictive variance combine observation noise and parameter uncertainty?",
        ranked,
    )

    assert [item["fragment"].fragment_id for item in filtered] == [
        relevant.fragment_id
    ]


def test_annotation_and_inspiration_duplicate_is_returned_once() -> None:
    annotation = _fragment(
        "55555555-5555-5555-8555-555555555555",
        source_type="zotero_annotation_comment",
        note_text="Parameter uncertainty broadens the posterior predictive distribution.",
    ).model_copy(
        update={
            "zotero_annotation_key": "ANN1",
            "content_hash": "b" * 64,
        }
    )
    inspiration = _fragment(
        "66666666-6666-5666-8666-666666666666",
        source_type="zotero_inspiration_note",
        note_text="Parameter uncertainty broadens the posterior predictive distribution.",
    ).model_copy(
        update={
            "zotero_annotation_key": "ANN1",
            "content_hash": "b" * 64,
        }
    )
    ranked = [
        {
            "kind": "note",
            "fragment": annotation,
            "reranker_score": 0.9,
            "semantic_score": 0.8,
        },
        {
            "kind": "note",
            "fragment": inspiration,
            "reranker_score": 0.8,
            "semantic_score": 0.8,
        },
    ]

    filtered = service._filter_relevant_notes_and_duplicates(
        "posterior predictive parameter uncertainty",
        ranked,
    )

    assert [item["fragment"].fragment_id for item in filtered] == [
        annotation.fragment_id
    ]


def test_missing_requested_document_returns_structured_warning(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "research.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE documents(id INTEGER PRIMARY KEY,read_status TEXT)"
        )
        connection.execute(
            "INSERT INTO documents(id,read_status) VALUES(1,'read'),(2,'archived')"
        )
        connection.commit()
    monkeypatch.setattr(service, "DEFAULT_DB_PATH", database)

    assert service._requested_document_warnings([1, 2, 999999]) == [
        {
            "code": "requested_document_not_found",
            "document_ids": [999999],
        },
        {
            "code": "requested_document_archived",
            "document_ids": [2],
        },
    ]


def test_strong_pdf_result_does_not_force_fill_with_large_score_gap() -> None:
    ranked = [
        {"kind": "pdf", "reranker_score": 7.98, "fragment": object()},
        {"kind": "pdf", "reranker_score": 6.66, "fragment": object()},
        {"kind": "pdf", "reranker_score": 6.65, "fragment": object()},
    ]
    filtered, omitted = service._filter_low_relevance_pdf_candidates(ranked)
    assert filtered == [ranked[0]]
    assert omitted == 2


def test_pdf_threshold_keeps_close_results_and_never_removes_notes() -> None:
    note = {"kind": "note", "reranker_score": 0.8, "fragment": object()}
    ranked = [
        {"kind": "pdf", "reranker_score": 7.0, "fragment": object()},
        {"kind": "pdf", "reranker_score": 6.0, "fragment": object()},
        note,
    ]
    filtered, omitted = service._filter_low_relevance_pdf_candidates(ranked)
    assert filtered == ranked
    assert omitted == 0


def _pdf_id(document_id: int, chunk_id: int) -> str:
    return fragment_uuid(
        canonical_source_locator(
            "pdf_chunk", document_id=document_id, chunk_id=chunk_id
        )
    )
