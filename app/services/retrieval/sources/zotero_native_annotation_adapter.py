from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Iterable

from app.schemas.retrieval_fragment import RetrievalFragment
from app.services.retrieval.fragment_id import (
    annotation_duplicate_group_id,
    annotation_source_group_id,
    canonical_source_locator,
    fragment_uuid,
)
from app.services.retrieval.fragment_normalizer import (
    bbox_from_position,
    normalize_text,
    page_number_from_position,
    parse_json,
)
from app.services.retrieval.metadata_resolver import RetrievalMetadataResolver
from app.services.retrieval.sources._common import make_fragment


ADAPTER_VERSION = "zotero_native_annotation_adapter.v1"
ANNOTATION_TYPE_NAMES = {
    1: "highlight",
    2: "note",
    3: "image",
    4: "ink",
    5: "underline",
    6: "text",
}


def read_zotero_native_annotation_fragments(
    conn: sqlite3.Connection,
    resolver: RetrievalMetadataResolver,
    *,
    document_ids: Iterable[int] | None = None,
) -> list[RetrievalFragment]:
    selected_ids = {int(value) for value in (document_ids or [])}
    tags_by_item = _tags_by_item(conn)
    rows = conn.execute(
        """
        SELECT
            annotation_item.itemID AS annotation_item_id,
            annotation_item.libraryID,
            annotation_item.key AS annotation_key,
            annotation_item.dateAdded,
            annotation_item.dateModified,
            annotation.parentItemID AS attachment_item_id,
            annotation.type AS annotation_type,
            annotation.authorName,
            annotation.text,
            annotation.comment,
            annotation.color,
            annotation.pageLabel,
            annotation.sortIndex,
            annotation.position,
            attachment_item.key AS attachment_key,
            parent_item.key AS item_key
        FROM itemAnnotations AS annotation
        JOIN items AS annotation_item ON annotation_item.itemID = annotation.itemID
        JOIN items AS attachment_item ON attachment_item.itemID = annotation.parentItemID
        LEFT JOIN itemAttachments AS attachment ON attachment.itemID = annotation.parentItemID
        LEFT JOIN items AS parent_item ON parent_item.itemID = attachment.parentItemID
        LEFT JOIN deletedItems AS deleted_annotation ON deleted_annotation.itemID = annotation.itemID
        LEFT JOIN deletedItems AS deleted_attachment ON deleted_attachment.itemID = annotation.parentItemID
        WHERE deleted_annotation.itemID IS NULL
          AND deleted_attachment.itemID IS NULL
        ORDER BY
            annotation_item.libraryID,
            attachment_item.key,
            annotation.sortIndex,
            annotation_item.key
        """
    ).fetchall()

    orders: dict[tuple[int, str], int] = defaultdict(int)
    fragments: list[RetrievalFragment] = []
    for row in rows:
        data = dict(row)
        annotation_key = str(data.get("annotation_key") or "").strip()
        attachment_key = str(data.get("attachment_key") or "").strip()
        if not annotation_key or not attachment_key:
            continue
        library_id = int(data.get("libraryID") or 0)
        metadata = resolver.resolve_attachment(
            attachment_key,
            item_key=data.get("item_key"),
        )
        if selected_ids and not (
            metadata.document_id in selected_ids
            or selected_ids.intersection(metadata.candidate_document_ids)
        ):
            continue

        group_key = (library_id, attachment_key)
        source_order = orders[group_key]
        orders[group_key] = source_order + 1
        position = parse_json(data.get("position"), {})
        if not isinstance(position, dict):
            position = {}
        page_number = page_number_from_position(position)
        page_label = normalize_text(data.get("pageLabel"), preserve_paragraphs=False) or None
        annotation_type = int(data.get("annotation_type") or 0)
        annotation_subtype = ANNOTATION_TYPE_NAMES.get(annotation_type, f"type_{annotation_type}")
        source_group_id = annotation_source_group_id(library_id, annotation_key)
        duplicate_group_id = annotation_duplicate_group_id(library_id, annotation_key)
        annotation_tags = tags_by_item.get(int(data["annotation_item_id"]), [])
        tags = list(dict.fromkeys([*metadata.tags, *annotation_tags]))
        warnings = []
        if page_number is None:
            warnings.append("physical_page_unavailable")
        if annotation_type not in {1, 5}:
            warnings.append(f"annotation_subtype_{annotation_subtype}")

        highlight_text = normalize_text(data.get("text"))
        highlight_fragment_id: str | None = None
        if highlight_text:
            locator = canonical_source_locator(
                "zotero_highlight",
                library_id=library_id,
                annotation_key=annotation_key,
            )
            highlight_fragment_id = fragment_uuid(locator)
            fragments.append(
                make_fragment(
                    source_type="zotero_highlight",
                    origin_kind="native",
                    source_record_id=annotation_key,
                    canonical_locator=locator,
                    text=highlight_text,
                    adapter_version=ADAPTER_VERSION,
                    metadata=metadata,
                    zotero_library_id=library_id,
                    zotero_item_key=data.get("item_key"),
                    zotero_attachment_key=attachment_key,
                    zotero_annotation_key=annotation_key,
                    source_group_id=source_group_id,
                    duplicate_group_id=duplicate_group_id,
                    page_number=page_number,
                    page_label=page_label,
                    source_order=source_order,
                    position=position,
                    bbox=bbox_from_position(position),
                    tags=tags,
                    source_created_at=_string_or_none(data.get("dateAdded")),
                    source_updated_at=_string_or_none(data.get("dateModified")),
                    provenance=[
                        {
                            "store": "zotero_snapshot",
                            "table": "itemAnnotations",
                            "row_id": int(data["annotation_item_id"]),
                            "annotation_key": annotation_key,
                        }
                    ],
                    warnings=warnings,
                    raw_metadata={
                        "annotation_type": annotation_type,
                        "annotation_subtype": annotation_subtype,
                        "sort_index": data.get("sortIndex"),
                        "color": data.get("color"),
                        "author_name": data.get("authorName"),
                    },
                )
            )

        comment_text = normalize_text(data.get("comment"))
        if comment_text:
            locator = canonical_source_locator(
                "zotero_annotation_comment",
                library_id=library_id,
                annotation_key=annotation_key,
            )
            comment_warnings = list(warnings)
            if highlight_fragment_id is None:
                comment_warnings.append("annotation_highlight_text_unavailable")
            fragments.append(
                make_fragment(
                    source_type="zotero_annotation_comment",
                    origin_kind="native",
                    source_record_id=annotation_key,
                    canonical_locator=locator,
                    text=comment_text,
                    adapter_version=ADAPTER_VERSION,
                    metadata=metadata,
                    zotero_library_id=library_id,
                    zotero_item_key=data.get("item_key"),
                    zotero_attachment_key=attachment_key,
                    zotero_annotation_key=annotation_key,
                    parent_fragment_id=highlight_fragment_id,
                    source_group_id=source_group_id,
                    duplicate_group_id=duplicate_group_id,
                    page_number=page_number,
                    page_label=page_label,
                    source_order=source_order,
                    position=position,
                    bbox=bbox_from_position(position),
                    tags=tags,
                    source_created_at=_string_or_none(data.get("dateAdded")),
                    source_updated_at=_string_or_none(data.get("dateModified")),
                    provenance=[
                        {
                            "store": "zotero_snapshot",
                            "table": "itemAnnotations",
                            "row_id": int(data["annotation_item_id"]),
                            "annotation_key": annotation_key,
                            "field": "comment",
                        }
                    ],
                    warnings=comment_warnings,
                    raw_metadata={
                        "annotation_type": annotation_type,
                        "annotation_subtype": annotation_subtype,
                        "sort_index": data.get("sortIndex"),
                        "color": data.get("color"),
                        "author_name": data.get("authorName"),
                    },
                )
            )
    return fragments


def _tags_by_item(conn: sqlite3.Connection) -> dict[int, list[str]]:
    rows = conn.execute(
        """
        SELECT itemTags.itemID, tags.name
        FROM itemTags
        JOIN tags ON tags.tagID = itemTags.tagID
        ORDER BY itemTags.itemID, tags.name
        """
    ).fetchall()
    result: dict[int, list[str]] = defaultdict(list)
    for row in rows:
        if row[1]:
            result[int(row[0])].append(str(row[1]))
    return result


def _string_or_none(value: object) -> str | None:
    return str(value) if value is not None else None
