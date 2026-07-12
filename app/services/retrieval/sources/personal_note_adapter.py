from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Iterable

from app.schemas.retrieval_fragment import RetrievalFragment
from app.services.retrieval.fragment_id import canonical_source_locator
from app.services.retrieval.fragment_normalizer import normalize_text
from app.services.retrieval.metadata_resolver import (
    ResolvedSourceMetadata,
    RetrievalMetadataResolver,
)
from app.services.retrieval.sources._common import make_fragment


ADAPTER_VERSION = "personal_note_adapter.v1"


def read_personal_note_fragments(
    conn: sqlite3.Connection,
    resolver: RetrievalMetadataResolver,
    *,
    document_ids: Iterable[int] | None = None,
) -> list[RetrievalFragment]:
    if not _table_exists(conn, "personal_notes"):
        return []
    selected_ids = {int(value) for value in (document_ids or [])}
    rows = conn.execute(
        """
        SELECT *
        FROM personal_notes
        ORDER BY
            COALESCE(document_id, 2147483647),
            created_at,
            title,
            COALESCE(content_hash, '')
        """
    ).fetchall()

    orders: dict[int | None, int] = defaultdict(int)
    fragments: list[RetrievalFragment] = []
    for row in rows:
        data = dict(row)
        document_id = _int_or_none(data.get("document_id"))
        if selected_ids and document_id not in selected_ids:
            continue
        text = normalize_text(data.get("content"))
        if not text:
            continue
        metadata = (
            resolver.for_document(document_id)
            if document_id is not None
            else ResolvedSourceMetadata(mapping_status="personal_note_unscoped")
        )
        source_order = orders[document_id]
        orders[document_id] = source_order + 1
        title = normalize_text(data.get("title"), preserve_paragraphs=False) or metadata.title
        fragments.append(
            make_fragment(
                source_type="personal_note",
                origin_kind="manual_import",
                source_record_id=str(data["id"]),
                canonical_locator=canonical_source_locator(
                    "personal_note",
                    row_id=data["id"],
                ),
                text=text,
                adapter_version=ADAPTER_VERSION,
                metadata=metadata,
                document_id=document_id,
                title=title,
                heading_path=[title] if title else [],
                section=normalize_text(data.get("scope_path"), preserve_paragraphs=False) or None,
                source_order=source_order,
                position={
                    "scope_type": data.get("scope_type"),
                    "scope_path": data.get("scope_path"),
                },
                context_status="source_complete",
                context_method="full_note_content",
                original_file_path=data.get("source_path") or metadata.original_file_path,
                source_created_at=_string_or_none(data.get("created_at")),
                source_updated_at=_string_or_none(data.get("updated_at")),
                provenance=[
                    {
                        "store": "production_db",
                        "table": "personal_notes",
                        "row_id": int(data["id"]),
                    }
                ],
                raw_metadata={
                    "note_type": data.get("note_type"),
                    "scope_type": data.get("scope_type"),
                    "scope_path": data.get("scope_path"),
                    "summary": data.get("summary"),
                    "stored_content_hash": data.get("content_hash"),
                },
            )
        )
    return fragments


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
