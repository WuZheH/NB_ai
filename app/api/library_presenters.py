from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import FileResponse

from app.services import library_service, object_candidate_service


READ_STATUSES = {"read", "mastered"}
SNIPPET_MAX_CHARS = 220
SUMMARY_PLACEHOLDER = "暂无个人总结"
ZOTERO_OPEN_PDF_PREFIX = "zotero://open-pdf/"
LOCAL_FRONTEND_ORIGINS = {"http://127.0.0.1:5173", "http://localhost:5173"}
PDF_CORS_EXPOSE_HEADERS = "Content-Length, Content-Range, Accept-Ranges, Content-Type"


def _read_shelf_item(document: Any) -> dict[str, Any]:
    document_id = _value(document, "source_document_id") or _value(document, "document_id") or _value(document, "item_id")
    return {
        "document_id": document_id,
        "title": _value(document, "title", ""),
        "document_type": _value(document, "document_type") or "unknown",
        "read_status": _value(document, "read_status") or "unknown",
        "object_import_mode": _value(document, "object_import_mode"),
        "object_import_status": _value(document, "object_import_status"),
        "chapter_count": int(_value(document, "chapter_count", 0) or 0),
        "chunk_count": int(_value(document, "chunk_count", 0) or 0),
        "evidence_count": int(_value(document, "chunk_count", 0) or 0),
        "source_type": _source_type(document),
        "zotero_key": _value(document, "zotero_key"),
        "pdf_path": _value(document, "pdf_path"),
        **_source_open_fields(document, document_id=document_id, pdf_page=None),
        "summary": _summary_or_placeholder(_value(document, "summary")),
        "tags": list(_value(document, "tags", []) or []),
        "updated_at": _format_datetime(_value(document, "updated_at")),
    }


def _object_search_results(query: str, limit: int) -> list[dict[str, Any]]:
    try:
        payload = object_candidate_service.search_object_candidates(query, limit=max(1, min(limit, 20)))
    except Exception:
        return []
    return list(payload.get("objects") or [])


def _search_result_item(result: Any) -> dict[str, Any]:
    document_id = _value(result, "document_id")
    chunk_id = _chunk_id(result)
    pdf_page = _value(result, "pdf_page_start") or _value(result, "pdf_page")
    zotero_key = _value(result, "zotero_key")
    source_fields = _source_open_fields(result, document_id=document_id, pdf_page=pdf_page)
    locator_fields = _locator_contract_fields(result, pdf_page=pdf_page)
    return {
        "result_type": _normalize_result_type(_value(result, "result_type") or _value(result, "source_type")),
        "document_id": document_id,
        "chunk_id": chunk_id,
        "title": _value(result, "title") or _value(result, "document_title") or "",
        "snippet": _snippet(_value(result, "snippet", "")),
        "pdf_page": pdf_page,
        "page_start": pdf_page,
        **locator_fields,
        **source_fields,
        "source_trace": {
            "document_id": document_id,
            "chunk_id": chunk_id,
            "pdf_page": pdf_page,
            "zotero_key": zotero_key,
            **source_fields,
        },
    }


def _grouped_search_document_item(group: Any) -> dict[str, Any]:
    return {
        "document_id": _value(group, "document_id"),
        "document_title": _value(group, "document_title") or "",
        "document_type": _value(group, "document_type") or "unknown",
        "document_relevance_score": _value(group, "document_relevance_score", 0),
        "document_relevance_label": _value(group, "document_relevance_label") or "低相关",
        "match_reasons": list(_value(group, "match_reasons", []) or []),
        "top_chunks": [_grouped_search_chunk_item(chunk) for chunk in _value(group, "top_chunks", [])],
    }


