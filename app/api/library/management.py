from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.schemas.library_deletion import (
    ArchiveDocumentsRequest,
)
from app.services.library import book_archive_service
from app.services.library.local_mutation_security import (
    issue_mutation_session,
    require_mutation_token,
)


router = APIRouter(prefix="/management")


@router.post("/mutation-session")
def create_mutation_session(request: Request) -> dict[str, Any]:
    return issue_mutation_session(request)


@router.post("/archive")
def archive_documents(
    payload: ArchiveDocumentsRequest,
    request: Request,
) -> dict[str, Any]:
    require_mutation_token(request, rate_scope="archive_documents")
    try:
        return book_archive_service.archive_documents(payload.document_ids)
    except book_archive_service.ArchiveError as exc:
        raise _http_error(exc.error_code, str(exc), exc.status_code) from exc


@router.post("/restore")
def restore_documents(
    payload: ArchiveDocumentsRequest,
    request: Request,
) -> dict[str, Any]:
    require_mutation_token(request, rate_scope="restore_documents")
    try:
        return book_archive_service.restore_documents(payload.document_ids)
    except book_archive_service.ArchiveError as exc:
        raise _http_error(exc.error_code, str(exc), exc.status_code) from exc


def _http_error(error_code: str, message: str, status_code: int) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            "status": "error",
            "error_code": error_code,
            "message": message,
        },
    )
