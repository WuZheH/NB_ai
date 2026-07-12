from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Iterable

from app.schemas.retrieval_fragment import RetrievalFragment
from app.services.retrieval.fragment_id import canonical_source_locator
from app.services.retrieval.fragment_normalizer import html_to_text, sha256_text, split_text_blocks
from app.services.retrieval.metadata_resolver import RetrievalMetadataResolver
from app.services.retrieval.sources._common import make_fragment


ADAPTER_VERSION = "zotero_child_note_adapter.v1"


def read_zotero_child_note_fragments(
    conn: sqlite3.Connection,
    resolver: RetrievalMetadataResolver,
    *,
    document_ids: Iterable[int] | None = None,
) -> list[RetrievalFragment]:
    selected_ids = {int(value) for value in (document_ids or [])}
    rows = conn.execute(
        """
        SELECT
            note_item.itemID AS note_item_id,
            note_item.libraryID,
            note_item.key AS note_key,
            note_item.dateAdded,
            note_item.dateModified,
            note.parentItemID,
            note.note AS note_html,
            note.title AS note_title,
            parent_item.key AS parent_key,
            attachment.contentType AS parent_content_type,
            grandparent_item.key AS grandparent_key,
            deleted_parent.itemID AS deleted_parent_item_id
        FROM itemNotes AS note
        JOIN items AS note_item ON note_item.itemID = note.itemID
        JOIN items AS parent_item ON parent_item.itemID = note.parentItemID
        LEFT JOIN itemAttachments AS attachment ON attachment.itemID = note.parentItemID
        LEFT JOIN items AS grandparent_item ON grandparent_item.itemID = attachment.parentItemID
        LEFT JOIN deletedItems AS deleted_note ON deleted_note.itemID = note.itemID
        LEFT JOIN deletedItems AS deleted_parent ON deleted_parent.itemID = note.parentItemID
        WHERE note.parentItemID IS NOT NULL
          AND deleted_note.itemID IS NULL
        ORDER BY parent_item.key, note_item.dateAdded, note_item.key
        """
    ).fetchall()

    orders: dict[str, int] = defaultdict(int)
    fragments: list[RetrievalFragment] = []
    for row in rows:
        data = dict(row)
        note_key = str(data.get("note_key") or "").strip()
        parent_key = str(data.get("parent_key") or "").strip()
        if not note_key or not parent_key:
            continue
        is_pdf_parent = str(data.get("parent_content_type") or "").casefold() == "application/pdf"
        if is_pdf_parent:
            metadata = resolver.resolve_attachment(
                parent_key,
                item_key=data.get("grandparent_key"),
            )
        else:
            metadata = resolver.resolve_item(parent_key)
        if selected_ids and not (
            metadata.document_id in selected_ids
            or selected_ids.intersection(metadata.candidate_document_ids)
        ):
            continue

        text = html_to_text(data.get("note_html"))
        if not text:
            continue
        source_order = orders[parent_key]
        orders[parent_key] = source_order + 1
        title = str(data.get("note_title") or "").strip() or metadata.title
        blocks = split_text_blocks(data.get("note_html"))
        warnings = ["parent_item_deleted"] if data.get("deleted_parent_item_id") else []
        fragments.append(
            make_fragment(
                source_type="zotero_child_note",
                origin_kind="native",
                source_record_id=note_key,
                canonical_locator=canonical_source_locator(
                    "zotero_child_note",
                    library_id=int(data.get("libraryID") or 0),
                    item_key=note_key,
                ),
                text=text,
                adapter_version=ADAPTER_VERSION,
                metadata=metadata,
                zotero_library_id=int(data.get("libraryID") or 0),
                zotero_item_key=metadata.zotero_item_key or (
                    data.get("grandparent_key") if is_pdf_parent else parent_key
                ),
                zotero_attachment_key=metadata.zotero_attachment_key or (
                    parent_key if is_pdf_parent else None
                ),
                title=title,
                heading_path=[title] if title else [],
                source_order=source_order,
                position={
                    "parent_item_key": parent_key,
                    "paragraph_count": len(blocks),
                },
                context_status="source_complete",
                context_method="full_note_content",
                zotero_uri=f"zotero://select/library/items/{note_key}",
                source_created_at=_string_or_none(data.get("dateAdded")),
                source_updated_at=_string_or_none(data.get("dateModified")),
                warnings=warnings,
                provenance=[
                    {
                        "store": "zotero_snapshot",
                        "table": "itemNotes",
                        "row_id": int(data["note_item_id"]),
                        "item_key": note_key,
                    }
                ],
                raw_metadata={
                    "parent_item_key": parent_key,
                    "parent_kind": "pdf_attachment" if is_pdf_parent else "regular_item",
                    "grandparent_item_key": data.get("grandparent_key"),
                    "paragraph_count": len(blocks),
                    "source_html_hash": sha256_text(data.get("note_html")),
                },
            )
        )
    return fragments


def _string_or_none(value: object) -> str | None:
    return str(value) if value is not None else None
