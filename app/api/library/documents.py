from __future__ import annotations

from app.api.library.read_common import *  # noqa: F401,F403
from app.schemas.library_deletion import (
    DeleteDocumentRequest,
    DeleteDocumentsBatchRequest,
    DeletionPreviewRequest,
    DeletionOptions,
)
from app.services.library import document_deletion_service
from app.services.library.local_mutation_security import (
    require_local_renderer,
    require_mutation_token,
)


router = APIRouter()


@router.post("/documents/delete-batch")
def delete_documents_batch(
    payload: DeleteDocumentsBatchRequest,
    request: Request,
) -> dict[str, Any]:
    require_mutation_token(request, rate_scope="delete_documents_batch", rate_limit=5)
    try:
        return document_deletion_service.delete_documents_batch(
            document_ids=payload.document_ids,
            requests=[item.model_dump() for item in payload.requests],
            confirmation_text=payload.confirmation_text,
        )
    except document_deletion_service.DeletionError as exc:
        raise _deletion_http_error(exc) from exc


@router.get("/documents/{document_id}/deletion-preview")
def deletion_preview(
    document_id: int,
    request: Request,
    delete_managed_pdf: bool = Query(default=False),
) -> dict[str, Any]:
    require_local_renderer(request, rate_scope="deletion_preview", rate_limit=60)
    try:
        return document_deletion_service.create_deletion_preview(
            document_id,
            deletion_options=DeletionOptions(delete_managed_pdf=delete_managed_pdf),
        )
    except document_deletion_service.DeletionError as exc:
        raise _deletion_http_error(exc) from exc


@router.post("/documents/{document_id}/deletion-preview")
def deletion_preview_with_acknowledgment(
    document_id: int,
    payload: DeletionPreviewRequest,
    request: Request,
) -> dict[str, Any]:
    require_local_renderer(request, rate_scope="deletion_preview", rate_limit=60)
    try:
        return document_deletion_service.create_deletion_preview(
            document_id,
            deletion_options=payload.deletion_options,
            manual_preservation_acknowledgment=payload.manual_preservation_acknowledgment,
        )
    except document_deletion_service.DeletionError as exc:
        raise _deletion_http_error(exc) from exc


@router.post("/documents/{document_id}/delete")
def delete_document(
    document_id: int,
    payload: DeleteDocumentRequest,
    request: Request,
) -> dict[str, Any]:
    require_mutation_token(request, rate_scope="delete_document", rate_limit=10)
    if document_id != payload.document_id:
        raise HTTPException(
            status_code=422,
            detail={
                "status": "error",
                "error_code": "deletion_document_id_mismatch",
                "message": "路径 document ID 与请求正文不一致。",
            },
        )
    try:
        return document_deletion_service.delete_document(
            document_id=document_id,
            preview_token=payload.preview_token,
            expected_document_revision=payload.expected_document_revision,
            confirmation_text=payload.confirmation_text,
            deletion_options=payload.deletion_options,
            manual_preservation_acknowledgment=payload.manual_preservation_acknowledgment,
        )
    except document_deletion_service.DeletionError as exc:
        raise _deletion_http_error(exc) from exc


def _deletion_http_error(exc: document_deletion_service.DeletionError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={
            "status": "error",
            "error_code": exc.error_code,
            "message": str(exc),
            **exc.details,
        },
    )


@router.get("/documents/{document_id}/zotero-link-candidates")
def zotero_link_candidates(document_id: int) -> dict[str, Any]:
    try:
        result = zotero_linking_service.suggest_zotero_link_candidates(document_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception:
        result = zotero_linking_service.safe_unavailable_result(document_id)

    payload = {
        "status": result.status,
        "implementation_status": result.implementation_status,
        "document_id": result.document_id,
        "document_title": result.document_title,
        "candidates": [_zotero_link_candidate_item(candidate) for candidate in result.candidates],
        **safety_fields(),
    }
    if result.message:
        payload["message"] = result.message
    return payload


@router.get("/documents/{document_id}")
def document_detail(document_id: int) -> dict[str, Any]:
    try:
        document = library_service.show_library_document(document_id)
        evidence_items = library_service.show_library_evidence(document_id, limit=10)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        object_groups = object_candidate_service.document_object_groups(document_id)
    except Exception:
        object_groups = []

    return {
        "status": "ok",
        "implementation_status": "connected",
        "document": _document_item(document),
        "object_groups": object_groups,
        "evidence_preview": [_evidence_preview_item(document, item) for item in evidence_items],
        "notes_preview": [_note_preview_item(item) for item in _value(document, "related_notes", [])],
        "linked_relations": [_relation_item(item) for item in _value(document, "related_relations", [])],
        **safety_fields(),
    }
