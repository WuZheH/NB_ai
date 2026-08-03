from __future__ import annotations

import math
import re
import time
from typing import Any

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import Document, KnowledgeChunk
from app.models.object_candidate import ObjectCandidate
from app.services import local_embedding_service, local_reranker_service
from app.services.library_service import READ_LIBRARY_STATUSES, is_metadata_chunk_text
from app.services.object_candidate_service import (
    OBJECT_SPECS,
    _build_db_candidate,
    _build_spec_candidate,
    _load_chunk_rows,
    _load_notes_by_chunk,
    _normalize_text,
)


OBJECT_PROFILE_MAX_CHARS = 800
OBJECT_EVIDENCE_SNIPPET_CHARS = 200
OBJECT_RECALL_LIMIT = 30
OBJECT_LIMIT = 50
OBJECT_CANDIDATE_POOL_LIMIT = 30
OBJECT_HARD_RETURN_CAP = 50
OBJECT_MAX_DISTANCE = None
OBJECT_MIN_EMBEDDING_SCORE = None
OBJECT_SCORE_DIRECTION = "distance_lower_is_better"

# Runtime cache: object_key -> embedding vector
_PROFILE_EMBEDDING_CACHE: dict[str, list[float]] = {}


def search_semantic_objects(
    query: str,
    recall_limit: int = OBJECT_RECALL_LIMIT,
    limit: int = OBJECT_LIMIT,
) -> dict[str, Any]:
    normalized_query = _compact_text(query)
    if not normalized_query:
        raise ValueError("query must not be empty.")

    started = time.perf_counter()
    safe_limit = max(1, min(int(limit or OBJECT_LIMIT), OBJECT_HARD_RETURN_CAP))
    safe_recall_limit = max(
        OBJECT_CANDIDATE_POOL_LIMIT,
        min(int(recall_limit or OBJECT_RECALL_LIMIT), OBJECT_HARD_RETURN_CAP),
        safe_limit,
    )
    safe_recall_limit = min(safe_recall_limit, OBJECT_HARD_RETURN_CAP)

    vector_payload = _search_semantic_objects_with_vector_store(
        normalized_query,
        recall_limit=safe_recall_limit,
        limit=safe_limit,
        started=started,
    )
    if vector_payload is not None:
        return vector_payload

    return _search_semantic_objects_in_memory(
        normalized_query,
        recall_limit=safe_recall_limit,
        limit=safe_limit,
        started=started,
        retrieval_backend="in_memory",
        fallback_reason=None,
        vector_store_status=None,
    )


