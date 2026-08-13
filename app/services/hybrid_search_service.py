from __future__ import annotations

from pathlib import Path

from app.services.keyword_search_service import search_keywords
from app.services.vector_index_service import (
    VECTOR_INDEX_DIR,
    ScoredSearchResult,
    hydrate_scored_results,
    vector_search_hits,
)


KEYWORD_WEIGHT = 0.5
VECTOR_WEIGHT = 0.5


def hybrid_search(
    query: str,
    limit: int = 10,
    document_type: str | None = None,
    content_layer: str | None = None,
    read_status: str | None = None,
    index_dir: Path = VECTOR_INDEX_DIR,
    embedder_name: str | None = None,
) -> list[ScoredSearchResult]:
    keyword_results = search_keywords(
        query=query,
        document_type=document_type,
        content_layer=content_layer,
        read_status=read_status,
        limit=max(limit * 2, 20),
    )
    vector_hits = vector_search_hits(
        query=query,
        limit=max(limit * 10, 50),
        index_dir=index_dir,
        embedder_name=embedder_name,
    )

    keyword_scores: dict[int, float] = {}
    for rank, result in enumerate(keyword_results):
        keyword_scores[result.chunk_id] = 1.0 / (rank + 1)

    vector_scores = {hit.chunk_id: _normalize_vector_score(hit.score) for hit in vector_hits}
    chunk_ids = set(keyword_scores) | set(vector_scores)
    final_scores = {
        chunk_id: (KEYWORD_WEIGHT * keyword_scores.get(chunk_id, 0.0))
        + (VECTOR_WEIGHT * vector_scores.get(chunk_id, 0.0))
        for chunk_id in chunk_ids
    }
    return hydrate_scored_results(
        query=query,
        chunk_scores=final_scores,
        limit=limit,
        document_type=document_type,
        content_layer=content_layer,
        read_status=read_status,
    )


def _normalize_vector_score(score: float) -> float:
    return max(0.0, min(1.0, score))
