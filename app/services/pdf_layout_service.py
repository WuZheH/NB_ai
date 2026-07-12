from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.services.book_import_contract import ChunkLayoutLineLink, ChunkLayoutLink, PdfLayoutBlock, PdfLayoutLine, PdfLayoutSpan


LAYOUT_SCHEMA_TABLES = (
    "pdf_page_layout_blocks",
    "pdf_page_layout_lines",
    "pdf_page_layout_spans",
    "chunk_layout_links",
    "chunk_layout_line_links",
    "pdf_page_text_layer_cache",
)
BLOCK_LAYOUT_SCHEMA_TABLES = (
    "pdf_page_layout_blocks",
    "chunk_layout_links",
)
LINE_LAYOUT_SCHEMA_TABLES = (
    "pdf_page_layout_lines",
    "pdf_page_layout_spans",
    "chunk_layout_line_links",
)
TEXT_CACHE_SCHEMA_TABLES = ("pdf_page_text_layer_cache",)


def planned_layout_schema_sql(missing_tables: set[str] | None = None) -> list[str]:
    missing = missing_tables or set(LAYOUT_SCHEMA_TABLES)
    statements: list[str] = []
    if "pdf_page_layout_blocks" in missing:
        statements.extend(
            [
                "CREATE TABLE IF NOT EXISTS pdf_page_layout_blocks (...)",
                "CREATE INDEX IF NOT EXISTS ix_pdf_page_layout_blocks_document_page ON pdf_page_layout_blocks(document_id, pdf_page)",
                "CREATE INDEX IF NOT EXISTS ix_pdf_page_layout_blocks_text_hash ON pdf_page_layout_blocks(text_hash)",
            ]
        )
    if "chunk_layout_links" in missing:
        statements.extend(
            [
                "CREATE TABLE IF NOT EXISTS chunk_layout_links (...)",
                "CREATE INDEX IF NOT EXISTS ix_chunk_layout_links_chunk_id ON chunk_layout_links(chunk_id)",
                "CREATE INDEX IF NOT EXISTS ix_chunk_layout_links_document_page ON chunk_layout_links(document_id, pdf_page)",
            ]
        )
    if "pdf_page_layout_lines" in missing:
        statements.extend(
            [
                "CREATE TABLE IF NOT EXISTS pdf_page_layout_lines (...)",
                "CREATE INDEX IF NOT EXISTS ix_pdf_page_layout_lines_document_page ON pdf_page_layout_lines(document_id, pdf_page)",
            ]
        )
    if "pdf_page_layout_spans" in missing:
        statements.extend(
            [
                "CREATE TABLE IF NOT EXISTS pdf_page_layout_spans (...)",
                "CREATE INDEX IF NOT EXISTS ix_pdf_page_layout_spans_document_page ON pdf_page_layout_spans(document_id, pdf_page)",
            ]
        )
    if "chunk_layout_line_links" in missing:
        statements.extend(
            [
                "CREATE TABLE IF NOT EXISTS chunk_layout_line_links (...)",
                "CREATE INDEX IF NOT EXISTS ix_chunk_layout_line_links_chunk_id ON chunk_layout_line_links(chunk_id)",
            ]
        )
    if "pdf_page_text_layer_cache" in missing:
        statements.extend(
            [
                "CREATE TABLE IF NOT EXISTS pdf_page_text_layer_cache (...)",
                "CREATE INDEX IF NOT EXISTS ix_pdf_page_text_layer_cache_document_page ON pdf_page_text_layer_cache(document_id, pdf_page)",
            ]
        )
    return statements