def _search_semantic_objects_in_memory(
    normalized_query: str,
    *,
    recall_limit: int,
    limit: int,
    started: float | None = None,
    retrieval_backend: str,
    fallback_reason: str | None,
    vector_store_status: dict[str, Any] | None,
) -> dict[str, Any]:
    started = started or time.perf_counter()
    timings: dict[str, float] = {}

    # Load embedding model
    embed_model = local_embedding_service._load_model(timings)

    # Load all objects and build profiles
    load_start = time.perf_counter()
    all_objects = _load_all_objects()
    timings["load_objects_ms"] = _elapsed_ms(load_start)

    if not all_objects:
        return _response(
            query=normalized_query,
            results=[],
            timings=timings,
            retrieval_backend=retrieval_backend,
            fallback_reason=fallback_reason,
            vector_store_status=vector_store_status,
        )

    # Build profiles
    profile_start = time.perf_counter()
    profiles: list[tuple[dict[str, Any], str]] = []
    for obj in all_objects:
        profile_text = _build_object_profile(obj)
        if profile_text:
            profiles.append((obj, profile_text))
    timings["build_profiles_ms"] = _elapsed_ms(profile_start)

    if not profiles:
        return _response(
            query=normalized_query,
            results=[],
            timings=timings,
            retrieval_backend=retrieval_backend,
            fallback_reason=fallback_reason,
            vector_store_status=vector_store_status,
        )

    # Embed query
    query_embed_start = time.perf_counter()
    query_embedding = _encode_text(embed_model, normalized_query)
    timings["query_embedding_ms"] = _elapsed_ms(query_embed_start)

    # Embed profiles and compute cosine similarity
    search_start = time.perf_counter()
    scored: list[tuple[float, dict[str, Any], str]] = []
    for obj, profile_text in profiles:
        obj_key = str(obj.get("object_key") or obj.get("object_name") or "")
        profile_embedding = _cached_profile_embedding(embed_model, obj_key, profile_text)
        score = _cosine_similarity(query_embedding, profile_embedding)
        scored.append((score, obj, profile_text))
    scored.sort(key=lambda item: item[0], reverse=True)
    timings["object_embedding_ms"] = _elapsed_ms(search_start)

    recalled = scored[:recall_limit]
    # Rerank with CrossEncoder
    degraded_reason: str | None = None
    rerank_start = time.perf_counter()
    rerank_scores: list[float] = []
    try:
        if not recalled:
            rerank_scores = []
        else:
            reranker = local_reranker_service._load_reranker(timings)
            pairs = [(normalized_query, profile_text) for _, _, profile_text in recalled]
            rerank_scores = local_reranker_service._predict_scores(reranker, pairs)
    except (local_reranker_service.LocalRerankerUnavailable, Exception):
        degraded_reason = "object_reranker_unavailable"
        rerank_scores = [0.0] * len(recalled)

    timings["rerank_ms"] = _elapsed_ms(rerank_start)

    # Merge scores and sort
    merged: list[dict[str, Any]] = []
    for rank, ((embedding_score, obj, profile_text), rerank_score) in enumerate(zip(recalled, rerank_scores), start=1):
        exact_match = _exact_name_or_alias_match(normalized_query, obj, profile_text)
        relevance_label = _relevance_label_from_score(embedding_score)
        merged.append({
            **obj,
            "rank": rank,
            "embedding_score": round(float(embedding_score), 6),
            "normalized_score": round(float(embedding_score), 6),
            "relevance_label": relevance_label,
            "display_relevance_label": "高" if exact_match else relevance_label,
            "boosted_exact_match": exact_match,
            "boost_reason": "exact_name_or_alias" if exact_match else None,
            "effective_rank_score": -1.0 if exact_match else round(1.0 - float(embedding_score), 6),
            "score_direction": OBJECT_SCORE_DIRECTION,
            "passed_threshold": True,
            "hidden_reason": None,
            "search_source": "fallback_in_memory",
            "rerank_score": round(float(rerank_score), 4),
            "final_score": round(float(rerank_score if rerank_score != 0.0 or degraded_reason is None else embedding_score), 4),
            "object_profile_preview": _snippet(profile_text, 300),
        })

    if degraded_reason:
        merged.sort(key=lambda item: (0 if item.get("boosted_exact_match") else 1, -float(item["embedding_score"])))
    else:
        merged.sort(key=lambda item: (0 if item.get("boosted_exact_match") else 1, -float(item["rerank_score"])))
    _renumber_ranks(merged)

    top = merged[:limit]

    # Build final result items with evidence details
    results = [_build_result_item(item) for item in top]

    timings["total_ms"] = _elapsed_ms(started)

    response = _response(
        query=normalized_query,
        results=results,
        timings=timings,
        retrieval_backend=retrieval_backend,
        fallback_reason=fallback_reason,
        vector_store_status=vector_store_status,
        object_total_candidates=len(recalled),
        object_hidden_low_score_count=0,
    )
    if degraded_reason:
        response["degraded_reason"] = degraded_reason
    return response


