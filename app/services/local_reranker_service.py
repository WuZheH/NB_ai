from __future__ import annotations

import os
from pathlib import Path
import time
from typing import Any

from app.core.paths import MODEL_CACHE_ROOT, RERANKER_MODEL_PATH
from app.services import local_embedding_service


RERANKER_MODEL_NAME = "Qwen3-Reranker-0.6B"
DEFAULT_RERANKER_MODEL_PATH = RERANKER_MODEL_PATH
DEFAULT_MODEL_CACHE = MODEL_CACHE_ROOT
DEFAULT_MARKER_CACHE = DEFAULT_MODEL_CACHE

_RERANKER: Any | None = None
_RERANKER_LOAD_MS: float | None = None


class LocalRerankerUnavailable(RuntimeError):
    pass


def search_reranker_sidecar(query: str, recall_limit: int = 20, limit: int = 10) -> dict[str, Any]:
    normalized_query = _compact_text(query)
    if not normalized_query:
        raise ValueError("query must not be empty.")

    started = time.perf_counter()
    safe_recall_limit = max(1, min(int(recall_limit or 20), 50))
    safe_limit = max(1, min(int(limit or 10), safe_recall_limit))

    embedding_started = time.perf_counter()
    recall_payload = local_embedding_service.search_embedding_sidecar(normalized_query, limit=safe_recall_limit)
    embedding_recall_ms = _elapsed_ms(embedding_started)
    candidates = list(recall_payload.get("results") or [])
    retrieval_backend = str(recall_payload.get("retrieval_backend") or "in_memory")
    fallback_reason = recall_payload.get("fallback_reason")
    vector_store_status = recall_payload.get("vector_store_status")
    if not candidates:
        return _response(
            query=normalized_query,
            results=[],
            timing={
                "embedding_recall_ms": embedding_recall_ms,
                "load_reranker_ms": 0.0,
                "rerank_ms": 0.0,
                "total_ms": _elapsed_ms(started),
            },
            recall_limit=safe_recall_limit,
            limit=safe_limit,
            retrieval_backend=retrieval_backend,
            fallback_reason=fallback_reason,
            vector_store_status=vector_store_status,
        )

    timings: dict[str, float] = {}
    reranker = _load_reranker(timings)
    rerank_started = time.perf_counter()
    pairs = [(normalized_query, _candidate_text(candidate)) for candidate in candidates]
    scores = _predict_scores(reranker, pairs)
    timings["rerank_ms"] = _elapsed_ms(rerank_started)

    reranked = []
    for candidate, rerank_score in zip(candidates, scores):
        item = {
            **candidate,
            "embedding_score": float(candidate.get("score") or 0.0),
            "rerank_score": float(rerank_score),
        }
        item.update(_classify_tier(item))
        reranked.append(item)
    reranked.sort(key=lambda item: item["rerank_score"], reverse=True)

    top_results = reranked[:safe_limit]
    tier_counts = {
        "primary": sum(1 for r in top_results if r.get("tier") == "primary"),
        "secondary": sum(1 for r in top_results if r.get("tier") == "secondary"),
        "reference": sum(1 for r in top_results if r.get("tier") == "reference"),
    }

    return _response(
        query=normalized_query,
        results=top_results,
        timing={
            "embedding_recall_ms": embedding_recall_ms,
            "load_reranker_ms": timings.get("load_reranker_ms", 0.0),
            "rerank_ms": timings.get("rerank_ms", 0.0),
            "total_ms": _elapsed_ms(started),
        },
        recall_limit=safe_recall_limit,
        limit=safe_limit,
        tier_counts=tier_counts,
        retrieval_backend=retrieval_backend,
        fallback_reason=fallback_reason,
        vector_store_status=vector_store_status,
    )


def _load_reranker(timings: dict[str, float]) -> Any:
    global _RERANKER, _RERANKER_LOAD_MS
    if _RERANKER is not None:
        timings["load_reranker_ms"] = 0.0
        return _RERANKER

    _set_local_cache_env()
    model_path = _model_path()
    if not model_path.exists():
        raise LocalRerankerUnavailable(f"Local reranker model is missing: {model_path}")

    started = time.perf_counter()
    try:
        from sentence_transformers import CrossEncoder
    except Exception as exc:  # pragma: no cover - depends on runtime environment
        raise LocalRerankerUnavailable(f"sentence_transformers CrossEncoder import failed: {exc}") from exc

    try:
        _RERANKER = CrossEncoder(str(model_path), device=_device_name())
    except Exception as exc:  # pragma: no cover - depends on local model/GPU state
        raise LocalRerankerUnavailable(f"local reranker model load failed: {exc}") from exc

    _RERANKER_LOAD_MS = _elapsed_ms(started)
    timings["load_reranker_ms"] = _RERANKER_LOAD_MS
    return _RERANKER


def _predict_scores(reranker: Any, pairs: list[tuple[str, str]]) -> list[float]:
    raw_scores = reranker.predict(pairs, batch_size=1, show_progress_bar=False)
    if hasattr(raw_scores, "tolist"):
        raw_scores = raw_scores.tolist()
    if isinstance(raw_scores, (int, float)):
        raw_scores = [raw_scores]
    return [float(score[0] if isinstance(score, list) else score) for score in raw_scores]