def _grouped_search_chunk_item(chunk: Any) -> dict[str, Any]:
    document_id = _value(chunk, "document_id")
    chunk_id = _value(chunk, "chunk_id")
    pdf_page = _value(chunk, "pdf_page_start")
    source_fields = _source_open_fields(chunk, document_id=document_id, pdf_page=pdf_page)
    locator_fields = _locator_contract_fields(chunk, pdf_page=pdf_page)
    return {
        "chunk_id": chunk_id,
        "document_id": document_id,
        "document_title": _value(chunk, "document_title") or "",
        "heading_path": _value(chunk, "heading_path"),
        "section_path": list(_value(chunk, "section_path", []) or []),
        "section_label": _value(chunk, "section_label") or _section_fallback_label(pdf_page),
        "location_label": _value(chunk, "location_label") or _location_fallback_label(document_id, chunk_id, pdf_page),
        "heading_level": _value(chunk, "heading_level"),
        "pdf_page": pdf_page,
        "page_start": pdf_page,
        "snippet": _snippet(_value(chunk, "snippet", "")),
        "chunk_text": _value(chunk, "chunk_text", ""),
        **locator_fields,
        "relevance_score": _value(chunk, "relevance_score", 0),
        "relevance_label": _value(chunk, "relevance_label") or "低相关",
        "match_reasons": list(_value(chunk, "match_reasons", []) or []),
        "tags": list(_value(chunk, "tags", []) or []),
        **source_fields,
        "source_trace": {
            "document_id": document_id,
            "chunk_id": chunk_id,
            "pdf_page": pdf_page,
            "section_label": _value(chunk, "section_label") or _section_fallback_label(pdf_page),
            "location_label": _value(chunk, "location_label") or _location_fallback_label(document_id, chunk_id, pdf_page),
            **source_fields,
        },
    }


def _document_item(document: Any) -> dict[str, Any]:
    document_id = _value(document, "document_id")
    return {
        "document_id": document_id,
        "title": _value(document, "title", ""),
        "read_status": _value(document, "read_status") or "unknown",
        "document_type": _value(document, "document_type") or "unknown",
        "object_import_mode": _value(document, "object_import_mode"),
        "object_import_status": _value(document, "object_import_status"),
        "zotero_key": _value(document, "zotero_key"),
        "pdf_path": _value(document, "pdf_path"),
        **_source_open_fields(document, document_id=document_id, pdf_page=None),
        "summary": _summary_or_placeholder(_value(document, "summary")),
        "tags": list(_value(document, "tags", []) or []),
        "evidence_count": _value(document, "chunk_count", 0),
        "note_count": _value(document, "note_count", 0),
    }


def _evidence_preview_item(document: Any, item: Any) -> dict[str, Any]:
    pdf_page = _value(item, "pdf_page_start") or _value(item, "pdf_page")
    chunk_id = _value(item, "chunk_id")
    document_id = _value(document, "document_id")
    source_fields = _source_open_fields(
        item,
        document_id=document_id,
        pdf_page=pdf_page,
        item_key=_value(document, "zotero_key"),
    )
    locator_fields = _locator_contract_fields(item, pdf_page=pdf_page)
    return {
        "chunk_id": chunk_id,
        "snippet": _snippet(_value(item, "snippet", "")),
        "pdf_page": pdf_page,
        "page_start": pdf_page,
        "section_title": _value(item, "heading_path"),
        **locator_fields,
        **source_fields,
        "source_trace": {
            "document_id": document_id,
            "chunk_id": chunk_id,
            "pdf_page": pdf_page,
            "zotero_key": _value(document, "zotero_key"),
            **source_fields,
        },
    }


def _chunk_detail_item(chunk: Any) -> dict[str, Any]:
    pdf_page = _value(chunk, "pdf_page_start") or _value(chunk, "pdf_page")
    chunk_id = _value(chunk, "chunk_id")
    document_id = _value(chunk, "document_id")
    source_fields = _source_open_fields(chunk, document_id=document_id, pdf_page=pdf_page)
    chunk_text = _value(chunk, "chunk_text", _value(chunk, "full_text", _value(chunk, "snippet", "")))
    locator_fields = _locator_contract_fields(chunk, pdf_page=pdf_page, chunk_text=chunk_text)
    return {
        "chunk_id": chunk_id,
        "document_id": document_id,
        "title": _value(chunk, "document_title") or _value(chunk, "title") or "",
        "chunk_text": chunk_text,
        "full_text": chunk_text,
        "snippet": _snippet(_value(chunk, "snippet", "")),
        "pdf_page": pdf_page,
        "page_start": pdf_page,
        "section_id": _value(chunk, "section_id"),
        "section_title": _value(chunk, "heading_path") or _value(chunk, "section_title"),
        "heading_path": _value(chunk, "heading_path"),
        **locator_fields,
        **source_fields,
        "source_trace": {
            "document_id": document_id,
            "chunk_id": chunk_id,
            "pdf_page": pdf_page,
            "zotero_key": _value(chunk, "zotero_key"),
            **source_fields,
        },
    }


