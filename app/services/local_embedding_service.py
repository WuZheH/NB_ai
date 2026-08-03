from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
from threading import Lock
import time
from typing import Any

from sqlalchemy import select

from app.core.paths import EMBEDDING_MODEL_PATH
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
from app.db.session import SessionLocal
from app.models import Document, KnowledgeChunk
from app.services.library_service import READ_LIBRARY_STATUSES, is_metadata_chunk_text


MODEL_NAME = "Qwen3-Embedding-0.6B"
DEFAULT_MODEL_PATH = EMBEDDING_MODEL_PATH
MAX_CANDIDATE_TEXT_CHARS = 1200
PASSAGE_SNIPPET_CHARS = 320

_MODEL: Any | None = None
_MODEL_LOAD_MS: float | None = None
_EMBEDDING_CACHE: dict[tuple[int, str], list[float]] = {}
_MODEL_LOAD_LOCK = Lock()


class LocalEmbeddingUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class EmbeddingCandidate:
    chunk_id: int
    document_id: int
    title: str
    passage_text: str
    heading_path: str
    source_type: str = "chunk"
    pdf_page_start: int | None = None
    pdf_page_end: int | None = None


def search_embedding_sidecar(query: str, limit: int = 10) -> dict[str, Any]:
    normalized_query = _compact_text(query)
    if not normalized_query:
        raise ValueError("query must not be empty.")

    vector_payload = _search_vector_store_passages(normalized_query, limit=limit)
    if vector_payload is not None:
        return vector_payload

    return _search_embedding_sidecar_in_memory(
        normalized_query,
        limit=limit,
        retrieval_backend="in_memory",
        fallback_reason=None,
        vector_store_status=None,
    )


def _search_embedding_sidecar_in_memory(
    normalized_query: str,
    *,
    limit: int = 10,
    retrieval_backend: str,
    fallback_reason: str | None,
    vector_store_status: dict[str, Any] | None,
) -> dict[str, Any]:
    started = time.perf_counter()
    timings: dict[str, float] = {}
    model = _load_model(timings)
    candidates = _load_candidates()
    if not candidates:
        return _response(
            query=normalized_query,
            results=[],
            timings={**timings, "search_ms": _elapsed_ms(started)},
            retrieval_backend=retrieval_backend,
            fallback_reason=fallback_reason,
            vector_store_status=vector_store_status,
        )

    query_start = time.perf_counter()
    query_embedding = _encode_text(model, normalized_query)
    timings["query_embedding_ms"] = _elapsed_ms(query_start)

    search_start = time.perf_counter()
    scored = []
    for candidate in candidates:
        candidate_embedding = _candidate_embedding(model, candidate)
        score = _cosine_similarity(query_embedding, candidate_embedding)
        scored.append((score, candidate))
    scored.sort(key=lambda item: item[0], reverse=True)
    timings["search_ms"] = _elapsed_ms(search_start)

    safe_limit = max(1, min(int(limit or 10), 50))
    results = [_result_item(score, candidate) for score, candidate in scored[:safe_limit]]
    return _response(
        query=normalized_query,
        results=results,
        timings=timings,
        retrieval_backend=retrieval_backend,
        fallback_reason=fallback_reason,
        vector_store_status=vector_store_status,
    )


def _search_vector_store_passages(query: str, limit: int = 10) -> dict[str, Any] | None:
    try:
        from app.services import vector_store_service

        status = vector_store_service.check_vector_store_status()
        vector_status = _vector_status_summary(status)
        fallback_reason = _vector_store_fallback_reason(status, vector_store_service.PASSAGE_TABLE)
        if fallback_reason:
            return _search_embedding_sidecar_in_memory(
                query,
                limit=limit,
                retrieval_backend="fallback_in_memory",
                fallback_reason=fallback_reason,
                vector_store_status=vector_status,
            )

        started = time.perf_counter()
        payload = vector_store_service.search_passage_vectors(query, limit=limit, status=status)
        if payload.get("status") != "ok":
            return _search_embedding_sidecar_in_memory(
                query,
                limit=limit,
                retrieval_backend="fallback_in_memory",
                fallback_reason="vector_table_missing",
                vector_store_status=vector_status,
            )
        results = [_vector_passage_result(item) for item in payload.get("results") or []]
        results = _enrich_passage_page_metadata(results)
        return _response(
            query=query,
            results=results,
            timings={"vector_search_ms": _elapsed_ms(started)},
            retrieval_backend="lancedb",
            fallback_reason=None,
            vector_store_status=vector_status,
        )
    except Exception:
        return _search_embedding_sidecar_in_memory(
            query,
            limit=limit,
            retrieval_backend="fallback_in_memory",
            fallback_reason="vector_search_failed",
            vector_store_status={"available": False, "stale": False, "reason": "vector_search_failed"},
        )