def _search_semantic_objects_with_vector_store(
    normalized_query: str,
    *,
    recall_limit: int,
    limit: int,
    started: float,
) -> dict[str, Any] | None:
    try:
        from app.services import vector_store_service

        status = vector_store_service.check_vector_store_status()
        vector_status = _vector_status_summary(status)
        fallback_reason = _vector_store_fallback_reason(status, vector_store_service.OBJECT_TABLE)
        if fallback_reason:
            return _search_semantic_objects_in_memory(
                normalized_query,
                recall_limit=recall_limit,
                limit=limit,
                started=started,
                retrieval_backend="fallback_in_memory",
                fallback_reason=fallback_reason,
                vector_store_status=vector_status,
            )

        timings: dict[str, float] = {}
        recall_start = time.perf_counter()
        vector_payload = vector_store_service.search_object_vectors(normalized_query, limit=recall_limit, status=status)
        timings["object_vector_recall_ms"] = _elapsed_ms(recall_start)
        if vector_payload.get("status") != "ok":
            return _search_semantic_objects_in_memory(
                normalized_query,
                recall_limit=recall_limit,
                limit=limit,
                started=started,
                retrieval_backend="fallback_in_memory",
                fallback_reason="vector_table_missing",
                vector_store_status=vector_status,
            )

        vector_hits = list(vector_payload.get("results") or [])
        if not vector_hits:
            timings["total_ms"] = _elapsed_ms(started)
            return _response(
                query=normalized_query,
                results=[],
                timings=timings,
                retrieval_backend="lancedb",
                fallback_reason=None,
                vector_store_status=vector_status,
            )

        load_start = time.perf_counter()
        object_by_key = {
            str(obj.get("object_key") or obj.get("object_name") or "").strip(): obj
            for obj in _load_all_objects()
        }
        timings["load_objects_ms"] = _elapsed_ms(load_start)

        recalled: list[tuple[float, float | None, dict[str, Any], str]] = []
        for hit in vector_hits:
            object_key = str(hit.get("object_key") or "").strip()
            obj = object_by_key.get(object_key)
            if not obj:
                obj = _object_from_vector_hit(hit)
            profile_text = str(hit.get("object_profile_text") or hit.get("text_for_embedding") or "")
            raw_distance = _safe_float(hit.get("_distance"))
            recalled.append((_score_from_distance(raw_distance), raw_distance, obj, profile_text))

        degraded_reason: str | None = None
        rerank_start = time.perf_counter()
        try:
            if not recalled:
                rerank_scores = []
            else:
                reranker = local_reranker_service._load_reranker(timings)
                pairs = [(normalized_query, profile_text) for _, _, _, profile_text in recalled]
                rerank_scores = local_reranker_service._predict_scores(reranker, pairs)
        except (local_reranker_service.LocalRerankerUnavailable, Exception):
            degraded_reason = "object_reranker_unavailable"
            rerank_scores = [0.0] * len(recalled)
        timings["rerank_ms"] = _elapsed_ms(rerank_start)

        merged: list[dict[str, Any]] = []
        for rank, ((embedding_score, raw_distance, obj, profile_text), rerank_score) in enumerate(zip(recalled, rerank_scores), start=1):
            exact_match = _exact_name_or_alias_match(normalized_query, obj, profile_text)
            relevance_label = _relevance_label_from_distance(raw_distance)
            merged.append({
                **obj,
                "rank": rank,
                "embedding_score": round(float(embedding_score), 6),
                "raw_distance": raw_distance,
                "normalized_score": round(float(embedding_score), 6),
                "relevance_label": relevance_label,
                "display_relevance_label": "高" if exact_match else relevance_label,
                "boosted_exact_match": exact_match,
                "boost_reason": "exact_name_or_alias" if exact_match else None,
                "effective_rank_score": -1.0 if exact_match else (float(raw_distance) if raw_distance is not None else float("inf")),
                "score_direction": OBJECT_SCORE_DIRECTION,
                "passed_threshold": True,
                "hidden_reason": None,
                "search_source": "object_embeddings",
                "rerank_score": round(float(rerank_score), 4),
                "final_score": round(float(rerank_score if rerank_score != 0.0 or degraded_reason is None else embedding_score), 4),
                "object_profile_preview": _snippet(profile_text, 300),
            })

        merged.sort(key=lambda item: (
            0 if item.get("boosted_exact_match") else 1,
            float("inf") if item.get("raw_distance") is None else float(item["raw_distance"]),
        ))
        _renumber_ranks(merged)

        results = [_build_result_item(item) for item in merged[:limit]]
        timings["total_ms"] = _elapsed_ms(started)
        response = _response(
            query=normalized_query,
            results=results,
            timings=timings,
            retrieval_backend="lancedb",
            fallback_reason=None,
            vector_store_status=vector_status,
            object_total_candidates=len(recalled),
            object_hidden_low_score_count=0,
        )
        if degraded_reason:
            response["degraded_reason"] = degraded_reason
        return response
    except Exception:
        return _search_semantic_objects_in_memory(
            normalized_query,
            recall_limit=recall_limit,
            limit=limit,
            started=started,
            retrieval_backend="fallback_in_memory",
            fallback_reason="vector_search_failed",
            vector_store_status={"available": False, "stale": False, "reason": "vector_search_failed"},
        )


def reset_profile_cache() -> None:
    _PROFILE_EMBEDDING_CACHE.clear()


