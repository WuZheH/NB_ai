from __future__ import annotations

from app.api.library.common import *  # noqa: F401,F403


router = APIRouter()


@router.get("/evidence/{chunk_id}")
def evidence_detail(chunk_id: int) -> dict[str, Any]:
    try:
        chunk = library_service.show_library_chunk(chunk_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "status": "ok",
        "implementation_status": "connected",
        "evidence": _chunk_detail_item(chunk),
        "linked_notes": [_related_note_item(item) for item in _value(chunk, "related_notes", [])],
        "linked_relations": [_relation_item(item) for item in _value(chunk, "related_relations", [])],
        **safety_fields(),
    }


@router.get("/objects/search")
def search_object_candidates(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=10, ge=1, le=20),
) -> dict[str, Any]:
    return object_candidate_service.search_object_candidates(q, limit=limit)


@router.get("/objects/{object_key}")
def object_candidate_detail(object_key: str) -> dict[str, Any]:
    return object_candidate_service.get_object_candidate(object_key)


@router.get("/evidence/{chunk_id}/objects")
def evidence_object_candidates(chunk_id: int) -> dict[str, Any]:
    return object_candidate_service.objects_for_evidence(chunk_id)


@router.get("/evidence/{chunk_id}/zotero-annotation-candidates")
def zotero_annotation_candidates(chunk_id: int) -> dict[str, Any]:
    try:
        chunk = library_service.show_library_chunk(chunk_id)
        document_id = _value(chunk, "document_id")
        result = zotero_annotation_linking_service.find_annotation_candidates_for_evidence(document_id, chunk_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception:
        return {
            "status": "zotero_annotations_unavailable",
            "implementation_status": "connected",
            "chunk_id": chunk_id,
            "document_id": None,
            "candidates": [],
            "message": "Zotero annotations are unavailable in this read-only environment.",
            **safety_fields(),
        }

    return {
        "status": result.status,
        "implementation_status": result.implementation_status,
        "chunk_id": result.chunk_id,
        "document_id": result.document_id,
        "candidates": [_zotero_annotation_candidate_item(candidate) for candidate in result.candidates],
        **({"message": result.message} if result.message else {}),
        **safety_fields(),
    }


@router.get("/evidence/{chunk_id}/pdf-location")
def evidence_pdf_location(
    chunk_id: int,
    query: str | None = None,
    fallback_terms: list[str] = Query(default=[]),
) -> dict[str, Any]:
    try:
        chunk = library_service.show_library_chunk(chunk_id)
        document_id = _value(chunk, "document_id")
        terms = [query] if query else []
        terms.extend(fallback_terms or [])
        terms.extend([
            _value(chunk, "title"),
            _value(chunk, "heading_path"),
            _value(chunk, "snippet"),
        ])
        location = pdf_chunk_locator_service.locate_chunk_in_pdf_page(document_id, chunk_id, fallback_terms=terms)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception:
        location = pdf_chunk_locator_service.PdfChunkLocatorResult(
            status="pdf_unavailable",
            locator_status="pdf_missing",
            locator_reason="PDF 渲染或读取失败，无法定位该片段。",
            is_metadata_chunk=False,
            is_locatable=False,
            document_id=None,
            chunk_id=chunk_id,
            pdf_page=None,
            page_index=None,
            match_method="not_found",
            confidence="none",
            rects=[],
            page_width=None,
            page_height=None,
            snippet_used="",
            warnings=["pdf_locator_failed"],
            highlight_count=0,
        )

    return {
        "status": "ok"
        if location.locator_status
        in {
            "exact_text_location",
            "layout_line_location",
            "layout_sentence_location",
            "layout_block_location",
            "layout_bbox_location",
            "chunk_aligned",
            "partial_chunk_aligned",
            "fallback_term_found",
            "page_level_only",
        }
        else location.locator_status,
        "implementation_status": "connected",
        "location": _pdf_location_item(location),
        **safety_fields(),
    }
