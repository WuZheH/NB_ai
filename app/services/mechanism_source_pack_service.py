from __future__ import annotations

import json
import sqlite3
from typing import Any, Mapping

from app.services.citation_renderer_service import add_citation_fields
from app.services.mechanism_source_parity_service import build_mechanism_source_pack
from app.services.note_object_alignment_service import build_note_object_alignment_dry_run


REVIEWED_OBJECT_STATUSES = {"accepted", "approved", "edited", "reviewed", "committed"}


def build_mechanism_source_pack_from_inputs(
    *,
    note: Mapping[str, Any] | None = None,
    source_excerpt: Mapping[str, Any] | None = None,
    matched_chunks: list[Mapping[str, Any]] | None = None,
    linked_objects: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    note = note or {}
    source_excerpt = source_excerpt or {}
    matched_chunks = list(matched_chunks or [])
    linked_objects = list(linked_objects or [])
    source_note_id = note.get("server_note_id") or note.get("client_note_id") or note.get("id")
    pack = build_mechanism_source_pack(
        note=note,
        source_note_id=source_note_id,
        source_excerpt=source_excerpt,
        matched_chunks=matched_chunks,
        linked_objects=linked_objects,
    )
    warnings = _pack_warnings(pack)
    blocking = [warning for warning in warnings if warning == "source_mode_unknown"]
    pack["citation_tokens"] = _citation_tokens(note, source_excerpt, linked_objects)
    pack["readiness"] = {
        "ready_for_prompt_export": not blocking,
        "missing": blocking,
    }
    pack["warnings"] = warnings
    return {
        "mechanism_source_pack": pack,
        **_safety_flags(),
    }


def build_mechanism_source_pack_dry_run(
    conn: sqlite3.Connection,
    note: Mapping[str, Any],
    *,
    alignment_result: Mapping[str, Any] | None = None,
    object_ids: list[int] | None = None,
    linked_objects: list[Mapping[str, Any]] | None = None,
    chunk_id: int | None = None,
    document_id: int | None = None,
) -> dict[str, Any]:
    conn.row_factory = sqlite3.Row
    alignment = dict(alignment_result or build_note_object_alignment_dry_run(conn, note, document_id=document_id))
    document_alignment = alignment.get("document_alignment") or {}
    object_alignment = alignment.get("object_alignment") or {}
    matched_chunk_ids = list(document_alignment.get("matched_chunk_ids") or [])
    if chunk_id is not None and chunk_id not in matched_chunk_ids:
        matched_chunk_ids.insert(0, chunk_id)
    chunks = _load_chunks(conn, matched_chunk_ids)
    primary_chunk = chunks[0] if chunks else {}
    source_excerpt = {
        "selected_text": note.get("selected_text"),
        "chunk_id": primary_chunk.get("chunk_id"),
        "document_id": primary_chunk.get("document_id") or document_alignment.get("matched_document_id"),
        "document_title": primary_chunk.get("document_title"),
        "chapter_title": primary_chunk.get("chapter_title"),
        "chapter_id": primary_chunk.get("chapter_id") or document_alignment.get("matched_chapter_id"),
        "chunk_text": primary_chunk.get("chunk_text"),
        "pdf_page": primary_chunk.get("pdf_page"),
        "page_label": note.get("page_label"),
    }
    linked_ids = list(object_ids or object_alignment.get("matched_object_ids") or [])
    linked_object_rows = list(linked_objects) if linked_objects is not None else _load_objects(conn, linked_ids)
    result = build_mechanism_source_pack_from_inputs(
        note=note,
        source_excerpt=source_excerpt,
        matched_chunks=chunks,
        linked_objects=linked_object_rows,
    )
    result["alignment_result"] = alignment
    result["mechanism_source_pack"]["readiness"]["ready_for_mechanism_source_pack"] = result[
        "mechanism_source_pack"
    ]["readiness"]["ready_for_prompt_export"]
    return result


def _load_chunks(conn: sqlite3.Connection, chunk_ids: list[int]) -> list[dict[str, Any]]:
    if not chunk_ids or not _table_exists(conn, "knowledge_chunks"):
        return []
    placeholders = ", ".join("?" for _ in chunk_ids)
    rows = conn.execute(
        f"""
        SELECT k.id AS chunk_id, k.document_id, k.chapter_id, k.heading_path,
               k.chunk_text, k.pdf_page_start AS pdf_page,
               d.title AS document_title, bc.title AS chapter_title
        FROM knowledge_chunks k
        LEFT JOIN documents d ON d.id = k.document_id
        LEFT JOIN book_chapters bc ON bc.id = k.chapter_id
        WHERE k.id IN ({placeholders})
        ORDER BY k.id
        """,
        tuple(chunk_ids),
    ).fetchall()
    by_id = {int(row["chunk_id"]): dict(row) for row in rows}
    return [by_id[chunk_id] for chunk_id in chunk_ids if chunk_id in by_id]


def _load_objects(conn: sqlite3.Connection, object_ids: list[int]) -> list[dict[str, Any]]:
    if not object_ids or not _table_exists(conn, "object_candidates"):
        return []
    placeholders = ", ".join("?" for _ in object_ids)
    rows = conn.execute(
        f"""
        SELECT id AS object_id, document_id, object_name, object_type, review_status,
               status, mapped_chunk_ids_json, evidence_refs_json
        FROM object_candidates
        WHERE id IN ({placeholders})
        ORDER BY id
        """,
        tuple(object_ids),
    ).fetchall()
    return [dict(row) for row in rows if _is_reviewed_object(dict(row))]


def _citation_tokens(
    note: Mapping[str, Any],
    source_excerpt: Mapping[str, Any],
    linked_objects: list[Mapping[str, Any]],
) -> list[str]:
    items = []
    if note.get("server_note_id") or note.get("client_note_id"):
        items.append(
            add_citation_fields(
                {
                    "source_type": "note",
                    "server_note_id": note.get("server_note_id"),
                    "client_note_id": note.get("client_note_id"),
                    "note_id": note.get("server_note_id") or note.get("client_note_id"),
                    "document_id": source_excerpt.get("document_id"),
                    "chapter_id": source_excerpt.get("chapter_id"),
                    "chunk_id": source_excerpt.get("chunk_id"),
                    "pdf_page": source_excerpt.get("pdf_page"),
                    "page_label": source_excerpt.get("page_label"),
                    "document_title": source_excerpt.get("document_title"),
                },
                "zotero_notes",
            )
        )
    if source_excerpt.get("chunk_id") or source_excerpt.get("document_id"):
        items.append(
            add_citation_fields(
                {
                    "source_type": "chunk",
                    "chunk_id": source_excerpt.get("chunk_id"),
                    "document_id": source_excerpt.get("document_id"),
                    "chapter_id": source_excerpt.get("chapter_id"),
                    "pdf_page": source_excerpt.get("pdf_page"),
                    "page_label": source_excerpt.get("page_label"),
                    "document_title": source_excerpt.get("document_title"),
                    "chapter_title": source_excerpt.get("chapter_title"),
                },
                "evidence_chunks",
            )
        )
    for obj in linked_objects:
        items.append(
            add_citation_fields(
                {
                    "source_type": "object_candidate",
                    "object_candidate_id": obj.get("object_id") or obj.get("id"),
                    "object_name": obj.get("object_name"),
                    "document_id": obj.get("document_id"),
                    "source_chunk_ids": _json_list(obj.get("mapped_chunk_ids_json")),
                },
                "objects",
            )
        )
    tokens: list[str] = []
    for item in items:
        for token in item.get("citation_tokens") or []:
            if token not in tokens:
                tokens.append(token)
    return tokens


def _pack_warnings(pack: Mapping[str, Any]) -> list[str]:
    warnings = []
    source_mode = str(pack.get("source_mode") or "unknown")
    if source_mode == "unknown":
        warnings.append("source_mode_unknown")
    if not str((pack.get("primary_user_note") or {}).get("note_text") or "").strip():
        warnings.append("primary_user_note_empty")
    if not (
        str((pack.get("primary_source_excerpt") or {}).get("selected_text") or "").strip()
        or str((pack.get("primary_source_excerpt") or {}).get("chunk_text") or "").strip()
    ):
        warnings.append("primary_source_excerpt_empty")
    if not (pack.get("linked_knowledge") or {}).get("objects"):
        warnings.append("linked_objects_empty")
    return warnings


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _json_list(value: Any) -> list[Any]:
    try:
        loaded = json.loads(str(value or "[]"))
    except (TypeError, ValueError):
        return []
    return loaded if isinstance(loaded, list) else []


def _is_reviewed_object(row: Mapping[str, Any]) -> bool:
    return str(row.get("review_status") or "").casefold() in REVIEWED_OBJECT_STATUSES or str(
        row.get("status") or ""
    ).casefold() in REVIEWED_OBJECT_STATUSES


def _safety_flags() -> dict[str, bool]:
    return {
        "db_write_performed": False,
        "llm_called": False,
        "relation_generated": False,
        "mechanism_generated": False,
        "zotero_write_performed": False,
        "vector_store_write_performed": False,
    }
