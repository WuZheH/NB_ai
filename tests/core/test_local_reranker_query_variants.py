from __future__ import annotations

from typing import Any

import pytest

from app.services import local_reranker_service as service


def _candidate(
    chunk_id: int,
    text: str,
    *,
    document_id: int = 1,
    score: float = 0.5,
) -> dict[str, Any]:
    return {
        "document_id": document_id,
        "chunk_id": chunk_id,
        "title": "HumanML3D",
        "heading_path": "Evaluation",
        "passage_text": text,
        "score": score,
    }


def _patch_reranker(monkeypatch: pytest.MonkeyPatch) -> list[list[tuple[str, str]]]:
    observed: list[list[tuple[str, str]]] = []
    monkeypatch.setattr(service, "_load_reranker", lambda _timings: object())

    def predict(_model: object, pairs: list[tuple[str, str]]) -> list[float]:
        observed.append(pairs)
        return [
            1.0 if "ground-truth text description" in text else -1.0
            for _query, text in pairs
        ]

    monkeypatch.setattr(service, "_predict_scores", predict)
    return observed


@pytest.mark.parametrize(
    "query",
    ("R-precision", "R precision", "R\u2011precision", "R\u2014precision"),
)
def test_identifier_variants_recall_and_rerank_with_original_query(
    monkeypatch: pytest.MonkeyPatch,
    query: str,
) -> None:
    calls: list[str] = []
    relevant = _candidate(
        2,
        "R-precision: for each generated motion, its ground-truth text "
        "description and 31 randomly selected mismatched descriptions...",
    )

    def recall(variant: str, *, limit: int) -> dict[str, Any]:
        calls.append(variant)
        assert limit == 20
        results = (
            [relevant]
            if variant.casefold() == "rprecision"
            else [_candidate(1, "unrelated motion metric")]
        )
        return {"results": results, "retrieval_backend": "vector_store"}

    monkeypatch.setattr(
        service.local_embedding_service,
        "search_embedding_sidecar",
        recall,
    )
    observed = _patch_reranker(monkeypatch)

    result = service.search_reranker_sidecar(query, recall_limit=20, limit=2)

    assert len(calls) <= 3
    assert any(value.casefold() == "rprecision" for value in calls)
    assert result["results"][0]["chunk_id"] == 2
    assert all(pair_query == query for pair_query, _text in observed[0])
    assert result["deduplicated_candidate_count"] == 2


def test_long_natural_language_query_uses_single_recall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    query = "How does posterior predictive uncertainty combine its sources?"
    monkeypatch.setattr(
        service.local_embedding_service,
        "search_embedding_sidecar",
        lambda variant, *, limit: (
            calls.append(variant)
            or {"results": [_candidate(1, "posterior predictive uncertainty")]}
        ),
    )
    _patch_reranker(monkeypatch)

    result = service.search_reranker_sidecar(query)

    assert calls == [query]
    assert result["query_variants"] == [query]


def test_variant_merge_is_deduplicated_balanced_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def recall(variant: str, *, limit: int) -> dict[str, Any]:
        calls.append(variant)
        offset = calls.index(variant) * 100
        results = [_candidate(1, "shared")]
        results.extend(
            _candidate(offset + value, f"candidate {offset + value}")
            for value in range(2, 62)
        )
        return {"results": results, "retrieval_backend": "vector_store"}

    monkeypatch.setattr(
        service.local_embedding_service,
        "search_embedding_sidecar",
        recall,
    )
    observed = _patch_reranker(monkeypatch)

    result = service.search_reranker_sidecar(
        "R-precision",
        recall_limit=50,
        limit=50,
    )

    identities = [
        (item["document_id"], item["chunk_id"])
        for item in result["results"]
    ]
    assert len(identities) == 50
    assert len(set(identities)) == 50
    assert {2, 102, 202}.issubset({chunk_id for _doc, chunk_id in identities})
    assert len(observed[0]) == 50


def test_vector_backend_fallback_metadata_is_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service.local_embedding_service,
        "search_embedding_sidecar",
        lambda _variant, *, limit: {
            "results": [_candidate(1, "fallback candidate")],
            "retrieval_backend": "in_memory",
            "fallback_reason": "vector_store_unavailable",
            "vector_store_status": {"status": "unavailable"},
        },
    )
    _patch_reranker(monkeypatch)

    result = service.search_reranker_sidecar("R-precision")

    assert result["retrieval_backend"] == "in_memory"
    assert result["fallback_reason"] == "vector_store_unavailable"
    assert result["vector_store_status"] == {"status": "unavailable"}