def _note_preview_item(note: Any) -> dict[str, Any]:
    return {
        "note_id": _value(note, "note_id"),
        "title": _value(note, "title", ""),
        "note_type": _value(note, "note_type") or "unknown",
        "summary": _summary_or_placeholder(_value(note, "summary")),
        "snippet": _snippet(_value(note, "snippet", "")),
    }


def _related_note_item(note: Any) -> dict[str, Any]:
    return _note_preview_item(note)


def _relation_item(relation: Any) -> dict[str, Any]:
    source_type = _value(relation, "source_type")
    source_id = _value(relation, "source_id")
    relation_type = _value(relation, "relation_type")
    target_type = _value(relation, "target_type")
    target_id = _value(relation, "target_id")
    raw_relation = _raw_relation(source_type, source_id, relation_type, target_type, target_id)
    return {
        "relation_id": _value(relation, "relation_id"),
        "source_type": source_type,
        "source_id": source_id,
        "source_label": _value(relation, "source_label") or _entity_fallback(source_type, source_id),
        "relation_type": relation_type,
        "relation_label_zh": _value(relation, "relation_label_zh") or _relation_label_zh(relation_type),
        "target_type": target_type,
        "target_id": target_id,
        "target_label": _value(relation, "target_label") or _entity_fallback(target_type, target_id),
        "evidence_chunk_id": _value(relation, "evidence_chunk_id"),
        "evidence_pdf_page": _value(relation, "evidence_pdf_page"),
        "confidence": _value(relation, "confidence"),
        "description": _value(relation, "description"),
        "raw_relation": _value(relation, "raw_relation") or raw_relation,
    }


def _section_fallback_label(pdf_page: Any) -> str:
    return f"p.{pdf_page} · 未识别章节" if pdf_page else "未识别章节"


def _location_fallback_label(document_id: Any, chunk_id: Any, pdf_page: Any) -> str:
    parts = [f"doc{document_id}" if document_id else "doc unknown", f"chunk {chunk_id}" if chunk_id else "chunk unknown"]
    if pdf_page:
        parts.append(f"p.{pdf_page}")
    return " · ".join(parts)


def _zotero_link_candidate_item(candidate: Any) -> dict[str, Any]:
    return {
        "candidate_status": _value(candidate, "candidate_status"),
        "zotero_item_key": _value(candidate, "zotero_item_key"),
        "zotero_attachment_key": _value(candidate, "zotero_attachment_key"),
        "zotero_select_uri": _value(candidate, "zotero_select_uri"),
        "zotero_open_pdf_uri_template": _value(candidate, "zotero_open_pdf_uri_template"),
        "match_method": _value(candidate, "match_method"),
        "confidence": _value(candidate, "confidence"),
        "reason": _value(candidate, "reason"),
        "warnings": list(_value(candidate, "warnings", []) or []),
    }


def _zotero_annotation_candidate_item(candidate: Any) -> dict[str, Any]:
    return {
        "candidate_status": _value(candidate, "candidate_status"),
        "document_id": _value(candidate, "document_id"),
        "chunk_id": _value(candidate, "chunk_id"),
        "zotero_attachment_key": _value(candidate, "zotero_attachment_key"),
        "zotero_annotation_key": _value(candidate, "zotero_annotation_key"),
        "annotation_text": _value(candidate, "annotation_text"),
        "annotation_comment": _value(candidate, "annotation_comment"),
        "annotation_page": _value(candidate, "annotation_page"),
        "annotation_position": _value(candidate, "annotation_position"),
        "match_method": _value(candidate, "match_method"),
        "confidence": _value(candidate, "confidence"),
        "warnings": list(_value(candidate, "warnings", []) or []),
        "zotero_annotation_uri_candidate": _value(candidate, "zotero_annotation_uri_candidate"),
    }