def reset_runtime_cache() -> None:
    _EMBEDDING_CACHE.clear()


def initialize_embedding_model() -> None:
    _load_model({})


def shutdown_embedding_model() -> None:
    global _MODEL, _MODEL_LOAD_MS
    _MODEL = None
    _MODEL_LOAD_MS = None
    reset_runtime_cache()


def _load_model(timings: dict[str, float]) -> Any:
    global _MODEL, _MODEL_LOAD_MS
    if _MODEL is not None and model_state("embedding") == "ready":
        timings["load_model_ms"] = 0.0
        return _MODEL
    if model_state("embedding") == "failed":
        raise LocalEmbeddingUnavailable(model_error_code("embedding") or "model_load_failed")

    with _MODEL_LOAD_LOCK:
        if _MODEL is not None and model_state("embedding") == "ready":
            timings["load_model_ms"] = 0.0
            return _MODEL
        if model_state("embedding") == "failed":
            raise LocalEmbeddingUnavailable(model_error_code("embedding") or "model_load_failed")

        _set_local_cache_env()
        try:
            model_path = _model_path()
        except MachineConfigUnavailable as exc:
            raise LocalEmbeddingUnavailable(exc.error_code) from exc

        mark_model_loading("embedding")
        started = time.perf_counter()
        try:
            from sentence_transformers import SentenceTransformer

            candidate = SentenceTransformer(str(model_path), device=_device_name())
        except Exception as exc:  # pragma: no cover - depends on runtime environment
            mark_model_failed("embedding", "embedding_model_load_failed")
            raise LocalEmbeddingUnavailable("embedding_model_load_failed") from exc

        try:
            _validate_embedding(_raw_encode(candidate, "Search readiness check."))
        except Exception as exc:
            mark_model_failed("embedding", "embedding_model_self_check_failed")
            raise LocalEmbeddingUnavailable("embedding_model_self_check_failed") from exc

        _MODEL = candidate
        mark_model_ready("embedding")
        _MODEL_LOAD_MS = _elapsed_ms(started)
        timings["load_model_ms"] = _MODEL_LOAD_MS
        return _MODEL


def _load_candidates() -> list[EmbeddingCandidate]:
    with SessionLocal() as session:
        rows = session.execute(
            select(Document, KnowledgeChunk)
            .join(KnowledgeChunk, KnowledgeChunk.document_id == Document.id)
            .where(Document.read_status.in_(READ_LIBRARY_STATUSES))
            .order_by(Document.id, KnowledgeChunk.chunk_index, KnowledgeChunk.id)
        ).all()

    candidates: list[EmbeddingCandidate] = []
    for document, chunk in rows:
        text = _compact_text(chunk.chunk_text)
        if not text or is_metadata_chunk_text(text):
            continue
        candidates.append(
            EmbeddingCandidate(
                chunk_id=chunk.id,
                document_id=document.id,
                title=document.title,
                passage_text=text[:MAX_CANDIDATE_TEXT_CHARS],
                heading_path=_compact_text(chunk.heading_path),
                pdf_page_start=int(chunk.pdf_page_start) if chunk.pdf_page_start is not None else None,
                pdf_page_end=int(chunk.pdf_page_end) if chunk.pdf_page_end is not None else None,
            )
        )
    return candidates


def _candidate_embedding(model: Any, candidate: EmbeddingCandidate) -> list[float]:
    cache_key = (candidate.chunk_id, candidate.passage_text)
    if cache_key not in _EMBEDDING_CACHE:
        text = " ".join(part for part in [candidate.title, candidate.heading_path, candidate.passage_text] if part)
        _EMBEDDING_CACHE[cache_key] = _encode_text(model, text)
    return _EMBEDDING_CACHE[cache_key]


def _encode_text(model: Any, text: str) -> list[float]:
    try:
        values = _raw_encode(model, text)
        _validate_embedding(values)
        return values
    except Exception as exc:
        mark_model_failed("embedding", "embedding_model_inference_failed")
        raise LocalEmbeddingUnavailable("embedding_model_inference_failed") from exc


def _raw_encode(model: Any, text: str) -> list[float]:
    embedding = model.encode(
        [text],
        batch_size=1,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )[0]
    return [float(value) for value in embedding.tolist()]


def _validate_embedding(values: list[float]) -> None:
    if len(values) != 1024 or not all(math.isfinite(value) for value in values):
        raise LocalEmbeddingUnavailable("embedding_model_self_check_failed")


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def _result_item(score: float, candidate: EmbeddingCandidate) -> dict[str, Any]:
    return {
        "document_id": candidate.document_id,
        "chunk_id": candidate.chunk_id,
        "score": round(float(score), 6),
        "title": candidate.title,
        "passage_text": _snippet(candidate.passage_text, PASSAGE_SNIPPET_CHARS),
        "heading_path": candidate.heading_path,
        "source_type": candidate.source_type,
        "pdf_page": candidate.pdf_page_start,
        "pdf_page_start": candidate.pdf_page_start,
        "pdf_page_end": candidate.pdf_page_end,
        "page_start": candidate.pdf_page_start,
        "source_trace": {
            "selection_type": "evidence",
            "document_id": candidate.document_id,
            "chunk_id": candidate.chunk_id,
            "pdf_page": candidate.pdf_page_start,
        },
    }


