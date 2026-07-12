from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.services import database_search_service


router = APIRouter(prefix="/api/v1/search")


@router.get("/database", response_model=None)
def search_database(
    q: str = Query(..., min_length=1),
    document_id: int | None = Query(default=None),
    chapter_id: int | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=25),
    include_layers: str = Query(default="all"),
) -> Any:
    try:
        return database_search_service.build_database_search(
            query=q,
            document_id=document_id,
            chapter_id=chapter_id,
            limit=limit,
            include_layers=include_layers,
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "error": "invalid_request",
                "detail": str(exc),
                "query": q,
                **_safety_flags(),
            },
        )
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "error": "database_search_unavailable",
                "detail": str(exc),
                "query": q,
                "results": {
                    "evidence_chunks": [],
                    "zotero_notes": [],
                    "objects": [],
                    "mechanisms": [],
                },
                "counts": {
                    "evidence_chunks": 0,
                    "zotero_notes": 0,
                    "objects": 0,
                    "mechanisms": 0,
                },
                "warnings": ["database search backend unavailable"],
                **_safety_flags(),
            },
        )


def _safety_flags() -> dict[str, bool]:
    return {
        "db_write_performed": False,
        "zotero_write_performed": False,
        "zotero_db_write_performed": False,
        "llm_called": False,
        "object_candidates_generated": False,
        "relation_generated": False,
        "mechanism_generated": False,
        "vector_write_performed": False,
        "vector_store_write_performed": False,
    }
