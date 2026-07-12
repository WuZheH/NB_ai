from __future__ import annotations

from app.api.library.common import *  # noqa: F401,F403


router = APIRouter()


@router.get("/read-shelf")
def read_shelf(
    include_test_data: bool = False,
    limit: int = Query(default=100, ge=20, le=500),
) -> dict[str, Any]:
    try:
        documents = library_service.get_library_home(item_type="document", limit=limit)
    except Exception as exc:
        return {
            "status": "not_connected_yet",
            "implementation_status": "shell_only",
            "items": [],
            "message": f"Read Shelf backend not connected in Phase 16B: {exc}",
            **safety_fields(),
        }

    items = [
        _read_shelf_item(document)
        for document in documents
        if _value(document, "read_status") in READ_STATUSES
        and (include_test_data or not library_service.is_test_library_record(document))
    ]
    items = _annotate_read_shelf_duplicates(items)
    return {
        "status": "ok",
        "implementation_status": "connected",
        "items": items,
        **safety_fields(),
    }


@router.get("/search")
def search_library(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=20, ge=1, le=50),
    group_by: str | None = Query(default=None),
    mode: str | None = Query(default=None),
    limit_documents: int = Query(default=5, ge=1, le=20),
    limit_chunks_per_document: int = Query(default=5, ge=1, le=20),
    document_type: str | None = None,
    tag: str | None = None,
    include_test_data: bool = False,
) -> dict[str, Any]:
    object_results = _object_search_results(q, limit=limit_documents if group_by == "document" else min(limit, 10))
    if group_by == "document" or mode in {"hybrid", "hybrid_lexical_v1"}:
        try:
            grouped_results = library_service.search_library_grouped(
                q,
                limit_documents=limit_documents,
                limit_chunks_per_document=limit_chunks_per_document,
            )
        except Exception as exc:
            return {
                "status": "not_connected_yet",
                "implementation_status": "shell_only",
                "query": q,
                "mode": "hybrid_lexical_v1",
                "grouped": True,
                "object_first": True,
                "objects": object_results,
                "results": [],
                "message": f"Grouped library search backend not connected in Phase 18B: {exc}",
                **safety_fields(),
            }

        filtered_groups = []
        for group in grouped_results:
            if not include_test_data and library_service.is_test_library_record(
                {"title": _value(group, "document_title"), "document_type": _value(group, "document_type")}
            ):
                continue
            if document_type and _value(group, "document_type") != document_type:
                continue
            group_item = _grouped_search_document_item(group)
            if tag:
                group_item["top_chunks"] = [
                    chunk for chunk in group_item["top_chunks"] if tag in (chunk.get("tags") or [])
                ]
                if not group_item["top_chunks"]:
                    continue
            filtered_groups.append(group_item)

        return {
            "status": "ok",
            "implementation_status": "connected",
            "query": q,
            "mode": "hybrid_lexical_v1",
            "grouped": True,
            "object_first": True,
            "objects": object_results,
            "results": filtered_groups[:limit_documents],
            **safety_fields(),
        }

    try:
        raw_results = library_service.search_library(q, limit=limit)
    except Exception as exc:
        return {
            "status": "not_connected_yet",
            "implementation_status": "shell_only",
            "query": q,
            "object_first": True,
            "objects": object_results,
            "results": [],
            "message": f"Library search backend not connected in Phase 16B: {exc}",
            **safety_fields(),
        }

    results = []
    for result in raw_results:
        if not include_test_data and library_service.is_test_library_record(result):
            continue
        if document_type and _value(result, "document_type") != document_type:
            continue
        tags = list(_value(result, "tags", []) or [])
        if tag and tag not in tags:
            continue
        results.append(_search_result_item(result))
        if len(results) >= limit:
            break

    return {
        "status": "ok",
        "implementation_status": "connected",
        "query": q,
        "object_first": True,
        "objects": object_results,
        "results": results,
        **safety_fields(),
    }