def _response(
    query: str,
    results: list[dict[str, Any]],
    timings: dict[str, float],
    *,
    retrieval_backend: str = "in_memory",
    fallback_reason: str | None = None,
    vector_store_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "query": query,
        "model": MODEL_NAME,
        "mode": "local_embedding_sidecar_v1",
        "retrieval_backend": retrieval_backend,
        "vector_store_status": vector_store_status,
        "fallback_reason": fallback_reason,
        "results": results,
        "timing": {key: round(value, 2) for key, value in timings.items()},
        "safety": {
            "db_write_performed": False,
            "external_llm_called": False,
            "zotero_write_performed": False,
        },
    }


def _vector_passage_result(item: dict[str, Any]) -> dict[str, Any]:
    document_id = _int_or_none(item.get("document_id"))
    chunk_id = _int_or_none(item.get("chunk_id"))
    distance = item.get("_distance")
    score = _score_from_distance(distance)
    # LanceDB may carry pdf_page fields from newer builds; prefer those, null is fine
    lancedb_page = _int_or_none(item.get("pdf_page_start"))
    lancedb_page_end = _int_or_none(item.get("pdf_page_end"))
    return {
        "document_id": document_id,
        "chunk_id": chunk_id,
        "score": score,
        "embedding_score": score,
        "distance": float(distance) if distance is not None else None,
        "title": str(item.get("title") or ""),
        "passage_text": _snippet(str(item.get("passage_text") or item.get("text_for_embedding") or ""), PASSAGE_SNIPPET_CHARS),
        "heading_path": str(item.get("heading_path") or ""),
        "source_type": "chunk",
        "pdf_page": lancedb_page,
        "pdf_page_start": lancedb_page,
        "pdf_page_end": lancedb_page_end,
        "page_start": lancedb_page,
        "source_trace": {
            "selection_type": "evidence",
            "document_id": document_id,
            "chunk_id": chunk_id,
            "pdf_page": lancedb_page,
        },
    }


def _enrich_passage_page_metadata(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Batch query DB to fill pdf_page fields for results that lack them."""
    need_enrich = [r for r in results if r.get("pdf_page_start") is None and r.get("chunk_id")]
    if not need_enrich:
        return results
    try:
        from app.services import vector_store_service

        chunk_ids = [int(r["chunk_id"]) for r in need_enrich]
        metadata = vector_store_service.load_chunk_page_metadata(chunk_ids)
        for r in need_enrich:
            meta = metadata.get(int(r["chunk_id"]))
            if meta is not None:
                page = meta.get("pdf_page_start")
                r["pdf_page"] = page
                r["pdf_page_start"] = page
                r["pdf_page_end"] = meta.get("pdf_page_end")
                r["page_start"] = page
                if r.get("source_trace"):
                    r["source_trace"]["pdf_page"] = page
    except Exception:
        pass
    return results


def _vector_store_fallback_reason(status: dict[str, Any], table_name: str) -> str | None:
    from app.services import vector_store_service

    return vector_store_service.vector_table_fallback_reason(
        status,
        table_name,
    )


def _vector_status_summary(status: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "available": bool(status.get("available")),
        "stale": bool(status.get("stale")),
        "reason": status.get("reason"),
    }
    if status.get("freshness") is not None:
        summary["freshness"] = status["freshness"]
    return summary


def _score_from_distance(distance: Any) -> float:
    if distance is None:
        return 0.0
    try:
        value = max(0.0, float(distance))
    except (TypeError, ValueError):
        return 0.0
    return round(1.0 / (1.0 + value), 6)


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _set_local_cache_env() -> None:
    model_cache = _model_path().parent
    os.environ.setdefault("HF_HOME", str(model_cache))
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(model_cache / "hub"))
    os.environ.setdefault("TRANSFORMERS_CACHE", str(model_cache))
    os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", str(model_cache))
    os.environ.setdefault("TORCH_HOME", str(model_cache / "torch"))


def _model_path() -> Path:
    config = require_runtime_machine_config()
    assert config.embedding is not None
    return config.embedding.path


def _device_name() -> str | None:
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return None


def _snippet(text: str, max_chars: int) -> str:
    compact = _compact_text(text)
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _compact_text(value: str | None) -> str:
    return " ".join(str(value or "").split())


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000
