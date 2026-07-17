from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.core.database import connect_immutable_readonly_sqlite
from app.core.paths import DEFAULT_DB_PATH
from app.domains.retrieval.result_contracts import (
    NOTEBOOK_SOURCE_TYPES,
    NotebookFragment,
    NotebookSourceType,
    OpenTarget,
)
from app.schemas.retrieval_fragment import RetrievalFragment, RetrievalSourceType
from app.services.retrieval.source_registry import RetrievalSourceRegistry


_SUPPORT_SOURCE_TYPES: tuple[RetrievalSourceType, ...] = (
    "pdf_chunk",
    "zotero_highlight",
    "zotero_annotation_comment",
    "zotero_child_note",
    "zotero_inspiration_note",
)
EXCLUDED_NOTE_ORIGIN_KINDS: tuple[str, ...] = ("synthetic_seed",)


class NotebookFragmentNotFound(LookupError):
    pass


@dataclass(frozen=True)
class NotebookFragmentRecord:
    fragment: NotebookFragment
    source: RetrievalFragment


def list_notebook_fragments(
    *,
    source_types: Iterable[NotebookSourceType] = NOTEBOOK_SOURCE_TYPES,
    document_ids: Iterable[int] | None = None,
    registry: RetrievalSourceRegistry | None = None,
) -> list[NotebookFragment]:
    requested = tuple(dict.fromkeys(source_types))
    unknown = set(requested).difference(NOTEBOOK_SOURCE_TYPES)
    if unknown:
        raise ValueError(f"unsupported notebook source types: {sorted(unknown)}")

    support: set[RetrievalSourceType] = set(requested)
    if "zotero_annotation_comment" in requested:
        support.add("zotero_highlight")
    if "zotero_inspiration_note" in requested:
        support.update({"zotero_highlight", "pdf_chunk"})

    catalog = (registry or RetrievalSourceRegistry()).read(
        source_types=tuple(item for item in _SUPPORT_SOURCE_TYPES if item in support),
        document_ids=document_ids,
    )
    fragments = list(catalog.fragments)
    by_id = {item.fragment_id: item for item in fragments}
    document_types = _document_types(
        {item.document_id for item in fragments if item.document_id is not None}
    )
    return [
        _to_notebook_fragment(item, by_id=by_id, document_types=document_types)
        for item in fragments
        if item.source_type in requested and _is_product_fragment(item)
    ]


def get_notebook_fragment(
    fragment_id: str,
    *,
    registry: RetrievalSourceRegistry | None = None,
) -> NotebookFragment:
    return get_notebook_fragment_record(fragment_id, registry=registry).fragment


def get_notebook_fragment_record(
    fragment_id: str,
    *,
    registry: RetrievalSourceRegistry | None = None,
) -> NotebookFragmentRecord:
    cleaned = str(fragment_id or "").strip()
    if not cleaned:
        raise NotebookFragmentNotFound("fragment_id must not be empty")
    catalog = (registry or RetrievalSourceRegistry()).read(
        source_types=_SUPPORT_SOURCE_TYPES,
    )
    by_id = {item.fragment_id: item for item in catalog.fragments}
    item = by_id.get(cleaned)
    if (
        item is None
        or item.source_type not in NOTEBOOK_SOURCE_TYPES
        or not _is_product_fragment(item)
    ):
        raise NotebookFragmentNotFound(f"Notebook fragment not found: {cleaned}")
    document_types = _document_types(
        {value.document_id for value in catalog.fragments if value.document_id is not None}
    )
    return NotebookFragmentRecord(
        fragment=_to_notebook_fragment(item, by_id=by_id, document_types=document_types),
        source=item,
    )


def get_notebook_fragments(
    fragment_ids: Iterable[str],
    *,
    registry: RetrievalSourceRegistry | None = None,
) -> list[NotebookFragment]:
    ordered_ids = list(dict.fromkeys(str(value).strip() for value in fragment_ids if str(value).strip()))
    if not ordered_ids:
        return []
    catalog = (registry or RetrievalSourceRegistry()).read(source_types=_SUPPORT_SOURCE_TYPES)
    by_id = {item.fragment_id: item for item in catalog.fragments}
    missing = [value for value in ordered_ids if value not in by_id]
    unsupported = [
        value
        for value in ordered_ids
        if value in by_id
        and (
            by_id[value].source_type not in NOTEBOOK_SOURCE_TYPES
            or not _is_product_fragment(by_id[value])
        )
    ]
    if missing or unsupported:
        unavailable = [*missing, *unsupported]
        raise NotebookFragmentNotFound(
            f"Notebook fragments not found: {', '.join(unavailable[:5])}"
        )
    document_types = _document_types(
        {value.document_id for value in catalog.fragments if value.document_id is not None}
    )
    return [
        _to_notebook_fragment(by_id[value], by_id=by_id, document_types=document_types)
        for value in ordered_ids
    ]


