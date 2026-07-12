from __future__ import annotations

from app.api.library.common import *  # noqa: F401,F403


router = APIRouter()


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
