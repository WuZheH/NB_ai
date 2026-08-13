from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Mapping

from app.services import inspiration_note_matching_service


def build_note_object_alignment_dry_run(
    conn: sqlite3.Connection,
    note: Mapping[str, Any],
    *,
    document_id: int | None = None,
    chapter_id: int | None = None,
) -> dict[str, Any]:
    conn.row_factory = sqlite3.Row
    match = inspiration_note_matching_service.build_inspiration_match_report(
        conn,
        note,
        document_id=document_id or _optional_int(note.get("matched_document_id")),
        max_candidates=5,
    )
    matched_chunk_ids = _matched_chunk_ids(note, match)
    matched_document_id = _optional_int(match.get("matched_document_id")) or document_id
    matched_chapter_id = chapter_id or _chapter_for_chunk(conn, _first_int(matched_chunk_ids))
    object_alignment = _object_alignment(conn, note, matched_document_id, matched_chunk_ids)
    readiness_missing = []
    if matched_document_id is None:
        readiness_missing.append("matched_document_id")
    if not matched_chunk_ids:
        readiness_missing.append("matched_chunk_ids")
    if not _text(note.get("note_text")) and not _text(note.get("selected_text")):
        readiness_missing.append("primary_note_or_source_excerpt")
    if not object_alignment["matched_object_ids"]:
        readiness_missing.append("object_candidate_review")
    return {
        "note_identity": {
            "server_note_id": note.get("server_note_id"),
            "client_note_id": note.get("client_note_id"),
            "zotero_attachment_key": note.get("zotero_attachment_key"),
            "zotero_annotation_key": note.get("zotero_annotation_key"),
        },
        "document_alignment": {
            "matched_document_id": matched_document_id,
            "matched_chapter_id": matched_chapter_id,
            "matched_chunk_ids": matched_chunk_ids,
            "alignment_confidence": _confidence_score(match.get("match_confidence")),
            "alignment_method": match.get("match_method") or "unmatched",
            "warnings": list(match.get("warnings") or []),
        },
        "object_alignment": object_alignment,
        "readiness": {
            "ready_for_mechanism_source_pack": not readiness_missing,
            "missing": readiness_missing,
        },
        **_safety_flags(),
    }


def load_zotero_note(
    conn: sqlite3.Connection,
    *,
    server_note_id: str | None = None,
    client_note_id: str | None = None,
    note_row_id: int | None = None,
) -> dict[str, Any] | None:
    if not _table_exists(conn, "zotero_inspiration_notes"):
        return None
    predicates = []
    params: list[Any] = []
    if note_row_id is not None:
        predicates.append("id = ?")
        params.append(note_row_id)
    if server_note_id:
        predicates.append("server_note_id = ?")
        params.append(server_note_id)
    if client_note_id:
        predicates.append("client_note_id = ?")
        params.append(client_note_id)
    if not predicates:
        predicates.append("1 = 1")
    row = conn.execute(
        f"""
        SELECT *
        FROM zotero_inspiration_notes
        WHERE {' AND '.join(predicates)}
        ORDER BY id
        LIMIT 1
        """,
        tuple(params),
    ).fetchone()
    return dict(row) if row else None


def load_zotero_notes(
    conn: sqlite3.Connection,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    if not _table_exists(conn, "zotero_inspiration_notes"):
        return []
    rows = conn.execute(
        "SELECT * FROM zotero_inspiration_notes ORDER BY id LIMIT ?",
        (max(1, int(limit)),),
    ).fetchall()
    return [dict(row) for row in rows]


def _object_alignment(
    conn: sqlite3.Connection,
    note: Mapping[str, Any],
    document_id: int | None,
    matched_chunk_ids: list[int],
) -> dict[str, Any]:
    if not _table_exists(conn, "object_candidates"):
        return {
            "matched_object_ids": [],
            "candidate_object_terms": _candidate_terms(note),
            "alignment_method": "object_candidates_table_unavailable",
            "warnings": ["object_candidates_table_unavailable"],
        }
    terms = _candidate_terms(note)
    rows = conn.execute(
        """
        SELECT id, document_id, object_name, object_type, review_status, status,
               mapped_chunk_ids_json, evidence_refs_json
        FROM object_candidates
        ORDER BY id
        """
    ).fetchall()
    matched: list[int] = []
    methods: list[str] = []
    warnings: list[str] = []
    for row in rows:
        item = dict(row)
        if document_id is not None and _optional_int(item.get("document_id")) not in (None, document_id):
            continue
        object_chunks = _json_int_list(item.get("mapped_chunk_ids_json"))
        chunk_overlap = bool(set(object_chunks) & set(matched_chunk_ids))
        object_text = f"{item.get('object_name') or ''} {item.get('object_type') or ''}".casefold()
        term_overlap = any(term.casefold() in object_text for term in terms if len(term) >= 3)
        if chunk_overlap or term_overlap:
            object_id = _optional_int(item.get("id"))
            if object_id is not None and object_id not in matched:
                matched.append(object_id)
                methods.append("chunk_overlap" if chunk_overlap else "term_overlap")
    if not matched:
        warnings.append("no_object_candidate_overlap")
    return {
        "matched_object_ids": matched,
        "candidate_object_terms": terms,
        "alignment_method": "+".join(sorted(set(methods))) if methods else "none",
        "warnings": warnings,
    }


def _matched_chunk_ids(note: Mapping[str, Any], match: Mapping[str, Any]) -> list[int]:
    ids = _json_int_list(note.get("matched_chunk_ids_json"))
    matched = _optional_int(match.get("matched_chunk_id")) or _optional_int(note.get("matched_chunk_id"))
    if matched is not None and matched not in ids:
        ids.insert(0, matched)
    return ids


def _candidate_terms(note: Mapping[str, Any]) -> list[str]:
    values = [
        note.get("note_text"),
        note.get("selected_text"),
        note.get("user_tags_json"),
        " ".join(str(item) for item in note.get("user_tags") or []),
    ]
    raw = " ".join(_text(value) for value in values)
    terms = []
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", raw):
        clean = token.strip()
        if clean and clean not in terms:
            terms.append(clean)
    return terms[:24]


def _chapter_for_chunk(conn: sqlite3.Connection, chunk_id: int | None) -> int | None:
    if chunk_id is None or not _table_exists(conn, "knowledge_chunks"):
        return None
    row = conn.execute("SELECT chapter_id FROM knowledge_chunks WHERE id = ?", (chunk_id,)).fetchone()
    return _optional_int(row[0]) if row else None


def _confidence_score(value: Any) -> float:
    mapping = {"high": 1.0, "medium": 0.75, "low": 0.35, "none": 0.0}
    return mapping.get(str(value or "").casefold(), 0.0)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _json_int_list(value: Any) -> list[int]:
    try:
        loaded = json.loads(str(value or "[]"))
    except (TypeError, ValueError):
        return []
    if not isinstance(loaded, list):
        return []
    ids: list[int] = []
    for item in loaded:
        integer = _optional_int(item)
        if integer is not None and integer not in ids:
            ids.append(integer)
    return ids


def _first_int(values: list[Any]) -> int | None:
    for value in values:
        integer = _optional_int(value)
        if integer is not None:
            return integer
    return None


def _optional_int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safety_flags() -> dict[str, bool]:
    return {
        "db_write_performed": False,
        "llm_called": False,
        "relation_generated": False,
        "mechanism_generated": False,
        "zotero_write_performed": False,
        "vector_store_write_performed": False,
    }
