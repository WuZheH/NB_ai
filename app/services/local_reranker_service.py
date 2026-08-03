from __future__ import annotations

import os
from pathlib import Path
import math
import re
from threading import Lock
import time
from typing import Any

from app.core.paths import RERANKER_MODEL_PATH
from app.runtime.machine_config import (
    MachineConfigUnavailable,
    require_runtime_machine_config,
)
from app.runtime.model_readiness import (
    mark_model_failed,
    mark_model_loading,
    mark_model_ready,
    model_error_code,
    model_state,
)
from app.services import local_embedding_service
from app.services.retrieval.query_normalizer import (
    compact_identifier,
    normalize_query,
)


RERANKER_MODEL_NAME = "Qwen3-Reranker-0.6B"
DEFAULT_RERANKER_MODEL_PATH = RERANKER_MODEL_PATH
RERANKER_BATCH_SIZE = 8

_RERANKER: Any | None = None
_RERANKER_LOAD_MS: float | None = None
_RERANKER_LOAD_LOCK = Lock()


class LocalRerankerUnavailable(RuntimeError):
    pass


def search_reranker_sidecar(query: str, recall_limit: int = 20, limit: int = 10) -> dict[str, Any]:
    normalized_query = _compact_text(query)
    if not normalized_query:
        raise ValueError("query must not be empty.")

    started = time.perf_counter()
    safe_recall_limit = max(1, min(int(recall_limit or 20), 50))
    safe_limit = max(1, min(int(limit or 10), safe_recall_limit))

    query_variants = _query_recall_variants(normalized_query)
    embedding_started = time.perf_counter()
    recall_payloads = [
        (
            variant,
            local_embedding_service.search_embedding_sidecar(
                variant,
                limit=safe_recall_limit,
            ),
        )
        for variant in query_variants
    ]
    embedding_recall_ms = _elapsed_ms(embedding_started)
    candidates, variant_recall_count = _merge_variant_candidates(
        recall_payloads,
        limit=safe_recall_limit,
    )
    recall_payload = recall_payloads[0][1]
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
            query_variants=query_variants,
            variant_recall_count=variant_recall_count,
            deduplicated_candidate_count=0,
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
        query_variants=query_variants,
        variant_recall_count=variant_recall_count,
        deduplicated_candidate_count=len(candidates),
    )


def _load_reranker(timings: dict[str, float]) -> Any:
    global _RERANKER, _RERANKER_LOAD_MS
    if _RERANKER is not None and model_state("reranker") == "ready":
        timings["load_reranker_ms"] = 0.0
        return _RERANKER
    if model_state("reranker") == "failed":
        raise LocalRerankerUnavailable(model_error_code("reranker") or "model_load_failed")

    with _RERANKER_LOAD_LOCK:
        if _RERANKER is not None and model_state("reranker") == "ready":
            timings["load_reranker_ms"] = 0.0
            return _RERANKER
        if model_state("reranker") == "failed":
            raise LocalRerankerUnavailable(model_error_code("reranker") or "model_load_failed")

        _set_local_cache_env()
        try:
            model_path = _model_path()
        except MachineConfigUnavailable as exc:
            raise LocalRerankerUnavailable(exc.error_code) from exc

        mark_model_loading("reranker")
        started = time.perf_counter()
        try:
            from sentence_transformers import CrossEncoder

            candidate = CrossEncoder(str(model_path), device=_device_name())
        except Exception as exc:  # pragma: no cover - depends on runtime environment
            mark_model_failed("reranker", "reranker_model_load_failed")
            raise LocalRerankerUnavailable("reranker_model_load_failed") from exc

        try:
            scores = _raw_predict_scores(candidate, [("readiness", "readiness document")])
            if len(scores) != 1 or not math.isfinite(scores[0]):
                raise ValueError("reranker_self_check_invalid")
        except Exception as exc:
            mark_model_failed("reranker", "reranker_model_self_check_failed")
            raise LocalRerankerUnavailable("reranker_model_self_check_failed") from exc

        _RERANKER = candidate
        mark_model_ready("reranker")
        _RERANKER_LOAD_MS = _elapsed_ms(started)
        timings["load_reranker_ms"] = _RERANKER_LOAD_MS
        return _RERANKER


def initialize_reranker_model() -> None:
    _load_reranker({})


def shutdown_reranker_model() -> None:
    global _RERANKER, _RERANKER_LOAD_MS
    _RERANKER = None
    _RERANKER_LOAD_MS = None


def _predict_scores(reranker: Any, pairs: list[tuple[str, str]]) -> list[float]:
    try:
        scores = _raw_predict_scores(reranker, pairs)
        if len(scores) != len(pairs) or not all(math.isfinite(score) for score in scores):
            raise ValueError("reranker_scores_invalid")
        return scores
    except LocalRerankerUnavailable:
        raise
    except Exception as exc:
        mark_model_failed("reranker", "reranker_model_inference_failed")
        raise LocalRerankerUnavailable("reranker_model_inference_failed") from exc


