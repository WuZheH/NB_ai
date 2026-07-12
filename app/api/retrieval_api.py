from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.schemas.retrieval_search import (
    RetrievalSearchRequest,
    RetrievalSearchResponse,
)
from app.services.retrieval import fts_search_service, fts_status_service


router = APIRouter(prefix="/api/v1/retrieval", tags=["local-retrieval"])


@router.post("/search", response_model=RetrievalSearchResponse)
def search_retrieval(
    request: RetrievalSearchRequest,
) -> dict[str, Any]:
    try:
        return fts_search_service.search_retrieval(request)
    except fts_search_service.RetrievalIndexUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "retrieval_index_unavailable",
                "index_status": exc.status,
                **_safety_flags(),
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_retrieval_query",
                "message": str(exc),
                **_safety_flags(),
            },
        ) from exc


@router.get("/index/status", response_model=None)
def retrieval_index_status() -> dict[str, Any]:
    return fts_status_service.get_index_status()


def _safety_flags() -> dict[str, bool]:
    return {
        "db_write_performed": False,
        "production_db_write_performed": False,
        "zotero_db_write_performed": False,
        "vector_write_performed": False,
        "llm_called": False,
    }
