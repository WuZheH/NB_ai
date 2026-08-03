from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domains.retrieval.fragment_repository import (
    NotebookFragmentNotFound,
    NotebookFragmentRecord,
    get_notebook_fragment_record,
)
from app.domains.retrieval.locator_contracts import FragmentLocator, LocatorStrategy
from app.domains.retrieval.result_contracts import NotebookFragment
from app.schemas.retrieval_fragment import RetrievalFragment
from app.services.retrieval.source_registry import RetrievalSourceRegistry


SELECTED_TEXT_LIMIT = 512


class FragmentLocatorNotFound(LookupError):
    """Raised when a public Search fragment has no repository record."""


@dataclass(frozen=True)
class _LocatorChoice:
    strategy: LocatorStrategy
    confidence: float


def get_fragment_locator(
    fragment_id: str,
    *,
    registry: RetrievalSourceRegistry | None = None,
) -> FragmentLocator:
    try:
        record = get_notebook_fragment_record(fragment_id, registry=registry)
    except NotebookFragmentNotFound as exc:
        # Keep a stable public error and avoid reflecting arbitrary IDs.
        raise FragmentLocatorNotFound("Search fragment locator was not found.") from exc
    return build_fragment_locator(record)


def build_fragment_locator(record: NotebookFragmentRecord) -> FragmentLocator:
    fragment = record.fragment
    source = record.source
    note_key = _note_key(fragment, source)
    selected_text, text_truncated = _locator_text(fragment)
    bbox = _safe_bbox(source.bbox)
    choice = _choose_strategy(
        fragment=fragment,
        note_key=note_key,
        bbox=bbox,
        selected_text=selected_text,
    )

    warnings: list[str] = []
    if text_truncated:
        warnings.append("selected_text_truncated")
    if not fragment.zotero_attachment_key and not note_key:
        warnings.append("zotero_mapping_unavailable")
    if choice.strategy in {"bbox", "text", "page"} and fragment.pdf_page is None:
        warnings.append("physical_page_unavailable")
    if choice.strategy == "page":
        if fragment.pdf_page is None:
            warnings.append("page_location_unavailable")
        elif selected_text is None:
            warnings.append("precise_highlight_unavailable")

    return FragmentLocator(
        fragment_id=fragment.fragment_id,
        source_type=fragment.source_type,
        document_id=fragment.document_id,
        document_title=fragment.document_title,
        zotero_item_key=fragment.zotero_item_key,
        zotero_attachment_key=fragment.zotero_attachment_key,
        zotero_annotation_key=fragment.zotero_annotation_key,
        zotero_note_key=note_key,
        pdf_page=fragment.pdf_page,
        page_label=fragment.page_label,
        bbox=bbox,
        selected_text=selected_text,
        locator_strategy=choice.strategy,
        locator_confidence=choice.confidence,
        warnings=list(dict.fromkeys(warnings)),
    )


def _choose_strategy(
    *,
    fragment: NotebookFragment,
    note_key: str | None,
    bbox: dict[str, Any] | None,
    selected_text: str | None,
) -> _LocatorChoice:
    if fragment.zotero_annotation_key:
        return _LocatorChoice("annotation", 1.0)
    if note_key:
        return _LocatorChoice("note", 1.0)
    if bbox and fragment.zotero_attachment_key:
        return _LocatorChoice("bbox", 0.98)
    if selected_text and fragment.zotero_attachment_key:
        return _LocatorChoice("text", 0.85)
    if fragment.pdf_page is not None and fragment.zotero_attachment_key:
        return _LocatorChoice("page", 0.65)
    if fragment.pdf_page is not None:
        return _LocatorChoice("page", 0.2)
    if selected_text:
        return _LocatorChoice("text", 0.1)
    return _LocatorChoice("page", 0.0)


def _locator_text(fragment: NotebookFragment) -> tuple[str | None, bool]:
    if fragment.source_type == "zotero_child_note":
        return None, False
    candidate = fragment.selected_text
    if fragment.source_type == "pdf_chunk":
        candidate = fragment.text
    cleaned = str(candidate or "").strip()
    if not cleaned:
        return None, False
    return cleaned[:SELECTED_TEXT_LIMIT], len(cleaned) > SELECTED_TEXT_LIMIT


def _note_key(fragment: NotebookFragment, source: RetrievalFragment) -> str | None:
    if fragment.source_type != "zotero_child_note":
        return None
    return _clean(source.source_record_id)


def _safe_bbox(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not value:
        return None
    result: dict[str, Any] = {}
    page_index = value.get("pageIndex")
    if isinstance(page_index, int) and not isinstance(page_index, bool) and page_index >= 0:
        result["pageIndex"] = page_index
    for key in ("rects", "paths"):
        sanitized = _numeric_tree(value.get(key), depth=0)
        if sanitized:
            result[key] = sanitized
    return result or None


def _numeric_tree(value: Any, *, depth: int) -> Any:
    if depth > 5:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, (list, tuple)):
        result = [
            item
            for item in (_numeric_tree(child, depth=depth + 1) for child in value[:200])
            if item is not None
        ]
        return result or None
    return None


def _clean(value: Any) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned or None