def _raw_predict_scores(reranker: Any, pairs: list[tuple[str, str]]) -> list[float]:
    raw_scores = reranker.predict(
        pairs,
        batch_size=RERANKER_BATCH_SIZE,
        show_progress_bar=False,
    )
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
    query_variants: list[str] | None = None,
    variant_recall_count: dict[str, int] | None = None,
    deduplicated_candidate_count: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "query": query,
        "mode": "local_reranker_sidecar_v1",
        "embedding_model": local_embedding_service.MODEL_NAME,
        "reranker_model": RERANKER_MODEL_NAME,
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
    if query_variants is not None:
        payload["query_variants"] = list(query_variants)
    if variant_recall_count is not None:
        payload["variant_recall_count"] = dict(variant_recall_count)
    if deduplicated_candidate_count is not None:
        payload["deduplicated_candidate_count"] = int(
            deduplicated_candidate_count
        )
    if tier_counts is not None:
        payload["tier_counts"] = tier_counts
    return payload


def _candidate_text(candidate: dict[str, Any]) -> str:
    return _compact_text(" ".join([
        str(candidate.get("title") or ""),
        str(candidate.get("heading_path") or ""),
        str(candidate.get("passage_text") or ""),
    ]))


def _query_recall_variants(query: str) -> list[str]:
    normalized = normalize_query(query)
    variants = [query]
    compact = compact_identifier(normalized.normalized_query)
    if _looks_like_short_identifier(query):
        for value in (
            normalized.normalized_query,
            compact,
        ):
            candidate = _compact_text(value)
            if candidate and candidate.casefold() not in {
                existing.casefold() for existing in variants
            }:
                variants.append(candidate)
            if len(variants) == 3:
                break
    return variants


def _looks_like_short_identifier(query: str) -> bool:
    canonical = _compact_text(query).translate(
        str.maketrans(
            {
                "‐": "-",
                "‑": "-",
                "‒": "-",
                "–": "-",
                "—": "-",
                "―": "-",
            }
        )
    )
    dashed = re.fullmatch(
        r"([A-Za-z0-9]{1,3})-([A-Za-z0-9]{2,18})",
        canonical,
    )
    if dashed:
        prefix = dashed.group(1)
        return prefix.casefold() not in {"a", "i"}
    spaced = re.fullmatch(
        r"([A-Za-z])\s+([A-Za-z0-9]{2,18})",
        canonical,
    )
    return bool(
        spaced
        and spaced.group(1).casefold() not in {"a", "i"}
    )


def _merge_variant_candidates(
    recall_payloads: list[tuple[str, dict[str, Any]]],
    *,
    limit: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    bounded_limit = max(1, min(int(limit), 50))
    queues: list[tuple[str, list[dict[str, Any]]]] = []
    recall_counts: dict[str, int] = {}
    recall_details: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for variant, payload in recall_payloads:
        results = [dict(item) for item in list(payload.get("results") or [])]
        queues.append((variant, results))
        recall_counts[variant] = len(results)
        for rank, item in enumerate(results, start=1):
            recall_details.setdefault(_candidate_identity(item), []).append(
                {
                    "query_variant": variant,
                    "recall_rank": rank,
                    "embedding_score": float(item.get("score") or 0.0),
                }
            )

    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    offset = 0
    while len(merged) < bounded_limit:
        added = False
        for _variant, results in queues:
            if offset >= len(results):
                continue
            added = True
            candidate = results[offset]
            identity = _candidate_identity(candidate)
            if identity in seen:
                continue
            seen.add(identity)
            candidate["variant_recall"] = recall_details.get(identity, [])
            merged.append(candidate)
            if len(merged) == bounded_limit:
                break
        if not added:
            break
        offset += 1
    return merged, recall_counts


def _candidate_identity(candidate: dict[str, Any]) -> tuple[str, str]:
    document_id = str(candidate.get("document_id") or "")
    chunk_id = str(
        candidate.get("chunk_id")
        or candidate.get("source_id")
        or candidate.get("fragment_id")
        or ""
    )
    if not document_id or not chunk_id:
        return (
            document_id,
            "anonymous:" + str(id(candidate)),
        )
    return document_id, chunk_id


def _set_local_cache_env() -> None:
    model_cache = _model_path().parent
    os.environ.setdefault("HF_HOME", str(model_cache))
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(model_cache / "hub"))
    os.environ.setdefault("TRANSFORMERS_CACHE", str(model_cache))
    os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", str(model_cache))
    os.environ.setdefault("TORCH_HOME", str(model_cache / "torch"))


def _model_path() -> Path:
    config = require_runtime_machine_config()
    assert config.reranker is not None
    return config.reranker.path


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