def _build_result_item(item: dict[str, Any]) -> dict[str, Any]:
    obj_type = str(item.get("object_type") or "other")
    return {
        "object_id": item.get("id"),
        "object_key": item.get("object_key") or "",
        "canonical_name": item.get("object_name") or "",
        "object_type": obj_type,
        "object_type_label": _object_type_label(obj_type),
        "description": item.get("description") or "",
        "document_id": item.get("document_id"),
        "document_title": _primary_document_title(item),
        "embedding_score": item.get("embedding_score", 0.0),
        "rank": item.get("rank"),
        "raw_distance": item.get("raw_distance"),
        "normalized_score": item.get("normalized_score", item.get("embedding_score", 0.0)),
        "relevance_label": item.get("relevance_label") or "",
        "display_relevance_label": item.get("display_relevance_label") or item.get("relevance_label") or "",
        "boosted_exact_match": bool(item.get("boosted_exact_match", False)),
        "boost_reason": item.get("boost_reason"),
        "effective_rank_score": item.get("effective_rank_score"),
        "score_direction": item.get("score_direction") or OBJECT_SCORE_DIRECTION,
        "passed_threshold": bool(item.get("passed_threshold", True)),
        "hidden_reason": item.get("hidden_reason"),
        "search_source": item.get("search_source") or item.get("retrieval_source") or "",
        "rerank_score": item.get("rerank_score", 0.0),
        "final_score": item.get("final_score", 0.0),
        "evidence_count": len(item.get("evidence_refs") or []),
        "representative_evidence": _representative_evidence(item.get("evidence_refs") or []),
        "object_profile_preview": item.get("object_profile_preview") or "",
        "topic_tags": list(item.get("topic_tags") or []),
        "problem_tags": list(item.get("problem_tags") or []),
        "mechanism_tags": list(item.get("mechanism_tags") or []),
        "inspiration_tags": list(item.get("inspiration_tags") or []),
        "status": item.get("status") or "candidate",
        "source": item.get("search_source") or item.get("source") or "",
        "object_source": item.get("source") or "",
        "aliases": list(item.get("aliases") or []),
    }


def _primary_document_title(item: dict[str, Any]) -> str:
    top_docs = item.get("top_documents") or []
    if top_docs:
        return str(top_docs[0].get("title") or "")
    evidence = item.get("evidence_refs") or []
    if evidence:
        return str(evidence[0].get("document_title") or "")
    return ""


def _representative_evidence(evidence_refs: list[dict[str, Any]], limit: int = 2) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen_chunks: set[int] = set()
    sorted_refs = sorted(
        [ref for ref in evidence_refs if isinstance(ref, dict)],
        key=lambda ref: (
            0 if ref.get("is_locatable") else 1,
            -(float(ref.get("score") or 0.0)),
            int(ref.get("chunk_id") or 0),
        ),
    )
    for ref in sorted_refs:
        chunk_id = ref.get("chunk_id")
        if not chunk_id:
            continue
        chunk_id_int = int(chunk_id)
        if chunk_id_int in seen_chunks:
            continue
        seen_chunks.add(chunk_id_int)
        selected.append({
            "chunk_id": chunk_id_int,
            "document_id": ref.get("document_id"),
            "snippet": _snippet(str(ref.get("snippet") or ref.get("chunk_text") or ""), OBJECT_EVIDENCE_SNIPPET_CHARS),
            "heading_path": ref.get("heading_path") or ref.get("section_title") or "",
            "pdf_page": ref.get("pdf_page") or ref.get("pdf_page_start"),
            "is_locatable": bool(ref.get("is_locatable")),
            "locator_status": ref.get("locator_status"),
        })
        if len(selected) >= limit:
            break
    return selected


def _load_all_objects() -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    # Load DB objects
    with SessionLocal() as session:
        rows = session.scalars(
            select(ObjectCandidate).where(ObjectCandidate.status == "candidate")
        ).all()

    for row in rows:
        key = row.object_key.strip().lower()
        if key in seen_keys:
            continue
        seen_keys.add(key)
        try:
            candidate = _build_db_candidate(row)
            objects.append(candidate)
        except Exception:
            continue

    # Load spec-derived objects (only if not already in DB)
    with SessionLocal() as session:
        chunk_rows = _load_chunk_rows(session)
        notes_by_chunk = _load_notes_by_chunk(session)
        for spec in OBJECT_SPECS:
            key = spec.object_key.strip().lower()
            if key in seen_keys:
                continue
            seen_keys.add(key)
            candidate = _build_spec_candidate(spec, chunk_rows, notes_by_chunk)
            if candidate.get("evidence_refs"):
                objects.append(candidate)

    return objects


