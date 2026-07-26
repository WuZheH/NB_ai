from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.schemas.chat_tools import (
    DeleteDocumentRequest,
    DeletePreviewRequest,
    ImportDocumentRequest,
    ImportPreviewRequest,
    ListLibraryRequest,
)
from app.services import chat_tool_service


router = APIRouter(prefix="/api/v1/chat-tools", tags=["chat-tools"])


def require_chat_adapter(request: Request) -> None:
    client_host = str(request.client.host if request.client else "")
    if client_host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        _deny("chat_gateway_loopback_required", 403)
    if request.headers.get("x-forwarded-for") or request.headers.get("forwarded"):
        _deny("chat_gateway_forwarded_request_forbidden", 403)
    expected = os.environ.get("SEARCH_CHAT_GATEWAY_TOKEN", "").strip()
    if len(expected) < 32:
        _deny("chat_gateway_not_configured", 503)
    authorization = str(request.headers.get("authorization") or "")
    supplied = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    if not supplied or not hmac.compare_digest(supplied, expected):
        _deny("chat_gateway_authentication_failed", 401)
    adapter = str(request.headers.get("x-search-chat-adapter") or "")
    if adapter not in {"mcp", "actions"}:
        _deny("chat_gateway_adapter_required", 403)


@router.post("/list-library")
def list_library(payload: ListLibraryRequest, request: Request) -> dict[str, Any]:
    require_chat_adapter(request)
    return _call(
        chat_tool_service.list_library,
        scope=payload.scope,
        query=payload.query,
        document_type=payload.document_type,
        status=payload.status,
        limit=payload.limit,
    )


@router.post("/import-preview")
def import_preview(payload: ImportPreviewRequest, request: Request) -> dict[str, Any]:
    require_chat_adapter(request)
    return _call(
        chat_tool_service.import_preview,
        source_type=payload.source_type,
        inbox_filename=payload.inbox_filename,
        zotero_item_key=payload.zotero_item_key,
        zotero_attachment_key=payload.zotero_attachment_key,
    )


@router.post("/import-document")
def import_document(payload: ImportDocumentRequest, request: Request) -> dict[str, Any]:
    require_chat_adapter(request)
    return _call(
        chat_tool_service.import_document,
        confirmation_token=payload.confirmation_token,
        confirmed=payload.confirmed,
    )


@router.post("/delete-preview")
def delete_preview(payload: DeletePreviewRequest, request: Request) -> dict[str, Any]:
    require_chat_adapter(request)
    return _call(chat_tool_service.delete_preview, payload.document_id)


@router.post("/delete-document")
def delete_document(payload: DeleteDocumentRequest, request: Request) -> dict[str, Any]:
    require_chat_adapter(request)
    return _call(
        chat_tool_service.delete_document,
        confirmation_token=payload.confirmation_token,
        confirmed=payload.confirmed,
    )


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except chat_tool_service.ChatToolError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={
                "status": "error",
                "error_code": exc.error_code,
                "message": str(exc),
                **exc.details,
            },
        ) from exc


def _deny(error_code: str, status_code: int) -> None:
    raise HTTPException(
        status_code=status_code,
        detail={
            "status": "error",
            "error_code": error_code,
            "message": "Search chat gateway request was denied.",
        },
    )