def create_pdf_layout_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS pdf_page_layout_blocks (
            id INTEGER PRIMARY KEY,
            document_id INTEGER NOT NULL,
            pdf_page INTEGER NOT NULL,
            page_width REAL,
            page_height REAL,
            source_backend TEXT NOT NULL,
            backend_version TEXT,
            block_index INTEGER NOT NULL,
            block_type TEXT,
            text TEXT,
            normalized_text TEXT,
            bbox_json TEXT NOT NULL,
            polygon_json TEXT,
            confidence REAL,
            text_hash TEXT,
            created_at TEXT
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_pdf_page_layout_blocks_document_page "
        "ON pdf_page_layout_blocks(document_id, pdf_page)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_pdf_page_layout_blocks_text_hash "
        "ON pdf_page_layout_blocks(text_hash)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS chunk_layout_links (
            id INTEGER PRIMARY KEY,
            chunk_id INTEGER NOT NULL,
            document_id INTEGER NOT NULL,
            pdf_page INTEGER NOT NULL,
            block_id INTEGER NOT NULL,
            match_method TEXT NOT NULL,
            overlap_score REAL,
            confidence TEXT,
            created_at TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS pdf_page_layout_lines (
            id INTEGER PRIMARY KEY,
            document_id INTEGER NOT NULL,
            pdf_page INTEGER NOT NULL,
            block_id INTEGER,
            line_index INTEGER NOT NULL,
            text TEXT,
            normalized_text TEXT,
            bbox_json TEXT NOT NULL,
            confidence REAL,
            source_backend TEXT NOT NULL,
            text_hash TEXT,
            created_at TEXT
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_pdf_page_layout_lines_document_page "
        "ON pdf_page_layout_lines(document_id, pdf_page)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS pdf_page_layout_spans (
            id INTEGER PRIMARY KEY,
            document_id INTEGER NOT NULL,
            pdf_page INTEGER NOT NULL,
            block_id INTEGER,
            line_id INTEGER,
            span_index INTEGER NOT NULL,
            text TEXT,
            normalized_text TEXT,
            bbox_json TEXT NOT NULL,
            confidence REAL,
            source_backend TEXT NOT NULL,
            text_hash TEXT,
            created_at TEXT
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_pdf_page_layout_spans_document_page "
        "ON pdf_page_layout_spans(document_id, pdf_page)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS chunk_layout_line_links (
            id INTEGER PRIMARY KEY,
            chunk_id INTEGER NOT NULL,
            document_id INTEGER NOT NULL,
            pdf_page INTEGER NOT NULL,
            line_id INTEGER NOT NULL,
            match_method TEXT NOT NULL,
            overlap_score REAL,
            confidence TEXT,
            created_at TEXT
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunk_layout_line_links_chunk_id "
        "ON chunk_layout_line_links(chunk_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunk_layout_links_chunk_id "
        "ON chunk_layout_links(chunk_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunk_layout_links_document_page "
        "ON chunk_layout_links(document_id, pdf_page)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS pdf_page_text_layer_cache (
            id INTEGER PRIMARY KEY,
            document_id INTEGER NOT NULL,
            pdf_page INTEGER NOT NULL,
            source TEXT,
            extracted_text TEXT,
            page_text_length INTEGER,
            created_at TEXT
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_pdf_page_text_layer_cache_document_page "
        "ON pdf_page_text_layer_cache(document_id, pdf_page)"
    )


def schema_tables_present(connection: sqlite3.Connection) -> bool:
    return set(layout_schema_status(connection)["missing_tables"]) == set()


def layout_schema_status(connection: sqlite3.Connection) -> dict[str, Any]:
    placeholders = ", ".join(["?"] * len(LAYOUT_SCHEMA_TABLES))
    rows = connection.execute(
        f"SELECT name FROM sqlite_master WHERE type='table' AND name IN ({placeholders})",
        LAYOUT_SCHEMA_TABLES,
    ).fetchall()
    existing = {str(row[0]) for row in rows}
    missing = set(LAYOUT_SCHEMA_TABLES) - existing
    return {
        "existing_tables": sorted(existing),
        "missing_tables": sorted(missing),
        "block_schema_present": set(BLOCK_LAYOUT_SCHEMA_TABLES).issubset(existing),
        "line_schema_present": set(LINE_LAYOUT_SCHEMA_TABLES).issubset(existing),
        "text_cache_schema_present": set(TEXT_CACHE_SCHEMA_TABLES).issubset(existing),
        "layout_schema_present": not missing,
    }


def normalize_layout_text(text: Any) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip().casefold()
    return normalized


def compact_match_text(text: Any) -> str:
    return re.sub(r"[\W_]+", "", normalize_layout_text(text), flags=re.UNICODE)


def normalize_ocr_line_text_for_display(text: Any) -> str:
    value = html.unescape(str(text or ""))
    value = re.sub(r"<\s*sub\s*>(.*?)<\s*/\s*sub\s*>", r"_\1", value, flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(r"<\s*sup\s*>(.*?)<\s*/\s*sup\s*>", r"^\1", value, flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(r"</?\s*math\s*[^>]*>", "", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    replacements = {
        r"\delta": "δ",
        r"\Delta": "Δ",
        r"\infty": "∞",
        r"\in": "∈",
        r"\leq": "≤",
        r"\le": "≤",
        r"\geq": "≥",
        r"\ge": "≥",
        r"\cdots": "…",
        r"\ldots": "…",
        r"\dots": "…",
        r"\times": "×",
        r"\cdot": "·",
    }
    for source, target in replacements.items():
        value = value.replace(source, target)
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\s+([，。；：,.!?;:)）])", r"\1", value)
    value = re.sub(r"([（(])\s+", r"\1", value)
    return value.strip()


def normalize_ocr_line_text_for_match(text: Any) -> str:
    value = normalize_ocr_line_text_for_display(text).casefold()
    value = re.sub(r"[`*_#]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def layout_text_hash(text: Any) -> str:
    return hashlib.sha256(normalize_layout_text(text).encode("utf-8")).hexdigest()


def persist_layout_blocks_and_links(
    connection: sqlite3.Connection,
    *,
    document_id: int,
    chunks: list[Any],
    chunk_ids: list[int],
    layout_blocks: list[PdfLayoutBlock],
    layout_lines: list[PdfLayoutLine] | None = None,
    layout_spans: list[PdfLayoutSpan] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    create_pdf_layout_schema(connection)
    layout_lines = layout_lines or []
    layout_spans = layout_spans or []
    if not layout_blocks and not layout_lines and not layout_spans:
        return {
            "inserted_layout_blocks": 0,
            "inserted_layout_lines": 0,
            "inserted_layout_spans": 0,
            "inserted_chunk_layout_links": 0,
            "inserted_chunk_layout_line_links": 0,
            "layout_match_rate": 0.0,
            "layout_line_match_rate": 0.0,
        }
    now = created_at or datetime.utcnow().isoformat(sep=" ", timespec="seconds")
    inserted_blocks = insert_layout_blocks(connection, document_id=document_id, layout_blocks=layout_blocks, created_at=now)
    inserted_lines = insert_layout_lines(
        connection,
        document_id=document_id,
        layout_lines=layout_lines,
        inserted_blocks=inserted_blocks,
        created_at=now,
    )
    inserted_spans = insert_layout_spans(
        connection,
        document_id=document_id,
        layout_spans=layout_spans,
        inserted_blocks=inserted_blocks,
        inserted_lines=inserted_lines,
        created_at=now,
    )
    line_links = build_chunk_layout_line_links_from_source_lines(
        chunks=chunks,
        chunk_ids=chunk_ids,
        document_id=document_id,
        persisted_lines=inserted_lines,
    )
    if not line_links:
        line_links = align_chunks_to_layout_lines(
            chunks=chunks,
            chunk_ids=chunk_ids,
            document_id=document_id,
            persisted_lines=inserted_lines,
        )
    inserted_line_link_count = insert_chunk_layout_line_links(connection, line_links, created_at=now)
    links = align_chunks_to_layout_blocks(
        chunks=chunks,
        chunk_ids=chunk_ids,
        document_id=document_id,
        persisted_blocks=inserted_blocks,
    )
    inserted_link_count = insert_chunk_layout_links(connection, links, created_at=now)
    matched_chunks = {link.chunk_id for link in links if link.confidence in {"high", "medium"}}
    line_matched_chunks = {link.chunk_id for link in line_links if link.confidence in {"high", "medium"}}
    return {
        "inserted_layout_blocks": len(inserted_blocks),
        "inserted_layout_lines": len(inserted_lines),
        "inserted_layout_spans": len(inserted_spans),
        "inserted_chunk_layout_links": inserted_link_count,
        "inserted_chunk_layout_line_links": inserted_line_link_count,
        "layout_match_rate": len(matched_chunks) / len(chunk_ids) if chunk_ids else 0.0,
        "layout_line_match_rate": len(line_matched_chunks) / len(chunk_ids) if chunk_ids else 0.0,
    }


def insert_pdf_page_text_layer_cache(
    connection: sqlite3.Connection,
    *,
    document_id: int,
    pdf_page: int,
    source: str,
    extracted_text: str,
    created_at: str,
) -> int:
    create_pdf_layout_schema(connection)
    connection.execute(
        """
        DELETE FROM pdf_page_text_layer_cache
        WHERE document_id = ? AND pdf_page = ? AND source = ?
        """,
        (document_id, pdf_page, source),
    )
    connection.execute(
        """
        INSERT INTO pdf_page_text_layer_cache (
            document_id, pdf_page, source, extracted_text, page_text_length, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (document_id, pdf_page, source, extracted_text, len(extracted_text), created_at),
    )
    return 1


def delete_existing_ocr_layout_for_page(
    connection: sqlite3.Connection,
    *,
    document_id: int,
    pdf_page: int,
    source_backend: str = "surya_ocr",
) -> None:
    line_ids = [
        int(row[0])
        for row in connection.execute(
            """
            SELECT id FROM pdf_page_layout_lines
            WHERE document_id = ? AND pdf_page = ? AND source_backend = ?
            """,
            (document_id, pdf_page, source_backend),
        ).fetchall()
    ]
    if line_ids:
        placeholders = ", ".join(["?"] * len(line_ids))
        connection.execute(f"DELETE FROM chunk_layout_line_links WHERE line_id IN ({placeholders})", line_ids)
        connection.execute(f"DELETE FROM pdf_page_layout_spans WHERE line_id IN ({placeholders})", line_ids)
    connection.execute(
        """
        DELETE FROM pdf_page_layout_spans
        WHERE document_id = ? AND pdf_page = ? AND source_backend = ?
        """,
        (document_id, pdf_page, source_backend),
    )
    connection.execute(
        """
        DELETE FROM pdf_page_layout_lines
        WHERE document_id = ? AND pdf_page = ? AND source_backend = ?
        """,
        (document_id, pdf_page, source_backend),
    )
    connection.execute(
        """
        DELETE FROM pdf_page_text_layer_cache
        WHERE document_id = ? AND pdf_page = ? AND source = ?
        """,
        (document_id, pdf_page, source_backend),
    )


def insert_ocr_page_layout(
    connection: sqlite3.Connection,
    *,
    document_id: int,
    ocr_page_layout: Any,
    chunks: list[Any],
    chunk_ids: list[int],
    created_at: str | None = None,
) -> dict[str, Any]:
    create_pdf_layout_schema(connection)
    now = created_at or datetime.utcnow().isoformat(sep=" ", timespec="seconds")
    source_backend = str(getattr(ocr_page_layout, "ocr_backend", "surya") or "surya")
    if source_backend == "surya":
        source_backend = "surya_ocr"
    pdf_page = int(getattr(ocr_page_layout, "pdf_page"))
    delete_existing_ocr_layout_for_page(
        connection,
        document_id=document_id,
        pdf_page=pdf_page,
        source_backend=source_backend,
    )
    extracted_text = str(
        getattr(ocr_page_layout, "extracted_text", "")
        or "\n".join(getattr(line, "text", "") for line in getattr(ocr_page_layout, "lines", []))
    )
    connection.execute(
        """
        INSERT INTO pdf_page_text_layer_cache (
            document_id, pdf_page, source, extracted_text, page_text_length, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (document_id, pdf_page, source_backend, extracted_text, len(extracted_text), now),
    )
    inserted_lines = insert_layout_lines(
        connection,
        document_id=document_id,
        layout_lines=list(getattr(ocr_page_layout, "lines", []) or []),
        inserted_blocks=[],
        created_at=now,
    )
    inserted_spans = insert_layout_spans(
        connection,
        document_id=document_id,
        layout_spans=list(getattr(ocr_page_layout, "spans", []) or []),
        inserted_blocks=[],
        inserted_lines=inserted_lines,
        created_at=now,
    )
    line_links = build_chunk_layout_line_links_from_source_lines(
        chunks=chunks,
        chunk_ids=chunk_ids,
        document_id=document_id,
        persisted_lines=inserted_lines,
    )
    if not line_links:
        line_links = align_chunks_to_layout_lines(
            chunks=chunks,
            chunk_ids=chunk_ids,
            document_id=document_id,
            persisted_lines=inserted_lines,
        )
    inserted_line_links = insert_chunk_layout_line_links(connection, line_links, created_at=now)
    linked_chunks = {link.chunk_id for link in line_links if link.confidence in {"high", "medium"}}
    return {
        "pdf_page": pdf_page,
        "lines_written": len(inserted_lines),
        "spans_written": len(inserted_spans),
        "text_cache_written": 1,
        "chunk_line_links_written": inserted_line_links,
        "chunks_linked": len(linked_chunks),
        "chunks_unlinked": max(0, len(chunk_ids) - len(linked_chunks)),
    }


def insert_layout_blocks(
    connection: sqlite3.Connection,
    *,
    document_id: int,
    layout_blocks: list[PdfLayoutBlock],
    created_at: str,
) -> list[dict[str, Any]]:
    inserted: list[dict[str, Any]] = []
    for block in layout_blocks:
        normalized_text = block.normalized_text or normalize_layout_text(block.text)
        cursor = connection.execute(
            """
            INSERT INTO pdf_page_layout_blocks (
                document_id, pdf_page, page_width, page_height, source_backend,
                backend_version, block_index, block_type, text, normalized_text,
                bbox_json, polygon_json, confidence, text_hash, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                block.pdf_page,
                block.page_width,
                block.page_height,
                block.source_backend,
                block.backend_version,
                block.block_index,
                block.block_type,
                block.text,
                normalized_text,
                json.dumps(block.bbox, ensure_ascii=False),
                json.dumps(block.polygon, ensure_ascii=False) if block.polygon else None,
                block.confidence,
                block.text_hash or layout_text_hash(block.text),
                created_at,
            ),
        )
        inserted.append(
            {
                "id": int(cursor.lastrowid),
                "document_id": document_id,
                "pdf_page": block.pdf_page,
                "block_index": block.block_index,
                "text": block.text,
                "normalized_text": normalized_text,
                "bbox": block.bbox,
                "page_width": block.page_width,
                "page_height": block.page_height,
            }
        )
    return inserted


def insert_layout_lines(
    connection: sqlite3.Connection,
    *,
    document_id: int,
    layout_lines: list[PdfLayoutLine],
    inserted_blocks: list[dict[str, Any]],
    created_at: str,
) -> list[dict[str, Any]]:
    block_id_by_key = {
        (int(block["pdf_page"]), int(block["block_index"])): int(block["id"])
        for block in inserted_blocks
    }
    inserted: list[dict[str, Any]] = []
    for line in layout_lines:
        normalized_text = line.normalized_text or normalize_layout_text(line.text)
        block_id = line.block_id or block_id_by_key.get((int(line.pdf_page), int(line.block_index)))
        cursor = connection.execute(
            """
            INSERT INTO pdf_page_layout_lines (
                document_id, pdf_page, block_id, line_index, text, normalized_text,
                bbox_json, confidence, source_backend, text_hash, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                line.pdf_page,
                block_id,
                line.line_index,
                line.text,
                normalized_text,
                json.dumps(line.bbox, ensure_ascii=False),
                line.confidence,
                line.source_backend,
                line.text_hash or layout_text_hash(line.text),
                created_at,
            ),
        )
        inserted.append(
            {
                "id": int(cursor.lastrowid),
                "document_id": document_id,
                "pdf_page": line.pdf_page,
                "block_id": block_id,
                "block_index": line.block_index,
                "line_index": line.line_index,
                "text": line.text,
                "normalized_text": normalized_text,
                "bbox": line.bbox,
            }
        )
    return inserted


def insert_layout_spans(
    connection: sqlite3.Connection,
    *,
    document_id: int,
    layout_spans: list[PdfLayoutSpan],
    inserted_blocks: list[dict[str, Any]],
    inserted_lines: list[dict[str, Any]],
    created_at: str,
) -> list[dict[str, Any]]:
    block_id_by_key = {
        (int(block["pdf_page"]), int(block["block_index"])): int(block["id"])
        for block in inserted_blocks
    }
    line_id_by_key = {
        (int(line["pdf_page"]), int(line["block_index"]), int(line["line_index"])): int(line["id"])
        for line in inserted_lines
    }
    inserted: list[dict[str, Any]] = []
    for span in layout_spans:
        normalized_text = span.normalized_text or normalize_layout_text(span.text)
        block_id = span.block_id or block_id_by_key.get((int(span.pdf_page), int(span.block_index)))
        line_id = span.line_id
        if line_id is None and span.line_index is not None:
            line_id = line_id_by_key.get((int(span.pdf_page), int(span.block_index), int(span.line_index)))
        cursor = connection.execute(
            """
            INSERT INTO pdf_page_layout_spans (
                document_id, pdf_page, block_id, line_id, span_index, text,
                normalized_text, bbox_json, confidence, source_backend, text_hash, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                span.pdf_page,
                block_id,
                line_id,
                span.span_index,
                span.text,
                normalized_text,
                json.dumps(span.bbox, ensure_ascii=False),
                span.confidence,
                span.source_backend,
                span.text_hash or layout_text_hash(span.text),
                created_at,
            ),
        )
        inserted.append({"id": int(cursor.lastrowid), "line_id": line_id, "block_id": block_id})
    return inserted


def align_chunks_to_layout_blocks(
    *,
    chunks: list[Any],
    chunk_ids: list[int],
    document_id: int,
    persisted_blocks: list[dict[str, Any]],
    max_blocks_per_chunk: int = 8,
) -> list[ChunkLayoutLink]:
    blocks_by_page: dict[int, list[dict[str, Any]]] = {}
    for block in persisted_blocks:
        blocks_by_page.setdefault(int(block["pdf_page"]), []).append(block)

    links: list[ChunkLayoutLink] = []
    for chunk, chunk_id in zip(chunks, chunk_ids):
        page_start = getattr(chunk, "pdf_page_start", None)
        page_end = getattr(chunk, "pdf_page_end", None) or page_start
        if page_start is None or page_end is None:
            continue
        chunk_text = getattr(chunk, "chunk_text", "")
        chunk_compact = compact_match_text(chunk_text)
        chunk_tokens = _token_set(chunk_text)
        candidates: list[tuple[str, float, str, dict[str, Any]]] = []
        page_range_blocks: list[dict[str, Any]] = []
        for page in range(int(page_start), int(page_end) + 1):
            for block in blocks_by_page.get(page, []):
                page_range_blocks.append(block)
                block_text = block.get("text") or block.get("normalized_text") or ""
                block_compact = compact_match_text(block_text)
                if not block_compact:
                    continue
                if block_compact in chunk_compact or (len(chunk_compact) >= 24 and chunk_compact in block_compact):
                    candidates.append(("high", 1.0, "exact_text", block))
                    continue
                block_tokens = _token_set(block_text)
                score = _overlap_score(chunk_tokens, block_tokens)
                if score >= 0.72:
                    candidates.append(("high", score, "token_overlap", block))
                elif score >= 0.35:
                    candidates.append(("medium", score, "token_overlap", block))
        if not candidates:
            candidates.extend(("low", 0.0, "page_range", block) for block in page_range_blocks[:max_blocks_per_chunk])
        candidates.sort(key=lambda item: (0 if item[0] == "high" else 1, -item[1], int(item[3]["block_index"])))
        for confidence, score, method, block in candidates[:max_blocks_per_chunk]:
            links.append(
                ChunkLayoutLink(
                    chunk_id=int(chunk_id),
                    document_id=int(document_id),
                    pdf_page=int(block["pdf_page"]),
                    block_id=int(block["id"]),
                    match_method=method,
                    overlap_score=round(float(score), 4),
                    confidence=confidence,
                )
            )
    return links


def build_chunk_layout_line_links_from_source_lines(
    *,
    chunks: list[Any],
    chunk_ids: list[int],
    document_id: int,
    persisted_lines: list[dict[str, Any]],
) -> list[ChunkLayoutLineLink]:
    line_by_key = {
        f"{int(line['pdf_page'])}:{int(line['line_index'])}": line
        for line in persisted_lines
        if line.get("pdf_page") is not None and line.get("line_index") is not None
    }
    line_by_id = {int(line["id"]): line for line in persisted_lines if line.get("id") is not None}
    links: list[ChunkLayoutLineLink] = []
    for chunk, chunk_id in zip(chunks, chunk_ids):
        selected: list[dict[str, Any]] = []
        for key in getattr(chunk, "source_line_keys", []) or []:
            line = line_by_key.get(str(key))
            if line and int(line["id"]) not in {int(item["id"]) for item in selected}:
                selected.append(line)
        for raw_id in getattr(chunk, "source_line_ids", []) or []:
            try:
                line_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            line = line_by_id.get(line_id)
            if line and int(line["id"]) not in {int(item["id"]) for item in selected}:
                selected.append(line)
        for line in selected:
            links.append(
                ChunkLayoutLineLink(
                    chunk_id=int(chunk_id),
                    document_id=document_id,
                    pdf_page=int(line["pdf_page"]),
                    line_id=int(line["id"]),
                    match_method="ocr_layout_first_source_lines",
                    overlap_score=1.0,
                    confidence="high",
                )
            )
    return links


def align_chunks_to_layout_lines(
    *,
    chunks: list[Any],
    chunk_ids: list[int],
    document_id: int,
    persisted_lines: list[dict[str, Any]],
    max_lines_per_chunk: int = 10,
) -> list[ChunkLayoutLineLink]:
    lines_by_page: dict[int, list[dict[str, Any]]] = {}
    for line in persisted_lines:
        lines_by_page.setdefault(int(line["pdf_page"]), []).append(line)

    links: list[ChunkLayoutLineLink] = []
    for chunk, chunk_id in zip(chunks, chunk_ids):
        page_start = getattr(chunk, "pdf_page_start", None)
        page_end = getattr(chunk, "pdf_page_end", None) or page_start
        if page_start is None or page_end is None:
            continue
        chunk_text = getattr(chunk, "chunk_text", "")
        best_group: dict[str, Any] | None = None
        for page in range(int(page_start), int(page_end) + 1):
            page_lines = lines_by_page.get(page, [])
            page_width, page_height = _infer_page_size(page_lines)
            group = select_best_contiguous_line_group(
                chunk_text,
                page_lines,
                page_width=page_width,
                page_height=page_height,
                max_lines=max_lines_per_chunk,
            )
            if group.get("selected_lines") and (
                best_group is None or float(group.get("group_score") or 0.0) > float(best_group.get("group_score") or 0.0)
            ):
                best_group = group
        if not best_group:
            continue
        for selected in best_group.get("selected_lines", []):
            line = selected["line"]
            score = float(selected.get("score") or 0.0)
            if score >= 0.72:
                confidence = "high"
                method = "exact_text" if selected.get("exact") else "token_overlap"
            else:
                confidence = "medium"
                method = "line_window"
            links.append(
                ChunkLayoutLineLink(
                    chunk_id=int(chunk_id),
                    document_id=int(document_id),
                    pdf_page=int(line["pdf_page"]),
                    line_id=int(line["id"]),
                    match_method=method,
                    overlap_score=round(float(score), 4),
                    confidence=confidence,
                )
            )
    return links


def classify_layout_line_role(line: Any, page_width: float | None = None, page_height: float | None = None) -> str:
    text = normalize_ocr_line_text_for_display(_line_value(line, "text", ""))
    compact = re.sub(r"\s+", "", text)
    bbox = _line_bbox(line)
    y0 = float(bbox.get("y0", 0.0))
    y1 = float(bbox.get("y1", y0))
    x0 = float(bbox.get("x0", 0.0))
    x1 = float(bbox.get("x1", x0))
    width = float(page_width or max(float(bbox.get("page_width", 0.0)), x1, 1.0))
    height = float(page_height or max(float(bbox.get("page_height", 0.0)), y1, 1.0))
    top_ratio = y0 / height if height else 0.0
    bottom_ratio = y1 / height if height else 0.0
    center_ratio = ((x0 + x1) / 2.0) / width if width else 0.0

    if not compact:
        return "unknown"
    if re.fullmatch(r"\d{1,4}|[ivxlcdmIVXLCDM]{1,8}", compact) and (top_ratio <= 0.12 or bottom_ratio >= 0.9):
        if 0.35 <= center_ratio <= 1.05:
            return "page_number"
    if top_ratio <= 0.08 and re.search(r"第\s*\d+\s*章|chapter\s+\d+|单源最短路径|最短路径", text, flags=re.IGNORECASE):
        if len(compact) <= 28:
            return "header"
    if bottom_ratio >= 0.94 and y0 >= max(250.0, height * 0.75) and len(compact) <= 40:
        return "footer"
    if re.match(r"^(图|表)\s*\d+(?:[-－]\d+)?\b|^fig(?:ure)?\.?\s*\d+", text, flags=re.IGNORECASE):
        return "figure_caption"
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", text))
    math_markers = len(re.findall(r"[\\δ∞∈≤≥=+\-*/_^()]|<math", str(_line_value(line, "text", ""))))
    if math_markers >= 6 and cjk_count <= 4:
        return "formula"
    return "body"


def select_best_contiguous_line_group(
    chunk_text: Any,
    candidate_lines: list[dict[str, Any]],
    *,
    page_width: float | None = None,
    page_height: float | None = None,
    max_lines: int = 10,
) -> dict[str, Any]:
    chunk_match_text = normalize_ocr_line_text_for_match(chunk_text)
    chunk_compact = compact_match_text(chunk_match_text)
    chunk_tokens = _token_set(chunk_match_text)
    allowed_figure_labels = set(re.findall(r"(?:图|表)\s*\d+(?:[-－]\d+)?", str(chunk_text or "")))
    diagnostics: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    sorted_lines = sorted(candidate_lines, key=lambda item: (int(item.get("pdf_page", 0)), int(item.get("line_index", 0))))
    for line in sorted_lines:
        role = classify_layout_line_role(line, page_width, page_height)
        text = normalize_ocr_line_text_for_match(line.get("text") or line.get("normalized_text") or "")
        score, exact = _line_relevance_score(chunk_match_text, chunk_compact, chunk_tokens, text)
        excluded = role in {"header", "page_number", "footer"}
        if role == "figure_caption" and not any(label and label in normalize_ocr_line_text_for_display(line.get("text")) for label in allowed_figure_labels):
            excluded = True
        item = {
            "line": line,
            "line_index": int(line.get("line_index", 0)),
            "role": role,
            "score": round(float(score), 4),
            "exact": exact,
            "excluded": excluded,
            "display_text": normalize_ocr_line_text_for_display(line.get("text") or ""),
        }
        diagnostics.append({key: value for key, value in item.items() if key != "line"})
        if not excluded and text:
            eligible.append(item)

    best: dict[str, Any] | None = None
    limit = max(1, int(max_lines or 10))
    for start in range(len(eligible)):
        for end in range(start, min(len(eligible), start + limit)):
            window = eligible[start : end + 1]
            if not _line_indexes_are_contiguous(window):
                break
            strong_count = sum(1 for item in window if float(item["score"]) >= 0.72)
            medium_count = sum(1 for item in window if 0.35 <= float(item["score"]) < 0.72)
            if strong_count == 0 and medium_count < 2:
                continue
            window_text = " ".join(str(item["display_text"]) for item in window)
            window_overlap = _overlap_score(chunk_tokens, _token_set(window_text))
            low_count = sum(1 for item in window if float(item["score"]) < 0.35)
            count = len(window)
            density = sum(float(item["score"]) for item in window) / count
            group_score = (
                sum(float(item["score"]) for item in window)
                + 1.25 * window_overlap
                + 0.08 * strong_count
                + 0.03 * medium_count
                - 0.06 * max(0, count - 6)
                - 0.12 * low_count
            )
            if density < 0.28:
                continue
            candidate = {
                "selected_lines": window,
                "group_score": round(float(group_score), 4),
                "window_overlap": round(float(window_overlap), 4),
                "density": round(float(density), 4),
            }
            if best is None or (float(candidate["group_score"]), len(window)) > (float(best["group_score"]), len(best["selected_lines"])):
                best = candidate

    selected = list(best["selected_lines"]) if best else []
    while selected and float(selected[0]["score"]) < 0.25:
        selected.pop(0)
    while selected and float(selected[-1]["score"]) < 0.25:
        selected.pop()
    if not selected:
        best = {"selected_lines": [], "group_score": 0.0, "window_overlap": 0.0, "density": 0.0}
    else:
        best = {**(best or {}), "selected_lines": selected}
    selected_indexes = {int(item["line_index"]) for item in selected}
    return {
        **best,
        "diagnostics": diagnostics,
        "selected_line_indexes": [int(item["line_index"]) for item in selected],
        "line_roles": {int(item["line_index"]): item["role"] for item in diagnostics},
        "selected_roles": {int(item["line_index"]): item["role"] for item in selected},
        "selected_text": [str(item["display_text"]) for item in selected],
        "excluded_line_indexes": [int(item["line_index"]) for item in diagnostics if item["excluded"]],
        "unselected_relevant_line_indexes": [
            int(item["line_index"])
            for item in diagnostics
            if int(item["line_index"]) not in selected_indexes and not item["excluded"] and float(item["score"]) >= 0.35
        ],
    }


def _line_relevance_score(chunk_match_text: str, chunk_compact: str, chunk_tokens: set[str], line_match_text: str) -> tuple[float, bool]:
    line_compact = compact_match_text(line_match_text)
    if not line_compact:
        return 0.0, False
    if line_compact in chunk_compact or (len(chunk_compact) >= 24 and chunk_compact in line_compact):
        return 1.0, True
    token_score = _overlap_score(chunk_tokens, _token_set(line_match_text))
    char_score = _char_ngram_overlap(chunk_match_text, line_match_text)
    return max(token_score, char_score), False


def _char_ngram_overlap(left: str, right: str, n: int = 3) -> float:
    left_compact = compact_match_text(left)
    right_compact = compact_match_text(right)
    if len(left_compact) < n or len(right_compact) < n:
        return 0.0
    left_grams = {left_compact[index : index + n] for index in range(len(left_compact) - n + 1)}
    right_grams = {right_compact[index : index + n] for index in range(len(right_compact) - n + 1)}
    return _overlap_score(left_grams, right_grams)


def _line_indexes_are_contiguous(window: list[dict[str, Any]]) -> bool:
    if not window:
        return False
    indexes = [int(item["line_index"]) for item in window]
    return indexes == list(range(indexes[0], indexes[-1] + 1))


def relink_chunks_to_ocr_lines(
    *,
    document_id: int,
    page_start: int,
    page_end: int,
    chunk_ids: list[int] | None = None,
    dry_run: bool = True,
    db_path: str | Path | None = None,
    source_backend: str = "surya_ocr",
    explain_alignment: bool = False,
    allow_multi_page: bool = False,
) -> dict[str, Any]:
    if page_end < page_start:
        raise ValueError("page_end must be >= page_start")
    if page_end > page_start and not allow_multi_page:
        raise ValueError("OCR line relink is limited to one page unless allow_multi_page=True")
    if db_path is None:
        from app.core.paths import DEFAULT_DB_PATH

        db_path = DEFAULT_DB_PATH
    db = Path(db_path)
    uri = f"file:{db.as_posix()}?mode=ro" if dry_run else str(db)
    backup_path: Path | None = None
    reports: list[dict[str, Any]] = []
    with sqlite3.connect(uri, uri=dry_run) as connection:
        connection.row_factory = sqlite3.Row
        if not dry_run:
            backup_path = _backup_sqlite_for_relink(db)
        params: list[Any] = [document_id, page_end, page_start]
        chunk_filter = ""
        if chunk_ids:
            chunk_filter = f" AND id IN ({', '.join(['?'] * len(chunk_ids))})"
            params.extend(int(chunk_id) for chunk_id in chunk_ids)
        rows = connection.execute(
            f"""
            SELECT
                id, chunk_index, heading_path, chunk_text, char_count, token_count,
                overlap_before, overlap_after, pdf_page_start, pdf_page_end, pdf_path
            FROM knowledge_chunks
            WHERE document_id = ?
              AND COALESCE(pdf_page_start, pdf_page_end, 0) <= ?
              AND COALESCE(pdf_page_end, pdf_page_start, 0) >= ?
              {chunk_filter}
            ORDER BY chunk_index, id
            """,
            params,
        ).fetchall()
        lines = _load_ocr_lines_for_range(connection, document_id, page_start, page_end, source_backend)
        page_sizes = {
            page: _resolve_pdf_page_size(connection, document_id, page, lines_by_page)
            for page, lines_by_page in _group_lines_by_page(lines).items()
        }
        for row in rows:
            chunk = SimpleNamespace(
                pdf_page_start=row["pdf_page_start"],
                pdf_page_end=row["pdf_page_end"],
                chunk_text=row["chunk_text"],
            )
            current = _load_current_line_links(connection, document_id, int(row["id"]), page_start, page_end, source_backend)
            selected_links = align_chunks_to_layout_lines(
                chunks=[chunk],
                chunk_ids=[int(row["id"])],
                document_id=document_id,
                persisted_lines=lines,
            )
            selected_ids = {link.line_id for link in selected_links}
            proposed_lines = [line for line in lines if int(line["id"]) in selected_ids]
            page_width, page_height = page_sizes.get(int(row["pdf_page_start"] or page_start), _infer_page_size(lines))
            group = select_best_contiguous_line_group(
                row["chunk_text"],
                [line for line in lines if int(line["pdf_page"]) == int(row["pdf_page_start"] or page_start)],
                page_width=page_width,
                page_height=page_height,
            )
            report = {
                "chunk_id": int(row["id"]),
                "current_linked_line_indexes": [int(item["line_index"]) for item in current],
                "proposed_linked_line_indexes": [int(line["line_index"]) for line in proposed_lines],
                "removed_line_indexes": sorted(
                    set(int(item["line_index"]) for item in current) - set(int(line["line_index"]) for line in proposed_lines)
                ),
                "added_line_indexes": sorted(
                    set(int(line["line_index"]) for line in proposed_lines) - set(int(item["line_index"]) for item in current)
                ),
                "proposed_line_roles": {
                    int(line["line_index"]): classify_layout_line_role(line, page_width, page_height) for line in proposed_lines
                },
                "current_union_bbox": _union_bbox(current),
                "proposed_union_bbox": _union_bbox(proposed_lines),
                "current_union_area_ratio": _union_area_ratio(current, page_width, page_height),
                "proposed_union_area_ratio": _union_area_ratio(proposed_lines, page_width, page_height),
                "proposed_links": [link.to_dict() for link in selected_links],
            }
            if explain_alignment:
                report["alignment_diagnostics"] = group.get("diagnostics", [])
                report["selected_text"] = group.get("selected_text", [])
            reports.append(report)
            if not dry_run:
                _delete_chunk_line_links_for_page(connection, document_id, int(row["id"]), page_start, page_end, source_backend)
                insert_chunk_layout_line_links(connection, selected_links, created_at=datetime.utcnow().isoformat(sep=" ", timespec="seconds"))
        if not dry_run:
            connection.commit()
    return {
        "document_id": document_id,
        "page_start": page_start,
        "page_end": page_end,
        "chunk_ids": chunk_ids,
        "dry_run": dry_run,
        "no_database_writes_performed": dry_run,
        "source_backend": source_backend,
        "backup_path": str(backup_path) if backup_path else None,
        "chunks": reports,
    }


def _load_ocr_lines_for_range(
    connection: sqlite3.Connection,
    document_id: int,
    page_start: int,
    page_end: int,
    source_backend: str,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT id, document_id, pdf_page, block_id, line_index, text, normalized_text, bbox_json, confidence, source_backend
        FROM pdf_page_layout_lines
        WHERE document_id = ? AND pdf_page BETWEEN ? AND ? AND source_backend = ?
        ORDER BY pdf_page, line_index
        """,
        (document_id, page_start, page_end, source_backend),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        bbox = json.loads(row["bbox_json"] or "{}") if isinstance(row, sqlite3.Row) else json.loads(row[7] or "{}")
        getter = row.__getitem__
        result.append(
            {
                "id": int(getter("id")),
                "document_id": int(getter("document_id")),
                "pdf_page": int(getter("pdf_page")),
                "block_id": getter("block_id"),
                "block_index": 0,
                "line_index": int(getter("line_index")),
                "text": getter("text"),
                "normalized_text": getter("normalized_text"),
                "bbox": bbox,
                "confidence": getter("confidence"),
                "source_backend": getter("source_backend"),
            }
        )
    return result


def _load_current_line_links(
    connection: sqlite3.Connection,
    document_id: int,
    chunk_id: int,
    page_start: int,
    page_end: int,
    source_backend: str,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT ln.id, ln.document_id, ln.pdf_page, ln.block_id, ln.line_index, ln.text, ln.normalized_text,
               ln.bbox_json, ln.confidence, ln.source_backend, l.match_method, l.overlap_score, l.confidence AS link_confidence
        FROM chunk_layout_line_links l
        JOIN pdf_page_layout_lines ln ON ln.id = l.line_id
        WHERE l.document_id = ? AND l.chunk_id = ? AND l.pdf_page BETWEEN ? AND ? AND ln.source_backend = ?
        ORDER BY ln.pdf_page, ln.line_index
        """,
        (document_id, chunk_id, page_start, page_end, source_backend),
    ).fetchall()
    current: list[dict[str, Any]] = []
    for row in rows:
        current.append(
            {
                "id": int(row["id"]),
                "document_id": int(row["document_id"]),
                "pdf_page": int(row["pdf_page"]),
                "block_id": row["block_id"],
                "block_index": 0,
                "line_index": int(row["line_index"]),
                "text": row["text"],
                "normalized_text": row["normalized_text"],
                "bbox": json.loads(row["bbox_json"] or "{}"),
                "confidence": row["confidence"],
                "source_backend": row["source_backend"],
                "match_method": row["match_method"],
                "overlap_score": row["overlap_score"],
                "link_confidence": row["link_confidence"],
            }
        )
    return current


def _delete_chunk_line_links_for_page(
    connection: sqlite3.Connection,
    document_id: int,
    chunk_id: int,
    page_start: int,
    page_end: int,
    source_backend: str,
) -> None:
    connection.execute(
        """
        DELETE FROM chunk_layout_line_links
        WHERE id IN (
            SELECT l.id
            FROM chunk_layout_line_links l
            JOIN pdf_page_layout_lines ln ON ln.id = l.line_id
            WHERE l.document_id = ?
              AND l.chunk_id = ?
              AND l.pdf_page BETWEEN ? AND ?
              AND ln.source_backend = ?
        )
        """,
        (document_id, chunk_id, page_start, page_end, source_backend),
    )


def _group_lines_by_page(lines: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for line in lines:
        grouped.setdefault(int(line["pdf_page"]), []).append(line)
    return grouped


def _resolve_pdf_page_size(
    connection: sqlite3.Connection,
    document_id: int,
    pdf_page: int,
    fallback_lines: list[dict[str, Any]],
) -> tuple[float | None, float | None]:
    document = connection.execute(
        "SELECT pdf_path, source_path FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()
    pdf_path = str((document["pdf_path"] if isinstance(document, sqlite3.Row) else document[0]) or "")
    source_path = str((document["source_path"] if isinstance(document, sqlite3.Row) else document[1]) or "")
    path = Path(pdf_path or source_path)
    if path.exists():
        try:
            import fitz  # type: ignore

            pdf = fitz.open(path)
            rect = pdf[int(pdf_page) - 1].rect
            width, height = float(rect.width), float(rect.height)
            pdf.close()
            return width, height
        except Exception:
            pass
    return _infer_page_size(fallback_lines)


def _infer_page_size(lines: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    max_x = 0.0
    max_y = 0.0
    for line in lines:
        bbox = _line_bbox(line)
        max_x = max(max_x, float(bbox.get("x1", 0.0)))
        max_y = max(max_y, float(bbox.get("y1", 0.0)))
        max_x = max(max_x, float(line.get("page_width") or 0.0))
        max_y = max(max_y, float(line.get("page_height") or 0.0))
    return (max_x or None), (max_y or None)


def _union_bbox(lines: list[dict[str, Any]]) -> dict[str, float] | None:
    rects = [_line_bbox(line) for line in lines if _valid_bbox(_line_bbox(line))]
    if not rects:
        return None
    return {
        "x0": min(float(rect["x0"]) for rect in rects),
        "y0": min(float(rect["y0"]) for rect in rects),
        "x1": max(float(rect["x1"]) for rect in rects),
        "y1": max(float(rect["y1"]) for rect in rects),
    }


def _union_area_ratio(lines: list[dict[str, Any]], page_width: float | None, page_height: float | None) -> float | None:
    union = _union_bbox(lines)
    if not union or not page_width or not page_height:
        return None
    return round(((union["x1"] - union["x0"]) * (union["y1"] - union["y0"])) / (float(page_width) * float(page_height)), 6)


def _line_value(line: Any, key: str, default: Any = None) -> Any:
    if isinstance(line, dict):
        return line.get(key, default)
    return getattr(line, key, default)


def _line_bbox(line: Any) -> dict[str, Any]:
    bbox = _line_value(line, "bbox")
    if isinstance(bbox, dict):
        return bbox
    bbox_json = _line_value(line, "bbox_json")
    if isinstance(bbox_json, str):
        try:
            value = json.loads(bbox_json)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            return {}
    return {}


def _backup_sqlite_for_relink(db_path: Path) -> Path:
    if not db_path.exists():
        raise FileNotFoundError(str(db_path))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"research_memory_before_ocr_line_relink_{timestamp}.db"
    shutil.copy2(db_path, backup_path)
    return backup_path


def insert_chunk_layout_links(connection: sqlite3.Connection, links: list[ChunkLayoutLink], *, created_at: str) -> int:
    for link in links:
        connection.execute(
            """
            INSERT INTO chunk_layout_links (
                chunk_id, document_id, pdf_page, block_id, match_method,
                overlap_score, confidence, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                link.chunk_id,
                link.document_id,
                link.pdf_page,
                link.block_id,
                link.match_method,
                link.overlap_score,
                link.confidence,
                created_at,
            ),
        )
    return len(links)


def insert_chunk_layout_line_links(connection: sqlite3.Connection, links: list[ChunkLayoutLineLink], *, created_at: str) -> int:
    for link in links:
        connection.execute(
            """
            INSERT INTO chunk_layout_line_links (
                chunk_id, document_id, pdf_page, line_id, match_method,
                overlap_score, confidence, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                link.chunk_id,
                link.document_id,
                link.pdf_page,
                link.line_id,
                link.match_method,
                link.overlap_score,
                link.confidence,
                created_at,
            ),
        )
    return len(links)


def load_layout_location_for_chunk(connection: sqlite3.Connection, *, document_id: int, chunk_id: int) -> dict[str, Any] | None:
    line_location = load_layout_line_location_for_chunk(connection, document_id=document_id, chunk_id=chunk_id)
    if line_location:
        return line_location
    return load_layout_block_location_for_chunk(connection, document_id=document_id, chunk_id=chunk_id)


def load_layout_line_location_for_chunk(connection: sqlite3.Connection, *, document_id: int, chunk_id: int) -> dict[str, Any] | None:
    try:
        rows = connection.execute(
            """
            SELECT
                ln.id, ln.pdf_page, ln.bbox_json, ln.text, l.match_method,
                l.overlap_score, l.confidence
            FROM chunk_layout_line_links l
            JOIN pdf_page_layout_lines ln ON ln.id = l.line_id
            WHERE l.document_id = ? AND l.chunk_id = ? AND l.confidence IN ('high', 'medium')
            ORDER BY ln.pdf_page, ln.line_index
            """,
            (document_id, chunk_id),
        ).fetchall()
    except sqlite3.OperationalError:
        return None
    if not rows:
        return None
    first_page = int(rows[0][1])
    same_page_rows = [row for row in rows if int(row[1]) == first_page]
    rects: list[dict[str, float]] = []
    snippets: list[str] = []
    confidences: list[str] = []
    scores: list[float] = []
    methods: list[str] = []
    for row in same_page_rows:
        try:
            rect = json.loads(row[2] or "{}")
        except json.JSONDecodeError:
            continue
        if _valid_bbox(rect):
            rects.append({key: float(rect[key]) for key in ("x0", "y0", "x1", "y1")})
            snippets.append(normalize_ocr_line_text_for_display(row[3] or ""))
            methods.append(str(row[4] or "layout_line_match"))
            scores.append(float(row[5] or 0.0))
            confidences.append(str(row[6] or "medium"))
    if not rects:
        return None
    confidence = "high" if any(value == "high" for value in confidences) else "medium"
    primary_method = methods[0] if methods else "layout_line_match"
    return {
        "pdf_page": first_page,
        "rects": rects,
        "confidence": confidence,
        "snippet_used": " ".join(snippets)[:220],
        "match_method": primary_method,
        "locator_status": "layout_line_location",
        "visual_mode": "layout_line_highlight",
        "matched_lines": snippets,
        "highlight_count": len(rects),
        "overlap_score": max(scores) if scores else None,
        "source_match_methods": methods,
    }


def load_layout_block_location_for_chunk(connection: sqlite3.Connection, *, document_id: int, chunk_id: int) -> dict[str, Any] | None:
    try:
        rows = connection.execute(
            """
            SELECT
                b.id, b.pdf_page, b.page_width, b.page_height, b.bbox_json,
                b.text, l.match_method, l.overlap_score, l.confidence
            FROM chunk_layout_links l
            JOIN pdf_page_layout_blocks b ON b.id = l.block_id
            WHERE l.document_id = ? AND l.chunk_id = ? AND l.confidence IN ('high', 'medium')
            ORDER BY b.pdf_page, b.block_index
            """,
            (document_id, chunk_id),
        ).fetchall()
    except sqlite3.OperationalError:
        return None
    if not rows:
        return None
    first_page = int(rows[0][1])
    same_page_rows = [row for row in rows if int(row[1]) == first_page]
    rects: list[dict[str, float]] = []
    snippets: list[str] = []
    confidences: list[str] = []
    scores: list[float] = []
    page_width = None
    page_height = None
    for row in same_page_rows:
        try:
            rect = json.loads(row[4] or "{}")
        except json.JSONDecodeError:
            continue
        if _valid_bbox(rect):
            rects.append({key: float(rect[key]) for key in ("x0", "y0", "x1", "y1")})
            snippets.append(str(row[5] or ""))
            confidences.append(str(row[8] or "medium"))
            scores.append(float(row[7] or 0.0))
            page_width = row[2] if row[2] is not None else page_width
            page_height = row[3] if row[3] is not None else page_height
    if not rects:
        return None
    confidence = "high" if any(value == "high" for value in confidences) else "medium"
    return {
        "pdf_page": first_page,
        "rects": rects,
        "page_width": page_width,
        "page_height": page_height,
        "confidence": confidence,
        "snippet_used": " ".join(snippets)[:220],
        "match_method": "layout_block_match",
        "locator_status": "layout_block_location",
        "visual_mode": "layout_block_highlight",
        "overlap_score": max(scores) if scores else None,
    }


def _valid_bbox(rect: dict[str, Any]) -> bool:
    try:
        return all(float(rect[key]) >= 0 for key in ("x0", "y0", "x1", "y1")) and float(rect["x1"]) > float(rect["x0"]) and float(rect["y1"]) > float(rect["y0"])
    except (KeyError, TypeError, ValueError):
        return False


def _token_set(text: Any) -> set[str]:
    normalized = normalize_layout_text(text)
    tokens = set(re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2,}", normalized))
    for cjk_run in re.findall(r"[\u4e00-\u9fff]{4,}", normalized):
        tokens.update(cjk_run[index : index + 2] for index in range(0, len(cjk_run) - 1))
    return tokens


def _overlap_score(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, min(len(left), len(right)))
