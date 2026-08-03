from __future__ import annotations

from collections import defaultdict
from typing import Callable, Iterable

from app.schemas.retrieval_fragment import RetrievalFragment


_CONTEXT_LIMIT = 600


def build_fragment_contexts(
    fragments: Iterable[RetrievalFragment],
) -> list[RetrievalFragment]:
    """Attach context using source-native order and explicit links."""

    items = list(fragments)
    by_id = {item.fragment_id: item for item in items}
    by_annotation = {
        item.zotero_annotation_key: item
        for item in items
        if item.source_type == "zotero_highlight" and item.zotero_annotation_key
    }
    chunks = {
        int(item.source_record_id): item
        for item in items
        if item.source_type == "pdf_chunk" and item.source_record_id.isdigit()
    }
    updates: dict[str, RetrievalFragment] = {}

    _apply_neighbor_context(
        items,
        updates,
        source_type="pdf_chunk",
        group_key=lambda item: f"document:{item.document_id}",
        method="document_page_chunk_order",
    )
    _apply_neighbor_context(
        items,
        updates,
        source_type="zotero_highlight",
        group_key=lambda item: f"attachment:{item.zotero_library_id}:{item.zotero_attachment_key}",
        method="attachment_page_sort_index",
    )
    _apply_neighbor_context(
        items,
        updates,
        source_type="markdown_note",
        group_key=lambda item: str(item.raw_metadata.get("relative_path") or ""),
        method="markdown_block_order",
    )

    current = {item.fragment_id: updates.get(item.fragment_id, item) for item in items}
    for original in items:
        item = current[original.fragment_id]
        if item.source_type == "zotero_annotation_comment":
            parent = by_id.get(item.parent_fragment_id or "")
            if parent is not None:
                current[item.fragment_id] = _updated_context(
                    item,
                    before=parent.text,
                    after=None,
                    status="linked_source_context",
                    method="annotation_highlight_link",
                )
        elif item.source_type == "zotero_inspiration_note":
            chunk_id = _int_or_none(item.raw_metadata.get("matched_chunk_id"))
            chunk = chunks.get(chunk_id) if chunk_id is not None else None
            if chunk is not None:
                current[item.fragment_id] = _updated_context(
                    item,
                    before=chunk.text,
                    after=chunk.context_after,
                    status="linked_source_context",
                    method="linked_pdf_chunk",
                )
            elif item.zotero_annotation_key and item.zotero_annotation_key in by_annotation:
                highlight = by_annotation[item.zotero_annotation_key]
                current[item.fragment_id] = _updated_context(
                    item,
                    before=highlight.text,
                    after=None,
                    status="linked_source_context",
                    method="zotero_annotation_key",
                )
            elif item.context_before or item.context_after:
                current[item.fragment_id] = item.model_copy(
                    update={
                        "context_status": "stored_source_context",
                        "context_method": item.context_method or "stored_inspiration_context",
                    }
                )
            else:
                current[item.fragment_id] = item.model_copy(
                    update={
                        "context_status": "no_source_context",
                        "context_method": None,
                    }
                )
        elif item.source_type in {"zotero_child_note", "personal_note"}:
            current[item.fragment_id] = item.model_copy(
                update={
                    "context_status": "source_complete",
                    "context_method": item.context_method or "full_note_content",
                }
            )

    return [current[item.fragment_id] for item in items]


def _apply_neighbor_context(
    items: list[RetrievalFragment],
    updates: dict[str, RetrievalFragment],
    *,
    source_type: str,
    group_key: Callable[[RetrievalFragment], str],
    method: str,
) -> None:
    groups: dict[str, list[RetrievalFragment]] = defaultdict(list)
    for item in items:
        if item.source_type == source_type:
            groups[group_key(item)].append(item)

    for group in groups.values():
        ordered = sorted(
            group,
            key=lambda item: (
                item.source_order if item.source_order is not None else 2**31,
                item.page_number if item.page_number is not None else 2**31,
                item.fragment_id,
            ),
        )
        for index, item in enumerate(ordered):
            before = item.context_before
            after = item.context_after
            if before is None and index > 0:
                before = _tail(ordered[index - 1].text)
            if after is None and index + 1 < len(ordered):
                after = _head(ordered[index + 1].text)
            if before or after:
                status = "available" if before and after else "partial"
                updates[item.fragment_id] = _updated_context(
                    item,
                    before=before,
                    after=after,
                    status=status,
                    method=item.context_method or method,
                )
            elif item.context_status in {"pending", "unbuilt"}:
                updates[item.fragment_id] = item.model_copy(
                    update={"context_status": "no_source_context", "context_method": None}
                )


def _updated_context(
    item: RetrievalFragment,
    *,
    before: str | None,
    after: str | None,
    status: str,
    method: str | None,
) -> RetrievalFragment:
    return item.model_copy(
        update={
            "context_before": _tail(before) if before else None,
            "context_after": _head(after) if after else None,
            "context_status": status,
            "context_method": method,
        }
    )


def _head(value: str) -> str:
    return value[:_CONTEXT_LIMIT].strip()


def _tail(value: str) -> str:
    return value[-_CONTEXT_LIMIT:].strip()


def _int_or_none(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
