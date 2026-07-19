from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import Document
from app.runtime.machine_config import require_runtime_machine_config
from app.services import local_reranker_service, object_semantic_search_service


DEFAULT_OBJECT_LIMIT = 50
DEFAULT_PASSAGE_RECALL_LIMIT = 30
DEFAULT_PASSAGE_LIMIT = 15


def search_high_quality(
    query: str,
    *,
    object_limit: int = DEFAULT_OBJECT_LIMIT,
    passage_recall_limit: int = DEFAULT_PASSAGE_RECALL_LIMIT,
    passage_limit: int = DEFAULT_PASSAGE_LIMIT,
) -> dict[str, Any]:
    normalized_query = _compact_text(query)
    if not normalized_query:
        raise ValueError("query must not be empty.")
    require_runtime_machine_config()

    started = time.perf_counter()
    safe_object_limit = max(1, min(int(object_limit or DEFAULT_OBJECT_LIMIT), 50))
    safe_passage_recall_limit = max(1, min(int(passage_recall_limit or DEFAULT_PASSAGE_RECALL_LIMIT), 50))
    safe_passage_limit = max(1, min(int(passage_limit or DEFAULT_PASSAGE_LIMIT), safe_passage_recall_limit))

    objects_payload = object_semantic_search_service.search_semantic_objects(
        normalized_query,
        recall_limit=max(safe_object_limit, object_semantic_search_service.OBJECT_CANDIDATE_POOL_LIMIT),
        limit=safe_object_limit,
    )
    reranker_payload = local_reranker_service.search_reranker_sidecar(
        normalized_query,
        recall_limit=safe_passage_recall_limit,
        limit=safe_passage_limit,
    )

    objects = [_object_item(item, objects_payload) for item in (objects_payload.get("results") or [])]
    papers = _group_passages_by_document(reranker_payload.get("results") or [])

    retrieval_backend = _merge_retrieval_backend(objects_payload, reranker_payload)
    vector_store_status = reranker_payload.get("vector_store_status") or objects_payload.get("vector_store_status")
    fallback_reason = reranker_payload.get("fallback_reason") or objects_payload.get("fallback_reason")
    degraded_reason = reranker_payload.get("degraded_reason") or objects_payload.get("degraded_reason")

    return {
        "status": "ok",
        "implementation_status": "connected",
        "query": normalized_query,
        "mode": "high_quality_search_v1",
        "retrieval_backend": retrieval_backend,
        "fallback_reason": fallback_reason,
        "vector_store_status": vector_store_status,
        "degraded_reason": degraded_reason,
        "objects": objects,
        "papers": papers,
        "debug": {
            "object_count": len(objects),
            "object_total_candidates": objects_payload.get("object_total_candidates", len(objects)),
            "object_returned_count": objects_payload.get("object_returned_count", len(objects)),
            "object_hidden_low_score_count": objects_payload.get("object_hidden_low_score_count", 0),
            "object_score_direction": objects_payload.get("object_score_direction"),
            "object_threshold": objects_payload.get("object_threshold"),
            "object_threshold_kind": objects_payload.get("object_threshold_kind"),
            "paper_count": len(papers),
            "passage_count": sum(paper["total_passage_count"] for paper in papers),
            "object_retrieval_backend": objects_payload.get("retrieval_backend"),
            "passage_retrieval_backend": reranker_payload.get("retrieval_backend"),
            "object_fallback_reason": objects_payload.get("fallback_reason"),
            "passage_fallback_reason": reranker_payload.get("fallback_reason"),
            "object_timing": objects_payload.get("timing") or {},
            "passage_timing": reranker_payload.get("timing") or {},
            "elapsed_ms": round(_elapsed_ms(started), 2),
        },
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


def _object_item(item: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    return {
        **item,
        "retrieval_backend": payload.get("retrieval_backend"),
        "fallback_reason": payload.get("fallback_reason"),
    }


def _group_passages_by_document(passages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[Any, dict[str, Any]] = {}
    document_ids = {
        passage.get("document_id") or passage.get("source_trace", {}).get("document_id")
        for passage in passages
        if passage.get("document_id") or passage.get("source_trace", {}).get("document_id")
    }
    document_metadata = _document_metadata_by_id(document_ids)
    for passage in passages:
        document_id = passage.get("document_id") or passage.get("source_trace", {}).get("document_id")
        if document_id is None:
            continue
        metadata = document_metadata.get(document_id, {})
        group = groups.setdefault(
            document_id,
            {
                "document_id": document_id,
                "title": metadata.get("title") or passage.get("title") or "Untitled document",
                "document_type": metadata.get("document_type") or passage.get("document_type") or "other",
                "object_import_mode": metadata.get("object_import_mode") or passage.get("object_import_mode"),
                "source_kind": metadata.get("document_type") or passage.get("source_kind") or "other",
                "best_score": 0.0,
                "max_rerank_score": 0.0,
                "primary_count": 0,
                "secondary_count": 0,
                "reference_count": 0,
                "total_passage_count": 0,
                "tiers": {"primary": [], "secondary": [], "reference": []},
                "top_passages": [],
            },
        )
        tier = passage.get("tier") if passage.get("tier") in {"primary", "secondary", "reference"} else "secondary"
        passage_item = _passage_item(passage, tier)
        group["tiers"][tier].append(passage_item)
        group[f"{tier}_count"] += 1
        group["total_passage_count"] += 1
        rerank_score = _score_value(passage.get("rerank_score"))
        group["max_rerank_score"] = max(group["max_rerank_score"], rerank_score)
        if tier == "primary":
            group["best_score"] = max(group["best_score"], rerank_score)

    for group in groups.values():
        for tier in ("primary", "secondary", "reference"):
            group["tiers"][tier].sort(key=lambda item: _score_value(item.get("rerank_score")), reverse=True)
        if not group["best_score"]:
            group["best_score"] = (
                _score_value(group["tiers"]["secondary"][0].get("rerank_score")) if group["tiers"]["secondary"]
                else _score_value(group["tiers"]["reference"][0].get("rerank_score")) if group["tiers"]["reference"]
                else 0.0
            )
        group["top_passages"] = (
            group["tiers"]["primary"][:3]
            + group["tiers"]["secondary"][: max(0, 3 - len(group["tiers"]["primary"][:3]))]
        )

    return sorted(
        groups.values(),
        key=lambda group: (
            1 if group["primary_count"] else 0,
            _score_value(group["best_score"]),
            1 if group["secondary_count"] else 0,
            _score_value(group["max_rerank_score"]),
        ),
        reverse=True,
    )


def _document_metadata_by_id(document_ids: set[Any]) -> dict[Any, dict[str, Any]]:
    normalized_ids: list[int] = []
    for document_id in document_ids:
        try:
            normalized_ids.append(int(document_id))
        except (TypeError, ValueError):
            continue
    if not normalized_ids:
        return {}
    with SessionLocal() as session:
        rows = session.execute(
            select(Document.id, Document.title, Document.document_type, Document.object_import_mode)
            .where(Document.id.in_(normalized_ids))
        ).all()
    metadata: dict[Any, dict[str, Any]] = {}
    for row in rows:
        item = {
            "title": row.title,
            "document_type": row.document_type,
            "object_import_mode": row.object_import_mode,
        }
        metadata[row.id] = item
        metadata[str(row.id)] = item
    return metadata


def _passage_item(passage: dict[str, Any], tier: str) -> dict[str, Any]:
    return {
        "document_id": passage.get("document_id"),
        "chunk_id": passage.get("chunk_id"),
        "passage_text": passage.get("passage_text") or "",
        "heading_path": passage.get("heading_path") or "",
        "embedding_score": float(passage.get("embedding_score") or passage.get("score") or 0.0),
        "rerank_score": float(passage.get("rerank_score") or 0.0),
        "tier": tier,
        "tier_label": _tier_label(tier),
        "tier_reason": passage.get("tier_reason") or "",
        "is_reference_context": bool(passage.get("is_reference_context") or tier == "reference"),
        "is_low_confidence": bool(passage.get("is_low_confidence") or tier == "secondary"),
        "pdf_page": passage.get("pdf_page") or passage.get("pdf_page_start"),
        "pdf_page_start": passage.get("pdf_page_start") or passage.get("pdf_page"),
        "pdf_page_end": passage.get("pdf_page_end"),
        "page_start": passage.get("page_start") or passage.get("pdf_page_start"),
        "source_trace": passage.get("source_trace") or {
            "selection_type": "evidence",
            "document_id": passage.get("document_id"),
            "chunk_id": passage.get("chunk_id"),
        },
    }


def _merge_retrieval_backend(objects_payload: dict[str, Any], reranker_payload: dict[str, Any]) -> str:
    backends = {objects_payload.get("retrieval_backend"), reranker_payload.get("retrieval_backend")}
    if "fallback_in_memory" in backends:
        return "fallback_in_memory"
    if backends == {"lancedb"}:
        return "lancedb"
    return str(reranker_payload.get("retrieval_backend") or objects_payload.get("retrieval_backend") or "in_memory")


def _tier_label(tier: str) -> str:
    if tier == "primary":
        return "核心命中"
    if tier == "reference":
        return "参考文献脉络"
    return "补充片段"


def _score_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _compact_text(value: str | None) -> str:
    return " ".join(str(value or "").split())


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000
