from __future__ import annotations

import sqlite3
from typing import Iterable

from app.schemas.retrieval_fragment import RetrievalFragment
from app.services.retrieval.fragment_id import canonical_source_locator
from app.services.retrieval.fragment_normalizer import parse_heading_path
from app.services.retrieval.metadata_resolver import RetrievalMetadataResolver
from app.services.retrieval.sources._common import make_fragment


ADAPTER_VERSION = "pdf_chunk_adapter.v1"


def read_pdf_chunk_fragments(
    conn: sqlite3.Connection,
    resolver: RetrievalMetadataResolver,
    *,
    document_ids: Iterable[int] | None = None,
) -> list[RetrievalFragment]:
    selected_ids = tuple(sorted({int(value) for value in (document_ids or [])}))
    where = ""
    params: tuple[int, ...] = ()
    if selected_ids:
        placeholders = ",".join("?" for _ in selected_ids)
        where = f"WHERE chunks.document_id IN ({placeholders})"
        params = selected_ids

    rows = conn.execute(
        f"""
        SELECT
            chunks.id AS chunk_id,
            chunks.document_id,
            chunks.node_id,
            chunks.chunk_index,
            chunks.heading_path,
            chunks.chunk_text,
            chunks.overlap_before,
            chunks.overlap_after,
            chunks.content_hash AS stored_content_hash,
            chunks.pdf_path AS chunk_pdf_path,
            chunks.pdf_page_start,
            chunks.pdf_page_end,
            chunks.chapter_id,
            chunks.zotero_open_url,
            chunks.created_at,
            chunks.updated_at,
            nodes.order_index AS node_order,
            chapters.chapter_index,
            chapters.title AS chapter_title
        FROM knowledge_chunks AS chunks
        LEFT JOIN markdown_nodes AS nodes ON nodes.id = chunks.node_id
        LEFT JOIN book_chapters AS chapters ON chapters.id = chunks.chapter_id
        {where}
        ORDER BY
            chunks.document_id,
            COALESCE(chunks.pdf_page_start, 2147483647),
            COALESCE(chapters.chapter_index, 2147483647),
            COALESCE(nodes.order_index, 2147483647),
            chunks.chunk_index,
            chunks.content_hash
        """,
        params,
    ).fetchall()

    orders: dict[int, int] = {}
    fragments: list[RetrievalFragment] = []
    for row in rows:
        data = dict(row)
        document_id = int(data["document_id"])
        source_order = orders.get(document_id, 0)
        orders[document_id] = source_order + 1
        heading_path = parse_heading_path(data.get("heading_path"))
        text = str(data.get("chunk_text") or "").strip()
        if not text:
            continue
        metadata = resolver.for_document(document_id)
        page_number = _int_or_none(data.get("pdf_page_start"))
        context_before = data.get("overlap_before")
        context_after = data.get("overlap_after")
        warnings: list[str] = []
        if page_number is None:
            warnings.append("physical_page_unavailable")
        fragments.append(
            make_fragment(
                source_type="pdf_chunk",
                origin_kind="manual_import",
                source_record_id=str(data["chunk_id"]),
                canonical_locator=canonical_source_locator(
                    "pdf_chunk",
                    document_id=document_id,
                    chunk_id=data["chunk_id"],
                ),
                text=text,
                adapter_version=ADAPTER_VERSION,
                metadata=metadata,
                document_id=document_id,
                page_number=page_number,
                page_label=None,
                section=heading_path[-1] if heading_path else data.get("chapter_title"),
                heading_path=heading_path,
                source_order=source_order,
                context_before=context_before,
                context_after=context_after,
                context_status="stored_source_context" if context_before or context_after else "pending",
                context_method="imported_chunk_overlap" if context_before or context_after else None,
                original_file_path=data.get("chunk_pdf_path") or metadata.original_file_path,
                zotero_uri=data.get("zotero_open_url") or metadata.zotero_uri,
                source_created_at=_string_or_none(data.get("created_at")),
                source_updated_at=_string_or_none(data.get("updated_at")),
                provenance=[
                    {
                        "store": "production_db",
                        "table": "knowledge_chunks",
                        "row_id": int(data["chunk_id"]),
                    }
                ],
                warnings=warnings,
                raw_metadata={
                    "chunk_index": data.get("chunk_index"),
                    "node_id": data.get("node_id"),
                    "node_order": data.get("node_order"),
                    "chapter_id": data.get("chapter_id"),
                    "chapter_index": data.get("chapter_index"),
                    "pdf_page_end": data.get("pdf_page_end"),
                    "stored_content_hash": data.get("stored_content_hash"),
                },
            )
        )
    return fragments


def _int_or_none(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _string_or_none(value: object) -> str | None:
    return str(value) if value is not None else None