def _build_object_profile(obj: dict[str, Any]) -> str:
    parts: list[str] = []

    # Object name
    name = str(obj.get("object_name") or "").strip()
    if name:
        parts.append(f"对象：{name}")

    # Object type with label
    obj_type = str(obj.get("object_type") or "")
    if obj_type:
        type_label = _object_type_label(obj_type)
        parts.append(f"类型：{obj_type}（{type_label}）")

    # Aliases
    aliases = list(obj.get("aliases") or [])
    if aliases:
        parts.append(f"别名：{'、'.join(str(a) for a in aliases[:6])}")

    # Tags
    tag_parts = []
    for tag_list, prefix in [
        (obj.get("topic_tags") or [], "主题"),
        (obj.get("problem_tags") or [], "问题"),
        (obj.get("mechanism_tags") or [], "机制"),
        (obj.get("inspiration_tags") or [], "灵感"),
    ]:
        if tag_list:
            tag_parts.append(f"{prefix}：{'、'.join(str(t) for t in tag_list[:5])}")
    if tag_parts:
        parts.append(f"标签：{'；'.join(tag_parts)}")

    # Description
    desc = str(obj.get("description") or "").strip()
    if desc:
        parts.append(f"说明：{desc}")

    # Source document title
    doc_title = _primary_document_title(obj)
    if doc_title:
        parts.append(f"来源论文：{doc_title}")

    # Representative evidence snippets
    evidence_refs = obj.get("evidence_refs") or []
    if evidence_refs:
        top_evidence = evidence_refs[:3]
        for i, ev in enumerate(top_evidence, 1):
            snippet = str(ev.get("snippet") or ev.get("chunk_text") or "")[:OBJECT_EVIDENCE_SNIPPET_CHARS]
            if snippet:
                parts.append(f"证据{i}：{snippet}")

    # Status
    status = str(obj.get("status") or "")
    review = str(obj.get("review_status") or "")
    if review == "accepted":
        parts.append("状态：已审核通过")
    elif status:
        parts.append(f"状态：{status}")

    profile = "\n".join(parts)
    return _compact_text(profile)[:OBJECT_PROFILE_MAX_CHARS]


def _cached_profile_embedding(model: Any, obj_key: str, profile_text: str) -> list[float]:
    if obj_key and obj_key in _PROFILE_EMBEDDING_CACHE:
        return _PROFILE_EMBEDDING_CACHE[obj_key]
    embedding = _encode_text(model, profile_text)
    if obj_key:
        _PROFILE_EMBEDDING_CACHE[obj_key] = embedding
    return embedding


def _encode_text(model: Any, text: str) -> list[float]:
    return local_embedding_service._encode_text(model, text)


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    return local_embedding_service._cosine_similarity(left, right)


def _object_type_label(obj_type: str) -> str:
    labels = {
        "method": "方法",
        "dataset": "数据集",
        "metric": "指标",
        "problem": "问题",
        "mechanism": "机制",
        "concept": "概念",
        "task": "任务",
        "contribution": "贡献",
        "limitation": "限制",
        "inspiration": "灵感",
        "experiment_setting": "实验设置",
        "method/concept": "方法/概念",
        "other": "其他",
    }
    normalized = str(obj_type or "other").strip().lower()
    return labels.get(normalized, "其他")