def _pdf_location_item(location: Any) -> dict[str, Any]:
    return {
        "status": _value(location, "status"),
        "locator_status": _value(location, "locator_status") or _value(location, "status"),
        "locator_reason": _value(location, "locator_reason"),
        "is_metadata_chunk": bool(_value(location, "is_metadata_chunk", False)),
        "is_locatable": bool(_value(location, "is_locatable", False)),
        "document_id": _value(location, "document_id"),
        "chunk_id": _value(location, "chunk_id"),
        "pdf_page": _value(location, "pdf_page"),
        "page_index": _value(location, "page_index"),
        "match_method": _value(location, "match_method"),
        "confidence": _value(location, "confidence"),
        "rects": list(_value(location, "rects", []) or []),
        "page_width": _value(location, "page_width"),
        "page_height": _value(location, "page_height"),
        "snippet_used": _snippet(_value(location, "snippet_used", "")),
        "warnings": list(_value(location, "warnings", []) or []),
        "highlight_count": _value(location, "highlight_count", len(list(_value(location, "rects", []) or []))),
        "matched_term": _value(location, "matched_term"),
        "matched_lines": list(_value(location, "matched_lines", []) or []),
        "original_pdf_page": _value(location, "original_pdf_page"),
        "corrected_pdf_page": _value(location, "corrected_pdf_page"),
        "page_metadata_mismatch": bool(_value(location, "page_metadata_mismatch", False)),
        "visual_mode": _value(location, "visual_mode"),
        "is_exact_text_highlight": bool(_value(location, "is_exact_text_highlight", False)),
        "is_layout_text_highlight": bool(_value(location, "is_layout_text_highlight", False)),
        "approximate_region": _value(location, "approximate_region"),
        "page_text_length": _value(location, "page_text_length"),
    }


def _locator_contract_fields(item: Any, pdf_page: Any = None, chunk_text: Any = None) -> dict[str, Any]:
    explicit_status = _value(item, "locator_status")
    if explicit_status:
        return {
            "is_metadata_chunk": bool(_value(item, "is_metadata_chunk", False)),
            "is_locatable": bool(_value(item, "is_locatable", False)),
            "locator_status": explicit_status,
            "locator_reason": _value(item, "locator_reason") or "",
            "match_method": _value(item, "match_method") or "not_applicable",
            "highlight_count": _value(item, "highlight_count", 0),
        }
    text = chunk_text if chunk_text is not None else _value(item, "chunk_text", _value(item, "full_text", _value(item, "snippet", "")))
    contract = library_service.evidence_locator_contract(
        chunk_text=text,
        pdf_page_start=pdf_page,
        pdf_path=_value(item, "pdf_path") or _value(item, "source_pdf_path"),
        is_metadata=library_service.is_metadata_chunk_text(text),
    )
    return dict(contract)


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _source_type(document: Any) -> str:
    explicit = _value(document, "source_type")
    if explicit:
        return explicit
    if _value(document, "zotero_key"):
        return "zotero"
    if _value(document, "pdf_path") or _value(document, "has_pdf"):
        return "pdf"
    return "unknown"


def _summary_or_placeholder(summary: Any) -> str:
    if isinstance(summary, str) and summary.strip():
        return summary
    return SUMMARY_PLACEHOLDER