@router.get("/search/embedding-sidecar")
def search_embedding_sidecar(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    try:
        payload = local_embedding_service.search_embedding_sidecar(q, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except local_embedding_service.LocalEmbeddingUnavailable as exc:
        return {
            "status": "local_embedding_unavailable",
            "implementation_status": "connected",
            "query": q,
            "model": local_embedding_service.MODEL_NAME,
            "mode": "local_embedding_sidecar_v1",
            "results": [],
            "message": str(exc),
            **safety_fields(),
        }
    except Exception as exc:
        return {
            "status": "local_embedding_error",
            "implementation_status": "connected",
            "query": q,
            "model": local_embedding_service.MODEL_NAME,
            "mode": "local_embedding_sidecar_v1",
            "results": [],
            "message": f"Local embedding sidecar failed: {exc}",
            **safety_fields(),
        }

    return {
        "status": "ok",
        "implementation_status": "connected",
        **payload,
        **safety_fields(),
    }


@router.get("/search/high-quality")
def search_high_quality(
    q: str = Query(..., min_length=1),
    object_limit: int = Query(default=50, ge=1, le=50),
    passage_recall_limit: int = Query(default=30, ge=1, le=50),
    passage_limit: int = Query(default=15, ge=1, le=50),
) -> dict[str, Any]:
    try:
        payload = high_quality_search_service.search_high_quality(
            q,
            object_limit=object_limit,
            passage_recall_limit=passage_recall_limit,
            passage_limit=passage_limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        return {
            "status": "high_quality_search_error",
            "implementation_status": "connected",
            "query": q,
            "mode": "high_quality_search_v1",
            "objects": [],
            "papers": [],
            "message": f"High-quality search failed: {exc}",
            **safety_fields(),
        }
    return {
        **payload,
        **safety_fields(),
    }


@router.get("/search/reranker-sidecar")
def search_reranker_sidecar(
    q: str = Query(..., min_length=1),
    recall_limit: int = Query(default=20, ge=1, le=50),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    try:
        payload = local_reranker_service.search_reranker_sidecar(q, recall_limit=recall_limit, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (local_embedding_service.LocalEmbeddingUnavailable, local_reranker_service.LocalRerankerUnavailable) as exc:
        return {
            "status": "local_reranker_unavailable",
            "implementation_status": "connected",
            "query": q,
            "mode": "local_reranker_sidecar_v1",
            "embedding_model": local_embedding_service.MODEL_NAME,
            "reranker_model": local_reranker_service.RERANKER_MODEL_NAME,
            "results": [],
            "message": str(exc),
            **safety_fields(),
        }
    except Exception as exc:
        return {
            "status": "local_reranker_error",
            "implementation_status": "connected",
            "query": q,
            "mode": "local_reranker_sidecar_v1",
            "embedding_model": local_embedding_service.MODEL_NAME,
            "reranker_model": local_reranker_service.RERANKER_MODEL_NAME,
            "results": [],
            "message": f"Local reranker sidecar failed: {exc}",
            **safety_fields(),
        }

    return {
        "status": "ok",
        "implementation_status": "connected",
        **payload,
        **safety_fields(),
    }


@router.get("/search/semantic-objects")
def search_semantic_objects(
    q: str = Query(..., min_length=1),
    recall_limit: int = Query(default=20, ge=1, le=50),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    try:
        payload = object_semantic_search_service.search_semantic_objects(
            q, recall_limit=recall_limit, limit=limit
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except local_embedding_service.LocalEmbeddingUnavailable as exc:
        return {
            "status": "semantic_objects_unavailable",
            "implementation_status": "connected",
            "query": q,
            "mode": "semantic_object_search_v1",
            "embedding_model": local_embedding_service.MODEL_NAME,
            "reranker_model": local_reranker_service.RERANKER_MODEL_NAME,
            "results": [],
            "message": str(exc),
            **safety_fields(),
        }
    except Exception as exc:
        return {
            "status": "semantic_objects_error",
            "implementation_status": "connected",
            "query": q,
            "mode": "semantic_object_search_v1",
            "embedding_model": local_embedding_service.MODEL_NAME,
            "reranker_model": local_reranker_service.RERANKER_MODEL_NAME,
            "results": [],
            "message": f"Semantic object search failed: {exc}",
            **safety_fields(),
        }

    return {
        "status": "ok",
        "implementation_status": "connected",
        **payload,
        **safety_fields(),
    }


@router.get("/vector-store/status")
def vector_store_status() -> dict[str, Any]:
    return {
        "status": "ok",
        "implementation_status": "connected",
        **vector_store_service.check_vector_store_status(),
        "worker": vector_store_worker.worker_status(),
        **vector_store_worker.vector_auto_sync_boundary(),
        **safety_fields(),
    }


def _annotate_read_shelf_duplicates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        key = _duplicate_key(item)
        if key:
            groups.setdefault(key, []).append(item)

    for key, group in groups.items():
        if len(group) <= 1:
            continue
        primary = sorted(group, key=lambda item: int(item.get("document_id") or 0))[0]
        group_id = f"dup-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]}"
        for item in group:
            item["duplicate_group_id"] = group_id
            item["duplicate_count"] = len(group)
            item["duplicate_primary_document_id"] = primary.get("document_id")
            item["duplicate_reason"] = "same_title_and_pdf_or_zotero_source"
            item["duplicate_warning"] = f"可能重复：{len(group)} 个副本，建议打开 document_id={primary.get('document_id')}。"
            item["is_duplicate_primary"] = item.get("document_id") == primary.get("document_id")
    for item in items:
        item.setdefault("duplicate_count", 1)
        item.setdefault("duplicate_group_id", None)
        item.setdefault("duplicate_primary_document_id", item.get("document_id"))
        item.setdefault("duplicate_reason", None)
        item.setdefault("duplicate_warning", None)
        item.setdefault("is_duplicate_primary", True)
    return items


def _duplicate_key(item: dict[str, Any]) -> str | None:
    title = " ".join(str(item.get("title") or "").strip().lower().split())
    pdf_path = str(item.get("pdf_path") or "").strip().replace("\\", "/").lower()
    zotero_key = str(item.get("zotero_key") or "").strip().lower()
    if title and pdf_path:
        return f"title_pdf:{title}|{pdf_path}"
    if title and zotero_key:
        return f"title_zotero:{title}|{zotero_key}"
    return None


@router.get("/vector-store/search-passages")
def vector_store_search_passages(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    try:
        payload = vector_store_service.search_passage_vectors(q, limit=limit)
    except Exception as exc:
        payload = {"status": "vector_search_unavailable", "results": [], "message": str(exc)}
    return {
        "implementation_status": "connected",
        "query": q,
        "backend": vector_store_service.BACKEND,
        **payload,
        **safety_fields(),
    }


@router.get("/vector-store/search-objects")
def vector_store_search_objects(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    try:
        payload = vector_store_service.search_object_vectors(q, limit=limit)
    except Exception as exc:
        payload = {"status": "vector_search_unavailable", "results": [], "message": str(exc)}
    return {
        "implementation_status": "connected",
        "query": q,
        "backend": vector_store_service.BACKEND,
        **payload,
        **safety_fields(),
    }