def _response(
    query: str,
    results: list[dict[str, Any]],
    timings: dict[str, float],
    *,
    retrieval_backend: str = "in_memory",
    fallback_reason: str | None = None,
    vector_store_status: dict[str, Any] | None = None,
    object_total_candidates: int | None = None,
    object_hidden_low_score_count: int | None = None,
) -> dict[str, Any]:
    total_candidates = len(results) if object_total_candidates is None else int(object_total_candidates)
    hidden_low_score = 0 if object_hidden_low_score_count is None else int(object_hidden_low_score_count)
    return {
        "status": "ok",
        "implementation_status": "connected",
        "query": query,
        "mode": "semantic_object_search_v1",
        "embedding_model": local_embedding_service.MODEL_NAME,
        "reranker_model": local_reranker_service.RERANKER_MODEL_NAME,
        "retrieval_backend": retrieval_backend,
        "vector_store_status": vector_store_status,
        "fallback_reason": fallback_reason,
        "object_total_candidates": total_candidates,
        "object_returned_count": len(results),
        "object_hidden_low_score_count": hidden_low_score,
        "object_score_direction": OBJECT_SCORE_DIRECTION,
        "object_threshold": None,
        "object_threshold_kind": "disabled",
        "object_min_normalized_score": OBJECT_MIN_EMBEDDING_SCORE,
        "results": results,
        "timing": {key: round(value, 2) for key, value in timings.items()},
        "safety": {
            "db_write_performed": False,
            "external_llm_called": False,
            "zotero_write_performed": False,
        },
        "production_write_enabled": False,
        "db_write_performed": False,
        "external_llm_called": False,
        "final_hypothesis_created": False,
    }


def _object_from_vector_hit(hit: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": hit.get("object_id"),
        "object_key": hit.get("object_key") or "",
        "object_name": hit.get("canonical_name") or "",
        "object_type": hit.get("object_type") or "other",
        "document_id": hit.get("document_id"),
        "top_documents": [{"title": hit.get("document_title") or ""}] if hit.get("document_title") else [],
        "evidence_refs": [],
        "aliases": [],
        "topic_tags": [],
        "problem_tags": [],
        "mechanism_tags": [],
        "inspiration_tags": [],
        "status": "candidate",
        "source": "vector_store",
    }


def _vector_store_fallback_reason(status: dict[str, Any], table_name: str) -> str | None:
    if not status.get("available"):
        reason = status.get("reason")
        if reason == "vector_manifest_missing":
            return "vector_store_unavailable"
        return str(reason or "vector_store_unavailable")
    freshness = status.get("freshness") or {}
    if status.get("stale") or (freshness and not freshness.get("complete")):
        return str(
            status.get("reason")
            or freshness.get("reason")
            or "vector_store_stale"
        )
    table = (status.get("tables") or {}).get(table_name) or {}
    if not table.get("exists"):
        return "vector_table_missing"
    return None


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


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _relevance_label_from_distance(distance: float | None) -> str:
    if distance is None:
        return "未知"
    if distance <= 0.70:
        return "高"
    if distance <= 0.95:
        return "中"
    return "低"


def _relevance_label_from_score(score: float | None) -> str:
    try:
        value = float(score or 0.0)
    except (TypeError, ValueError):
        return "未知"
    if value >= 0.62:
        return "高"
    if value >= 0.51:
        return "中"
    return "低"


def _exact_name_or_alias_match(query: str, obj: dict[str, Any], profile_text: str = "") -> bool:
    normalized_query = _normalize_exact_match_token(query)
    if not normalized_query:
        return False
    return any(
        normalized_query == _normalize_exact_match_token(candidate)
        for candidate in _exact_match_candidates(obj, profile_text)
    )


def _exact_match_candidates(obj: dict[str, Any], profile_text: str = "") -> list[str]:
    fields: list[str] = []
    for key in ("object_name", "canonical_name", "display_name", "name", "object_key"):
        value = obj.get(key)
        if value:
            fields.append(str(value))
    for key in ("aliases", "topic_tags", "problem_tags", "mechanism_tags", "inspiration_tags", "tags"):
        for value in obj.get(key) or []:
            if value:
                fields.append(str(value))
    fields.extend(_profile_alias_candidates(profile_text))
    return fields


def _profile_alias_candidates(profile_text: str) -> list[str]:
    text = str(profile_text or "")
    if not text:
        return []
    candidates: list[str] = []
    match = re.search(r"别名[:：]\s*([^。；;\n]+)", text)
    if match:
        candidates.extend(part.strip() for part in re.split(r"[、,，/;；]", match.group(1)) if part.strip())
    return candidates


def _normalize_exact_match_token(value: Any) -> str:
    return "".join(ch for ch in str(value or "").strip().casefold() if ch not in {" ", "\t", "\r", "\n", "-", "_"})


def _renumber_ranks(items: list[dict[str, Any]]) -> None:
    for rank, item in enumerate(items, start=1):
        item["rank"] = rank


def _snippet(text: str, max_chars: int) -> str:
    compact = _compact_text(text)
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _compact_text(value: str | None) -> str:
    return " ".join(str(value or "").split())


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000