def _format_datetime(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat()
    if value is None:
        return None
    return str(value)


def _snippet(value: Any) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= SNIPPET_MAX_CHARS:
        return text
    return text[: SNIPPET_MAX_CHARS - 3].rstrip() + "..."


def _normalize_result_type(value: Any) -> str:
    text = str(value or "document")
    if "chunk" in text:
        return "chunk"
    if "note" in text:
        return "note"
    return "document"


def _chunk_id(result: Any) -> int | None:
    if _normalize_result_type(_value(result, "result_type") or _value(result, "source_type")) == "chunk":
        return _value(result, "id") or _value(result, "chunk_id")
    return _value(result, "chunk_id")


def _source_open_fields(
    item: Any,
    document_id: int | None,
    pdf_page: int | None,
    item_key: Any = None,
) -> dict[str, Any]:
    zotero_item_key = _clean_key(item_key) or _clean_key(_value(item, "zotero_item_key")) or _clean_key(_value(item, "zotero_key"))
    zotero_attachment_key = _clean_key(_value(item, "zotero_attachment_key"))
    explicit_open_pdf = _clean_zotero_open_pdf_uri(_value(item, "zotero_open_pdf_uri") or _value(item, "zotero_open_url"))
    zotero_open_pdf_uri = _build_zotero_open_pdf_uri(zotero_attachment_key, pdf_page) or explicit_open_pdf
    zotero_select_uri = _build_zotero_select_uri(zotero_item_key)
    pdf_fallback_url = _build_pdf_fallback_url(document_id, pdf_page)

    if zotero_open_pdf_uri:
        label = (
            f"Open in Zotero at page {pdf_page} / 在 Zotero 中打开第 {pdf_page} 页"
            if pdf_page
            else "Open in Zotero PDF / 在 Zotero 中打开 PDF"
        )
        return {
            "zotero_item_key": zotero_item_key,
            "zotero_attachment_key": zotero_attachment_key,
            "zotero_select_uri": zotero_select_uri,
            "zotero_open_pdf_uri": zotero_open_pdf_uri,
            "preferred_source_open_url": zotero_open_pdf_uri,
            "preferred_source_open_label": label,
            "pdf_fallback_url": pdf_fallback_url,
            "pdf_page": pdf_page,
        }
    if zotero_select_uri:
        return {
            "zotero_item_key": zotero_item_key,
            "zotero_attachment_key": zotero_attachment_key,
            "zotero_select_uri": zotero_select_uri,
            "zotero_open_pdf_uri": None,
            "preferred_source_open_url": zotero_select_uri,
            "preferred_source_open_label": "Show in Zotero / 在 Zotero 中定位条目",
            "pdf_fallback_url": pdf_fallback_url,
            "pdf_page": pdf_page,
        }
    return {
        "zotero_item_key": zotero_item_key,
        "zotero_attachment_key": zotero_attachment_key,
        "zotero_select_uri": None,
        "zotero_open_pdf_uri": None,
        "preferred_source_open_url": pdf_fallback_url,
        "preferred_source_open_label": _local_pdf_label(pdf_page) if pdf_fallback_url else "PDF unavailable",
        "pdf_fallback_url": pdf_fallback_url,
        "pdf_page": pdf_page,
    }


def _build_pdf_fallback_url(document_id: int | None, pdf_page: int | None) -> str | None:
    if not document_id:
        return None
    page_hash = f"#page={pdf_page}" if pdf_page else ""
    return f"/api/v1/library/documents/{document_id}/pdf{page_hash}"


def _build_zotero_open_pdf_uri(attachment_key: str | None, pdf_page: int | None) -> str | None:
    if not attachment_key:
        return None
    page_query = f"?page={pdf_page}" if pdf_page else ""
    return f"zotero://open-pdf/library/items/{attachment_key}{page_query}"


def _build_zotero_select_uri(item_key: str | None) -> str | None:
    if not item_key:
        return None
    return f"zotero://select/library/items/{item_key}"


def _clean_zotero_open_pdf_uri(value: Any) -> str | None:
    text = str(value or "").strip()
    if text.startswith(ZOTERO_OPEN_PDF_PREFIX):
        return text
    return None


def _clean_key(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _local_pdf_label(pdf_page: int | None) -> str:
    return "Open local PDF / 打开本地 PDF"


def _safe_pdf_filename(path: Path) -> str:
    name = path.name
    if name.lower().endswith(".pdf"):
        return name
    return "source.pdf"


def _apply_pdf_cors_headers(response: FileResponse, request: Request) -> None:
    origin = request.headers.get("origin")
    if origin in LOCAL_FRONTEND_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Expose-Headers"] = PDF_CORS_EXPOSE_HEADERS


RELATION_LABELS_ZH = {
    "measured_by": "使用评价指标",
    "evaluates_on": "评估于",
    "uses": "使用",
    "has_limitation": "存在局限",
    "addresses": "解决问题",
    "improves": "改进",
    "derived_from": "来源于",
    "related_to": "相关",
}


def _relation_label_zh(relation_type: Any) -> str:
    text = str(relation_type or "unknown")
    return RELATION_LABELS_ZH.get(text, text)


def _raw_relation(
    source_type: Any,
    source_id: Any,
    relation_type: Any,
    target_type: Any,
    target_id: Any,
) -> str:
    return f"{_entity_fallback(source_type, source_id)} {relation_type or 'unknown'} {_entity_fallback(target_type, target_id)}"


def _entity_fallback(entity_type: Any, entity_id: Any) -> str:
    return f"{entity_type or 'unknown'}:{entity_id if entity_id is not None else 'unknown'}"
