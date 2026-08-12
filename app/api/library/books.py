from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.schemas import safety_fields
from app.services import book_chapter_service, workspace_read_service


router = APIRouter()


@router.get("/books/{document_id}")
def get_book_detail(document_id: int) -> dict[str, Any]:
    try:
        payload = book_chapter_service.build_book_detail_payload(document_id)
    except book_chapter_service.BookDocumentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except book_chapter_service.NotBookDocument as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except book_chapter_service.BookChapterSchemaUnavailable as exc:
        return {
            "status": "schema_unavailable",
            "implementation_status": "contract_ready",
            "document_id": document_id,
            "message": str(exc),
            **safety_fields(),
        }
    return {
        "status": "ok",
        "implementation_status": "connected_read_only",
        **payload,
        **safety_fields(),
    }


@router.get("/books/{document_id}/chapters/{chapter_id}/workspace-state")
def get_book_chapter_workspace_state(document_id: int, chapter_id: int) -> dict[str, Any]:
    try:
        payload = workspace_read_service.build_workspace_state(
            document_id=document_id,
            chapter_id=chapter_id,
        )
    except workspace_read_service.WorkspaceReadError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        **payload,
        "implementation_status": "connected_read_only",
    }