def _to_notebook_fragment(
    item: RetrievalFragment,
    *,
    by_id: dict[str, RetrievalFragment],
    document_types: dict[int, str | None],
) -> NotebookFragment:
    source_type = item.source_type
    selected_text: str | None = None
    note_text: str | None = None
    text: str | None = None
    server_note_id: str | None = None
    client_note_id: str | None = None

    if source_type == "pdf_chunk":
        text = item.text
    elif source_type == "zotero_annotation_comment":
        note_text = item.text
        parent = by_id.get(item.parent_fragment_id or "")
        selected_text = parent.text if parent and parent.source_type == "zotero_highlight" else None
    elif source_type == "zotero_child_note":
        note_text = item.text
    elif source_type == "zotero_inspiration_note":
        selected_text = _clean(item.raw_metadata.get("selected_text"))
        if not (selected_text and selected_text == item.text and item.note_comment is None):
            note_text = item.text
        server_note_id = item.source_record_id
        client_note_id = _clean(item.raw_metadata.get("client_note_id"))
    else:  # pragma: no cover - caller filters the catalog first
        raise ValueError(f"unsupported notebook source type: {source_type}")

    chunk_id = (
        _int_or_none(item.source_record_id)
        if source_type == "pdf_chunk"
        else _int_or_none(item.raw_metadata.get("matched_chunk_id"))
    )
    pdf_url = _pdf_url(item.document_id, item.page_number)
    zotero_url = item.zotero_uri
    return NotebookFragment(
        fragment_id=item.fragment_id,
        source_type=source_type,
        server_note_id=server_note_id,
        client_note_id=client_note_id,
        zotero_item_key=item.zotero_item_key,
        zotero_attachment_key=item.zotero_attachment_key,
        zotero_annotation_key=item.zotero_annotation_key,
        document_id=item.document_id,
        document_title=item.title,
        document_type=(
            document_types.get(item.document_id) if item.document_id is not None else None
        ),
        chunk_id=chunk_id,
        pdf_page=item.page_number,
        page_label=item.page_label,
        text=text,
        selected_text=selected_text,
        note_text=note_text,
        context_before=item.context_before,
        context_after=item.context_after,
        tags=list(item.tags),
        content_hash=item.content_hash,
        provenance=list(item.provenance),
        open_target=OpenTarget(
            pdf_url=pdf_url,
            zotero_url=zotero_url,
            can_open_pdf=pdf_url is not None,
            can_open_zotero=zotero_url is not None,
            pdf_disabled_reason=None if pdf_url else "No mapped Search PDF document is available.",
            zotero_disabled_reason=None if zotero_url else "No Zotero item or attachment URI is available.",
        ),
    )


def _document_types(document_ids: set[int]) -> dict[int, str | None]:
    if not document_ids:
        return {}
    placeholders = ",".join("?" for _ in document_ids)
    with connect_immutable_readonly_sqlite(DEFAULT_DB_PATH) as connection:
        rows = connection.execute(
            f"SELECT id, document_type FROM documents WHERE id IN ({placeholders})",
            tuple(sorted(document_ids)),
        ).fetchall()
    return {int(row["id"]): _clean(row["document_type"]) for row in rows}


def _pdf_url(document_id: int | None, page: int | None) -> str | None:
    if document_id is None:
        return None
    suffix = f"#page={page}" if page else ""
    return f"/api/v1/library/documents/{document_id}/pdf{suffix}"


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _is_product_fragment(item: RetrievalFragment) -> bool:
    # The notebook-search product is explicitly limited to the user's reading
    # notes. Historical synthetic acceptance seeds share the same source_type
    # but are not user-authored corpus evidence.
    return not (
        item.source_type != "pdf_chunk"
        and item.origin_kind in EXCLUDED_NOTE_ORIGIN_KINDS
    )