def _response(
    *,
    query: str,
    results: list[dict[str, Any]],
    timing: dict[str, float],
    recall_limit: int,
    limit: int,
    tier_counts: dict[str, int] | None = None,
    retrieval_backend: str = "in_memory",
    fallback_reason: str | None = None,
    vector_store_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "query": query,
        "mode": "local_reranker_sidecar_v1",
        "embedding_model": local_embedding_service.MODEL_NAME,
        "reranker_model": RERANKER_MODEL_NAME,
        "reranker_model_path": str(_model_path()),
        "retrieval_backend": retrieval_backend,
        "vector_store_status": vector_store_status,
        "fallback_reason": fallback_reason,
        "recall_limit": recall_limit,
        "limit": limit,
        "results": results,
        "timing": {key: round(value, 2) for key, value in timing.items()},
        "safety": {
            "db_write_performed": False,
            "external_llm_called": False,
            "zotero_write_performed": False,
        },
    }
    if tier_counts is not None:
        payload["tier_counts"] = tier_counts
    return payload


def _candidate_text(candidate: dict[str, Any]) -> str:
    return _compact_text(" ".join([
        str(candidate.get("title") or ""),
        str(candidate.get("heading_path") or ""),
        str(candidate.get("passage_text") or ""),
    ]))


def _set_local_cache_env() -> None:
    os.environ.setdefault("HF_HOME", str(DEFAULT_MODEL_CACHE))
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(DEFAULT_MODEL_CACHE / "hub"))
    os.environ.setdefault("TRANSFORMERS_CACHE", str(DEFAULT_MODEL_CACHE))
    os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", str(DEFAULT_MODEL_CACHE))
    os.environ.setdefault("TORCH_HOME", str(DEFAULT_MODEL_CACHE / "torch"))
    os.environ.setdefault("MARKER_CACHE_DIR", str(DEFAULT_MARKER_CACHE))


def _model_path() -> Path:
    return Path(
        os.environ.get("SEARCH_RERANKER_MODEL")
        or os.environ.get("NOTEBOOK_AI_RERANKER_MODEL_PATH")
        or DEFAULT_RERANKER_MODEL_PATH
    )


def _device_name() -> str | None:
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return None


def _classify_tier(item: dict[str, Any]) -> dict[str, Any]:
    heading = str(item.get("heading_path") or "")
    passage = str(item.get("passage_text") or "")
    rerank_score = float(item.get("rerank_score") or 0.0)

    is_ref_heading = _is_reference_heading(heading)
    is_ref_text = is_ref_heading and _looks_like_reference_text(passage)

    if is_ref_heading or is_ref_text:
        tier = "reference"
        tier_label = "参考文献脉络"
        tier_reason = "reference_heading" if is_ref_heading else "reference_like_text"
    elif rerank_score > 0:
        tier = "primary"
        tier_label = "核心命中"
        tier_reason = "positive_rerank_body"
    else:
        tier = "secondary"
        tier_label = "补充片段"
        tier_reason = "low_score_body"

    return {
        "tier": tier,
        "tier_label": tier_label,
        "tier_reason": tier_reason,
        "is_reference_context": tier == "reference",
        "is_low_confidence": tier == "secondary",
    }


def _is_reference_heading(heading: str) -> bool:
    if not heading:
        return False
    heading_lower = heading.lower()
    ref_keywords = ["references", "参考文献", "bibliography"]
    return any(kw in heading_lower for kw in ref_keywords)


def _looks_like_reference_text(passage: str) -> bool:
    """Lightweight heuristic: the text resembles a citation entry.

    Checks whether the passage contains author patterns (surname, initial),
    a year token, and conference/journal markers — typical of reference lists.
    This is only called when the heading already indicates a reference section.
    """
    if not passage or len(passage) < 30:
        return False
    text = _compact_text(passage)
    import re

    # Author-like: "Surname, X." or "J. Smith" or "et al"
    author_patterns = [
        r"[A-Z][a-z]+,\s+[A-Z]\.",          # "He, K."
        r"[A-Z]\.\s+[A-Z][a-z]+",            # "J. Smith"
        r"et\s+al\.?",                        # "et al"
    ]
    has_author = any(re.search(pat, text) for pat in author_patterns)

    # Year-like: 4-digit year in range 1900-2030
    has_year = bool(re.search(r"\b(19[5-9]\d|20[0-2]\d|2030)\b", text))

    # Conference/journal markers
    venue_markers = [
        "conference", "proceedings", "journal", "transactions",
        "ieee", "acm", "springer", "arxiv", "corr",
        "vol\\.", "pp\\.", "pages",
    ]
    lowers = text.lower()
    has_venue = any(marker in lowers for marker in venue_markers)

    return (has_author and has_year) or (has_author and has_venue) or (has_year and has_venue)


def _compact_text(value: str | None) -> str:
    return " ".join(str(value or "").split())


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000
