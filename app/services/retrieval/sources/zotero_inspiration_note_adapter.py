from __future__ import annotations

import sqlite3
from typing import Iterable

from app.schemas.retrieval_fragment import RetrievalFragment
from app.services.retrieval.fragment_id import (
    annotation_duplicate_group_id,
    annotation_source_group_id,
    canonical_source_locator,
)
from app.services.retrieval.fragment_normalizer import (
    bbox_from_position,
    normalize_string_list,
    normalize_text,
    page_number_from_position,
    parse_json,
)
from app.services.retrieval.metadata_resolver import RetrievalMetadataResolver
from app.services.retrieval.sources._common import make_fragment


ADAPTER_VERSION = "zotero_inspiration_note_adapter.v1"


def read_zotero_inspiration_note_fragments(
    conn: sqlite3.Connection,
    resolver: RetrievalMetadataResolver,
    *,
    document_ids: Iterable[int] | None = None,
) -> list[RetrievalFragment]:
    selected_ids = {int(value) for value in (document_ids or [])}
    if not _table_exists(conn, "zotero_inspiration_notes"):
        return []
    rows = conn.execute(
        """
        SELECT *
        FROM zotero_inspiration_notes
        ORDER BY
            COALESCE(zotero_attachment_key, ''),
            COALESCE(pdf_page, 2147483647),
            COALESCE(created_at, received_at, ''),
            server_note_id
        """
    ).fetchall()

    fragments: list[RetrievalFragment] = []
    for source_order, row in enumerate(rows):
        data = dict(row)
        row_id = int(data["id"])
        server_note_id = normalize_text(data.get("server_note_id"), preserve_paragraphs=False)
        source_record_id = server_note_id or f"row-{row_id}"
        attachment_key = normalize_text(
            data.get("zotero_attachment_key"),
            preserve_paragraphs=False,
        ) or None
        item_key = normalize_text(data.get("zotero_item_key"), preserve_paragraphs=False) or None
        matched_document_id = _int_or_none(data.get("matched_document_id"))
        if attachment_key:
            metadata = resolver.resolve_attachment(attachment_key, item_key=item_key)
        elif matched_document_id is not None:
            metadata = resolver.for_document(matched_document_id)
        elif item_key:
            metadata = resolver.resolve_item(item_key)
        else:
            metadata = resolver.for_document(matched_document_id) if matched_document_id else resolver.resolve_item("")

        effective_document_id = matched_document_id or metadata.document_id
        warnings: list[str] = []
        if metadata.mapping_status == "ambiguous_attachment_mapping":
            effective_document_id = None
            if matched_document_id is not None:
                warnings.append("matched_document_suppressed_due_to_ambiguous_attachment_mapping")
        elif (
            matched_document_id is not None
            and metadata.document_id is not None
            and matched_document_id != metadata.document_id
        ):
            effective_document_id = None
            warnings.append("matched_document_conflicts_with_attachment_mapping")

        candidate_ids = set(metadata.candidate_document_ids)
        if matched_document_id is not None:
            candidate_ids.add(matched_document_id)
        if selected_ids and not (
            effective_document_id in selected_ids
            or selected_ids.intersection(candidate_ids)
        ):
            continue

        selected_text = normalize_text(data.get("selected_text"))
        note_text = normalize_text(data.get("note_text"))
        text = note_text or selected_text
        if not text:
            continue
        position = parse_json(data.get("bbox_json"), {})
        if not isinstance(position, dict):
            position = {}
        page_number = page_number_from_position(position)
        if page_number is None:
            page_number = _int_or_none(data.get("pdf_page"))
            if page_number is not None:
                warnings.append("physical_page_from_legacy_pdf_page")
        if page_number is None:
            warnings.append("physical_page_unavailable")

        annotation_key = normalize_text(
            data.get("zotero_annotation_key"),
            preserve_paragraphs=False,
        ) or None
        library_id = metadata.zotero_library_id
        source_group_id = None
        duplicate_group_id = None
        if annotation_key and library_id is not None:
            source_group_id = annotation_source_group_id(library_id, annotation_key)
            duplicate_group_id = annotation_duplicate_group_id(library_id, annotation_key)

        context_before = normalize_text(data.get("context_before")) or None
        context_after = normalize_text(data.get("context_after")) or None
        fragments.append(
            make_fragment(
                source_type="zotero_inspiration_note",
                origin_kind=_origin_kind(data.get("source")),
                source_record_id=source_record_id,
                canonical_locator=canonical_source_locator(
                    "zotero_inspiration_note",
                    server_note_id=server_note_id or None,
                    row_id=row_id,
                ),
                text=text,
                adapter_version=ADAPTER_VERSION,
                metadata=metadata,
                document_id=effective_document_id,
                zotero_library_id=library_id,
                zotero_item_key=item_key,
                zotero_attachment_key=attachment_key,
                zotero_annotation_key=annotation_key,
                source_group_id=source_group_id,
                duplicate_group_id=duplicate_group_id,
                page_number=page_number,
                page_label=normalize_text(data.get("page_label"), preserve_paragraphs=False) or None,
                source_order=source_order,
                position=position or None,
                bbox=bbox_from_position(position),
                note_comment=selected_text if note_text and selected_text else None,
                context_before=context_before,
                context_after=context_after,
                context_status="stored_source_context" if context_before or context_after else "pending",
                context_method="stored_inspiration_context" if context_before or context_after else None,
                tags=normalize_string_list(data.get("user_tags_json")),
                source_created_at=_string_or_none(data.get("created_at")),
                source_updated_at=_string_or_none(data.get("updated_at")),
                provenance=[
                    {
                        "store": "production_db",
                        "table": "zotero_inspiration_notes",
                        "row_id": row_id,
                        "server_note_id": server_note_id or None,
                        "source": data.get("source"),
                    }
                ],
                warnings=warnings,
                raw_metadata={
                    "client_note_id": data.get("client_note_id"),
                    "source": data.get("source"),
                    "selection_type": data.get("selection_type"),
                    "selected_text": selected_text,
                    "selected_text_hash": data.get("selected_text_hash"),
                    "matched_document_id": matched_document_id,
                    "matched_chunk_id": _int_or_none(data.get("matched_chunk_id")),
                    "matched_chunk_ids": parse_json(data.get("matched_chunk_ids_json"), []),
                    "match_status": data.get("match_status"),
                    "sync_status": data.get("sync_status"),
                },
            )
        )
    return fragments


def _origin_kind(value: object) -> str:
    source = str(value or "").casefold()
    if source == "zotero_native_annotation":
        return "native"
    if source == "zotero_plugin":
        return "plugin"
    if "synthetic" in source or "seed" in source:
        return "synthetic_seed"
    return "manual_import"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone() is not None


def _int_or_none(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _string_or_none(value: object) -> str | None:
    return str(value) if value is not None else None
