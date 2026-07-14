from __future__ import annotations

from typing import Any

from app.domains.retrieval.fragment_repository import (
    NOTEBOOK_SOURCE_TYPES,
    NotebookFragmentNotFound,
    _is_product_fragment,
    _pdf_url,
)
from app.domains.retrieval.result_contracts import (
    NotebookFragmentLocator,
    PdfHighlightRect,
)
from app.schemas.retrieval_fragment import RetrievalFragment
from app.services.retrieval.source_registry import RetrievalSourceRegistry


def get_notebook_fragment_locator(
    fragment_id: str,
    *,
    registry: RetrievalSourceRegistry | None = None,
) -> NotebookFragmentLocator:
    """Return only the safe, read-only metadata needed by PDF preview.

    The registry already owns NOTEBOOK_AI's fragment-to-document mapping.  This
    adapter deliberately does not accept file paths and never exposes the
    registry's source path fields.  ``pdf_page`` is the physical, one-based
    page number shared by the document endpoint and PDF.js.
    """

    cleaned = str(fragment_id or "").strip()
    if not cleaned:
        raise NotebookFragmentNotFound("fragment_id must not be empty")

    catalog = (registry or RetrievalSourceRegistry()).read(
        source_types=(
            "pdf_chunk",
            "zotero_highlight",
            "zotero_annotation_comment",
            "zotero_child_note",
            "zotero_inspiration_note",
        ),
    )
    by_id = {item.fragment_id: item for item in catalog.fragments}
    item = by_id.get(cleaned)
    if (
        item is None
        or item.source_type not in NOTEBOOK_SOURCE_TYPES
        or not _is_product_fragment(item)
    ):
        raise NotebookFragmentNotFound(f"Notebook fragment not found: {cleaned}")

    highlight_source = _highlight_source(item, by_id)
    bbox = _safe_bbox(highlight_source.bbox if highlight_source else item.bbox)
    rects = _rects_from_bbox(bbox)
    selected_text = _selected_text(item, highlight_source)
    pdf_page = item.page_number or (highlight_source.page_number if highlight_source else None)
    has_attachment = bool(item.zotero_attachment_key)
    has_document_pdf = item.source_type == "pdf_chunk" and item.document_id is not None
    has_pdf_mapping = bool(item.document_id is not None and (has_document_pdf or has_attachment))
    endpoint = _pdf_url(item.document_id, pdf_page) if has_pdf_mapping else None

    warnings = list(dict.fromkeys(item.warnings))
    if not has_pdf_mapping:
        warnings.append("fragment_pdf_mapping_missing")
    if not pdf_page:
        warnings.append("physical_page_unavailable")

    strategy = "none"
    if endpoint and pdf_page and rects:
        strategy = "bbox"
    elif endpoint and pdf_page and selected_text:
        strategy = "text"
    elif endpoint and pdf_page:
        strategy = "page"

    return NotebookFragmentLocator(
        fragment_id=item.fragment_id,
        source_type=item.source_type,
        document_id=item.document_id,
        document_title=item.title,
        zotero_item_key=item.zotero_item_key,
        zotero_attachment_key=item.zotero_attachment_key,
        zotero_annotation_key=item.zotero_annotation_key,
        pdf_page=pdf_page,
        page_index=pdf_page - 1 if pdf_page else None,
        page_label=item.page_label,
        bbox=bbox,
        rects=rects,
        selected_text=selected_text,
        locator_strategy=strategy,
        pdf_available=bool(endpoint),
        pdf_endpoint=endpoint,
        warnings=warnings,
    )


def _highlight_source(
    item: RetrievalFragment,
    by_id: dict[str, RetrievalFragment],
) -> RetrievalFragment | None:
    """Annotation comments inherit the selected text and rectangles of their highlight."""

    if item.source_type != "zotero_annotation_comment":
        return item
    parent = by_id.get(item.parent_fragment_id or "")
    return parent if parent and parent.source_type == "zotero_highlight" else item


def _selected_text(
    item: RetrievalFragment,
    highlight_source: RetrievalFragment | None,
) -> str | None:
    if item.source_type == "pdf_chunk":
        return _limited_text(item.text)
    if item.source_type == "zotero_annotation_comment" and highlight_source:
        return _limited_text(highlight_source.text)
    if item.source_type == "zotero_inspiration_note":
        return _limited_text(item.raw_metadata.get("selected_text"))
    return None


def _limited_text(value: Any, *, limit: int = 6000) -> str | None:
    text = str(value or "").strip()
    return text[:limit] or None


def _safe_bbox(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    page_index = _int_or_none(value.get("pageIndex"))
    rects = value.get("rects")
    if page_index is None and not isinstance(rects, list):
        return None
    result: dict[str, Any] = {}
    if page_index is not None and page_index >= 0:
        result["pageIndex"] = page_index
    if isinstance(rects, list):
        result["rects"] = [list(rect) for rect in rects if _rect_values(rect) is not None]
    return result or None


def _rects_from_bbox(bbox: dict[str, Any] | None) -> list[PdfHighlightRect]:
    if not bbox:
        return []
    result: list[PdfHighlightRect] = []
    for raw in bbox.get("rects", []):
        values = _rect_values(raw)
        if values is None:
            continue
        x0, y0, x1, y1 = values
        if x1 > x0 and y1 > y0:
            result.append(PdfHighlightRect(x0=x0, y0=y0, x1=x1, y1=y1))
    return result


def _rect_values(value: Any) -> tuple[float, float, float, float] | None:
    if isinstance(value, dict):
        raw = (value.get("x0"), value.get("y0"), value.get("x1"), value.get("y1"))
    elif isinstance(value, (list, tuple)) and len(value) == 4:
        raw = tuple(value)
    else:
        return None
    try:
        return tuple(float(part) for part in raw)  # type: ignore[return-value]
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
